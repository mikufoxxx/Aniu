from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BacktestRun, DailyBar
from app.services.market_data_service import normalize_symbol

_TUSHARE_DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
_TUSHARE_HTTP_TIMEOUT_SECONDS = 20
_TUSHARE_CURL_TIMEOUT_SECONDS = _TUSHARE_HTTP_TIMEOUT_SECONDS + 5
_MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES = 3


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

        normalized_date = _normalize_trade_date(trade_date)
        if symbols:
            rows: list[dict[str, Any]] = []
            for symbol in symbols:
                rows.extend(
                    self._request_tushare_daily(
                        {
                            "ts_code": normalize_symbol(symbol),
                            "start_date": normalized_date,
                            "end_date": normalized_date,
                        }
                    )
                )
            return rows
        return self._request_tushare_daily({"trade_date": normalized_date})

    def _request_tushare_daily(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        settings = get_settings()
        url = settings.tushare_api_url or "http://api.tushare.pro"
        payload = {
            "api_name": "daily",
            "token": settings.tushare_token,
            "params": params,
            "fields": _TUSHARE_DAILY_FIELDS,
        }
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--max-time",
            str(_TUSHARE_HTTP_TIMEOUT_SECONDS),
            "--connect-timeout",
            "5",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_TUSHARE_CURL_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Tushare 日线请求超时: {params}") from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "unknown error").strip()
            if completed.returncode == 28:
                raise RuntimeError(f"Tushare 日线请求超时: {params}")
            raise RuntimeError(f"Tushare 日线请求失败: {message}")

        try:
            body = json.loads(completed.stdout)
        except ValueError as exc:
            raise RuntimeError("Tushare 日线响应不是合法 JSON。") from exc

        code = int(body.get("code") or 0)
        if code != 0:
            message = str(body.get("msg") or "unknown error")
            raise RuntimeError(f"Tushare 日线接口错误: {message}")

        data = body.get("data") or {}
        fields = data.get("fields") or []
        items = data.get("items") or []
        if not isinstance(fields, list) or not isinstance(items, list):
            raise RuntimeError("Tushare 日线响应结构不正确。")

        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, list):
                continue
            rows.append(dict(zip(fields, item, strict=False)))
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
        progress_callback=None,
    ) -> dict[str, Any]:
        dates = self._date_range(start_date, end_date)
        normalized_symbols = [normalize_symbol(symbol) for symbol in symbols] if symbols else None
        daily_results: list[dict[str, Any]] = []
        stored_symbols: set[str] = set()
        total_stored = 0
        total_skipped = 0
        consecutive_failures = 0
        total_days = len(dates)

        for index, trade_date in enumerate(dates):
            try:
                result = self.refresh_daily_bars(
                    db,
                    trade_date=trade_date,
                    symbols=normalized_symbols,
                )
                consecutive_failures = 0
            except RuntimeError as exc:
                consecutive_failures += 1
                result = {
                    "trade_date": trade_date,
                    "source": "tushare",
                    "stored_count": 0,
                    "skipped_count": 1,
                    "requested_symbols": normalized_symbols or [],
                    "error": str(exc),
                }
            daily_results.append(result)
            total_stored += int(result["stored_count"])
            total_skipped += int(result["skipped_count"])
            if progress_callback:
                progress_callback(
                    {
                        "phase": "refreshing_daily",
                        "total_days": total_days,
                        "processed_days": index + 1,
                        "current_trade_date": trade_date,
                        "stored_count": total_stored,
                        "skipped_count": total_skipped,
                        "error_count": sum(1 for item in daily_results if item.get("error")),
                    }
                )
            stored_symbols.update(
                db.scalars(
                    select(DailyBar.symbol)
                    .where(DailyBar.trade_date == trade_date)
                    .distinct()
                ).all()
            )
            if consecutive_failures >= _MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES:
                remaining_dates = dates[index + 1 :]
                for skipped_date in remaining_dates:
                    daily_results.append(
                        {
                            "trade_date": skipped_date,
                            "source": "tushare",
                            "stored_count": 0,
                            "skipped_count": 1,
                            "requested_symbols": normalized_symbols or [],
                            "error": (
                                f"连续 {_MAX_CONSECUTIVE_DAILY_REFRESH_FAILURES} 天刷新失败，"
                                "停止本轮剩余日期请求。"
                            ),
                        }
                    )
                total_skipped += len(remaining_dates)
                if progress_callback:
                    progress_callback(
                        {
                            "phase": "refreshing_daily",
                            "total_days": total_days,
                            "processed_days": total_days,
                            "current_trade_date": dates[-1],
                            "stored_count": total_stored,
                            "skipped_count": total_skipped,
                            "error_count": sum(
                                1 for item in daily_results if item.get("error")
                            ),
                        }
                    )
                break

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
