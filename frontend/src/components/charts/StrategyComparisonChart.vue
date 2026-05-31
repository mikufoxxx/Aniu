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

const props = defineProps<{
  title: string
  subtitle?: string
  items: Array<{
    strategy_name: string
    display_name: string
    return_ratio: number
    max_drawdown: number
    score: number
  }>
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  const names = props.items.map((item) => item.display_name)
  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { fontSize: 12 } },
    legend: { top: 0, right: 8, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11 } },
    grid: { left: 42, right: 18, top: 38, bottom: 32 },
    xAxis: { type: 'category', data: names, axisLabel: { fontSize: 11 } },
    yAxis: [
      { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef0f4' } } },
      { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { show: false } },
    ],
    series: [
      {
        name: '收益',
        type: 'bar',
        data: props.items.map((item) => Number((item.return_ratio * 100).toFixed(2))),
        itemStyle: { color: '#dc2626' },
      },
      {
        name: '回撤',
        type: 'bar',
        data: props.items.map((item) => Number((item.max_drawdown * 100).toFixed(2))),
        itemStyle: { color: '#16a34a' },
      },
      {
        name: '评分',
        type: 'line',
        yAxisIndex: 1,
        data: props.items.map((item) => Number(item.score.toFixed(2))),
        lineStyle: { color: '#2563eb', width: 2 },
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

watch(() => props.items, render, { deep: true })
</script>
