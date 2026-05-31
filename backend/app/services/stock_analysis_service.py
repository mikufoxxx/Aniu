from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.services.a_share_retail_analysis_service import a_share_retail_analysis_service
from app.core.config import get_settings
from app.services.ai_stock_picker_service import ai_stock_picker_service
from app.services.llm_service import llm_service
from app.services.market_data_service import normalize_symbol
from app.services.settings_service import settings_service


class StockAnalysisService:
    def analyze(
        self,
        db: Session,
        *,
        symbol: str,
        initial_cash: float,
    ) -> dict[str, Any]:
        settings = get_settings()
        normalized_symbol = normalize_symbol(symbol)
        snapshot = ai_stock_picker_service.build_snapshot(
            db=db,
            symbols=[normalized_symbol],
            limit=1,
            prefer_realtime=True,
            lookback_days=settings.market_data_maintenance_lookback_days,
        )
        candidate = next(
            (
                item
                for item in snapshot["recommendations"]
                if str(item.get("symbol") or "").upper() == normalized_symbol
            ),
            None,
        )
        if candidate is None:
            raise ValueError("没有找到该股票的可分析数据，请先刷新行情和日线数据。")

        decision = self._heuristic_decision(
            candidate=candidate,
            initial_cash=initial_cash,
        )
        retail_analysis = a_share_retail_analysis_service.build(candidate)
        llm_decision = self._llm_decision(
            db=db,
            candidate=candidate,
            context=snapshot["context"],
            decision=decision,
            retail_analysis=retail_analysis,
        )
        if llm_decision.get("used"):
            decision = self._merge_llm_decision(decision, llm_decision)

        return {
            "symbol": normalized_symbol,
            "name": candidate.get("name") or normalized_symbol,
            "price": float(candidate.get("price") or 0),
            "score": float(candidate.get("score") or 0),
            "action": decision["action"],
            "rating": decision["rating"],
            "reason": decision["reason"],
            "decision": decision,
            "retail_analysis": retail_analysis,
            "llm_decision": llm_decision,
            "data_sources": snapshot["data_sources"],
            "context": snapshot["context"],
            "stock_pick_snapshot": snapshot,
        }

    def _heuristic_decision(
        self,
        *,
        candidate: dict[str, Any],
        initial_cash: float,
    ) -> dict[str, Any]:
        score = float(candidate.get("score") or 0)
        price = float(candidate.get("price") or 0)
        if score >= 70:
            action = "BUY"
            rating = "强关注"
            allocation_ratio = 0.2
        elif score >= 50:
            action = "BUY"
            rating = "轻仓关注"
            allocation_ratio = 0.1
        else:
            action = "HOLD"
            rating = "观望"
            allocation_ratio = 0.0

        quantity = 0
        if action == "BUY" and price > 0:
            budget = initial_cash * allocation_ratio
            quantity = int(budget // (price * 100)) * 100
            if quantity <= 0:
                action = "HOLD"
                rating = "资金不足"
                allocation_ratio = 0.0

        return {
            "action": action,
            "rating": rating,
            "target_allocation_ratio": allocation_ratio,
            "suggested_quantity": quantity,
            "suggested_amount": round(quantity * price, 2),
            "reason": (
                f"量化评分 {score:.2f}，现价 {price:.2f}，"
                f"建议{rating}。"
            ),
        }

    def _llm_decision(
        self,
        *,
        db: Session,
        candidate: dict[str, Any],
        context: str,
        decision: dict[str, Any],
        retail_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        app_settings = settings_service.get_or_create_settings(db)
        base_url = str(getattr(app_settings, "llm_base_url", None) or "").strip()
        api_key = str(getattr(app_settings, "llm_api_key", None) or "").strip()
        model = str(getattr(app_settings, "llm_model", None) or "").strip()
        if not base_url or not api_key or not model:
            return {"used": False}

        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "你是A股单股分析助手。只返回JSON，不要输出Markdown。",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "candidate": candidate,
                            "retail_analysis": retail_analysis,
                            "market_context": context,
                            "fallback_decision": decision,
                            "output_schema": {
                                "action": "BUY, HOLD or SELL",
                                "rating": "一句短评级",
                                "target_allocation_ratio": "0到1之间的小数",
                                "reason": "一句话说明依据",
                            },
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
        }
        try:
            response = llm_service._call_llm(
                base_url=base_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=60,
            )
            raw = self._extract_json(response)
            action = str(raw.get("action") or "").upper()
            if action not in {"BUY", "HOLD", "SELL"}:
                action = decision["action"]
            return {
                "used": True,
                "model": model,
                "raw_decision": raw,
                "action": action,
                "rating": str(raw.get("rating") or decision["rating"]),
                "target_allocation_ratio": self._normalize_ratio(
                    raw.get("target_allocation_ratio"),
                    fallback=decision["target_allocation_ratio"],
                ),
                "reason": str(raw.get("reason") or decision["reason"]),
            }
        except Exception:
            return {"used": False}

    def _merge_llm_decision(
        self,
        decision: dict[str, Any],
        llm_decision: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(decision)
        merged["action"] = llm_decision["action"]
        merged["rating"] = llm_decision["rating"]
        merged["target_allocation_ratio"] = llm_decision["target_allocation_ratio"]
        merged["reason"] = llm_decision["reason"]
        return merged

    def _normalize_ratio(self, value: Any, *, fallback: float) -> float:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            ratio = fallback
        if ratio > 1:
            ratio = ratio / 100
        return min(max(ratio, 0.0), 0.95)

    def _extract_json(self, response: dict[str, Any]) -> dict[str, Any]:
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM 未返回分析内容")
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 分析不是 JSON 对象")
        return parsed


stock_analysis_service = StockAnalysisService()
