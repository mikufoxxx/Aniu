from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


class AShareRetailAnalysisService:
    """Beginner-oriented A-share analysis built from the quant candidate payload."""

    def build(self, candidate: dict[str, Any]) -> dict[str, Any]:
        dimensions = [
            self._technical(candidate),
            self._liquidity(candidate),
            self._valuation(candidate),
            self._capital_flow(candidate),
            self._financial_quality(candidate),
            self._risk(candidate),
        ]
        score = self._weighted_score(dimensions)
        decision = self._decision(score, dimensions)
        price = _number(candidate.get("price"))
        return {
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name") or candidate.get("symbol"),
            "overall_score": score,
            "decision": decision["decision"],
            "action_signal": decision["label"],
            "risk_level": decision["risk_level"],
            "dimension_scores": dimensions,
            "key_reasons": self._collect(dimensions, "reasons", limit=6),
            "warnings": self._collect(dimensions, "warnings", limit=6),
            "entry_zone": self._entry_zone(price, decision["decision"], candidate),
            "sell_plan": self._sell_plan(price, decision["decision"], candidate),
            "retail_notes": [
                "只适合小仓位验证，不要满仓追涨。",
                "优先看 1-5 个交易日反馈，跌破纪律价先退出。",
            ],
        }

    def _technical(self, candidate: dict[str, Any]) -> dict[str, Any]:
        daily = candidate.get("daily_factors") or {}
        score = 50.0
        reasons: list[str] = []
        warnings: list[str] = []
        change_pct = _number(candidate.get("change_pct")) or 0.0
        daily_momentum = _number(daily.get("momentum_pct"))
        volatility = _number(daily.get("volatility_pct"))

        if change_pct >= 3:
            score += 12
            reasons.append("当日动量较强")
        elif change_pct <= -3:
            score -= 12
            warnings.append("当日跌幅偏大")
        if daily_momentum is not None:
            if daily_momentum >= 8:
                score += 12
                reasons.append("阶段趋势向上")
            elif daily_momentum < 0:
                score -= 10
                warnings.append("阶段动量偏弱")
        if volatility is not None and volatility >= 8:
            score -= 8
            warnings.append("近期波动偏大")
        return self._dimension("technical", "技术面", score, 0.28, reasons, warnings)

    def _liquidity(self, candidate: dict[str, Any]) -> dict[str, Any]:
        amount = _number(candidate.get("amount")) or 0.0
        turnover = _number(candidate.get("turnover")) or 0.0
        volume_ratio = _number(candidate.get("volume_ratio")) or 0.0
        score = 45.0
        reasons: list[str] = []
        warnings: list[str] = []
        if amount >= 500_000_000:
            score += 18
            reasons.append("成交额足够，散户进出相对容易")
        elif amount < 80_000_000:
            score -= 16
            warnings.append("成交额偏低，流动性不足")
        if turnover >= 1:
            score += 8
        if volume_ratio >= 1.5:
            score += 8
            reasons.append("量能放大")
        elif volume_ratio < 0.7:
            score -= 6
            warnings.append("量能不足")
        return self._dimension("liquidity", "流动性", score, 0.18, reasons, warnings)

    def _valuation(self, candidate: dict[str, Any]) -> dict[str, Any]:
        daily = candidate.get("daily_factors") or {}
        pe = _number(daily.get("pe_ttm"))
        pb = _number(daily.get("pb"))
        score = 55.0
        reasons: list[str] = []
        warnings: list[str] = []
        if pe is not None:
            if 0 < pe <= 35:
                score += 12
                reasons.append("估值处于可跟踪区间")
            elif pe > 70:
                score -= 18
                warnings.append("市盈率偏高")
            elif pe <= 0:
                score -= 10
                warnings.append("市盈率为负或不可比")
        if pb is not None and pb > 8:
            score -= 8
            warnings.append("市净率偏高")
        return self._dimension("valuation", "估值", score, 0.14, reasons, warnings)

    def _capital_flow(self, candidate: dict[str, Any]) -> dict[str, Any]:
        daily = candidate.get("daily_factors") or {}
        score = 52.0
        reasons: list[str] = []
        warnings: list[str] = []
        net_amount = _number(daily.get("moneyflow_net_amount"))
        large_rate = _number(daily.get("moneyflow_buy_lg_amount_rate"))
        if net_amount is not None:
            if net_amount > 0:
                score += 12
                reasons.append("资金净流入")
            elif net_amount < 0:
                score -= 12
                warnings.append("资金净流出")
        if large_rate is not None and large_rate > 0:
            score += 6
            reasons.append("大单买入占比为正")
        return self._dimension("capital_flow", "资金流", score, 0.14, reasons, warnings)

    def _financial_quality(self, candidate: dict[str, Any]) -> dict[str, Any]:
        financial = candidate.get("financial_factors") or {}
        score = 52.0
        reasons: list[str] = []
        warnings: list[str] = []
        roe = _number(financial.get("roe")) or _number(financial.get("roe_dt"))
        profit_yoy = _number(financial.get("netprofit_yoy"))
        debt = _number(financial.get("debt_to_assets"))
        if roe is not None:
            if roe >= 12:
                score += 12
                reasons.append("ROE 表现较好")
            elif roe < 5:
                score -= 10
                warnings.append("ROE 偏低")
        if profit_yoy is not None:
            if profit_yoy >= 10:
                score += 8
                reasons.append("利润同比增长")
            elif profit_yoy < 0:
                score -= 10
                warnings.append("利润同比下滑")
        if debt is not None and debt >= 70:
            score -= 8
            warnings.append("资产负债率偏高")
        return self._dimension("financial_quality", "财务质量", score, 0.14, reasons, warnings)

    def _risk(self, candidate: dict[str, Any]) -> dict[str, Any]:
        daily = candidate.get("daily_factors") or {}
        score = 60.0
        warnings: list[str] = []
        if daily.get("limit_event"):
            score -= 10
            warnings.append("近期存在涨跌停事件，追高风险更高")
        pledge = daily.get("pledge_stat") or {}
        pledge_ratio = _number(pledge.get("pledge_ratio")) if isinstance(pledge, dict) else None
        if pledge_ratio is not None and pledge_ratio >= 35:
            score -= 12
            warnings.append("股权质押比例偏高")
        return self._dimension("risk_control", "风险纪律", score, 0.12, [], warnings)

    def _dimension(
        self,
        key: str,
        label: str,
        score: float,
        weight: float,
        reasons: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "score": round(max(0.0, min(100.0, score)), 2),
            "weight": weight,
            "reasons": reasons,
            "warnings": warnings,
        }

    def _weighted_score(self, dimensions: list[dict[str, Any]]) -> float:
        weight_sum = sum(float(item["weight"]) for item in dimensions)
        if weight_sum <= 0:
            return 0.0
        return round(sum(float(item["score"]) * float(item["weight"]) for item in dimensions) / weight_sum, 2)

    def _decision(self, score: float, dimensions: list[dict[str, Any]]) -> dict[str, str]:
        warnings = self._collect(dimensions, "warnings", limit=10)
        risk_level = "高" if score < 45 or len(warnings) >= 4 else "中" if score < 65 or warnings else "低"
        if score >= 72 and risk_level != "高":
            return {"decision": "buy", "label": "可小仓试探", "risk_level": risk_level}
        if score >= 62:
            return {"decision": "watch", "label": "重点观察", "risk_level": risk_level}
        if score >= 50:
            return {"decision": "hold", "label": "只跟踪", "risk_level": risk_level}
        return {"decision": "avoid", "label": "暂不参与", "risk_level": risk_level}

    def _entry_zone(
        self,
        price: float | None,
        decision: str,
        candidate: dict[str, Any],
    ) -> dict[str, Any] | None:
        if price is None or price <= 0 or decision == "avoid":
            return None
        low = price * (0.985 if decision == "buy" else 0.96)
        high = price * (1.01 if decision == "buy" else 0.995)
        return {
            "low": round(low, 3),
            "high": round(max(low, high), 3),
            "label": "现价附近分批，不追高" if decision == "buy" else "等回踩到支撑附近",
        }

    def _sell_plan(
        self,
        price: float | None,
        decision: str,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        if price is None or price <= 0:
            return {"stop_loss": None, "first_target": None, "rule": "缺少价格时不交易。"}
        stop_loss = price * (0.94 if decision == "buy" else 0.96)
        first_target = price * (1.06 if decision == "buy" else 1.04)
        second_target = price * (1.12 if decision == "buy" else 1.08)
        return {
            "stop_loss": round(stop_loss, 3),
            "first_target": round(first_target, 3),
            "second_target": round(second_target, 3),
            "rule": "跌破止损先退出；达到第一目标先减仓，第二目标不强留。",
        }

    def _collect(
        self,
        dimensions: list[dict[str, Any]],
        key: str,
        *,
        limit: int,
    ) -> list[str]:
        items: list[str] = []
        for dimension in dimensions:
            for item in dimension.get(key) or []:
                text = str(item).strip()
                if text and text not in items:
                    items.append(text)
        return items[:limit]


a_share_retail_analysis_service = AShareRetailAnalysisService()
