from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
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
from app.db.database import session_scope
from app.services.a_share_retail_analysis_service import a_share_retail_analysis_service
from app.services.ai_data_request_service import ai_data_request_service
from app.services.ai_forecast_service import ai_forecast_service
from app.services.chart_data_service import chart_data_service
from app.services.llm_service import llm_service
from app.services.ai_stock_picker_service import ai_stock_picker_service
from app.services.historical_data_service import historical_data_service
from app.services.market_data_service import market_data_service, normalize_symbol
from app.services.settings_service import settings_service


logger = logging.getLogger(__name__)

ARENA_DEFAULT_INITIAL_CASH = 200000.0
ARENA_LIVE_CACHE_TTL_SECONDS = 60
ARENA_LIVE_REFRESH_CACHE_TTL_SECONDS = 10
ARENA_EQUITY_RUN_LIMIT = 500
ARENA_ORDER_FORECAST_CACHE_LIMIT = 100
ARENA_STORED_CANDIDATE_LIMIT = 20
ARENA_STORED_POOL_SYMBOL_LIMIT = 30
ARENA_CANDIDATE_PAYLOAD_MAX_READ_BYTES = 2_000_000
ARENA_DASHBOARD_INTRADAY_ORDER_LIMIT = 5
ARENA_SCHEDULE_TIMEZONE = ZoneInfo("Asia/Shanghai")
ARENA_DASHBOARD_SECTIONS = {"morning", "intraday", "closing", "learning"}
ARENA_SCHEDULE_WINDOWS = (
    {
        "phase": "morning_recommendation",
        "label": "早盘推荐",
        "start": (8, 0),
        "window_minutes": 30,
    },
    {
        "phase": "intraday_trade",
        "label": "盘中模拟",
        "start": (9, 35),
        "window_minutes": 25,
    },
    {
        "phase": "intraday_trade",
        "label": "盘中模拟",
        "start": (10, 40),
        "window_minutes": 20,
    },
    {
        "phase": "intraday_trade",
        "label": "盘中模拟",
        "start": (13, 30),
        "window_minutes": 20,
    },
    {
        "phase": "intraday_trade",
        "label": "盘中模拟",
        "start": (14, 25),
        "window_minutes": 20,
    },
    {
        "phase": "closing_review",
        "label": "收盘复盘",
        "start": (15, 10),
        "window_minutes": 30,
    },
    {
        "phase": "nightly_learning",
        "label": "夜间学习",
        "start": (20, 0),
        "window_minutes": 30,
    },
)
ARENA_PHASE_DATA_POLICIES = {
    "morning_recommendation": {
        "label": "早盘推荐",
        "data_focus": "截至上一交易日的历史数据、盘前资讯和公告",
        "news_query": "今日A股盘前要闻 重要公告 政策行业消息",
        "ai_data_dimensions": [
            "daily_history",
            "news",
            "announcement",
            "quote",
            "moneyflow",
            "sector_heat",
        ],
        "required_inputs": [
            "history_before_today",
            "news",
            "announcement",
        ],
        "prefer_realtime": False,
        "history_cutoff": "before_today",
    },
    "intraday_trade": {
        "label": "盘中模拟",
        "data_focus": "实时行情、盘中新闻和已有历史结构",
        "news_query": "今日A股盘中实时新闻 板块异动 个股公告",
        "ai_data_dimensions": [
            "quote",
            "news",
            "announcement",
            "daily_history",
        ],
        "required_inputs": [
            "realtime_quote",
            "news",
            "daily_history",
            "positions",
        ],
        "prefer_realtime": True,
        "history_cutoff": None,
    },
    "closing_review": {
        "label": "收盘复盘",
        "data_focus": "今日操作、收益、走势和历史日线",
        "news_query": "今日A股收盘新闻 盘后公告 板块复盘",
        "ai_data_dimensions": [
            "quote",
            "daily_history",
            "moneyflow",
            "sector_heat",
            "news",
        ],
        "required_inputs": [
            "today_operations",
            "return",
            "intraday_trend",
            "daily_history",
        ],
        "prefer_realtime": True,
        "history_cutoff": None,
    },
    "nightly_learning": {
        "label": "夜间学习",
        "data_focus": "历史数据和回测结果",
        "news_query": "A股历史策略复盘 市场结构变化",
        "ai_data_dimensions": [
            "daily_history",
            "moneyflow",
            "sector_heat",
            "financial_indicator",
            "valuation",
        ],
        "required_inputs": [
            "daily_history",
            "backtest",
            "historical_performance",
        ],
        "prefer_realtime": False,
        "history_cutoff": None,
    },
}
DEFAULT_AGENT_STYLES = ["auto", "auto", "auto", "auto", "auto"]

STOP_LOSS_BY_STYLE = {
    "auto": 0.06,
    "momentum": 0.07,
    "balanced": 0.06,
    "risk_control": 0.05,
}


class ArenaService:
    _order_forecast_cache: dict[str, dict[str, Any]] = {}

    def __init__(self) -> None:
        self._live_payload_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._live_payload_cache_lock = RLock()
        self._live_payload_inflight_locks: dict[str, RLock] = {}

    def _utcnow(self) -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def _now_shanghai(self) -> datetime:
        return datetime.now(ARENA_SCHEDULE_TIMEZONE)

    def run_once(
        self,
        db: Session,
        *,
        phase: str = "intraday_trade",
        agents: list[dict[str, Any]] | None = None,
        initial_cash: float | None = None,
        schedule_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_phase = self._normalize_phase(phase)
        active_agents = [
            self._normalize_agent_payload(agent, index)
            for index, agent in enumerate(agents or self.enabled_agents(db))
        ]
        settings = get_settings()
        app_settings = settings_service.get_or_create_settings(db)
        effective_initial_cash = self._arena_initial_cash(db, initial_cash)
        phase_context = self._phase_data_context(db=db, phase=normalized_phase)
        agent_candidate_contexts = self._build_agent_candidate_contexts(
            db=db,
            agents=active_agents,
            phase=normalized_phase,
            phase_context=phase_context,
            limit=settings.market_data_maintenance_dataset_limit,
            lookback_days=settings.market_data_maintenance_lookback_days,
        )
        candidates = self._combined_candidates(agent_candidate_contexts)
        data_sources = self._combined_data_sources(agent_candidate_contexts)
        candidate_pool_scope = self._candidate_pool_scope(agent_candidate_contexts)
        agent_candidate_pools = self._agent_candidate_pools(agent_candidate_contexts)
        candidate_payload = self._stored_run_candidate_payload(
            candidates=candidates,
            data_sources=data_sources,
            candidate_pool_scope=candidate_pool_scope,
            agent_candidate_pools=agent_candidate_pools,
            agent_candidate_contexts=agent_candidate_contexts,
            schedule_context=schedule_context or {},
            phase_context=phase_context,
            agent_recommendations=[],
        )
        arena_run = ArenaRun(
            phase=normalized_phase,
            initial_cash=effective_initial_cash,
            universe_json=None,
            candidate_payload=candidate_payload,
            leaderboard_payload=[],
        )
        db.add(arena_run)
        db.flush()
        snapshot_id = f"arena-{arena_run.id}"

        if normalized_phase == "morning_recommendation":
            recommendations = self._morning_recommendations(
                db=db,
                agents=active_agents,
                agent_candidate_contexts=agent_candidate_contexts,
                snapshot_id=snapshot_id,
                app_settings=app_settings,
            )
            candidate_payload = {
                **candidate_payload,
                "agent_recommendations": recommendations,
            }
            arena_run.candidate_payload = candidate_payload
            db.add(arena_run)
            db.commit()
            self._invalidate_live_payload_cache()
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
                "candidate_pool_scope": candidate_pool_scope,
                "agent_candidate_pools": agent_candidate_pools,
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
            self._invalidate_live_payload_cache()
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
                "candidate_pool_scope": candidate_pool_scope,
                "agent_candidate_pools": agent_candidate_pools,
                "stock_pick_snapshot": None,
            }

        if normalized_phase == "nightly_learning":
            reviews = self._nightly_learning_reviews(
                db=db,
                agents=active_agents,
                agent_candidate_contexts=agent_candidate_contexts,
                snapshot_id=snapshot_id,
                initial_cash=effective_initial_cash,
            )
            leaderboard = self.leaderboard(db)["items"]
            arena_run.candidate_payload = {
                **candidate_payload,
                "agent_reviews": reviews,
            }
            arena_run.leaderboard_payload = leaderboard
            db.add(arena_run)
            db.commit()
            self._invalidate_live_payload_cache()
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
                "candidate_pool_scope": candidate_pool_scope,
                "agent_candidate_pools": agent_candidate_pools,
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
                initial_cash=effective_initial_cash,
            )
            self._mark_positions(account, agent_candidates)
            sell_decision = self._stop_loss_decision(
                account,
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                data_sources=agent_data_sources,
            )
            decision = sell_decision or self._decide_for_agent(
                db=db,
                account=account,
                agent=agent,
                candidates=agent_candidates,
                agent_index=index,
                available_cash=account.cash,
                snapshot_id=snapshot_id,
                stock_pick_snapshot_id=stock_pick_snapshot_id,
                data_sources=agent_data_sources,
                ai_data_request=candidate_context.get("ai_data_request"),
                phase_context=candidate_context.get("phase_context") or phase_context,
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
                order_payload = self._order_payload(db, order)
                orders.append(order_payload)
            else:
                order_payload = None

            leaderboard.append(self._leaderboard_item(account))

        arena_run.leaderboard_payload = leaderboard
        db.add(arena_run)
        db.commit()
        self._invalidate_live_payload_cache()
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
            "candidate_pool_scope": candidate_pool_scope,
            "agent_candidate_pools": agent_candidate_pools,
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

    def _phase_data_context(self, *, db: Session, phase: str) -> dict[str, Any]:
        normalized_phase = self._normalize_phase(phase)
        policy = dict(ARENA_PHASE_DATA_POLICIES[normalized_phase])
        history_end_date = None
        if policy.get("history_cutoff") == "before_today":
            today = self._now_shanghai().strftime("%Y%m%d")
            history_end_date = db.scalar(
                select(DailyBar.trade_date)
                .where(DailyBar.trade_date < today)
                .order_by(DailyBar.trade_date.desc())
                .limit(1)
            )
        elif not policy.get("prefer_realtime"):
            history_end_date = db.scalar(
                select(DailyBar.trade_date)
                .order_by(DailyBar.trade_date.desc())
                .limit(1)
            )
        return {
            "phase": normalized_phase,
            "label": policy["label"],
            "data_focus": policy["data_focus"],
            "news_query": policy["news_query"],
            "ai_data_dimensions": list(policy["ai_data_dimensions"]),
            "required_inputs": list(policy["required_inputs"]),
            "prefer_realtime": bool(policy["prefer_realtime"]),
            "history_cutoff": policy.get("history_cutoff"),
            "history_end_date": history_end_date,
            "timezone": str(ARENA_SCHEDULE_TIMEZONE),
        }

    def _normalize_agent_payload(
        self,
        agent: dict[str, Any],
        index: int = 0,
    ) -> dict[str, Any]:
        agent_id = str(agent.get("id") or f"agent_{index + 1}").strip()
        return {
            "id": agent_id or f"agent_{index + 1}",
            "name": str(agent.get("name") or agent_id or f"AI {index + 1}"),
            "style": str(agent.get("style") or "auto"),
            "provider": str(agent.get("provider") or "openai-compatible"),
            "model": str(agent.get("model") or ""),
            "enabled": bool(agent.get("enabled", True)),
            "prompt": str(agent.get("prompt") or ""),
        }

    def _arena_initial_cash(
        self,
        db: Session,
        explicit_initial_cash: float | None,
    ) -> float:
        if explicit_initial_cash is not None:
            return float(explicit_initial_cash)
        app_settings = settings_service.get_or_create_settings(db)
        value = getattr(app_settings, "arena_initial_cash", None)
        try:
            amount = float(value)
        except (TypeError, ValueError):
            amount = ARENA_DEFAULT_INITIAL_CASH
        return amount if amount >= 10000 else ARENA_DEFAULT_INITIAL_CASH

    def process_due_schedules(self) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.arena_automation_enabled:
            return []
        now = self._now_shanghai()
        if not self._is_trading_day(now):
            return []

        processed: list[dict[str, Any]] = []
        with session_scope() as db:
            agents = self.enabled_agents(db)
            due_entries = self._due_schedule_entries(now=now, agents=agents)
            for entry in due_entries:
                slot_key = str(entry["slot_key"])
                if self._schedule_slot_completed(db, slot_key=slot_key):
                    continue
                try:
                    result = self.run_once(
                        db,
                        phase=str(entry["phase"]),
                        agents=[entry["agent"]],
                        initial_cash=None,
                        schedule_context={
                            "slot_key": slot_key,
                            "label": entry["label"],
                            "phase": entry["phase"],
                            "agent_id": entry["agent_id"],
                            "scheduled_at": entry["scheduled_at"].isoformat(),
                            "window_start": entry["window_start"].isoformat(),
                            "triggered_at": now.isoformat(),
                        },
                    )
                    processed.append(
                        {
                            "slot_key": slot_key,
                            "phase": entry["phase"],
                            "agent_id": entry["agent_id"],
                            "run_id": result["run_id"],
                        }
                    )
                except Exception as exc:
                    logger.exception(
                        "arena scheduled run failed: slot_key=%s error=%s",
                        slot_key,
                        exc,
                    )
        return processed

    def _is_trading_day(self, now: datetime) -> bool:
        return now.weekday() < 5

    def _due_schedule_entries(
        self,
        *,
        now: datetime,
        agents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        localized_now = now
        if localized_now.tzinfo is None:
            localized_now = localized_now.replace(tzinfo=ARENA_SCHEDULE_TIMEZONE)
        entries = self._schedule_entries_for_day(now=localized_now, agents=agents)
        grace_minutes = max(1, min(get_settings().arena_schedule_grace_minutes, 30))
        grace = timedelta(minutes=grace_minutes)
        return [
            entry
            for entry in entries
            if entry["scheduled_at"] <= localized_now < entry["scheduled_at"] + grace
        ]

    def _schedule_entries_for_day(
        self,
        *,
        now: datetime,
        agents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not agents:
            return []
        localized_now = now
        if localized_now.tzinfo is None:
            localized_now = localized_now.replace(tzinfo=ARENA_SCHEDULE_TIMEZONE)
        entries: list[dict[str, Any]] = []
        normalized_agents = [
            self._normalize_agent_payload(agent, index)
            for index, agent in enumerate(agents)
        ]
        for window in ARENA_SCHEDULE_WINDOWS:
            hour, minute = window["start"]
            window_start = localized_now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            window_minutes = int(window["window_minutes"])
            stagger_minutes = max(
                1,
                min(6, window_minutes // max(len(normalized_agents), 1)),
            )
            for index, agent in enumerate(normalized_agents):
                scheduled_at = window_start + timedelta(minutes=index * stagger_minutes)
                agent_id = str(agent["id"])
                slot_key = (
                    f"{scheduled_at.date().isoformat()}:"
                    f"{window['phase']}:{window_start.strftime('%H%M')}:{agent_id}"
                )
                entries.append(
                    {
                        "slot_key": slot_key,
                        "phase": window["phase"],
                        "label": window["label"],
                        "agent_id": agent_id,
                        "agent": agent,
                        "window_start": window_start,
                        "scheduled_at": scheduled_at,
                        "stagger_minutes": stagger_minutes,
                    }
                )
        return entries

    def _schedule_slot_completed(self, db: Session, *, slot_key: str) -> bool:
        since = self._utcnow() - timedelta(days=2)
        matched_id = db.scalar(
            select(ArenaRun.id)
            .where(
                ArenaRun.created_at >= since,
                func.json_extract(ArenaRun.candidate_payload, "$.schedule_context.slot_key")
                == slot_key,
            )
            .order_by(ArenaRun.id.desc())
            .limit(1)
        )
        return matched_id is not None

    def _build_agent_candidate_contexts(
        self,
        *,
        db: Session,
        agents: list[dict[str, Any]],
        phase: str,
        phase_context: dict[str, Any],
        limit: int,
        lookback_days: int,
    ) -> dict[str, dict[str, Any]]:
        contexts: dict[str, dict[str, Any]] = {}
        prefer_realtime = bool(phase_context.get("prefer_realtime", True))
        history_end_date = phase_context.get("history_end_date")
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            snapshot_kwargs: dict[str, Any] = {
                "db": db,
                "symbols": None,
                "limit": limit,
                "prefer_realtime": prefer_realtime,
                "lookback_days": lookback_days,
                "agent": agent,
            }
            if history_end_date:
                snapshot_kwargs["end_date"] = str(history_end_date)
            stock_pick_snapshot = ai_stock_picker_service.build_snapshot(**snapshot_kwargs)
            selection_plan = stock_pick_snapshot.get("selection_plan") or {}
            candidates = stock_pick_snapshot["recommendations"]
            contexts[agent_id] = {
                "phase": phase,
                "phase_context": phase_context,
                "stock_pick_snapshot": stock_pick_snapshot,
                "stock_pick_snapshot_id": stock_pick_snapshot["snapshot_id"],
                "selection_plan": selection_plan,
                "selection_mode": stock_pick_snapshot.get("selection_mode") or "auto_universe",
                "candidates": candidates,
                "data_sources": list(stock_pick_snapshot.get("data_sources") or []),
                "ai_data_request": self._agent_ai_data_request(
                    db=db,
                    candidates=candidates,
                    selection_plan=selection_plan,
                    lookback_days=lookback_days,
                    phase_context=phase_context,
                ),
            }
        return contexts

    def _agent_ai_data_request(
        self,
        *,
        db: Session,
        candidates: list[dict[str, Any]],
        selection_plan: dict[str, Any],
        lookback_days: int,
        phase_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        symbols = [
            str(candidate.get("symbol") or "")
            for candidate in candidates[:5]
            if candidate.get("symbol")
        ]
        if not symbols:
            return None
        phase_dimensions = list((phase_context or {}).get("ai_data_dimensions") or [])
        dimensions = self._merge_dimensions(
            phase_dimensions,
            list(selection_plan.get("dimensions") or ["quote", "daily_history"]),
        )
        prefer_realtime = bool((phase_context or {}).get("prefer_realtime", True))
        history_end_date = (phase_context or {}).get("history_end_date")
        try:
            result = ai_data_request_service.execute(
                db,
                symbols=symbols,
                dimensions=dimensions,
                limit=len(symbols),
                lookback_days=int(selection_plan.get("lookback_days") or lookback_days),
                prefer_realtime=prefer_realtime,
                refresh=False,
                end_date=str(history_end_date) if history_end_date else None,
                news_query=str((phase_context or {}).get("news_query") or "") or None,
            )
        except Exception as exc:
            return {
                "requested_symbols": symbols,
                "requested_dimensions": dimensions,
                "actions": [],
                "refresh": None,
                "dataset": {},
                "context": "",
                "context_length": 0,
                "prefer_realtime": prefer_realtime,
                "history_end_date": str(history_end_date) if history_end_date else None,
                "phase_context": phase_context or {},
                "error": str(exc),
            }
        compact = self._compact_ai_data_request(result)
        compact["prefer_realtime"] = prefer_realtime
        compact["history_end_date"] = str(history_end_date) if history_end_date else None
        compact["phase_context"] = phase_context or {}
        return compact

    def _compact_ai_data_request(self, result: dict[str, Any]) -> dict[str, Any]:
        dataset = result.get("dataset") or {}
        return {
            "requested_symbols": list(result.get("requested_symbols") or []),
            "requested_dimensions": list(result.get("requested_dimensions") or []),
            "actions": list(result.get("actions") or []),
            "refresh": result.get("refresh"),
            "dataset": {
                "item_count": int(dataset.get("item_count") or 0),
                "data_sources": list(dataset.get("data_sources") or []),
                "coverage": dict(dataset.get("coverage") or {}),
            },
            "context": str(result.get("context") or ""),
            "context_length": int(result.get("context_length") or 0),
        }

    def _merge_dimensions(
        self,
        base_dimensions: list[str],
        extra_dimensions: list[str],
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*base_dimensions, *extra_dimensions]:
            dimension = str(item or "").strip().lower()
            if not dimension or dimension in seen:
                continue
            seen.add(dimension)
            merged.append(dimension)
        return merged

    def _ai_data_request_lookback_days(
        self,
        ai_data_request: dict[str, Any] | None,
        fallback: int,
    ) -> int:
        for action in (ai_data_request or {}).get("actions") or []:
            window = action.get("temporal_window") or {}
            if window.get("lookback_days"):
                return int(window["lookback_days"])
        return fallback

    def _candidate_pool_scope(
        self,
        agent_candidate_contexts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "mode": "agent_independent_auto_universe",
            "user_candidate_pool": "stock_analysis_only",
            "arena_user_symbols_effect": "ignored",
            "agent_pool_count": len(agent_candidate_contexts),
        }

    def _agent_candidate_pools(
        self,
        agent_candidate_contexts: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        pools: list[dict[str, Any]] = []
        for agent_id, context in agent_candidate_contexts.items():
            candidates = context["candidates"]
            pools.append(
                {
                    "agent_id": agent_id,
                    "selection_mode": context["selection_mode"],
                    "snapshot_id": context["stock_pick_snapshot_id"],
                    "candidate_count": len(candidates),
                    "symbols": [
                        str(candidate.get("symbol"))
                        for candidate in candidates
                        if candidate.get("symbol")
                    ],
                }
            )
        return pools

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

    def _stored_run_candidate_payload(
        self,
        *,
        candidates: list[dict[str, Any]],
        data_sources: list[str],
        candidate_pool_scope: dict[str, Any],
        agent_candidate_pools: list[dict[str, Any]],
        agent_candidate_contexts: dict[str, dict[str, Any]],
        schedule_context: dict[str, Any],
        phase_context: dict[str, Any],
        agent_recommendations: list[dict[str, Any]],
        agent_reviews: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "universe_size": len(candidates),
            "candidate_count": len(candidates),
            "data_sources": list(data_sources),
            "candidates": [
                self._compact_candidate_for_storage(candidate)
                for candidate in candidates[:ARENA_STORED_CANDIDATE_LIMIT]
            ],
            "candidate_pool_scope": dict(candidate_pool_scope),
            "agent_candidate_pools": self._compact_agent_candidate_pools(agent_candidate_pools),
            "stock_pick_snapshot": None,
            "agent_stock_pick_snapshots": {
                agent_id: self._compact_stock_pick_snapshot(context.get("stock_pick_snapshot") or {})
                for agent_id, context in agent_candidate_contexts.items()
            },
            "agent_recommendations": list(agent_recommendations),
            "agent_reviews": list(agent_reviews or []),
            "schedule_context": dict(schedule_context),
            "phase_context": dict(phase_context),
        }

    def _compact_candidate_for_storage(self, candidate: dict[str, Any]) -> dict[str, Any]:
        payload = self._pick_payload(candidate)
        payload["source"] = candidate.get("source")
        payload["amount"] = candidate.get("amount")
        payload["turnover"] = candidate.get("turnover")
        payload["volume_ratio"] = candidate.get("volume_ratio")
        return payload

    def _compact_stock_pick_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        dataset = snapshot.get("dataset") or {}
        return {
            "snapshot_id": snapshot.get("snapshot_id"),
            "selection_mode": snapshot.get("selection_mode"),
            "data_sources": list(snapshot.get("data_sources") or []),
            "context_length": int(snapshot.get("context_length") or 0),
            "recommendation_count": len(snapshot.get("recommendations") or []),
            "dataset": {
                "universe_size": int(dataset.get("universe_size") or 0),
                "item_count": int(dataset.get("item_count") or 0),
                "lookback_days": int(dataset.get("lookback_days") or 0),
                "data_sources": list(dataset.get("data_sources") or []),
                "coverage": dict(dataset.get("coverage") or {}),
            },
            "selection_plan": dict(snapshot.get("selection_plan") or {}),
        }

    def _compact_agent_candidate_pools(
        self,
        pools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compact: list[dict[str, Any]] = []
        for pool in pools:
            symbols = [str(symbol) for symbol in pool.get("symbols") or [] if symbol]
            compact.append(
                {
                    "agent_id": pool.get("agent_id"),
                    "selection_mode": pool.get("selection_mode"),
                    "snapshot_id": pool.get("snapshot_id"),
                    "candidate_count": int(pool.get("candidate_count") or len(symbols)),
                    "symbols": symbols[:ARENA_STORED_POOL_SYMBOL_LIMIT],
                    "symbol_preview_count": min(len(symbols), ARENA_STORED_POOL_SYMBOL_LIMIT),
                }
            )
        return compact

    def _morning_recommendations(
        self,
        *,
        db: Session,
        agents: list[dict[str, Any]],
        agent_candidate_contexts: dict[str, dict[str, Any]],
        snapshot_id: str,
        app_settings: AppSettings,
    ) -> list[dict[str, Any]]:
        recommendations: list[dict[str, Any]] = []
        for index, agent in enumerate(agents):
            agent_id = str(agent.get("id") or f"agent_{index + 1}")
            candidate_context = agent_candidate_contexts[agent_id]
            candidates = candidate_context["candidates"]
            stock_pick_snapshot_id = candidate_context["stock_pick_snapshot_id"]
            data_sources = candidate_context["data_sources"]
            phase_context = candidate_context.get("phase_context") or {}
            ai_data_request = candidate_context.get("ai_data_request")
            style = str(agent.get("style") or "auto")
            llm_config = self._resolve_llm_config(agent=agent, app_settings=app_settings)
            llm_result = self._llm_morning_recommendation(
                agent=agent,
                candidates=candidates,
                snapshot_id=snapshot_id,
                data_sources=data_sources,
                ai_data_request=ai_data_request,
                phase_context=phase_context,
                llm_config=llm_config,
            )
            picks = (llm_result or {}).get("picks") or self._morning_picks(
                candidates=candidates,
                style=style,
                agent=agent,
                agent_index=index,
            )
            picks = self._attach_morning_predictions(
                db,
                picks=picks,
                generated_at=self._now_shanghai(),
                history_end_date=str(phase_context.get("history_end_date") or ""),
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
                    "reason": (llm_result or {}).get("reason") or (
                        f"{playbook['label']} 早盘精选 {len(picks)} 只，"
                        f"首选 {candidate.get('symbol')}，评分 "
                        f"{float(candidate.get('score') or 0):.2f}；"
                        f"依据 {self._phase_basis_text(phase_context)}。"
                    ),
                    "decision_context": self._decision_context(
                        snapshot_id=snapshot_id,
                        stock_pick_snapshot_id=stock_pick_snapshot_id,
                        agent=agent,
                        data_sources=data_sources,
                        ai_data_request=ai_data_request,
                        candidate=candidate,
                        action="WATCH",
                        llm_decision=(llm_result or {}).get("llm_decision"),
                    ),
                }
            )
        return recommendations

    def _llm_morning_recommendation(
        self,
        *,
        agent: dict[str, Any],
        candidates: list[dict[str, Any]],
        snapshot_id: str,
        data_sources: list[str],
        ai_data_request: dict[str, Any] | None,
        phase_context: dict[str, Any],
        llm_config: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        if llm_config is None or not candidates:
            return None
        payload = {
            "model": llm_config["model"],
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是A股模拟交易竞技场的早盘选股AI。"
                        "只能基于今天以前的历史数据、盘前新闻和公告生成1到5只观察名单，"
                        "不得输出买卖指令。只返回JSON，不要输出Markdown。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "snapshot_id": snapshot_id,
                            "phase_context": phase_context,
                            "agent": {
                                "id": agent.get("id"),
                                "name": agent.get("name"),
                                "style": agent.get("style"),
                                "provider": agent.get("provider"),
                                "model": agent.get("model"),
                                "prompt": agent.get("prompt"),
                            },
                            "data_sources": data_sources,
                            "ai_data_request": ai_data_request or {},
                            "candidates": candidates[:12],
                            "output_schema": {
                                "picks": "1到5个候选股票代码，按优先级排序",
                                "reason": "说明如何综合历史数据、新闻和公告",
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
            raw = self._extract_llm_json(response)
        except Exception:
            return None

        picks = self._morning_picks_from_llm(raw=raw, candidates=candidates)
        if not picks:
            return None
        return {
            "picks": picks,
            "reason": str(raw.get("reason") or "").strip(),
            "llm_decision": {
                "used": True,
                "provider": llm_config["provider"],
                "model": llm_config["model"],
                "raw_decision": raw,
            },
        }

    def _morning_picks_from_llm(
        self,
        *,
        raw: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        raw_picks = raw.get("picks") or raw.get("symbols") or raw.get("watchlist") or []
        if not isinstance(raw_picks, list):
            return []
        candidates_by_symbol = {
            str(candidate.get("symbol") or "").strip().upper(): candidate
            for candidate in candidates
            if candidate.get("symbol")
        }
        picks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_picks:
            symbol = (
                str(item.get("symbol") or "")
                if isinstance(item, dict)
                else str(item or "")
            ).strip().upper()
            if not symbol or symbol in seen or symbol not in candidates_by_symbol:
                continue
            seen.add(symbol)
            picks.append(self._pick_payload(candidates_by_symbol[symbol]))
            if len(picks) >= 5:
                break
        return picks

    def _attach_morning_predictions(
        self,
        db: Session,
        *,
        picks: list[dict[str, Any]],
        generated_at: datetime,
        history_end_date: str,
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        cutoff = history_end_date or None
        for pick in picks:
            item = dict(pick)
            symbol = str(item.get("symbol") or "").strip()
            if symbol:
                try:
                    item["prediction"] = chart_data_service.frozen_prediction_snapshot(
                        db,
                        symbol=symbol,
                        generated_at=generated_at,
                        end_date=cutoff,
                    )
                except Exception as exc:
                    item["prediction"] = {
                        "frozen": True,
                        "symbol": symbol,
                        "generated_at": generated_at.isoformat(),
                        "history_end_date": cutoff,
                        "source": "morning_frozen_quant_regime_projection",
                        "forecast_series": [],
                        "error": str(exc),
                    }
            enriched.append(item)
        return enriched

    def agent_dashboard(
        self,
        db: Session,
        *,
        agent_id: str,
        section: str = "morning",
    ) -> dict[str, Any] | None:
        normalized_section = self._normalize_dashboard_section(section)
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
        summary_orders = self._recent_orders(
            db,
            agent_id=agent_id,
            limit=20,
            include_charts=False,
        )
        summary["charts"] = chart_data_service.arena_summary_charts(
            orders=summary_orders,
            positions=list(summary.get("positions") or []),
        )
        section_counts = self._dashboard_section_counts(db, agent_id=agent_id)
        morning_recommendations: list[dict[str, Any]] = []
        recent_orders: list[dict[str, Any]] = []
        closing_reviews: list[dict[str, Any]] = []
        learning_reviews: list[dict[str, Any]] = []

        if normalized_section == "morning":
            morning_recommendations = self._recent_agent_recommendations(
                db,
                agent_id=agent_id,
                limit=10,
                attach_actuals=True,
            )
        elif normalized_section == "intraday":
            prediction_source = self._recent_agent_recommendations(
                db,
                agent_id=agent_id,
                limit=1,
                attach_actuals=False,
            )
            recent_orders = self._recent_orders(
                db,
                agent_id=agent_id,
                limit=ARENA_DASHBOARD_INTRADAY_ORDER_LIMIT,
                prediction_by_symbol=self._prediction_by_symbol(prediction_source),
                include_charts=True,
            )
        elif normalized_section == "closing":
            closing_reviews = self.recent_agent_memories_by_type(
                db,
                agent_id=agent_id,
                memory_type="closing_review",
                limit=10,
                current_schedule_only=True,
            )
        else:
            learning_reviews = self.recent_agent_memories_by_type(
                db,
                agent_id=agent_id,
                memory_type="nightly_learning",
                limit=10,
                current_schedule_only=True,
            )

        return {
            "agent": agent,
            "summary": summary,
            "section": normalized_section,
            "section_counts": section_counts,
            "morning": {
                "recommendations": morning_recommendations
            },
            "intraday": {
                "orders": recent_orders
            },
            "closing": {
                "reviews": closing_reviews
            },
            "learning": {
                "reviews": learning_reviews
            },
        }

    def _normalize_dashboard_section(self, section: str | None) -> str:
        normalized = str(section or "morning").strip().lower()
        return normalized if normalized in ARENA_DASHBOARD_SECTIONS else "morning"

    def _dashboard_section_counts(self, db: Session, *, agent_id: str) -> dict[str, int]:
        morning = len(
            self._recent_agent_recommendations(
                db,
                agent_id=agent_id,
                limit=10,
                attach_actuals=False,
            )
        )
        intraday = len(
            self._recent_orders(
                db,
                agent_id=agent_id,
                limit=100,
                include_charts=False,
            )
        )
        closing = len(
            self.recent_agent_memories_by_type(
                db,
                agent_id=agent_id,
                memory_type="closing_review",
                limit=20,
                current_schedule_only=True,
                include_metrics=False,
            )
        )
        learning = len(
            self.recent_agent_memories_by_type(
                db,
                agent_id=agent_id,
                memory_type="nightly_learning",
                limit=20,
                current_schedule_only=True,
                include_metrics=False,
            )
        )
        return {
            "morning": morning,
            "intraday": intraday,
            "closing": closing,
            "learning": learning,
        }

    def order_forecast(
        self,
        db: Session,
        *,
        order_id: int,
        refresh: bool = False,
    ) -> dict[str, Any]:
        cache_key = self._order_forecast_cache_key(order_id)
        if not refresh and cache_key in self._order_forecast_cache:
            return self._order_forecast_cache[cache_key]

        order = db.get(ArenaOrder, order_id)
        if order is None:
            raise LookupError("竞技场订单不存在。")
        charts = chart_data_service.order_charts(
            db,
            symbol=order.symbol,
            action=order.action,
            trade_date=self._order_marker_trade_time(order),
            price=order.price,
            quantity=order.quantity,
        )
        forecast = ai_forecast_service.analyze_order(
            order_id=order.id,
            symbol=order.symbol,
            name=order.name or order.symbol,
            action=order.action,
            price=order.price,
            quantity=order.quantity,
            price_series=charts["price_series"],
        )
        self._order_forecast_cache[cache_key] = forecast
        while len(self._order_forecast_cache) > ARENA_ORDER_FORECAST_CACHE_LIMIT:
            self._order_forecast_cache.pop(next(iter(self._order_forecast_cache)))
        return forecast

    def _order_forecast_cache_key(self, order_id: int) -> str:
        settings = get_settings()
        base_url = str(settings.forecast_ai_base_url or settings.openai_base_url or "").strip()
        models = str(settings.forecast_ai_models or "").strip()
        has_key = bool(str(settings.forecast_ai_api_key or settings.openai_api_key or "").strip())
        return f"{order_id}:{base_url}:{models}:{has_key}"

    def _closing_reviews(
        self,
        *,
        db: Session,
        agents: list[dict[str, Any]],
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
            phase_context = candidate_context.get("phase_context") or {}
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
                "phase_context": phase_context,
                "ai_data_request": candidate_context.get("ai_data_request"),
                "leaderboard": metrics,
                "recent_orders": recent_orders,
            }
            summary = self._closing_summary(metrics=metrics, recent_orders=recent_orders)
            memory = ArenaAgentMemory(
                agent_id=agent_id,
                agent_name=str(agent.get("name") or account.agent_name),
                style=str(agent.get("style") or account.style or "auto"),
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
        agents: list[dict[str, Any]],
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
            phase_context = candidate_context.get("phase_context") or {}
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
                "phase_context": phase_context,
                "ai_data_request": candidate_context.get("ai_data_request"),
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
                style=str(agent.get("style") or "auto"),
                memory_type="nightly_learning",
                summary=summary,
                metrics_payload=metrics_payload,
            )
            db.add(memory)
            db.flush()
            reviews.append(self._memory_payload(memory))
        return reviews

    def leaderboard(
        self,
        db: Session,
        *,
        refresh_quotes: bool = False,
    ) -> dict[str, Any]:
        cache_key = f"leaderboard:{refresh_quotes}"
        cached = self._cached_live_payload(cache_key)
        if cached is not None:
            return cached
        lock = self._live_payload_inflight_lock(cache_key)
        with lock:
            cached = self._cached_live_payload(cache_key)
            if cached is not None:
                return cached
            items = self._leaderboard_items(
                db,
                include_latest_recommendation=True,
                refresh_quotes=refresh_quotes,
            )
            items.sort(key=lambda item: item["total_assets"], reverse=True)
            return self._store_live_payload(
                cache_key,
                {"items": items},
                ttl_seconds=self._live_payload_ttl(refresh_quotes=refresh_quotes),
            )

    def equity_curves(
        self,
        db: Session,
        *,
        interval: str = "daily",
        refresh_quotes: bool = False,
        current_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_interval = str(interval or "daily").strip().lower()
        if normalized_interval not in {"daily", "weekly", "hourly"}:
            raise ValueError("收益曲线粒度必须是 daily、weekly 或 hourly。")
        cache_key = f"equity_curves:{normalized_interval}:{refresh_quotes}"
        cached = self._cached_live_payload(cache_key)
        if cached is not None:
            return cached
        lock = self._live_payload_inflight_lock(cache_key)
        with lock:
            cached = self._cached_live_payload(cache_key)
            if cached is not None:
                return cached
            return self._build_equity_curves_payload(
                db,
                interval=normalized_interval,
                refresh_quotes=refresh_quotes,
                current_items=current_items,
                cache_key=cache_key,
            )

    def _build_equity_curves_payload(
        self,
        db: Session,
        *,
        interval: str,
        refresh_quotes: bool,
        current_items: list[dict[str, Any]] | None,
        cache_key: str,
    ) -> dict[str, Any]:

        now = datetime.now(ARENA_SCHEDULE_TIMEZONE)
        agents = self.list_agents(db)["agents"]
        agent_by_id = {str(agent.get("id")): agent for agent in agents}
        current_items = (
            deepcopy(current_items)
            if current_items is not None
            else self._leaderboard_items(
                db,
                include_latest_recommendation=False,
                refresh_quotes=refresh_quotes,
            )
        )
        curve_map: dict[str, dict[str, Any]] = {
            str(item["agent_id"]): {
                "agent_id": str(item["agent_id"]),
                "agent_name": str(item["agent_name"]),
                "model": str(agent_by_id.get(str(item["agent_id"]), {}).get("model") or ""),
                "points_by_bucket": {},
            }
            for item in current_items
        }
        active_agent_ids = set(curve_map)

        runs = db.scalars(
            select(ArenaRun)
            .where(ArenaRun.leaderboard_payload.is_not(None))
            .order_by(ArenaRun.id.desc())
            .limit(ARENA_EQUITY_RUN_LIMIT)
        ).all()
        for run in reversed(runs):
            created_at = self._local_time(run.created_at)
            bucket = self._equity_bucket(created_at, interval)
            for item in run.leaderboard_payload or []:
                if not isinstance(item, dict) or not item.get("agent_id"):
                    continue
                agent_id = str(item["agent_id"])
                if agent_id not in active_agent_ids:
                    continue
                agent = agent_by_id.get(agent_id, {})
                curve = curve_map.setdefault(
                    agent_id,
                    {
                        "agent_id": agent_id,
                        "agent_name": str(item.get("agent_name") or agent.get("name") or agent_id),
                        "model": str(agent.get("model") or ""),
                        "points_by_bucket": {},
                    },
                )
                curve["points_by_bucket"][bucket] = {
                    "trade_date": bucket,
                    "value": round(self._number(item.get("total_assets")), 2),
                    "return_ratio": round(self._number(item.get("return_ratio")), 6),
                }

        current_bucket = self._equity_bucket(now, interval)
        for item in current_items:
            agent_id = str(item["agent_id"])
            curve = curve_map.setdefault(
                agent_id,
                {
                    "agent_id": agent_id,
                    "agent_name": str(item["agent_name"]),
                    "model": str(agent_by_id.get(agent_id, {}).get("model") or ""),
                    "points_by_bucket": {},
                },
            )
            curve["agent_name"] = str(item["agent_name"])
            curve["points_by_bucket"][current_bucket] = {
                "trade_date": current_bucket,
                "value": round(self._number(item.get("total_assets")), 2),
                "return_ratio": round(self._number(item.get("return_ratio")), 6),
            }

        current_order = {str(item["agent_id"]): index for index, item in enumerate(current_items)}
        curves = []
        for curve in curve_map.values():
            points = [
                curve["points_by_bucket"][key]
                for key in sorted(curve["points_by_bucket"])
            ]
            curves.append(
                {
                    "agent_id": curve["agent_id"],
                    "agent_name": curve["agent_name"],
                    "model": curve.get("model") or "",
                    "points": points,
                }
            )
        curves.sort(key=lambda curve: current_order.get(str(curve["agent_id"]), 10_000))
        return self._store_live_payload(cache_key, {
            "interval": interval,
            "refreshed_at": now.isoformat(),
            "curves": curves,
        }, ttl_seconds=self._live_payload_ttl(refresh_quotes=refresh_quotes))

    def _cached_live_payload(self, cache_key: str) -> dict[str, Any] | None:
        now = time.time()
        with self._live_payload_cache_lock:
            cached = self._live_payload_cache.get(cache_key)
            if cached is None:
                return None
            expires_at, payload = cached
            if expires_at <= now:
                self._live_payload_cache.pop(cache_key, None)
                self._live_payload_inflight_locks.pop(cache_key, None)
                return None
            return deepcopy(payload)

    def _store_live_payload(
        self,
        cache_key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        ttl = max(1, int(ttl_seconds or get_settings().realtime_quote_cache_ttl_seconds))
        with self._live_payload_cache_lock:
            self._prune_live_payload_cache_locked()
            self._live_payload_cache[cache_key] = (
                time.time() + ttl,
                deepcopy(payload),
            )
        return payload

    def _invalidate_live_payload_cache(self) -> None:
        with self._live_payload_cache_lock:
            self._live_payload_cache.clear()
            self._live_payload_inflight_locks.clear()

    def _live_payload_ttl(self, *, refresh_quotes: bool) -> int:
        if refresh_quotes:
            return max(
                ARENA_LIVE_REFRESH_CACHE_TTL_SECONDS,
                int(get_settings().realtime_quote_cache_ttl_seconds),
            )
        return ARENA_LIVE_CACHE_TTL_SECONDS

    def _live_payload_inflight_lock(self, cache_key: str) -> RLock:
        with self._live_payload_cache_lock:
            lock = self._live_payload_inflight_locks.get(cache_key)
            if lock is None:
                lock = RLock()
                self._live_payload_inflight_locks[cache_key] = lock
            return lock

    def _prune_live_payload_cache_locked(self) -> None:
        now = time.time()
        expired = [
            key for key, (expires_at, _payload) in self._live_payload_cache.items()
            if expires_at <= now
        ]
        for key in expired:
            self._live_payload_cache.pop(key, None)
            self._live_payload_inflight_locks.pop(key, None)

    def _leaderboard_items(
        self,
        db: Session,
        *,
        include_latest_recommendation: bool,
        refresh_quotes: bool = False,
    ) -> list[dict[str, Any]]:
        accounts = db.scalars(
            select(ArenaAccount)
            .options(selectinload(ArenaAccount.positions))
            .order_by(ArenaAccount.id)
        ).all()
        account_by_agent_id = {account.agent_id: account for account in accounts}
        agents = self.list_agents(db)["agents"]
        agent_by_id = {str(agent.get("id")): agent for agent in agents}
        ordered_agent_ids = [str(agent.get("id")) for agent in agents if agent.get("id")]
        has_stored_configs = db.scalar(select(ArenaAgentConfig.id).limit(1)) is not None
        if not has_stored_configs:
            for account in accounts:
                if account.agent_id in agent_by_id:
                    continue
                agent_by_id[account.agent_id] = {
                    "id": account.agent_id,
                    "name": account.agent_name,
                    "style": account.style,
                    "model": "",
                }
                ordered_agent_ids.append(account.agent_id)

        settings = settings_service.get_or_create_settings(db)
        initial_cash = float(settings.arena_initial_cash or ARENA_DEFAULT_INITIAL_CASH)
        quote_by_symbol = (
            self._realtime_quotes_for_positions(accounts) if refresh_quotes else {}
        )
        recent_by_agent_id = (
            self._recent_agent_recommendation_map(
                db,
                agent_ids=ordered_agent_ids,
                limit=1,
            )
            if include_latest_recommendation
            else {}
        )
        items: list[dict[str, Any]] = []
        for agent_id in ordered_agent_ids:
            agent = agent_by_id[agent_id]
            account = account_by_agent_id.get(agent_id)
            if account is None:
                item = self._empty_leaderboard_item(agent=agent, initial_cash=initial_cash)
            else:
                item = self._leaderboard_item(
                    account,
                    include_positions=True,
                    quote_by_symbol=quote_by_symbol,
                )
            if include_latest_recommendation:
                recent = recent_by_agent_id.get(agent_id) or []
                item["latest_recommendation"] = recent[0] if recent else None
            items.append(item)
        return items

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
        current_schedule_only: bool = False,
        include_metrics: bool = True,
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
        if current_schedule_only:
            phase = "nightly_learning" if memory_type == "nightly_learning" else "closing_review"
            memories = [
                memory for memory in memories
                if self._is_current_phase_output(phase=phase, created_at=memory.created_at)
            ]
        return [self._memory_payload(memory, include_metrics=include_metrics) for memory in memories]

    def list_agents(self, db: Session) -> dict[str, Any]:
        stored = db.scalars(
            select(ArenaAgentConfig).order_by(ArenaAgentConfig.id)
        ).all()
        if stored:
            agents = [self._agent_config_payload(item) for item in stored]
        else:
            agents = self._default_agents()
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
            record.style = str(agent.get("style") or "auto")
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
        self._invalidate_live_payload_cache()
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
        record.style = str(agent.get("style") or "auto")
        record.provider = str(agent.get("provider") or "openai-compatible")
        record.model = str(agent.get("model") or "")
        record.prompt = str(agent.get("prompt") or "")
        record.enabled = bool(agent.get("enabled", True))
        db.add(record)
        db.commit()
        self._invalidate_live_payload_cache()
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
        self._invalidate_live_payload_cache()
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
            return self._default_agents()
        return [
            {
                "id": item.agent_id,
                "name": item.agent_name,
                "style": "auto",
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
            "style": item.style or "auto",
            "provider": item.provider,
            "model": item.model,
            "enabled": item.enabled,
            "prompt": item.prompt,
        }

    def _default_agent(self, agent_id: str) -> dict[str, Any] | None:
        match = next((item for item in self._default_agents() if item["id"] == agent_id), None)
        if match is None:
            return None
        return dict(match)

    def _default_agents(self) -> list[dict[str, Any]]:
        models = list(ai_forecast_service.public_config().get("models") or [])
        agents: list[dict[str, Any]] = []
        for index, model in enumerate(models):
            style = DEFAULT_AGENT_STYLES[index % len(DEFAULT_AGENT_STYLES)]
            agents.append(
                {
                    "id": f"model_{self._model_slug(model)}",
                    "name": self._model_display_name(model),
                    "style": style,
                    "provider": "forecast-ai",
                    "model": model,
                    "enabled": True,
                    "prompt": f"使用 {model} 独立完成选股、模拟交易、复盘和学习。",
                }
            )
        return agents

    def _model_slug(self, model: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", str(model or "").lower()).strip("_")
        return slug or "ai"

    def _model_display_name(self, model: str) -> str:
        replacements = {
            "gpt-5.5": "GPT-5.5",
            "deepseek-v4-flash": "DeepSeek V4 Flash",
            "grok-4.20-0309-non-reasoning": "Grok 4.20",
            "minimax-m2.7": "MiniMax M2.7",
            "deepseek-v4-pro": "DeepSeek V4 Pro",
        }
        return replacements.get(model, model)

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
                "style": agent.get("style") or "auto",
                "cash": 0,
                "position_value": 0,
                "total_assets": 0,
                "return_ratio": 0,
                "order_count": 0,
                "realized_pnl": 0,
                "positions": [],
                "playbook": self._playbook_for_style(str(agent.get("style") or "auto")),
            }
        quote_by_symbol = self._realtime_quotes_for_positions([account])
        payload = self._leaderboard_item(
            account,
            include_positions=True,
            quote_by_symbol=quote_by_symbol,
        )
        payload["playbook"] = self._playbook_for_style(account.style)
        return payload

    def _decide_for_agent(
        self,
        *,
        db: Session,
        account: ArenaAccount,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        agent_index: int,
        available_cash: float,
        snapshot_id: str,
        stock_pick_snapshot_id: str,
        data_sources: list[str],
        ai_data_request: dict[str, Any] | None,
        phase_context: dict[str, Any],
        app_settings: AppSettings,
        recent_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        style = str(agent.get("style") or "auto")
        fallback_style = self._fallback_style(style)
        llm_config = self._resolve_llm_config(agent=agent, app_settings=app_settings)
        dimension_request = None
        if llm_config is not None:
            base_dimensions = list((ai_data_request or {}).get("requested_dimensions") or [])
            dimension_request = self._llm_dimension_request(
                agent=agent,
                candidates=candidates,
                snapshot_id=snapshot_id,
                base_dimensions=base_dimensions,
                phase_context=phase_context,
                llm_config=llm_config,
            )
            if dimension_request:
                ai_data_request = self._agent_ai_data_request(
                    db=db,
                    candidates=candidates,
                    selection_plan={
                        "dimensions": self._merge_dimensions(
                            base_dimensions,
                            list(dimension_request.get("dimensions") or []),
                        ),
                        "lookback_days": self._ai_data_request_lookback_days(
                            ai_data_request,
                            get_settings().market_data_maintenance_lookback_days,
                        ),
                    },
                    lookback_days=get_settings().market_data_maintenance_lookback_days,
                    phase_context=phase_context,
                )
        llm_decision = self._llm_decision(
            agent=agent,
            candidates=candidates,
            positions=account.positions,
            snapshot_id=snapshot_id,
            data_sources=data_sources,
            ai_data_request=ai_data_request,
            dimension_request=dimension_request,
            phase_context=phase_context,
            llm_config=llm_config,
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
                    ai_data_request=ai_data_request,
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
                ai_data_request=ai_data_request,
                llm_decision=llm_decision["context"],
                agent_index=agent_index,
            )
        if fallback_style == "momentum":
            allocation_ratio = 0.45
        elif fallback_style == "risk_control":
            allocation_ratio = 0.2
        else:
            allocation_ratio = 0.3

        budget = available_cash * allocation_ratio
        preferred_index = (
            0
            if style == "auto" or fallback_style == "momentum"
            else min(agent_index, len(candidates) - 1)
        )
        ordered_candidates = self._ordered_candidates_for_agent(
            candidates=candidates,
            style=style,
            agent=agent,
            agent_index=agent_index,
        )
        candidate = self._select_affordable_candidate(
            candidates=ordered_candidates,
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
            reason=f"{self._playbook_for_style(style)['label']} 根据候选评分 {candidate['score']} 执行模拟买入。",
            snapshot_id=snapshot_id,
            stock_pick_snapshot_id=stock_pick_snapshot_id,
            data_sources=data_sources,
            ai_data_request=ai_data_request,
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
        ai_data_request: dict[str, Any] | None,
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
                ai_data_request=ai_data_request,
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
        ai_data_request: dict[str, Any] | None,
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
                ai_data_request=ai_data_request,
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
        ai_data_request: dict[str, Any] | None,
        dimension_request: dict[str, Any] | None,
        phase_context: dict[str, Any],
        llm_config: dict[str, str] | None,
        app_settings: AppSettings,
        recent_memories: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
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
                        "必须先读取phase_context，再结合该阶段要求的数据输入做低频模拟交易决策。"
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
                        ai_data_request=ai_data_request,
                        phase_context=phase_context,
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
                "data_dimension_request": dimension_request or {},
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

    def _llm_dimension_request(
        self,
        *,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        snapshot_id: str,
        base_dimensions: list[str],
        phase_context: dict[str, Any],
        llm_config: dict[str, str],
    ) -> dict[str, Any] | None:
        allowed_dimensions = [
            "quote",
            "news",
            "announcement",
            "daily_history",
            "moneyflow",
            "sector_heat",
            "limit_event",
            "financial_indicator",
            "valuation",
            "pledge_stat",
            "stock_profile",
        ]
        prompt_payload = {
            "snapshot_id": snapshot_id,
            "agent": {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "style": agent.get("style"),
                "prompt": agent.get("prompt"),
            },
            "base_dimensions": base_dimensions,
            "phase_context": phase_context,
            "allowed_dimensions": allowed_dimensions,
            "candidates": [
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "score": item.get("score"),
                    "ai_selection": item.get("ai_selection") or {},
                    "retail_analysis": item.get("retail_analysis") or {},
                }
                for item in candidates[:5]
            ],
            "output_schema": {
                "dimensions": "从 allowed_dimensions 里选择还想补看的维度数组",
                "reason": "一句话说明为什么需要这些数据",
            },
        }
        payload = {
            "model": llm_config["model"],
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是A股竞技场AI的数据研究员。"
                        "先读取phase_context确认当前阶段，再判断还需要补充哪些数据维度，只返回JSON。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False, default=str),
                },
            ],
        }
        try:
            response = llm_service._call_llm(
                base_url=llm_config["base_url"],
                api_key=llm_config["api_key"],
                payload=payload,
                timeout_seconds=45,
            )
            raw = self._extract_llm_json(response)
        except Exception:
            return None
        dimensions = [
            str(item or "").strip().lower()
            for item in raw.get("dimensions") or []
            if str(item or "").strip().lower() in allowed_dimensions
        ]
        dimensions = self._merge_dimensions([], dimensions)
        if not dimensions:
            return None
        return {
            "dimensions": dimensions,
            "reason": str(raw.get("reason") or ""),
            "raw_decision": raw,
        }

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

        runtime_settings = get_settings()
        forecast_config = ai_forecast_service.internal_config()
        forecast_models = list(forecast_config.get("models") or [])
        use_forecast = provider == "forecast-ai" or str(agent.get("model") or "") in forecast_models
        base_url = str(
            provider_config.get("base_url")
            or (forecast_config.get("base_url") if use_forecast else None)
            or getattr(app_settings, "llm_base_url", None)
            or runtime_settings.openai_base_url
            or ""
        ).strip()
        api_key = str(
            provider_config.get("api_key")
            or (forecast_config.get("api_key") if use_forecast else None)
            or getattr(app_settings, "llm_api_key", None)
            or runtime_settings.openai_api_key
            or ""
        ).strip()
        model = str(
            agent.get("model")
            or provider_config.get("default_model")
            or provider_config.get("model")
            or getattr(app_settings, "llm_model", None)
            or (forecast_models[0] if use_forecast and forecast_models else "")
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
        ai_data_request: dict[str, Any] | None,
        phase_context: dict[str, Any],
        recent_memories: list[dict[str, Any]],
    ) -> str:
        prompt_payload = {
            "snapshot_id": snapshot_id,
            "phase_context": phase_context,
            "agent": {
                "id": agent.get("id"),
                "name": agent.get("name"),
                "style": agent.get("style"),
                "provider": agent.get("provider"),
                "model": agent.get("model"),
                "prompt": agent.get("prompt"),
            },
            "data_sources": data_sources,
            "ai_data_request": ai_data_request or {},
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
        stop_loss = STOP_LOSS_BY_STYLE.get(self._fallback_style(account.style), 0.06)
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
        ai_data_request: dict[str, Any] | None = None,
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
                "style": str(agent.get("style") or "auto"),
                "provider": str(agent.get("provider") or "openai-compatible"),
                "model": str(agent.get("model") or ""),
                "prompt": str(agent.get("prompt") or ""),
            },
            "action": action,
            "data_sources": data_sources,
            "ai_data_request": ai_data_request or {},
            "selection_plan": (candidate.get("ai_selection") or {}).get("plan") or {},
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
            style=str(agent.get("style") or "auto"),
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
        quote_by_symbol: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        position_payloads: list[dict[str, Any]] = []
        quote_map = quote_by_symbol or {}
        for position in account.positions:
            if position.quantity <= 0:
                continue
            normalized_symbol = normalize_symbol(position.symbol)
            quote = quote_map.get(normalized_symbol) or {}
            quote_price = self._number(quote.get("price"), default=0.0)
            last_price = quote_price if quote_price > 0 else float(position.last_price or 0)
            position_payloads.append(
                {
                    "symbol": normalized_symbol,
                    "name": position.name,
                    "quantity": position.quantity,
                    "avg_cost": round(position.avg_cost, 4),
                    "last_price": round(last_price, 4),
                    "market_value": round(position.quantity * last_price, 2),
                    "unrealized_pnl": round(
                        position.quantity * (last_price - position.avg_cost),
                        2,
                    ),
                }
            )
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

    def _empty_leaderboard_item(
        self,
        *,
        agent: dict[str, Any],
        initial_cash: float,
    ) -> dict[str, Any]:
        return {
            "agent_id": str(agent.get("id") or ""),
            "agent_name": str(agent.get("name") or agent.get("id") or "AI"),
            "style": str(agent.get("style") or "auto"),
            "cash": round(initial_cash, 2),
            "position_value": 0.0,
            "total_assets": round(initial_cash, 2),
            "return_ratio": 0.0,
            "order_count": 0,
            "realized_pnl": 0.0,
            "positions": [],
        }

    def _morning_picks(
        self,
        *,
        candidates: list[dict[str, Any]],
        style: str,
        agent: dict[str, Any],
        agent_index: int,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        ordered = self._ordered_candidates_for_agent(
            candidates=candidates,
            style=style,
            agent=agent,
            agent_index=agent_index,
        )
        pick_limit = min(5, max(1, len(ordered) - 1))
        return [self._pick_payload(candidate) for candidate in ordered[:pick_limit]]

    def _ordered_candidates_for_agent(
        self,
        *,
        candidates: list[dict[str, Any]],
        style: str,
        agent: dict[str, Any],
        agent_index: int,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        normalized_style = str(style or "auto").strip()
        fallback_style = self._fallback_style(style)
        if normalized_style == "auto":
            profile = self._agent_profile_index(agent)
            if profile == 0:
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        self._candidate_ai_score(item),
                        self._candidate_recent_momentum(item),
                        self._number(item.get("change_pct")),
                        self._number(item.get("amount")),
                    ),
                    reverse=True,
                )
            elif profile == 1:
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        self._candidate_financial_quality(item),
                        self._candidate_ai_score(item),
                        self._candidate_recent_momentum(item),
                    ),
                    reverse=True,
                )
            elif profile == 2:
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        self._candidate_risk_adjusted_score(item),
                        self._candidate_ai_score(item),
                        self._number(item.get("amount")),
                    ),
                    reverse=True,
                )
            else:
                ordered = sorted(
                    candidates,
                    key=lambda item: (
                        self._number(item.get("amount")),
                        self._number(item.get("volume_ratio")),
                        self._candidate_ai_score(item),
                    ),
                    reverse=True,
                )
            return self._rotate_priority_band(
                ordered,
                agent=agent,
                agent_index=agent_index,
            )
        if fallback_style == "risk_control":
            ordered = sorted(
                candidates,
                key=lambda item: (
                    float(item.get("change_pct") or 0),
                    float(item.get("score") or 0),
                ),
                reverse=True,
            )
        elif fallback_style == "balanced":
            ordered = candidates[agent_index:] + candidates[:agent_index]
        else:
            ordered = candidates
        return list(ordered)

    def _rotate_priority_band(
        self,
        ordered: list[dict[str, Any]],
        *,
        agent: dict[str, Any],
        agent_index: int,
    ) -> list[dict[str, Any]]:
        band_size = min(len(ordered), 8)
        if band_size <= 1:
            return ordered
        offset = (self._agent_seed(agent) + agent_index) % band_size
        if offset == 0:
            return ordered
        priority_band = ordered[:band_size]
        return priority_band[offset:] + priority_band[:offset] + ordered[band_size:]

    def _agent_profile_index(self, agent: dict[str, Any]) -> int:
        return self._agent_seed(agent) % 4

    def _agent_seed(self, agent: dict[str, Any]) -> int:
        identity = "|".join(
            str(agent.get(key) or "")
            for key in ("id", "model", "name", "provider", "prompt")
        )
        return int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16)

    def _candidate_ai_score(self, candidate: dict[str, Any]) -> float:
        ai_selection = candidate.get("ai_selection") or {}
        return self._number(ai_selection.get("score") or candidate.get("score"))

    def _candidate_recent_momentum(self, candidate: dict[str, Any]) -> float:
        daily = candidate.get("daily_factors") or {}
        return self._number(daily.get("recent_momentum_pct") or daily.get("momentum_pct"))

    def _candidate_financial_quality(self, candidate: dict[str, Any]) -> float:
        financial = candidate.get("financial_factors") or {}
        return (
            self._number(financial.get("roe")) * 0.4
            + self._number(financial.get("netprofit_yoy")) * 0.3
            + self._number(financial.get("grossprofit_margin")) * 0.1
            + self._candidate_ai_score(candidate) * 0.2
        )

    def _candidate_risk_adjusted_score(self, candidate: dict[str, Any]) -> float:
        ai_selection = candidate.get("ai_selection") or {}
        risk_count = len(ai_selection.get("risk_flags") or [])
        daily = candidate.get("daily_factors") or {}
        above_ma60 = bool(daily.get("above_ma60"))
        volatility = self._number(daily.get("volatility_pct"))
        change_pct = self._number(candidate.get("change_pct"))
        return (
            self._candidate_ai_score(candidate)
            - risk_count * 8
            + (8 if above_ma60 else 0)
            + max(0.0, 6.0 - volatility)
            - max(0.0, change_pct - 5.0)
        )

    def _number(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

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
            "reasons": list(ai_selection.get("selection_reasons") or []),
            "data_sources": [
                source
                for source in [
                    candidate.get("source"),
                    *list(candidate.get("source_candidates") or []),
                    "tushare_daily" if (candidate.get("daily_factors") or {}).get("bars_used") else None,
                ]
                if source
            ],
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
        if style == "auto":
            return {
                "mode": "ai_adaptive",
                "label": "AI自适应",
                "holding_period": "由AI按市场判断",
            }
        return {
            "mode": "quant_rotation",
            "label": "量化轮动",
            "holding_period": "3-10个交易日",
        }

    def _fallback_style(self, style: str) -> str:
        normalized = str(style or "auto").strip()
        if normalized == "auto":
            return "balanced"
        if normalized in {"momentum", "balanced", "risk_control"}:
            return normalized
        return "balanced"

    def _recent_agent_recommendations(
        self,
        db: Session,
        *,
        agent_id: str,
        limit: int,
        attach_actuals: bool = True,
    ) -> list[dict[str, Any]]:
        runs = db.scalars(
            select(ArenaRun)
            .where(
                ArenaRun.phase == "morning_recommendation",
                func.length(ArenaRun.candidate_payload) <= ARENA_CANDIDATE_PAYLOAD_MAX_READ_BYTES,
            )
            .order_by(ArenaRun.id.desc())
            .limit(max(1, min(limit * 5, 100)))
        ).all()
        recommendations: list[dict[str, Any]] = []
        for run in runs:
            payload = run.candidate_payload or {}
            if not self._is_current_phase_output(
                phase="morning_recommendation",
                created_at=run.created_at,
                schedule_context=payload.get("schedule_context"),
            ):
                continue
            for item in payload.get("agent_recommendations") or []:
                if item.get("agent_id") != agent_id:
                    continue
                enriched = dict(item)
                self._enrich_recommendation_names(db, enriched)
                if attach_actuals:
                    self._attach_recommendation_prediction_actuals(db, enriched)
                enriched["run_id"] = run.id
                enriched["created_at"] = run.created_at.isoformat() if run.created_at else None
                recommendations.append(enriched)
                if len(recommendations) >= limit:
                    return recommendations
        return recommendations

    def _recent_agent_recommendation_map(
        self,
        db: Session,
        *,
        agent_ids: list[str],
        limit: int,
    ) -> dict[str, list[dict[str, Any]]]:
        normalized_ids = [str(agent_id) for agent_id in agent_ids if agent_id]
        if not normalized_ids:
            return {}

        requested_ids = set(normalized_ids)
        per_agent_limit = max(1, min(limit, 10))
        recommendations = {agent_id: [] for agent_id in normalized_ids}
        runs = db.scalars(
            select(ArenaRun)
            .where(
                ArenaRun.phase == "morning_recommendation",
                func.length(ArenaRun.candidate_payload) <= ARENA_CANDIDATE_PAYLOAD_MAX_READ_BYTES,
            )
            .order_by(ArenaRun.id.desc())
            .limit(max(10, min(len(normalized_ids) * per_agent_limit * 5, 100)))
        ).all()
        for run in runs:
            if all(len(items) >= per_agent_limit for items in recommendations.values()):
                break
            payload = run.candidate_payload or {}
            if not self._is_current_phase_output(
                phase="morning_recommendation",
                created_at=run.created_at,
                schedule_context=payload.get("schedule_context"),
            ):
                continue
            for item in payload.get("agent_recommendations") or []:
                agent_id = str(item.get("agent_id") or "")
                if agent_id not in requested_ids:
                    continue
                if len(recommendations[agent_id]) >= per_agent_limit:
                    continue
                enriched = dict(item)
                self._enrich_recommendation_names(db, enriched)
                self._attach_recommendation_prediction_actuals(db, enriched)
                enriched["run_id"] = run.id
                enriched["created_at"] = run.created_at.isoformat() if run.created_at else None
                recommendations[agent_id].append(enriched)
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

    def _attach_recommendation_prediction_actuals(
        self,
        db: Session,
        recommendation: dict[str, Any],
    ) -> None:
        for pick in recommendation.get("picks") or []:
            if not isinstance(pick, dict):
                continue
            prediction = pick.get("prediction")
            if not isinstance(prediction, dict):
                continue
            symbol = str(pick.get("symbol") or "").strip()
            if not symbol:
                continue
            try:
                series = chart_data_service.price_series(db, symbol=symbol, limit=120)
                forecast = chart_data_service._forecast_with_trading_dates(
                    list(prediction.get("forecast_series") or []),
                    base_date=str(
                        prediction.get("history_end_date")
                        or (series[-1].get("trade_date") if series else "")
                    ),
                )
                prediction["forecast_series"] = forecast
                prediction["actual_comparison"] = chart_data_service.forecast_actual_comparison(
                    price_series=series,
                    forecast_series=forecast,
                )
            except Exception as exc:
                prediction["actual_comparison"] = {
                    "matched_points": [],
                    "latest_error_pct": None,
                    "summary": f"实际走势对照暂不可用：{exc}",
                }

    def _recent_orders(
        self,
        db: Session,
        *,
        agent_id: str,
        limit: int,
        prediction_by_symbol: dict[str, dict[str, Any]] | None = None,
        include_charts: bool = True,
    ) -> list[dict[str, Any]]:
        orders = db.scalars(
            select(ArenaOrder)
            .where(ArenaOrder.agent_id == agent_id)
            .order_by(ArenaOrder.id.desc())
            .limit(limit)
        ).all()
        orders = [
            order for order in orders
            if self._is_current_phase_output(
                phase="intraday_trade",
                created_at=order.created_at,
            )
        ]
        quote_by_symbol = self._realtime_quotes_for_orders(orders) if include_charts else {}
        prediction_by_symbol = prediction_by_symbol or {}
        hourly_by_symbol: dict[str, list[dict[str, Any]]] = {}
        if include_charts:
            for order in orders:
                try:
                    symbol = normalize_symbol(order.symbol)
                except ValueError:
                    continue
                if symbol not in hourly_by_symbol:
                    hourly_by_symbol[symbol] = chart_data_service.real_hourly_series(symbol)

        payloads: list[dict[str, Any]] = []
        for order in orders:
            item = {
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
            if include_charts:
                normalized_symbol = normalize_symbol(order.symbol)
                item["charts"] = chart_data_service.order_charts(
                    db,
                    symbol=order.symbol,
                    action=order.action,
                    trade_date=self._order_marker_trade_time(order),
                    price=order.price,
                    quantity=order.quantity,
                    realtime_quote=quote_by_symbol.get(normalize_symbol(order.symbol)),
                    frozen_prediction=prediction_by_symbol.get(normalize_symbol(order.symbol)),
                    hourly_series=hourly_by_symbol.get(normalized_symbol, []),
                )
            payloads.append(item)
        return payloads

    def _prediction_by_symbol(
        self,
        recommendations: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for recommendation in recommendations:
            for pick in recommendation.get("picks") or []:
                if not isinstance(pick, dict):
                    continue
                prediction = pick.get("prediction")
                if not isinstance(prediction, dict):
                    continue
                symbol = str(pick.get("symbol") or "").strip()
                if not symbol:
                    continue
                try:
                    result.setdefault(normalize_symbol(symbol), prediction)
                except ValueError:
                    continue
        return result

    def _realtime_quotes_for_orders(self, orders: list[ArenaOrder]) -> dict[str, dict[str, Any]]:
        return self._realtime_quotes_for_symbols([order.symbol for order in orders if order.symbol])

    def _realtime_quotes_for_positions(self, accounts: list[ArenaAccount]) -> dict[str, dict[str, Any]]:
        return self._realtime_quotes_for_symbols(
            [
                position.symbol
                for account in accounts
                for position in account.positions
                if position.symbol and position.quantity > 0
            ],
            prefer_realtime=False,
        )

    def _realtime_quotes_for_symbols(
        self,
        symbols: list[str],
        *,
        prefer_realtime: bool = True,
    ) -> dict[str, dict[str, Any]]:
        normalized_symbols: set[str] = set()
        for symbol in symbols:
            try:
                normalized_symbols.add(normalize_symbol(symbol))
            except ValueError:
                continue
        symbols = sorted(normalized_symbols)
        if not symbols:
            return {}
        try:
            quotes = market_data_service.get_quotes(symbols, prefer_realtime=prefer_realtime)
        except Exception:
            return {}
        return {
            normalize_symbol(str(quote.get("symbol") or "")): quote
            for quote in quotes
            if quote.get("symbol")
        }

    def _local_time(self, value: datetime | None) -> datetime:
        timestamp = value or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(ARENA_SCHEDULE_TIMEZONE)

    def _order_marker_trade_time(self, order: ArenaOrder) -> str | None:
        timestamp = order.created_at or order.decision_recorded_at
        if timestamp is None:
            return None
        return self._local_time(timestamp).strftime("%Y-%m-%d %H:%M")

    def _equity_bucket(self, value: datetime, interval: str) -> str:
        if interval == "hourly":
            return value.strftime("%Y-%m-%d %H:00")
        if interval == "weekly":
            year, week, _ = value.isocalendar()
            return f"{year}-W{week:02d}"
        return value.strftime("%Y-%m-%d")

    def _is_current_phase_output(
        self,
        *,
        phase: str,
        created_at: datetime | None,
        schedule_context: dict[str, Any] | None = None,
    ) -> bool:
        reference_at = self._phase_output_reference_time(
            created_at=created_at,
            schedule_context=schedule_context,
        )
        if reference_at is None:
            return False
        now = self._now_shanghai()
        created_local = self._local_time(reference_at)
        if created_local.date() != now.date():
            return False
        return any(
            window_start <= created_local <= window_end and now >= window_start
            for window_start, window_end in self._phase_windows_for_day(
                phase=phase,
                now=now,
            )
        )

    def _phase_output_reference_time(
        self,
        *,
        created_at: datetime | None,
        schedule_context: dict[str, Any] | None,
    ) -> datetime | None:
        if isinstance(schedule_context, dict):
            scheduled_at = self._parse_datetime(schedule_context.get("scheduled_at"))
            if scheduled_at is not None:
                return scheduled_at
        return created_at

    def _parse_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _phase_start_for_day(self, *, phase: str, now: datetime) -> datetime | None:
        windows = self._phase_windows_for_day(phase=phase, now=now)
        if not windows:
            return None
        return min(window[0] for window in windows)

    def _phase_windows_for_day(self, *, phase: str, now: datetime) -> list[tuple[datetime, datetime]]:
        localized_now = now
        if localized_now.tzinfo is None:
            localized_now = localized_now.replace(tzinfo=ARENA_SCHEDULE_TIMEZONE)
        windows: list[tuple[datetime, datetime]] = []
        for window in ARENA_SCHEDULE_WINDOWS:
            if window["phase"] != phase:
                continue
            hour, minute = window["start"]
            window_start = localized_now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            windows.append((
                window_start,
                window_start + timedelta(minutes=int(window["window_minutes"])),
            ))
        return windows

    def _closing_summary(
        self,
        *,
        metrics: dict[str, Any],
        recent_orders: list[dict[str, Any]],
    ) -> str:
        return_pct = float(metrics.get("return_ratio") or 0) * 100
        position_count = len(metrics.get("positions") or [])
        trade_count = len(recent_orders)
        trend_summary = self._closing_trend_summary(recent_orders)
        return (
            f"{metrics['agent_name']} 收盘复盘：总资产 {metrics['total_assets']:.2f}，"
            f"收益 {return_pct:.2f}%，现金 {metrics['cash']:.2f}，"
            f"持仓 {position_count} 只，今日操作 {trade_count} 笔，"
            f"{trend_summary}；已结合历史日线复核。"
        )

    def _closing_trend_summary(self, recent_orders: list[dict[str, Any]]) -> str:
        for order in recent_orders:
            price_series = ((order.get("charts") or {}).get("price_series") or [])
            prices = [
                float(item.get("close") or item.get("price") or 0)
                for item in price_series
                if isinstance(item, dict) and (item.get("close") or item.get("price"))
            ]
            if len(prices) < 2 or prices[0] <= 0:
                continue
            change_pct = (prices[-1] - prices[0]) / prices[0] * 100
            return f"走势 {change_pct:+.2f}%"
        return "走势暂无足够成交图表"

    def _phase_basis_text(self, phase_context: dict[str, Any]) -> str:
        focus = str(phase_context.get("data_focus") or "").strip()
        history_end_date = str(phase_context.get("history_end_date") or "").strip()
        news_query = str(phase_context.get("news_query") or "").strip()
        parts: list[str] = []
        if history_end_date:
            parts.append(f"历史截至 {history_end_date}")
        if focus:
            parts.append(focus)
        if news_query:
            parts.append(f"资讯查询 {news_query}")
        return "，".join(parts) or "阶段数据策略"

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

    def _memory_payload(
        self,
        memory: ArenaAgentMemory,
        *,
        include_metrics: bool = True,
    ) -> dict[str, Any]:
        return {
            "id": memory.id,
            "agent_id": memory.agent_id,
            "agent_name": memory.agent_name,
            "style": memory.style,
            "memory_type": memory.memory_type,
            "summary": memory.summary,
            "metrics": (memory.metrics_payload or {}) if include_metrics else {},
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

    def _order_payload(self, db: Session, order: ArenaOrder) -> dict[str, Any]:
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
            "charts": chart_data_service.order_charts(
                db,
                symbol=order.symbol,
                action=order.action,
                trade_date=self._order_marker_trade_time(order),
                price=order.price,
                quantity=order.quantity,
            ),
        }


arena_service = ArenaService()
