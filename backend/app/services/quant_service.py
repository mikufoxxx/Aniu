from __future__ import annotations

import math
from typing import Any

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
    def generate_candidates(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int = 20,
        prefer_realtime: bool = True,
    ) -> dict[str, Any]:
        universe = [normalize_symbol(symbol) for symbol in (symbols or DEFAULT_UNIVERSE)]
        quotes = market_data_service.get_quotes(universe, prefer_realtime=prefer_realtime)
        candidates = [self._score_quote(item) for item in quotes]
        candidates.sort(key=lambda item: item["score"], reverse=True)
        selected = candidates[: max(1, min(limit, len(candidates)))]
        data_sources = sorted(
            {str(item.get("source")) for item in selected if item.get("source")}
        )
        return {
            "universe_size": len(universe),
            "candidate_count": len(selected),
            "data_sources": data_sources,
            "candidates": selected,
        }

    def _score_quote(self, quote: dict[str, Any]) -> dict[str, Any]:
        change_pct = _number(quote.get("change_pct"))
        amount = max(_number(quote.get("amount")), 0.0)
        turnover = max(_number(quote.get("turnover")), 0.0)
        volume_ratio = max(_number(quote.get("volume_ratio"), 1.0), 0.0)

        factor_scores = {
            "momentum": max(min(change_pct / 10.0, 1.0), -1.0),
            "amount": min(math.log10(amount + 1) / 10.0, 1.0),
            "turnover": min(turnover / 8.0, 1.0),
            "volume_ratio": min(volume_ratio / 3.0, 1.0),
        }
        score = (
            factor_scores["momentum"] * 40
            + factor_scores["amount"] * 30
            + factor_scores["turnover"] * 15
            + factor_scores["volume_ratio"] * 15
        )
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
            "rationale": self._rationale(change_pct, amount, volume_ratio),
        }

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
