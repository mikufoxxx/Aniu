<template>
  <section class="chart-shell">
    <div class="chart-head">
      <div>
        <strong>{{ title }}</strong>
        <span>{{ subtitle }}</span>
      </div>
    </div>
    <div ref="chartEl" class="radar-canvas"></div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { FactorRadarPoint } from '@/types'

const props = defineProps<{
  title: string
  subtitle?: string
  items: FactorRadarPoint[]
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  return {
    animation: false,
    tooltip: { textStyle: { fontSize: 12 } },
    radar: {
      radius: '62%',
      indicator: props.items.map((item) => ({ name: item.name, max: 1, min: -1 })),
      splitArea: { areaStyle: { color: ['#ffffff', '#f8fafc'] } },
      axisName: { color: '#475569', fontSize: 11 },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: props.items.map((item) => item.value),
            name: '因子',
            areaStyle: { color: 'rgba(37, 99, 235, 0.12)' },
            lineStyle: { color: '#2563eb', width: 2 },
            symbolSize: 4,
          },
        ],
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
