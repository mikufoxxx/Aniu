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
            <span>开始日期</span>
            <input v-model="backtestStartDate" type="text" placeholder="20260526" />
          </label>
          <label class="arena-field">
            <span>结束日期</span>
            <input v-model="backtestEndDate" type="text" placeholder="20260528" />
          </label>
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
import { computed, onMounted, ref } from 'vue'
import { api } from '@/services/api'
import type { ArenaAgentConfig, ArenaLeaderboardPayload, ArenaRunPayload, BacktestPayload, DailyRangeRefreshPayload, MarketSourceHealthPayload, QuantCandidate, QuantDatasetPayload } from '@/types'

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
const agentSaving = ref(false)
const errorMessage = ref('')
const agents = ref<ArenaAgentConfig[]>(defaultAgents.map((agent) => ({ ...agent })))
const sourceHealth = ref<MarketSourceHealthPayload | null>(null)
const candidates = ref<QuantCandidate[]>([])
const quantDataset = ref<QuantDatasetPayload | null>(null)
const arenaResult = ref<ArenaRunPayload | null>(null)
const arenaLeaderboard = ref<ArenaLeaderboardPayload | null>(null)
const dailyRefreshResult = ref<DailyRangeRefreshPayload | null>(null)
const backtestResult = ref<BacktestPayload | null>(null)
const refreshStartDate = ref('20260526')
const refreshEndDate = ref('20260528')
const backtestStartDate = ref('20260526')
const backtestEndDate = ref('20260528')
const refreshFullMarket = ref(true)

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

async function loadSources(): Promise<void> {
  sourceHealth.value = await api.getMarketSourceHealth()
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
      symbols: parseSymbols(),
      limit: 10,
      prefer_realtime: true,
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
      symbols: parseSymbols(),
      limit: 20,
      prefer_realtime: true,
      lookback_days: 20,
    })
    quantDataset.value = payload
    candidates.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '量化数据集构建失败。'
  } finally {
    loading.value = false
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
      symbols: refreshFullMarket.value ? undefined : parseSymbols(),
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '日线刷新失败。'
  } finally {
    historyLoading.value = false
  }
}

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

function formatPercent(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return `${(value * 100).toFixed(2)}%`
}

function formatSignedPercent(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

onMounted(async () => {
  try {
    await Promise.all([loadSources(), loadAgents(), loadCandidates(), loadLeaderboard()])
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
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
  .arena-dataset-summary,
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
