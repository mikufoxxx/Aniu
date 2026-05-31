export interface AppSettings {
  id: number
  provider_name: string
  mx_api_key: string | null
  llm_base_url: string | null
  llm_api_key: string | null
  llm_model: string
  llm_provider_configs: Record<string, Record<string, unknown>>
  automation_context_window_tokens: number | null
  system_prompt: string
  created_at: string
  updated_at: string
}

export interface ScheduleConfig {
  id: number
  name: string
  run_type: 'analysis' | 'trade'
  cron_expression: string
  task_prompt: string
  timeout_seconds: number
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
  updated_at: string
}

export interface RunSummary {
  id: number
  trigger_source: string
  run_type: string
  schedule_name: string | null
  status: string
  analysis_summary: string | null
  error_message: string | null
  api_call_count: number
  executed_trade_count: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  started_at: string
  finished_at: string | null
}

export interface RunDetail extends RunSummary {
  final_answer: string | null
  output_markdown: string | null
  api_details: ApiDetail[]
  raw_tool_previews: RawToolPreview[]
  trade_details: TradeDetail[]
  decision_payload: Record<string, unknown> | null
  executed_actions: Array<Record<string, unknown>> | null
  llm_request_payload: Record<string, unknown> | null
  llm_response_payload: Record<string, unknown> | null
  skill_payloads: Record<string, unknown> | null
  trade_orders: TradeOrder[]
}

export interface ApiDetail {
  tool_name: string
  name: string
  summary: string
  preview_index: number | null
  tool_call_id?: string | null
  status?: 'running' | 'done' | 'failed' | null
  ok?: boolean | null
  stream_key?: string | null
}

export interface RawToolPreview {
  preview_index: number
  tool_name: string
  display_name: string
  summary: string
  preview: string
  truncated: boolean
}

export interface RawToolPreviewDetail extends RawToolPreview {
  full_preview: string
}

export interface TradeDetail {
  action: 'buy' | 'sell'
  action_text: string
  symbol: string
  name: string
  volume: number
  price: number | null
  amount: number | null
  summary: string
  tool_name: string | null
  preview_index: number | null
  status?: 'running' | 'done' | 'failed' | null
  ok?: boolean | null
  stream_key?: string | null
}

export interface RunSummaryPage {
  items: RunSummary[]
  next_before_id: number | null
  has_more: boolean
}

export interface TradeOrder {
  id: number
  symbol: string
  action: string
  quantity: number
  price_type: string
  price: number | null
  status: string
  response_payload: Record<string, unknown> | null
  created_at: string
}

export interface PositionOverview {
  name: string
  symbol: string
  amount: number
  volume: number | null
  available_volume: number | null
  day_profit: number | null
  day_profit_ratio: number | null
  profit: number | null
  profit_ratio: number | null
  profit_text: string
  current_price: number | null
  cost_price: number | null
  position_ratio: number | null
}

export interface OrderOverview {
  order_id: string
  order_time: string | null
  name: string
  symbol: string
  side: string
  side_text: string
  status: string
  status_text: string
  order_price: number | null
  order_quantity: number | null
  filled_price: number | null
  filled_quantity: number | null
}

export interface TradeSummary {
  name: string
  symbol: string
  volume: number
  buy_amount: number
  sell_amount: number
  buy_price: number | null
  sell_price: number | null
  profit: number
  profit_ratio: number | null
  opened_at: string | null
  closed_at: string | null
}

export interface AccountOverview {
  open_date: string | null
  daily_profit_trade_date: string | null
  operating_days: number | null
  initial_capital: number | null
  total_assets: number | null
  total_market_value: number | null
  cash_balance: number | null
  total_position_ratio: number | null
  holding_profit: number | null
  total_return_ratio: number | null
  nav: number | null
  daily_profit: number | null
  daily_return_ratio: number | null
  positions: PositionOverview[]
  orders: OrderOverview[]
  trade_summaries: TradeSummary[]
  errors: string[]
}

export interface MarketSourceHealth {
  id: string
  name: string
  tier: 'daily' | 'low_frequency' | 'quasi_high_frequency' | 'supplemental'
  status: string
  cadence: string
  risk: string
}

export interface MarketSourceHealthPayload {
  sources: MarketSourceHealth[]
  recommended_usage: Record<string, string>
}

export interface MarketDataCoverageDate {
  trade_date: string
  symbol_count: number
  row_count: number
}

export interface MarketDataCoverageSource {
  source: string
  row_count: number
}

export interface MarketDataCoveragePayload {
  total_rows: number
  unique_symbols: number
  first_trade_date: string | null
  latest_trade_date: string | null
  latest_trade_date_symbols: number
  most_complete_trade_date: string | null
  most_complete_trade_date_symbols: number
  recent_trade_dates: MarketDataCoverageDate[]
  source_counts: MarketDataCoverageSource[]
  refresh_suggestion: {
    needed: boolean
    start_date: string | null
    end_date: string | null
    reason: string
  }
  readiness: Record<string, boolean>
}

export interface QuantCandidate {
  symbol: string
  name: string
  price: number | null
  change_pct: number
  amount: number
  turnover: number
  volume_ratio: number
  source: string | null
  timestamp: string | null
  score: number
  factor_scores: Record<string, number>
  profile: {
    name?: string | null
    area?: string | null
    industry?: string | null
    market?: string | null
    exchange?: string | null
    list_status?: string | null
    list_date?: string | null
    is_hs?: string | null
  }
  financial_factors: {
    ann_date?: string | null
    end_date?: string | null
    roe?: number
    roe_dt?: number
    grossprofit_margin?: number
    netprofit_margin?: number
    netprofit_yoy?: number
    or_yoy?: number
    debt_to_assets?: number
    assets_turn?: number
    current_ratio?: number
  }
  daily_factors: {
    latest_trade_date?: string | null
    bars_used?: number
    latest_close?: number | null
    momentum_pct?: number
    avg_amount?: number
    volatility_pct?: number
    limit_event?: {
      trade_date?: string | null
      limit_type?: string | null
      name?: string | null
      industry?: string | null
      pct_chg?: number
      open_times?: number
      up_stat?: string | null
      limit_times?: number
      fd_amount?: number
      turnover_ratio?: number
    } | null
    margin_detail?: {
      trade_date?: string | null
      name?: string | null
      rzye?: number
      rqye?: number
      rzmre?: number
      rqyl?: number
      rzche?: number
      rqchl?: number
      rqmcl?: number
      rzrqye?: number
      net_financing_buy?: number
    } | null
    dragon_tiger?: {
      trade_date?: string | null
      reason?: string | null
      net_amount?: number
      l_buy?: number
      l_sell?: number
      net_rate?: number
      amount_rate?: number
      institution_net_buy?: number
      institution_count?: number
    } | null
    block_trade?: {
      trade_date?: string | null
      trade_count?: number
      total_amount?: number
      total_vol?: number
      avg_price?: number
      price_vs_close_pct?: number
      top_buyer?: string | null
      top_seller?: string | null
    } | null
    shareholder_number?: {
      ann_date?: string | null
      end_date?: string | null
      holder_num?: number
      previous_holder_num?: number
      holder_num_change_pct?: number
    } | null
    shareholder_trade?: {
      ann_date?: string | null
      trade_count?: number
      increase_count?: number
      decrease_count?: number
      net_change_vol?: number
      net_change_ratio?: number
      top_holder?: string | null
    } | null
    pledge_stat?: {
      end_date?: string | null
      pledge_count?: number
      unrest_pledge?: number
      rest_pledge?: number
      total_share?: number
      pledge_ratio?: number
    } | null
  }
  rationale: string
}

export interface QuantCandidatesPayload {
  universe_size: number
  candidate_count: number
  data_sources: string[]
  candidates: QuantCandidate[]
}

export interface QuantDatasetPayload {
  universe_size: number
  item_count: number
  lookback_days: number
  data_sources: string[]
  coverage: Record<string, number>
  items: QuantCandidate[]
}

export interface MarketDataMaintenancePayload {
  status: string
  profile_refresh?: {
    source: string
    stored_count: number
    error?: string | null
  } | null
  financial_refresh?: {
    source: string
    stored_count: number
    error?: string | null
    requested_symbols?: string[]
  } | null
  refresh: DailyRangeRefreshPayload
  dataset: QuantDatasetPayload
  report?: MarketReport
}

export interface MarketDataMaintenanceRunRecord {
  id: number
  status: string
  refresh_start_date: string | null
  refresh_end_date: string | null
  processed_days: number
  stored_count: number
  skipped_count: number
  refresh_unique_symbols: number
  dataset_universe_size: number
  dataset_item_count: number
  latest_trade_date: string | null
  latest_trade_date_symbols: number
  most_complete_trade_date: string | null
  most_complete_trade_date_symbols: number
  refresh_needed: boolean
  refresh_reason: string
  coverage: Record<string, unknown>
  result: Record<string, unknown>
  created_at: string
}

export interface MarketDataMaintenanceRunListPayload {
  items: MarketDataMaintenanceRunRecord[]
}

export interface MarketDataMaintenanceJobPayload {
  job_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  submitted_at: string
  completed_at?: string | null
  progress?: {
    phase?: string
    total_days?: number
    processed_days?: number
    current_trade_date?: string
    stored_count?: number
    skipped_count?: number
    error_count?: number
    data_source_counts?: Record<string, number>
    data_source_error_count?: number
  } | null
  result?: MarketDataMaintenancePayload | null
  error?: string | null
}

export interface AIMarketContextPayload {
  context: string
  context_length: number
}

export interface AIStockPicksPayload {
  snapshot_id: string
  selection_mode: 'auto_universe' | 'custom_symbols'
  data_sources: string[]
  coverage: Record<string, unknown>
  dataset: QuantDatasetPayload
  recommendations: QuantCandidate[]
  context: string
  context_length: number
}

export interface MarketReportRecommendation {
  symbol: string
  name: string
  action: string
  score: number
  price: number | null
  change_pct: number | null
  daily_momentum_pct: number
  reason: string
}

export interface MarketReport {
  id: number
  report_type: string
  title: string
  symbols: string[]
  lookback_days: number
  data_sources: string[]
  coverage: Record<string, number>
  recommendations: MarketReportRecommendation[]
  dataset: QuantDatasetPayload
  context: string
  summary: string
  created_at: string
}

export interface MarketReportPerformanceItem {
  symbol: string
  name: string
  action: string
  score: number
  entry_date: string | null
  entry_close: number | null
  evaluation_date: string | null
  evaluation_close: number | null
  return_pct: number | null
  status: string
}

export interface MarketReportPerformancePayload {
  report_id: number
  report_type: string
  title: string
  horizon_days: number
  evaluated_count: number
  pending_count: number
  average_return_pct: number | null
  best_return_pct: number | null
  worst_return_pct: number | null
  items: MarketReportPerformanceItem[]
}

export interface MarketReportListPayload {
  items: MarketReport[]
}

export interface DailyRefreshPayload {
  trade_date: string
  source: string
  stored_count: number
  skipped_count: number
  daily_basic_count?: number
  daily_basic_error?: string | null
  moneyflow_count?: number
  moneyflow_error?: string | null
  index_count?: number
  index_error?: string | null
  sector_count?: number
  sector_error?: string | null
  sector_member_count?: number
  sector_member_error?: string | null
  requested_symbols: string[]
}

export interface DailyRangeRefreshPayload {
  start_date: string
  end_date: string
  source: string
  processed_days: number
  stored_count: number
  skipped_count: number
  unique_symbols: number
  requested_symbols: string[]
  data_source_counts: Record<string, number>
  data_source_errors: Record<string, string[]>
  daily_results: DailyRefreshPayload[]
}

export interface BacktestTrade {
  action: string
  symbol: string
  trade_date: string
  price: number
  quantity: number
  amount: number
}

export interface BacktestPayload {
  run_id: number
  strategy_name: string
  selected_symbol: string
  start_date: string
  end_date: string
  initial_cash: number
  final_assets: number
  return_ratio: number
  max_drawdown: number
  trade_count: number
  metrics: Record<string, unknown>
  trades: BacktestTrade[]
}

export interface ArenaAgentConfig {
  id: string
  name: string
  style: 'momentum' | 'balanced' | 'risk_control'
  provider?: string
  model?: string
  enabled?: boolean
  prompt?: string
}

export interface ArenaAgentsPayload {
  agents: ArenaAgentConfig[]
}

export interface ArenaOrder {
  id: number
  agent_id: string
  agent_name: string
  style: string
  action: string
  symbol: string
  name: string
  quantity: number
  price: number
  amount: number
  remaining_cash: number
  reason: string
  decision_context?: Record<string, unknown>
  decision_started_at?: string | null
  decision_generated_at?: string | null
  decision_recorded_at?: string | null
  decision_latency_ms?: number
  record_latency_ms?: number
}

export interface ArenaAgentRecommendation {
  agent_id: string
  agent_name: string
  style: string
  action: string
  symbol: string
  name: string
  score?: number
  price?: number | null
  reason: string
  decision_context?: Record<string, unknown>
}

export interface ArenaAgentMemory {
  id: number
  agent_id: string
  agent_name: string
  style: string
  memory_type: string
  summary: string
  metrics: Record<string, unknown>
  created_at?: string | null
}

export interface ArenaLeaderboardItem {
  agent_id: string
  agent_name: string
  style: string
  cash: number
  position_value: number
  total_assets: number
  return_ratio: number
  order_count: number
  realized_pnl: number
  positions?: Array<{
    symbol: string
    name: string
    quantity: number
    avg_cost: number
    last_price: number
    market_value: number
    unrealized_pnl: number
  }>
}

export interface ArenaLeaderboardPayload {
  items: ArenaLeaderboardItem[]
}

export interface ArenaRunPayload {
  run_id: number
  phase: string
  candidate_count: number
  candidates: QuantCandidate[]
  agent_recommendations: ArenaAgentRecommendation[]
  agent_reviews: ArenaAgentMemory[]
  leaderboard: ArenaLeaderboardItem[]
  orders: ArenaOrder[]
  data_sources: string[]
  stock_pick_snapshot?: AIStockPicksPayload | null
}

export interface ChatToolCall {
  tool_call_id?: string | null
  tool_name: string
  status: 'running' | 'done'
  ok?: boolean
  summary?: string
  arguments?: unknown
  started_at: number
  finished_at?: number
}

export interface ChatAttachment {
  id: number
  filename: string
  mime_type: string
  size: number
  url: string
}

export interface ChatMessage {
  id?: number
  role: 'user' | 'assistant' | 'system'
  content: string
  tool_calls?: ChatToolCall[]
  attachments?: ChatAttachment[]
  created_at?: string
  pending?: boolean
}

export interface ChatRequest {
  messages: ChatMessage[]
}

export interface ChatResponse {
  message: ChatMessage
  context: Record<string, boolean>
}

export interface ChatSession {
  id: number
  title: string
  kind?: string
  slug?: string | null
  created_at: string
  updated_at: string
  last_message_at: string | null
  message_count: number
}

export interface ChatSessionMessagesPayload {
  session: ChatSession
  messages: ChatMessage[]
  next_before_id: number | null
  has_more: boolean
}

export interface PersistentSession {
  id: number
  title: string
  kind: string
  slug: string | null
  created_at: string
  updated_at: string
  last_message_at: string | null
  message_count: number
  archived_summary: string | null
  summary_revision: number
  last_compacted_message_id: number | null
  last_compacted_run_id: number | null
}

export interface PersistentSessionMessagesPayload {
  session: PersistentSession
  messages: ChatMessage[]
  next_before_id: number | null
  has_more: boolean
}

export interface ChatStreamRequest {
  session_id: number
  content: string
  attachment_ids?: number[]
}

export interface LoginRequest {
  password: string
}

export interface LoginResponse {
  authenticated: boolean
  token: string | null
}

export type SkillCompatibilityLevel = 'native' | 'prompt_only' | 'needs_attention'
export type SkillRole = 'runtime' | 'standard'

export interface SkillListItem {
  id: string
  name: string
  description: string
  source: 'builtin' | 'workspace'
  role: SkillRole
  enabled: boolean
  can_disable: boolean
  can_delete: boolean
  always_enabled: boolean
}

export interface SkillInfo {
  id: string
  name: string
  description: string
  location: string
  source: 'builtin' | 'workspace'
  role: SkillRole
  enabled: boolean
  can_disable: boolean
  can_delete: boolean
  always_enabled: boolean
  has_handler: boolean
  tool_names: string[]
  run_types: string[]
  category: string | null
  compatibility_level: SkillCompatibilityLevel
  compatibility_summary: string
  issues: string[]
  support_files: string[]
  clawhub_slug: string | null
  clawhub_version: string | null
  clawhub_url: string | null
  published_at: string | null
}
