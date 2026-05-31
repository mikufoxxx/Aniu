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

const props = defineProps<{
  title: string
  subtitle?: string
  items: Array<{ name: string; value: number }>
}>()

const chartEl = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null

function buildOption(): EChartsOption {
  return {
    animation: false,
    tooltip: { trigger: 'item', textStyle: { fontSize: 12 } },
    series: [
      {
        type: 'pie',
        radius: ['46%', '72%'],
        center: ['50%', '54%'],
        label: { fontSize: 11, formatter: '{b}' },
        data: props.items,
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
