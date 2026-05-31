from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyBar
from app.services.chart_data_service import chart_data_service
from app.services.historical_data_service import _normalize_trade_date
from app.services.market_data_service import normalize_symbol


class QuantResearchService:
    def run_research(
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
            if bar.close and float(bar.close) > 0:
                by_symbol[bar.symbol].append(bar)

        profiles = [
            self._profile_symbol(symbol_bars)
            for symbol_bars in by_symbol.values()
            if len(symbol_bars) >= 2
        ]
        if not profiles:
            raise ValueError("可用日频数据不足，至少需要每只股票两根日线。")

        strategies = [
            self._daily_momentum(profiles, initial_cash),
            self._equal_weight(profiles, initial_cash),
            self._low_drawdown(profiles, initial_cash),
        ]
        strategies.sort(key=lambda item: item["score"], reverse=True)
        best_strategy = strategies[0]
        return {
            "symbol_count": len(set(normalized_symbols)),
            "bar_count": len(bars),
            "start_date": start,
            "end_date": end,
            "initial_cash": initial_cash,
            "best_strategy": best_strategy,
            "strategies": strategies,
            "comparison_chart": self._comparison_chart(strategies),
            "ai_learning_context": self._learning_context(best_strategy, strategies),
        }

    def _profile_symbol(self, bars: list[DailyBar]) -> dict[str, Any]:
        first = bars[0]
        last = bars[-1]
        first_close = float(first.close or 0)
        last_close = float(last.close or 0)
        returns = [
            (float(bars[index].close or 0) - float(bars[index - 1].close or 0))
            / float(bars[index - 1].close or 1)
            for index in range(1, len(bars))
            if float(bars[index - 1].close or 0) > 0
        ]
        curve = [float(bar.close or 0) / first_close for bar in bars]
        return {
            "symbol": first.symbol,
            "bars": bars,
            "price_series": [
                {
                    "trade_date": bar.trade_date,
                    "open": round(float(bar.open or bar.close or 0), 4),
                    "high": round(float(bar.high or bar.close or 0), 4),
                    "low": round(float(bar.low or bar.close or 0), 4),
                    "close": round(float(bar.close or 0), 4),
                    "volume": float(bar.vol or 0),
                    "amount": float(bar.amount or 0),
                    "ma5": None,
                    "ma20": None,
                }
                for bar in bars
            ],
            "first_close": first_close,
            "last_close": last_close,
            "period_return": (last_close - first_close) / first_close,
            "max_drawdown": self._max_drawdown(curve),
            "volatility": mean(abs(value) for value in returns) if returns else 0.0,
        }

    def _daily_momentum(self, profiles: list[dict[str, Any]], initial_cash: float) -> dict[str, Any]:
        selected = max(profiles, key=lambda item: item["period_return"])
        result = self._single_symbol_trade(
            selected,
            initial_cash,
            strategy_name="daily_momentum",
            display_name="日线动量",
            reason="选择区间涨幅最高的股票，验证顺势策略是否优于分散或低波动方案。",
        )
        result["score"] = round(result["return_ratio"] * 100 - abs(result["max_drawdown"]) * 25, 4)
        return result

    def _low_drawdown(self, profiles: list[dict[str, Any]], initial_cash: float) -> dict[str, Any]:
        selected = min(
            profiles,
            key=lambda item: (
                item["volatility"],
                abs(item["max_drawdown"]),
                -item["period_return"],
            ),
        )
        result = self._single_symbol_trade(
            selected,
            initial_cash,
            strategy_name="low_drawdown",
            display_name="低波动回撤",
            reason="优先选择日收益波动更低的标的，观察稳健风格在同一股票池里的防守能力。",
        )
        result["score"] = round(result["return_ratio"] * 100 - selected["volatility"] * 80 - abs(result["max_drawdown"]) * 30, 4)
        return result

    def _equal_weight(self, profiles: list[dict[str, Any]], initial_cash: float) -> dict[str, Any]:
        cash_per_symbol = initial_cash / len(profiles)
        trades: list[dict[str, Any]] = []
        positions: list[dict[str, Any]] = []
        cash = initial_cash
        for profile in profiles:
            buy_bar = profile["bars"][0]
            sell_bar = profile["bars"][-1]
            buy_price = profile["first_close"]
            quantity = int(cash_per_symbol // (buy_price * 100)) * 100
            buy_amount = quantity * buy_price
            cash -= buy_amount
            positions.append(
                {
                    "profile": profile,
                    "quantity": quantity,
                    "buy_bar": buy_bar,
                    "sell_bar": sell_bar,
                }
            )
            trades.append(self._trade("BUY", profile["symbol"], buy_bar.trade_date, buy_price, quantity))

        trade_dates = [bar.trade_date for bar in profiles[0]["bars"]]
        equity_curve: list[float] = []
        for index, _trade_date in enumerate(trade_dates):
            equity = cash
            for position in positions:
                bars = position["profile"]["bars"]
                bar = bars[min(index, len(bars) - 1)]
                equity += position["quantity"] * float(bar.close or 0)
            equity_curve.append(equity)

        final_assets = equity_curve[-1] if equity_curve else initial_cash
        for position in positions:
            profile = position["profile"]
            trades.append(
                self._trade(
                    "SELL",
                    profile["symbol"],
                    position["sell_bar"].trade_date,
                    profile["last_close"],
                    position["quantity"],
                )
            )
        return_ratio = (final_assets - initial_cash) / initial_cash
        max_drawdown = self._max_drawdown(equity_curve)
        equity_points = chart_data_service.equity_curve_from_values(
            trade_dates=trade_dates,
            values=equity_curve,
            initial_cash=initial_cash,
        )
        drawdown_points = chart_data_service.drawdown_curve_from_values(
            trade_dates=trade_dates,
            values=equity_curve,
        )
        return {
            "strategy_name": "equal_weight",
            "display_name": "等权分散",
            "selected_symbols": [profile["symbol"] for profile in profiles],
            "final_assets": round(final_assets, 2),
            "return_ratio": round(return_ratio, 6),
            "max_drawdown": round(max_drawdown, 6),
            "trade_count": len(trades),
            "score": round(return_ratio * 100 - abs(max_drawdown) * 20 + min(len(profiles), 5) * 0.05, 4),
            "reason": "同一股票池等权买入，作为量化研究里的分散基准。",
            "metrics": {"candidate_symbols": len(profiles), "cash_remaining": round(cash, 2)},
            "trades": trades,
            "charts": chart_data_service.strategy_charts(
                price_series=profiles[0]["price_series"],
                trades=trades,
                equity_curve=equity_points,
                drawdown_curve=drawdown_points,
            ),
        }

    def _single_symbol_trade(
        self,
        profile: dict[str, Any],
        initial_cash: float,
        *,
        strategy_name: str,
        display_name: str,
        reason: str,
    ) -> dict[str, Any]:
        quantity = int(initial_cash // (profile["first_close"] * 100)) * 100
        buy_amount = quantity * profile["first_close"]
        cash = initial_cash - buy_amount
        final_assets = cash + quantity * profile["last_close"]
        return_ratio = (final_assets - initial_cash) / initial_cash
        equity_curve = [cash + quantity * float(bar.close or 0) for bar in profile["bars"]]
        trade_dates = [bar.trade_date for bar in profile["bars"]]
        trades = [
            self._trade("BUY", profile["symbol"], profile["bars"][0].trade_date, profile["first_close"], quantity),
            self._trade("SELL", profile["symbol"], profile["bars"][-1].trade_date, profile["last_close"], quantity),
        ]
        equity_points = chart_data_service.equity_curve_from_values(
            trade_dates=trade_dates,
            values=equity_curve,
            initial_cash=initial_cash,
        )
        drawdown_points = chart_data_service.drawdown_curve_from_values(
            trade_dates=trade_dates,
            values=equity_curve,
        )
        return {
            "strategy_name": strategy_name,
            "display_name": display_name,
            "selected_symbols": [profile["symbol"]],
            "final_assets": round(final_assets, 2),
            "return_ratio": round(return_ratio, 6),
            "max_drawdown": round(self._max_drawdown(equity_curve), 6),
            "trade_count": len(trades),
            "score": 0.0,
            "reason": reason,
            "metrics": {
                "candidate_symbols": 1,
                "period_return": round(profile["period_return"], 6),
                "volatility": round(profile["volatility"], 6),
                "quantity": quantity,
            },
            "trades": trades,
            "charts": chart_data_service.strategy_charts(
                price_series=profile["price_series"],
                trades=trades,
                equity_curve=equity_points,
                drawdown_curve=drawdown_points,
            ),
        }

    def _trade(self, action: str, symbol: str, trade_date: str, price: float, quantity: int) -> dict[str, Any]:
        return {
            "action": action,
            "symbol": symbol,
            "trade_date": trade_date,
            "price": round(price, 4),
            "quantity": quantity,
            "amount": round(price * quantity, 2),
        }

    def _max_drawdown(self, equity_curve: list[float]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = min(max_drawdown, (value - peak) / peak)
        return max_drawdown

    def _learning_context(self, best_strategy: dict[str, Any], strategies: list[dict[str, Any]]) -> str:
        rows = [
            f"{item['strategy_name']}: 收益 {item['return_ratio']:.2%}, 最大回撤 {item['max_drawdown']:.2%}, 标的 {','.join(item['selected_symbols'])}"
            for item in strategies
        ]
        return (
            "量化研究结论："
            f"当前样本最佳策略是 {best_strategy['strategy_name']}，"
            f"选择 {','.join(best_strategy['selected_symbols'])}。"
            "策略对比："
            + "；".join(rows)
        )

    def _comparison_chart(self, strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "strategy_name": item["strategy_name"],
                "display_name": item["display_name"],
                "return_ratio": item["return_ratio"],
                "max_drawdown": item["max_drawdown"],
                "score": item["score"],
            }
            for item in strategies
        ]


quant_research_service = QuantResearchService()
