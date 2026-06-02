<template>
  <div class="stock-report-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section v-if="report" class="panel stock-report-panel">
      <div class="panel-head">
        <div>
          <h2>{{ report.title }}</h2>
          <p class="report-muted">
            {{ report.symbol }} · {{ report.model || '默认模型' }} · {{ formatDate(report.created_at) }}
          </p>
        </div>
        <button class="button ghost small" type="button" @click="goBack">
          <span class="material-symbols-rounded" aria-hidden="true">arrow_back</span>
          返回
        </button>
      </div>

      <div class="report-summary">
        <b :class="actionClass(report.action)">{{ report.action }}</b>
        <strong>{{ report.rating }}</strong>
        <span>{{ report.summary }}</span>
      </div>

      <div class="report-skill-row">
        <span v-for="skill in report.selected_skills" :key="skill.id">
          {{ skill.name }}
        </span>
        <span v-if="!report.selected_skills.length">未指定 Skill</span>
      </div>

      <div class="report-metric-grid">
        <div v-for="item in headlineItems" :key="item.label">
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>

      <div v-if="chartsAvailable && report.charts" class="report-chart-grid">
        <MarketKlineChart
          title="价格走势与 AI 信号"
          :subtitle="`${report.name || report.symbol} ${report.symbol}`"
          :price-series="report.charts.price_series"
          :interval-series="report.charts.interval_series"
          :forecast-series="report.charts.forecast_series"
          :markers="report.charts.signal_markers"
          :support-resistance="report.charts.support_resistance"
          :data-summary="report.charts.data_summary"
          :forecast-actual-comparison="report.charts.forecast_actual_comparison"
        />
        <FactorRadarChart
          title="量化因子雷达"
          subtitle="评分结构与因子权重"
          :items="report.charts.factor_radar"
        />
        <TechnicalIndicatorChart
          title="MACD / RSI / KDJ"
          subtitle="趋势强弱与超买超卖"
          :price-series="report.charts.price_series"
        />
        <DistributionChart
          title="收益分布"
          subtitle="历史波动分布"
          :items="report.charts.return_distribution"
        />
        <VolumeProfileChart
          title="成交价量分布"
          subtitle="筹码密集区与关键价位"
          :items="report.charts.volume_profile"
        />
      </div>

      <div class="report-section-list">
        <article v-for="section in report.sections" :key="section.id" class="report-section">
          <h3>{{ section.title }}</h3>
          <p>{{ section.content }}</p>
          <div v-if="section.items?.length" class="report-item-grid">
            <div v-for="item in section.items" :key="`${section.id}-${item.label}-${item.value}`">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section v-else class="panel empty-state">
      <p>{{ loading ? '报告加载中...' : '报告不存在。' }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/services/api'
import type { StockAnalysisReportPayload } from '@/types'
import DistributionChart from '@/components/charts/DistributionChart.vue'
import FactorRadarChart from '@/components/charts/FactorRadarChart.vue'
import MarketKlineChart from '@/components/charts/MarketKlineChart.vue'
import TechnicalIndicatorChart from '@/components/charts/TechnicalIndicatorChart.vue'
import VolumeProfileChart from '@/components/charts/VolumeProfileChart.vue'

const route = useRoute()
const router = useRouter()
const report = ref<StockAnalysisReportPayload | null>(null)
const loading = ref(false)
const errorMessage = ref('')

const chartsAvailable = computed(() => Boolean(report.value?.charts?.price_series?.length))
const headlineItems = computed(() => {
  const currentReport = report.value
  if (!currentReport) return []
  const snapshot = currentReport.source_snapshot || {}
  const support = currentReport.charts?.support_resistance || {}
  const summary = currentReport.charts?.data_summary || {}
  return [
    { label: '现价', value: formatPrice(snapshot.price) },
    { label: '评分', value: formatNumber(snapshot.score, 1) },
    { label: '涨跌幅', value: formatPercent(snapshot.change_pct) },
    { label: '成交额', value: formatAmount(snapshot.amount) },
    { label: '支撑', value: formatPrice(support.support) },
    { label: '压力', value: formatPrice(support.resistance) },
    { label: '日线覆盖', value: `${summary.daily_points ?? currentReport.charts?.price_series?.length ?? 0} 根` },
    { label: '最新交易日', value: String(summary.latest_daily_trade_date || '--') },
  ]
})

async function loadReport(): Promise<void> {
  const reportId = Number(route.params.reportId)
  if (!Number.isFinite(reportId) || reportId <= 0) {
    errorMessage.value = '报告 ID 无效。'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    report.value = await api.getStockAnalysisReport(reportId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告加载失败。'
  } finally {
    loading.value = false
  }
}

function goBack(): void {
  router.push({ name: 'stock-analysis' })
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 19)
}

function formatPrice(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(3) : '--'
}

function formatNumber(value: unknown, digits = 2): string {
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(digits) : '--'
}

function formatPercent(value: unknown): string {
  const number = Number(value)
  return Number.isFinite(number) ? `${number.toFixed(2)}%` : '--'
}

function formatAmount(value: unknown): string {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  const absolute = Math.abs(number)
  if (absolute >= 100000000) return `${(number / 100000000).toFixed(2)}亿`
  if (absolute >= 10000) return `${(number / 10000).toFixed(2)}万`
  return number.toFixed(2)
}

function actionClass(action: string): string {
  if (action === 'BUY') return 'action-buy'
  if (action === 'SELL') return 'action-sell'
  return 'action-hold'
}

onMounted(() => {
  loadReport()
})
</script>

<style scoped>
.stock-report-page,
.stock-report-panel,
.report-section-list,
.report-section {
  display: grid;
  gap: 12px;
}

.report-muted,
.report-summary span,
.report-section p,
.report-item-grid span {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.report-summary {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  border: 1px solid #ececf1;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 12px;
}

.report-summary b {
  border-radius: 999px;
  padding: 6px 9px;
  font-size: 12px;
}

.report-summary strong,
.report-section h3,
.report-item-grid strong {
  color: #111827;
}

.report-skill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.report-skill-row span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #ffffff;
  color: #374151;
  padding: 6px 9px;
  font-size: 12px;
}

.report-metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.report-metric-grid div {
  display: grid;
  gap: 4px;
  border: 1px solid #ececf1;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 10px;
}

.report-metric-grid span {
  color: #6b7280;
  font-size: 12px;
}

.report-metric-grid strong {
  color: #111827;
  font-size: 15px;
}

.report-chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
  gap: 12px;
}

.report-chart-grid :deep(.chart-shell:first-child) {
  grid-row: span 4;
}

.report-section {
  border: 1px solid #ececf1;
  border-radius: 8px;
  padding: 12px;
}

.report-section h3 {
  margin: 0;
  font-size: 16px;
}

.report-item-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.report-item-grid div {
  display: grid;
  gap: 4px;
  border: 1px solid #f1f2f4;
  border-radius: 8px;
  background: #ffffff;
  padding: 10px;
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

@media (max-width: 760px) {
  .report-summary,
  .report-metric-grid,
  .report-chart-grid,
  .report-item-grid {
    grid-template-columns: 1fr;
  }
}
</style>
