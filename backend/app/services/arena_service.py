from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import ArenaOrder, ArenaRun
from app.services.quant_service import quant_service


DEFAULT_AGENTS = [
    {"id": "momentum_ai", "name": "动量 AI", "style": "momentum"},
    {"id": "balanced_ai", "name": "均衡 AI", "style": "balanced"},
    {"id": "risk_ai", "name": "风控 AI", "style": "risk_control"},
]


class ArenaService:
    def run_once(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        agents: list[dict[str, str]] | None = None,
        initial_cash: float = 200000.0,
    ) -> dict[str, Any]:
        active_agents = agents or DEFAULT_AGENTS
        candidate_payload = quant_service.generate_candidates(
            symbols=symbols,
            limit=max(5, len(active_agents)),
            prefer_realtime=True,
        )
        candidates = candidate_payload["candidates"]
        arena_run = ArenaRun(
            initial_cash=initial_cash,
            universe_json=symbols,
            candidate_payload=candidate_payload,
            leaderboard_payload=[],
        )
        db.add(arena_run)
        db.flush()

        leaderboard: list[dict[str, Any]] = []
        orders: list[dict[str, Any]] = []
        for index, agent in enumerate(active_agents):
            decision = self._decide_for_agent(
                agent=agent,
                candidates=candidates,
                agent_index=index,
                initial_cash=initial_cash,
            )
            if decision is not None:
                order = ArenaOrder(
                    arena_run_id=arena_run.id,
                    agent_id=decision["agent_id"],
                    agent_name=decision["agent_name"],
                    style=decision["style"],
                    action="BUY",
                    symbol=decision["symbol"],
                    name=decision["name"],
                    quantity=decision["quantity"],
                    price=decision["price"],
                    amount=decision["amount"],
                    remaining_cash=decision["remaining_cash"],
                    reason=decision["reason"],
                )
                db.add(order)
                db.flush()
                order_payload = self._order_payload(order)
                orders.append(order_payload)
            else:
                order_payload = None

            total_assets = initial_cash
            cash = initial_cash
            position_value = 0.0
            if order_payload is not None:
                cash = float(order_payload["remaining_cash"])
                position_value = float(order_payload["amount"])
                total_assets = cash + position_value
            leaderboard.append(
                {
                    "agent_id": str(agent.get("id") or f"agent_{index + 1}"),
                    "agent_name": str(agent.get("name") or f"AI {index + 1}"),
                    "style": str(agent.get("style") or "balanced"),
                    "cash": round(cash, 2),
                    "position_value": round(position_value, 2),
                    "total_assets": round(total_assets, 2),
                    "return_ratio": 0.0,
                    "order_count": 1 if order_payload is not None else 0,
                }
            )

        arena_run.leaderboard_payload = leaderboard
        db.add(arena_run)
        db.commit()
        return {
            "run_id": arena_run.id,
            "candidate_count": candidate_payload["candidate_count"],
            "candidates": candidates,
            "leaderboard": leaderboard,
            "orders": orders,
            "data_sources": candidate_payload["data_sources"],
        }

    def _decide_for_agent(
        self,
        *,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        agent_index: int,
        initial_cash: float,
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        style = str(agent.get("style") or "balanced")
        if style == "momentum":
            allocation_ratio = 0.45
        elif style == "risk_control":
            allocation_ratio = 0.2
        else:
            allocation_ratio = 0.3

        budget = initial_cash * allocation_ratio
        preferred_index = 0 if style == "momentum" else min(agent_index, len(candidates) - 1)
        candidate = self._select_affordable_candidate(
            candidates=candidates,
            preferred_index=preferred_index,
            budget=budget,
        )
        if candidate is None:
            return None
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
            "symbol": candidate["symbol"],
            "name": candidate.get("name") or candidate["symbol"],
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "remaining_cash": round(initial_cash - amount, 2),
            "reason": f"{style} 根据候选评分 {candidate['score']} 执行模拟买入。",
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
        }


arena_service = ArenaService()
