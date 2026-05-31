from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.database import get_db
from app.schemas.aniu import (
    AIMarketContextRequest,
    AIMarketContextResponse,
    AIStockPicksRequest,
    AIStockPicksResponse,
    ArenaAgentDashboardResponse,
    ArenaAgentMemoriesResponse,
    ArenaAgentRequest,
    ArenaRunRequest,
    ArenaRunResponse,
    ArenaLeaderboardResponse,
    ArenaAgentsResponse,
    ArenaAgentsUpdateRequest,
    BacktestRequest,
    BacktestResponse,
    DailyRangeRefreshRequest,
    DailyRangeRefreshResponse,
    DailyRefreshRequest,
    DailyRefreshResponse,
    MarketDataCoverageResponse,
    MarketDataMaintenanceJobResponse,
    MarketDataMaintenanceRunListResponse,
    MarketDataMaintenanceRunRequest,
    MarketDataMaintenanceRunResponse,
    MarketReportListResponse,
    MarketReportPerformanceResponse,
    MarketReportRead,
    MarketReportRequest,
    MarketSourceHealthResponse,
    QuantCandidatesRequest,
    QuantCandidatesResponse,
    QuantDatasetRequest,
    QuantDatasetResponse,
    StockAnalysisRequest,
    StockAnalysisResponse,
)
from app.services.ai_market_context_service import ai_market_context_service
from app.services.ai_stock_picker_service import ai_stock_picker_service
from app.services.arena_service import arena_service
from app.services.historical_data_service import historical_data_service
from app.services.market_data_maintenance_service import market_data_maintenance_service
from app.services.stock_analysis_service import stock_analysis_service
from app.services.market_data_service import market_data_service
from app.services.market_report_service import market_report_service
from app.services.quant_service import quant_service

router = APIRouter(tags=["aniu-market-quant-arena"])


@router.get("/market/sources/health", response_model=MarketSourceHealthResponse)
def get_market_source_health(
    _user: str = Depends(get_current_user),
) -> MarketSourceHealthResponse:
    return market_data_service.source_health()


@router.get("/market/data/coverage", response_model=MarketDataCoverageResponse)
def get_market_data_coverage(
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketDataCoverageResponse:
    return historical_data_service.summarize_daily_coverage(db)


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


@router.post("/market/ai-context", response_model=AIMarketContextResponse)
def build_ai_market_context(
    payload: AIMarketContextRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AIMarketContextResponse:
    try:
        context = ai_market_context_service.build_context(
            db,
            symbols=payload.symbols,
            limit=payload.limit,
            lookback_days=payload.lookback_days,
        )
        return {"context": context, "context_length": len(context)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/picks", response_model=AIStockPicksResponse)
def build_ai_stock_picks(
    payload: AIStockPicksRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AIStockPicksResponse:
    try:
        return ai_stock_picker_service.build_snapshot(
            db,
            symbols=payload.symbols,
            limit=payload.limit,
            prefer_realtime=payload.prefer_realtime,
            lookback_days=payload.lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/market/reports", response_model=MarketReportRead)
def generate_market_report(
    payload: MarketReportRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketReportRead:
    try:
        return market_report_service.generate_report(
            db,
            report_type=payload.report_type,
            symbols=payload.symbols,
            limit=payload.limit,
            lookback_days=payload.lookback_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market/reports", response_model=MarketReportListResponse)
def list_market_reports(
    report_type: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketReportListResponse:
    try:
        return market_report_service.list_reports(
            db,
            report_type=report_type,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market/reports/{report_id}/performance", response_model=MarketReportPerformanceResponse)
def get_market_report_performance(
    report_id: int,
    horizon_days: int = 1,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketReportPerformanceResponse:
    try:
        return market_report_service.evaluate_performance(
            db,
            report_id=report_id,
            horizon_days=horizon_days,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@router.post("/market/daily/refresh-range", response_model=DailyRangeRefreshResponse)
def refresh_daily_bar_range(
    payload: DailyRangeRefreshRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> DailyRangeRefreshResponse:
    try:
        return historical_data_service.refresh_daily_range(
            db,
            start_date=payload.start_date,
            end_date=payload.end_date,
            symbols=payload.symbols,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/market/maintenance/run", response_model=MarketDataMaintenanceRunResponse)
def run_market_data_maintenance(
    payload: MarketDataMaintenanceRunRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketDataMaintenanceRunResponse:
    try:
        return market_data_maintenance_service.run_now(
            db,
            end_date=payload.end_date,
            lookback_days=payload.lookback_days,
            symbols=payload.symbols,
            dataset_limit=payload.dataset_limit,
            report_type=payload.report_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/market/maintenance/jobs", response_model=MarketDataMaintenanceJobResponse)
def start_market_data_maintenance_job(
    payload: MarketDataMaintenanceRunRequest,
    _user: str = Depends(get_current_user),
) -> MarketDataMaintenanceJobResponse:
    try:
        return market_data_maintenance_service.start_job(
            end_date=payload.end_date,
            lookback_days=payload.lookback_days,
            symbols=payload.symbols,
            dataset_limit=payload.dataset_limit,
            report_type=payload.report_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/market/maintenance/runs", response_model=MarketDataMaintenanceRunListResponse)
def list_market_data_maintenance_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> MarketDataMaintenanceRunListResponse:
    return market_data_maintenance_service.list_runs(db, limit=limit)


@router.get("/market/maintenance/jobs/{job_id}", response_model=MarketDataMaintenanceJobResponse)
def get_market_data_maintenance_job(
    job_id: str,
    _user: str = Depends(get_current_user),
) -> MarketDataMaintenanceJobResponse:
    try:
        return market_data_maintenance_service.get_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


@router.post("/stocks/analyze", response_model=StockAnalysisResponse)
def analyze_stock(
    payload: StockAnalysisRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> StockAnalysisResponse:
    try:
        return stock_analysis_service.analyze(
            db,
            symbol=payload.symbol,
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
            phase=payload.phase,
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


@router.post("/arena/agents", response_model=ArenaAgentRequest)
def create_arena_agent(
    payload: ArenaAgentRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentRequest:
    try:
        return arena_service.upsert_agent(
            db,
            agent_id=payload.id,
            agent=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.get("/arena/agents/{agent_id}", response_model=ArenaAgentRequest)
def get_arena_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentRequest:
    agent = arena_service.get_agent(db, agent_id=agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="AI 选手不存在。")
    return agent


@router.get("/arena/agents/{agent_id}/dashboard", response_model=ArenaAgentDashboardResponse)
def get_arena_agent_dashboard(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentDashboardResponse:
    dashboard = arena_service.agent_dashboard(db, agent_id=agent_id)
    if dashboard is None:
        raise HTTPException(status_code=404, detail="AI 选手不存在。")
    return dashboard


@router.put("/arena/agents/{agent_id}", response_model=ArenaAgentRequest)
def update_arena_agent(
    agent_id: str,
    payload: ArenaAgentRequest,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentRequest:
    try:
        return arena_service.upsert_agent(
            db,
            agent_id=agent_id,
            agent=payload.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/arena/agents/{agent_id}", response_model=ArenaAgentsResponse)
def delete_arena_agent(
    agent_id: str,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentsResponse:
    result = arena_service.delete_agent(db, agent_id=agent_id)
    if result is None:
        raise HTTPException(status_code=404, detail="AI 选手不存在。")
    return result


@router.get("/arena/agents/{agent_id}/memories", response_model=ArenaAgentMemoriesResponse)
def list_arena_agent_memories(
    agent_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ArenaAgentMemoriesResponse:
    return {"memories": arena_service.recent_agent_memories(db, agent_id=agent_id, limit=limit)}
