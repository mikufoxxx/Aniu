<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
    </div>
    <div ref="chartEl" class="mini-chart-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { DistributionBucket } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  items: DistributionBucket[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function label(item: DistributionBucket): string {
  return `${(item.low * 100).toFixed(1)}%~${(item.high * 100).toFixed(1)}%`
}

function buildOption(): EChartsOption {
  return {
    animation: false,
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 } },
    grid: { left: 32, right: 12, top: 20, bottom: 44 },
    xAxis: { type: 'category', data: props.items.map(label), axisLabel: { rotate: 26, fontSize: 10 } },
    yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef0f4' } }, axisLabel: { fontSize: 10 } },
    series: [
      {
        name: '频次',
        type: 'bar',
        data: props.items.map((item) => item.count),
        itemStyle: { color: '#2563eb' },
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
