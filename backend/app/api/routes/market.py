from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.database import get_db
from app.schemas.aniu import (
    ArenaRunRequest,
    ArenaRunResponse,
    ArenaLeaderboardResponse,
    ArenaAgentsResponse,
    ArenaAgentsUpdateRequest,
    BacktestRequest,
    BacktestResponse,
    DailyRefreshRequest,
    DailyRefreshResponse,
    MarketSourceHealthResponse,
    QuantCandidatesRequest,
    QuantCandidatesResponse,
    QuantDatasetRequest,
    QuantDatasetResponse,
)
from app.services.arena_service import arena_service
from app.services.historical_data_service import historical_data_service
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
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> QuantCandidatesResponse:
    try:
        return quant_service.generate_candidates(
            db=db,
            symbols=payload.symbols,
            limit=payload.limit,
            prefer_realtime=payload.prefer_realtime,
            lookback_days=payload.lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/quant/dataset", response_model=QuantDatasetResponse)
def build_quant_dataset(
    payload: QuantDatasetRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> QuantDatasetResponse:
    try:
        return quant_service.build_dataset(
            db,
            symbols=payload.symbols,
            limit=payload.limit,
            prefer_realtime=payload.prefer_realtime,
            lookback_days=payload.lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/market/daily/refresh", response_model=DailyRefreshResponse)
def refresh_daily_bars(
    payload: DailyRefreshRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> DailyRefreshResponse:
    try:
        return historical_data_service.refresh_daily_bars(
            db,
            trade_date=payload.trade_date,
            symbols=payload.symbols,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/quant/backtest", response_model=BacktestResponse)
def run_quant_backtest(
    payload: BacktestRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> BacktestResponse:
    try:
        return historical_data_service.run_daily_momentum_backtest(
            db,
            symbols=payload.symbols,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_cash=payload.initial_cash,
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


@router.get("/arena/leaderboard", response_model=ArenaLeaderboardResponse)
def get_arena_leaderboard(
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaLeaderboardResponse:
    return arena_service.leaderboard(db)


@router.get("/arena/agents", response_model=ArenaAgentsResponse)
def list_arena_agents(
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentsResponse:
    return arena_service.list_agents(db)


@router.put("/arena/agents", response_model=ArenaAgentsResponse)
def replace_arena_agents(
    payload: ArenaAgentsUpdateRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentsResponse:
    try:
        return arena_service.replace_agents(
            db,
            agents=[agent.model_dump() for agent in payload.agents],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
