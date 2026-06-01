<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
      <div class="chart-legend">
        <span v-if="latestRealtimePoint" class="chart-live-pill">
          实时 {{ latestRealtimePoint.source || 'quote' }} {{ latestRealtimePoint.trade_date }}
        </span>
        <span>MA5</span>
        <span>MA20</span>
        <span>成交额</span>
        <span v-if="activeForecastSeries.length">AI未来线</span>
      </div>
    </div>
    <div class="chart-interval-controls">
      <button
        v-for="option in intervalOptions"
        :key="option.key"
        type="button"
        :class="{ active: activeInterval === option.key }"
        @click="activeInterval = option.key"
      >
        {{ option.label }}
      </button>
    </div>
    <div v-if="metaItems.length || explanationNotes?.length" class="chart-data-strip">
      <span v-for="item in metaItems" :key="item.label">
        <b>{{ item.label }}</b>{{ item.value }}
      </span>
      <span v-for="note in explanationNotes || []" :key="note" class="chart-note">{{ note }}</span>
    </div>
    <div ref="chartEl" class="chart-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { ForecastPoint, PriceSeriesPoint, TradeMarker } from '@/types'

type ChartInterval = 'daily' | 'weekly' | 'monthly' | 'hourly'

const props = defineProps<{
  title: string
  subtitle?: string
  priceSeries: PriceSeriesPoint[]
  intervalSeries?: Record<ChartInterval, PriceSeriesPoint[]>
  forecastSeries?: ForecastPoint[]
  markers?: TradeMarker[]
  dataSummary?: Record<string, unknown>
  forecastSnapshot?: Record<string, unknown>
  forecastActualComparison?: Record<string, unknown>
  explanationNotes?: string[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const activeInterval = ref<ChartInterval>('daily')
let chart: ECharts | null = null

const subtitle = computed(() => props.subtitle || `${props.priceSeries.length} 根日线`)
const baseIntervalOptions: Array<{ key: ChartInterval; label: string }> = [
  { key: 'daily', label: '日' },
  { key: 'weekly', label: '周' },
  { key: 'monthly', label: '月' },
]
const intervalOptions = computed(() => {
  const options = [...baseIntervalOptions]
  if (props.intervalSeries?.hourly?.length) {
    options.push({ key: 'hourly' as const, label: '小时' })
  }
  return options
})

const selectedPriceSeries = computed(() => {
  const series = props.intervalSeries?.[activeInterval.value]?.length
    ? props.intervalSeries[activeInterval.value]
    : props.priceSeries
  return normalizePriceSeries(series)
})
const latestRealtimePoint = computed(() => {
  const point = props.priceSeries[props.priceSeries.length - 1]
  return point?.is_realtime ? point : null
})
const activeForecastSeries = computed(() => (
  activeInterval.value === 'daily' ? normalizeForecastSeries(props.forecastSeries || []) : []
))
const metaItems = computed(() => {
  const summary = props.dataSummary ?? {}
  const comparison = props.forecastActualComparison ?? {}
  const items: Array<{ label: string; value: string }> = []
  if (typeof summary.daily_points === 'number') {
    items.push({ label: '日线', value: `${summary.daily_points} 根` })
  }
  if (typeof summary.hourly_points === 'number' && summary.hourly_points > 0) {
    items.push({
      label: '分时',
      value: `${summary.hourly_points} 根 · ${String(summary.latest_hourly_source || '--')}`,
    })
  }
  if (summary.forecast_frozen) {
    items.push({
      label: '预测',
      value: `已冻结 · ${String(summary.forecast_history_end_date || '--')}`,
    })
  } else if (activeForecastSeries.value.length) {
    items.push({ label: '预测', value: `${activeForecastSeries.value.length} 点` })
  }
  if (comparison.summary) {
    items.push({ label: '对照', value: String(comparison.summary) })
  }
  return items
})

function resize(): void {
  chart?.resize()
}

function compactDateKey(value: string | undefined): string {
  return String(value || '').replace(/\D/g, '').slice(0, 8)
}

function tradeSortKey(value: string | undefined, fallback = 0): number {
  const text = String(value || '')
  const week = text.match(/^(\d{4})W(\d{2})$/)
  if (week) return Number(`${week[1]}${week[2]}0000`)
  const digits = text.replace(/\D/g, '')
  if (digits.length >= 12) return Number(digits.slice(0, 12))
  if (digits.length >= 8) return Number(`${digits.slice(0, 8)}0000`)
  if (digits.length >= 6) return Number(`${digits.slice(0, 6)}000000`)
  return fallback
}

function normalizePriceSeries(series: PriceSeriesPoint[]): PriceSeriesPoint[] {
  return [...series]
    .map((item, index) => {
      const close = Number(item.close)
      if (!Number.isFinite(close) || close <= 0) return null
      const open = Number.isFinite(Number(item.open)) && Number(item.open) > 0 ? Number(item.open) : close
      const highInput = Number.isFinite(Number(item.high)) && Number(item.high) > 0 ? Number(item.high) : close
      const lowInput = Number.isFinite(Number(item.low)) && Number(item.low) > 0 ? Number(item.low) : close
      const high = Math.max(open, close, highInput)
      const low = Math.min(open, close, lowInput)
      return {
        ...item,
        open,
        high,
        low,
        close,
        volume: Number(item.volume || 0),
        amount: Number(item.amount || 0),
        _sortKey: tradeSortKey(item.trade_date, index),
      }
    })
    .filter((item): item is PriceSeriesPoint & { _sortKey: number } => Boolean(item))
    .sort((a, b) => a._sortKey - b._sortKey)
    .map(({ _sortKey: _ignored, ...item }) => item)
}

function normalizeForecastSeries(series: ForecastPoint[]): ForecastPoint[] {
  return [...series]
    .filter((item) => Number.isFinite(Number(item.price)) && Number(item.price) > 0 && item.trade_date)
    .sort((a, b) => tradeSortKey(a.trade_date) - tradeSortKey(b.trade_date))
}

function nearestSameDayPoint(
  markerDate: string,
  visibleSeries: PriceSeriesPoint[],
): PriceSeriesPoint | undefined {
  const markerDay = compactDateKey(markerDate)
  if (!markerDay) return undefined
  const markerKey = tradeSortKey(markerDate)
  const sameDay = visibleSeries.filter((point) => compactDateKey(point.trade_date) === markerDay)
  if (!sameDay.length) return undefined
  if (markerKey <= 0 || markerDate.replace(/\D/g, '').length < 12) return sameDay[sameDay.length - 1]
  return sameDay.reduce((best, point) => {
    const bestDistance = Math.abs(tradeSortKey(best.trade_date) - markerKey)
    const pointDistance = Math.abs(tradeSortKey(point.trade_date) - markerKey)
    return pointDistance < bestDistance ? point : best
  }, sameDay[0])
}

function markerCoordinate(
  marker: TradeMarker,
  visibleSeries: PriceSeriesPoint[],
): [string, number] | null {
  if (!marker.trade_date) return null
  const markerDate = String(marker.trade_date)
  const markerPrice = Number(marker.price)
  if (!Number.isFinite(markerPrice) || markerPrice <= 0) return null
  const markerKey = tradeSortKey(markerDate)
  const exact = visibleSeries.find((point) => {
    return String(point.trade_date) === markerDate || tradeSortKey(point.trade_date) === markerKey
  })
  const target = exact || nearestSameDayPoint(markerDate, visibleSeries)
  if (!target?.trade_date) return null
  return [String(target.trade_date), markerPrice]
}

function buildOption(): EChartsOption {
  const visibleSeries = selectedPriceSeries.value
  const forecast = activeForecastSeries.value
  const dates = [
    ...visibleSeries.map((item) => item.trade_date),
    ...forecast.map((item) => item.trade_date),
  ]
  const candles = [
    ...visibleSeries.map((item) => [item.open, item.close, item.low, item.high]),
  ]
  const ma5 = [
    ...visibleSeries.map((item) => item.ma5 ?? null),
    ...forecast.map(() => null),
  ]
  const ma20 = [
    ...visibleSeries.map((item) => item.ma20 ?? null),
    ...forecast.map(() => null),
  ]
  const amounts = [
    ...visibleSeries.map((item) => ({
      value: Number(item.amount || 0),
      itemStyle: {
        color: Number(item.close || 0) >= Number(item.open || 0) ? '#dc2626' : '#16a34a',
      },
    })),
    ...forecast.map(() => 0),
  ]
  const lastActualIndex = visibleSeries.length - 1
  const lastActualClose = lastActualIndex >= 0 ? visibleSeries[lastActualIndex].close : null
  const forecastLine = [
    ...visibleSeries.map((item, index) => forecast.length && index === lastActualIndex ? item.close : null),
    ...forecast.map((item) => item.price),
  ]
  const forecastUpper = [
    ...visibleSeries.map((_item, index) => forecast.length && index === lastActualIndex ? lastActualClose : null),
    ...forecast.map((item) => item.upper ?? null),
  ]
  const forecastLower = [
    ...visibleSeries.map((_item, index) => forecast.length && index === lastActualIndex ? lastActualClose : null),
    ...forecast.map((item) => item.lower ?? null),
  ]
  const markerData: Array<{
    name: string
    coord: [string, number]
    value: string
    itemStyle: { color: string }
    label: { color: string; formatter: string }
  }> = []
  for (const marker of (props.markers || []).filter(
    (item): item is TradeMarker & { trade_date: string } => Boolean(item.trade_date),
  )) {
    const coord = markerCoordinate(marker, visibleSeries)
    if (!coord) continue
    markerData.push({
      name: marker.action,
      coord,
      value: `${marker.action} ${marker.quantity}`,
      itemStyle: { color: marker.action === 'SELL' ? '#16a34a' : '#dc2626' },
      label: { color: '#111827', formatter: marker.action === 'SELL' ? 'S' : 'B' },
    })
  }
  return {
    animation: false,
    grid: [
      { left: 46, right: 18, top: 34, height: 210 },
      { left: 46, right: 18, top: 274, height: 64 },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      borderWidth: 1,
      textStyle: { fontSize: 12 },
    },
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], height: 18, bottom: 4 },
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#eef0f4' } } },
      { scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 10 } },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: candles,
        itemStyle: {
          color: '#dc2626',
          color0: '#16a34a',
          borderColor: '#dc2626',
          borderColor0: '#16a34a',
        },
        markPoint: {
          symbol: 'pin',
          symbolSize: 38,
          data: markerData,
        },
      },
      { name: 'MA5', type: 'line', data: ma5, smooth: true, symbol: 'none', lineStyle: { width: 1.4, color: '#2563eb' } },
      { name: 'MA20', type: 'line', data: ma20, smooth: true, symbol: 'none', lineStyle: { width: 1.4, color: '#7c3aed' } },
      {
        name: 'AI未来线',
        type: 'line',
        data: forecastLine,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { width: 1.8, color: '#0f766e', type: 'dashed' },
      },
      { name: '上沿', type: 'line', data: forecastUpper, smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#99f6e4', type: 'dotted' } },
      { name: '下沿', type: 'line', data: forecastLower, smooth: true, symbol: 'none', lineStyle: { width: 1, color: '#99f6e4', type: 'dotted' } },
      {
        name: '成交额',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: amounts,
        itemStyle: { color: '#94a3b8' },
      },
    ],
  }
}

function render(): void {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})

watch(() => [props.priceSeries, props.intervalSeries, props.forecastSeries, props.markers, activeInterval.value], render, { deep: true })
watch(intervalOptions, (options) => {
  if (!options.some((option) => option.key === activeInterval.value)) {
    activeInterval.value = 'daily'
  }
})
</script>
