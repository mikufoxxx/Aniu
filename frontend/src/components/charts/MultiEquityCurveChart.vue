<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
    </div>
    <div ref="chartEl" class="comparison-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { EquityCurvePoint } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  curves: Array<{
    strategy_name: string
    display_name: string
    points: EquityCurvePoint[]
  }>
  valueMode?: 'asset' | 'return'
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  const dates = Array.from(new Set(props.curves.flatMap((curve) => curve.points.map((point) => point.trade_date)))).sort()
  const valueByDate = (curve: { points: EquityCurvePoint[] }) => {
    const values = new Map(curve.points.map((point) => [
      point.trade_date,
      props.valueMode === 'return' ? Number((point.return_ratio * 100).toFixed(2)) : point.value,
    ]))
    return dates.map((date) => values.get(date) ?? null)
  }
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value) => props.valueMode === 'return'
        ? `${Number(value || 0).toFixed(2)}%`
        : Number(value || 0).toFixed(2),
      textStyle: { fontSize: 12 },
    },
    legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    grid: { left: 48, right: 18, top: 36, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: {
      type: 'value',
      scale: true,
      splitLine: { lineStyle: { color: '#eef0f4' } },
      axisLabel: {
        fontSize: 10,
        formatter: props.valueMode === 'return' ? '{value}%' : '{value}',
      },
    },
    series: props.curves.map((curve) => ({
      name: curve.display_name,
      type: 'line',
      smooth: true,
      symbol: 'none',
      data: valueByDate(curve),
    })),
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

watch(() => props.curves, render, { deep: true })
</script>
