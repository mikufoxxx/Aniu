<template>
  <div class="quant-report-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section v-if="report" class="panel quant-report-head">
      <div class="panel-head">
        <div>
          <h2>{{ report.title || '量化研究报告' }}</h2>
          <p class="report-muted">
            {{ report.start_date }} - {{ report.end_date }} · {{ formatDate(report.created_at) }}
          </p>
        </div>
        <button class="button ghost small" type="button" @click="goBack">
          <span class="material-symbols-rounded" aria-hidden="true">arrow_back</span>
          返回
        </button>
      </div>
      <div class="quant-report-summary">
        <div>
          <span>最佳策略</span>
          <strong>{{ report.best_strategy.display_name }}</strong>
        </div>
        <div>
          <span>收益</span>
          <strong :class="profitClass(report.best_strategy.return_ratio)">
            {{ formatPercent(report.best_strategy.return_ratio) }}
          </strong>
        </div>
        <div>
          <span>最大回撤</span>
          <strong>{{ formatPercent(report.best_strategy.max_drawdown) }}</strong>
        </div>
        <div>
          <span>样本</span>
          <strong>{{ report.symbol_count }}股 / {{ report.bar_count }}根</strong>
        </div>
      </div>
      <p class="report-muted">{{ report.summary || report.ai_learning_context }}</p>
    </section>

    <section v-if="report" class="quant-chart-grid">
      <MarketKlineChart
        title="价格走势与交易点"
        :subtitle="report.best_strategy.selected_symbols.join(', ')"
        :price-series="report.best_strategy.charts.price_series"
        :interval-series="report.best_strategy.charts.interval_series"
        :forecast-series="report.best_strategy.charts.forecast_series"
        :markers="report.best_strategy.charts.trade_markers"
      />
      <PerformanceDashboard
        title="权益曲线与回撤"
        :subtitle="report.best_strategy.display_name"
        :equity-curve="report.best_strategy.charts.equity_curve"
        :drawdown-curve="report.best_strategy.charts.drawdown_curve"
      />
      <StrategyComparisonChart
        title="策略收益 / 回撤 / 评分"
        subtitle="同一股票池横向对比"
        :items="report.comparison_chart"
      />
      <MultiEquityCurveChart
        title="多策略权益曲线"
        subtitle="用于观察策略稳定性和分化"
        :curves="report.strategy_equity_curves"
      />
      <BenchmarkAlphaChart
        title="基准对比与 Alpha"
        subtitle="策略相对等权买入基准的超额收益"
        :strategy-curve="report.best_strategy.charts.equity_curve"
        :benchmark-curve="report.benchmark_curve"
        :alpha-curve="report.alpha_curve"
      />
      <TechnicalIndicatorChart
        title="MACD / RSI 技术指标"
        subtitle="趋势强弱与超买超卖"
        :price-series="report.best_strategy.charts.price_series"
      />
      <DistributionChart
        title="单日收益分布"
        subtitle="检查收益是否集中在少数日期"
        :items="report.best_strategy.charts.return_distribution"
      />
    </section>

    <section v-else class="panel empty-state">
      <p>{{ loading ? '报告加载中...' : '报告不存在。' }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import BenchmarkAlphaChart from '@/components/charts/BenchmarkAlphaChart.vue'
import DistributionChart from '@/components/charts/DistributionChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import MultiEquityCurveChart from '@/components/charts/MultiEquityCurveChart.vue'
import PerformanceDashboard from '@/components/charts/PerformanceDashboard.vue'
import StrategyComparisonChart from '@/components/charts/StrategyComparisonChart.vue'
import TechnicalIndicatorChart from '@/components/charts/TechnicalIndicatorChart.vue'
import { api } from '@/services/api'
import type { QuantResearchPayload } from '@/types'
import { formatTime } from '@/utils/formatters'

const route = useRoute()
const router = useRouter()
const report = ref<QuantResearchPayload | null>(null)
const loading = ref(false)
const errorMessage = ref('')

async function loadReport(): Promise<void> {
  const reportId = Number(route.params.reportId)
  if (!Number.isFinite(reportId) || reportId <= 0) {
    errorMessage.value = '报告 ID 无效。'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    report.value = await api.getQuantResearchReport(reportId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告加载失败。'
  } finally {
    loading.value = false
  }
}

function goBack(): void {
  router.push({ name: 'quant-research' })
}

function formatDate(value: string | null | undefined): string {
  return formatTime(value)
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function profitClass(value: number): string {
  if (value > 0) return 'profit-up'
  if (value < 0) return 'profit-down'
  return ''
}

onMounted(() => {
  loadReport()
})
</script>

<style scoped>
.quant-report-page {
  display: grid;
  gap: 14px;
}

.quant-report-head {
  display: grid;
  gap: 12px;
}

.report-muted,
.quant-report-summary span {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.quant-report-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.quant-report-summary div {
  display: grid;
  gap: 4px;
  border: 1px solid #ececf1;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 10px;
}

.quant-report-summary strong {
  color: #111827;
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

@media (max-width: 980px) {
  .quant-report-summary,
  .quant-chart-grid {
    grid-template-columns: 1fr;
  }

  .quant-chart-grid > :first-child {
    grid-row: auto;
  }
}
</style>
