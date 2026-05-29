from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _mask_key(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 8:
        return "****" + value[-2:] if len(value) > 2 else "****"
    return value[:3] + "****" + value[-4:]


def _mask_provider_configs(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    masked: dict[str, Any] = {}
    for provider, config in value.items():
        if not isinstance(config, dict):
            continue
        provider_config = dict(config)
        api_key = provider_config.get("api_key")
        if isinstance(api_key, str):
            provider_config["api_key"] = _mask_key(api_key)
        masked[str(provider)] = provider_config
    return masked


class AppSettingsBase(BaseModel):
    provider_name: str = "openai-compatible"
    mx_api_key: str | None = Field(default=None, max_length=512)
    llm_base_url: str | None = Field(default=None, max_length=512)
    llm_api_key: str | None = Field(default=None, max_length=512)
    llm_model: str = Field(default="gpt-4o-mini", max_length=128)
    llm_provider_configs: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = Field(max_length=20000)
    automation_session_id: int | None = None
    automation_context_window_tokens: int | None = Field(default=128000, ge=4096)
    automation_recent_message_limit: int = Field(default=24, ge=4, le=200)
    automation_enable_auto_compaction: bool = True
    automation_idle_summary_hours: int = Field(default=12, ge=1, le=168)


class AppSettingsRead(AppSettingsBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def mask_sensitive_fields(self) -> "AppSettingsRead":
        self.mx_api_key = _mask_key(self.mx_api_key)
        self.llm_api_key = _mask_key(self.llm_api_key)
        self.llm_provider_configs = _mask_provider_configs(self.llm_provider_configs)
        return self


class AppSettingsUpdate(AppSettingsBase):
    pass


class SkillListItemRead(BaseModel):
    id: str
    name: str
    description: str
    source: Literal["builtin", "workspace"]
    role: Literal["runtime", "standard"]
    enabled: bool
    can_disable: bool
    can_delete: bool
    always_enabled: bool


class SkillInfoRead(SkillListItemRead):
    location: str
    has_handler: bool
    tool_names: list[str] = Field(default_factory=list)
    run_types: list[str] = Field(default_factory=list)
    category: str | None = None
    compatibility_level: Literal["native", "prompt_only", "needs_attention"]
    compatibility_summary: str
    issues: list[str] = Field(default_factory=list)
    support_files: list[str] = Field(default_factory=list)
    clawhub_slug: str | None = None
    clawhub_version: str | None = None
    clawhub_url: str | None = None
    published_at: datetime | None = None


class SkillImportClawHubRequest(BaseModel):
    slug_or_url: str = Field(min_length=1, max_length=512)


class SkillImportSkillHubRequest(BaseModel):
    slug_or_url: str = Field(min_length=1, max_length=512)


class ScheduleBase(BaseModel):
    name: str = Field(default="默认任务", max_length=64)
    run_type: Literal["analysis", "trade"] = "analysis"
    cron_expression: str = Field(default="*/30 * * * *", min_length=5, max_length=64)
    task_prompt: str = Field(
        default="请根据当前市场和持仓情况生成交易决策。", max_length=20000
    )
    timeout_seconds: int = Field(default=1800, ge=5, le=3600)
    enabled: bool = False


class ScheduleRead(ScheduleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    retry_count: int = 0
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    retry_after_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScheduleUpdate(ScheduleBase):
    id: int | None = None


class TradeOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    action: str
    quantity: int
    price_type: str
    price: float | None = None
    status: str
    response_payload: dict[str, Any] | None = None
    created_at: datetime


class ApiDetailRead(BaseModel):
    tool_name: str
    name: str
    summary: str
    preview_index: int | None = None
    tool_call_id: str | None = None
    status: Literal["running", "done", "failed"] | None = None
    ok: bool | None = None


class RawToolPreviewRead(BaseModel):
    preview_index: int
    tool_name: str
    display_name: str
    summary: str
    preview: str
    truncated: bool = False


class RawToolPreviewDetailRead(RawToolPreviewRead):
    full_preview: str


class TradeDetailRead(BaseModel):
    action: Literal["buy", "sell"]
    action_text: str
    symbol: str
    name: str
    volume: int
    price: float | None = None
    amount: float | None = None
    summary: str
    tool_name: str | None = None
    preview_index: int | None = None
    status: Literal["running", "done", "failed"] | None = None
    ok: bool | None = None


class RunSummaryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger_source: str
    run_type: str
    schedule_id: int | None = None
    schedule_name: str | None = None
    chat_session_id: int | None = None
    prompt_message_id: int | None = None
    response_message_id: int | None = None
    context_summary_version: int | None = None
    context_tokens_estimate: int | None = None
    status: str
    analysis_summary: str | None = None
    error_message: str | None = None
    api_call_count: int = 0
    executed_trade_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RunDetailRead(RunSummaryRead):
    model_config = ConfigDict(from_attributes=True)

    final_answer: str | None = None
    output_markdown: str | None = None
    api_details: list[ApiDetailRead] = Field(default_factory=list)
    raw_tool_previews: list[RawToolPreviewRead] = Field(default_factory=list)
    trade_details: list[TradeDetailRead] = Field(default_factory=list)
    decision_payload: dict[str, Any] | None = None
    executed_actions: list[dict[str, Any]] | None = None
    llm_request_payload: dict[str, Any] | None = None
    llm_response_payload: dict[str, Any] | None = None
    skill_payloads: dict[str, Any] | None = None
    trade_orders: list[TradeOrderRead] = Field(default_factory=list)


class RunSummaryPageRead(BaseModel):
    items: list[RunSummaryRead] = Field(default_factory=list)
    next_before_id: int | None = None
    has_more: bool = False


class PositionOverviewRead(BaseModel):
    name: str
    symbol: str
    amount: float
    volume: int | None = None
    available_volume: int | None = None
    day_profit: float | None = None
    day_profit_ratio: float | None = None
    profit: float | None = None
    profit_ratio: float | None = None
    profit_text: str
    current_price: float | None = None
    cost_price: float | None = None
    position_ratio: float | None = None


class OrderOverviewRead(BaseModel):
    order_id: str
    order_time: str | None = None
    name: str
    symbol: str
    side: str
    side_text: str
    status: str
    status_text: str
    order_price: float | None = None
    order_quantity: int | None = None
    filled_price: float | None = None
    filled_quantity: int | None = None


class TradeSummaryRead(BaseModel):
    name: str
    symbol: str
    volume: int
    buy_amount: float
    sell_amount: float
    buy_price: float | None = None
    sell_price: float | None = None
    profit: float
    profit_ratio: float | None = None
    opened_at: str | None = None
    closed_at: str | None = None


class AccountOverviewRead(BaseModel):
    open_date: str | None = None
    daily_profit_trade_date: str | None = None
    operating_days: int | None = None
    initial_capital: float | None = None
    total_assets: float | None = None
    total_market_value: float | None = None
    cash_balance: float | None = None
    total_position_ratio: float | None = None
    holding_profit: float | None = None
    total_return_ratio: float | None = None
    nav: float | None = None
    daily_profit: float | None = None
    daily_return_ratio: float | None = None
    positions: list[PositionOverviewRead] = Field(default_factory=list)
    orders: list[OrderOverviewRead] = Field(default_factory=list)
    trade_summaries: list[TradeSummaryRead] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AccountOverviewDebugRead(AccountOverviewRead):
    raw_balance: dict[str, Any] | None = None
    raw_positions: dict[str, Any] | None = None
    raw_orders: dict[str, Any] | None = None


class MarketSourceHealthRead(BaseModel):
    id: str
    name: str
    tier: Literal["daily", "low_frequency", "quasi_high_frequency", "supplemental"]
    status: str
    cadence: str
    risk: str


class MarketSourceHealthResponse(BaseModel):
    sources: list[MarketSourceHealthRead]
    recommended_usage: dict[str, str]


class MarketDataCoverageDateRead(BaseModel):
    trade_date: str
    symbol_count: int
    row_count: int


class MarketDataCoverageSourceRead(BaseModel):
    source: str
    row_count: int


class MarketDataCoverageRefreshSuggestionRead(BaseModel):
    needed: bool
    start_date: str | None = None
    end_date: str | None = None
    reason: str


class MarketDataCoverageResponse(BaseModel):
    total_rows: int
    unique_symbols: int
    first_trade_date: str | None = None
    latest_trade_date: str | None = None
    latest_trade_date_symbols: int
    most_complete_trade_date: str | None = None
    most_complete_trade_date_symbols: int
    recent_trade_dates: list[MarketDataCoverageDateRead] = Field(default_factory=list)
    source_counts: list[MarketDataCoverageSourceRead] = Field(default_factory=list)
    refresh_suggestion: MarketDataCoverageRefreshSuggestionRead
    readiness: dict[str, bool]


class QuantCandidatesRequest(BaseModel):
    symbols: list[str] | None = None
    limit: int = Field(default=200, ge=1, le=200)
    prefer_realtime: bool = True
    lookback_days: int = Field(default=120, ge=1, le=120)


class QuantDatasetRequest(QuantCandidatesRequest):
    pass


class QuantCandidateRead(BaseModel):
    symbol: str
    name: str
    price: float | None = None
    change_pct: float
    amount: float
    turnover: float
    volume_ratio: float
    source: str | None = None
    timestamp: str | None = None
    score: float
    factor_scores: dict[str, float]
    daily_factors: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class QuantCandidatesResponse(BaseModel):
    universe_size: int
    candidate_count: int
    data_sources: list[str]
    candidates: list[QuantCandidateRead]


class QuantDatasetResponse(BaseModel):
    universe_size: int
    item_count: int
    lookback_days: int
    data_sources: list[str]
    coverage: dict[str, int]
    items: list[QuantCandidateRead]


class AIMarketContextRequest(BaseModel):
    symbols: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=50)
    lookback_days: int = Field(default=120, ge=1, le=120)


class AIMarketContextResponse(BaseModel):
    context: str
    context_length: int


class AIStockPicksRequest(QuantDatasetRequest):
    pass


class AIStockPicksResponse(BaseModel):
    snapshot_id: str
    selection_mode: Literal["auto_universe", "custom_symbols"]
    data_sources: list[str]
    coverage: dict[str, Any]
    dataset: QuantDatasetResponse
    recommendations: list[QuantCandidateRead]
    context: str
    context_length: int


class MarketReportRequest(BaseModel):
    report_type: Literal["morning", "closing"]
    symbols: list[str] | None = None
    limit: int = Field(default=50, ge=1, le=50)
    lookback_days: int = Field(default=120, ge=1, le=120)


class MarketReportRecommendationRead(BaseModel):
    symbol: str
    name: str
    action: str
    score: float
    price: float | None = None
    change_pct: float | None = None
    daily_momentum_pct: float
    reason: str


class MarketReportRead(BaseModel):
    id: int
    report_type: str
    title: str
    symbols: list[str] = Field(default_factory=list)
    lookback_days: int
    data_sources: list[str] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    recommendations: list[MarketReportRecommendationRead] = Field(default_factory=list)
    dataset: dict[str, Any] = Field(default_factory=dict)
    context: str
    summary: str
    created_at: datetime


class MarketReportListResponse(BaseModel):
    items: list[MarketReportRead] = Field(default_factory=list)


class MarketReportPerformanceItemRead(BaseModel):
    symbol: str
    name: str
    action: str
    score: float
    entry_date: str | None = None
    entry_close: float | None = None
    evaluation_date: str | None = None
    evaluation_close: float | None = None
    return_pct: float | None = None
    status: str


class MarketReportPerformanceResponse(BaseModel):
    report_id: int
    report_type: str
    title: str
    horizon_days: int
    evaluated_count: int
    pending_count: int
    average_return_pct: float | None = None
    best_return_pct: float | None = None
    worst_return_pct: float | None = None
    items: list[MarketReportPerformanceItemRead] = Field(default_factory=list)


class DailyRefreshRequest(BaseModel):
    trade_date: str = Field(min_length=8, max_length=10)
    symbols: list[str] | None = None


class DailyRefreshResponse(BaseModel):
    trade_date: str
    source: str
    stored_count: int
    skipped_count: int = 0
    requested_symbols: list[str] = Field(default_factory=list)
    error: str | None = None


class DailyRangeRefreshRequest(BaseModel):
    start_date: str = Field(min_length=8, max_length=10)
    end_date: str = Field(min_length=8, max_length=10)
    symbols: list[str] | None = None


class DailyRangeRefreshResponse(BaseModel):
    start_date: str
    end_date: str
    source: str
    processed_days: int
    stored_count: int
    skipped_count: int = 0
    unique_symbols: int
    requested_symbols: list[str] = Field(default_factory=list)
    daily_results: list[DailyRefreshResponse] = Field(default_factory=list)


class MarketDataMaintenanceRunRequest(BaseModel):
    end_date: str | None = Field(default=None, min_length=8, max_length=10)
    lookback_days: int = Field(default=120, ge=1, le=120)
    symbols: list[str] | None = None
    dataset_limit: int = Field(default=500, ge=1, le=500)
    report_type: Literal["morning", "closing"] | None = None


class MarketDataMaintenanceRunResponse(BaseModel):
    status: str
    refresh: DailyRangeRefreshResponse
    dataset: QuantDatasetResponse
    report: MarketReportRead | None = None


class MarketDataMaintenanceJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    submitted_at: datetime
    completed_at: datetime | None = None
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class MarketDataMaintenanceRunRead(BaseModel):
    id: int
    status: str
    refresh_start_date: str | None = None
    refresh_end_date: str | None = None
    processed_days: int
    stored_count: int
    skipped_count: int
    refresh_unique_symbols: int
    dataset_universe_size: int
    dataset_item_count: int
    latest_trade_date: str | None = None
    latest_trade_date_symbols: int
    most_complete_trade_date: str | None = None
    most_complete_trade_date_symbols: int
    refresh_needed: bool
    refresh_reason: str
    coverage: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MarketDataMaintenanceRunListResponse(BaseModel):
    items: list[MarketDataMaintenanceRunRead] = Field(default_factory=list)


class BacktestRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)
    start_date: str = Field(min_length=8, max_length=10)
    end_date: str = Field(min_length=8, max_length=10)
    initial_cash: float = Field(default=200000.0, ge=10000, le=100000000)


class BacktestTradeRead(BaseModel):
    action: str
    symbol: str
    trade_date: str
    price: float
    quantity: int
    amount: float


class BacktestResponse(BaseModel):
    run_id: int
    strategy_name: str
    selected_symbol: str
    start_date: str
    end_date: str
    initial_cash: float
    final_assets: float
    return_ratio: float
    max_drawdown: float
    trade_count: int
    metrics: dict[str, Any]
    trades: list[BacktestTradeRead]


class ArenaAgentRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    style: Literal["momentum", "balanced", "risk_control"] = "balanced"
    provider: str = Field(default="openai-compatible", max_length=64)
    model: str = Field(default="", max_length=128)
    enabled: bool = True
    prompt: str = Field(default="", max_length=2000)


class ArenaRunRequest(BaseModel):
    symbols: list[str] | None = None
    agents: list[ArenaAgentRequest] | None = None
    initial_cash: float = Field(default=200000.0, ge=10000, le=100000000)


class ArenaAgentsUpdateRequest(BaseModel):
    agents: list[ArenaAgentRequest] = Field(min_length=1, max_length=20)


class ArenaAgentsResponse(BaseModel):
    agents: list[ArenaAgentRequest] = Field(default_factory=list)


class ArenaOrderRead(BaseModel):
    id: int
    agent_id: str
    agent_name: str
    style: str
    action: str
    symbol: str
    name: str
    quantity: int
    price: float
    amount: float
    remaining_cash: float
    reason: str
    decision_context: dict[str, Any] = Field(default_factory=dict)


class ArenaPositionRead(BaseModel):
    symbol: str
    name: str
    quantity: int
    avg_cost: float
    last_price: float
    market_value: float
    unrealized_pnl: float


class ArenaLeaderboardItemRead(BaseModel):
    agent_id: str
    agent_name: str
    style: str
    cash: float
    position_value: float
    total_assets: float
    return_ratio: float
    order_count: int
    realized_pnl: float = 0.0
    positions: list[ArenaPositionRead] = Field(default_factory=list)


class ArenaLeaderboardResponse(BaseModel):
    items: list[ArenaLeaderboardItemRead] = Field(default_factory=list)


class ArenaRunResponse(BaseModel):
    run_id: int
    candidate_count: int
    candidates: list[QuantCandidateRead]
    leaderboard: list[ArenaLeaderboardItemRead]
    orders: list[ArenaOrderRead]
    data_sources: list[str]
    stock_pick_snapshot: AIStockPicksResponse | None = None


class ChatAttachmentRef(BaseModel):
    """Reference to a previously uploaded attachment.

    Accepted in chat requests; the backend resolves metadata from DB.
    """

    id: int = Field(ge=1)


class ChatAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    mime_type: str
    size: int
    url: str | None = None


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(default="", max_length=50000)
    tool_calls: list[dict[str, Any]] | None = None
    attachments: list[ChatAttachmentRead] | None = None


class ChatRequest(BaseModel):
    """Legacy stateless chat request (kept for backward compatibility)."""

    messages: list[ChatMessage] = Field(default_factory=list, min_length=1)


class ChatResponse(BaseModel):
    message: ChatMessage
    context: dict[str, bool]


class ChatStreamRequest(BaseModel):
    session_id: int = Field(ge=1)
    content: str = Field(default="", max_length=50000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_non_empty_message(self) -> "ChatStreamRequest":
        if self.content.strip() or self.attachment_ids:
            return self
        raise ValueError("content 和 attachment_ids 至少要提供一项。")


class ChatSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    kind: str = "user"
    slug: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0


class ChatSessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    attachments: list[ChatAttachmentRead] | None = None
    created_at: datetime


class ChatSessionMessagesPageRead(BaseModel):
    session: ChatSessionRead
    messages: list[ChatMessageRead] = Field(default_factory=list)
    next_before_id: int | None = None
    has_more: bool = False


class PersistentSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    kind: str = "automation"
    slug: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0
    archived_summary: str | None = None
    summary_revision: int = 0
    last_compacted_message_id: int | None = None
    last_compacted_run_id: int | None = None


class PersistentSessionMessagesPageRead(BaseModel):
    session: PersistentSessionRead
    messages: list[ChatMessageRead] = Field(default_factory=list)
    next_before_id: int | None = None
    has_more: bool = False


class LoginRequest(BaseModel):
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    authenticated: bool
    token: str | None = None
