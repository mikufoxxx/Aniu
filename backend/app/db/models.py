from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.constants import DEFAULT_SYSTEM_PROMPT


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(32), default="openai-compatible")
    mx_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    llm_provider_configs: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    disabled_skill_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default=DEFAULT_SYSTEM_PROMPT,
    )
    analyst_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。"
            "当信号不明确时返回HOLD。"
        ),
    )
    market_query: Mapped[str] = mapped_column(
        String(255), default="上证指数今天走势和市场概况"
    )
    news_query: Mapped[str] = mapped_column(String(255), default="今天A股市场热点新闻")
    screener_query: Mapped[str] = mapped_column(
        String(255), default="A股今天值得关注的强势股"
    )
    max_actions: Mapped[int] = mapped_column(Integer, default=2)
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    automation_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_context_window_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=128000
    )
    automation_recent_message_limit: Mapped[int] = mapped_column(
        Integer, default=24
    )
    automation_enable_auto_compaction: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    automation_idle_summary_hours: Mapped[int] = mapped_column(Integer, default=12)
    automation_context_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="default"
    )
    automation_context_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategySchedule(Base):
    __tablename__ = "strategy_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="默认调度任务")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_after_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_source: Mapped[str] = mapped_column(String(32), default="manual")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    schedule_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    prompt_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_summary_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    llm_response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    skill_payloads: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    executed_actions: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trade_orders: Mapped[list["TradeOrder"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunEvent.sequence",
    )


class TradeOrder(Base):
    __tablename__ = "trade_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    price_type: Mapped[str] = mapped_column(String(16), default="MARKET")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[StrategyRun] = relationship(back_populates="trade_orders")


class DailyBar(Base):
    __tablename__ = "daily_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_daily_bars_symbol_trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_chg: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    pe_ttm: Mapped[float | None] = mapped_column(Float, nullable=True)
    pb: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_mv: Mapped[float | None] = mapped_column(Float, nullable=True)
    circ_mv: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_net_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_net_d5_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_lg_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_lg_amount_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_md_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_md_amount_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_sm_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    moneyflow_buy_sm_amount_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class IndexBar(Base):
    __tablename__ = "index_bars"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", name="uq_index_bars_symbol_trade_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[str] = mapped_column(String(8), index=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_chg: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="tushare_index")
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(64), default="daily_momentum")
    symbols_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    start_date: Mapped[str] = mapped_column(String(8), index=True)
    end_date: Mapped[str] = mapped_column(String(8), index=True)
    initial_cash: Mapped[float] = mapped_column(Float, default=200000.0)
    final_assets: Mapped[float] = mapped_column(Float, default=0.0)
    return_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    metrics_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    trades_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class MarketReport(Base):
    __tablename__ = "market_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(120))
    symbols_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    lookback_days: Mapped[int] = mapped_column(Integer, default=20)
    data_sources_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    coverage_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    recommendations_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    dataset_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    context_text: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class MarketDataMaintenanceRun(Base):
    __tablename__ = "market_data_maintenance_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    refresh_start_date: Mapped[str | None] = mapped_column(String(8), nullable=True)
    refresh_end_date: Mapped[str | None] = mapped_column(String(8), nullable=True)
    processed_days: Mapped[int] = mapped_column(Integer, default=0)
    stored_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    refresh_unique_symbols: Mapped[int] = mapped_column(Integer, default=0)
    dataset_universe_size: Mapped[int] = mapped_column(Integer, default=0)
    dataset_item_count: Mapped[int] = mapped_column(Integer, default=0)
    latest_trade_date: Mapped[str | None] = mapped_column(String(8), nullable=True)
    latest_trade_date_symbols: Mapped[int] = mapped_column(Integer, default=0)
    most_complete_trade_date: Mapped[str | None] = mapped_column(String(8), nullable=True)
    most_complete_trade_date_symbols: Mapped[int] = mapped_column(Integer, default=0)
    refresh_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_reason: Mapped[str] = mapped_column(String(120), default="")
    coverage_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class ArenaRun(Base):
    __tablename__ = "arena_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    initial_cash: Mapped[float] = mapped_column(Float, default=200000.0)
    universe_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    candidate_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    leaderboard_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    orders: Mapped[list["ArenaOrder"]] = relationship(
        back_populates="arena_run",
        cascade="all, delete-orphan",
        order_by="ArenaOrder.id",
    )


class ArenaAgentConfig(Base):
    __tablename__ = "arena_agent_configs"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_arena_agent_configs_agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(120))
    style: Mapped[str] = mapped_column(String(32), default="balanced")
    provider: Mapped[str] = mapped_column(String(64), default="openai-compatible")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ArenaAccount(Base):
    __tablename__ = "arena_accounts"
    __table_args__ = (
        UniqueConstraint("agent_id", name="uq_arena_accounts_agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(120))
    style: Mapped[str] = mapped_column(String(32), default="balanced")
    initial_cash: Mapped[float] = mapped_column(Float, default=200000.0)
    cash: Mapped[float] = mapped_column(Float, default=200000.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    positions: Mapped[list["ArenaPosition"]] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        order_by="ArenaPosition.symbol",
    )


class ArenaPosition(Base):
    __tablename__ = "arena_positions"
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", name="uq_arena_positions_account_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("arena_accounts.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    account: Mapped[ArenaAccount] = relationship(back_populates="positions")


class ArenaOrder(Base):
    __tablename__ = "arena_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    arena_run_id: Mapped[int] = mapped_column(
        ForeignKey("arena_runs.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(120))
    style: Mapped[str] = mapped_column(String(32), default="balanced")
    action: Mapped[str] = mapped_column(String(16), default="BUY")
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    remaining_cash: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    arena_run: Mapped[ArenaRun] = relationship(back_populates="orders")


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    state_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    run: Mapped[StrategyRun] = relationship(back_populates="events")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    kind: Mapped[str] = mapped_column(String(32), default="user", index=True)
    slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    archived_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_compacted_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    last_compacted_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    messages: Mapped[list["ChatMessageRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageRecord.id",
    )


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
