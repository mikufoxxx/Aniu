<template>
  <div class="arena-home">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="arena-layout">
      <section class="panel arena-main-panel">
        <div class="panel-head">
          <div>
            <h2>AI 收益排名</h2>
            <p class="arena-muted">统一初始资金 {{ formatAmount(arenaInitialCash) }}；点击卡片进入早盘、盘中、收盘、学习详情。</p>
          </div>
          <div class="arena-panel-actions">
            <button class="button ghost small" :disabled="saving" @click="addAgent">
              <span class="material-symbols-rounded" aria-hidden="true">person_add</span>
              新增 AI
            </button>
            <button class="button ghost small" :disabled="loading" @click="loadArenaState()">
              <span class="material-symbols-rounded" aria-hidden="true">sync</span>
              刷新
            </button>
          </div>
        </div>
        <div class="arena-view-tabs" role="tablist" aria-label="竞技场视图">
          <button
            type="button"
            :class="{ active: activeArenaView === 'ranking' }"
            @click="setArenaView('ranking')"
          >
            收益排名
          </button>
          <button
            type="button"
            :class="{ active: activeArenaView === 'equity' }"
            @click="setArenaView('equity')"
          >
            收益曲线
          </button>
        </div>
        <div class="arena-schedule-strip">
          <span>
            <span class="material-symbols-rounded" aria-hidden="true">schedule</span>
            早盘 08:00-08:30 错峰
          </span>
          <span>盘中 09:35 / 10:40 / 13:30 / 14:25</span>
          <span>收盘 15:10 起</span>
          <span>夜间 20:00 起</span>
        </div>

        <div v-if="activeArenaView === 'ranking'" class="arena-agent-list">
          <template v-if="loading && !rankedAgents.length">
            <article v-for="item in 4" :key="item" class="arena-agent-row arena-agent-skeleton" aria-hidden="true">
              <div class="skeleton-block skeleton-rank"></div>
              <div class="arena-agent-main">
                <div class="skeleton-block skeleton-title"></div>
                <div class="skeleton-chip-row">
                  <span class="skeleton-block"></span>
                  <span class="skeleton-block"></span>
                  <span class="skeleton-block"></span>
                </div>
              </div>
              <div class="arena-agent-metrics">
                <div v-for="metric in 6" :key="metric">
                  <span class="skeleton-block"></span>
                  <strong class="skeleton-block"></strong>
                </div>
              </div>
            </article>
          </template>
          <template v-else>
            <article
              v-for="(agent, index) in rankedAgents"
              :key="agent.id"
              class="arena-agent-row"
              role="button"
              tabindex="0"
              @click="openAgent(agent.id)"
              @keydown.enter.self="openAgent(agent.id)"
            >
              <button
                class="button ghost small arena-card-config"
                type="button"
                @click.stop="openAgentConfig(agent.id)"
              >
                <span class="material-symbols-rounded" aria-hidden="true">settings</span>
                配置
              </button>
              <div class="arena-rank">#{{ index + 1 }}</div>
              <div class="arena-agent-main">
                <div class="arena-agent-title">
                  <strong>{{ agent.name }}</strong>
                  <span>{{ agent.model || '默认模型' }} · AI 自主判断风格</span>
                  <div class="arena-card-tags">
                    <span :class="{ 'is-muted': !agent.enabled }">{{ agent.enabled ? '运行中' : '已停用' }}</span>
                    <span>早盘精选 {{ agent.latestPicks.length }}</span>
                    <span>持仓 {{ agent.positionCount }}</span>
                    <span>复盘 {{ agent.closingCount }}</span>
                    <span>学习 {{ agent.learningCount }}</span>
                  </div>
                </div>
                <div class="arena-pick-chips">
                  <span v-for="pick in agent.latestPicks" :key="pick.symbol">
                    <b>{{ pick.name || pick.symbol }}</b>
                    <small>
                      {{ pick.symbol }} · {{ formatPrice(pick.price) }}
                      <em :class="profitClass(percentRatio(pick.change_pct))">
                        {{ formatChange(pick.change_pct) }}
                      </em>
                    </small>
                    <small>AI {{ scoreText(pick.ai_selection_score ?? pick.score) }} · {{ riskText(pick.risk_flags) }}</small>
                  </span>
                  <span v-if="!agent.latestPicks.length">暂无早盘精选</span>
                </div>
              </div>
              <div class="arena-agent-metrics">
                <div>
                  <span>总资产</span>
                  <strong>{{ formatAmount(agent.totalAssets) }}</strong>
                </div>
                <div>
                  <span>收益</span>
                  <strong :class="profitClass(agent.returnRatio)">{{ formatPercent(agent.returnRatio) }}</strong>
                </div>
                <div>
                  <span>订单</span>
                  <strong>{{ agent.orderCount }}</strong>
                </div>
                <div>
                  <span>现金</span>
                  <strong>{{ formatAmount(agent.cash) }}</strong>
                </div>
                <div>
                  <span>持仓市值</span>
                  <strong>{{ formatAmount(agent.positionValue) }}</strong>
                </div>
                <div>
                  <span>持仓数</span>
                  <strong>{{ agent.positionCount }}</strong>
                </div>
                <div>
                  <span>已实现</span>
                  <strong :class="profitClass(agent.realizedPnl)">{{ formatAmount(agent.realizedPnl) }}</strong>
                </div>
                <div>
                  <span>最新动作</span>
                  <strong>{{ agent.latestAction }}</strong>
                </div>
              </div>
            </article>
          </template>
        </div>
        <section v-else class="arena-equity-panel">
          <div class="arena-equity-toolbar">
            <div>
              <strong>多 AI 收益变化</strong>
              <span>{{ equityCurves?.refreshed_at ? `刷新 ${formatTime(equityCurves.refreshed_at)}` : '等待实时权益快照' }}</span>
            </div>
            <div class="arena-interval-tabs" role="tablist" aria-label="收益曲线粒度">
              <button
                v-for="interval in equityIntervals"
                :key="interval.id"
                type="button"
                :class="{ active: equityInterval === interval.id }"
                @click="setEquityInterval(interval.id)"
              >
                {{ interval.label }}
              </button>
            </div>
          </div>
          <MultiEquityCurveChart
            v-if="equityChartCurves.length"
            title="收益率曲线"
            subtitle="每条线代表一个 AI 的独立模拟账户，按当前持仓实时重估"
            :curves="equityChartCurves"
            value-mode="return"
          />
          <div v-else class="empty-state">
            <p>{{ equityLoading ? '正在加载收益曲线…' : '暂无收益曲线数据。' }}</p>
          </div>
        </section>

      </section>
    </section>

    <Transition name="modal">
    <div v-if="configAgent" class="arena-config-overlay" @click.self="closeAgentConfig">
      <section class="arena-config-modal panel">
        <div class="panel-head">
          <div>
            <h2>配置 AI</h2>
            <p class="arena-muted">只修改当前 AI 的竞技场参数。</p>
          </div>
          <button class="button ghost small" type="button" @click="closeAgentConfig">
            <span class="material-symbols-rounded" aria-hidden="true">close</span>
            关闭
          </button>
        </div>

        <div class="arena-agent-editor">
          <label>
            <span>ID</span>
            <input v-model="configAgent.id" />
          </label>
          <label>
            <span>名称</span>
            <input v-model="configAgent.name" />
          </label>
          <label>
            <span>服务商</span>
            <input v-model="configAgent.provider" placeholder="forecast-ai" />
          </label>
          <label>
            <span>模型</span>
            <select v-model="configAgent.model">
              <option value="">默认模型</option>
              <option v-for="model in aiConfig.models" :key="model" :value="model">
                {{ model }}
              </option>
            </select>
          </label>
          <div class="arena-config-summary">
            <span>风格 AI 自主判断</span>
            <span>接口 {{ aiConfig.base_url || '--' }}</span>
            <span>Key {{ aiConfig.api_key_configured ? aiConfig.api_key_masked : '未配置' }}</span>
            <span>已配置模型 {{ aiConfig.models.length }}</span>
          </div>
          <label class="arena-enable">
            <input v-model="configAgent.enabled" type="checkbox" /> 启用
          </label>
        </div>

        <div class="arena-modal-actions">
          <button class="button ghost small" type="button" @click="removeConfigAgent">
            <span class="material-symbols-rounded" aria-hidden="true">delete</span>
            删除
          </button>
          <button class="button primary small" :disabled="saving" type="button" @click="saveConfigAgent">
            <span class="material-symbols-rounded" aria-hidden="true">save</span>
            {{ saving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </section>
    </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { AppSettings, ArenaAgentConfig, ArenaAgentDashboardPayload, ArenaAIConfigPayload, ArenaEquityCurvesPayload, ArenaLeaderboardPayload } from '@/types'
import MultiEquityCurveChart from '@/components/charts/MultiEquityCurveChart.vue'

const router = useRouter()
const route = useRoute()
const agents = ref<ArenaAgentConfig[]>([])
const dashboards = ref<Record<string, ArenaAgentDashboardPayload>>({})
const leaderboard = ref<ArenaLeaderboardPayload>({ items: [] })
const equityCurves = ref<ArenaEquityCurvesPayload | null>(null)
const aiConfig = ref<ArenaAIConfigPayload>({
  base_url: '',
  api_key_configured: false,
  api_key_masked: '未配置',
  models: [],
  model_count: 0,
})
const loading = ref(false)
const equityLoading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const configAgentIndex = ref(-1)
const arenaInitialCash = ref(200000)
const equityInterval = ref<'daily' | 'weekly' | 'hourly'>('daily')
let arenaRefreshTimer: number | null = null
let arenaLoading = false

const equityIntervals = [
  { id: 'daily' as const, label: '日' },
  { id: 'weekly' as const, label: '周' },
  { id: 'hourly' as const, label: '小时' },
]

const activeArenaView = computed<'ranking' | 'equity'>(() => {
  return route.query.view === 'equity' ? 'equity' : 'ranking'
})

const rankedAgents = computed(() => {
  return agents.value
    .map((agent) => {
      const board = dashboards.value[agent.id]
      const rank = leaderboard.value.items.find((item) => item.agent_id === agent.id)
      const rankPicks = rank?.latest_recommendation?.picks ?? []
      const boardPicks = board?.morning.recommendations[0]?.picks ?? []
      const latestPicks = rankPicks.length ? rankPicks : boardPicks
      const summary = (board?.summary ?? {}) as Record<string, unknown>
      const rankPositions = rank?.positions ?? []
      const summaryPositions = Array.isArray(summary.positions) ? summary.positions : []
      const latestOrder = board?.intraday.orders[0]
      return {
        ...agent,
        totalAssets: rank?.total_assets ?? Number(board?.summary.total_assets ?? 0),
        returnRatio: rank?.return_ratio ?? Number(board?.summary.return_ratio ?? 0),
        orderCount: rank?.order_count ?? Number(board?.summary.order_count ?? 0),
        cash: rank?.cash ?? Number(summary.cash ?? 0),
        positionValue: rank?.position_value ?? Number(summary.position_value ?? 0),
        realizedPnl: rank?.realized_pnl ?? Number(summary.realized_pnl ?? 0),
        positionCount: rankPositions.length || summaryPositions.length,
        latestAction: latestOrder ? `${latestOrder.action} ${latestOrder.name || latestOrder.symbol}` : '--',
        closingCount: board?.closing.reviews.length ?? 0,
        learningCount: board?.learning.reviews.length ?? 0,
        latestPicks,
      }
    })
    .sort((left, right) => right.totalAssets - left.totalAssets)
})

const equityChartCurves = computed(() => {
  return (equityCurves.value?.curves ?? []).map((curve) => ({
    strategy_name: curve.agent_id,
    display_name: curve.agent_name,
    points: curve.points,
  }))
})

async function loadArenaState(silent = false): Promise<void> {
  if (arenaLoading) return
  arenaLoading = true
  if (!silent) {
    loading.value = true
    errorMessage.value = ''
  }
  try {
    const [agentPayload, leaderboardPayload, configPayload, settingsPayload] = await Promise.all([
      api.getArenaAgents(),
      api.getArenaLeaderboard(),
      api.getArenaAIConfig(),
      api.getSettings(),
    ])
    agents.value = agentPayload.agents.map(normalizeAgent)
    leaderboard.value = leaderboardPayload
    aiConfig.value = configPayload
    arenaInitialCash.value = normalizeInitialCash(settingsPayload)
    if (activeArenaView.value === 'equity') await loadEquityCurves(true)
    if (silent) return
    await loadDashboardPreviews()
  } catch (error) {
    if (!silent) errorMessage.value = error instanceof Error ? error.message : '竞技场加载失败。'
  } finally {
    arenaLoading = false
    if (!silent) loading.value = false
  }
}

async function loadDashboardPreviews(): Promise<void> {
  const entries = await Promise.allSettled(
    agents.value.map(async (agent) => [agent.id, await api.getArenaAgentDashboard(agent.id)] as const),
  )
  const nextDashboards = { ...dashboards.value }
  for (const entry of entries) {
    if (entry.status === 'fulfilled') {
      nextDashboards[entry.value[0]] = entry.value[1]
    }
  }
  dashboards.value = nextDashboards
}

async function loadEquityCurves(silent = false): Promise<void> {
  if (!silent) equityLoading.value = true
  try {
    equityCurves.value = await api.getArenaEquityCurves(equityInterval.value)
  } catch (error) {
    if (!silent) errorMessage.value = error instanceof Error ? error.message : '收益曲线加载失败。'
  } finally {
    if (!silent) equityLoading.value = false
  }
}

function setArenaView(view: 'ranking' | 'equity'): void {
  router.replace({ path: route.path, query: { ...route.query, view: view === 'equity' ? 'equity' : undefined } })
  if (view === 'equity') void loadEquityCurves()
}

function setEquityInterval(interval: 'daily' | 'weekly' | 'hourly'): void {
  equityInterval.value = interval
  void loadEquityCurves()
}

const configAgent = computed(() => agents.value[configAgentIndex.value] ?? null)

async function saveAgents(): Promise<void> {
  saving.value = true
  errorMessage.value = ''
  try {
    const payload = await api.updateArenaAgents({ agents: agents.value })
    agents.value = payload.agents.map(normalizeAgent)
    await loadArenaState()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 配置保存失败。'
  } finally {
    saving.value = false
  }
}

function addAgent(): void {
  const next = agents.value.length + 1
  const agent = normalizeAgent({
    id: `custom_ai_${next}`,
    name: `自定义 AI ${next}`,
    style: 'auto',
  })
  agents.value.push(agent)
  configAgentIndex.value = agents.value.length - 1
}

function removeAgent(agentId: string): void {
  const activeAgent = configAgent.value
  agents.value = agents.value.filter((agent) => agent.id !== agentId)
  if (activeAgent?.id === agentId) configAgentIndex.value = -1
}

function normalizeAgent(agent: ArenaAgentConfig): ArenaAgentConfig {
  return {
    ...agent,
    style: 'auto',
    provider: agent.provider ?? 'forecast-ai',
    model: agent.model ?? '',
    enabled: agent.enabled ?? true,
    prompt: agent.prompt ?? '',
  }
}

function openAgent(agentId: string): void {
  router.push({ name: 'arena-agent-detail', params: { agentId } })
}

function openAgentConfig(agentId: string): void {
  configAgentIndex.value = agents.value.findIndex((agent) => agent.id === agentId)
}

function closeAgentConfig(): void {
  configAgentIndex.value = -1
}

async function saveConfigAgent(): Promise<void> {
  await saveAgents()
  closeAgentConfig()
}

async function removeConfigAgent(): Promise<void> {
  if (!configAgent.value) return
  agents.value.splice(configAgentIndex.value, 1)
  closeAgentConfig()
  await saveAgents()
}

function scoreText(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(1) : '--'
}

function formatPrice(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '--'
}

function formatChange(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '--'
}

function percentRatio(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value / 100 : 0
}

function riskText(flags: string[] | undefined): string {
  return flags?.length ? `风险 ${flags.length}` : '无风险标记'
}

function profitClass(value: number): string {
  if (value > 0) return 'profit-up'
  if (value < 0) return 'profit-down'
  return ''
}

function formatAmount(value: number): string {
  if (Math.abs(value) >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)}万`
  return value.toFixed(2)
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function normalizeInitialCash(payload: AppSettings): number {
  const value = Number(payload.arena_initial_cash)
  return Number.isFinite(value) && value >= 10000 ? value : 200000
}

onMounted(() => {
  loadArenaState()
  arenaRefreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void loadArenaState(true)
  }, 30000)
})

onBeforeUnmount(() => {
  if (arenaRefreshTimer) window.clearInterval(arenaRefreshTimer)
})
</script>

<style scoped>
.arena-home {
  display: grid;
  gap: 12px;
}

.arena-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
}

.arena-hero h1 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 24px;
  line-height: 1.1;
}

.arena-hero p,
.arena-muted {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.arena-panel-actions,
.arena-editor-actions,
.arena-modal-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.arena-view-tabs,
.arena-interval-tabs {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  width: fit-content;
  border: 1px solid #ececf1;
  border-radius: 999px;
  background: #f7f7f5;
  padding: 3px;
}

.arena-view-tabs {
  margin: 12px 0;
}

.arena-view-tabs button,
.arena-interval-tabs button {
  min-height: 28px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: #6b7280;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease, box-shadow 0.18s ease;
}

.arena-view-tabs button.active,
.arena-interval-tabs button.active {
  background: #ffffff;
  color: #111827;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
}

.arena-schedule-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: -2px 0 12px;
  color: #5f6368;
  font-size: 12px;
}

.arena-schedule-strip span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 28px;
  border: 1px solid #ececf1;
  border-radius: 999px;
  background: #fbfbfa;
  padding: 0 10px;
}

.arena-schedule-strip .material-symbols-rounded {
  font-size: 16px;
}

.arena-layout {
  display: block;
}

.arena-agent-list {
  display: grid;
  gap: 10px;
}

.arena-equity-panel {
  display: grid;
  gap: 12px;
}

.arena-equity-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #fbfbfa;
  padding: 12px;
}

.arena-equity-toolbar > div:first-child {
  display: grid;
  gap: 3px;
}

.arena-equity-toolbar strong {
  color: #111827;
  font-size: 14px;
}

.arena-equity-toolbar span {
  color: #6b7280;
  font-size: 12px;
}

.arena-agent-row {
  position: relative;
  display: grid;
  grid-template-columns: minmax(430px, 1fr) minmax(280px, 340px);
  gap: 16px;
  align-items: stretch;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #ffffff;
  padding: 14px 14px 14px 56px;
  cursor: pointer;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}

.arena-card-config {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 1;
}

.arena-agent-row:hover {
  border-color: #dededb;
  background: #fcfcfb;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  transform: translateY(-1px);
}

.arena-rank {
  position: absolute;
  top: 16px;
  left: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #f7f7f5;
  color: #6b7280;
  font-size: 12px;
  font-weight: 650;
}

.arena-agent-main {
  min-width: 0;
  padding-right: 82px;
}

.arena-agent-title {
  display: grid;
  gap: 4px;
}

.arena-agent-title strong {
  color: #111827;
  font-size: 15px;
}

.arena-agent-title span,
.arena-agent-metrics span {
  color: #6b7280;
  font-size: 12px;
}

.arena-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.arena-card-tags span {
  display: inline-flex;
  align-items: center;
  border: 1px solid #ececf1;
  border-radius: 999px;
  background: #f7f7f5;
  color: #4b5563;
  padding: 4px 7px;
  font-size: 11px;
  line-height: 1;
}

.arena-card-tags .is-muted {
  color: #8b949e;
  background: #fbfbfa;
}

.arena-pick-chips {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: 8px;
}

.arena-pick-chips span {
  display: grid;
  gap: 2px;
  min-width: 0;
  border: 1px solid #ececf1;
  border-radius: 10px;
  background: #f7f7f5;
  color: #374151;
  padding: 6px 8px;
  font-size: 12px;
}

.arena-pick-chips b {
  color: #111827;
  font-size: 12px;
}

.arena-pick-chips small {
  color: #6b7280;
  font-size: 11px;
}

.arena-pick-chips em {
  margin-left: 4px;
  font-style: normal;
}

.arena-agent-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding-top: 42px;
}

.arena-agent-metrics div {
  display: grid;
  gap: 3px;
  min-height: 52px;
  border: 1px solid #f0f0ee;
  border-radius: 10px;
  background: #fbfbfa;
  padding: 8px;
}

.arena-agent-metrics strong {
  overflow: hidden;
  color: #111827;
  font-size: 13px;
  line-height: 1.2;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.arena-agent-editor,
.arena-agent-editor label {
  display: grid;
  gap: 7px;
}

.arena-agent-editor {
  border: 1px solid #ececf1;
  border-radius: 12px;
  padding: 12px;
  color: #6b7280;
  font-size: 12px;
}

.arena-agent-editor input,
.arena-agent-editor select {
  width: 100%;
  border: 1px solid #dededb;
  border-radius: 10px;
  background: #ffffff;
  color: #111827;
  padding: 8px 10px;
}

.arena-config-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.arena-config-summary span {
  border: 1px solid #ececf1;
  border-radius: 999px;
  background: #f7f7f5;
  color: #4b5563;
  padding: 5px 8px;
}

.arena-enable {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
}

.arena-config-overlay {
  position: fixed;
  inset: 0;
  z-index: 30;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(17, 24, 39, 0.28);
  backdrop-filter: blur(2px);
}

.arena-config-modal {
  width: min(540px, 100%);
  max-height: calc(100vh - 40px);
  overflow: auto;
}

.arena-modal-actions {
  justify-content: flex-end;
  margin-top: 12px;
}

.arena-agent-skeleton {
  pointer-events: none;
}

.skeleton-block {
  display: block;
  min-height: 12px;
  border-radius: 999px;
  background: linear-gradient(90deg, #f0f0ed 0%, #fafaf8 48%, #f0f0ed 100%);
  background-size: 220% 100%;
  animation: skeletonPulse 1.35s ease-in-out infinite;
}

.skeleton-rank {
  width: 30px;
  height: 18px;
}

.skeleton-title {
  width: min(220px, 70%);
  height: 18px;
}

.skeleton-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 14px;
}

.skeleton-chip-row .skeleton-block {
  width: 148px;
  height: 48px;
  border-radius: 10px;
}

.arena-agent-skeleton .arena-agent-metrics .skeleton-block {
  width: 70%;
}

.arena-agent-skeleton .arena-agent-metrics strong.skeleton-block {
  width: 88%;
  height: 14px;
}

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.22s ease, backdrop-filter 0.22s ease;
}

.modal-enter-active .arena-config-modal,
.modal-leave-active .arena-config-modal {
  transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.22s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
  backdrop-filter: blur(0);
}

.modal-enter-from .arena-config-modal,
.modal-leave-to .arena-config-modal {
  opacity: 0;
  transform: translateY(12px) scale(0.98);
}

@keyframes skeletonPulse {
  from {
    background-position: 120% 0;
  }
  to {
    background-position: -120% 0;
  }
}

@media (max-width: 1100px) {
  .arena-layout,
  .arena-agent-row {
    grid-template-columns: 1fr;
  }

  .arena-agent-metrics {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    padding-top: 0;
  }
}

@media (max-width: 820px) {
  .arena-equity-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .arena-agent-main {
    padding-right: 0;
  }

  .arena-pick-chips,
  .arena-agent-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
