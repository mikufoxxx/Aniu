from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import StockAnalysisReport
from app.services.a_share_retail_analysis_service import a_share_retail_analysis_service
from app.core.config import get_settings
from app.services.chart_data_service import chart_data_service
from app.services.ai_forecast_service import ai_forecast_service
from app.services.ai_stock_picker_service import ai_stock_picker_service
from app.services.llm_service import llm_service
from app.services.market_data_service import normalize_symbol
from app.services.settings_service import settings_service
from app.skills import skill_registry


class StockAnalysisService:
    def analyze(
        self,
        db: Session,
        *,
        symbol: str,
        initial_cash: float,
        model: str | None = None,
        skill_id: str | None = None,
        skill_ids: list[str] | None = None,
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
        selected_skills = self._selected_analysis_skills(
            skill_ids=skill_ids,
            legacy_skill_id=skill_id,
        )
        llm_decision = self._llm_decision(
            db=db,
            candidate=candidate,
            context=snapshot["context"],
            decision=decision,
            retail_analysis=retail_analysis,
            selected_model=model,
            selected_skills=selected_skills,
        )
        if llm_decision.get("used"):
            decision = self._merge_llm_decision(decision, llm_decision)
        charts = chart_data_service.stock_analysis_charts(
            db,
            symbol=normalized_symbol,
            action=decision["action"],
            price=float(candidate.get("price") or 0),
            quantity=int(decision.get("suggested_quantity") or 0),
            factor_scores=candidate.get("factor_scores") or {},
        )
        selected_model = str(llm_decision.get("model") or model or "")
        analysis_report = self._save_analysis_report(
            db=db,
            symbol=normalized_symbol,
            name=str(candidate.get("name") or normalized_symbol),
            model=selected_model,
            selected_skills=selected_skills,
            candidate=candidate,
            decision=decision,
            retail_analysis=retail_analysis,
            llm_decision=llm_decision,
            data_sources=snapshot["data_sources"],
        )

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
            "analysis_config": {
                "ai_config": ai_forecast_service.public_config(),
                "selected_model": selected_model,
                "selected_skill": self._public_skill(selected_skills[0]) if selected_skills else None,
                "selected_skills": [self._public_skill(skill) for skill in selected_skills],
            },
            "analysis_report": analysis_report,
            "data_sources": snapshot["data_sources"],
            "context": snapshot["context"],
            "stock_pick_snapshot": snapshot,
            "charts": charts,
        }

    def refresh_charts(
        self,
        db: Session,
        *,
        symbol: str,
        action: str,
        price: float,
        quantity: int,
        factor_scores: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_symbol = normalize_symbol(symbol)
        charts = chart_data_service.stock_analysis_charts(
            db,
            symbol=normalized_symbol,
            action=action if action in {"BUY", "HOLD", "SELL"} else "HOLD",
            price=price,
            quantity=quantity,
            factor_scores=factor_scores,
        )
        latest = charts["price_series"][-1] if charts["price_series"] else {}
        latest_price = latest.get("close")
        return {
            "symbol": normalized_symbol,
            "latest_price": float(latest_price) if isinstance(latest_price, (int, float)) else None,
            "refreshed_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "charts": charts,
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
        selected_model: str | None,
        selected_skills: list[dict[str, Any]],
    ) -> dict[str, Any]:
        app_settings = settings_service.get_or_create_settings(db)
        llm_config = self._resolve_llm_config(
            app_settings=app_settings,
            selected_model=selected_model,
        )
        if llm_config is None:
            return {"used": False}
        model = llm_config["model"]

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
                            "selected_skills": [
                                self._prompt_skill(skill)
                                for skill in selected_skills
                            ],
                            "output_schema": {
                                "action": "BUY, HOLD or SELL",
                                "rating": "一句短评级",
                                "target_allocation_ratio": "0到1之间的小数",
                                "reason": "一句话说明依据",
                                "report_summary": "多技能汇总后的报告摘要",
                                "skill_opinions": [
                                    {
                                        "skill_id": "对应 selected_skills.id",
                                        "stance": "bullish, neutral or bearish",
                                        "summary": "该技能视角下的核心结论",
                                    }
                                ],
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
                base_url=llm_config["base_url"],
                api_key=llm_config["api_key"],
                payload=payload,
                timeout_seconds=60,
            )
            raw = self._extract_json(response)
            action = str(raw.get("action") or "").upper()
            if action not in {"BUY", "HOLD", "SELL"}:
                action = decision["action"]
            return {
                "used": True,
                "provider": llm_config["provider"],
                "model": model,
                "skill": self._public_skill(selected_skills[0]) if selected_skills else None,
                "skills": [self._public_skill(skill) for skill in selected_skills],
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

    def _resolve_llm_config(
        self,
        *,
        app_settings: Any,
        selected_model: str | None,
    ) -> dict[str, str] | None:
        forecast_config = ai_forecast_service.internal_config()
        forecast_models = list(forecast_config.get("models") or [])
        requested_model = str(selected_model or "").strip()
        if requested_model:
            model = requested_model if requested_model in forecast_models else requested_model
            base_url = str(forecast_config.get("base_url") or "").strip()
            api_key = str(forecast_config.get("api_key") or "").strip()
            if base_url and api_key:
                return {
                    "provider": "forecast-ai",
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                }

        base_url = str(getattr(app_settings, "llm_base_url", None) or "").strip()
        api_key = str(getattr(app_settings, "llm_api_key", None) or "").strip()
        model = str(requested_model or getattr(app_settings, "llm_model", None) or "").strip()
        if base_url and api_key and model:
            return {
                "provider": str(getattr(app_settings, "provider_name", None) or "openai-compatible"),
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
            }

        forecast_model = requested_model or (forecast_models[0] if forecast_models else "")
        base_url = str(forecast_config.get("base_url") or "").strip()
        api_key = str(forecast_config.get("api_key") or "").strip()
        if base_url and api_key and forecast_model:
            return {
                "provider": "forecast-ai",
                "base_url": base_url,
                "api_key": api_key,
                "model": forecast_model,
            }
        return None

    def _selected_analysis_skills(
        self,
        *,
        skill_ids: list[str] | None,
        legacy_skill_id: str | None,
    ) -> list[dict[str, Any]]:
        requested: list[str] = []
        for value in [*(skill_ids or []), legacy_skill_id]:
            normalized = str(value or "").strip()
            if normalized and normalized not in requested:
                requested.append(normalized)
        if not requested:
            return []

        packages = {
            item.id: item
            for item in skill_registry.enabled_packages()
            if item.supports_run_type("analysis")
        }
        selected: list[dict[str, Any]] = []
        for skill_id in requested[:8]:
            package = packages.get(skill_id)
            if package is None:
                continue
            selected.append(
                {
                    "id": package.id,
                    "name": package.name,
                    "description": package.description,
                    "source": package.source,
                    "run_types": package.run_types,
                    "prompt": package.sop_text[:6000],
                }
            )
        return selected

    def _public_skill(self, skill: dict[str, Any] | None) -> dict[str, Any] | None:
        if not skill:
            return None
        return {
            "id": skill["id"],
            "name": skill["name"],
            "description": skill["description"],
            "source": skill["source"],
            "run_types": list(skill.get("run_types") or []),
        }

    def _prompt_skill(self, skill: dict[str, Any] | None) -> dict[str, Any] | None:
        if not skill:
            return None
        return {
            **(self._public_skill(skill) or {}),
            "instructions": skill.get("prompt") or "",
        }

    def _save_analysis_report(
        self,
        *,
        db: Session,
        symbol: str,
        name: str,
        model: str,
        selected_skills: list[dict[str, Any]],
        candidate: dict[str, Any],
        decision: dict[str, Any],
        retail_analysis: dict[str, Any],
        llm_decision: dict[str, Any],
        data_sources: list[str],
    ) -> dict[str, Any]:
        payload = self._build_analysis_report_payload(
            report_id=0,
            symbol=symbol,
            name=name,
            model=model,
            selected_skills=selected_skills,
            candidate=candidate,
            decision=decision,
            retail_analysis=retail_analysis,
            llm_decision=llm_decision,
            data_sources=data_sources,
            created_at=None,
        )
        record = StockAnalysisReport(
            symbol=symbol,
            name=name,
            title=payload["title"],
            model=model,
            action=decision["action"],
            rating=decision["rating"],
            selected_skills_payload=payload["selected_skills"],
            report_payload=payload,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        payload["id"] = record.id
        payload["created_at"] = record.created_at.isoformat() if record.created_at else None
        record.report_payload = payload
        db.add(record)
        db.commit()
        return payload

    def get_report(self, db: Session, *, report_id: int) -> dict[str, Any] | None:
        record = db.get(StockAnalysisReport, report_id)
        if record is None:
            return None
        payload = dict(record.report_payload or {})
        payload.setdefault("id", record.id)
        payload.setdefault("symbol", record.symbol)
        payload.setdefault("name", record.name)
        payload.setdefault("title", record.title)
        payload.setdefault("model", record.model)
        payload.setdefault("action", record.action)
        payload.setdefault("rating", record.rating)
        payload.setdefault("summary", "")
        payload.setdefault("selected_skills", list(record.selected_skills_payload or []))
        payload.setdefault("sections", [])
        payload.setdefault("source_snapshot", {})
        payload.setdefault("created_at", record.created_at.isoformat() if record.created_at else None)
        return payload

    def _build_analysis_report_payload(
        self,
        *,
        report_id: int,
        symbol: str,
        name: str,
        model: str,
        selected_skills: list[dict[str, Any]],
        candidate: dict[str, Any],
        decision: dict[str, Any],
        retail_analysis: dict[str, Any],
        llm_decision: dict[str, Any],
        data_sources: list[str],
        created_at: str | None,
    ) -> dict[str, Any]:
        raw = llm_decision.get("raw_decision") if llm_decision.get("used") else {}
        raw = raw if isinstance(raw, dict) else {}
        summary = str(raw.get("report_summary") or decision["reason"])
        selected_public_skills = [self._public_skill(skill) for skill in selected_skills]
        sections = [
            {
                "id": "decision",
                "title": "交易结论",
                "content": decision["reason"],
                "items": [
                    {"label": "动作", "value": decision["action"]},
                    {"label": "评级", "value": decision["rating"]},
                    {"label": "目标仓位", "value": f"{float(decision.get('target_allocation_ratio') or 0) * 100:.1f}%"},
                ],
            },
            {
                "id": "skill_synthesis",
                "title": "多 Skill 汇总",
                "content": summary,
                "items": self._skill_opinion_items(
                    selected_skills=selected_skills,
                    raw_opinions=raw.get("skill_opinions"),
                ),
            },
            {
                "id": "risk",
                "title": "风险与观察点",
                "content": self._risk_summary(retail_analysis),
                "items": [
                    {"label": "风险等级", "value": retail_analysis.get("risk_level") or "--"},
                    {"label": "行动信号", "value": retail_analysis.get("action_signal") or "--"},
                ],
            },
            {
                "id": "data",
                "title": "数据底稿",
                "content": f"评分 {float(candidate.get('score') or 0):.2f}，数据源 {len(data_sources)} 个。",
                "items": [{"label": "数据源", "value": source} for source in data_sources],
            },
        ]
        return {
            "id": report_id,
            "symbol": symbol,
            "name": name,
            "title": f"{name} {symbol} 多 Skill 分析报告",
            "model": model,
            "action": decision["action"],
            "rating": decision["rating"],
            "summary": summary,
            "selected_skills": selected_public_skills,
            "sections": sections,
            "source_snapshot": {
                "price": candidate.get("price"),
                "score": candidate.get("score"),
                "change_pct": candidate.get("change_pct"),
                "data_sources": data_sources,
            },
            "created_at": created_at,
        }

    def _skill_opinion_items(
        self,
        *,
        selected_skills: list[dict[str, Any]],
        raw_opinions: Any,
    ) -> list[dict[str, Any]]:
        opinions = raw_opinions if isinstance(raw_opinions, list) else []
        by_id = {
            str(item.get("skill_id") or ""): item
            for item in opinions
            if isinstance(item, dict)
        }
        items: list[dict[str, Any]] = []
        for skill in selected_skills:
            opinion = by_id.get(skill["id"]) or {}
            stance = str(opinion.get("stance") or "neutral")
            summary = str(opinion.get("summary") or skill.get("description") or "")
            items.append(
                {
                    "label": f"{skill['name']} · {stance}",
                    "value": summary,
                    "skill_id": skill["id"],
                }
            )
        return items

    def _risk_summary(self, retail_analysis: dict[str, Any]) -> str:
        warnings = retail_analysis.get("warnings")
        if isinstance(warnings, list) and warnings:
            return "；".join(str(item) for item in warnings[:3])
        return "暂未发现需要单独标记的高危风险，仍需结合盘中走势和成交额验证。"

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
