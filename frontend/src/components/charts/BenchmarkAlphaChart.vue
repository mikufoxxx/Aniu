<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
      <div class="chart-legend">
        <span>策略</span>
        <span>基准</span>
        <span>Alpha</span>
      </div>
    </div>
    <div ref="chartEl" class="comparison-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { AlphaCurvePoint, EquityCurvePoint } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  strategyCurve: EquityCurvePoint[]
  benchmarkCurve: EquityCurvePoint[]
  alphaCurve: AlphaCurvePoint[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function percent(value: number | null | undefined): number | null {
  return typeof value === 'number' ? Number((value * 100).toFixed(2)) : null
}

function buildOption(): EChartsOption {
  const dates = props.strategyCurve.map((point) => point.trade_date)
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      valueFormatter: (value) => `${Number(value || 0).toFixed(2)}%`,
      textStyle: { fontSize: 12 },
    },
    legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    grid: [
      { left: 46, right: 18, top: 36, height: 120 },
      { left: 46, right: 18, top: 194, height: 44 },
    ],
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    xAxis: [
      { type: 'category', data: dates, boundaryGap: false, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, boundaryGap: false, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { type: 'value', scale: true, splitLine: { lineStyle: { color: '#eef0f4' } }, axisLabel: { fontSize: 10, formatter: '{value}%' } },
      { type: 'value', scale: true, gridIndex: 1, splitLine: { show: false }, axisLabel: { fontSize: 10, formatter: '{value}%' } },
    ],
    series: [
      {
        name: '策略收益',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: props.strategyCurve.map((point) => percent(point.return_ratio)),
        lineStyle: { width: 1.8, color: '#2563eb' },
      },
      {
        name: '等权基准',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: props.benchmarkCurve.map((point) => percent(point.return_ratio)),
        lineStyle: { width: 1.5, color: '#64748b', type: 'dashed' },
      },
      {
        name: 'Alpha',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: props.alphaCurve.map((point) => percent(point.alpha)),
        itemStyle: { color: '#0f766e' },
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

watch(() => [props.strategyCurve, props.benchmarkCurve, props.alphaCurve], render, { deep: true })
</script>
