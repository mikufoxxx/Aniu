<template>
  <div class="tab-content arena-page">
    <section class="panel arena-agent-detail-head">
      <div class="panel-head">
        <div class="head-main">
          <h2>{{ dashboard?.agent.name || agentId }}</h2>
          <p class="section-kicker">{{ dashboard?.summary.playbook?.label || 'AI Detail' }}</p>
        </div>
        <div class="panel-head-actions">
          <RouterLink class="button ghost small soft-header-button overview-refresh-button" to="/arena">
            <span class="material-symbols-rounded" aria-hidden="true">arrow_back</span>
            返回竞技场
          </RouterLink>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :class="{ 'is-loading': loading }"
            :disabled="loading"
            @click="loadDashboard()"
          >
            <span class="material-symbols-rounded" aria-hidden="true">sync</span>
            刷新
          </button>
        </div>
      </div>
      <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
      <div v-if="dashboard" class="arena-agent-detail-summary">
        <div>
          <span>总资产</span>
          <strong>{{ formatAmount(numberValue(dashboard.summary.total_assets)) }}</strong>
        </div>
        <div>
          <span>收益</span>
          <strong>{{ formatPercent(numberValue(dashboard.summary.return_ratio)) }}</strong>
        </div>
        <div>
          <span>订单</span>
          <strong>{{ numberValue(dashboard.summary.order_count).toFixed(0) }}</strong>
        </div>
        <div>
          <span>打法</span>
          <strong>{{ dashboard.summary.playbook?.holding_period || '--' }}</strong>
        </div>
      </div>
      <div v-if="dashboard" class="arena-allocation-panel">
        <div class="arena-allocation-head">
          <div>
            <strong>资产配置</strong>
            <span>按最新行情重估现金、持仓市值和仓位权重</span>
          </div>
          <span>{{ positionRows.length ? `${positionRows.length} 只持仓` : '当前空仓' }}</span>
        </div>
        <div class="arena-allocation-bars">
          <div v-for="item in allocationItems" :key="item.key">
            <div class="arena-allocation-line">
              <span>{{ item.label }}</span>
              <strong>{{ formatAmount(item.value) }}</strong>
              <small>{{ formatPercent(item.weight) }}</small>
            </div>
            <div class="arena-allocation-track">
              <span :style="{ width: `${Math.min(100, Math.max(0, item.weight * 100))}%` }"></span>
            </div>
          </div>
        </div>
        <div v-if="positionRows.length" class="arena-position-table">
          <div class="arena-position-row arena-position-head">
            <span>股票</span>
            <span>数量</span>
            <span>成本/现价</span>
            <span>市值</span>
            <span>仓位</span>
            <span>浮盈亏</span>
          </div>
          <div v-for="position in positionRows" :key="position.symbol" class="arena-position-row">
            <span>
              <strong>{{ position.name || position.symbol }}</strong>
              <small>{{ position.symbol }}</small>
            </span>
            <span>{{ position.quantity }}</span>
            <span>{{ formatPrice(position.avg_cost) }} / {{ formatPrice(position.last_price) }}</span>
            <span>{{ formatAmount(position.market_value) }}</span>
            <span>{{ formatPercent(position.weight) }}</span>
            <span :class="profitClass(position.unrealized_pnl)">{{ formatAmount(position.unrealized_pnl) }}</span>
          </div>
        </div>
        <div v-else class="arena-allocation-empty">当前无持仓，资产全部以现金形式保留。</div>
      </div>
      <div v-if="dashboard" class="arena-dashboard-charts">
        <BreakdownChart
          v-if="actionDistribution.length"
          title="交易动作分布"
          subtitle="按成交金额统计"
          :items="actionDistribution"
        />
        <BreakdownChart
          v-if="symbolExposure.length"
          title="持仓暴露"
          subtitle="按市值统计"
          :items="symbolExposure"
        />
      </div>
    </section>

    <section v-if="dashboard" class="arena-agent-detail-layout">
      <aside class="panel arena-agent-detail-sidebar">
        <button
          v-for="tab in sectionTabs"
          :key="tab.id"
          class="detail-nav-button"
          :class="{ active: activeSection === tab.id }"
          type="button"
          @click="activeSection = tab.id"
        >
          <span>{{ tab.title }}</span>
          <small>{{ tab.count }}</small>
        </button>
      </aside>
      <section class="panel arena-agent-detail-section">
        <div class="panel-head">
          <div class="head-main">
            <h2>{{ activeTab.title }}</h2>
            <p class="section-kicker">{{ activeTab.subtitle }}</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :disabled="runningPhase === activeTab.phase"
            @click="runPhase(activeTab.phase)"
          >
            <span class="material-symbols-rounded" aria-hidden="true">play_arrow</span>
            {{ runningPhase === activeTab.phase ? '运行中…' : activeTab.action }}
          </button>
        </div>

        <template v-if="activeSection === 'morning'">
          <div v-if="morningRecommendation" class="arena-agent-section-list">
            <article>
              <div class="arena-agent-section-head">
                <strong>{{ morningRecommendation.playbook?.label || '早盘精选' }}</strong>
                <span>{{ morningRecommendation.created_at || '--' }}</span>
              </div>
              <p>{{ morningRecommendation.reason }}</p>
              <div class="arena-agent-picks">
                <article v-for="pick in morningRecommendation.picks || []" :key="pick.symbol" class="arena-pick-card">
                  <strong>{{ pick.name }} {{ pick.symbol }}</strong>
                  <span>{{ formatPrice(pick.price) }} · {{ scoreText(pick.score) }} · {{ formatChange(pick.change_pct) }}</span>
                  <p>{{ pick.reason || firstText(pick.reasons) || '由量价、趋势、资金与风险项综合入选。' }}</p>
                  <small v-if="pick.prediction">
                    预测冻结 {{ predictionText(pick.prediction) }}
                  </small>
                </article>
              </div>
            </article>
          </div>
          <div v-else class="empty-state"><p>暂无早盘推荐。</p></div>
        </template>

        <template v-else-if="activeSection === 'intraday'">
          <div v-if="dashboard.intraday.orders.length" class="arena-agent-section-list">
            <article v-for="order in dashboard.intraday.orders" :key="order.id">
              <div class="arena-agent-section-head">
                <strong>{{ order.action }} {{ order.name }} {{ order.symbol }}</strong>
                <span>{{ order.quantity }} / {{ formatPrice(order.price) }}</span>
              </div>
              <p>{{ order.reason }}</p>
              <div class="arena-order-explain">
                <div>
                  <strong>成交信息</strong>
                  <span>{{ orderExecutionText(order) }}</span>
                </div>
                <div>
                  <strong>为什么{{ order.action === 'SELL' ? '卖' : '买' }}</strong>
                  <span>{{ orderDecisionReason(order) }}</span>
                </div>
                <div>
                  <strong>关键价位</strong>
                  <span>{{ orderPricePlan(order) }}</span>
                </div>
                <div>
                  <strong>纪律条件</strong>
                  <span>{{ orderRiskPlan(order) }}</span>
                </div>
                <div>
                  <strong>数据输入</strong>
                  <span>{{ orderDataSources(order) }}</span>
                </div>
                <div>
                  <strong>图表对照</strong>
                  <span>{{ orderChartSummary(order) }}</span>
                </div>
              </div>
              <MarketKlineChart
                v-if="order.charts"
                title="交易走势与买卖点"
                :subtitle="`${order.name} ${order.symbol}`"
                :price-series="order.charts.price_series"
                :interval-series="order.charts.interval_series"
                :forecast-series="order.charts.forecast_series"
                :markers="order.charts.trade_markers"
                :support-resistance="order.charts.support_resistance"
                :data-summary="order.charts.data_summary"
                :forecast-snapshot="order.charts.forecast_snapshot"
                :forecast-actual-comparison="order.charts.forecast_actual_comparison"
                :explanation-notes="order.charts.explanation_notes"
              />
            </article>
          </div>
          <div v-else class="empty-state"><p>暂无盘中模拟记录。</p></div>
        </template>

        <template v-else-if="activeSection === 'closing'">
          <div v-if="dashboard.closing.reviews.length" class="arena-agent-section-list">
            <article v-for="review in dashboard.closing.reviews" :key="review.id">
              <div class="arena-agent-section-head">
                <strong>收盘复盘</strong>
                <span>{{ review.created_at || '--' }}</span>
              </div>
              <p>{{ review.summary }}</p>
            </article>
          </div>
          <div v-else class="empty-state"><p>暂无收盘复盘。</p></div>
        </template>

        <template v-else>
          <div v-if="dashboard.learning.reviews.length" class="arena-agent-section-list">
            <article v-for="review in dashboard.learning.reviews" :key="review.id">
              <div class="arena-agent-section-head">
                <strong>回测学习</strong>
                <span>{{ review.created_at || '--' }}</span>
              </div>
              <p>{{ review.summary }}</p>
            </article>
          </div>
          <div v-else class="empty-state"><p>暂无学习记录。</p></div>
        </template>
      </section>
    </section>
    <div v-else-if="loading" class="empty-state">
      <p>正在整合最新行情、订单和图表…</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { ArenaAgentDashboardPayload, ArenaAgentRecommendation, ArenaOrder } from '@/types'
import BreakdownChart from '@/components/charts/BreakdownChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'

type ArenaPhase = 'morning_recommendation' | 'intraday_trade' | 'closing_review' | 'nightly_learning'
type DetailSection = 'morning' | 'intraday' | 'closing' | 'learning'

interface DetailTab {
  id: DetailSection
  title: string
  subtitle: string
  count: number
  phase: ArenaPhase
  action: string
}

interface DetailPosition {
  symbol: string
  name: string
  quantity: number
  avg_cost: number
  last_price: number
  market_value: number
  unrealized_pnl: number
  weight: number
}

const route = useRoute()
const router = useRouter()
const agentId = computed(() => String(route.params.agentId || ''))
const dashboard = ref<ArenaAgentDashboardPayload | null>(null)
const loading = ref(false)
const runningPhase = ref<ArenaPhase | null>(null)
const errorMessage = ref('')
let dashboardRefreshTimer: number | null = null
let dashboardLoading = false
const detailSections: DetailSection[] = ['morning', 'intraday', 'closing', 'learning']
const activeSection = computed<DetailSection>({
  get() {
    const section = String(route.query.section || 'morning')
    return detailSections.includes(section as DetailSection) ? section as DetailSection : 'morning'
  },
  set(section) {
    router.replace({ path: route.path, query: { ...route.query, section } })
  },
})

const morningRecommendation = computed<ArenaAgentRecommendation | null>(() => {
  return dashboard.value?.morning.recommendations[0] ?? null
})

const sectionTabs = computed<DetailTab[]>(() => [
  {
    id: 'morning',
    title: '早盘',
    subtitle: 'Morning',
    count: sectionCount('morning', dashboard.value?.morning.recommendations.length ?? 0),
    phase: 'morning_recommendation',
    action: '生成推荐',
  },
  {
    id: 'intraday',
    title: '盘中',
    subtitle: 'Intraday',
    count: sectionCount('intraday', dashboard.value?.intraday.orders.length ?? 0),
    phase: 'intraday_trade',
    action: '模拟交易',
  },
  {
    id: 'closing',
    title: '收盘',
    subtitle: 'Closing',
    count: sectionCount('closing', dashboard.value?.closing.reviews.length ?? 0),
    phase: 'closing_review',
    action: '生成复盘',
  },
  {
    id: 'learning',
    title: '学习',
    subtitle: 'Learning',
    count: sectionCount('learning', dashboard.value?.learning.reviews.length ?? 0),
    phase: 'nightly_learning',
    action: '夜间学习',
  },
])

function sectionCount(section: DetailSection, fallback: number): number {
  const counts = dashboard.value?.section_counts
  const value = counts?.[section]
  return typeof value === 'number' ? value : fallback
}

const activeTab = computed<DetailTab>(() => {
  return sectionTabs.value.find((tab) => tab.id === activeSection.value) ?? sectionTabs.value[0]
})

const actionDistribution = computed(() => {
  return dashboard.value?.summary.charts?.action_distribution ?? []
})

const symbolExposure = computed(() => {
  return (dashboard.value?.summary.charts?.symbol_exposure ?? []).map((item) => ({
    name: item.name || item.symbol,
    value: item.market_value,
  }))
})

const positionRows = computed<DetailPosition[]>(() => {
  const rawPositions = dashboard.value?.summary.positions
  const totalAssets = numberValue(dashboard.value?.summary.total_assets)
  if (!Array.isArray(rawPositions)) return []
  return rawPositions.map((item) => {
    const record = item as Record<string, unknown>
    const marketValue = numberValue(record.market_value)
    return {
      symbol: String(record.symbol || ''),
      name: String(record.name || record.symbol || ''),
      quantity: Math.round(numberValue(record.quantity)),
      avg_cost: numberValue(record.avg_cost),
      last_price: numberValue(record.last_price),
      market_value: marketValue,
      unrealized_pnl: numberValue(record.unrealized_pnl),
      weight: totalAssets > 0 ? marketValue / totalAssets : 0,
    }
  })
})

const allocationItems = computed(() => {
  const totalAssets = numberValue(dashboard.value?.summary.total_assets)
  const cash = numberValue(dashboard.value?.summary.cash)
  const positionValue = numberValue(dashboard.value?.summary.position_value)
  return [
    {
      key: 'cash',
      label: '现金',
      value: cash,
      weight: totalAssets > 0 ? cash / totalAssets : 0,
    },
    {
      key: 'position',
      label: '持仓市值',
      value: positionValue,
      weight: totalAssets > 0 ? positionValue / totalAssets : 0,
    },
  ]
})

async function loadDashboard(silent = false): Promise<void> {
  if (dashboardLoading) return
  dashboardLoading = true
  if (!silent) {
    loading.value = true
    errorMessage.value = ''
  }
  try {
    dashboard.value = await api.getArenaAgentDashboard(agentId.value, activeSection.value)
  } catch (error) {
    if (!silent) errorMessage.value = error instanceof Error ? error.message : 'AI 详情加载失败。'
  } finally {
    dashboardLoading = false
    if (!silent) loading.value = false
  }
}

async function runPhase(phase: ArenaPhase): Promise<void> {
  if (!dashboard.value) return
  runningPhase.value = phase
  errorMessage.value = ''
  try {
    await api.runArena({
      phase,
      agents: [dashboard.value.agent],
    })
    await loadDashboard()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '阶段运行失败。'
  } finally {
    runningPhase.value = null
  }
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function scoreText(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(1) : '--'
}

function firstText(values: unknown): string {
  return Array.isArray(values) ? String(values.find(Boolean) || '') : ''
}

function formatPrice(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return value.toFixed(value >= 100 ? 2 : 3)
}

function formatChange(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return `${value.toFixed(2)}%`
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

function profitClass(value: number): string {
  if (value > 0) return 'profit-up'
  if (value < 0) return 'profit-down'
  return ''
}

function nestedRecord(value: unknown, key: string): Record<string, unknown> {
  if (!value || typeof value !== 'object') return {}
  const record = value as Record<string, unknown>
  const child = record[key]
  return child && typeof child === 'object' ? child as Record<string, unknown> : {}
}

function orderDecisionReason(order: ArenaOrder): string {
  const candidate = nestedRecord(order.decision_context, 'selected_candidate')
  const aiSelection = nestedRecord(candidate, 'ai_selection')
  const reasons = aiSelection.selection_reasons
  if (Array.isArray(reasons) && reasons.length) return reasons.slice(0, 2).join('；')
  const rationale = candidate.rationale
  return typeof rationale === 'string' && rationale ? rationale : order.reason
}

function orderPricePlan(order: ArenaOrder): string {
  const retail = nestedRecord(order.decision_context, 'retail_analysis')
  const entry = nestedRecord(retail, 'entry_zone')
  const sell = nestedRecord(retail, 'sell_plan')
  const entryText = entry.low && entry.high ? `买入区 ${entry.low}-${entry.high}` : `成交 ${formatPrice(order.price)}`
  const sellText = sell.stop_loss || sell.first_target
    ? `止损 ${sell.stop_loss ?? '--'}，目标 ${sell.first_target ?? '--'}`
    : '按图表支撑压力与订单纪律执行'
  return `${entryText}；${sellText}`
}

function orderExecutionText(order: ArenaOrder): string {
  const marker = order.charts?.trade_markers?.[0]
  const time = marker?.time_label || marker?.trade_date || order.created_at || '--'
  const amount = typeof marker?.amount === 'number' ? marker.amount : order.amount
  return `${time} · ${order.action} ${order.quantity} 股 · 成交 ${formatPrice(order.price)} · 金额 ${formatAmount(amount)}`
}

function orderRiskPlan(order: ArenaOrder): string {
  const retail = nestedRecord(order.decision_context, 'retail_analysis')
  const sell = nestedRecord(retail, 'sell_plan')
  const stop = sell.stop_loss
  const firstTarget = sell.first_target
  const secondTarget = sell.second_target
  const rule = typeof sell.rule === 'string' ? sell.rule : ''
  if (stop || firstTarget || secondTarget) {
    return `止损 ${stop ?? '--'}；第一目标 ${firstTarget ?? '--'}；第二目标 ${secondTarget ?? '--'}。${rule}`
  }
  const support = order.charts?.support_resistance?.support
  const resistance = order.charts?.support_resistance?.resistance
  if (support || resistance) return `跌破支撑 ${formatPrice(support)} 失效；接近压力 ${formatPrice(resistance)} 降低仓位。`
  return '暂无结构化纪律价，按账户风控和最新图表信号执行。'
}

function orderDataSources(order: ArenaOrder): string {
  const sources = (order.decision_context?.data_sources ?? []) as unknown
  if (Array.isArray(sources) && sources.length) return sources.slice(0, 5).join(' / ')
  const summary = order.charts?.data_summary
  if (summary?.latest_hourly_source) return `分时 ${String(summary.latest_hourly_source)}`
  return '本地日线 + 后端统一行情缓存'
}

function orderChartSummary(order: ArenaOrder): string {
  const summary = order.charts?.data_summary ?? {}
  const indicators = summary.latest_indicators
  const indicatorRecord = indicators && typeof indicators === 'object'
    ? indicators as Record<string, unknown>
    : {}
  const parts = [
    `日线 ${summary.latest_daily_trade_date || '--'}${summary.latest_daily_is_realtime ? ' 实时合并' : ''}`,
    summary.latest_hourly_trade_time ? `分时 ${summary.latest_hourly_trade_time}` : '',
    `MA20 ${formatPrice(Number(indicatorRecord.ma20))}`,
    `MA60 ${formatPrice(Number(indicatorRecord.ma60))}`,
    `RSI ${formatPrice(Number(indicatorRecord.rsi14))}`,
  ].filter(Boolean)
  return parts.join('；')
}

function predictionText(value: Record<string, unknown>): string {
  const base = value.history_end_date ? `基于 ${String(value.history_end_date)}` : '已保存'
  const comparison = value.actual_comparison as Record<string, unknown> | undefined
  if (comparison?.summary) return `${base}；${String(comparison.summary)}`
  return base
}

onMounted(() => {
  loadDashboard()
  dashboardRefreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void loadDashboard(true)
  }, 60000)
})

watch(activeSection, () => {
  void loadDashboard()
})

onBeforeUnmount(() => {
  if (dashboardRefreshTimer) window.clearInterval(dashboardRefreshTimer)
})
</script>

<style scoped>
.arena-agent-detail-head {
  margin-bottom: 14px;
}

.arena-agent-detail-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.arena-dashboard-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.arena-allocation-panel {
  display: grid;
  gap: 12px;
  margin-top: 12px;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #ffffff;
  padding: 12px;
}

.arena-allocation-head,
.arena-allocation-line,
.arena-position-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.arena-allocation-head > div {
  display: grid;
  gap: 3px;
}

.arena-allocation-head strong {
  color: #111827;
  font-size: 14px;
}

.arena-allocation-head span,
.arena-allocation-line span,
.arena-allocation-line small,
.arena-position-row small {
  color: #6b7280;
  font-size: 12px;
}

.arena-allocation-bars {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.arena-allocation-bars > div {
  display: grid;
  gap: 8px;
  border: 1px solid #f0f0ee;
  border-radius: 10px;
  background: #fbfbfa;
  padding: 10px;
}

.arena-allocation-line strong {
  color: #111827;
  font-size: 13px;
}

.arena-allocation-track {
  height: 7px;
  overflow: hidden;
  border-radius: 999px;
  background: #ececf1;
}

.arena-allocation-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #111827;
  transition: width 0.24s ease;
}

.arena-position-table {
  display: grid;
  gap: 4px;
}

.arena-position-row {
  display: grid;
  grid-template-columns: minmax(130px, 1.3fr) 0.6fr 1fr 0.8fr 0.7fr 0.8fr;
  min-height: 38px;
  border-bottom: 1px solid #f0f0ee;
  color: #374151;
  font-size: 12px;
}

.arena-position-row:last-child {
  border-bottom: 0;
}

.arena-position-row > span {
  min-width: 0;
}

.arena-position-row > span:first-child {
  display: grid;
  gap: 1px;
}

.arena-position-row strong {
  overflow: hidden;
  color: #111827;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.arena-position-head {
  min-height: 28px;
  color: #6b7280;
  font-size: 11px;
  font-weight: 650;
}

.arena-allocation-empty {
  border: 1px dashed #dededb;
  border-radius: 10px;
  color: #6b7280;
  padding: 12px;
  font-size: 12px;
}

.arena-agent-detail-summary div,
.arena-agent-section-list article {
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #ffffff;
  padding: 12px;
}

.arena-agent-detail-summary span,
.arena-agent-section-head span,
.arena-agent-section-list small,
.arena-agent-section-list p {
  color: #6b7280;
  font-size: 13px;
}

.arena-agent-detail-summary strong,
.arena-agent-section-list strong {
  display: block;
  color: #111827;
}

.arena-agent-detail-layout {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
}

.arena-agent-detail-sidebar {
  position: sticky;
  top: 18px;
  display: grid;
  gap: 8px;
  padding: 10px;
}

.detail-nav-button {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #374151;
  padding: 10px 11px;
  text-align: left;
}

.detail-nav-button:hover,
.detail-nav-button.active {
  background: #f3f4f6;
  color: #111827;
}

.detail-nav-button span {
  font-size: 14px;
  font-weight: 650;
}

.detail-nav-button small {
  color: #6b7280;
  font-size: 12px;
}

.arena-agent-section-list {
  display: grid;
  gap: 10px;
}

.arena-agent-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.arena-agent-section-list p {
  margin: 0 0 8px;
  line-height: 1.5;
}

.arena-agent-picks {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.arena-pick-card,
.arena-order-explain > div {
  display: grid;
  gap: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #f9fafb;
  color: #374151;
  padding: 9px;
  font-size: 12px;
}

.arena-pick-card strong,
.arena-order-explain strong {
  color: #111827;
  font-size: 12px;
}

.arena-pick-card p {
  margin: 0;
  color: #6b7280;
  line-height: 1.35;
}

.arena-pick-card small,
.arena-order-explain span {
  color: #6b7280;
  font-size: 11px;
  line-height: 1.35;
}

.arena-order-explain {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin: 8px 0 10px;
}

@media (max-width: 900px) {
  .arena-agent-detail-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .arena-agent-detail-layout {
    grid-template-columns: 1fr;
  }

  .arena-dashboard-charts {
    grid-template-columns: 1fr;
  }

  .arena-allocation-bars {
    grid-template-columns: 1fr;
  }

  .arena-position-row {
    grid-template-columns: minmax(120px, 1.2fr) 0.6fr 1fr;
  }

  .arena-position-row span:nth-child(4),
  .arena-position-row span:nth-child(5),
  .arena-position-row span:nth-child(6) {
    display: none;
  }

  .arena-agent-detail-sidebar {
    position: static;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .arena-agent-picks,
  .arena-order-explain {
    grid-template-columns: 1fr;
  }
}
</style>
