<template>
  <div class="tab-content arena-page">
    <section class="content-grid content-grid-primary arena-grid">
      <section class="panel arena-control-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>AI竞技场</h2>
            <p class="section-kicker">Arena</p>
          </div>
          <div class="panel-head-actions">
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="agentSaving"
              @click="saveAgents"
            >
              {{ agentSaving ? '保存中…' : '保存选手' }}
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :class="{ 'is-loading': loading }"
              :disabled="loading"
              @click="runArena"
            >
              {{ loading ? '运行中…' : '运行一轮' }}
            </button>
          </div>
        </div>

        <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

        <div class="arena-form-grid">
          <label class="arena-field">
            <span>股票池</span>
            <textarea v-model="symbolsText" rows="5"></textarea>
          </label>
          <label class="arena-field">
            <span>初始资金</span>
            <input v-model.number="initialCash" type="number" min="10000" step="10000" />
          </label>
        </div>

        <div class="arena-agent-grid">
          <div v-for="agent in agents" :key="agent.id" class="arena-agent-card">
            <label>
              <span>ID</span>
              <input v-model="agent.id" type="text" />
            </label>
            <label>
              <span>名称</span>
              <input v-model="agent.name" type="text" />
            </label>
            <label>
              <span>风格</span>
              <select v-model="agent.style">
                <option value="momentum">动量型</option>
                <option value="balanced">均衡型</option>
                <option value="risk_control">风控型</option>
              </select>
            </label>
            <label>
              <span>模型</span>
              <input v-model="agent.model" type="text" placeholder="deepseek-chat" />
            </label>
            <label class="arena-inline-check">
              <input v-model="agent.enabled" type="checkbox" />
              <b>启用</b>
            </label>
          </div>
        </div>
      </section>

      <section class="panel arena-source-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>数据源状态</h2>
            <p class="section-kicker">Sources</p>
          </div>
        </div>
        <div class="arena-source-list">
          <div v-for="source in sourceHealth?.sources" :key="source.id" class="arena-source-row">
            <div>
              <strong>{{ source.name }}</strong>
              <span>{{ tierText(source.tier) }}</span>
            </div>
            <b :class="sourceStatusClass(source.status)">{{ source.status }}</b>
            <p>{{ source.cadence }}</p>
          </div>
        </div>
      </section>

      <section class="panel arena-history-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>历史数据与回测</h2>
            <p class="section-kicker">Backtest</p>
          </div>
          <div class="panel-head-actions">
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="historyLoading"
              @click="refreshDaily"
            >
              {{ historyLoading ? '刷新中…' : '刷新日线' }}
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="historyLoading"
              @click="runMaintenance"
            >
              {{ historyLoading ? '维护中…' : '数据维护' }}
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="historyLoading"
              @click="runBacktest"
            >
              {{ historyLoading ? '回测中…' : '运行回测' }}
            </button>
          </div>
        </div>

        <div class="arena-history-grid">
          <label class="arena-field arena-checkbox-field">
            <span>刷新范围</span>
            <label class="arena-inline-check">
              <input v-model="refreshFullMarket" type="checkbox" />
              <b>全市场最大化刷新</b>
            </label>
          </label>
          <label class="arena-field">
            <span>刷新开始</span>
            <input v-model="refreshStartDate" type="text" placeholder="20260526" />
          </label>
          <label class="arena-field">
            <span>刷新结束</span>
            <input v-model="refreshEndDate" type="text" placeholder="20260528" />
          </label>
          <label class="arena-field">
            <span>维护回看</span>
            <input v-model.number="maintenanceLookbackDays" type="number" min="1" max="120" />
          </label>
          <label class="arena-field">
            <span>开始日期</span>
            <input v-model="backtestStartDate" type="text" placeholder="20260526" />
          </label>
          <label class="arena-field">
            <span>结束日期</span>
            <input v-model="backtestEndDate" type="text" placeholder="20260528" />
          </label>
        </div>

        <div v-if="dataCoverage" class="arena-coverage-summary">
          <div>
            <strong>{{ formatInteger(dataCoverage.total_rows) }}</strong>
            <span>日线行数</span>
          </div>
          <div>
            <strong>{{ formatInteger(dataCoverage.unique_symbols) }}</strong>
            <span>覆盖股票</span>
          </div>
          <div>
            <strong>{{ dataCoverage.latest_trade_date ?? '--' }}</strong>
            <span>最新日期 · {{ formatInteger(dataCoverage.latest_trade_date_symbols) }}只</span>
          </div>
          <div>
            <strong>{{ dataCoverage.most_complete_trade_date ?? '--' }}</strong>
            <span>最完整 · {{ formatInteger(dataCoverage.most_complete_trade_date_symbols) }}只</span>
          </div>
          <div>
            <strong :class="dataCoverage.readiness.backtest_ready ? 'profit-up' : 'profit-down'">
              {{ readinessText(dataCoverage.readiness.backtest_ready) }}
            </strong>
            <span>回测可用</span>
          </div>
          <div>
            <strong :class="dataCoverage.readiness.latest_day_complete ? 'profit-up' : 'profit-down'">
              {{ readinessText(dataCoverage.readiness.latest_day_complete) }}
            </strong>
            <span>最新日完整</span>
          </div>
        </div>

        <div v-if="dataCoverage?.recent_trade_dates.length" class="arena-coverage-days">
          <span v-for="item in dataCoverage.recent_trade_dates.slice(0, 5)" :key="item.trade_date">
            {{ item.trade_date }} · {{ formatInteger(item.symbol_count) }}只
          </span>
        </div>

        <div v-if="dailyRefreshResult" class="arena-history-result">
          <strong>日线入库</strong>
          <span>
            {{ dailyRefreshResult.start_date }}-{{ dailyRefreshResult.end_date }} ·
            {{ dailyRefreshResult.processed_days }} 天 ·
            {{ dailyRefreshResult.stored_count }} 条 ·
            {{ dailyRefreshResult.unique_symbols }} 只 ·
            {{ dailyRefreshResult.source }}
          </span>
        </div>

        <div v-if="maintenanceJob" class="arena-history-result">
          <strong>维护任务</strong>
          <span>
            {{ maintenanceJob.status }} ·
            <template v-if="maintenanceJob.progress">
              {{ progressText(maintenanceJob.progress.phase) }} ·
              {{ maintenanceJob.progress.processed_days ?? 0 }}/{{ maintenanceJob.progress.total_days ?? 0 }} 天 ·
              当前 {{ maintenanceJob.progress.current_trade_date ?? '--' }} ·
              入库 {{ maintenanceJob.progress.stored_count ?? 0 }} ·
              跳过 {{ maintenanceJob.progress.skipped_count ?? 0 }} ·
              错误 {{ maintenanceJob.progress.error_count ?? 0 }} ·
            </template>
            {{ formatDateTime(maintenanceJob.submitted_at) }}
            <template v-if="maintenanceJob.completed_at">
              - {{ formatDateTime(maintenanceJob.completed_at) }}
            </template>
            <template v-if="maintenanceJob.error">
              · {{ maintenanceJob.error }}
            </template>
          </span>
        </div>

        <div v-if="backtestResult" class="arena-history-result">
          <strong>回测结果</strong>
          <span>
            {{ backtestResult.selected_symbol }} ·
            总资产 {{ formatAmount(backtestResult.final_assets) }} ·
            收益率 {{ formatPercent(backtestResult.return_ratio) }} ·
            最大回撤 {{ formatPercent(backtestResult.max_drawdown) }}
          </span>
        </div>
      </section>

      <section class="panel arena-candidates-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>AI候选池</h2>
            <p class="section-kicker">Candidates</p>
          </div>
          <div class="panel-head-actions">
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="loading"
              @click="loadCandidates"
            >
              刷新候选
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="loading"
              @click="loadQuantDataset"
            >
              构建数据集
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="loading"
              @click="loadAIMarketContext"
            >
              AI上下文
            </button>
          </div>
        </div>
        <div v-if="quantDataset" class="arena-dataset-summary">
          <div>
            <strong>{{ quantDataset.item_count }}</strong>
            <span>标的</span>
          </div>
          <div>
            <strong>{{ quantDataset.coverage.realtime_symbols ?? 0 }}</strong>
            <span>实时覆盖</span>
          </div>
          <div>
            <strong>{{ quantDataset.coverage.daily_history_symbols ?? 0 }}</strong>
            <span>日线覆盖</span>
          </div>
          <div>
            <strong>{{ quantDataset.lookback_days }}</strong>
            <span>回看日</span>
          </div>
        </div>
        <div v-if="aiMarketContext" class="arena-ai-context">
          <strong>AI 输入上下文 · {{ aiMarketContext.context_length }} 字</strong>
          <pre>{{ aiMarketContext.context }}</pre>
        </div>
        <div class="positions-surface" v-if="candidates.length">
          <div class="arena-table-row arena-table-head">
            <div>名称 / 代码</div>
            <div>评分</div>
            <div>价格 / 涨幅</div>
            <div>日线信号</div>
            <div>成交额</div>
            <div>来源</div>
          </div>
          <div v-for="item in candidates" :key="item.symbol" class="arena-table-row">
            <div class="position-cell-main">
              <strong class="position-name">{{ item.name || item.symbol }}</strong>
              <span class="position-symbol">{{ item.symbol }}</span>
            </div>
            <div>{{ item.score.toFixed(2) }}</div>
            <div>
              <span>{{ formatPrice(item.price) }}</span>
              <span class="metric-sub" :class="profitClass(item.change_pct)">
                {{ item.change_pct.toFixed(2) }}%
              </span>
            </div>
            <div>
              <span :class="profitClass(item.daily_factors?.momentum_pct)">
                {{ formatSignedPercent(item.daily_factors?.momentum_pct) }}
              </span>
              <span class="metric-sub">{{ item.daily_factors?.bars_used ?? 0 }}日</span>
            </div>
            <div>{{ formatAmount(item.amount) }}</div>
            <div>{{ item.source || '--' }}</div>
          </div>
        </div>
        <div v-else class="empty-state">
          <p>暂无候选池，点击刷新候选或运行一轮。</p>
        </div>
      </section>

      <section class="panel arena-reports-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>早晚报</h2>
            <p class="section-kicker">Reports</p>
          </div>
          <div class="panel-head-actions">
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="reportLoading"
              @click="generateReport('morning')"
            >
              {{ reportLoading ? '生成中…' : '早盘推荐' }}
            </button>
            <button
              class="button ghost small soft-header-button overview-refresh-button"
              :disabled="reportLoading"
              @click="generateReport('closing')"
            >
              {{ reportLoading ? '生成中…' : '收盘分析' }}
            </button>
          </div>
        </div>
        <div class="arena-report-list" v-if="marketReports.length">
          <article v-for="report in marketReports" :key="report.id" class="arena-report-card">
            <div class="arena-report-head">
              <strong>{{ report.title }}</strong>
              <span>{{ formatDateTime(report.created_at) }}</span>
            </div>
            <p>{{ report.summary }}</p>
            <div class="arena-report-meta">
              <span>实时 {{ report.coverage.realtime_symbols ?? 0 }}</span>
              <span>日线 {{ report.coverage.daily_history_symbols ?? 0 }}</span>
              <span>{{ report.data_sources.join(', ') || '--' }}</span>
            </div>
            <div v-if="marketReportPerformance[report.id]" class="arena-report-performance">
              <span>1日评估 {{ marketReportPerformance[report.id].evaluated_count }}只</span>
              <span :class="profitClass(marketReportPerformance[report.id].average_return_pct)">
                均值 {{ formatSignedPercent(marketReportPerformance[report.id].average_return_pct) }}
              </span>
              <span :class="profitClass(marketReportPerformance[report.id].best_return_pct)">
                最好 {{ formatSignedPercent(marketReportPerformance[report.id].best_return_pct) }}
              </span>
              <span :class="profitClass(marketReportPerformance[report.id].worst_return_pct)">
                最差 {{ formatSignedPercent(marketReportPerformance[report.id].worst_return_pct) }}
              </span>
            </div>
            <div class="arena-report-picks">
              <span v-for="item in report.recommendations.slice(0, 3)" :key="`${report.id}-${item.symbol}`">
                {{ item.action }} {{ item.symbol }} {{ item.score.toFixed(1) }}
              </span>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">
          <p>暂无早晚报。</p>
        </div>
      </section>

      <section class="panel arena-leaderboard-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>排行榜</h2>
            <p class="section-kicker">Leaderboard</p>
          </div>
        </div>
        <div class="arena-rank-list" v-if="displayLeaderboard.length">
          <div v-for="rank in displayLeaderboard" :key="rank.agent_id" class="arena-rank-card">
            <div>
              <strong>{{ rank.agent_name }}</strong>
              <span>{{ styleText(rank.style) }}</span>
            </div>
            <b>{{ formatAmount(rank.total_assets) }}</b>
            <p>
              现金 {{ formatAmount(rank.cash) }} ·
              持仓 {{ formatAmount(rank.position_value) }} ·
              已实现 {{ formatAmount(rank.realized_pnl) }} ·
              订单 {{ rank.order_count }}
            </p>
          </div>
        </div>
        <div v-else class="empty-state">
          <p>尚未运行竞技场。</p>
        </div>
      </section>

      <section class="panel arena-orders-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>模拟交易</h2>
            <p class="section-kicker">Orders</p>
          </div>
        </div>
        <div class="positions-surface" v-if="arenaResult?.orders.length">
          <div class="arena-order-row arena-table-head">
            <div>AI</div>
            <div>标的</div>
            <div>数量 / 价格</div>
            <div>金额</div>
            <div>现金</div>
          </div>
          <div v-for="order in arenaResult.orders" :key="order.id" class="arena-order-row">
            <div>{{ order.agent_name }}</div>
            <div>{{ order.name }} {{ order.symbol }}</div>
            <div>{{ order.quantity }} / {{ formatPrice(order.price) }}</div>
            <div>{{ formatAmount(order.amount) }}</div>
            <div>{{ formatAmount(order.remaining_cash) }}</div>
          </div>
        </div>
        <div v-else class="empty-state">
          <p>暂无模拟交易记录。</p>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/services/api'
import type { AIMarketContextPayload, ArenaAgentConfig, ArenaLeaderboardPayload, ArenaRunPayload, BacktestPayload, DailyRangeRefreshPayload, MarketDataCoveragePayload, MarketDataMaintenanceJobPayload, MarketDataMaintenancePayload, MarketReport, MarketReportPerformancePayload, MarketSourceHealthPayload, QuantCandidate, QuantDatasetPayload } from '@/types'

const defaultSymbols = ['600519.SH', '000001.SZ', '300750.SZ', '601318.SH', '000858.SZ']
const defaultAgents: ArenaAgentConfig[] = [
  { id: 'momentum_ai', name: '动量 AI', style: 'momentum', provider: 'openai-compatible', model: '', enabled: true, prompt: '' },
  { id: 'balanced_ai', name: '均衡 AI', style: 'balanced', provider: 'openai-compatible', model: '', enabled: true, prompt: '' },
  { id: 'risk_ai', name: '风控 AI', style: 'risk_control', provider: 'openai-compatible', model: '', enabled: true, prompt: '' },
]

const symbolsText = ref(defaultSymbols.join('\n'))
const initialCash = ref(200000)
const loading = ref(false)
const historyLoading = ref(false)
const reportLoading = ref(false)
const agentSaving = ref(false)
const errorMessage = ref('')
const agents = ref<ArenaAgentConfig[]>(defaultAgents.map((agent) => ({ ...agent })))
const sourceHealth = ref<MarketSourceHealthPayload | null>(null)
const dataCoverage = ref<MarketDataCoveragePayload | null>(null)
const candidates = ref<QuantCandidate[]>([])
const quantDataset = ref<QuantDatasetPayload | null>(null)
const aiMarketContext = ref<AIMarketContextPayload | null>(null)
const arenaResult = ref<ArenaRunPayload | null>(null)
const arenaLeaderboard = ref<ArenaLeaderboardPayload | null>(null)
const dailyRefreshResult = ref<DailyRangeRefreshPayload | null>(null)
const maintenanceJob = ref<MarketDataMaintenanceJobPayload | null>(null)
const backtestResult = ref<BacktestPayload | null>(null)
const marketReports = ref<MarketReport[]>([])
const marketReportPerformance = ref<Record<number, MarketReportPerformancePayload>>({})
const refreshStartDate = ref('20260526')
const refreshEndDate = ref('20260528')
const maintenanceLookbackDays = ref(120)
const backtestStartDate = ref('20260526')
const backtestEndDate = ref('20260528')
const refreshFullMarket = ref(true)
let maintenancePollTimer: number | undefined

const displayLeaderboard = computed(() => {
  if (arenaLeaderboard.value?.items.length) return arenaLeaderboard.value.items
  return arenaResult.value?.leaderboard ?? []
})

function parseSymbols(): string[] {
  return symbolsText.value
    .split(/[\n,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function selectedSymbols(): string[] | undefined {
  return refreshFullMarket.value ? undefined : parseSymbols()
}

async function loadSources(): Promise<void> {
  sourceHealth.value = await api.getMarketSourceHealth()
}

async function loadDataCoverage(): Promise<void> {
  dataCoverage.value = await api.getMarketDataCoverage()
}

async function loadAgents(): Promise<void> {
  const payload = await api.getArenaAgents()
  agents.value = payload.agents.map((agent) => ({
    ...agent,
    provider: agent.provider ?? 'openai-compatible',
    model: agent.model ?? '',
    enabled: agent.enabled ?? true,
    prompt: agent.prompt ?? '',
  }))
}

async function saveAgents(): Promise<void> {
  agentSaving.value = true
  errorMessage.value = ''
  try {
    const payload = await api.updateArenaAgents({ agents: agents.value })
    agents.value = payload.agents
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 选手保存失败。'
  } finally {
    agentSaving.value = false
  }
}

async function loadLeaderboard(): Promise<void> {
  arenaLeaderboard.value = await api.getArenaLeaderboard()
}

async function loadCandidates(): Promise<void> {
  errorMessage.value = ''
  try {
    const payload = await api.generateQuantCandidates({
      symbols: selectedSymbols(),
      limit: 200,
      prefer_realtime: true,
      lookback_days: 120,
    })
    candidates.value = payload.candidates
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '候选池刷新失败。'
  }
}

async function loadQuantDataset(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const payload = await api.buildQuantDataset({
      symbols: selectedSymbols(),
      limit: 200,
      prefer_realtime: true,
      lookback_days: 120,
    })
    quantDataset.value = payload
    candidates.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '量化数据集构建失败。'
  } finally {
    loading.value = false
  }
}

async function loadAIMarketContext(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    aiMarketContext.value = await api.buildAIMarketContext({
      symbols: selectedSymbols(),
      limit: 50,
      lookback_days: 120,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 上下文构建失败。'
  } finally {
    loading.value = false
  }
}

async function loadMarketReports(): Promise<void> {
  const payload = await api.listMarketReports({ limit: 6 })
  marketReports.value = payload.items
  await Promise.allSettled(payload.items.map((report) => loadReportPerformance(report.id)))
}

async function loadReportPerformance(reportId: number): Promise<void> {
  const payload = await api.getMarketReportPerformance(reportId, { horizon_days: 1 })
  marketReportPerformance.value = {
    ...marketReportPerformance.value,
    [reportId]: payload,
  }
}

async function generateReport(reportType: 'morning' | 'closing'): Promise<void> {
  reportLoading.value = true
  errorMessage.value = ''
  try {
    const report = await api.generateMarketReport({
      report_type: reportType,
      symbols: selectedSymbols(),
      limit: 50,
      lookback_days: 120,
    })
    marketReports.value = [report, ...marketReports.value.filter((item) => item.id !== report.id)].slice(0, 6)
    await loadReportPerformance(report.id)
    quantDataset.value = report.dataset as QuantDatasetPayload
    candidates.value = (quantDataset.value.items ?? []) as QuantCandidate[]
    aiMarketContext.value = {
      context: report.context,
      context_length: report.context.length,
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告生成失败。'
  } finally {
    reportLoading.value = false
  }
}

async function runArena(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const payload = await api.runArena({
      symbols: parseSymbols(),
      initial_cash: initialCash.value,
    })
    arenaResult.value = payload
    candidates.value = payload.candidates
    await loadLeaderboard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '竞技场运行失败。'
  } finally {
    loading.value = false
  }
}

async function refreshDaily(): Promise<void> {
  historyLoading.value = true
  errorMessage.value = ''
  try {
    dailyRefreshResult.value = await api.refreshDailyRange({
      start_date: refreshStartDate.value,
      end_date: refreshEndDate.value,
      symbols: selectedSymbols(),
    })
    await loadDataCoverage()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日线刷新失败。'
  } finally {
    historyLoading.value = false
  }
}

async function runMaintenance(): Promise<void> {
  historyLoading.value = true
  errorMessage.value = ''
  try {
    const job = await api.startMarketDataMaintenanceJob({
      end_date: refreshEndDate.value,
      lookback_days: maintenanceLookbackDays.value,
      symbols: selectedSymbols(),
      dataset_limit: 500,
    })
    maintenanceJob.value = job
    await pollMaintenanceJob(job.job_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据维护失败。'
    historyLoading.value = false
  }
}

async function pollMaintenanceJob(jobId: string): Promise<void> {
  clearMaintenancePoll()
  maintenancePollTimer = window.setTimeout(async () => {
    try {
      const job = await api.getMarketDataMaintenanceJob(jobId)
      maintenanceJob.value = job
      if (job.status === 'queued' || job.status === 'running') {
        await pollMaintenanceJob(jobId)
        return
      }
      historyLoading.value = false
      if (job.status === 'failed') {
        errorMessage.value = job.error || '数据维护失败。'
        return
      }
      if (job.result) {
        await applyMaintenanceResult(job.result)
      }
    } catch (error) {
      historyLoading.value = false
      errorMessage.value = error instanceof Error ? error.message : '数据维护状态查询失败。'
    }
  }, 3000)
}

async function applyMaintenanceResult(payload: MarketDataMaintenancePayload): Promise<void> {
  dailyRefreshResult.value = payload.refresh
  quantDataset.value = payload.dataset
  candidates.value = payload.dataset.items
  await loadDataCoverage()
  if (payload.report) {
    marketReports.value = [
      payload.report,
      ...marketReports.value.filter((item) => item.id !== payload.report?.id),
    ].slice(0, 6)
    await loadReportPerformance(payload.report.id)
  }
}

function clearMaintenancePoll(): void {
  if (maintenancePollTimer !== undefined) {
    window.clearTimeout(maintenancePollTimer)
    maintenancePollTimer = undefined
  }
}

onUnmounted(() => {
  clearMaintenancePoll()
})

async function runBacktest(): Promise<void> {
  historyLoading.value = true
  errorMessage.value = ''
  try {
    backtestResult.value = await api.runBacktest({
      symbols: parseSymbols(),
      start_date: backtestStartDate.value,
      end_date: backtestEndDate.value,
      initial_cash: initialCash.value,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回测运行失败，请先刷新对应日期的日线数据。'
  } finally {
    historyLoading.value = false
  }
}

function tierText(tier: string): string {
  const mapping: Record<string, string> = {
    daily: '日频',
    low_frequency: '低频',
    quasi_high_frequency: '准高频',
    supplemental: '补充',
  }
  return mapping[tier] ?? tier
}

function progressText(phase: string | undefined): string {
  const mapping: Record<string, string> = {
    refreshing_daily: '刷新日线',
    building_dataset: '构建数据集',
    completed: '已完成',
  }
  return phase ? mapping[phase] ?? phase : '--'
}

function readinessText(value: boolean | undefined): string {
  return value ? '是' : '否'
}

function styleText(style: string): string {
  const mapping: Record<string, string> = {
    momentum: '动量型',
    balanced: '均衡型',
    risk_control: '风控型',
  }
  return mapping[style] ?? style
}

function sourceStatusClass(status: string): string {
  return status === 'available' ? 'profit-up' : 'profit-down'
}

function profitClass(value: number | null | undefined): string {
  if (!value) return ''
  return value > 0 ? 'profit-up' : 'profit-down'
}

function formatPrice(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return value.toFixed(value >= 100 ? 2 : 3)
}

function formatAmount(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  if (Math.abs(value) >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)}万`
  return value.toFixed(2)
}

function formatInteger(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return Math.round(value).toLocaleString('zh-CN')
}

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return `${(value * 100).toFixed(2)}%`
}

function formatSignedPercent(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

onMounted(async () => {
  try {
    await Promise.all([
      loadSources(),
      loadDataCoverage(),
      loadAgents(),
      loadCandidates(),
      loadLeaderboard(),
      loadMarketReports(),
    ])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '页面初始化失败。'
  }
})
</script>

<style scoped>
.arena-grid {
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
}

.arena-candidates-panel,
.arena-reports-panel,
.arena-orders-panel {
  grid-column: 1 / -1;
}

.arena-form-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px;
  gap: 14px;
}

.arena-history-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 12px;
}

.arena-checkbox-field {
  align-content: start;
}

.arena-inline-check {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 41px;
  padding: 0 10px;
  border: 1px solid rgba(145, 170, 214, 0.16);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.82);
  color: #eef4ff;
}

.arena-inline-check input {
  width: 16px;
  height: 16px;
}

.arena-inline-check b {
  font-size: 13px;
}

.arena-history-result {
  display: grid;
  gap: 4px;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
}

.arena-history-result strong {
  color: #f7fbff;
}

.arena-history-result span {
  color: #b7c8e3;
  font-size: 13px;
}

.arena-coverage-summary {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.arena-coverage-summary div {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  padding: 10px 12px;
}

.arena-coverage-summary strong {
  display: block;
  color: #f7fbff;
  font-size: 17px;
  line-height: 1.2;
}

.arena-coverage-summary strong.profit-up {
  color: #57d68d;
}

.arena-coverage-summary strong.profit-down {
  color: #ff7b7b;
}

.arena-coverage-summary span,
.arena-coverage-days span {
  color: #9cb2cf;
  font-size: 12px;
}

.arena-coverage-days {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.arena-coverage-days span {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.62);
  padding: 6px 8px;
}

.arena-dataset-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.arena-dataset-summary div {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  padding: 10px 12px;
}

.arena-dataset-summary strong {
  display: block;
  color: #f7fbff;
  font-size: 18px;
  line-height: 1.2;
}

.arena-dataset-summary span {
  color: #9cb2cf;
  font-size: 12px;
}

.arena-ai-context {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.7);
  padding: 12px;
}

.arena-ai-context strong {
  color: #f7fbff;
}

.arena-ai-context pre {
  max-height: 260px;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  color: #b7c8e3;
  font-size: 12px;
  line-height: 1.6;
}

.arena-report-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.arena-report-card {
  display: grid;
  gap: 8px;
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  padding: 12px;
}

.arena-report-head,
.arena-report-meta,
.arena-report-performance,
.arena-report-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.arena-report-head {
  justify-content: space-between;
}

.arena-report-card strong {
  color: #f7fbff;
}

.arena-report-card p,
.arena-report-card span {
  color: #9cb2cf;
  font-size: 13px;
  margin: 0;
}

.arena-report-picks span {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  padding: 4px 8px;
  color: #dbe7f8;
}

.arena-report-performance span {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  padding: 4px 8px;
}

.arena-field {
  display: grid;
  gap: 8px;
  color: #9cb2cf;
  font-size: 13px;
}

.arena-field textarea,
.arena-field input {
  width: 100%;
  border: 1px solid rgba(145, 170, 214, 0.16);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.82);
  color: #eef4ff;
  padding: 10px 12px;
}

.arena-agent-grid,
.arena-rank-list,
.arena-source-list {
  display: grid;
  gap: 10px;
}

.arena-agent-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 14px;
}

.arena-agent-card,
.arena-rank-card,
.arena-source-row {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  padding: 12px;
}

.arena-agent-card {
  display: grid;
  gap: 8px;
}

.arena-agent-card label {
  display: grid;
  gap: 4px;
  color: #9cb2cf;
  font-size: 12px;
}

.arena-agent-card input,
.arena-agent-card select {
  width: 100%;
  border: 1px solid rgba(145, 170, 214, 0.16);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.82);
  color: #eef4ff;
  padding: 8px 10px;
}

.arena-agent-card strong,
.arena-rank-card strong,
.arena-source-row strong {
  display: block;
  color: #f7fbff;
}

.arena-agent-card span,
.arena-rank-card span,
.arena-source-row span,
.arena-rank-card p,
.arena-source-row p {
  color: #9cb2cf;
  font-size: 13px;
  margin: 2px 0 0;
}

.arena-source-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 12px;
}

.arena-source-row p {
  grid-column: 1 / -1;
}

.arena-table-row,
.arena-order-row {
  display: grid;
  align-items: center;
  min-height: 58px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(145, 170, 214, 0.08);
  color: #dbe7f8;
}

.arena-table-row {
  grid-template-columns: 1.45fr 0.65fr 0.95fr 0.85fr 0.95fr 0.75fr;
}

.arena-order-row {
  grid-template-columns: 1fr 1.5fr 1fr 1fr 1fr;
}

.arena-table-head {
  min-height: 44px;
  color: #9cb2cf;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

@media (max-width: 900px) {
  .arena-grid,
  .arena-form-grid,
  .arena-history-grid,
  .arena-coverage-summary,
  .arena-dataset-summary,
  .arena-report-list,
  .arena-agent-grid {
    grid-template-columns: 1fr;
  }

  .arena-table-row,
  .arena-order-row {
    grid-template-columns: 1fr;
    gap: 4px;
    padding: 12px;
  }
}
</style>
