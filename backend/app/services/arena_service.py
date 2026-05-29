from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import ArenaAccount, ArenaAgentConfig, ArenaOrder, ArenaPosition, ArenaRun
from app.services.quant_service import quant_service


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
    def run_once(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        agents: list[dict[str, str]] | None = None,
        initial_cash: float = 200000.0,
    ) -> dict[str, Any]:
        active_agents = agents or self.enabled_agents(db)
        candidate_payload = quant_service.generate_candidates(
            db=db,
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
            account = self._get_or_create_account(
                db,
                agent=agent,
                initial_cash=initial_cash,
            )
            self._mark_positions(account, candidates)
            sell_decision = self._stop_loss_decision(account)
            decision = sell_decision or self._decide_for_agent(
                agent=agent,
                candidates=candidates,
                agent_index=index,
                available_cash=account.cash,
            )
            if decision is not None:
                if decision["action"] == "SELL":
                    self._apply_sell(account, decision)
                else:
                    self._apply_buy(account, decision)
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
            "candidate_count": candidate_payload["candidate_count"],
            "candidates": candidates,
            "leaderboard": leaderboard,
            "orders": orders,
            "data_sources": candidate_payload["data_sources"],
        }

    def leaderboard(self, db: Session) -> dict[str, Any]:
        accounts = db.scalars(
            select(ArenaAccount)
            .options(selectinload(ArenaAccount.positions))
            .order_by(ArenaAccount.id)
        ).all()
        items = [self._leaderboard_item(account, include_positions=True) for account in accounts]
        items.sort(key=lambda item: item["total_assets"], reverse=True)
        return {"items": items}

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

    def _decide_for_agent(
        self,
        *,
        agent: dict[str, str],
        candidates: list[dict[str, Any]],
        agent_index: int,
        available_cash: float,
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

        budget = available_cash * allocation_ratio
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
            "action": "BUY",
            "symbol": candidate["symbol"],
            "name": candidate.get("name") or candidate["symbol"],
            "quantity": quantity,
            "price": price,
            "amount": amount,
            "remaining_cash": round(available_cash - amount, 2),
            "reason": f"{style} 根据候选评分 {candidate['score']} 执行模拟买入。",
        }

    def _stop_loss_decision(self, account: ArenaAccount) -> dict[str, Any] | None:
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
            }
        return None

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
