<template>
  <div class="stock-report-page">
    <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

    <section v-if="report" class="panel stock-report-panel">
      <div class="panel-head">
        <div>
          <h2>{{ report.title }}</h2>
          <p class="report-muted">
            {{ report.symbol }} · {{ report.model || '默认模型' }} · {{ formatDate(report.created_at) }}
          </p>
        </div>
        <button class="button ghost small" type="button" @click="goBack">
          <span class="material-symbols-rounded" aria-hidden="true">arrow_back</span>
          返回
        </button>
      </div>

      <div class="report-summary">
        <b :class="actionClass(report.action)">{{ report.action }}</b>
        <strong>{{ report.rating }}</strong>
        <span>{{ report.summary }}</span>
      </div>

      <div class="report-skill-row">
        <span v-for="skill in report.selected_skills" :key="skill.id">
          {{ skill.name }}
        </span>
        <span v-if="!report.selected_skills.length">未指定 Skill</span>
      </div>

      <div class="report-section-list">
        <article v-for="section in report.sections" :key="section.id" class="report-section">
          <h3>{{ section.title }}</h3>
          <p>{{ section.content }}</p>
          <div v-if="section.items?.length" class="report-item-grid">
            <div v-for="item in section.items" :key="`${section.id}-${item.label}-${item.value}`">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section v-else class="panel empty-state">
      <p>{{ loading ? '报告加载中...' : '报告不存在。' }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/services/api'
import type { StockAnalysisReportPayload } from '@/types'

const route = useRoute()
const router = useRouter()
const report = ref<StockAnalysisReportPayload | null>(null)
const loading = ref(false)
const errorMessage = ref('')

async function loadReport(): Promise<void> {
  const reportId = Number(route.params.reportId)
  if (!Number.isFinite(reportId) || reportId <= 0) {
    errorMessage.value = '报告 ID 无效。'
    return
  }
  loading.value = true
  errorMessage.value = ''
  try {
    report.value = await api.getStockAnalysisReport(reportId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告加载失败。'
  } finally {
    loading.value = false
  }
}

function goBack(): void {
  router.push({ name: 'stock-analysis' })
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 19)
}

function actionClass(action: string): string {
  if (action === 'BUY') return 'action-buy'
  if (action === 'SELL') return 'action-sell'
  return 'action-hold'
}

onMounted(() => {
  loadReport()
})
</script>

<style scoped>
.stock-report-page,
.stock-report-panel,
.report-section-list,
.report-section {
  display: grid;
  gap: 12px;
}

.report-muted,
.report-summary span,
.report-section p,
.report-item-grid span {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}

.report-summary {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  border: 1px solid #ececf1;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 12px;
}

.report-summary b {
  border-radius: 999px;
  padding: 6px 9px;
  font-size: 12px;
}

.report-summary strong,
.report-section h3,
.report-item-grid strong {
  color: #111827;
}

.report-skill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.report-skill-row span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #ffffff;
  color: #374151;
  padding: 6px 9px;
  font-size: 12px;
}

.report-section {
  border: 1px solid #ececf1;
  border-radius: 8px;
  padding: 12px;
}

.report-section h3 {
  margin: 0;
  font-size: 16px;
}

.report-item-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.report-item-grid div {
  display: grid;
  gap: 4px;
  border: 1px solid #f1f2f4;
  border-radius: 8px;
  background: #ffffff;
  padding: 10px;
}

.action-buy {
  background: #ecfdf5;
  color: #047857;
}

.action-sell {
  background: #fff1f2;
  color: #be123c;
}

.action-hold {
  background: #f3f4f6;
  color: #374151;
}

@media (max-width: 760px) {
  .report-summary,
  .report-item-grid {
    grid-template-columns: 1fr;
  }
}
</style>
