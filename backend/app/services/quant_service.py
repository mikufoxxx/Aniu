from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import DailyBar
from app.services.market_data_service import DEFAULT_UNIVERSE, market_data_service, normalize_symbol


def _number(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(numeric):
        return default
    return numeric


class QuantService:
    _AUTO_UNIVERSE_LIMIT = 500

    def generate_candidates(
        self,
        *,
        db: Session | None = None,
        symbols: list[str] | None = None,
        limit: int = 20,
        prefer_realtime: bool = True,
        lookback_days: int = 20,
    ) -> dict[str, Any]:
        universe = self._resolve_universe(db, symbols, limit=limit)
        quotes = market_data_service.get_quotes(universe, prefer_realtime=prefer_realtime)
        daily_factors = self._daily_factors_by_symbol(
            db,
            symbols=universe,
            lookback_days=lookback_days,
        )
        candidates = [
            self._score_quote(item, daily_factors.get(normalize_symbol(str(item.get("symbol") or ""))))
            for item in quotes
        ]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        selected = candidates[: max(1, min(limit, len(candidates)))]
        data_sources = sorted(
            {str(item.get("source")) for item in selected if item.get("source")}
        )
        if any(item.get("daily_factors", {}).get("bars_used", 0) > 0 for item in selected):
            data_sources.append("tushare_daily")
        return {
            "universe_size": len(universe),
            "candidate_count": len(selected),
            "data_sources": data_sources,
            "candidates": selected,
        }

    def _resolve_universe(
        self,
        db: Session | None,
        symbols: list[str] | None,
        *,
        limit: int,
    ) -> list[str]:
        if symbols:
            return [normalize_symbol(symbol) for symbol in symbols]
        stored = self._stored_universe(db, limit=max(limit, self._AUTO_UNIVERSE_LIMIT))
        return stored or [normalize_symbol(symbol) for symbol in DEFAULT_UNIVERSE]

    def _stored_universe(self, db: Session | None, *, limit: int) -> list[str]:
        if db is None:
            return []
        trade_date = db.scalar(
            select(DailyBar.trade_date)
            .group_by(DailyBar.trade_date)
            .order_by(func.count(DailyBar.symbol).desc(), DailyBar.trade_date.desc())
            .limit(1)
        )
        if not trade_date:
            return []
        return list(
            db.scalars(
                select(DailyBar.symbol)
                .where(DailyBar.trade_date == trade_date)
                .order_by(DailyBar.amount.desc(), DailyBar.symbol)
                .limit(max(1, min(self._AUTO_UNIVERSE_LIMIT, int(limit))))
            ).all()
        )

    def build_dataset(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        limit: int = 50,
        prefer_realtime: bool = True,
        lookback_days: int = 20,
    ) -> dict[str, Any]:
        payload = self.generate_candidates(
            db=db,
            symbols=symbols,
            limit=limit,
            prefer_realtime=prefer_realtime,
            lookback_days=lookback_days,
        )
        items = payload["candidates"]
        coverage = {
            "realtime_symbols": sum(1 for item in items if item.get("source") != "fallback"),
            "daily_history_symbols": sum(
                1 for item in items if item.get("daily_factors", {}).get("bars_used", 0) > 0
            ),
            "symbols_with_price": sum(1 for item in items if item.get("price") is not None),
        }
        return {
            "universe_size": payload["universe_size"],
            "item_count": len(items),
            "lookback_days": lookback_days,
            "data_sources": payload["data_sources"],
            "coverage": coverage,
            "items": items,
        }

    def _score_quote(
        self,
        quote: dict[str, Any],
        daily_factors: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        change_pct = _number(quote.get("change_pct"))
        amount = max(_number(quote.get("amount")), 0.0)
        turnover = max(_number(quote.get("turnover")), 0.0)
        volume_ratio = max(_number(quote.get("volume_ratio"), 1.0), 0.0)
        daily = daily_factors or self._empty_daily_factors()

        factor_scores = {
            "momentum": max(min(change_pct / 10.0, 1.0), -1.0),
            "amount": min(math.log10(amount + 1) / 10.0, 1.0),
            "turnover": min(turnover / 8.0, 1.0),
            "volume_ratio": min(volume_ratio / 3.0, 1.0),
            "daily_momentum": max(
                min(_number(daily.get("momentum_pct")) / 15.0, 1.0),
                -1.0,
            ),
            "daily_liquidity": min(math.log10(max(_number(daily.get("avg_amount")), 0.0) + 1) / 8.0, 1.0),
        }
        score = self._weighted_score(factor_scores, daily["bars_used"])
        return {
            "symbol": normalize_symbol(str(quote.get("symbol") or "")),
            "name": str(quote.get("name") or ""),
            "price": quote.get("price"),
            "change_pct": change_pct,
            "amount": amount,
            "turnover": turnover,
            "volume_ratio": volume_ratio,
            "source": quote.get("source"),
            "timestamp": quote.get("timestamp"),
            "score": round(score, 4),
            "factor_scores": {
                key: round(value, 4) for key, value in factor_scores.items()
            },
            "daily_factors": daily,
            "rationale": self._rationale(change_pct, amount, volume_ratio),
        }

    def _weighted_score(self, factor_scores: dict[str, float], daily_bars_used: int) -> float:
        if daily_bars_used <= 0:
            score = (
                factor_scores["momentum"] * 40
                + factor_scores["amount"] * 30
                + factor_scores["turnover"] * 15
                + factor_scores["volume_ratio"] * 15
            )
        else:
            score = (
                factor_scores["momentum"] * 30
                + factor_scores["amount"] * 25
                + factor_scores["turnover"] * 10
                + factor_scores["volume_ratio"] * 10
                + factor_scores["daily_momentum"] * 20
                + factor_scores["daily_liquidity"] * 5
            )
        return round(score, 4)

    def _daily_factors_by_symbol(
        self,
        db: Session | None,
        *,
        symbols: list[str],
        lookback_days: int,
    ) -> dict[str, dict[str, Any]]:
        if db is None:
            return {}
        rows = db.scalars(
            select(DailyBar)
            .where(DailyBar.symbol.in_(symbols))
            .order_by(DailyBar.symbol, DailyBar.trade_date)
        ).all()
        by_symbol: dict[str, list[DailyBar]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        return {
            symbol: self._daily_factors(bars[-max(1, lookback_days):])
            for symbol, bars in by_symbol.items()
        }

    def _daily_factors(self, bars: list[DailyBar]) -> dict[str, Any]:
        usable = [bar for bar in bars if bar.close is not None]
        if not usable:
            return self._empty_daily_factors()
        first_close = float(usable[0].close or 0)
        latest = usable[-1]
        latest_close = float(latest.close or 0)
        momentum_pct = (
            (latest_close - first_close) / first_close * 100
            if first_close > 0
            else 0.0
        )
        returns: list[float] = []
        for previous, current in zip(usable, usable[1:]):
            previous_close = float(previous.close or 0)
            current_close = float(current.close or 0)
            if previous_close > 0:
                returns.append((current_close - previous_close) / previous_close * 100)
        avg_amount = sum(_number(bar.amount) for bar in usable) / len(usable)
        volatility_pct = self._stddev(returns)
        return {
            "latest_trade_date": latest.trade_date,
            "bars_used": len(usable),
            "latest_close": round(latest_close, 4),
            "momentum_pct": round(momentum_pct, 4),
            "avg_amount": round(avg_amount, 4),
            "volatility_pct": round(volatility_pct, 4),
        }

    def _empty_daily_factors(self) -> dict[str, Any]:
        return {
            "latest_trade_date": None,
            "bars_used": 0,
            "latest_close": None,
            "momentum_pct": 0.0,
            "avg_amount": 0.0,
            "volatility_pct": 0.0,
        }

    def _stddev(self, values: list[float]) -> float:
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        return math.sqrt(variance)

    def _rationale(self, change_pct: float, amount: float, volume_ratio: float) -> str:
        parts: list[str] = []
        if change_pct > 0:
            parts.append(f"涨幅 {change_pct:.2f}%")
        if amount > 0:
            parts.append(f"成交额约 {amount / 100000000:.1f} 亿")
        if volume_ratio > 1:
            parts.append(f"量比 {volume_ratio:.2f}")
        return "，".join(parts) or "基础行情完整，等待更多确认信号。"


quant_service = QuantService()
