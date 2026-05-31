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
    </section>

    <section class="content-grid content-grid-primary arena-agent-detail-grid">
      <section class="panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>早盘</h2>
            <p class="section-kicker">Morning</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :disabled="runningPhase === 'morning_recommendation'"
            @click="runPhase('morning_recommendation')"
          >
            {{ runningPhase === 'morning_recommendation' ? '运行中…' : '生成推荐' }}
          </button>
        </div>
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
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>盘中</h2>
            <p class="section-kicker">Intraday</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :disabled="runningPhase === 'intraday_trade'"
            @click="runPhase('intraday_trade')"
          >
            {{ runningPhase === 'intraday_trade' ? '运行中…' : '模拟交易' }}
          </button>
        </div>
        <div v-if="dashboard?.intraday.orders.length" class="arena-agent-section-list">
          <article v-for="order in dashboard.intraday.orders" :key="order.id">
            <div class="arena-agent-section-head">
              <strong>{{ order.action }} {{ order.name }} {{ order.symbol }}</strong>
              <span>{{ order.quantity }} / {{ formatPrice(order.price) }}</span>
            </div>
            <p>{{ order.reason }}</p>
            <small>决策 {{ latencyText(order.decision_latency_ms) }} / 写入 {{ latencyText(order.record_latency_ms) }}</small>
          </article>
        </div>
        <div v-else class="empty-state"><p>暂无盘中模拟记录。</p></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>收盘</h2>
            <p class="section-kicker">Closing</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :disabled="runningPhase === 'closing_review'"
            @click="runPhase('closing_review')"
          >
            {{ runningPhase === 'closing_review' ? '运行中…' : '生成复盘' }}
          </button>
        </div>
        <div v-if="dashboard?.closing.reviews.length" class="arena-agent-section-list">
          <article v-for="review in dashboard.closing.reviews" :key="review.id">
            <div class="arena-agent-section-head">
              <strong>收盘复盘</strong>
              <span>{{ review.created_at || '--' }}</span>
            </div>
            <p>{{ review.summary }}</p>
          </article>
        </div>
        <div v-else class="empty-state"><p>暂无收盘复盘。</p></div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>学习</h2>
            <p class="section-kicker">Learning</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :disabled="runningPhase === 'nightly_learning'"
            @click="runPhase('nightly_learning')"
          >
            {{ runningPhase === 'nightly_learning' ? '运行中…' : '夜间学习' }}
          </button>
        </div>
        <div v-if="dashboard?.learning.reviews.length" class="arena-agent-section-list">
          <article v-for="review in dashboard.learning.reviews" :key="review.id">
            <div class="arena-agent-section-head">
              <strong>回测学习</strong>
              <span>{{ review.created_at || '--' }}</span>
            </div>
            <p>{{ review.summary }}</p>
          </article>
        </div>
        <div v-else class="empty-state"><p>暂无学习记录。</p></div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/services/api'
import type { ArenaAgentDashboardPayload, ArenaAgentRecommendation } from '@/types'

type ArenaPhase = 'morning_recommendation' | 'intraday_trade' | 'closing_review' | 'nightly_learning'

const route = useRoute()
const agentId = computed(() => String(route.params.agentId || ''))
const dashboard = ref<ArenaAgentDashboardPayload | null>(null)
const loading = ref(false)
const runningPhase = ref<ArenaPhase | null>(null)
const errorMessage = ref('')

const morningRecommendation = computed<ArenaAgentRecommendation | null>(() => {
  return dashboard.value?.morning.recommendations[0] ?? null
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

.arena-agent-detail-summary div,
.arena-agent-section-list article {
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  padding: 12px;
}

.arena-agent-detail-summary span,
.arena-agent-section-head span,
.arena-agent-section-list small,
.arena-agent-section-list p {
  color: #9cb2cf;
  font-size: 13px;
}

.arena-agent-detail-summary strong,
.arena-agent-section-list strong {
  display: block;
  color: #f7fbff;
}

.arena-agent-detail-grid {
  align-items: start;
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
  border: 1px solid rgba(145, 170, 214, 0.14);
  border-radius: 8px;
  background: rgba(7, 14, 27, 0.52);
  color: #bfd0ea;
  padding: 6px 8px;
  font-size: 12px;
}

@media (max-width: 900px) {
  .arena-agent-detail-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
