<template>
  <div class="quant-page">
    <section class="panel quant-hero">
      <div>
        <h1>量化研究</h1>
        <p>独立研究候选因子和回测结果，不影响股票分析候选池，也不影响 AI 竞技场。</p>
      </div>
      <button class="button primary small" :disabled="loading" @click="loadCandidates">
        {{ loading ? '计算中...' : '生成量化候选' }}
      </button>
    </section>

    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="quant-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>因子候选</h2>
            <p class="quant-muted">基于行情、日线、资金、估值、龙虎榜等已有数据。</p>
          </div>
          <label class="quant-limit">
            数量
            <input v-model.number="limit" min="5" max="50" type="number" />
          </label>
        </div>

        <div class="quant-candidate-list">
          <article v-for="candidate in candidates" :key="candidate.symbol">
            <div>
              <strong>{{ candidate.name || candidate.symbol }}</strong>
              <span>{{ candidate.symbol }} · {{ candidate.profile?.industry || 'A股' }}</span>
            </div>
            <div>
              <b>{{ scoreText(candidate.score) }}</b>
              <span :class="profitClass(candidate.change_pct)">{{ formatPercent(candidate.change_pct / 100) }}</span>
            </div>
            <p>{{ candidate.rationale }}</p>
          </article>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>回测实验</h2>
            <p class="quant-muted">输入股票池，验证日线动量策略表现。</p>
          </div>
          <button class="button primary small" :disabled="backtesting" @click="runBacktest">
            {{ backtesting ? '回测中...' : '运行回测' }}
          </button>
        </div>
        <div class="quant-form">
          <label>
            股票池
            <textarea v-model="symbolsText" rows="3" placeholder="600519.SH, 300750.SZ"></textarea>
          </label>
          <label>
            开始日期
            <input v-model="startDate" placeholder="YYYYMMDD" />
          </label>
          <label>
            结束日期
            <input v-model="endDate" placeholder="YYYYMMDD" />
          </label>
        </div>
        <div v-if="backtest" class="quant-backtest">
          <div>
            <span>最终资产</span>
            <strong>{{ formatAmount(backtest.final_assets) }}</strong>
          </div>
          <div>
            <span>收益</span>
            <strong :class="profitClass(backtest.return_ratio)">{{ formatPercent(backtest.return_ratio) }}</strong>
          </div>
          <div>
            <span>最大回撤</span>
            <strong>{{ formatPercent(backtest.max_drawdown) }}</strong>
          </div>
          <div>
            <span>交易数</span>
            <strong>{{ backtest.trade_count }}</strong>
          </div>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { api } from '@/services/api'
import type { BacktestPayload, QuantCandidate } from '@/types'

const limit = ref(20)
const candidates = ref<QuantCandidate[]>([])
const loading = ref(false)
const backtesting = ref(false)
const errorMessage = ref('')
const symbolsText = ref('600519.SH,300750.SZ,000001.SZ')
const startDate = ref('20240101')
const endDate = ref('20241231')
const backtest = ref<BacktestPayload | null>(null)

async function loadCandidates(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const payload = await api.generateQuantCandidates({
      limit: Math.min(50, Math.max(5, limit.value || 20)),
      prefer_realtime: true,
      lookback_days: 1825,
    })
    candidates.value = payload.candidates
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '量化候选生成失败。'
  } finally {
    loading.value = false
  }
}

async function runBacktest(): Promise<void> {
  const symbols = parseSymbols(symbolsText.value)
  if (!symbols.length) {
    errorMessage.value = '请输入至少一支股票。'
    return
  }
  backtesting.value = true
  errorMessage.value = ''
  try {
    backtest.value = await api.runBacktest({
      symbols,
      start_date: startDate.value,
      end_date: endDate.value,
      initial_cash: 200000,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回测失败。'
  } finally {
    backtesting.value = false
  }
}

function parseSymbols(value: string): string[] {
  return value.split(/[\s,，;；]+/).map((item) => item.trim().toUpperCase()).filter(Boolean)
}

function scoreText(value: number | undefined): string {
  return typeof value === 'number' ? value.toFixed(1) : '--'
}

function profitClass(value: number): string {
  if (value > 0) return 'profit-up'
  if (value < 0) return 'profit-down'
  return ''
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatAmount(value: number): string {
  if (Math.abs(value) >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(2)}万`
  return value.toFixed(2)
}
</script>

<style scoped>
.quant-page {
  display: grid;
  gap: 14px;
}

.quant-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.quant-hero h1 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 24px;
  line-height: 1.1;
}

.quant-hero p,
.quant-muted,
.quant-candidate-list span,
.quant-candidate-list p,
.quant-backtest span {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.quant-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(340px, 0.75fr);
  gap: 14px;
  align-items: start;
}

.quant-limit,
.quant-form label {
  display: grid;
  gap: 7px;
  color: #6b7280;
  font-size: 12px;
}

.quant-limit input,
.quant-form input,
.quant-form textarea {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 9px;
  background: #ffffff;
  color: #111827;
  padding: 8px 10px;
}

.quant-limit input {
  width: 74px;
}

.quant-candidate-list,
.quant-form {
  display: grid;
  gap: 8px;
}

.quant-candidate-list article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 104px;
  gap: 8px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}

.quant-candidate-list article > p {
  grid-column: 1 / -1;
}

.quant-candidate-list div,
.quant-backtest div {
  display: grid;
  gap: 4px;
}

.quant-candidate-list article > div:last-of-type {
  justify-items: end;
}

.quant-candidate-list strong,
.quant-candidate-list b,
.quant-backtest strong {
  color: #111827;
}

.quant-backtest {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.quant-backtest div {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}

@media (max-width: 980px) {
  .quant-hero,
  .quant-grid {
    grid-template-columns: 1fr;
  }

  .quant-hero {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
