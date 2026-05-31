<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
    </div>
    <div ref="chartEl" class="performance-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { DrawdownCurvePoint, EquityCurvePoint } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  equityCurve: EquityCurvePoint[]
  drawdownCurve: DrawdownCurvePoint[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  const dates = props.equityCurve.map((item) => item.trade_date)
  return {
    animation: false,
    tooltip: { trigger: 'axis', borderWidth: 1, textStyle: { fontSize: 12 } },
    legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    grid: [
      { left: 54, right: 18, top: 34, height: 130 },
      { left: 54, right: 18, top: 190, height: 84 },
    ],
    xAxis: [
      { type: 'category', data: dates, axisLabel: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#eef0f4' } } },
      { scale: true, gridIndex: 1, axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { lineStyle: { color: '#f1f5f9' } } },
    ],
    series: [
      {
        name: '权益曲线',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: props.equityCurve.map((item) => item.value),
        lineStyle: { color: '#0f172a', width: 2 },
        areaStyle: { color: 'rgba(15, 23, 42, 0.08)' },
      },
      {
        name: '最大回撤',
        type: 'line',
        smooth: true,
        symbol: 'none',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: props.drawdownCurve.map((item) => Number((item.drawdown * 100).toFixed(2))),
        lineStyle: { color: '#dc2626', width: 1.8 },
        areaStyle: { color: 'rgba(220, 38, 38, 0.08)' },
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

watch(() => [props.equityCurve, props.drawdownCurve], render, { deep: true })
</script>
