<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
      <div class="chart-legend">
        <span>MACD</span>
        <span>Signal</span>
        <span>RSI14</span>
      </div>
    </div>
    <div ref="chartEl" class="comparison-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { PriceSeriesPoint } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  priceSeries: PriceSeriesPoint[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  const dates = props.priceSeries.map((item) => item.trade_date)
  const macdHist = props.priceSeries.map((item) => item.macd_hist ?? null)
  const macd = props.priceSeries.map((item) => item.macd ?? null)
  const signal = props.priceSeries.map((item) => item.macd_signal ?? null)
  const rsi = props.priceSeries.map((item) => item.rsi14 ?? null)
  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, textStyle: { fontSize: 12 } },
    legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    grid: [
      { left: 44, right: 18, top: 36, height: 112 },
      { left: 44, right: 18, top: 186, height: 52 },
    ],
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    xAxis: [
      { type: 'category', data: dates, boundaryGap: true, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, boundaryGap: true, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#eef0f4' } }, axisLabel: { fontSize: 10 } },
      { min: 0, max: 100, gridIndex: 1, splitLine: { lineStyle: { color: '#eef0f4' } }, axisLabel: { fontSize: 10 } },
    ],
    series: [
      {
        name: 'MACD柱',
        type: 'bar',
        data: macdHist,
        itemStyle: {
          color: (params) => (Number(params.value || 0) >= 0 ? '#dc2626' : '#16a34a'),
        },
      },
      { name: 'MACD', type: 'line', data: macd, smooth: true, symbol: 'none', lineStyle: { width: 1.4, color: '#2563eb' } },
      { name: 'Signal', type: 'line', data: signal, smooth: true, symbol: 'none', lineStyle: { width: 1.4, color: '#f59e0b' } },
      {
        name: 'RSI14',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: rsi,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.6, color: '#7c3aed' },
        markLine: {
          symbol: 'none',
          lineStyle: { type: 'dashed', color: '#cbd5e1', width: 1 },
          label: { color: '#64748b', fontSize: 10 },
          data: [{ yAxis: 70 }, { yAxis: 30 }],
        },
      },
    ],
  }
}

function render(): void {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  chart.setOption(buildOption(), true)
}

function resize(): void {
  chart?.resize()
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

watch(() => props.priceSeries, render, { deep: true })
</script>
