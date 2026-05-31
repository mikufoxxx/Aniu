<template>
  <div class="stock-analysis-page">
    <section class="panel analysis-hero">
      <div>
        <h1>股票分析</h1>
        <p>需要时点击“帮我选”，也可以手动输入股票池；这里的候选池只影响本页分析。</p>
      </div>
      <div class="analysis-controls">
        <label>
          候选数量
          <input v-model.number="candidateLimit" min="1" max="5" type="number" />
        </label>
        <button class="button primary small" :disabled="loadingCandidates" @click="loadCandidates">
          {{ loadingCandidates ? '选股中...' : '帮我选' }}
        </button>
      </div>
    </section>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="analysis-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>候选池</h2>
            <p class="analysis-muted">不会自动运行；不影响 AI 竞技场任何 AI 的候选池。</p>
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

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>AI 分析</h2>
            <p class="analysis-muted">选择股票后生成交易动作、理由和数据源。</p>
          </div>
          <button class="button primary small" :disabled="!selectedSymbol || analyzing" @click="analyzeSelected">
            {{ analyzing ? '分析中...' : '分析选中股票' }}
          </button>
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
              :markers="analysis.charts.signal_markers"
            />
            <FactorRadarChart
              title="量化因子雷达"
              subtitle="用于解释 AI 评分结构"
              :items="analysis.charts.factor_radar"
            />
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
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '@/services/api'
import type { QuantCandidate, StockAnalysisPayload } from '@/types'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import FactorRadarChart from '@/components/charts/FactorRadarChart.vue'

const candidateLimit = ref(5)
const symbolText = ref('')
const candidates = ref<QuantCandidate[]>([])
const selectedSymbol = ref('')
const analysis = ref<StockAnalysisPayload | null>(null)
const loadingCandidates = ref(false)
const analyzing = ref(false)
const errorMessage = ref('')

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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'AI 选股失败。'
  } finally {
    loadingCandidates.value = false
  }
}

function selectCandidate(symbol: string): void {
  selectedSymbol.value = symbol
}

async function analyzeSelected(): Promise<void> {
  if (!selectedSymbol.value) return
  analyzing.value = true
  errorMessage.value = ''
  try {
    analysis.value = await api.analyzeStock({
      symbol: selectedSymbol.value,
      initial_cash: 200000,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '股票分析失败。'
  } finally {
    analyzing.value = false
  }
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

function retailText(key: string): string {
  const value = retailAnalysis.value?.[key]
  return typeof value === 'string' && value.trim() ? value : '--'
}

function retailList(key: string): string[] {
  const value = retailAnalysis.value?.[key]
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean).slice(0, 4) : []
}
</script>

<style scoped>
.stock-analysis-page {
  display: grid;
  gap: 12px;
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
  color: #6b7280;
  font-size: 13px;
}

.analysis-controls {
  display: flex;
  align-items: end;
  gap: 10px;
}

.analysis-controls label,
.analysis-field {
  display: grid;
  gap: 8px;
  color: #6b7280;
  font-size: 12px;
}

.analysis-controls input,
.analysis-field textarea {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 9px;
  background: #ffffff;
  color: #111827;
  padding: 9px 10px;
}

.analysis-controls input {
  width: 88px;
}

.analysis-grid {
  display: grid;
  grid-template-columns: minmax(0, 0.95fr) minmax(0, 1.05fr);
  gap: 12px;
  align-items: start;
}

.candidate-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.candidate-row {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 112px;
  gap: 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #ffffff;
  padding: 10px;
  text-align: left;
}

.candidate-row.selected,
.candidate-row:hover {
  border-color: #111827;
  background: #f9fafb;
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

.analysis-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(260px, 0.55fr);
  gap: 10px;
  align-items: start;
}

.analysis-metrics div {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
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
  .analysis-chart-grid {
    grid-template-columns: 1fr;
  }

  .analysis-hero,
  .analysis-controls {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
