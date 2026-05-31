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
import { computed, ref } from 'vue'
import { api } from '@/services/api'
import type { QuantCandidate, QuantResearchPayload } from '@/types'
import BenchmarkAlphaChart from '@/components/charts/BenchmarkAlphaChart.vue'
import DistributionChart from '@/components/charts/DistributionChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import MultiEquityCurveChart from '@/components/charts/MultiEquityCurveChart.vue'
import PerformanceDashboard from '@/components/charts/PerformanceDashboard.vue'
import StrategyComparisonChart from '@/components/charts/StrategyComparisonChart.vue'
import TechnicalIndicatorChart from '@/components/charts/TechnicalIndicatorChart.vue'

const limit = ref(20)
const candidates = ref<QuantCandidate[]>([])
const loading = ref(false)
const researching = ref(false)
const errorMessage = ref('')
const symbolsText = ref('600011.SH,000767.SZ,600023.SH')
const startDate = ref('20250101')
const endDate = ref('20260531')
const research = ref<QuantResearchPayload | null>(null)

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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '策略研究失败。'
  } finally {
    researching.value = false
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
