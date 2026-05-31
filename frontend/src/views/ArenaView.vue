<template>
  <div class="arena-home">
    <section class="arena-hero panel">
      <div>
        <h1>AI 竞技场</h1>
        <p>每个 AI 独立选股、模拟交易、复盘和学习；首页只看排名与收益。</p>
      </div>
      <div class="arena-actions">
        <button class="button ghost small" :disabled="loading" @click="runArena('morning_recommendation')">早盘推荐</button>
        <button class="button primary small" :disabled="loading" @click="runArena('intraday_trade')">盘中模拟</button>
        <button class="button ghost small" :disabled="loading" @click="runArena('closing_review')">收盘复盘</button>
        <button class="button ghost small" :disabled="loading" @click="runArena('nightly_learning')">夜间学习</button>
      </div>
    </section>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="arena-layout">
      <section class="panel arena-main-panel">
        <div class="panel-head">
          <div>
            <h2>AI 收益排名</h2>
            <p class="arena-muted">按总资产排序；点击卡片进入 AI 的早盘、盘中、收盘、学习详情。</p>
          </div>
          <button class="button ghost small" :disabled="loading" @click="loadArenaState">刷新</button>
        </div>

        <div class="arena-agent-list">
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
              @click.stop="focusAgentConfig(agent.id)"
            >
              配置
            </button>
            <div class="arena-rank">#{{ index + 1 }}</div>
            <div class="arena-agent-main">
              <div class="arena-agent-title">
                <strong>{{ agent.name }}</strong>
                <span>{{ styleText(agent.style) }} · {{ agent.model || '默认模型' }}</span>
              </div>
              <div class="arena-pick-chips">
                <span v-for="pick in agent.latestPicks" :key="pick.symbol">
                  {{ pick.symbol }} {{ scoreText(pick.score) }}
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
            </div>
          </article>
        </div>
      </section>

      <aside class="panel arena-side-panel">
        <div class="panel-head">
          <div>
            <h2>AI 选手</h2>
            <p class="arena-muted">只保留竞技场需要的配置。</p>
          </div>
          <button class="button ghost small" :disabled="saving" @click="addAgent">新增</button>
        </div>
        <div class="arena-agent-editor-list">
          <label
            v-for="agent in agents"
            :id="agentEditorId(agent.id)"
            :key="agent.id"
            class="arena-agent-editor"
            :class="{ 'is-selected': selectedConfigAgentId === agent.id }"
          >
            <span>ID</span>
            <input v-model="agent.id" />
            <span>名称</span>
            <input v-model="agent.name" />
            <span>风格</span>
            <select v-model="agent.style">
              <option value="momentum">短线动量</option>
              <option value="balanced">量化轮动</option>
              <option value="risk_control">长线稳健</option>
            </select>
            <span>模型</span>
            <input v-model="agent.model" placeholder="deepseek-chat" />
            <div class="arena-editor-actions">
              <label class="arena-enable"><input v-model="agent.enabled" type="checkbox" /> 启用</label>
              <button class="button ghost small" type="button" @click="removeAgent(agent.id)">删除</button>
            </div>
          </label>
        </div>
        <button class="button primary arena-save-button" :disabled="saving" @click="saveAgents">
          {{ saving ? '保存中...' : '保存 AI 配置' }}
        </button>
      </aside>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { ArenaAgentConfig, ArenaAgentDashboardPayload, ArenaLeaderboardPayload } from '@/types'

type ArenaPhase = 'morning_recommendation' | 'intraday_trade' | 'closing_review' | 'nightly_learning'

const router = useRouter()
const agents = ref<ArenaAgentConfig[]>([])
const dashboards = ref<Record<string, ArenaAgentDashboardPayload>>({})
const leaderboard = ref<ArenaLeaderboardPayload>({ items: [] })
const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const selectedConfigAgentId = ref('')

const rankedAgents = computed(() => {
  return agents.value
    .map((agent) => {
      const board = dashboards.value[agent.id]
      const rank = leaderboard.value.items.find((item) => item.agent_id === agent.id)
      const latestPicks = board?.morning.recommendations[0]?.picks ?? []
      return {
        ...agent,
        totalAssets: rank?.total_assets ?? Number(board?.summary.total_assets ?? 0),
        returnRatio: rank?.return_ratio ?? Number(board?.summary.return_ratio ?? 0),
        orderCount: rank?.order_count ?? Number(board?.summary.order_count ?? 0),
        latestPicks,
      }
    })
    .sort((left, right) => right.totalAssets - left.totalAssets)
})

async function loadArenaState(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [agentPayload, leaderboardPayload] = await Promise.all([
      api.getArenaAgents(),
      api.getArenaLeaderboard(),
    ])
    agents.value = agentPayload.agents.map(normalizeAgent)
    leaderboard.value = leaderboardPayload
    const entries = await Promise.allSettled(
      agents.value.map(async (agent) => [agent.id, await api.getArenaAgentDashboard(agent.id)] as const),
    )
    dashboards.value = Object.fromEntries(
      entries
        .filter((entry): entry is PromiseFulfilledResult<readonly [string, ArenaAgentDashboardPayload]> => entry.status === 'fulfilled')
        .map((entry) => entry.value),
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '竞技场加载失败。'
  } finally {
    loading.value = false
  }
}

async function runArena(phase: ArenaPhase): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    await api.runArena({
      phase,
      initial_cash: 200000,
    })
    await loadArenaState()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '竞技场运行失败。'
  } finally {
    loading.value = false
  }
}

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
    style: 'balanced',
  })
  agents.value.push(agent)
  focusAgentConfig(agent.id)
}

function removeAgent(agentId: string): void {
  agents.value = agents.value.filter((agent) => agent.id !== agentId)
}

function normalizeAgent(agent: ArenaAgentConfig): ArenaAgentConfig {
  return {
    ...agent,
    provider: agent.provider ?? 'openai-compatible',
    model: agent.model ?? '',
    enabled: agent.enabled ?? true,
    prompt: agent.prompt ?? '',
  }
}

function openAgent(agentId: string): void {
  router.push({ name: 'arena-agent-detail', params: { agentId } })
}

function focusAgentConfig(agentId: string): void {
  selectedConfigAgentId.value = agentId
  nextTick(() => {
    const element = document.getElementById(agentEditorId(agentId))
    element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    element?.querySelector('input')?.focus()
  })
}

function agentEditorId(agentId: string): string {
  return `arena-agent-editor-${agentId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
}

function styleText(style: string): string {
  if (style === 'momentum') return '短线动量'
  if (style === 'risk_control') return '长线稳健'
  return '量化轮动'
}

function scoreText(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(1) : '--'
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

onMounted(() => {
  loadArenaState()
})
</script>

<style scoped>
.arena-home {
  display: grid;
  gap: 18px;
}

.arena-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
}

.arena-hero h1 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 30px;
  line-height: 1.1;
}

.arena-hero p,
.arena-muted {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.arena-actions,
.arena-editor-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.arena-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
  align-items: start;
}

.arena-agent-list,
.arena-agent-editor-list {
  display: grid;
  gap: 10px;
}

.arena-agent-row {
  position: relative;
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) minmax(280px, 0.8fr);
  gap: 16px;
  align-items: center;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #ffffff;
  padding: 14px 88px 14px 14px;
  cursor: pointer;
}

.arena-card-config {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 1;
}

.arena-agent-row:hover {
  border-color: #cfd4dc;
  background: #fbfbfc;
}

.arena-rank {
  color: #6b7280;
  font-weight: 650;
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

.arena-pick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.arena-pick-chips span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #f9fafb;
  color: #374151;
  padding: 4px 8px;
  font-size: 12px;
}

.arena-agent-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.arena-agent-metrics div {
  display: grid;
  gap: 4px;
}

.arena-agent-metrics strong {
  color: #111827;
  font-size: 14px;
}

.arena-agent-editor {
  display: grid;
  gap: 7px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px;
  color: #6b7280;
  font-size: 12px;
}

.arena-agent-editor.is-selected {
  border-color: #111827;
  box-shadow: 0 0 0 3px rgba(17, 24, 39, 0.08);
}

.arena-agent-editor input,
.arena-agent-editor select {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 9px;
  background: #ffffff;
  color: #111827;
  padding: 8px 10px;
}

.arena-enable {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #374151;
}

.arena-save-button {
  width: 100%;
  margin-top: 12px;
}

@media (max-width: 1100px) {
  .arena-layout,
  .arena-agent-row {
    grid-template-columns: 1fr;
  }
}
</style>
