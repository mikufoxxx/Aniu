from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from statistics import mean, pstdev
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyBar
from app.services.market_data_service import market_data_service, normalize_symbol
from app.services.trading_calendar_service import trading_calendar_service

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


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

    def price_series_with_realtime(
        self,
        db: Session,
        *,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 120,
        realtime_quote: dict[str, Any] | None = None,
        intraday_series: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        series = self.price_series(
            db,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if end_date:
            return series
        return self._merge_realtime_quote(
            series,
            symbol=symbol,
            realtime_quote=realtime_quote,
            intraday_series=intraday_series,
        )

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
        realtime_quote: dict[str, Any] | None = None,
        hourly_series: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        hourly = hourly_series if hourly_series is not None else self.real_hourly_series(symbol)
        series = self.price_series_with_realtime(
            db,
            symbol=symbol,
            limit=90,
            realtime_quote=realtime_quote,
            intraday_series=hourly,
        )
        trade_date = series[-1]["trade_date"] if series else None
        marker_price = float(price or (series[-1]["close"] if series else 0))
        forecast = self.forecast_series(series)
        return {
            "price_series": series,
            "interval_series": self.interval_series(series, hourly_series=hourly),
            "forecast_series": forecast,
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
            "data_summary": self.chart_data_summary(
                price_series=series,
                hourly_series=hourly,
                forecast_series=forecast,
                markers=[
                    {
                        "action": action,
                        "symbol": normalize_symbol(symbol),
                        "trade_date": trade_date,
                        "price": round(marker_price, 4),
                        "quantity": int(quantity or 0),
                    }
                ],
            ),
            "forecast_actual_comparison": self.forecast_actual_comparison(
                price_series=series,
                forecast_series=forecast,
                frozen=False,
            ),
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
        realtime_quote: dict[str, Any] | None = None,
        hourly_series: list[dict[str, Any]] | None = None,
        frozen_prediction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        hourly = hourly_series if hourly_series is not None else self.real_hourly_series(symbol)
        series = self.price_series_with_realtime(
            db,
            symbol=symbol,
            limit=90,
            realtime_quote=realtime_quote,
            intraday_series=hourly,
        )
        frozen_snapshot = dict(frozen_prediction or {})
        forecast = (
            self._forecast_with_trading_dates(
                list(frozen_snapshot.get("forecast_series") or []),
                base_date=str(
                    frozen_snapshot.get("history_end_date")
                    or (series[-1].get("trade_date") if series else "")
                ),
            )
            if frozen_snapshot
            else self.forecast_series(series)
        )
        if frozen_snapshot:
            frozen_snapshot["forecast_series"] = forecast
        markers = [
            {
                "action": action,
                "symbol": normalize_symbol(symbol),
                "trade_date": trade_date or (series[-1]["trade_date"] if series else None),
                "price": round(float(price or 0), 4),
                "quantity": int(quantity or 0),
            }
        ]
        return {
            "price_series": series,
            "interval_series": self.interval_series(series, hourly_series=hourly),
            "forecast_series": forecast,
            "trade_markers": markers,
            "forecast_snapshot": frozen_snapshot,
            "forecast_actual_comparison": self.forecast_actual_comparison(
                price_series=series,
                forecast_series=forecast,
                frozen=bool(frozen_snapshot),
            ),
            "data_summary": self.chart_data_summary(
                price_series=series,
                hourly_series=hourly,
                forecast_series=forecast,
                markers=markers,
                frozen_prediction=frozen_prediction,
            ),
            "explanation_notes": [
                "日/周/月线来自本地 Tushare 日线库存，盘中小时线来自东方财富分钟 K 线，失败时用腾讯分钟线聚合。",
                "最新日线会合并后端统一缓存的腾讯/easy-tdx 实时快照；同一批请求会复用缓存和进行中的请求。",
                "买卖点按订单成交时间映射到同日可见 K 线；分时页按真实分钟 K 线聚合后的 OHLC 展示。",
                "预测线优先使用早盘冻结快照，后续刷新只追加实际走势对照，不改写预测本身。",
            ],
        }

    def frozen_prediction_snapshot(
        self,
        db: Session,
        *,
        symbol: str,
        generated_at: datetime,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        series = self.price_series(
            db,
            symbol=symbol,
            end_date=end_date,
            limit=90,
        )
        forecast = self.forecast_series(series)
        support_resistance = self.support_resistance(series)
        return {
            "frozen": True,
            "symbol": normalize_symbol(symbol),
            "generated_at": generated_at.astimezone(MARKET_TIMEZONE).isoformat()
            if generated_at.tzinfo
            else generated_at.replace(tzinfo=MARKET_TIMEZONE).isoformat(),
            "history_end_date": end_date or (series[-1]["trade_date"] if series else None),
            "source": "morning_frozen_quant_regime_projection",
            "history_points": len(series),
            "base_close": series[-1]["close"] if series else None,
            "support_resistance": support_resistance,
            "forecast_series": forecast,
            "methodology": [
                "冻结时只使用早盘可见历史日线，不使用未来行情。",
                "预测由近20日收益、5日短动量、均线差、回归斜率、RSI 和支撑压力共同生成。",
                "刷新页面时不会重算这条预测线，只会用最新实际走势计算偏差。",
            ],
        }

    def forecast_actual_comparison(
        self,
        *,
        price_series: list[dict[str, Any]],
        forecast_series: list[dict[str, Any]],
        frozen: bool = False,
    ) -> dict[str, Any]:
        if not price_series or not forecast_series:
            return {"matched_points": [], "latest_error_pct": None, "summary": "暂无可对照数据。"}
        actual_by_day = {
            self._compact_trade_date(str(point.get("trade_date") or "")): point
            for point in price_series
            if point.get("trade_date")
        }
        matched: list[dict[str, Any]] = []
        for forecast in forecast_series:
            day = self._compact_trade_date(str(forecast.get("trade_date") or ""))
            actual = actual_by_day.get(day)
            if actual is None:
                continue
            forecast_price = self._safe_float(forecast.get("price"))
            actual_close = self._safe_float(actual.get("close"))
            if forecast_price in (None, 0) or actual_close is None:
                continue
            error_pct = (actual_close - forecast_price) / forecast_price * 100
            matched.append(
                {
                    "trade_date": day,
                    "forecast_price": round(forecast_price, 4),
                    "actual_close": round(actual_close, 4),
                    "error_pct": round(error_pct, 4),
                }
            )
        latest = matched[-1] if matched else None
        if latest is None:
            latest_actual = price_series[-1]
            label = "冻结预测" if frozen else "预测线"
            suffix = "尚未命中预测日期" if frozen else "从下一交易日开始，尚无实际收盘点可对照"
            summary = (
                f"{label}当前最新实际点为 {latest_actual.get('trade_date')} "
                f"{latest_actual.get('close')}，{suffix}。"
            )
        else:
            summary = (
                f"已对照 {len(matched)} 个实际收盘点，最新偏差 "
                f"{latest['error_pct']:.2f}%。"
            )
        return {
            "matched_points": matched,
            "latest_error_pct": latest.get("error_pct") if latest else None,
            "summary": summary,
        }

    def chart_data_summary(
        self,
        *,
        price_series: list[dict[str, Any]],
        hourly_series: list[dict[str, Any]],
        forecast_series: list[dict[str, Any]],
        markers: list[dict[str, Any]],
        frozen_prediction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest_daily = price_series[-1] if price_series else {}
        latest_hourly = hourly_series[-1] if hourly_series else {}
        return {
            "daily_points": len(price_series),
            "hourly_points": len(hourly_series),
            "forecast_points": len(forecast_series),
            "marker_count": len(markers),
            "latest_daily_trade_date": latest_daily.get("trade_date"),
            "latest_daily_source": latest_daily.get("source") or "tushare_daily",
            "latest_daily_is_realtime": bool(latest_daily.get("is_realtime")),
            "latest_hourly_trade_time": latest_hourly.get("trade_date"),
            "latest_hourly_source": latest_hourly.get("source"),
            "forecast_frozen": bool((frozen_prediction or {}).get("frozen")),
            "forecast_generated_at": (frozen_prediction or {}).get("generated_at"),
            "forecast_history_end_date": (frozen_prediction or {}).get("history_end_date"),
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

    def interval_series(
        self,
        price_series: list[dict[str, Any]],
        *,
        hourly_series: list[dict[str, Any]] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "daily": price_series,
            "weekly": self._aggregate_price_series(price_series, "week"),
            "monthly": self._aggregate_price_series(price_series, "month"),
            "hourly": self._enrich_price_points(hourly_series or []),
        }

    def real_hourly_series(self, symbol: str) -> list[dict[str, Any]]:
        try:
            return market_data_service.get_intraday_bars(symbol, interval="60m", limit=120)
        except Exception:
            return []

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
        short_returns = returns[-5:]
        drift = mean(recent_returns) if recent_returns else 0.0
        short_momentum = mean(short_returns) if short_returns else drift
        volatility = pstdev(recent_returns) if len(recent_returns) > 1 else abs(short_momentum) / 2
        support_resistance = self.support_resistance(price_series[-60:])
        support = float(support_resistance.get("support") or min(closes[-20:]))
        resistance = float(support_resistance.get("resistance") or max(closes[-20:]))
        ma5 = price_series[-1].get("ma5")
        ma20 = price_series[-1].get("ma20")
        ma_spread = (
            (float(ma5) - float(ma20)) / closes[-1]
            if isinstance(ma5, (int, float)) and isinstance(ma20, (int, float)) and closes[-1] > 0
            else 0.0
        )
        rsi14 = price_series[-1].get("rsi14")
        rsi_bias = 0.0
        if isinstance(rsi14, (int, float)):
            if rsi14 >= 72:
                rsi_bias = -0.006
            elif rsi14 <= 32:
                rsi_bias = 0.006
        regression_slope = self._linear_regression_slope(closes[-20:])
        trend_signal = drift * 0.35 + short_momentum * 0.30 + regression_slope * 0.25 + ma_spread * 0.10
        expected_return = max(-0.045, min(0.045, trend_signal + rsi_bias))
        current = closes[-1]
        trade_date = self._parse_trade_date(str(price_series[-1].get("trade_date") or ""))
        forecast_dates = self._future_trading_dates(trade_date.date(), horizon)
        points: list[dict[str, Any]] = []
        for step in range(1, horizon + 1):
            resistance_gap = (resistance - current) / current if current > 0 else 0.0
            support_gap = (current - support) / current if current > 0 else 0.0
            pressure = 0.0
            if resistance_gap < 0.025:
                pressure -= 0.006
            if support_gap < 0.025:
                pressure += 0.006
            mean_reversion = ((mean(closes[-20:]) - current) / current) * 0.04 if len(closes) >= 20 else 0.0
            step_return = max(-0.05, min(0.05, expected_return * (0.94 ** (step - 1)) + pressure + mean_reversion))
            current = max(0.01, current * (1 + step_return))
            confidence = min(0.18, max(0.012, volatility * math.sqrt(step) * 1.35))
            trend_score = max(-100.0, min(100.0, step_return / max(volatility, 0.0025) * 20))
            points.append(
                {
                    "trade_date": forecast_dates[step - 1].strftime("%Y%m%d"),
                    "price": round(current, 4),
                    "upper": round(current * (1 + confidence), 4),
                    "lower": round(max(0.01, current * (1 - confidence)), 4),
                    "confidence": round(max(0.35, 0.82 - confidence * 2.2), 4),
                    "trend_score": round(trend_score, 4),
                    "source": "quant_regime_projection",
                }
            )
        return points

    def _forecast_with_trading_dates(
        self,
        forecast_series: list[dict[str, Any]],
        *,
        base_date: str,
    ) -> list[dict[str, Any]]:
        if not forecast_series:
            return []
        parsed_base = self._parse_trade_date(base_date).date()
        dates = self._future_trading_dates(parsed_base, len(forecast_series))
        result: list[dict[str, Any]] = []
        for index, point in enumerate(forecast_series):
            item = dict(point)
            item["trade_date"] = dates[index].strftime("%Y%m%d")
            result.append(item)
        return result

    def _future_trading_dates(self, base_date: date, horizon: int) -> list[date]:
        dates: list[date] = []
        probe = base_date + timedelta(days=1)
        while len(dates) < horizon:
            try:
                next_day = trading_calendar_service.next_trading_day(probe)
            except Exception:
                next_day = self._next_weekday(probe)
            if dates and next_day <= dates[-1]:
                next_day = self._next_weekday(dates[-1] + timedelta(days=1))
            dates.append(next_day)
            probe = next_day + timedelta(days=1)
        return dates

    def _next_weekday(self, current: date) -> date:
        probe = current
        while probe.weekday() >= 5:
            probe += timedelta(days=1)
        return probe

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

    def _merge_realtime_quote(
        self,
        price_series: list[dict[str, Any]],
        *,
        symbol: str,
        realtime_quote: dict[str, Any] | None = None,
        intraday_series: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        quote = realtime_quote
        if quote is None:
            try:
                quotes = market_data_service.get_quotes([normalize_symbol(symbol)], prefer_realtime=True)
            except Exception:
                return price_series
            if not quotes:
                return price_series
            quote = quotes[0]
        if str(quote.get("source") or "") == "fallback":
            return price_series
        price = self._safe_float(quote.get("price"))
        if price is None or price <= 0:
            return price_series
        quote_date = self._quote_trade_date(quote)
        if not quote_date:
            return price_series

        rows = [self._raw_price_row(point) for point in price_series]
        latest_date = str(rows[-1].get("trade_date") or "") if rows else ""
        realtime_row = self._intraday_daily_row(
            quote=quote,
            quote_date=quote_date,
            price=price,
            intraday_series=intraday_series or [],
        ) or self._realtime_price_row(
            quote=quote,
            quote_date=quote_date,
            price=price,
            previous=rows[-1] if rows else None,
        )
        if latest_date == quote_date:
            rows[-1] = self._merge_same_day_realtime_row(rows[-1], realtime_row)
        elif not latest_date or quote_date > latest_date:
            rows.append(realtime_row)
        else:
            return price_series
        return self._enrich_price_points(rows)

    def _intraday_daily_row(
        self,
        *,
        quote: dict[str, Any],
        quote_date: str,
        price: float,
        intraday_series: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        day_points = sorted(
            [
                point
                for point in intraday_series
                if self._compact_trade_date(str(point.get("trade_date") or "")) == quote_date
            ],
            key=lambda point: self._series_sort_key(str(point.get("trade_date") or "")),
        )
        if not day_points:
            return None
        open_price = self._safe_float(day_points[0].get("open")) or price
        highs = [self._safe_float(point.get("high")) for point in day_points]
        lows = [self._safe_float(point.get("low")) for point in day_points]
        highs = [value for value in highs if value is not None]
        lows = [value for value in lows if value is not None]
        high = max([open_price, price, *highs])
        low = min([open_price, price, *lows])
        quote_amount = self._safe_float(quote.get("amount")) or 0.0
        intraday_amount = sum(float(point.get("amount") or 0) for point in day_points)
        source = str(quote.get("source") or day_points[-1].get("source") or "realtime")
        return {
            "trade_date": quote_date,
            "open": round(open_price, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(price, 4),
            "volume": sum(float(point.get("volume") or 0) for point in day_points),
            "amount": max(quote_amount, intraday_amount),
            "source": source,
            "timestamp": str(quote.get("timestamp") or day_points[-1].get("timestamp") or ""),
            "is_realtime": True,
        }

    def _merge_same_day_realtime_row(
        self,
        existing: dict[str, Any],
        realtime: dict[str, Any],
    ) -> dict[str, Any]:
        realtime_close = self._safe_float(realtime.get("close"))
        existing_high = self._safe_float(existing.get("high"))
        existing_low = self._safe_float(existing.get("low"))
        realtime_high = self._safe_float(realtime.get("high"))
        realtime_low = self._safe_float(realtime.get("low"))
        existing_amount = self._safe_float(existing.get("amount")) or 0.0
        realtime_amount = self._safe_float(realtime.get("amount")) or 0.0
        merged = dict(existing)
        if realtime_close is not None and realtime_close > 0:
            merged["close"] = round(realtime_close, 4)
            merged["high"] = round(
                max(
                    value
                    for value in (existing_high, realtime_high, realtime_close)
                    if value is not None
                ),
                4,
            )
            merged["low"] = round(
                min(
                    value
                    for value in (existing_low, realtime_low, realtime_close)
                    if value is not None
                ),
                4,
            )
        if realtime_amount > 0:
            merged["amount"] = max(existing_amount, realtime_amount)
        merged["source"] = str(realtime.get("source") or existing.get("source") or "realtime")
        merged["timestamp"] = str(realtime.get("timestamp") or existing.get("timestamp") or "")
        merged["is_realtime"] = True
        return merged

    def _realtime_price_row(
        self,
        *,
        quote: dict[str, Any],
        quote_date: str,
        price: float,
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        previous_close = self._safe_float(previous.get("close")) if previous else None
        change_pct = self._safe_float(quote.get("change_pct"))
        estimated_prev_close = None
        if change_pct is not None and change_pct > -99.0:
            estimated_prev_close = price / (1 + change_pct / 100)
        open_price = estimated_prev_close or previous_close or price
        high_candidates = [open_price, price]
        low_candidates = [open_price, price]
        if previous_close is not None:
            high_candidates.append(previous_close)
            low_candidates.append(previous_close)
        return {
            "trade_date": quote_date,
            "open": round(open_price, 4),
            "high": round(max(high_candidates), 4),
            "low": round(min(low_candidates), 4),
            "close": round(price, 4),
            "volume": 0.0,
            "amount": float(self._safe_float(quote.get("amount")) or 0),
            "source": str(quote.get("source") or "realtime"),
            "timestamp": str(quote.get("timestamp") or ""),
            "is_realtime": True,
        }

    def _raw_price_row(self, point: dict[str, Any]) -> dict[str, Any]:
        return {
            "trade_date": str(point.get("trade_date") or ""),
            "open": float(point.get("open") or point.get("close") or 0),
            "high": float(point.get("high") or point.get("close") or 0),
            "low": float(point.get("low") or point.get("close") or 0),
            "close": float(point.get("close") or 0),
            "volume": float(point.get("volume") or 0),
            "amount": float(point.get("amount") or 0),
            "source": point.get("source"),
            "timestamp": point.get("timestamp"),
            "is_realtime": bool(point.get("is_realtime")),
        }

    def _quote_trade_date(self, quote: dict[str, Any]) -> str | None:
        timestamp = str(quote.get("timestamp") or "").strip()
        digits = "".join(char for char in timestamp if char.isdigit())
        if len(digits) >= 8:
            return digits[:8]
        return datetime.now(MARKET_TIMEZONE).strftime("%Y%m%d")

    def _compact_trade_date(self, value: str) -> str:
        return "".join(char for char in value if char.isdigit())[:8]

    def _safe_float(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(numeric):
            return None
        return numeric

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
        compact = self._compact_trade_date(value)
        try:
            return datetime.strptime(compact, "%Y%m%d")
        except ValueError:
            return datetime.utcnow()

    def _series_sort_key(self, value: str) -> int:
        digits = "".join(char for char in value if char.isdigit())
        if len(digits) >= 12:
            return int(digits[:12])
        if len(digits) >= 8:
            return int(f"{digits[:8]}0000")
        return 0

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

    def _linear_regression_slope(self, values: list[float]) -> float:
        if len(values) < 3:
            return 0.0
        y_mean = mean(values)
        x_values = list(range(len(values)))
        x_mean = mean(x_values)
        denominator = sum((x - x_mean) ** 2 for x in x_values)
        if denominator <= 0 or values[-1] <= 0:
            return 0.0
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values)) / denominator
        return slope / values[-1]

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
