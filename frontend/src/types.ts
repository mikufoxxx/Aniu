export interface ForecastAIConfig {
  base_url: string
  api_key_configured: boolean
  api_key_masked: string
  models: string[]
  model_count: number
}

export interface AppSettings {
  id: number
  provider_name: string
  mx_api_key: string | null
  tushare_token: string | null
  tushare_api_url: string | null
  llm_base_url: string | null
  llm_api_key: string | null
  llm_model: string
  llm_provider_configs: Record<string, Record<string, unknown>>
  automation_context_window_tokens: number | null
  arena_initial_cash: number
  system_prompt: string
  forecast_ai_config: ForecastAIConfig
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
    recent_momentum_pct?: number
    ma20?: number | null
    ma60?: number | null
    above_ma20?: boolean
    above_ma60?: boolean
    distance_to_ma20_pct?: number
    distance_to_ma60_pct?: number
    high_60?: number | null
    low_60?: number | null
    range_position_pct?: number
    max_drawdown_pct?: number
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
  retail_analysis?: Record<string, unknown>
  ai_selection?: Record<string, unknown>
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
  selection_plan: Record<string, unknown>
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

export interface PriceSeriesPoint {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  source?: string | null
  timestamp?: string | null
  is_realtime?: boolean
  ma5?: number | null
  ma20?: number | null
  rsi14?: number | null
  macd?: number | null
  macd_signal?: number | null
  macd_hist?: number | null
}

export interface TradeMarker {
  action: string
  symbol: string
  trade_date?: string | null
  price: number
  quantity: number
}

export interface EquityCurvePoint {
  trade_date: string
  value: number
  return_ratio: number
}

export interface DrawdownCurvePoint {
  trade_date: string
  drawdown: number
}

export interface FactorRadarPoint {
  name: string
  key: string
  value: number
}

export interface StrategyCharts {
  price_series: PriceSeriesPoint[]
  interval_series: Record<'daily' | 'weekly' | 'monthly' | 'hourly', PriceSeriesPoint[]>
  forecast_series: ForecastPoint[]
  trade_markers: TradeMarker[]
  equity_curve: EquityCurvePoint[]
  drawdown_curve: DrawdownCurvePoint[]
  risk_metrics: Record<string, number>
  return_distribution: DistributionBucket[]
}

export interface AlphaCurvePoint {
  trade_date: string
  strategy_return: number
  benchmark_return: number
  alpha: number
}

export interface ForecastPoint {
  trade_date: string
  price: number
  upper?: number | null
  lower?: number | null
  source?: string
}

export interface StockAnalysisCharts {
  price_series: PriceSeriesPoint[]
  interval_series: Record<'daily' | 'weekly' | 'monthly' | 'hourly', PriceSeriesPoint[]>
  forecast_series: ForecastPoint[]
  signal_markers: TradeMarker[]
  factor_radar: FactorRadarPoint[]
  support_resistance: {
    support?: number | null
    resistance?: number | null
    last_close?: number | null
  }
  return_distribution: DistributionBucket[]
  volume_profile: VolumeProfileBucket[]
}

export interface OrderCharts {
  price_series: PriceSeriesPoint[]
  interval_series: Record<'daily' | 'weekly' | 'monthly' | 'hourly', PriceSeriesPoint[]>
  forecast_series: ForecastPoint[]
  trade_markers: TradeMarker[]
}

export interface AIForecastCard {
  model: string
  status: 'live_ai' | 'quant_fallback' | 'model_error_fallback' | string
  direction: 'bullish' | 'neutral' | 'bearish' | string
  direction_label: string
  confidence: number
  target_price: number
  stop_loss: number
  support: number
  resistance: number
  buy_zone: number[]
  sell_zone: number[]
  key_points: string[]
  risk_points: string[]
  analysis: string
}

export interface ArenaOrderForecastPayload {
  order_id: number
  symbol: string
  name: string
  action: string
  technical_context: Record<string, unknown>
  quantitative_summary: string
  ai_config: {
    base_url?: string
    api_key_configured?: boolean
    api_key_masked?: string
    models?: string[]
    model_count?: number
  }
  methodology: string[]
  model_forecasts: AIForecastCard[]
  generated_at: string
}

export interface DistributionBucket {
  low: number
  high: number
  count: number
  unit: string
}

export interface VolumeProfileBucket {
  price_low: number
  price_high: number
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

export interface QuantResearchStrategy {
  strategy_name: string
  display_name: string
  selected_symbols: string[]
  final_assets: number
  return_ratio: number
  max_drawdown: number
  trade_count: number
  score: number
  reason: string
  metrics: Record<string, unknown>
  trades: BacktestTrade[]
  charts: StrategyCharts
}

export interface QuantResearchPayload {
  symbol_count: number
  bar_count: number
  start_date: string
  end_date: string
  initial_cash: number
  best_strategy: QuantResearchStrategy
  strategies: QuantResearchStrategy[]
  comparison_chart: Array<{
    strategy_name: string
    display_name: string
    return_ratio: number
    max_drawdown: number
    score: number
  }>
  strategy_equity_curves: Array<{
    strategy_name: string
    display_name: string
    points: EquityCurvePoint[]
  }>
  benchmark_curve: EquityCurvePoint[]
  alpha_curve: AlphaCurvePoint[]
  ai_learning_context: string
}

export interface ArenaAgentConfig {
  id: string
  name: string
  style: 'auto' | 'momentum' | 'balanced' | 'risk_control'
  provider?: string
  model?: string
  enabled?: boolean
  prompt?: string
}

export interface ArenaAgentsPayload {
  agents: ArenaAgentConfig[]
}

export type ArenaAIConfigPayload = ForecastAIConfig

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
  charts?: OrderCharts | null
}

export interface ArenaAgentRecommendation {
  agent_id: string
  agent_name: string
  style: string
  action: string
  playbook?: {
    mode: string
    label: string
    holding_period: string
  }
  picks?: Array<{
    symbol: string
    name: string
    score?: number
    ai_selection_score?: number
    risk_flags?: string[]
    price?: number | null
    change_pct?: number | null
    reason?: string
  }>
  symbol: string
  name: string
  score?: number
  price?: number | null
  reason: string
  run_id?: number
  created_at?: string | null
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
  candidate_pool_scope: Record<string, unknown>
  agent_candidate_pools: Array<{
    agent_id: string
    selection_mode: string
    snapshot_id: string
    candidate_count: number
    symbols: string[]
  }>
  stock_pick_snapshot?: AIStockPicksPayload | null
}

export interface ArenaAgentDashboardPayload {
  agent: ArenaAgentConfig
  summary: Record<string, unknown> & {
    playbook?: {
      mode: string
      label: string
      holding_period: string
    }
    charts?: {
      action_distribution?: Array<{ name: string; value: number }>
      symbol_exposure?: Array<{
        symbol: string
        name: string
        market_value: number
        unrealized_pnl: number
      }>
    }
  }
  morning: { recommendations: ArenaAgentRecommendation[] }
  intraday: { orders: ArenaOrder[] }
  closing: { reviews: ArenaAgentMemory[] }
  learning: { reviews: ArenaAgentMemory[] }
}

export interface StockAnalysisPayload {
  symbol: string
  name: string
  price: number
  score: number
  action: 'BUY' | 'HOLD' | 'SELL'
  rating: string
  reason: string
  decision: Record<string, unknown>
  retail_analysis: Record<string, unknown>
  llm_decision: Record<string, unknown>
  analysis_config: {
    ai_config?: ForecastAIConfig
    selected_model?: string
    selected_skill?: {
      id: string
      name: string
      description?: string
      source?: string
      run_types?: string[]
    } | null
    selected_skills?: Array<{
      id: string
      name: string
      description?: string
      source?: string
      run_types?: string[]
    }>
  }
  analysis_report: StockAnalysisReportPayload
  data_sources: string[]
  context: string
  stock_pick_snapshot?: AIStockPicksPayload | null
  charts: StockAnalysisCharts
}

export interface StockAnalysisReportPayload {
  id: number
  symbol: string
  name: string
  title: string
  model: string
  action: string
  rating: string
  summary: string
  selected_skills: Array<{
    id: string
    name: string
    description?: string
    source?: string
    run_types?: string[]
  }>
  sections: Array<{
    id: string
    title: string
    content: string
    items?: Array<{
      label: string
      value: string
      skill_id?: string
    }>
  }>
  source_snapshot: Record<string, unknown>
  created_at?: string | null
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

export interface SkillInfo extends SkillListItem {
  location: string
}
