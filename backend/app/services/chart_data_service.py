from __future__ import annotations

from datetime import datetime, timedelta
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
        return self.price_series_from_bars(rows)

    def price_series_from_bars(self, rows: list[DailyBar]) -> list[dict[str, Any]]:
        closes: list[float] = []
        macd_values: list[float] = []
        points: list[dict[str, Any]] = []
        for bar in rows:
            close = float(bar.close or 0)
            if close <= 0:
                continue
            closes.append(close)
            macd = self._macd(closes)
            if macd is not None:
                macd_values.append(macd)
            macd_signal = self._ema(macd_values, 9) if macd_values else None
            macd_hist = macd - macd_signal if macd is not None and macd_signal is not None else None
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
                    "rsi14": self._rsi(closes, 14),
                    "macd": round(macd, 6) if macd is not None else None,
                    "macd_signal": round(macd_signal, 6) if macd_signal is not None else None,
                    "macd_hist": round(macd_hist, 6) if macd_hist is not None else None,
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
            "interval_series": self.interval_series(price_series),
            "forecast_series": self.forecast_series(price_series),
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
            "interval_series": self.interval_series(series),
            "forecast_series": self.forecast_series(series),
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
            "interval_series": self.interval_series(series),
            "forecast_series": self.forecast_series(series),
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
        periods = max(1, len(daily_returns))
        annual_return = (1 + total_return) ** (252 / periods) - 1 if total_return > -1 else -1.0
        volatility = pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
        average_return = mean(daily_returns) if daily_returns else 0.0
        sharpe = average_return / pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 and pstdev(daily_returns) > 0 else 0.0
        downside_returns = [value for value in daily_returns if value < 0]
        downside_dev = (
            pstdev(downside_returns) if len(downside_returns) > 1
            else abs(downside_returns[0]) if downside_returns else 0.0
        )
        sortino = average_return / downside_dev * math.sqrt(252) if downside_dev > 0 else 0.0
        positive_sum = sum(value for value in daily_returns if value > 0)
        negative_sum = abs(sum(value for value in daily_returns if value < 0))
        omega = positive_sum / negative_sum if negative_sum > 0 else (1.0 if positive_sum > 0 else 0.0)
        win_rate = (
            len([value for value in daily_returns if value > 0]) / len(daily_returns)
            if daily_returns else 0.0
        )
        profit_factor = omega
        max_drawdown = min((float(item.get("drawdown") or 0) for item in drawdown_curve), default=0.0)
        calmar = total_return / abs(max_drawdown) if max_drawdown < 0 else 0.0
        return {
            "total_return": round(total_return, 6),
            "annual_return": round(annual_return, 6),
            "volatility": round(volatility, 6),
            "sharpe": round(sharpe, 6),
            "sortino": round(sortino, 6),
            "omega": round(omega, 6),
            "win_rate": round(win_rate, 6),
            "profit_factor": round(profit_factor, 6),
            "max_drawdown": round(max_drawdown, 6),
            "calmar": round(calmar, 6),
        }

    def interval_series(self, price_series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        return {
            "daily": price_series,
            "weekly": self._aggregate_price_series(price_series, "week"),
            "monthly": self._aggregate_price_series(price_series, "month"),
            "hourly": self._synthetic_hourly_series(price_series),
        }

    def forecast_series(
        self,
        price_series: list[dict[str, Any]],
        *,
        horizon: int = 10,
    ) -> list[dict[str, Any]]:
        if not price_series:
            return []
        closes = [float(item.get("close") or 0) for item in price_series if float(item.get("close") or 0) > 0]
        if not closes:
            return []
        returns = [
            (closes[index] - closes[index - 1]) / closes[index - 1]
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]
        recent_returns = returns[-20:]
        drift = mean(recent_returns) if recent_returns else 0.0
        last_return = recent_returns[-1] if recent_returns else 0.0
        volatility = pstdev(recent_returns) if len(recent_returns) > 1 else abs(last_return) / 2
        expected_return = max(-0.08, min(0.08, drift * 0.55 + last_return * 0.45))
        current = closes[-1]
        trade_date = self._parse_trade_date(str(price_series[-1].get("trade_date") or ""))
        points: list[dict[str, Any]] = []
        for step in range(1, horizon + 1):
            current = max(0.01, current * (1 + expected_return))
            confidence = volatility * math.sqrt(step)
            points.append(
                {
                    "trade_date": (trade_date + timedelta(days=step)).strftime("%Y%m%d"),
                    "price": round(current, 4),
                    "upper": round(current * (1 + confidence), 4),
                    "lower": round(max(0.01, current * (1 - confidence)), 4),
                    "source": "ai_simulation",
                }
            )
        return points

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

    def _aggregate_price_series(
        self,
        price_series: list[dict[str, Any]],
        interval: str,
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for point in price_series:
            key = self._period_key(str(point.get("trade_date") or ""), interval)
            grouped.setdefault(key, []).append(point)
        rows: list[dict[str, Any]] = []
        for key in sorted(grouped):
            items = grouped[key]
            first = items[0]
            last = items[-1]
            rows.append(
                {
                    "trade_date": key,
                    "open": round(float(first.get("open") or first.get("close") or 0), 4),
                    "high": round(max(float(item.get("high") or item.get("close") or 0) for item in items), 4),
                    "low": round(min(float(item.get("low") or item.get("close") or 0) for item in items), 4),
                    "close": round(float(last.get("close") or 0), 4),
                    "volume": sum(float(item.get("volume") or 0) for item in items),
                    "amount": sum(float(item.get("amount") or 0) for item in items),
                    "ma5": None,
                    "ma20": None,
                }
            )
        return self._enrich_price_points(rows)

    def _synthetic_hourly_series(self, price_series: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for point in price_series[-20:]:
            trade_date = str(point.get("trade_date") or "")[:8]
            open_price = float(point.get("open") or point.get("close") or 0)
            high_price = float(point.get("high") or point.get("close") or open_price)
            low_price = float(point.get("low") or point.get("close") or open_price)
            close_price = float(point.get("close") or open_price)
            amount = float(point.get("amount") or 0) / 4
            volume = float(point.get("volume") or 0) / 4
            path = [
                ("10:30", open_price, max(open_price, high_price), min(open_price, high_price), high_price),
                ("11:30", high_price, high_price, min(low_price, high_price), (high_price + close_price) / 2),
                ("14:00", (high_price + close_price) / 2, max(high_price, close_price), low_price, low_price),
                ("15:00", low_price, max(low_price, close_price), min(low_price, close_price), close_price),
            ]
            for time_label, open_value, high_value, low_value, close_value in path:
                rows.append(
                    {
                        "trade_date": f"{trade_date} {time_label}",
                        "open": round(open_value, 4),
                        "high": round(high_value, 4),
                        "low": round(low_value, 4),
                        "close": round(close_value, 4),
                        "volume": volume,
                        "amount": amount,
                    }
                )
        return self._enrich_price_points(rows)

    def _enrich_price_points(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        closes: list[float] = []
        macd_values: list[float] = []
        enriched: list[dict[str, Any]] = []
        for row in rows:
            close = float(row.get("close") or 0)
            closes.append(close)
            macd = self._macd(closes)
            if macd is not None:
                macd_values.append(macd)
            macd_signal = self._ema(macd_values, 9) if macd_values else None
            macd_hist = macd - macd_signal if macd is not None and macd_signal is not None else None
            enriched.append(
                {
                    **row,
                    "ma5": self._moving_average(closes, 5),
                    "ma20": self._moving_average(closes, 20),
                    "rsi14": self._rsi(closes, 14),
                    "macd": round(macd, 6) if macd is not None else None,
                    "macd_signal": round(macd_signal, 6) if macd_signal is not None else None,
                    "macd_hist": round(macd_hist, 6) if macd_hist is not None else None,
                }
            )
        return enriched

    def _period_key(self, value: str, interval: str) -> str:
        trade_date = self._parse_trade_date(value)
        if interval == "month":
            return trade_date.strftime("%Y%m")
        year, week, _weekday = trade_date.isocalendar()
        return f"{year}W{week:02d}"

    def _parse_trade_date(self, value: str) -> datetime:
        compact = value[:8]
        try:
            return datetime.strptime(compact, "%Y%m%d")
        except ValueError:
            return datetime.utcnow()

    def _rsi(self, closes: list[float], window: int) -> float | None:
        if len(closes) <= window:
            return None
        changes = [
            closes[index] - closes[index - 1]
            for index in range(len(closes) - window, len(closes))
        ]
        gains = [max(change, 0.0) for change in changes]
        losses = [abs(min(change, 0.0)) for change in changes]
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 4)

    def _macd(self, closes: list[float]) -> float | None:
        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        if ema12 is None or ema26 is None:
            return None
        return ema12 - ema26

    def _ema(self, values: list[float], window: int) -> float | None:
        if len(values) < window:
            return None
        alpha = 2 / (window + 1)
        ema = sum(values[:window]) / window
        for value in values[window:]:
            ema = value * alpha + ema * (1 - alpha)
        return round(ema, 6)

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
