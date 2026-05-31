<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
      <div class="chart-legend">
        <span>MA5</span>
        <span>MA20</span>
        <span>成交额</span>
        <span v-if="forecastSeries?.length">AI未来线</span>
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
}>()

const chartEl = ref<HTMLDivElement | null>(null)
const activeInterval = ref<ChartInterval>('daily')
let chart: ECharts | null = null

const subtitle = computed(() => props.subtitle || `${props.priceSeries.length} 根日线`)
const intervalOptions: Array<{ key: ChartInterval; label: string }> = [
  { key: 'daily', label: '日' },
  { key: 'weekly', label: '周' },
  { key: 'monthly', label: '月' },
  { key: 'hourly', label: '小时' },
]

const selectedPriceSeries = computed(() => {
  return props.intervalSeries?.[activeInterval.value]?.length
    ? props.intervalSeries[activeInterval.value]
    : props.priceSeries
})

function resize(): void {
  chart?.resize()
}

function buildOption(): EChartsOption {
  const visibleSeries = selectedPriceSeries.value
  const forecast = props.forecastSeries || []
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
    ...visibleSeries.map((item) => Number(item.amount || 0)),
    ...forecast.map(() => 0),
  ]
  const forecastLine = [
    ...visibleSeries.map(() => null),
    ...forecast.map((item) => item.price),
  ]
  const forecastUpper = [
    ...visibleSeries.map(() => null),
    ...forecast.map((item) => item.upper ?? null),
  ]
  const forecastLower = [
    ...visibleSeries.map(() => null),
    ...forecast.map((item) => item.lower ?? null),
  ]
  const markerData = (props.markers || [])
    .filter((marker): marker is TradeMarker & { trade_date: string } => Boolean(marker.trade_date))
    .map((marker) => ({
      name: marker.action,
      coord: [marker.trade_date, marker.price],
      value: `${marker.action} ${marker.quantity}`,
      itemStyle: { color: marker.action === 'SELL' ? '#16a34a' : '#dc2626' },
      label: { color: '#111827', formatter: marker.action === 'SELL' ? 'S' : 'B' },
    }))
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
</script>
