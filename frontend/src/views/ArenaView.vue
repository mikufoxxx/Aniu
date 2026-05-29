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
            <strong>{{ agent.name }}</strong>
            <span>{{ styleText(agent.style) }}</span>
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
          </div>
        </div>
        <div class="positions-surface" v-if="candidates.length">
          <div class="arena-table-row arena-table-head">
            <div>名称 / 代码</div>
            <div>评分</div>
            <div>价格 / 涨幅</div>
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
        <div class="arena-rank-list" v-if="arenaResult?.leaderboard.length">
          <div v-for="rank in arenaResult.leaderboard" :key="rank.agent_id" class="arena-rank-card">
            <div>
              <strong>{{ rank.agent_name }}</strong>
              <span>{{ styleText(rank.style) }}</span>
            </div>
            <b>{{ formatAmount(rank.total_assets) }}</b>
            <p>现金 {{ formatAmount(rank.cash) }} · 持仓 {{ formatAmount(rank.position_value) }}</p>
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
import { onMounted, ref } from 'vue'
import { api } from '@/services/api'
import type { ArenaAgentConfig, ArenaRunPayload, MarketSourceHealthPayload, QuantCandidate } from '@/types'

const defaultSymbols = ['600519.SH', '000001.SZ', '300750.SZ', '601318.SH', '000858.SZ']
const agents: ArenaAgentConfig[] = [
  { id: 'momentum_ai', name: '动量 AI', style: 'momentum' },
  { id: 'balanced_ai', name: '均衡 AI', style: 'balanced' },
  { id: 'risk_ai', name: '风控 AI', style: 'risk_control' },
]

const symbolsText = ref(defaultSymbols.join('\n'))
const initialCash = ref(200000)
const loading = ref(false)
const errorMessage = ref('')
const sourceHealth = ref<MarketSourceHealthPayload | null>(null)
const candidates = ref<QuantCandidate[]>([])
const arenaResult = ref<ArenaRunPayload | null>(null)

function parseSymbols(): string[] {
  return symbolsText.value
    .split(/[\n,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function loadSources(): Promise<void> {
  sourceHealth.value = await api.getMarketSourceHealth()
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

async function runArena(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const payload = await api.runArena({
      symbols: parseSymbols(),
      agents,
      initial_cash: initialCash.value,
    })
    arenaResult.value = payload
    candidates.value = payload.candidates
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '竞技场运行失败。'
  } finally {
    loading.value = false
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

onMounted(async () => {
  try {
    await Promise.all([loadSources(), loadCandidates()])
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
  grid-template-columns: 1.5fr 0.7fr 1fr 1fr 0.8fr;
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
