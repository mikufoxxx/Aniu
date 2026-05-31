from __future__ import annotations

from typing import Any

from app.services.a_share_retail_analysis_service import a_share_retail_analysis_service


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AISelectionService:
    """AI-ready A-share selection context built from quote, daily and retail signals."""

    def enrich(self, candidate: dict[str, Any]) -> dict[str, Any]:
        item = dict(candidate)
        retail = a_share_retail_analysis_service.build(item)
        temporal = self._temporal_profile(item)
        risk_flags = self._risk_flags(item, retail, temporal)
        reasons = self._reasons(item, retail, temporal, risk_flags)
        score = self._score(item, retail, temporal, risk_flags)
        item["retail_analysis"] = retail
        item["ai_selection"] = {
            "score": score,
            "temporal_profile": temporal,
            "risk_flags": risk_flags,
            "selection_reasons": reasons,
            "data_used": self._data_used(item),
        }
        return item

    def _score(
        self,
        candidate: dict[str, Any],
        retail: dict[str, Any],
        temporal: dict[str, Any],
        risk_flags: list[str],
    ) -> float:
        base_score = _number(candidate.get("score"))
        retail_score = _number(retail.get("overall_score"), 50.0)
        score = base_score * 0.42 + retail_score * 0.38
        if temporal["above_ma20"]:
            score += 5
        if temporal["above_ma60"]:
            score += 8
        if temporal["momentum_alignment"] == "uptrend":
            score += 8
        elif temporal["momentum_alignment"] == "downtrend":
            score -= 16
        if temporal["range_position_pct"] >= 92:
            score -= 6
        if temporal["max_drawdown_pct"] <= -18:
            score -= 8
        if temporal["volatility_pct"] >= 8:
            score -= 6
        score -= len(risk_flags) * 7
        return round(max(0.0, min(120.0, score)), 4)

    def _temporal_profile(self, candidate: dict[str, Any]) -> dict[str, Any]:
        daily = candidate.get("daily_factors") or {}
        momentum = _number(daily.get("momentum_pct"))
        recent_momentum = _number(daily.get("recent_momentum_pct"))
        above_ma20 = bool(daily.get("above_ma20"))
        above_ma60 = bool(daily.get("above_ma60"))
        if above_ma20 and above_ma60 and momentum > 0 and recent_momentum >= 0:
            alignment = "uptrend"
        elif not above_ma60 or momentum < 0:
            alignment = "downtrend"
        else:
            alignment = "mixed"
        return {
            "latest_trade_date": daily.get("latest_trade_date"),
            "bars_used": int(daily.get("bars_used") or 0),
            "momentum_pct": round(momentum, 4),
            "recent_momentum_pct": round(recent_momentum, 4),
            "ma20": daily.get("ma20"),
            "ma60": daily.get("ma60"),
            "above_ma20": above_ma20,
            "above_ma60": above_ma60,
            "distance_to_ma20_pct": _number(daily.get("distance_to_ma20_pct")),
            "distance_to_ma60_pct": _number(daily.get("distance_to_ma60_pct")),
            "range_position_pct": _number(daily.get("range_position_pct"), 50.0),
            "max_drawdown_pct": _number(daily.get("max_drawdown_pct")),
            "volatility_pct": _number(daily.get("volatility_pct")),
            "momentum_alignment": alignment,
        }

    def _risk_flags(
        self,
        candidate: dict[str, Any],
        retail: dict[str, Any],
        temporal: dict[str, Any],
    ) -> list[str]:
        flags: list[str] = []
        change_pct = _number(candidate.get("change_pct"))
        amount = _number(candidate.get("amount"))
        if temporal["bars_used"] >= 20 and not temporal["above_ma20"]:
            flags.append("below_ma20")
        if temporal["bars_used"] >= 60 and not temporal["above_ma60"]:
            flags.append("below_ma60")
        if change_pct >= 7:
            flags.append("overheated_intraday")
        if amount < 80_000_000:
            flags.append("illiquid")
        if retail.get("risk_level") == "高":
            flags.append("high_retail_risk")
        if str(retail.get("decision") or "") == "avoid":
            flags.append("retail_avoid")
        return flags

    def _reasons(
        self,
        candidate: dict[str, Any],
        retail: dict[str, Any],
        temporal: dict[str, Any],
        risk_flags: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if temporal["momentum_alignment"] == "uptrend":
            reasons.append("日线趋势与短期动量同向")
        if temporal["above_ma60"]:
            reasons.append("价格站在 60 日线之上")
        if _number(candidate.get("amount")) >= 500_000_000:
            reasons.append("成交额适合散户进出")
        for reason in retail.get("key_reasons") or []:
            text = str(reason).strip()
            if text and text not in reasons:
                reasons.append(text)
        for flag in risk_flags:
            reasons.append(f"风险扣分: {flag}")
        return reasons[:8]

    def _data_used(self, candidate: dict[str, Any]) -> list[str]:
        sources = ["realtime_quote"]
        daily = candidate.get("daily_factors") or {}
        if int(daily.get("bars_used") or 0) > 0:
            sources.append("daily_history")
        if _number(daily.get("moneyflow_net_amount")):
            sources.append("moneyflow")
        if candidate.get("financial_factors"):
            sources.append("financial_indicator")
        if candidate.get("profile"):
            sources.append("stock_profile")
        return sources


ai_selection_service = AISelectionService()
