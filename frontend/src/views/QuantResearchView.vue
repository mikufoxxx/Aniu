<template>
  <div class="quant-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section class="quant-grid">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>因子候选</h2>
            <p class="quant-muted">基于行情、日线、资金、估值、龙虎榜等已有数据。</p>
          </div>
          <div class="quant-head-actions">
            <label class="quant-limit">
              数量
              <input v-model.number="limit" min="5" max="50" type="number" />
            </label>
            <button class="button primary small" :disabled="loading" @click="loadCandidates">
              <span class="material-symbols-rounded" aria-hidden="true">filter_alt</span>
              {{ loading ? '计算中...' : '生成候选' }}
            </button>
          </div>
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
            <h2>策略研究</h2>
            <p class="quant-muted">同一股票池对比动量、分散、低波动策略，结果可供 AI 学习。</p>
          </div>
          <button class="button primary small" :disabled="researching" @click="runResearch">
            <span class="material-symbols-rounded" aria-hidden="true">model_training</span>
            {{ researching ? '研究中...' : '研究策略' }}
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
        <div v-if="research" class="quant-summary">
          <div>
            <span>最佳策略</span>
            <strong>{{ research.best_strategy.display_name }}</strong>
          </div>
          <div>
            <span>样本</span>
            <strong>{{ research.symbol_count }}股 / {{ research.bar_count }}根</strong>
          </div>
          <div>
            <span>收益</span>
            <strong :class="profitClass(research.best_strategy.return_ratio)">{{ formatPercent(research.best_strategy.return_ratio) }}</strong>
          </div>
          <div>
            <span>最大回撤</span>
            <strong>{{ formatPercent(research.best_strategy.max_drawdown) }}</strong>
          </div>
        </div>
        <div v-if="research" class="strategy-list">
          <article v-for="strategy in research.strategies" :key="strategy.strategy_name">
            <div>
              <strong>{{ strategy.display_name }}</strong>
              <span>{{ strategy.selected_symbols.join(', ') }}</span>
            </div>
            <div class="strategy-metrics">
              <span :class="profitClass(strategy.return_ratio)">{{ formatPercent(strategy.return_ratio) }}</span>
              <span>{{ formatPercent(strategy.max_drawdown) }}</span>
              <span>{{ scoreText(strategy.score) }}</span>
            </div>
            <p>{{ strategy.reason }}</p>
          </article>
        </div>
      </section>
    </section>

    <section class="panel quant-report-panel">
      <div class="panel-head">
        <div>
          <h2>研究报告</h2>
          <p class="quant-muted">已保存的量化研究结果，点击进入完整策略详情。</p>
        </div>
        <button class="button ghost small" type="button" @click="loadResearchWorkspace">
          <span class="material-symbols-rounded" aria-hidden="true">sync</span>
          刷新
        </button>
      </div>
      <div class="quant-report-list">
        <button
          v-for="report in reports"
          :key="report.id"
          class="quant-report-card"
          type="button"
          @click="openResearchReport(report.id)"
        >
          <div>
            <strong>{{ report.title }}</strong>
            <span>{{ report.start_date }} - {{ report.end_date }} · {{ report.best_strategy_display_name }}</span>
            <small>{{ report.summary }}</small>
          </div>
          <div class="quant-report-card-metrics">
            <b :class="profitClass(report.return_ratio)">{{ formatPercent(report.return_ratio) }}</b>
            <span>{{ formatPercent(report.max_drawdown) }}</span>
          </div>
        </button>
        <div v-if="!reports.length" class="empty-state">
          <p>暂无保存的量化研究报告。</p>
        </div>
      </div>
    </section>

    <section v-if="research" class="quant-chart-grid">
      <MarketKlineChart
        title="价格走势与交易点"
        :subtitle="research.best_strategy.selected_symbols.join(', ')"
        :price-series="research.best_strategy.charts.price_series"
        :interval-series="research.best_strategy.charts.interval_series"
        :forecast-series="research.best_strategy.charts.forecast_series"
        :markers="research.best_strategy.charts.trade_markers"
      />
      <PerformanceDashboard
        title="权益曲线与回撤"
        :subtitle="research.best_strategy.display_name"
        :equity-curve="research.best_strategy.charts.equity_curve"
        :drawdown-curve="research.best_strategy.charts.drawdown_curve"
      />
      <StrategyComparisonChart
        title="策略收益 / 回撤 / 评分"
        subtitle="同一股票池横向对比"
        :items="research.comparison_chart"
      />
      <MultiEquityCurveChart
        title="多策略权益曲线"
        subtitle="用于观察策略稳定性和分化"
        :curves="research.strategy_equity_curves"
      />
      <BenchmarkAlphaChart
        title="基准对比与 Alpha"
        subtitle="策略相对等权买入基准的超额收益"
        :strategy-curve="research.best_strategy.charts.equity_curve"
        :benchmark-curve="research.benchmark_curve"
        :alpha-curve="research.alpha_curve"
      />
      <TechnicalIndicatorChart
        title="MACD / RSI 技术指标"
        subtitle="用于确认趋势强弱和超买超卖区间"
        :price-series="research.best_strategy.charts.price_series"
      />
      <DistributionChart
        title="单日收益分布"
        subtitle="检查收益是否集中在少数日期"
        :items="research.best_strategy.charts.return_distribution"
      />
      <section class="panel quant-risk-panel">
        <div class="panel-head">
          <div>
            <h2>风险指标</h2>
            <p class="quant-muted">参考 TradingView / Portfolio 类工具的核心风险读数。</p>
          </div>
        </div>
        <div class="quant-risk-grid">
          <div v-for="item in riskMetricItems" :key="item.key">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '@/services/api'
import type { QuantCandidate, QuantResearchPayload, QuantResearchReportSummary } from '@/types'
import BenchmarkAlphaChart from '@/components/charts/BenchmarkAlphaChart.vue'
import DistributionChart from '@/components/charts/DistributionChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import MultiEquityCurveChart from '@/components/charts/MultiEquityCurveChart.vue'
import PerformanceDashboard from '@/components/charts/PerformanceDashboard.vue'
import StrategyComparisonChart from '@/components/charts/StrategyComparisonChart.vue'
import TechnicalIndicatorChart from '@/components/charts/TechnicalIndicatorChart.vue'

const limit = ref(20)
const router = useRouter()
const candidates = ref<QuantCandidate[]>([])
const loading = ref(false)
const researching = ref(false)
const errorMessage = ref('')
const symbolsText = ref('600011.SH,000767.SZ,600023.SH')
const startDate = ref('20250101')
const endDate = ref(todayCompactDate())
const research = ref<QuantResearchPayload | null>(null)
const reports = ref<QuantResearchReportSummary[]>([])

const riskMetricItems = computed(() => {
  const metrics = research.value?.best_strategy.charts.risk_metrics ?? {}
  return [
    { key: 'total_return', label: '累计收益', value: formatPercent(Number(metrics.total_return ?? 0)) },
    { key: 'annual_return', label: '年化收益', value: formatPercent(Number(metrics.annual_return ?? 0)) },
    { key: 'volatility', label: '年化波动', value: formatPercent(Number(metrics.volatility ?? 0)) },
    { key: 'sharpe', label: 'Sharpe', value: Number(metrics.sharpe ?? 0).toFixed(2) },
    { key: 'sortino', label: 'Sortino', value: Number(metrics.sortino ?? 0).toFixed(2) },
    { key: 'win_rate', label: '胜率', value: formatPercent(Number(metrics.win_rate ?? 0)) },
    { key: 'omega', label: 'Omega', value: Number(metrics.omega ?? 0).toFixed(2) },
    { key: 'max_drawdown', label: '最大回撤', value: formatPercent(Number(metrics.max_drawdown ?? 0)) },
    { key: 'calmar', label: 'Calmar', value: Number(metrics.calmar ?? 0).toFixed(2) },
  ]
})

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

async function runResearch(): Promise<void> {
  const symbols = parseSymbols(symbolsText.value)
  if (!symbols.length) {
    errorMessage.value = '请输入至少一支股票。'
    return
  }
  researching.value = true
  errorMessage.value = ''
  try {
    research.value = await api.runQuantResearch({
      symbols,
      start_date: startDate.value,
      end_date: endDate.value,
      initial_cash: 200000,
    })
    if (research.value.id) {
      reports.value = [
        {
          id: research.value.id,
          title: research.value.title || '量化研究报告',
          summary: research.value.summary || research.value.ai_learning_context,
          symbols,
          start_date: research.value.start_date,
          end_date: research.value.end_date,
          initial_cash: research.value.initial_cash,
          symbol_count: research.value.symbol_count,
          bar_count: research.value.bar_count,
          best_strategy_name: research.value.best_strategy.strategy_name,
          best_strategy_display_name: research.value.best_strategy.display_name,
          final_assets: research.value.best_strategy.final_assets,
          return_ratio: research.value.best_strategy.return_ratio,
          max_drawdown: research.value.best_strategy.max_drawdown,
          trade_count: research.value.best_strategy.trade_count,
          created_at: research.value.created_at,
        },
        ...reports.value.filter((item) => item.id !== research.value?.id),
      ].slice(0, 20)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '策略研究失败。'
  } finally {
    researching.value = false
  }
}

async function loadResearchWorkspace(): Promise<void> {
  try {
    const payload = await api.getQuantResearchWorkspace({ limit: 20 })
    reports.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '量化研究报告加载失败。'
  }
}

function openResearchReport(reportId: number): void {
  router.push({ name: 'quant-research-report', params: { reportId: String(reportId) } })
}

function parseSymbols(value: string): string[] {
  return value.split(/[\s,，;；]+/).map((item) => item.trim().toUpperCase()).filter(Boolean)
}

function todayCompactDate(): string {
  const date = new Date()
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)
  const valueByType = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${valueByType.year}${valueByType.month}${valueByType.day}`
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

onMounted(() => {
  loadResearchWorkspace()
})
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
.quant-summary span,
.strategy-list span,
.strategy-list p {
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

.quant-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  gap: 14px;
  align-items: start;
}

.quant-chart-grid > :first-child {
  grid-row: span 2;
}

.quant-report-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.quant-report-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
  border: 1px solid #ececf1;
  border-radius: 10px;
  background: #ffffff;
  padding: 12px;
  text-align: left;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.quant-report-card:hover {
  border-color: #d8d8d3;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05);
  transform: translateY(-1px);
}

.quant-report-card div:first-child {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.quant-report-card strong,
.quant-report-card span,
.quant-report-card small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.quant-report-card strong {
  color: #111827;
  font-size: 14px;
}

.quant-report-card span,
.quant-report-card small {
  color: #6b7280;
  font-size: 12px;
}

.quant-report-card-metrics {
  display: grid;
  gap: 4px;
  justify-items: end;
}

.quant-report-card-metrics b {
  color: #111827;
  font-size: 14px;
}

.quant-risk-panel {
  padding: 12px;
}

.quant-risk-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.quant-risk-grid div {
  display: grid;
  gap: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}

.quant-risk-grid span {
  color: #6b7280;
  font-size: 12px;
}

.quant-risk-grid strong {
  color: #111827;
  font-size: 14px;
}

.quant-head-actions {
  display: flex;
  align-items: end;
  gap: 10px;
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
  border: 1px solid #dededb;
  border-radius: 12px;
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
  border: 1px solid #ececf1;
  border-radius: 12px;
  padding: 12px;
  background: #ffffff;
}

.quant-candidate-list article > p {
  grid-column: 1 / -1;
}

.quant-candidate-list div,
.quant-summary div {
  display: grid;
  gap: 4px;
}

.quant-candidate-list article > div:last-of-type {
  justify-items: end;
}

.quant-candidate-list strong,
.quant-candidate-list b,
.quant-summary strong,
.strategy-list strong {
  color: #111827;
}

.quant-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.quant-summary div,
.strategy-list article {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
}

.strategy-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.strategy-list article {
  display: grid;
  gap: 8px;
}

.strategy-list article > div:first-child {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
}

.strategy-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.strategy-metrics span {
  border-radius: 999px;
  background: #f3f4f6;
  padding: 5px 8px;
  text-align: center;
}

@media (max-width: 980px) {
  .quant-hero,
  .quant-grid,
  .quant-report-list,
  .quant-chart-grid {
    grid-template-columns: 1fr;
  }

  .quant-chart-grid > :first-child {
    grid-row: auto;
  }

  .quant-risk-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .quant-hero {
    align-items: flex-start;
    flex-direction: column;
  }

  .quant-head-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .quant-head-actions .button {
    min-width: 160px;
  }
}
</style>
