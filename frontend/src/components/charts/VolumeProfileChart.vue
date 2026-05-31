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
import type { VolumeProfileBucket } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  items: VolumeProfileBucket[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  return {
    animation: false,
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 } },
    grid: { left: 72, right: 14, top: 18, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { color: '#eef0f4' } } },
    yAxis: {
      type: 'category',
      data: props.items.map((item) => `${item.price_low.toFixed(2)}-${item.price_high.toFixed(2)}`),
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        name: '成交额',
        type: 'bar',
        data: props.items.map((item) => item.amount),
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

watch(() => props.items, render, { deep: true })
</script>
