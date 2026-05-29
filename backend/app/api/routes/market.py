from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.database import get_db
from app.schemas.aniu import (
    ArenaRunRequest,
    ArenaRunResponse,
    MarketSourceHealthResponse,
    QuantCandidatesRequest,
    QuantCandidatesResponse,
)
from app.services.arena_service import arena_service
from app.services.market_data_service import market_data_service
from app.services.quant_service import quant_service

router = APIRouter(tags=["aniu-market-quant-arena"])


@router.get("/market/sources/health", response_model=MarketSourceHealthResponse)
def get_market_source_health(
    _user: str = Depends(get_current_user),
) -> MarketSourceHealthResponse:
    return market_data_service.source_health()


@router.post("/quant/candidates", response_model=QuantCandidatesResponse)
def generate_quant_candidates(
    payload: QuantCandidatesRequest,
    _user: str = Depends(get_current_user),
) -> QuantCandidatesResponse:
    try:
        return quant_service.generate_candidates(
            symbols=payload.symbols,
            limit=payload.limit,
            prefer_realtime=payload.prefer_realtime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/arena/run", response_model=ArenaRunResponse)
def run_arena_once(
    payload: ArenaRunRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaRunResponse:
    try:
        agents = (
            [agent.model_dump() for agent in payload.agents]
            if payload.agents is not None
            else None
        )
        return arena_service.run_once(
            db,
            symbols=payload.symbols,
            agents=agents,
            initial_cash=payload.initial_cash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
