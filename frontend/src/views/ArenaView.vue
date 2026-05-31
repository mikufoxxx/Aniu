<template>
  <div class="arena-home">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="arena-layout">
      <section class="panel arena-main-panel">
        <div class="panel-head">
          <div>
            <h2>AI 收益排名</h2>
            <p class="arena-muted">按总资产排序；点击卡片进入 AI 的早盘、盘中、收盘、学习详情。</p>
          </div>
          <div class="arena-panel-actions">
            <button class="button ghost small" :disabled="saving" @click="addAgent">
              <span class="material-symbols-rounded" aria-hidden="true">person_add</span>
              新增 AI
            </button>
            <button class="button ghost small" :disabled="loading" @click="loadArenaState">
              <span class="material-symbols-rounded" aria-hidden="true">sync</span>
              刷新
            </button>
          </div>
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
              @click.stop="openAgentConfig(agent.id)"
            >
              <span class="material-symbols-rounded" aria-hidden="true">settings</span>
              配置
            </button>
            <div class="arena-rank">#{{ index + 1 }}</div>
            <div class="arena-agent-main">
              <div class="arena-agent-title">
                <strong>{{ agent.name }}</strong>
                <span>{{ styleText(agent.style) }} · {{ agent.model || '默认模型' }}</span>
                <div class="arena-model-meta">
                  <span>
                    <span class="material-symbols-rounded" aria-hidden="true">hub</span>
                    {{ agent.provider || 'openai-compatible' }}
                  </span>
                  <span>
                    <span class="material-symbols-rounded" aria-hidden="true">link</span>
                    {{ aiConfig.base_url || '--' }}
                  </span>
                  <span>
                    <span class="material-symbols-rounded" aria-hidden="true">key</span>
                    {{ aiConfig.api_key_configured ? aiConfig.api_key_masked : '未配置' }}
                  </span>
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
            </div>
          </article>
        </div>
      </section>

    </section>

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
            <span>风格</span>
            <select v-model="configAgent.style">
              <option value="momentum">短线动量</option>
              <option value="balanced">量化轮动</option>
              <option value="risk_control">长线稳健</option>
            </select>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { ArenaAgentConfig, ArenaAgentDashboardPayload, ArenaAIConfigPayload, ArenaLeaderboardPayload } from '@/types'

const router = useRouter()
const agents = ref<ArenaAgentConfig[]>([])
const dashboards = ref<Record<string, ArenaAgentDashboardPayload>>({})
const leaderboard = ref<ArenaLeaderboardPayload>({ items: [] })
const aiConfig = ref<ArenaAIConfigPayload>({
  base_url: '',
  api_key_configured: false,
  api_key_masked: '未配置',
  models: [],
  model_count: 0,
})
const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const configAgentIndex = ref(-1)

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
    const [agentPayload, leaderboardPayload, configPayload] = await Promise.all([
      api.getArenaAgents(),
      api.getArenaLeaderboard(),
      api.getArenaAIConfig(),
    ])
    agents.value = agentPayload.agents.map(normalizeAgent)
    leaderboard.value = leaderboardPayload
    aiConfig.value = configPayload
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
    style: 'balanced',
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

function styleText(style: string): string {
  if (style === 'momentum') return '短线动量'
  if (style === 'risk_control') return '长线稳健'
  return '量化轮动'
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

onMounted(() => {
  loadArenaState()
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

.arena-layout {
  display: block;
}

.arena-agent-list {
  display: grid;
  gap: 10px;
}

.arena-agent-row {
  position: relative;
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) minmax(260px, 0.75fr);
  gap: 12px;
  align-items: center;
  border: 1px solid #ececf1;
  border-radius: 12px;
  background: #ffffff;
  padding: 12px 82px 12px 12px;
  cursor: pointer;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.arena-card-config {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 1;
}

.arena-agent-row:hover {
  border-color: #dededb;
  background: #fcfcfb;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
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

.arena-model-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.arena-model-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #ececf1;
  border-radius: 999px;
  background: #f7f7f5;
  color: #4b5563;
  padding: 4px 7px;
  font-size: 11px;
  line-height: 1;
}

.arena-model-meta .material-symbols-rounded {
  font-size: 14px;
}

.arena-pick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.arena-pick-chips span {
  display: grid;
  gap: 2px;
  border: 1px solid #ececf1;
  border-radius: 10px;
  background: #f7f7f5;
  color: #374151;
  padding: 6px 8px;
  font-size: 12px;
  min-width: 148px;
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
}

.arena-config-modal {
  width: min(460px, 100%);
  max-height: calc(100vh - 40px);
  overflow: auto;
}

.arena-modal-actions {
  justify-content: flex-end;
  margin-top: 12px;
}

@media (max-width: 1100px) {
  .arena-layout,
  .arena-agent-row {
    grid-template-columns: 1fr;
  }
}
</style>
