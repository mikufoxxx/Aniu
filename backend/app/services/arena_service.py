from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db.models import (
    AppSettings,
    ArenaAccount,
    ArenaAgentConfig,
    ArenaAgentMemory,
    ArenaOrder,
    ArenaPosition,
    ArenaRun,
    DailyBar,
    StockProfile,
)
from app.services.a_share_retail_analysis_service import a_share_retail_analysis_service
from app.services.llm_service import llm_service
from app.services.ai_stock_picker_service import ai_stock_picker_service
from app.services.historical_data_service import historical_data_service
from app.services.settings_service import settings_service


DEFAULT_AGENTS = [
    {"id": "momentum_ai", "name": "动量 AI", "style": "momentum"},
    {"id": "balanced_ai", "name": "均衡 AI", "style": "balanced"},
    {"id": "risk_ai", "name": "风控 AI", "style": "risk_control"},
]

STOP_LOSS_BY_STYLE = {
    "momentum": 0.07,
    "balanced": 0.06,
    "risk_control": 0.05,
}


class ArenaService:
    def _utcnow(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def run_once(
        self,
        db: Session,
        *,
        phase: str = "intraday_trade",
        agents: list[dict[str, str]] | None = None,
        initial_cash: float = 200000.0,
    ) -> dict[str, Any]:
        normalized_phase = self._normalize_phase(phase)
        active_agents = agents or self.enabled_agents(db)
        settings = get_settings()
        app_settings = settings_service.get_or_create_settings(db)
        agent_candidate_contexts = self._build_agent_candidate_contexts(
            db=db,
            agents=active_agents,
            limit=settings.market_data_maintenance_dataset_limit,
            lookback_days=settings.market_data_maintenance_lookback_days,
        )
        candidates = self._combined_candidates(agent_candidate_contexts)
        data_sources = self._combined_data_sources(agent_candidate_contexts)
        candidate_payload = {
            "universe_size": len(candidates),
            "candidate_count": len(candidates),
            "data_sources": data_sources,
            "candidates": candidates,
            "stock_pick_snapshot": None,
            "agent_stock_pick_snapshots": {
                agent_id: context["stock_pick_snapshot"]
                for agent_id, context in agent_candidate_contexts.items()
            },
            "agent_recommendations": [],
        }
        arena_run = ArenaRun(
            phase=normalized_phase,
            initial_cash=initial_cash,
            universe_json=None,
            candidate_payload=candidate_payload,
            leaderboard_payload=[],
        )
        db.add(arena_run)
        db.flush()
        snapshot_id = f"arena-{arena_run.id}"

        if normalized_phase == "morning_recommendation":
            recommendations = self._morning_recommendations(
                agents=active_agents,
                agent_candidate_contexts=agent_candidate_contexts,
                snapshot_id=snapshot_id,
            )
            candidate_payload = {
                **candidate_payload,
                "agent_recommendations": recommendations,
            }
            arena_run.candidate_payload = candidate_payload
            db.add(arena_run)
            db.commit()
            return {
                "run_id": arena_run.id,
                "phase": normalized_phase,
                "candidate_count": candidate_payload["candidate_count"],
                "candidates": candidates,
                "agent_recommendations": recommendations,
                "agent_reviews": [],
                "leaderboard": [],
                "orders": [],
                "data_sources": candidate_payload["data_sources"],
                "stock_pick_snapshot": None,
            }

        if normalized_phase == "closing_review":
            reviews = self._closing_reviews(
                db=db,
                agents=active_agents,
                agent_candidate_contexts=agent_candidate_contexts,
                snapshot_id=snapshot_id,
            )
            leaderboard = self.leaderboard(db)["items"]
            arena_run.candidate_payload = {
                **candidate_payload,
                "agent_reviews": reviews,
            }
            arena_run.leaderboard_payload = leaderboard
            db.add(arena_run)
            db.commit()
            return {
                "run_id": arena_run.id,
                "phase": normalized_phase,
                "candidate_count": candidate_payload["candidate_count"],
                "candidates": candidates,
                "agent_recommendations": [],
                "agent_reviews": reviews,
                "leaderboard": leaderboard,
                "orders": [],
                "data_sources": candidate_payload["data_sources"],
                "stock_pick_snapshot": None,
            }

        if normalized_phase == "nightly_learning":
            reviews = self._nightly_learning_reviews(
                db=db,
                agents=active_agents,
                agent_candidate_contexts=agent_candidate_contexts,
                snapshot_id=snapshot_id,
                initial_cash=initial_cash,
            )
            leaderboard = self.leaderboard(db)["items"]
            arena_run.candidate_payload = {
                **candidate_payload,
                "agent_reviews": reviews,
            }
            arena_run.leaderboard_payload = leaderboard
            db.add(arena_run)
            db.commit()
            return {
                "run_id": arena_run.id,
                "phase": normalized_phase,
                "candidate_count": candidate_payload["candidate_count"],
                "candidates": candidates,
                "agent_recommendations": [],
                "agent_reviews": reviews,
                "leaderboard": leaderboard,
                "orders": [],
                "data_sources": candidate_payload["data_sources"],
                "stock_pick_snapshot": None,
            }

        leaderboard: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        for index, agent in enumerate(active_agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            candidate_context = agent_candidate_contexts[agent_id]
            agent_candidates = candidate_context["candidates"]
            agent_data_sources = candidate_context["data_sources"]
            stock_pick_snapshot_id = candidate_context["stock_pick_snapshot_id"]
            decision_started_at = self._utcnow()
            account = self._get_or_create_account(
                db,
                agent=agent,
                initial_cash=initial_cash,
            )
            self._mark_positions(account, agent_candidates)
            sell_decision = self._stop_loss_decision(
                account,
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                data_sources=agent_data_sources,
            )
            decision = sell_decision or self._decide_for_agent(
                account=account,
                agent=agent,
                candidates=agent_candidates,
                agent_index=index,
                available_cash=account.cash,
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                data_sources=agent_data_sources,
                app_settings=app_settings,
                recent_memories=self.recent_agent_memories(
                    db,
                    agent_id=agent_id,
                    limit=5,
                ),
            )
            decision_generated_at = self._utcnow()
            if decision is not None:
                if decision["action"] == "SELL":
                    self._apply_sell(account, decision)
                else:
                    self._apply_buy(account, decision)
                decision_recorded_at = self._utcnow()
                timing = self._decision_timing(
                    decision_started_at=decision_started_at,
                    decision_generated_at=decision_generated_at,
                    decision_recorded_at=decision_recorded_at,
                )
                decision_context = dict(decision.get("decision_context") or {})
                decision_context["timing"] = timing
                order = ArenaOrder(
                    arena_run_id=arena_run.id,
                    agent_id=decision["agent_id"],
                    agent_name=decision["agent_name"],
                    style=decision["style"],
                    action=decision["action"],
                    symbol=decision["symbol"],
                    name=decision["name"],
                    quantity=decision["quantity"],
                    price=decision["price"],
                    amount=decision["amount"],
                    remaining_cash=decision["remaining_cash"],
                    reason=decision["reason"],
                    decision_payload=decision_context,
                    decision_started_at=decision_started_at,
                    decision_generated_at=decision_generated_at,
                    decision_recorded_at=decision_recorded_at,
                    decision_latency_ms=timing["decision_latency_ms"],
                    record_latency_ms=timing["record_latency_ms"],
                )
                db.add(order)
                db.flush()
                order_payload = self._order_payload(order)
                orders.append(order_payload)
            else:
                order_payload = None

            leaderboard.append(self._leaderboard_item(account))

        arena_run.leaderboard_payload = leaderboard
        db.add(arena_run)
        db.commit()
        return {
            "run_id": arena_run.id,
            "phase": normalized_phase,
            "candidate_count": candidate_payload["candidate_count"],
            "candidates": candidates,
            "agent_recommendations": [],
            "agent_reviews": [],
            "leaderboard": leaderboard,
            "orders": orders,
            "data_sources": candidate_payload["data_sources"],
            "stock_pick_snapshot": None,
        }

    def _normalize_phase(self, phase: str) -> str:
        normalized = str(phase or "intraday_trade").strip().lower()
        if normalized not in {
            "morning_recommendation",
            "intraday_trade",
            "closing_review",
            "nightly_learning",
        }:
            raise ValueError(
                "竞技场阶段必须是 morning_recommendation、intraday_trade、"
                "closing_review 或 nightly_learning。"
            )
        return normalized

    def _build_agent_candidate_contexts(
        self,
        *,
        db: Session,
        agents: list[dict[str, str]],
        limit: int,
        lookback_days: int,
    ) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            stock_pick_snapshot = ai_stock_picker_service.build_snapshot(
                db=db,
                symbols=None,
                limit=limit,
                prefer_realtime=True,
                lookback_days=lookback_days,
            )
            contexts[agent_id] = {
                "stock_pick_snapshot": stock_pick_snapshot,
                "stock_pick_snapshot_id": stock_pick_snapshot["snapshot_id"],
                "candidates": stock_pick_snapshot["recommendations"],
                "data_sources": list(stock_pick_snapshot.get("data_sources") or []),
            }
        return contexts

    def _combined_candidates(
        self,
        agent_candidate_contexts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for context in agent_candidate_contexts.values():
            for candidate in context["candidates"]:
                symbol = str(candidate.get("symbol") or "")
                if symbol and symbol in seen:
                    continue
                if symbol:
                    seen.add(symbol)
                candidates.append(candidate)
        return candidates

    def _combined_data_sources(
        self,
        agent_candidate_contexts: dict[str, dict[str, Any]],
    ) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for context in agent_candidate_contexts.values():
            for source in context["data_sources"]:
                if source in seen:
                    continue
                seen.add(source)
                sources.append(source)
        return sources

    def _morning_recommendations(
        self,
        *,
        agents: list[dict[str, str]],
        agent_candidate_contexts: dict[str, dict[str, Any]],
        snapshot_id: str,
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            candidate_context = agent_candidate_contexts[agent_id]
            candidates = candidate_context["candidates"]
            stock_pick_snapshot_id = candidate_context["stock_pick_snapshot_id"]
            data_sources = candidate_context["data_sources"]
            style = str(agent.get("style") or "balanced")
            picks = self._morning_picks(
                candidates=candidates,
                style=style,
                agent_index=index,
            )
            candidate = picks[0] if picks else None
            if candidate is None:
                continue
            playbook = self._playbook_for_style(style)
            recommendations.append(
                {
                    "agent_id": agent_id,
                    "agent_name": str(agent.get("name") or f"AI {index + 1}"),
                    "style": style,
                    "action": "WATCH",
                    "playbook": playbook,
                    "picks": picks,
                    "symbol": candidate.get("symbol"),
                    "name": candidate.get("name") or candidate.get("symbol"),
                    "score": candidate.get("score"),
                    "price": candidate.get("price"),
                    "reason": (
                        f"{playbook['label']} 早盘精选 {len(picks)} 只，"
                        f"首选 {candidate.get('symbol')}，评分 "
                        f"{float(candidate.get('score') or 0):.2f}。"
                    ),
                    "decision_context": self._decision_context(
                        snapshot_id=snapshot_id,
                        stock_pick_snapshot_id=stock_pick_snapshot_id,
                        agent=agent,
                        data_sources=data_sources,
                        candidate=candidate,
                        action="WATCH",
                        llm_decision=None,
                    ),
                }
            )
        return recommendations

    def agent_dashboard(
        self,
        db: Session,
        *,
        agent_id: str,
    ) -> dict[str, Any] | None:
        agent = self.get_agent(db, agent_id=agent_id)
        if agent is None:
            agent = self._default_agent(agent_id)
        if agent is None:
            return None

        account = db.scalar(
            select(ArenaAccount)
            .options(selectinload(ArenaAccount.positions))
            .where(ArenaAccount.agent_id == agent_id)
        )
        summary = self._account_summary(agent=agent, account=account)
        return {
            "agent": agent,
            "summary": summary,
            "morning": {
                "recommendations": self._recent_agent_recommendations(
                    db,
                    agent_id=agent_id,
                    limit=10,
                )
            },
            "intraday": {
                "orders": self._recent_orders(db, agent_id=agent_id, limit=20)
            },
            "closing": {
                "reviews": self.recent_agent_memories_by_type(
                    db,
                    agent_id=agent_id,
                    memory_type="closing_review",
                    limit=10,
                )
            },
            "learning": {
                "reviews": self.recent_agent_memories_by_type(
                    db,
                    agent_id=agent_id,
                    memory_type="nightly_learning",
                    limit=10,
                )
            },
        }

    def _closing_reviews(
        self,
        *,
        db: Session,
        agents: list[dict[str, str]],
        agent_candidate_contexts: dict[str, dict[str, Any]],
        snapshot_id: str,
    ) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            candidate_context = agent_candidate_contexts[agent_id]
            candidates = candidate_context["candidates"]
            stock_pick_snapshot_id = candidate_context["stock_pick_snapshot_id"]
            data_sources = candidate_context["data_sources"]
            account = db.scalar(
                select(ArenaAccount)
                .options(selectinload(ArenaAccount.positions))
                .where(ArenaAccount.agent_id == agent_id)
            )
            if account is None:
                continue
            self._mark_positions(account, candidates)
            metrics = self._leaderboard_item(account, include_positions=True)
            recent_orders = self._recent_orders(db, agent_id=agent_id, limit=10)
            metrics_payload = {
                "snapshot_id": snapshot_id,
                "stock_pick_snapshot_id": stock_pick_snapshot_id,
                "data_sources": data_sources,
                "leaderboard": metrics,
                "recent_orders": recent_orders,
            }
            summary = self._closing_summary(metrics=metrics, recent_orders=recent_orders)
            memory = ArenaAgentMemory(
                agent_id=agent_id,
                agent_name=str(agent.get("name") or account.agent_name),
                style=str(agent.get("style") or account.style),
                memory_type="closing_review",
                summary=summary,
                metrics_payload=metrics_payload,
            )
            db.add(memory)
            db.flush()
            reviews.append(self._memory_payload(memory))
        return reviews

    def _nightly_learning_reviews(
        self,
        *,
        db: Session,
        agents: list[dict[str, str]],
        agent_candidate_contexts: dict[str, dict[str, Any]],
        snapshot_id: str,
        initial_cash: float,
    ) -> list[dict[str, Any]]:
        reviews: list[dict[str, Any]] = []
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            candidate_context = agent_candidate_contexts[agent_id]
            candidates = candidate_context["candidates"]
            stock_pick_snapshot_id = candidate_context["stock_pick_snapshot_id"]
            data_sources = candidate_context["data_sources"]
            backtest_symbols = [
                str(item["symbol"]) for item in candidates if item.get("symbol")
            ]
            window = self._backtest_window(db, symbols=backtest_symbols)
            backtest: dict[str, Any]
            if window is None:
                backtest = {"available": False, "reason": "可用日线不足，至少需要两个交易日。"}
            else:
                try:
                    backtest = historical_data_service.run_daily_momentum_backtest(
                        db,
                        symbols=backtest_symbols,
                        start_date=window["start_date"],
                        end_date=window["end_date"],
                        initial_cash=initial_cash,
                    )
                    backtest["available"] = True
                except ValueError as exc:
                    backtest = {"available": False, "reason": str(exc)}
            recent_memories = self.recent_agent_memories(db, agent_id=agent_id, limit=5)
            metrics_payload = {
                "snapshot_id": snapshot_id,
                "stock_pick_snapshot_id": stock_pick_snapshot_id,
                "data_sources": data_sources,
                "backtest": backtest,
                "recent_memories": recent_memories,
            }
            summary = self._nightly_learning_summary(
                agent_name=str(agent.get("name") or agent_id),
                backtest=backtest,
            )
            memory = ArenaAgentMemory(
                agent_id=agent_id,
                agent_name=str(agent.get("name") or agent_id),
                style=str(agent.get("style") or "balanced"),
                memory_type="nightly_learning",
                summary=summary,
                metrics_payload=metrics_payload,
            )
            db.add(memory)
            db.flush()
            reviews.append(self._memory_payload(memory))
        return reviews

    def leaderboard(self, db: Session) -> dict[str, Any]:
        accounts = db.scalars(
            select(ArenaAccount)
            .options(selectinload(ArenaAccount.positions))
            .order_by(ArenaAccount.id)
        ).all()
        items = [self._leaderboard_item(account, include_positions=True) for account in accounts]
        items.sort(key=lambda item: item["total_assets"], reverse=True)
        return {"items": items}

    def recent_agent_memories(
        self,
        db: Session,
        *,
        agent_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        memories = db.scalars(
            select(ArenaAgentMemory)
            .where(ArenaAgentMemory.agent_id == agent_id)
            .order_by(ArenaAgentMemory.id.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return [self._memory_payload(memory) for memory in memories]

    def recent_agent_memories_by_type(
        self,
        db: Session,
        *,
        agent_id: str,
        memory_type: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        memories = db.scalars(
            select(ArenaAgentMemory)
            .where(
                ArenaAgentMemory.agent_id == agent_id,
                ArenaAgentMemory.memory_type == memory_type,
            )
            .order_by(ArenaAgentMemory.id.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return [self._memory_payload(memory) for memory in memories]

    def list_agents(self, db: Session) -> dict[str, Any]:
        stored = db.scalars(
            select(ArenaAgentConfig).order_by(ArenaAgentConfig.id)
        ).all()
        if stored:
            agents = [self._agent_config_payload(item) for item in stored]
        else:
            agents = [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "style": item["style"],
                    "provider": "openai-compatible",
                    "model": "",
                    "enabled": True,
                    "prompt": "",
                }
                for item in DEFAULT_AGENTS
            ]
        return {"agents": agents}

    def replace_agents(
        self,
        db: Session,
        *,
        agents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing_by_id = {
            item.agent_id: item
            for item in db.scalars(select(ArenaAgentConfig)).all()
        }
        seen: set[str] = set()
        saved: list[ArenaAgentConfig] = []
        for agent in agents:
            agent_id = str(agent.get("id") or "").strip()
            if not agent_id:
                raise ValueError("AI 选手 id 不能为空。")
            if agent_id in seen:
                raise ValueError(f"AI 选手 id 重复: {agent_id}")
            seen.add(agent_id)
            record = existing_by_id.get(agent_id) or ArenaAgentConfig(agent_id=agent_id)
            record.agent_name = str(agent.get("name") or agent_id)
            record.style = str(agent.get("style") or "balanced")
            record.provider = str(agent.get("provider") or "openai-compatible")
            record.model = str(agent.get("model") or "")
            record.prompt = str(agent.get("prompt") or "")
            record.enabled = bool(agent.get("enabled", True))
            db.add(record)
            saved.append(record)

        for agent_id, record in existing_by_id.items():
            if agent_id not in seen:
                db.delete(record)
        db.commit()
        for item in saved:
            db.refresh(item)
        return {"agents": [self._agent_config_payload(item) for item in saved]}

    def get_agent(self, db: Session, *, agent_id: str) -> dict[str, Any] | None:
        record = db.scalar(
            select(ArenaAgentConfig).where(ArenaAgentConfig.agent_id == agent_id)
        )
        if record is None:
            return None
        return self._agent_config_payload(record)

    def upsert_agent(
        self,
        db: Session,
        *,
        agent_id: str,
        agent: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_id = str(agent_id or "").strip()
        if not normalized_id:
            raise ValueError("AI 选手 id 不能为空。")
        record = db.scalar(
            select(ArenaAgentConfig).where(ArenaAgentConfig.agent_id == normalized_id)
        )
        if record is None:
            record = ArenaAgentConfig(agent_id=normalized_id)
        record.agent_name = str(agent.get("name") or normalized_id)
        record.style = str(agent.get("style") or "balanced")
        record.provider = str(agent.get("provider") or "openai-compatible")
        record.model = str(agent.get("model") or "")
        record.prompt = str(agent.get("prompt") or "")
        record.enabled = bool(agent.get("enabled", True))
        db.add(record)
        db.commit()
        db.refresh(record)
        return self._agent_config_payload(record)

    def delete_agent(self, db: Session, *, agent_id: str) -> dict[str, Any] | None:
        record = db.scalar(
            select(ArenaAgentConfig).where(ArenaAgentConfig.agent_id == agent_id)
        )
        if record is None:
            return None
        db.delete(record)
        db.commit()
        stored = db.scalars(
            select(ArenaAgentConfig).order_by(ArenaAgentConfig.id)
        ).all()
        return {"agents": [self._agent_config_payload(item) for item in stored]}

    def enabled_agents(self, db: Session) -> list[dict[str, Any]]:
        records = db.scalars(
            select(ArenaAgentConfig)
            .where(ArenaAgentConfig.enabled.is_(True))
            .order_by(ArenaAgentConfig.id)
        ).all()
        if not records:
            return DEFAULT_AGENTS
        return [
            {
                "id": item.agent_id,
                "name": item.agent_name,
                "style": item.style,
                "provider": item.provider,
                "model": item.model,
                "prompt": item.prompt,
            }
            for item in records
        ]

    def _agent_config_payload(self, item: ArenaAgentConfig) -> dict[str, Any]:
        return {
            "id": item.agent_id,
            "name": item.agent_name,
            "style": item.style,
            "provider": item.provider,
            "model": item.model,
            "enabled": item.enabled,
            "prompt": item.prompt,
        }

    def _default_agent(self, agent_id: str) -> dict[str, Any] | None:
        match = next((item for item in DEFAULT_AGENTS if item["id"] == agent_id), None)
        if match is None:
            return None
        return {
            "id": match["id"],
            "name": match["name"],
            "style": match["style"],
            "provider": "openai-compatible",
            "model": "",
            "enabled": True,
            "prompt": "",
        }

    def _account_summary(
        self,
        *,
        agent: dict[str, Any],
        account: ArenaAccount | None,
    ) -> dict[str, Any]:
        if account is None:
            return {
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "style": agent["style"],
                "cash": 0,
                "position_value": 0,
                "total_assets": 0,
                "return_ratio": 0,
                "order_count": 0,
                "realized_pnl": 0,
                "positions": [],
                "playbook": self._playbook_for_style(str(agent.get("style") or "balanced")),
            }
        payload = self._leaderboard_item(account, include_positions=True)
        payload["playbook"] = self._playbook_for_style(account.style)
        return payload

    def _decide_for_agent(
        self,
        *,
        account: ArenaAccount,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        agent_index: int,
        available_cash: float,
        snapshot_id: str,
        stock_pick_snapshot_id: str,
        data_sources: list[str],
        app_settings: AppSettings,
        recent_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        style = str(agent.get("style") or "balanced")
        llm_decision = self._llm_decision(
            agent=agent,
            candidates=candidates,
            positions=account.positions,
            snapshot_id=snapshot_id,
            data_sources=data_sources,
            app_settings=app_settings,
            recent_memories=recent_memories,
        )
        if llm_decision is not None:
            if llm_decision["action"] == "SELL":
                return self._sell_decision_from_position(
                    account=account,
                    agent=agent,
                    position=llm_decision["position"],
                    candidate=llm_decision["candidate"],
                    sell_ratio=llm_decision["sell_ratio"],
                    reason=llm_decision["reason"],
                    snapshot_id=snapshot_id,
                    stock_pick_snapshot_id=stock_pick_snapshot_id,
                    data_sources=data_sources,
                    llm_decision=llm_decision["context"],
                )
            return self._buy_decision_from_candidate(
                agent=agent,
                style=style,
                candidate=llm_decision["candidate"],
                available_cash=available_cash,
                allocation_ratio=llm_decision["allocation_ratio"],
                reason=llm_decision["reason"],
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                data_sources=data_sources,
                llm_decision=llm_decision["context"],
                agent_index=agent_index,
            )
        if style == "momentum":
            allocation_ratio = 0.45
        elif style == "risk_control":
            allocation_ratio = 0.2
        else:
            allocation_ratio = 0.3

        budget = available_cash * allocation_ratio
        preferred_index = 0 if style == "momentum" else min(agent_index, len(candidates) - 1)
        candidate = self._select_affordable_candidate(
            candidates=candidates,
            preferred_index=preferred_index,
            budget=budget,
        )
        if candidate is None:
            return None
        return self._buy_decision_from_candidate(
            agent=agent,
            style=style,
            candidate=candidate,
            available_cash=available_cash,
            allocation_ratio=allocation_ratio,
            reason=f"{style} 根据候选评分 {candidate['score']} 执行模拟买入。",
            snapshot_id=snapshot_id,
            stock_pick_snapshot_id=stock_pick_snapshot_id,
            data_sources=data_sources,
            llm_decision=None,
            agent_index=agent_index,
        )

    def _buy_decision_from_candidate(
        self,
        *,
        agent: dict[str, str],
        style: str,
        candidate: dict[str, Any],
        available_cash: float,
        allocation_ratio: float,
        reason: str,
        snapshot_id: str,
        stock_pick_snapshot_id: str,
        data_sources: list[str],
        llm_decision: dict[str, Any] | None,
        agent_index: int,
    ) -> dict[str, Any] | None:
        budget = available_cash * allocation_ratio
        price = float(candidate.get("price") or 0)
        if price <= 0:
            return None
        quantity = int(budget // (price * 100)) * 100
        if quantity <= 0:
            return None
        amount = round(quantity * price, 2)
        return {
            "agent_id": str(agent.get("id") or f"agent_{agent_index + 1}"),
            "agent_name": str(agent.get("name") or f"AI {agent_index + 1}"),
            "style": style,
            "action": "BUY",
            "symbol": candidate["symbol"],
            "name": candidate.get("name") or candidate["symbol"],
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "remaining_cash": round(available_cash - amount, 2),
            "reason": reason,
            "decision_context": self._decision_context(
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                agent=agent,
                data_sources=data_sources,
                candidate=candidate,
                action="BUY",
                llm_decision=llm_decision,
            ),
        }

    def _sell_decision_from_position(
        self,
        *,
        account: ArenaAccount,
        agent: dict[str, str],
        position: ArenaPosition,
        candidate: dict[str, Any],
        sell_ratio: float,
        reason: str,
        snapshot_id: str,
        stock_pick_snapshot_id: str,
        data_sources: list[str],
        llm_decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        quantity = int((position.quantity * sell_ratio) // 100) * 100
        if quantity <= 0 and position.quantity > 0:
            quantity = position.quantity
        quantity = min(quantity, position.quantity)
        price = float(candidate.get("price") or position.last_price or 0)
        if quantity <= 0 or price <= 0:
            return None
        amount = round(quantity * price, 2)
        return {
            "agent_id": account.agent_id,
            "agent_name": account.agent_name,
            "style": account.style,
            "action": "SELL",
            "symbol": position.symbol,
            "name": position.name or position.symbol,
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "remaining_cash": round(account.cash + amount, 2),
            "reason": reason,
            "decision_context": self._decision_context(
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                agent=agent,
                data_sources=data_sources,
                candidate=candidate,
                action="SELL",
                llm_decision=llm_decision,
            ),
        }

    def _llm_decision(
        self,
        *,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        positions: list[ArenaPosition],
        snapshot_id: str,
        data_sources: list[str],
        app_settings: AppSettings,
        recent_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        llm_config = self._resolve_llm_config(agent=agent, app_settings=app_settings)
        if llm_config is None:
            return None
        model = llm_config["model"]

        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是A股模拟交易竞技场里的独立AI选手。"
                        "只能基于用户给出的同一份候选快照做低频模拟交易决策。"
                        "只返回JSON，不要输出Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": self._llm_prompt(
                        agent=agent,
                        candidates=candidates,
                        positions=positions,
                        snapshot_id=snapshot_id,
                        data_sources=data_sources,
                        recent_memories=recent_memories,
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
            raw_decision = self._extract_llm_json(response)
            action = str(raw_decision.get("action") or "HOLD").upper()
            if action not in {"BUY", "SELL"}:
                return None
            symbol = str(raw_decision.get("symbol") or "").strip().upper()
            candidate = next(
                (
                    item
                    for item in candidates
                    if str(item.get("symbol") or "").upper() == symbol
                ),
                None,
            )
            if action == "BUY" and candidate is None:
                return None
            reason = str(raw_decision.get("reason") or "LLM 基于候选快照执行模拟买入。")
            context = {
                "used": True,
                "provider": llm_config["provider"],
                "model": model,
                "raw_decision": raw_decision,
            }
            if action == "SELL":
                position = next(
                    (
                        item for item in positions
                        if str(item.symbol or "").upper() == symbol and item.quantity > 0
                    ),
                    None,
                )
                if position is None:
                    return None
                fallback_candidate = {
                    "symbol": position.symbol,
                    "name": position.name,
                    "price": position.last_price,
                    "factor_scores": {},
                    "daily_factors": {},
                }
                return {
                    "action": "SELL",
                    "position": position,
                    "candidate": candidate or fallback_candidate,
                    "sell_ratio": self._normalize_sell_ratio(
                        raw_decision.get("sell_ratio")
                    ),
                    "reason": reason,
                    "context": context,
                }

            allocation_ratio = self._normalize_allocation_ratio(
                raw_decision.get("allocation_ratio")
            )
            return {
                "action": "BUY",
                "candidate": candidate,
                "allocation_ratio": allocation_ratio,
                "reason": reason,
                "context": context,
            }
        except Exception:
            return None

    def _resolve_llm_config(
        self,
        *,
        agent: dict[str, str],
        app_settings: AppSettings,
    ) -> dict[str, str] | None:
        provider = str(
            agent.get("provider")
            or getattr(app_settings, "provider_name", None)
            or "openai-compatible"
        ).strip()
        provider_configs = getattr(app_settings, "llm_provider_configs", None)
        provider_config: dict[str, Any] = {}
        if isinstance(provider_configs, dict):
            raw_config = provider_configs.get(provider) or provider_configs.get(provider.lower())
            if isinstance(raw_config, dict):
                provider_config = raw_config

        base_url = str(
            provider_config.get("base_url")
            or getattr(app_settings, "llm_base_url", None)
            or ""
        ).strip()
        api_key = str(
            provider_config.get("api_key")
            or getattr(app_settings, "llm_api_key", None)
            or ""
        ).strip()
        model = str(
            agent.get("model")
            or provider_config.get("default_model")
            or provider_config.get("model")
            or getattr(app_settings, "llm_model", None)
            or ""
        ).strip()
        if not base_url or not api_key or not model:
            return None
        return {
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
        }

    def _llm_prompt(
        self,
        *,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        positions: list[ArenaPosition],
        snapshot_id: str,
        data_sources: list[str],
        recent_memories: list[dict[str, Any]],
    ) -> str:
        prompt_payload = {
            "snapshot_id": snapshot_id,
            "agent": {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "style": agent.get("style"),
                "provider": agent.get("provider"),
                "model": agent.get("model"),
                "prompt": agent.get("prompt"),
            },
            "data_sources": data_sources,
            "recent_memories": recent_memories,
            "positions": [
                {
                    "symbol": position.symbol,
                    "name": position.name,
                    "quantity": position.quantity,
                    "avg_cost": position.avg_cost,
                    "last_price": position.last_price,
                    "unrealized_pnl": round(
                        position.quantity * (position.last_price - position.avg_cost),
                        2,
                    ),
                }
                for position in positions
                if position.quantity > 0
            ],
            "candidates": candidates,
            "output_schema": {
                "action": "BUY, SELL or HOLD",
                "symbol": "候选或持仓股票代码，HOLD时可为空",
                "allocation_ratio": "0到1之间的小数，例如0.1代表使用10%现金",
                "sell_ratio": "0到1之间的小数，例如0.5代表卖出一半持仓",
                "reason": "一句话说明决策依据",
            },
        }
        return json.dumps(prompt_payload, ensure_ascii=False, default=str)

    def _extract_llm_json(self, response: dict[str, Any]) -> dict[str, Any]:
        content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM 未返回决策内容")
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("LLM 决策不是 JSON 对象")
        return parsed

    def _normalize_allocation_ratio(self, value: Any) -> float:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            ratio = 0.3
        if ratio > 1:
            ratio = ratio / 100
        return min(max(ratio, 0.01), 0.95)

    def _normalize_sell_ratio(self, value: Any) -> float:
        try:
            ratio = float(value)
        except (TypeError, ValueError):
            ratio = 1.0
        if ratio > 1:
            ratio = ratio / 100
        return min(max(ratio, 0.01), 1.0)

    def _stop_loss_decision(
        self,
        account: ArenaAccount,
        *,
        snapshot_id: str,
        stock_pick_snapshot_id: str,
        data_sources: list[str],
    ) -> dict[str, Any] | None:
        stop_loss = STOP_LOSS_BY_STYLE.get(account.style, 0.06)
        for position in account.positions:
            if position.quantity <= 0 or position.avg_cost <= 0 or position.last_price <= 0:
                continue
            drawdown = (position.last_price - position.avg_cost) / position.avg_cost
            if drawdown > -stop_loss:
                continue
            amount = round(position.quantity * position.last_price, 2)
            return {
                "agent_id": account.agent_id,
                "agent_name": account.agent_name,
                "style": account.style,
                "action": "SELL",
                "symbol": position.symbol,
                "name": position.name or position.symbol,
                "quantity": position.quantity,
                "price": position.last_price,
                "amount": amount,
                "remaining_cash": round(account.cash + amount, 2),
                "reason": (
                    f"{account.style} 触发止损，当前价较成本回撤 {abs(drawdown) * 100:.2f}%。"
                ),
                "decision_context": {
                    "snapshot_id": snapshot_id,
                    "stock_pick_snapshot_id": stock_pick_snapshot_id,
                    "agent": {
                        "id": account.agent_id,
                        "name": account.agent_name,
                        "style": account.style,
                    },
                    "action": "SELL",
                    "risk": {"drawdown_pct": round(drawdown * 100, 4)},
                    "selected_candidate": {},
                    "data_sources": data_sources,
                },
            }
        return None

    def _decision_context(
        self,
        *,
        snapshot_id: str,
        stock_pick_snapshot_id: str | None = None,
        agent: dict[str, str],
        data_sources: list[str],
        candidate: dict[str, Any],
        action: str,
        llm_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot_id,
            "stock_pick_snapshot_id": stock_pick_snapshot_id,
            "agent": {
                "id": str(agent.get("id") or ""),
                "name": str(agent.get("name") or ""),
                "style": str(agent.get("style") or "balanced"),
                "provider": str(agent.get("provider") or "openai-compatible"),
                "model": str(agent.get("model") or ""),
                "prompt": str(agent.get("prompt") or ""),
            },
            "action": action,
            "data_sources": data_sources,
            "selected_candidate": {
                "symbol": candidate.get("symbol"),
                "name": candidate.get("name"),
                "score": candidate.get("score"),
                "price": candidate.get("price"),
                "change_pct": candidate.get("change_pct"),
                "amount": candidate.get("amount"),
                "turnover": candidate.get("turnover"),
                "volume_ratio": candidate.get("volume_ratio"),
                "factor_scores": candidate.get("factor_scores") or {},
                "daily_factors": candidate.get("daily_factors") or {},
                "ai_selection": candidate.get("ai_selection") or {},
                "rationale": candidate.get("rationale"),
            },
            "retail_analysis": candidate.get("retail_analysis") or a_share_retail_analysis_service.build(candidate),
            "llm_decision": llm_decision or {"used": False},
        }

    def _decision_timing(
        self,
        *,
        decision_started_at: datetime,
        decision_generated_at: datetime,
        decision_recorded_at: datetime,
    ) -> dict[str, Any]:
        decision_latency_ms = int(
            (decision_generated_at - decision_started_at).total_seconds() * 1000
        )
        record_latency_ms = int(
            (decision_recorded_at - decision_generated_at).total_seconds() * 1000
        )
        return {
            "decision_started_at": decision_started_at.isoformat(),
            "decision_generated_at": decision_generated_at.isoformat(),
            "decision_recorded_at": decision_recorded_at.isoformat(),
            "decision_latency_ms": max(decision_latency_ms, 0),
            "record_latency_ms": max(record_latency_ms, 0),
        }

    def _get_or_create_account(
        self,
        db: Session,
        *,
        agent: dict[str, str],
        initial_cash: float,
    ) -> ArenaAccount:
        agent_id = str(agent.get("id") or "agent")
        account = db.scalar(
            select(ArenaAccount)
            .options(selectinload(ArenaAccount.positions))
            .where(ArenaAccount.agent_id == agent_id)
        )
        if account is not None:
            account.agent_name = str(agent.get("name") or account.agent_name)
            account.style = str(agent.get("style") or account.style)
            return account
        account = ArenaAccount(
            agent_id=agent_id,
            agent_name=str(agent.get("name") or agent_id),
            style=str(agent.get("style") or "balanced"),
            initial_cash=initial_cash,
            cash=initial_cash,
        )
        db.add(account)
        db.flush()
        return account

    def _mark_positions(
        self,
        account: ArenaAccount,
        candidates: list[dict[str, Any]],
    ) -> None:
        prices = {candidate["symbol"]: float(candidate.get("price") or 0) for candidate in candidates}
        for position in account.positions:
            if position.symbol in prices and prices[position.symbol] > 0:
                position.last_price = prices[position.symbol]

    def _apply_buy(self, account: ArenaAccount, decision: dict[str, Any]) -> None:
        symbol = str(decision["symbol"])
        price = float(decision["price"])
        quantity = int(decision["quantity"])
        amount = float(decision["amount"])
        current = next(
            (position for position in account.positions if position.symbol == symbol),
            None,
        )
        if current is None:
            current = ArenaPosition(
                account_id=account.id,
                symbol=symbol,
                name=str(decision.get("name") or symbol),
                quantity=0,
                avg_cost=0.0,
                last_price=price,
            )
            account.positions.append(current)

        previous_cost = current.avg_cost * current.quantity
        new_quantity = current.quantity + quantity
        current.quantity = new_quantity
        current.avg_cost = round((previous_cost + amount) / new_quantity, 6)
        current.last_price = price
        current.name = str(decision.get("name") or current.name)
        account.cash = round(account.cash - amount, 2)
        account.order_count += 1

    def _apply_sell(self, account: ArenaAccount, decision: dict[str, Any]) -> None:
        symbol = str(decision["symbol"])
        quantity = int(decision["quantity"])
        price = float(decision["price"])
        amount = float(decision["amount"])
        position = next(
            (item for item in account.positions if item.symbol == symbol),
            None,
        )
        if position is None or position.quantity <= 0:
            return
        sell_quantity = min(quantity, position.quantity)
        realized = (price - position.avg_cost) * sell_quantity
        position.quantity -= sell_quantity
        position.last_price = price
        if position.quantity <= 0:
            position.quantity = 0
        account.cash = round(account.cash + amount, 2)
        account.realized_pnl = round(account.realized_pnl + realized, 2)
        account.order_count += 1

    def _leaderboard_item(
        self,
        account: ArenaAccount,
        *,
        include_positions: bool = False,
    ) -> dict[str, Any]:
        position_payloads = [
            {
                "symbol": position.symbol,
                "name": position.name,
                "quantity": position.quantity,
                "avg_cost": round(position.avg_cost, 4),
                "last_price": round(position.last_price, 4),
                "market_value": round(position.quantity * position.last_price, 2),
                "unrealized_pnl": round(
                    position.quantity * (position.last_price - position.avg_cost),
                    2,
                ),
            }
            for position in account.positions
            if position.quantity > 0
        ]
        position_value = sum(item["market_value"] for item in position_payloads)
        total_assets = account.cash + position_value
        payload = {
            "agent_id": account.agent_id,
            "agent_name": account.agent_name,
            "style": account.style,
            "cash": round(account.cash, 2),
            "position_value": round(position_value, 2),
            "total_assets": round(total_assets, 2),
            "return_ratio": round(
                (total_assets - account.initial_cash) / account.initial_cash,
                6,
            )
            if account.initial_cash
            else 0.0,
            "order_count": account.order_count,
            "realized_pnl": round(account.realized_pnl, 2),
        }
        if include_positions:
            payload["positions"] = position_payloads
        return payload

    def _morning_picks(
        self,
        *,
        candidates: list[dict[str, Any]],
        style: str,
        agent_index: int,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if style == "risk_control":
            ordered = sorted(
                candidates,
                key=lambda item: (
                    float(item.get("change_pct") or 0),
                    float(item.get("score") or 0),
                ),
                reverse=True,
            )
        elif style == "balanced":
            ordered = candidates[agent_index:] + candidates[:agent_index]
        else:
            ordered = candidates
        pick_limit = min(5, max(1, len(ordered) - 1))
        return [self._pick_payload(candidate) for candidate in ordered[:pick_limit]]

    def _pick_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        ai_selection = candidate.get("ai_selection") or {}
        return {
            "symbol": candidate.get("symbol"),
            "name": candidate.get("name") or candidate.get("symbol"),
            "score": candidate.get("score"),
            "ai_selection_score": ai_selection.get("score"),
            "risk_flags": list(ai_selection.get("risk_flags") or []),
            "price": candidate.get("price"),
            "change_pct": candidate.get("change_pct"),
            "reason": candidate.get("rationale") or "",
        }

    def _playbook_for_style(self, style: str) -> dict[str, str]:
        if style == "momentum":
            return {
                "mode": "short_swing",
                "label": "短线动量",
                "holding_period": "1-5个交易日",
            }
        if style == "risk_control":
            return {
                "mode": "long_defensive",
                "label": "长线稳健",
                "holding_period": "2周以上",
            }
        return {
            "mode": "quant_rotation",
            "label": "量化轮动",
            "holding_period": "3-10个交易日",
        }

    def _recent_agent_recommendations(
        self,
        db: Session,
        *,
        agent_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        runs = db.scalars(
            select(ArenaRun)
            .where(ArenaRun.phase == "morning_recommendation")
            .order_by(ArenaRun.id.desc())
            .limit(max(1, min(limit * 5, 100)))
        ).all()
        recommendations: list[dict[str, Any]] = []
        for run in runs:
            payload = run.candidate_payload or {}
            for item in payload.get("agent_recommendations") or []:
                if item.get("agent_id") != agent_id:
                    continue
                enriched = dict(item)
                self._enrich_recommendation_names(db, enriched)
                enriched["run_id"] = run.id
                enriched["created_at"] = run.created_at.isoformat() if run.created_at else None
                recommendations.append(enriched)
                if len(recommendations) >= limit:
                    return recommendations
        return recommendations

    def _enrich_recommendation_names(
        self,
        db: Session,
        recommendation: dict[str, Any],
    ) -> None:
        picks = [dict(pick) for pick in recommendation.get("picks") or [] if isinstance(pick, dict)]
        recommendation["picks"] = picks
        symbols = {
            str(symbol).strip().upper()
            for symbol in [recommendation.get("symbol"), *(pick.get("symbol") for pick in picks)]
            if symbol
        }
        if not symbols:
            return
        profiles = db.scalars(select(StockProfile).where(StockProfile.symbol.in_(symbols))).all()
        names = {profile.symbol.upper(): profile.name for profile in profiles if profile.name}
        for pick in picks:
            symbol = str(pick.get("symbol") or "").strip().upper()
            name = str(pick.get("name") or "").strip()
            if symbol and (not name or name == symbol):
                pick["name"] = names.get(symbol) or symbol
        symbol = str(recommendation.get("symbol") or "").strip().upper()
        name = str(recommendation.get("name") or "").strip()
        if symbol and (not name or name == symbol):
            recommendation["name"] = names.get(symbol) or symbol

    def _recent_orders(
        self,
        db: Session,
        *,
        agent_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        orders = db.scalars(
            select(ArenaOrder)
            .where(ArenaOrder.agent_id == agent_id)
            .order_by(ArenaOrder.id.desc())
            .limit(limit)
        ).all()
        return [
            {
                "id": order.id,
                "action": order.action,
                "symbol": order.symbol,
                "name": order.name,
                "quantity": order.quantity,
                "price": order.price,
                "amount": order.amount,
                "reason": order.reason,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
            for order in orders
        ]

    def _closing_summary(
        self,
        *,
        metrics: dict[str, Any],
        recent_orders: list[dict[str, Any]],
    ) -> str:
        return_pct = float(metrics.get("return_ratio") or 0) * 100
        position_count = len(metrics.get("positions") or [])
        trade_count = len(recent_orders)
        return (
            f"{metrics['agent_name']} 收盘复盘：总资产 {metrics['total_assets']:.2f}，"
            f"收益 {return_pct:.2f}%，现金 {metrics['cash']:.2f}，"
            f"持仓 {position_count} 只，最近成交 {trade_count} 笔。"
        )

    def _backtest_window(
        self,
        db: Session,
        *,
        symbols: list[str],
    ) -> dict[str, str] | None:
        trade_dates = db.scalars(
            select(DailyBar.trade_date)
            .where(DailyBar.symbol.in_(symbols))
            .order_by(DailyBar.trade_date)
        ).all()
        unique_dates = sorted({date for date in trade_dates if date})
        if len(unique_dates) < 2:
            return None
        return {"start_date": unique_dates[0], "end_date": unique_dates[-1]}

    def _nightly_learning_summary(
        self,
        *,
        agent_name: str,
        backtest: dict[str, Any],
    ) -> str:
        if not backtest.get("available"):
            return f"{agent_name} 夜间学习：回测未执行，{backtest.get('reason') or '数据不足'}。"
        return_pct = float(backtest.get("return_ratio") or 0) * 100
        drawdown_pct = float(backtest.get("max_drawdown") or 0) * 100
        return (
            f"{agent_name} 夜间学习：回测 {backtest.get('start_date')} 至 "
            f"{backtest.get('end_date')}，最佳标的 {backtest.get('selected_symbol')}，"
            f"收益 {return_pct:.2f}%，最大回撤 {drawdown_pct:.2f}%。"
        )

    def _memory_payload(self, memory: ArenaAgentMemory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "agent_id": memory.agent_id,
            "agent_name": memory.agent_name,
            "style": memory.style,
            "memory_type": memory.memory_type,
            "summary": memory.summary,
            "metrics": memory.metrics_payload or {},
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
        }

    def _select_affordable_candidate(
        self,
        *,
        candidates: list[dict[str, Any]],
        preferred_index: int,
        budget: float,
    ) -> dict[str, Any] | None:
        ordered = candidates[preferred_index:] + candidates[:preferred_index]
        for candidate in ordered:
            price = float(candidate.get("price") or 0)
            if price > 0 and price * 100 <= budget:
                return candidate
        return None

    def _order_payload(self, order: ArenaOrder) -> dict[str, Any]:
        return {
            "id": order.id,
            "agent_id": order.agent_id,
            "agent_name": order.agent_name,
            "style": order.style,
            "action": order.action,
            "symbol": order.symbol,
            "name": order.name,
            "quantity": order.quantity,
            "price": order.price,
            "amount": order.amount,
            "remaining_cash": order.remaining_cash,
            "reason": order.reason,
            "decision_context": order.decision_payload or {},
            "decision_started_at": order.decision_started_at.isoformat()
            if order.decision_started_at
            else None,
            "decision_generated_at": order.decision_generated_at.isoformat()
            if order.decision_generated_at
            else None,
            "decision_recorded_at": order.decision_recorded_at.isoformat()
            if order.decision_recorded_at
            else None,
            "decision_latency_ms": int(order.decision_latency_ms or 0),
            "record_latency_ms": int(order.record_latency_ms or 0),
        }


arena_service = ArenaService()
