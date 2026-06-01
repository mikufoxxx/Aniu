<template>
  <div class="stock-analysis-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="analysis-grid">
      <section class="panel stock-candidate-panel">
        <div class="panel-head stock-panel-head">
          <div class="stock-panel-title">
            <h2>候选池</h2>
            <p class="analysis-muted">不会自动运行；不影响 AI 竞技场任何 AI 的候选池。</p>
          </div>
          <div class="analysis-controls">
            <label>
              候选数量
              <input v-model.number="candidateLimit" min="1" max="5" type="number" />
            </label>
            <button class="button primary small" :disabled="loadingCandidates" @click="loadCandidates">
              <span class="material-symbols-rounded" aria-hidden="true">auto_awesome</span>
              {{ loadingCandidates ? '选股中...' : '帮我选' }}
            </button>
          </div>
        </div>

        <label class="analysis-field">
          <span>手动股票池</span>
          <textarea
            v-model="symbolText"
            rows="3"
            placeholder="例如：600519.SH, 300750.SZ；留空则自动全市场筛选"
          ></textarea>
        </label>

        <div class="candidate-list">
          <button
            v-for="candidate in candidates"
            :key="candidate.symbol"
            class="candidate-row"
            :class="{ selected: selectedSymbol === candidate.symbol }"
            type="button"
            @click="selectCandidate(candidate.symbol)"
          >
            <div class="candidate-main">
              <strong>{{ candidate.name || candidate.symbol }}</strong>
              <span>{{ candidate.symbol }} · {{ candidate.profile?.industry || candidate.profile?.market || 'A股' }}</span>
              <small>{{ candidate.rationale || '暂无候选理由' }}</small>
            </div>
            <div class="candidate-side">
              <strong>{{ scoreText(candidate.score) }}</strong>
              <span>{{ formatPrice(candidate.price) }}</span>
              <span :class="profitClass(candidate.change_pct)">{{ formatPercent(candidate.change_pct / 100) }}</span>
            </div>
          </button>
          <div v-if="!candidates.length" class="empty-state">
            <p>点击“帮我选”后才会生成候选池。</p>
          </div>
        </div>
      </section>

      <section class="panel stock-ai-panel">
        <div class="panel-head stock-panel-head">
          <div class="stock-panel-title">
            <h2>AI 分析</h2>
            <p class="analysis-muted">选择股票后生成交易动作、理由和数据源。</p>
          </div>
          <div class="analysis-ai-controls">
            <label class="analysis-model-field">
              模型
              <select v-model="selectedModel">
                <option value="">默认</option>
                <option v-for="model in aiConfig.models" :key="model" :value="model">
                  {{ model }}
                </option>
              </select>
            </label>
            <label class="analysis-skill-field">
              Skill
              <div class="analysis-skill-picker">
                <button
                  v-for="skill in analysisSkills"
                  :key="skill.id"
                  class="analysis-skill-chip"
                  :class="{ selected: isSkillSelected(skill.id) }"
                  type="button"
                  @click="toggleSkill(skill.id)"
                >
                  <span class="material-symbols-rounded" aria-hidden="true">
                    {{ isSkillSelected(skill.id) ? 'check_circle' : 'radio_button_unchecked' }}
                  </span>
                  {{ skill.name }}
                </button>
                <span v-if="!analysisSkills.length" class="analysis-skill-empty">暂无可用 Skill</span>
              </div>
            </label>
            <button class="button primary small" :disabled="!selectedSymbol || analyzing" @click="analyzeSelected">
              <span class="material-symbols-rounded" aria-hidden="true">analytics</span>
              {{ analyzing ? '分析中...' : '分析选中股票' }}
            </button>
          </div>
        </div>

        <div v-if="analysis" class="analysis-result">
          <div class="analysis-result-head">
            <div>
              <strong>{{ analysis.name || analysis.symbol }}</strong>
              <span>{{ analysis.symbol }} · {{ analysis.rating }}</span>
            </div>
            <b :class="actionClass(analysis.action)">{{ analysis.action }}</b>
          </div>
          <div class="analysis-metrics">
            <div>
              <span>价格</span>
              <strong>{{ formatPrice(analysis.price) }}</strong>
            </div>
            <div>
              <span>评分</span>
              <strong>{{ scoreText(analysis.score) }}</strong>
            </div>
            <div>
              <span>数据源</span>
              <strong>{{ analysis.data_sources.length }}</strong>
            </div>
          </div>
          <div class="analysis-config-strip">
            <span>模型 {{ analysis.analysis_config.selected_model || selectedModel || '--' }}</span>
            <span>Skill {{ selectedSkillNames(analysis.analysis_config.selected_skills).join(' / ') || '未指定' }}</span>
            <span>接口 {{ analysis.analysis_config.ai_config?.base_url || aiConfig.base_url || '--' }}</span>
            <span>Key {{ analysis.analysis_config.ai_config?.api_key_configured ? analysis.analysis_config.ai_config.api_key_masked : '未配置' }}</span>
          </div>
          <button
            v-if="analysis.analysis_report"
            class="analysis-report-card"
            type="button"
            @click="openReport(analysis.analysis_report.id)"
          >
            <span class="material-symbols-rounded" aria-hidden="true">article</span>
            <div>
              <strong>{{ analysis.analysis_report.title }}</strong>
              <small>{{ analysis.analysis_report.summary }}</small>
            </div>
            <span class="material-symbols-rounded" aria-hidden="true">chevron_right</span>
          </button>
          <div v-if="retailAnalysis" class="retail-analysis">
            <div class="retail-analysis-head">
              <strong>散户 A 股分析</strong>
              <span>{{ retailText('action_signal') }} · 风险 {{ retailText('risk_level') }}</span>
            </div>
            <div class="retail-dimensions">
              <span v-for="item in retailDimensions" :key="item.key">
                {{ item.label }} {{ scoreText(item.score) }}
              </span>
            </div>
            <ul>
              <li v-for="reason in retailList('key_reasons')" :key="reason">{{ reason }}</li>
              <li v-for="warning in retailList('warnings')" :key="warning">{{ warning }}</li>
            </ul>
          </div>
          <p>{{ analysis.reason }}</p>
          <div class="analysis-chart-grid">
            <MarketKlineChart
              title="价格走势与 AI 信号"
              :subtitle="`${analysis.name} ${analysis.symbol}`"
              :price-series="analysis.charts.price_series"
              :interval-series="analysis.charts.interval_series"
              :forecast-series="analysis.charts.forecast_series"
              :markers="analysis.charts.signal_markers"
            />
            <FactorRadarChart
              title="量化因子雷达"
              subtitle="用于解释 AI 评分结构"
              :items="analysis.charts.factor_radar"
            />
            <TechnicalIndicatorChart
              title="MACD / RSI 技术指标"
              subtitle="趋势强弱与超买超卖"
              :price-series="analysis.charts.price_series"
            />
            <DistributionChart
              title="收益分布"
              subtitle="观察波动是否偏态"
              :items="analysis.charts.return_distribution"
            />
            <VolumeProfileChart
              title="成交价量分布"
              subtitle="识别筹码密集区"
              :items="analysis.charts.volume_profile"
            />
          </div>
          <div class="support-grid">
            <div>
              <span>支撑</span>
              <strong>{{ formatPrice(analysis.charts.support_resistance.support) }}</strong>
            </div>
            <div>
              <span>压力</span>
              <strong>{{ formatPrice(analysis.charts.support_resistance.resistance) }}</strong>
            </div>
            <div>
              <span>最新收盘</span>
              <strong>{{ formatPrice(analysis.charts.support_resistance.last_close) }}</strong>
            </div>
          </div>
          <div class="source-chips">
            <span v-for="source in analysis.data_sources" :key="source">{{ source }}</span>
          </div>
        </div>

        <div v-else class="empty-state">
          <p>先从左侧候选池选择一支股票。</p>
        </div>
      </section>
    </section>

    <section class="panel analysis-history-panel">
      <div class="panel-head stock-panel-head">
        <div class="stock-panel-title">
          <h2>分析报告</h2>
          <p class="analysis-muted">已保存的股票分析结果，点击卡片查看完整报告。</p>
        </div>
        <button class="button ghost small" type="button" @click="loadAnalysisWorkspace">
          <span class="material-symbols-rounded" aria-hidden="true">sync</span>
          刷新
        </button>
      </div>
      <div class="analysis-report-list">
        <button
          v-for="report in reports"
          :key="report.id"
          class="analysis-saved-card"
          type="button"
          @click="openReport(report.id)"
        >
          <div>
            <strong>{{ report.title }}</strong>
            <span>{{ report.symbol }} · {{ report.model || '默认模型' }} · {{ formatDate(report.created_at) }}</span>
            <small>{{ report.summary || report.rating }}</small>
          </div>
          <b :class="actionClass(report.action)">{{ report.action }}</b>
        </button>
        <div v-if="!reports.length" class="empty-state">
          <p>暂无保存的股票分析报告。</p>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { ForecastAIConfig, QuantCandidate, SkillListItem, StockAnalysisPayload, StockAnalysisReportSummary } from '@/types'
import DistributionChart from '@/components/charts/DistributionChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import FactorRadarChart from '@/components/charts/FactorRadarChart.vue'
import TechnicalIndicatorChart from '@/components/charts/TechnicalIndicatorChart.vue'
import VolumeProfileChart from '@/components/charts/VolumeProfileChart.vue'

const router = useRouter()
const candidateLimit = ref(5)
const symbolText = ref('')
const candidates = ref<QuantCandidate[]>([])
const selectedSymbol = ref('')
const analysis = ref<StockAnalysisPayload | null>(null)
const reports = ref<StockAnalysisReportSummary[]>([])
const loadingCandidates = ref(false)
const analyzing = ref(false)
const chartRefreshing = ref(false)
const errorMessage = ref('')
const aiConfig = ref<ForecastAIConfig>({
  base_url: '',
  api_key_configured: false,
  api_key_masked: '未配置',
  models: [],
  model_count: 0,
})
const skills = ref<SkillListItem[]>([])
const selectedModel = ref('')
const selectedSkillIds = ref<string[]>([])
let chartRefreshTimer: number | null = null

interface RetailDimension {
  key: string
  label: string
  score?: number
}

const retailAnalysis = computed<Record<string, unknown> | null>(() => analysis.value?.retail_analysis ?? null)
const retailDimensions = computed<RetailDimension[]>(() => {
  const items = retailAnalysis.value?.dimension_scores
  return Array.isArray(items) ? items as RetailDimension[] : []
})
const analysisSkills = computed(() => {
  return skills.value.filter((skill) => {
    if (!skill.enabled || skill.role === 'runtime') return false
    return !skill.run_types.length || skill.run_types.includes('analysis')
  })
})

async function loadAnalysisWorkspace(): Promise<void> {
  try {
    const payload = await api.getStockAnalysisWorkspace({ limit: 20 })
    aiConfig.value = payload.ai_config
    skills.value = payload.skills
    reports.value = payload.reports.items
    selectedModel.value ||= payload.ai_config.models[0] ?? ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分析配置加载失败。'
  }
}

function parseSymbols(): string[] {
  return symbolText.value
    .split(/[\s,，;；]+/)
    .map((symbol) => symbol.trim().toUpperCase())
    .filter(Boolean)
}

async function loadCandidates(): Promise<void> {
  loadingCandidates.value = true
  errorMessage.value = ''
  try {
    const payload = await api.buildAIStockPicks({
      symbols: parseSymbols(),
      limit: Math.min(5, Math.max(1, candidateLimit.value || 5)),
      prefer_realtime: true,
      lookback_days: 90,
    })
    candidates.value = payload.recommendations
    selectedSymbol.value = candidates.value[0]?.symbol ?? ''
    analysis.value = null
    stopChartAutoRefresh()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 选股失败。'
  } finally {
    loadingCandidates.value = false
  }
}

function selectCandidate(symbol: string): void {
  selectedSymbol.value = symbol
}

function isSkillSelected(skillId: string): boolean {
  return selectedSkillIds.value.includes(skillId)
}

function toggleSkill(skillId: string): void {
  selectedSkillIds.value = isSkillSelected(skillId)
    ? selectedSkillIds.value.filter((item) => item !== skillId)
    : [...selectedSkillIds.value, skillId]
}

function selectedSkillNames(skillsPayload: StockAnalysisPayload['analysis_config']['selected_skills'] = []): string[] {
  return (skillsPayload ?? []).map((skill) => skill.name).filter(Boolean)
}

function openReport(reportId: number): void {
  router.push({ name: 'stock-analysis-report', params: { reportId: String(reportId) } })
}

async function analyzeSelected(): Promise<void> {
  if (!selectedSymbol.value) return
  analyzing.value = true
  errorMessage.value = ''
  try {
    analysis.value = await api.analyzeStock({
      symbol: selectedSymbol.value,
      initial_cash: 200000,
      model: selectedModel.value || undefined,
      skill_ids: selectedSkillIds.value,
    })
    if (analysis.value.analysis_report) {
      reports.value = [
        {
          id: analysis.value.analysis_report.id,
          symbol: analysis.value.analysis_report.symbol,
          name: analysis.value.analysis_report.name,
          title: analysis.value.analysis_report.title,
          model: analysis.value.analysis_report.model,
          action: analysis.value.analysis_report.action,
          rating: analysis.value.analysis_report.rating,
          summary: analysis.value.analysis_report.summary,
          selected_skills: analysis.value.analysis_report.selected_skills,
          created_at: analysis.value.analysis_report.created_at,
        },
        ...reports.value.filter((item) => item.id !== analysis.value?.analysis_report.id),
      ].slice(0, 20)
    }
    startChartAutoRefresh()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '股票分析失败。'
  } finally {
    analyzing.value = false
  }
}

function analysisFactorScores(): Record<string, number> {
  if (!analysis.value) return {}
  return Object.fromEntries(
    analysis.value.charts.factor_radar.map((item) => [item.key, item.value]),
  )
}

async function refreshAnalysisCharts(): Promise<void> {
  if (!analysis.value || chartRefreshing.value || analyzing.value) return
  chartRefreshing.value = true
  try {
    const payload = await api.refreshStockAnalysisCharts({
      symbol: analysis.value.symbol,
      action: analysis.value.action,
      price: analysis.value.price,
      quantity: Number(analysis.value.decision.suggested_quantity || 0),
      factor_scores: analysisFactorScores(),
    })
    analysis.value = {
      ...analysis.value,
      price: payload.latest_price ?? analysis.value.price,
      charts: payload.charts,
    }
  } catch {
    // 图表低频刷新失败不覆盖已有 AI 分析结果。
  } finally {
    chartRefreshing.value = false
  }
}

function startChartAutoRefresh(): void {
  stopChartAutoRefresh()
  chartRefreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void refreshAnalysisCharts()
  }, 30000)
}

function stopChartAutoRefresh(): void {
  if (!chartRefreshTimer) return
  window.clearInterval(chartRefreshTimer)
  chartRefreshTimer = null
}

function actionClass(action: string): string {
  if (action === 'BUY') return 'action-buy'
  if (action === 'SELL') return 'action-sell'
  return 'action-hold'
}

function profitClass(value: number): string {
  if (value > 0) return 'profit-up'
  if (value < 0) return 'profit-down'
  return ''
}

function scoreText(value: number | undefined): string {
  return typeof value === 'number' ? value.toFixed(1) : '--'
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatPrice(value: number | null | undefined): string {
  if (typeof value !== 'number') return '--'
  return value.toFixed(value >= 100 ? 2 : 3)
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 16)
}

function retailText(key: string): string {
  const value = retailAnalysis.value?.[key]
  return typeof value === 'string' && value.trim() ? value : '--'
}

function retailList(key: string): string[] {
  const value = retailAnalysis.value?.[key]
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).slice(0, 4) : []
}

onMounted(() => {
  loadAnalysisWorkspace()
})

onBeforeUnmount(() => {
  stopChartAutoRefresh()
})
</script>

<style scoped>
.stock-analysis-page {
  display: grid;
  gap: 16px;
}

.stock-analysis-page .panel {
  min-width: 0;
  border-color: #e4e4e1;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.02);
}

.stock-panel-head {
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 16px;
}

.stock-panel-title {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.stock-panel-title h2 {
  color: #202123;
  font-size: 16px;
  line-height: 1.25;
  white-space: nowrap;
}

.stock-ai-panel .stock-panel-head {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
}

.stock-candidate-panel .stock-panel-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
}

.analysis-hero,
.analysis-result-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.analysis-hero h1 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 24px;
  line-height: 1.1;
}

.analysis-hero p,
.analysis-muted,
.candidate-row span,
.candidate-row small,
.analysis-result span,
.analysis-result p {
  margin: 0;
  color: #6f6f6f;
  font-size: 12px;
}

.analysis-controls {
  display: flex;
  align-items: end;
  gap: 8px;
  flex: 0 0 auto;
}

.analysis-controls .button {
  min-width: 108px;
  height: 42px;
  white-space: nowrap;
}

.analysis-ai-controls {
  display: grid;
  grid-template-columns: minmax(150px, 190px) minmax(160px, 1fr);
  grid-template-areas:
    "model action"
    "skills skills";
  gap: 12px 14px;
  align-items: end;
  width: 100%;
  min-width: 0;
}

.analysis-model-field {
  grid-area: model;
}

.analysis-skill-field {
  grid-area: skills;
}

.analysis-ai-controls .button {
  grid-area: action;
  justify-self: end;
  min-width: 142px;
  height: 40px;
  white-space: nowrap;
}

.analysis-controls label,
.analysis-ai-controls label,
.analysis-field {
  display: grid;
  gap: 7px;
  color: #6f6f6f;
  font-size: 12px;
  font-weight: 500;
}

.analysis-controls input,
.analysis-ai-controls select,
.analysis-field textarea {
  width: 100%;
  border: 1px solid #dededb;
  border-radius: 10px;
  background: #ffffff;
  color: #111827;
  padding: 9px 10px;
  outline: none;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
}

.analysis-controls input:focus,
.analysis-ai-controls select:focus,
.analysis-field textarea:focus {
  border-color: #a8a8a2;
  box-shadow: 0 0 0 3px rgba(32, 33, 35, 0.06);
}

.analysis-skill-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
  max-width: 100%;
}

.analysis-skill-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  border: 1px solid #e4e4e1;
  border-radius: 999px;
  background: #ffffff;
  color: #4b5563;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 520;
  line-height: 1.1;
  white-space: nowrap;
  transition: background 0.16s ease, border-color 0.16s ease, color 0.16s ease;
}

.analysis-skill-chip:hover {
  border-color: #cfcfca;
  background: #f7f7f5;
  color: #202123;
}

.analysis-skill-chip.selected {
  border-color: #202123;
  background: #202123;
  color: #ffffff;
}

.analysis-skill-chip .material-symbols-rounded {
  font-size: 15px;
}

.analysis-skill-empty {
  color: #9ca3af;
  font-size: 12px;
}

.analysis-history-panel {
  align-self: start;
}

.analysis-report-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.analysis-saved-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: start;
  border: 1px solid #ececf1;
  border-radius: 10px;
  background: #ffffff;
  padding: 12px;
  text-align: left;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.analysis-saved-card:hover {
  border-color: #d8d8d3;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
  transform: translateY(-1px);
}

.analysis-saved-card div {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.analysis-saved-card strong,
.analysis-saved-card span,
.analysis-saved-card small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.analysis-saved-card strong {
  color: #202123;
  font-size: 14px;
}

.analysis-saved-card span,
.analysis-saved-card small {
  color: #6f6f6f;
  font-size: 12px;
}

.analysis-saved-card b {
  border-radius: 999px;
  padding: 5px 8px;
  font-size: 12px;
}

.analysis-controls input {
  width: 86px;
}

.analysis-field textarea {
  min-height: 64px;
}

.stock-analysis-page .empty-state {
  display: grid;
  min-height: 92px;
  place-items: center;
  padding: 20px;
  color: #8a8a85;
}

.analysis-grid {
  display: grid;
  grid-template-columns: minmax(420px, 0.95fr) minmax(460px, 1.05fr);
  gap: 14px;
  align-items: start;
}

.candidate-list {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}

.candidate-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  gap: 10px;
  border: 1px solid #e4e4e1;
  border-radius: 10px;
  background: #ffffff;
  padding: 12px;
  text-align: left;
  transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}

.candidate-row.selected,
.candidate-row:hover {
  border-color: #c7c7c2;
  background: #fafaf9;
  box-shadow: none;
}

.candidate-main,
.candidate-side,
.analysis-result-head div,
.analysis-metrics div {
  display: grid;
  gap: 4px;
}

.candidate-side {
  justify-items: end;
  align-content: start;
}

.candidate-row strong,
.analysis-result strong {
  color: #111827;
}

.analysis-result {
  display: grid;
  gap: 12px;
  border-top: 1px solid #ececea;
  padding-top: 16px;
}

.analysis-result-head b {
  border-radius: 999px;
  padding: 7px 10px;
  font-size: 12px;
}

.analysis-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.analysis-config-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.analysis-config-strip span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #ffffff;
  color: #374151;
  padding: 5px 8px;
  font-size: 12px;
}

.analysis-report-card {
  width: 100%;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 20px;
  gap: 10px;
  align-items: center;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fbfbfc;
  color: #111827;
  padding: 12px;
  text-align: left;
}

.analysis-report-card:hover {
  border-color: #111827;
  background: #ffffff;
}

.analysis-report-card div {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.analysis-report-card small {
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.analysis-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(260px, 0.55fr);
  gap: 10px;
  align-items: start;
}

.analysis-chart-grid > :first-child {
  grid-row: span 2;
}

.support-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.support-grid div {
  display: grid;
  gap: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}

.support-grid span {
  color: #6b7280;
  font-size: 12px;
}

.support-grid strong {
  color: #111827;
}

.analysis-metrics div {
  border: 1px solid #e4e4e1;
  border-radius: 10px;
  padding: 12px;
}

.source-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.source-chips span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #f9fafb;
  color: #374151;
  padding: 5px 9px;
  font-size: 12px;
}

.retail-analysis {
  display: grid;
  gap: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 10px;
}

.retail-analysis-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.retail-dimensions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.retail-dimensions span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #ffffff;
  padding: 4px 8px;
}

.retail-analysis ul {
  margin: 0;
  padding-left: 18px;
  color: #374151;
  font-size: 13px;
}

.action-buy {
  background: #ecfdf5;
  color: #047857;
}

.action-sell {
  background: #fff1f2;
  color: #be123c;
}

.action-hold {
  background: #f3f4f6;
  color: #374151;
}

@media (max-width: 980px) {
  .analysis-hero,
  .analysis-grid,
  .analysis-chart-grid,
  .analysis-report-list,
  .support-grid {
    grid-template-columns: 1fr;
  }

  .analysis-chart-grid > :first-child {
    grid-row: auto;
  }

  .analysis-hero,
  .analysis-controls {
    align-items: flex-start;
    flex-direction: column;
  }

  .analysis-ai-controls {
    grid-template-columns: 1fr;
    width: 100%;
  }

  .analysis-controls .button {
    min-width: 160px;
  }
}
</style>
