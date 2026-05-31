<template>
  <div class="data-lab-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="data-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>数据源</h2>
            <p class="data-muted">低频实时、日线和补充数据的使用边界。</p>
          </div>
          <button class="button ghost small" :disabled="loading" @click="loadDataLab">刷新状态</button>
        </div>
        <div class="source-list">
          <article v-for="source in sourceHealth?.sources || []" :key="source.id">
            <div>
              <strong>{{ source.name }}</strong>
              <span>{{ source.cadence }}</span>
            </div>
            <b>{{ source.status }}</b>
            <p>{{ source.risk }}</p>
          </article>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>覆盖率</h2>
            <p class="data-muted">判断是否适合早盘推荐、收盘复盘和回测。</p>
          </div>
        </div>
        <div v-if="coverage" class="coverage-metrics">
          <div>
            <span>总行数</span>
            <strong>{{ coverage.total_rows }}</strong>
          </div>
          <div>
            <span>股票数</span>
            <strong>{{ coverage.unique_symbols }}</strong>
          </div>
          <div>
            <span>最新交易日</span>
            <strong>{{ coverage.latest_trade_date || '--' }}</strong>
          </div>
          <div>
            <span>完整交易日</span>
            <strong>{{ coverage.most_complete_trade_date || '--' }}</strong>
          </div>
        </div>
        <p v-if="coverage" class="data-note">{{ coverage.refresh_suggestion.reason }}</p>
      </section>
    </section>

    <section class="data-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>维护任务</h2>
            <p class="data-muted">异步刷新历史数据、候选集和日报。</p>
          </div>
          <button class="button primary small" :disabled="maintenanceRunning" @click="startMaintenance">
            {{ maintenanceRunning ? '维护中...' : '启动维护' }}
          </button>
        </div>
        <div class="data-form">
          <label>
            结束日期
            <input v-model="maintenanceEndDate" placeholder="YYYYMMDD" />
          </label>
          <label>
            回看天数
            <input v-model.number="maintenanceLookbackDays" min="5" type="number" />
          </label>
          <label class="data-span-2">
            股票范围
            <textarea v-model="maintenanceSymbols" rows="2" placeholder="留空自动全市场；或填 600519.SH,300750.SZ"></textarea>
          </label>
        </div>
        <div v-if="maintenanceJob" class="job-card">
          <strong>{{ maintenanceJob.status }}</strong>
          <span>{{ maintenanceJob.progress?.phase || maintenanceJob.error || '等待任务结果' }}</span>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>回测</h2>
            <p class="data-muted">用 Tushare 历史数据验证量化规则。</p>
          </div>
          <button class="button primary small" :disabled="backtesting" @click="runBacktest">
            {{ backtesting ? '回测中...' : '运行回测' }}
          </button>
        </div>
        <div class="data-form">
          <label>
            开始日期
            <input v-model="backtestStartDate" placeholder="YYYYMMDD" />
          </label>
          <label>
            结束日期
            <input v-model="backtestEndDate" placeholder="YYYYMMDD" />
          </label>
          <label>
            初始资金
            <input v-model.number="initialCash" min="10000" type="number" />
          </label>
          <label class="data-span-2">
            股票代码
            <textarea v-model="backtestSymbols" rows="2" placeholder="600519.SH,300750.SZ"></textarea>
          </label>
        </div>
        <div v-if="backtestResult" class="backtest-card">
          <div>
            <span>最终资产</span>
            <strong>{{ formatAmount(backtestResult.final_assets) }}</strong>
          </div>
          <div>
            <span>收益</span>
            <strong :class="profitClass(backtestResult.return_ratio)">{{ formatPercent(backtestResult.return_ratio) }}</strong>
          </div>
          <div>
            <span>最大回撤</span>
            <strong>{{ formatPercent(backtestResult.max_drawdown) }}</strong>
          </div>
          <div>
            <span>交易数</span>
            <strong>{{ backtestResult.trade_count }}</strong>
          </div>
        </div>
      </section>
    </section>

    <section class="panel">
      <div class="panel-head">
        <div>
          <h2>最近维护记录</h2>
          <p class="data-muted">用于排查数据是否真的更新。</p>
        </div>
      </div>
      <div class="run-list">
        <article v-for="run in maintenanceRuns" :key="run.id">
          <strong>#{{ run.id }} {{ run.status }}</strong>
          <span>{{ run.created_at }} · {{ run.latest_trade_date || '--' }}</span>
          <p>{{ run.refresh_reason }}</p>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/services/api'
import type {
  BacktestPayload,
  MarketDataCoveragePayload,
  MarketDataMaintenanceJobPayload,
  MarketDataMaintenanceRunRecord,
  MarketSourceHealthPayload,
} from '@/types'

const sourceHealth = ref<MarketSourceHealthPayload | null>(null)
const coverage = ref<MarketDataCoveragePayload | null>(null)
const maintenanceRuns = ref<MarketDataMaintenanceRunRecord[]>([])
const maintenanceJob = ref<MarketDataMaintenanceJobPayload | null>(null)
const backtestResult = ref<BacktestPayload | null>(null)
const loading = ref(false)
const maintenanceRunning = ref(false)
const backtesting = ref(false)
const errorMessage = ref('')

const maintenanceEndDate = ref('')
const maintenanceLookbackDays = ref(90)
const maintenanceSymbols = ref('')
const backtestStartDate = ref('20240101')
const backtestEndDate = ref('20241231')
const backtestSymbols = ref('600519.SH,300750.SZ')
const initialCash = ref(200000)

const parsedMaintenanceSymbols = computed(() => parseSymbols(maintenanceSymbols.value))
const parsedBacktestSymbols = computed(() => parseSymbols(backtestSymbols.value))

function parseSymbols(value: string): string[] {
  return value
    .split(/[\s,，;；]+/)
    .map((symbol) => symbol.trim().toUpperCase())
    .filter(Boolean)
}

async function loadDataLab(): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const [healthPayload, coveragePayload, runPayload] = await Promise.all([
      api.getMarketSourceHealth(),
      api.getMarketDataCoverage(),
      api.listMarketDataMaintenanceRuns({ limit: 6 }),
    ])
    sourceHealth.value = healthPayload
    coverage.value = coveragePayload
    maintenanceRuns.value = runPayload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据实验室加载失败。'
  } finally {
    loading.value = false
  }
}

async function startMaintenance(): Promise<void> {
  maintenanceRunning.value = true
  errorMessage.value = ''
  try {
    maintenanceJob.value = await api.startMarketDataMaintenanceJob({
      end_date: maintenanceEndDate.value || undefined,
      lookback_days: maintenanceLookbackDays.value,
      symbols: parsedMaintenanceSymbols.value,
      dataset_limit: 200,
      report_type: 'morning',
    })
    await pollMaintenanceJob(maintenanceJob.value.job_id)
    await loadDataLab()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '维护任务启动失败。'
  } finally {
    maintenanceRunning.value = false
  }
}

async function pollMaintenanceJob(jobId: string): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    maintenanceJob.value = await api.getMarketDataMaintenanceJob(jobId)
    if (maintenanceJob.value.status === 'completed' || maintenanceJob.value.status === 'failed') return
    await new Promise((resolve) => window.setTimeout(resolve, 2500))
  }
}

async function runBacktest(): Promise<void> {
  if (!parsedBacktestSymbols.value.length) {
    errorMessage.value = '请至少输入一支回测股票。'
    return
  }
  backtesting.value = true
  errorMessage.value = ''
  try {
    backtestResult.value = await api.runBacktest({
      symbols: parsedBacktestSymbols.value,
      start_date: backtestStartDate.value,
      end_date: backtestEndDate.value,
      initial_cash: initialCash.value,
    })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回测失败。'
  } finally {
    backtesting.value = false
  }
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
  loadDataLab()
})
</script>

<style scoped>
.data-lab-page {
  display: grid;
  gap: 18px;
}

.data-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.data-hero h1 {
  margin: 0 0 8px;
  color: #111827;
  font-size: 26px;
  line-height: 1.1;
}

.data-hero p,
.data-muted,
.source-list span,
.source-list p,
.coverage-metrics span,
.data-note,
.job-card span,
.backtest-card span,
.run-list span,
.run-list p {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.data-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  align-items: start;
}

.source-list,
.run-list {
  display: grid;
  gap: 8px;
}

.source-list article,
.run-list article,
.job-card {
  display: grid;
  gap: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #ffffff;
  padding: 14px;
}

.source-list article {
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px 12px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 10px;
}

.source-list article > div {
  display: flex;
  align-items: baseline;
  justify-content: flex-start;
  gap: 8px;
  min-width: 0;
}

.source-list strong,
.run-list strong,
.job-card strong {
  color: #111827;
}

.source-list strong {
  font-size: 14px;
  line-height: 1.25;
}

.source-list b {
  justify-self: end;
  border-radius: 999px;
  background: #f3f4f6;
  color: #374151;
  padding: 4px 8px;
  font-size: 12px;
  line-height: 1;
}

.source-list p {
  grid-column: 1 / -1;
  display: -webkit-box;
  line-height: 1.35;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
  overflow: hidden;
}

.coverage-metrics,
.backtest-card {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.coverage-metrics div,
.backtest-card div {
  display: grid;
  gap: 5px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px;
}

.coverage-metrics strong,
.backtest-card strong {
  color: #111827;
}

.data-note {
  margin-top: 14px;
}

.data-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.data-form label {
  display: grid;
  gap: 8px;
  color: #6b7280;
  font-size: 12px;
}

.data-form input,
.data-form textarea {
  width: 100%;
  border: 1px solid #d1d5db;
  border-radius: 9px;
  background: #ffffff;
  color: #111827;
  padding: 9px 10px;
}

.data-span-2 {
  grid-column: span 2;
}

.job-card,
.backtest-card {
  margin-top: 14px;
}

@media (max-width: 980px) {
  .data-hero,
  .data-grid,
  .data-form,
  .coverage-metrics,
  .backtest-card {
    grid-template-columns: 1fr;
  }

  .data-hero {
    align-items: stretch;
    flex-direction: column;
  }

  .data-span-2 {
    grid-column: span 1;
  }
}
</style>
