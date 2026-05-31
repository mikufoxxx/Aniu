from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyBar
from app.services.market_data_service import normalize_symbol


class ChartDataService:
    def price_series(
        self,
        db: Session,
        *,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        normalized_symbol = normalize_symbol(symbol)
        query = select(DailyBar).where(DailyBar.symbol == normalized_symbol)
        if start_date:
            query = query.where(DailyBar.trade_date >= start_date)
        if end_date:
            query = query.where(DailyBar.trade_date <= end_date)
        rows = list(
            db.scalars(
                query.order_by(DailyBar.trade_date.desc()).limit(max(1, min(limit, 500)))
            ).all()
        )
        rows.reverse()
        closes: list[float] = []
        points: list[dict[str, Any]] = []
        for bar in rows:
            close = float(bar.close or 0)
            if close <= 0:
                continue
            closes.append(close)
            open_price = float(bar.open or close)
            high_price = float(bar.high or max(open_price, close))
            low_price = float(bar.low or min(open_price, close))
            points.append(
                {
                    "trade_date": bar.trade_date,
                    "open": round(open_price, 4),
                    "high": round(high_price, 4),
                    "low": round(low_price, 4),
                    "close": round(close, 4),
                    "volume": float(bar.vol or 0),
                    "amount": float(bar.amount or 0),
                    "ma5": self._moving_average(closes, 5),
                    "ma20": self._moving_average(closes, 20),
                }
            )
        return points

    def strategy_charts(
        self,
        *,
        price_series: list[dict[str, Any]],
        trades: list[dict[str, Any]],
        equity_curve: list[dict[str, Any]],
        drawdown_curve: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "price_series": price_series,
            "trade_markers": [
                {
                    "action": trade["action"],
                    "symbol": trade["symbol"],
                    "trade_date": trade["trade_date"],
                    "price": trade["price"],
                    "quantity": trade["quantity"],
                }
                for trade in trades
            ],
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
        }

    def stock_analysis_charts(
        self,
        db: Session,
        *,
        symbol: str,
        action: str,
        price: float,
        quantity: int,
        factor_scores: dict[str, Any],
    ) -> dict[str, Any]:
        series = self.price_series(db, symbol=symbol, limit=90)
        trade_date = series[-1]["trade_date"] if series else None
        marker_price = float(price or (series[-1]["close"] if series else 0))
        return {
            "price_series": series,
            "signal_markers": [
                {
                    "action": action,
                    "symbol": normalize_symbol(symbol),
                    "trade_date": trade_date,
                    "price": round(marker_price, 4),
                    "quantity": int(quantity or 0),
                }
            ],
            "factor_radar": [
                {"name": self._factor_label(key), "key": key, "value": round(float(value or 0), 4)}
                for key, value in factor_scores.items()
                if isinstance(value, (int, float))
            ][:8],
        }

    def order_charts(
        self,
        db: Session,
        *,
        symbol: str,
        action: str,
        trade_date: str | None,
        price: float,
        quantity: int,
    ) -> dict[str, Any]:
        series = self.price_series(db, symbol=symbol, limit=90)
        return {
            "price_series": series,
            "trade_markers": [
                {
                    "action": action,
                    "symbol": normalize_symbol(symbol),
                    "trade_date": trade_date or (series[-1]["trade_date"] if series else None),
                    "price": round(float(price or 0), 4),
                    "quantity": int(quantity or 0),
                }
            ],
        }

    def equity_curve_from_values(
        self,
        *,
        trade_dates: list[str],
        values: list[float],
        initial_cash: float,
    ) -> list[dict[str, Any]]:
        return [
            {
                "trade_date": trade_date,
                "value": round(value, 2),
                "return_ratio": round((value - initial_cash) / initial_cash, 6)
                if initial_cash > 0
                else 0.0,
            }
            for trade_date, value in zip(trade_dates, values)
        ]

    def drawdown_curve_from_values(
        self,
        *,
        trade_dates: list[str],
        values: list[float],
    ) -> list[dict[str, Any]]:
        peak = 0.0
        points: list[dict[str, Any]] = []
        for trade_date, value in zip(trade_dates, values):
            peak = max(peak, value)
            drawdown = (value - peak) / peak if peak > 0 else 0.0
            points.append({"trade_date": trade_date, "drawdown": round(drawdown, 6)})
        return points

    def _moving_average(self, closes: list[float], window: int) -> float | None:
        if len(closes) < window:
            return None
        return round(sum(closes[-window:]) / window, 4)

    def _factor_label(self, key: str) -> str:
        labels = {
            "momentum": "涨跌",
            "amount": "成交额",
            "turnover": "换手",
            "volume_ratio": "量比",
            "daily_momentum": "日线动量",
            "moneyflow": "资金流",
            "sector_heat": "板块热度",
            "valuation": "估值",
        }
        return labels.get(key, key)


chart_data_service = ChartDataService()
