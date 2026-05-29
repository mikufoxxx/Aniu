from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun, DailyBar
from app.services.market_data_service import normalize_symbol


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_trade_date(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"交易日期格式不正确: {value}")
    return text


class HistoricalDataService:
    def fetch_daily_rows(
        self,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.tushare_token:
            raise RuntimeError("未配置 TUSHARE_TOKEN，无法刷新日频数据。")
        try:
            import tushare as ts
        except ImportError as exc:
            raise RuntimeError("当前环境未安装 tushare。") from exc

        pro = ts.pro_api(settings.tushare_token)
        if settings.tushare_api_url:
            pro._DataApi__http_url = settings.tushare_api_url

        normalized_date = _normalize_trade_date(trade_date)
        frames = []
        if symbols:
            for symbol in symbols:
                frame = pro.daily(
                    ts_code=normalize_symbol(symbol),
                    start_date=normalized_date,
                    end_date=normalized_date,
                )
                frames.append(frame)
        else:
            frames.append(pro.daily(trade_date=normalized_date))

        rows: list[dict[str, Any]] = []
        for frame in frames:
            if frame is None:
                continue
            rows.extend(frame.to_dict("records"))
        return rows

    def refresh_daily_bars(
        self,
        db: Session,
        *,
        trade_date: str,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        normalized_date = _normalize_trade_date(trade_date)
        rows = self.fetch_daily_rows(normalized_date, normalized_symbols)

        stored_count = 0
        skipped_count = 0
        for row in rows:
            symbol = normalize_symbol(str(row.get("ts_code") or row.get("symbol") or ""))
            row_trade_date = _normalize_trade_date(str(row.get("trade_date") or normalized_date))
            if row_trade_date != normalized_date:
                skipped_count += 1
                continue
            existing = db.scalar(
                select(DailyBar).where(
                    DailyBar.symbol == symbol,
                    DailyBar.trade_date == row_trade_date,
                )
            )
            bar = existing or DailyBar(symbol=symbol, trade_date=row_trade_date)
            bar.open = _to_float(row.get("open"))
            bar.high = _to_float(row.get("high"))
            bar.low = _to_float(row.get("low"))
            bar.close = _to_float(row.get("close"))
            bar.pre_close = _to_float(row.get("pre_close"))
            bar.pct_chg = _to_float(row.get("pct_chg"))
            bar.vol = _to_float(row.get("vol"))
            bar.amount = _to_float(row.get("amount"))
            bar.source = "tushare"
            bar.raw_payload = dict(row)
            db.add(bar)
            stored_count += 1

        db.commit()
        return {
            "trade_date": normalized_date,
            "source": "tushare",
            "stored_count": stored_count,
            "skipped_count": skipped_count,
            "requested_symbols": normalized_symbols or [],
        }

    def refresh_daily_range(
        self,
        db: Session,
        *,
        start_date: str,
        end_date: str,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        dates = self._date_range(start_date, end_date)
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        daily_results: list[dict[str, Any]] = []
        stored_symbols: set[str] = set()
        total_stored = 0
        total_skipped = 0

        for trade_date in dates:
            result = self.refresh_daily_bars(
                db,
                trade_date=trade_date,
                symbols=normalized_symbols,
            )
            daily_results.append(result)
            total_stored += int(result["stored_count"])
            total_skipped += int(result["skipped_count"])
            stored_symbols.update(
                db.scalars(
                    select(DailyBar.symbol)
                    .where(DailyBar.trade_date == trade_date)
                    .distinct()
                ).all()
            )

        return {
            "start_date": dates[0],
            "end_date": dates[-1],
            "source": "tushare",
            "processed_days": len(dates),
            "stored_count": total_stored,
            "skipped_count": total_skipped,
            "unique_symbols": len(stored_symbols),
            "requested_symbols": normalized_symbols or [],
            "daily_results": daily_results,
        }

    def run_daily_momentum_backtest(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_cash: float,
    ) -> dict[str, Any]:
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols]
        start = _normalize_trade_date(start_date)
        end = _normalize_trade_date(end_date)
        bars = db.scalars(
            select(DailyBar)
            .where(
                DailyBar.symbol.in_(normalized_symbols),
                DailyBar.trade_date >= start,
                DailyBar.trade_date <= end,
            )
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        ).all()
        by_symbol: dict[str, list[DailyBar]] = defaultdict(list)
        for bar in bars:
            if bar.close is not None:
                by_symbol[bar.symbol].append(bar)
        if not by_symbol:
            raise ValueError("没有找到可用于回测的日频数据，请先刷新历史数据。")

        ranked: list[tuple[float, str, list[DailyBar]]] = []
        for symbol, symbol_bars in by_symbol.items():
            if len(symbol_bars) < 2:
                continue
            first_close = float(symbol_bars[0].close or 0)
            last_close = float(symbol_bars[-1].close or 0)
            if first_close <= 0 or last_close <= 0:
                continue
            momentum = (last_close - first_close) / first_close
            ranked.append((momentum, symbol, symbol_bars))
        if not ranked:
            raise ValueError("可用日频数据不足，至少需要每只股票两根日线。")

        ranked.sort(reverse=True, key=lambda item: item[0])
        _, selected_symbol, selected_bars = ranked[0]
        buy_bar = selected_bars[0]
        sell_bar = selected_bars[-1]
        buy_price = float(buy_bar.close or 0)
        sell_price = float(sell_bar.close or 0)
        quantity = int(initial_cash // (buy_price * 100)) * 100
        buy_amount = quantity * buy_price
        cash_after_buy = initial_cash - buy_amount
        final_assets = cash_after_buy + quantity * sell_price
        return_ratio = (final_assets - initial_cash) / initial_cash
        equity_curve = [
            cash_after_buy + quantity * float(bar.close or 0)
            for bar in selected_bars
        ]
        max_drawdown = self._max_drawdown(equity_curve)
        trades = [
            {
                "action": "BUY",
                "symbol": selected_symbol,
                "trade_date": buy_bar.trade_date,
                "price": buy_price,
                "quantity": quantity,
                "amount": round(buy_amount, 2),
            },
            {
                "action": "SELL",
                "symbol": selected_symbol,
                "trade_date": sell_bar.trade_date,
                "price": sell_price,
                "quantity": quantity,
                "amount": round(quantity * sell_price, 2),
            },
        ]
        metrics = {
            "bars_used": len(bars),
            "candidate_symbols": len(by_symbol),
            "selected_momentum": round((sell_price - buy_price) / buy_price, 6),
            "quantity": quantity,
        }
        run = BacktestRun(
            strategy_name="daily_momentum",
            symbols_json=normalized_symbols,
            start_date=start,
            end_date=end,
            initial_cash=initial_cash,
            final_assets=round(final_assets, 2),
            return_ratio=round(return_ratio, 6),
            max_drawdown=round(max_drawdown, 6),
            trade_count=len(trades),
            selected_symbol=selected_symbol,
            metrics_payload=metrics,
            trades_payload=trades,
        )
        db.add(run)
        db.commit()
        return {
            "run_id": run.id,
            "strategy_name": run.strategy_name,
            "selected_symbol": selected_symbol,
            "start_date": start,
            "end_date": end,
            "initial_cash": initial_cash,
            "final_assets": run.final_assets,
            "return_ratio": run.return_ratio,
            "max_drawdown": run.max_drawdown,
            "trade_count": run.trade_count,
            "metrics": metrics,
            "trades": trades,
        }

    def _max_drawdown(self, equity_curve: list[float]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = min(max_drawdown, (value - peak) / peak)
        return max_drawdown

    def _date_range(self, start_date: str, end_date: str) -> list[str]:
        start = datetime.strptime(_normalize_trade_date(start_date), "%Y%m%d").date()
        end = datetime.strptime(_normalize_trade_date(end_date), "%Y%m%d").date()
        if start > end:
            raise ValueError("开始日期不能晚于结束日期。")
        days = (end - start).days + 1
        if days > 120:
            raise ValueError("单次最多刷新 120 个自然日。")
        return [
            (start + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(days)
        ]


historical_data_service = HistoricalDataService()
