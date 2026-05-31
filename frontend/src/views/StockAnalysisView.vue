<template>
  <div class="stock-analysis-page">
    <section class="panel analysis-hero">
      <div>
        <h1>股票分析</h1>
        <p>先让系统自动选出 1-5 支候选，再选择其中一支生成 AI 分析。</p>
      </div>
      <div class="analysis-controls">
        <label>
          候选数量
          <input v-model.number="candidateLimit" min="1" max="5" type="number" />
        </label>
        <button class="button primary small" :disabled="loadingCandidates" @click="loadCandidates">
          {{ loadingCandidates ? '选股中...' : 'AI 选股' }}
        </button>
      </div>
    </section>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="analysis-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>候选池</h2>
            <p class="analysis-muted">默认自动从数据集里挑选，也可以手动限定代码范围。</p>
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
            <div>
              <strong>{{ candidate.name || candidate.symbol }}</strong>
              <span>{{ candidate.symbol }} · {{ candidate.source || 'mixed' }}</span>
            </div>
            <div>
              <strong>{{ scoreText(candidate.score) }}</strong>
              <span :class="profitClass(candidate.change_pct)">{{ formatPercent(candidate.change_pct / 100) }}</span>
            </div>
          </button>
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
          <p>{{ analysis.reason }}</p>
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
import { onMounted, ref } from 'vue'
import { api } from '@/services/api'
import type { QuantCandidate, StockAnalysisPayload } from '@/types'

const candidateLimit = ref(5)
const symbolText = ref('')
const candidates = ref<QuantCandidate[]>([])
const selectedSymbol = ref('')
const analysis = ref<StockAnalysisPayload | null>(null)
const loadingCandidates = ref(false)
const analyzing = ref(false)
const errorMessage = ref('')

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

onMounted(() => {
  loadCandidates()
})
</script>

<style scoped>
.stock-analysis-page {
  display: grid;
  gap: 18px;
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
  font-size: 26px;
  line-height: 1.1;
}

.analysis-hero p,
.analysis-muted,
.candidate-row span,
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
  gap: 18px;
  align-items: start;
}

.candidate-list {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.candidate-row {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #ffffff;
  padding: 14px;
  text-align: left;
}

.candidate-row.selected,
.candidate-row:hover {
  border-color: #111827;
  background: #f9fafb;
}

.candidate-row div,
.analysis-result-head div,
.analysis-metrics div {
  display: grid;
  gap: 4px;
}

.candidate-row strong,
.analysis-result strong {
  color: #111827;
}

.analysis-result {
  display: grid;
  gap: 16px;
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

.analysis-metrics div {
  border: 1px solid #e5e7eb;
  border-radius: 12px;
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
  .analysis-grid {
    grid-template-columns: 1fr;
  }

  .analysis-hero,
  .analysis-controls {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
