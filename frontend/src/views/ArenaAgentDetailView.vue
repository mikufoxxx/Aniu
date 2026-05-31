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
            返回竞技场
          </RouterLink>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :class="{ 'is-loading': loading }"
            :disabled="loading"
            @click="loadDashboard"
          >
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
          <small>{{ tab.count }} 条</small>
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
                <span v-for="pick in morningRecommendation.picks || []" :key="pick.symbol">
                  {{ pick.name }} {{ pick.symbol }} · {{ scoreText(pick.score) }}
                </span>
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
              <MarketKlineChart
                v-if="order.charts"
                title="交易走势与买卖点"
                :subtitle="`${order.name} ${order.symbol}`"
                :price-series="order.charts.price_series"
                :markers="order.charts.trade_markers"
              />
              <small>决策 {{ latencyText(order.decision_latency_ms) }} / 写入 {{ latencyText(order.record_latency_ms) }}</small>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/services/api'
import type { ArenaAgentDashboardPayload, ArenaAgentRecommendation } from '@/types'
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

const route = useRoute()
const agentId = computed(() => String(route.params.agentId || ''))
const dashboard = ref<ArenaAgentDashboardPayload | null>(null)
const loading = ref(false)
const runningPhase = ref<ArenaPhase | null>(null)
const errorMessage = ref('')
const activeSection = ref<DetailSection>('morning')

const morningRecommendation = computed<ArenaAgentRecommendation | null>(() => {
  return dashboard.value?.morning.recommendations[0] ?? null
})

const sectionTabs = computed<DetailTab[]>(() => [
  {
    id: 'morning',
    title: '早盘',
    subtitle: 'Morning',
    count: dashboard.value?.morning.recommendations.length ?? 0,
    phase: 'morning_recommendation',
    action: '生成推荐',
  },
  {
    id: 'intraday',
    title: '盘中',
    subtitle: 'Intraday',
    count: dashboard.value?.intraday.orders.length ?? 0,
    phase: 'intraday_trade',
    action: '模拟交易',
  },
  {
    id: 'closing',
    title: '收盘',
    subtitle: 'Closing',
    count: dashboard.value?.closing.reviews.length ?? 0,
    phase: 'closing_review',
    action: '生成复盘',
  },
  {
    id: 'learning',
    title: '学习',
    subtitle: 'Learning',
    count: dashboard.value?.learning.reviews.length ?? 0,
    phase: 'nightly_learning',
    action: '夜间学习',
  },
])

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

async function loadDashboard(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    dashboard.value = await api.getArenaAgentDashboard(agentId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 详情加载失败。'
  } finally {
    loading.value = false
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

function latencyText(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${Math.max(0, Math.round(value))}ms`
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

onMounted(() => {
  loadDashboard()
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

.arena-agent-detail-summary div,
.arena-agent-section-list article {
  border: 1px solid #e5e7eb;
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
  grid-template-columns: 210px minmax(0, 1fr);
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
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.arena-agent-picks span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #f9fafb;
  color: #374151;
  padding: 6px 8px;
  font-size: 12px;
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

  .arena-agent-detail-sidebar {
    position: static;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
</style>
