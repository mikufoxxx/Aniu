from __future__ import annotations

import math
from statistics import mean, pstdev
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
            "risk_metrics": self.risk_metrics(equity_curve, drawdown_curve),
            "return_distribution": self.return_distribution_from_equity(equity_curve),
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
            "support_resistance": self.support_resistance(series),
            "return_distribution": self.return_distribution_from_prices(series),
            "volume_profile": self.volume_profile(series),
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

    def risk_metrics(
        self,
        equity_curve: list[dict[str, Any]],
        drawdown_curve: list[dict[str, Any]],
    ) -> dict[str, float]:
        returns = [float(item.get("return_ratio") or 0) for item in equity_curve]
        daily_returns = [
            returns[index] - returns[index - 1]
            for index in range(1, len(returns))
        ]
        total_return = returns[-1] if returns else 0.0
        volatility = pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
        average_return = mean(daily_returns) if daily_returns else 0.0
        sharpe = average_return / pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 and pstdev(daily_returns) > 0 else 0.0
        max_drawdown = min((float(item.get("drawdown") or 0) for item in drawdown_curve), default=0.0)
        calmar = total_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
        return {
            "total_return": round(total_return, 6),
            "volatility": round(volatility, 6),
            "sharpe": round(sharpe, 6),
            "max_drawdown": round(max_drawdown, 6),
            "calmar": round(calmar, 6),
        }

    def return_distribution_from_equity(
        self,
        equity_curve: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        returns = [
            float(equity_curve[index].get("return_ratio") or 0)
            - float(equity_curve[index - 1].get("return_ratio") or 0)
            for index in range(1, len(equity_curve))
        ]
        return self._histogram(returns, bucket_count=5, unit="ratio")

    def return_distribution_from_prices(
        self,
        price_series: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        returns = [
            (float(price_series[index].get("close") or 0) - float(price_series[index - 1].get("close") or 0))
            / float(price_series[index - 1].get("close") or 1)
            for index in range(1, len(price_series))
            if float(price_series[index - 1].get("close") or 0) > 0
        ]
        return self._histogram(returns, bucket_count=5, unit="ratio")

    def support_resistance(self, price_series: list[dict[str, Any]]) -> dict[str, float | None]:
        if not price_series:
            return {"support": None, "resistance": None, "last_close": None}
        lows = [float(item.get("low") or item.get("close") or 0) for item in price_series]
        highs = [float(item.get("high") or item.get("close") or 0) for item in price_series]
        last = price_series[-1]
        return {
            "support": round(min(lows), 4),
            "resistance": round(max(highs), 4),
            "last_close": round(float(last.get("close") or 0), 4),
        }

    def volume_profile(
        self,
        price_series: list[dict[str, Any]],
        *,
        bucket_count: int = 6,
    ) -> list[dict[str, Any]]:
        if not price_series:
            return []
        closes = [float(item.get("close") or 0) for item in price_series]
        low = min(closes)
        high = max(closes)
        if high <= low:
            return [
                {
                    "price_low": round(low, 4),
                    "price_high": round(high, 4),
                    "amount": round(sum(float(item.get("amount") or 0) for item in price_series), 2),
                }
            ]
        width = (high - low) / bucket_count
        buckets = [
            {"price_low": low + width * index, "price_high": low + width * (index + 1), "amount": 0.0}
            for index in range(bucket_count)
        ]
        for point in price_series:
            close = float(point.get("close") or 0)
            index = min(bucket_count - 1, max(0, int((close - low) / width)))
            buckets[index]["amount"] += float(point.get("amount") or 0)
        return [
            {
                "price_low": round(item["price_low"], 4),
                "price_high": round(item["price_high"], 4),
                "amount": round(item["amount"], 2),
            }
            for item in buckets
        ]

    def arena_summary_charts(
        self,
        *,
        orders: list[dict[str, Any]],
        positions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action_totals: dict[str, float] = {}
        for order in orders:
            action = str(order.get("action") or "UNKNOWN")
            action_totals[action] = action_totals.get(action, 0.0) + float(order.get("amount") or 0)
        symbol_exposure = [
            {
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or item.get("symbol") or ""),
                "market_value": round(float(item.get("market_value") or 0), 2),
                "unrealized_pnl": round(float(item.get("unrealized_pnl") or 0), 2),
            }
            for item in positions
        ]
        return {
            "action_distribution": [
                {"name": action, "value": round(amount, 2)}
                for action, amount in sorted(action_totals.items())
            ],
            "symbol_exposure": symbol_exposure,
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

    def _histogram(
        self,
        values: list[float],
        *,
        bucket_count: int,
        unit: str,
    ) -> list[dict[str, Any]]:
        if not values:
            return []
        low = min(values)
        high = max(values)
        if high <= low:
            return [{"low": round(low, 6), "high": round(high, 6), "count": len(values), "unit": unit}]
        width = (high - low) / bucket_count
        buckets = [{"low": low + width * index, "high": low + width * (index + 1), "count": 0, "unit": unit} for index in range(bucket_count)]
        for value in values:
            index = min(bucket_count - 1, max(0, int((value - low) / width)))
            buckets[index]["count"] += 1
        return [
            {
                "low": round(item["low"], 6),
                "high": round(item["high"], 6),
                "count": item["count"],
                "unit": unit,
            }
            for item in buckets
        ]

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
