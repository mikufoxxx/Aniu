<template>
  <div class="tab-content">
    <section class="content-grid content-grid-primary">
      <section class="panel settings-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>功能设置</h2>
            <p class="section-kicker">Configuration</p>
          </div>
        </div>

        <div class="settings-two-col">
          <div class="settings-left">
            <label class="field">
              <span>Base URL</span>
              <input v-model="settings.llm_base_url" placeholder="https://api.openai.com/v1" />
              <p class="field-help">大模型 API 的基础地址，默认可填写 OpenAI 兼容地址。</p>
            </label>
            <label class="field">
              <span>API Key</span>
              <input v-model="settings.llm_api_key" type="password" placeholder="sk-..." />
              <p class="field-help">用于访问大模型 API 的密钥。</p>
            </label>
            <div class="settings-inline-fields">
              <label class="field">
                <span>模型名</span>
                <input v-model="settings.llm_model" />
                <p class="field-help">要使用的大模型名称，例如 `gpt-4o-mini`。</p>
              </label>
              <label class="field">
                <span>最大上下文</span>
                <input v-model.number="settings.automation_context_window_tokens" type="number" min="4096" step="1024" />
                <p class="field-help">默认 128K。后端会按该值的 85% 作为自动化会话上下文压缩触发预算。</p>
              </label>
            </div>
            <label class="field">
              <span>妙想密钥</span>
              <input v-model="settings.mx_api_key" type="password" placeholder="妙想接口 apikey" />
              <p class="field-help">用于访问东方财富妙想接口的密钥。</p>
            </label>
            <div class="settings-inline-fields">
              <label class="field">
                <span>Tushare Token</span>
                <input v-model="settings.tushare_token" type="password" placeholder="Tushare token" />
                <p class="field-help">优先级高于服务器环境变量，用于日线、资金流、财务和回测数据。</p>
              </label>
              <label class="field">
                <span>Tushare URL</span>
                <input v-model="settings.tushare_api_url" placeholder="http://api.tushare.pro" />
                <p class="field-help">支持官方地址或你自己的代理地址。</p>
              </label>
            </div>
            <label class="field">
              <span>多 AI 服务商</span>
              <textarea v-model="providerConfigsText" rows="8" spellcheck="false" />
              <p class="field-help">JSON 格式。key 是 provider，例如 deepseek；value 支持 base_url、api_key、default_model。</p>
            </label>
          </div>
          <div class="settings-right">
            <div class="settings-ai-config">
              <div>
                <span class="meta-label">AI 竞技场 / 股票分析</span>
                <strong>已配置模型 {{ settings.forecast_ai_config.model_count }}</strong>
              </div>
              <div class="settings-ai-config-grid">
                <span>接口</span>
                <b>{{ settings.forecast_ai_config.base_url || '--' }}</b>
                <span>API Key</span>
                <b>{{ settings.forecast_ai_config.api_key_configured ? settings.forecast_ai_config.api_key_masked : '未配置' }}</b>
              </div>
              <div class="settings-model-chips">
                <span v-for="model in settings.forecast_ai_config.models" :key="model">{{ model }}</span>
                <span v-if="!settings.forecast_ai_config.models.length">暂无模型</span>
              </div>
            </div>
            <label class="field">
              <span>竞技场统一初始资金</span>
              <input
                v-model.number="settings.arena_initial_cash"
                type="number"
                min="10000"
                max="100000000"
                step="10000"
              />
              <p class="field-help">用于新 AI 账户和自动定时任务；已有账户不自动重置。</p>
            </label>
            <label class="field">
              <span>系统提示词</span>
              <textarea v-model="settings.system_prompt" rows="8" />
              <p class="field-help">指导大模型行为的系统提示词，会影响 AI 的分析和决策方式。</p>
            </label>
          </div>
        </div>

        <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>

        <div class="panel-actions">
          <button
            class="button primary"
            :class="{ 'is-loading': busy }"
            :disabled="busy"
            @click="saveSettingsWithProviderConfigs"
          >
            <span class="material-symbols-rounded" aria-hidden="true">save</span>
            保存设置
          </button>
        </div>
      </section>

      <section class="panel skills-panel">
        <div class="panel-head">
          <div class="head-main">
            <h2>技能管理</h2>
            <p class="section-kicker">Skills</p>
          </div>
          <button
            class="button ghost small soft-header-button overview-refresh-button"
            :class="{ 'is-loading': skillsBusy }"
            :disabled="skillsBusy"
            @click="reloadSkills"
          >
            <span class="material-symbols-rounded" aria-hidden="true">sync</span>
            重新扫描
          </button>
        </div>

        <div class="skills-toolbar">
          <div class="skills-overview-card">
            <span class="material-symbols-rounded skills-overview-icon" aria-hidden="true">inventory_2</span>
            <div>
              <span class="meta-label">已安装技能</span>
              <strong>{{ installedOverview.total }}</strong>
            </div>
            <div class="skills-overview-breakdown">
              <span>运行时技能 {{ installedOverview.runtime }}</span>
              <span>标准技能 {{ installedOverview.standard }}</span>
            </div>
          </div>
          <div class="skills-overview-card">
            <span class="material-symbols-rounded skills-overview-icon" aria-hidden="true">toggle_on</span>
            <div>
              <span class="meta-label">已启用技能</span>
              <strong>{{ enabledOverview.total }}</strong>
            </div>
            <div class="skills-overview-breakdown">
              <span>运行时技能 {{ enabledOverview.runtime }}</span>
              <span>标准技能 {{ enabledOverview.standard }}</span>
            </div>
          </div>
          <div class="skills-import-cluster">
            <div class="skills-import-head">
              <span class="material-symbols-rounded" aria-hidden="true">add_link</span>
              <div>
                <span class="meta-label skill-import-hint">导入技能</span>
                <p>SkillHub 链接或 zip 技能包</p>
              </div>
            </div>
            <div class="skills-import-inline">
              <label class="field skill-import-field">
                <div class="skill-import-control" :class="{ 'is-disabled': skillsBusy }">
                  <input
                    v-model="importInput"
                    placeholder="https://skillhub.cn链接或者技能名称"
                    :disabled="skillsBusy"
                    @input="handleImportInput"
                  />
                  <button
                    type="button"
                    class="button ghost small skill-import-file-button"
                    :disabled="skillsBusy"
                    @click="openImportFileDialog"
                  >
                    <span class="material-symbols-rounded" aria-hidden="true">upload_file</span>
                    {{ selectedArchive ? '更换文件' : '添加文件' }}
                  </button>
                </div>
                <input
                  ref="skillArchiveInputRef"
                  class="skill-import-native-input"
                  type="file"
                  accept=".zip,application/zip"
                  :disabled="skillsBusy"
                  @change="handleImportFileChange"
                />
              </label>
              <button
                class="button primary skills-import-submit"
                :class="{ 'is-loading': skillsBusy }"
                :disabled="skillsBusy"
                @click="importSkill"
              >
                <span class="material-symbols-rounded" aria-hidden="true">move_to_inbox</span>
                导入
              </button>
            </div>
            <p v-if="selectedArchive" class="skill-import-selected">
              已选择文件：{{ selectedArchive.name }}
            </p>
          </div>
        </div>

        <div v-if="skillsErrorMessage" class="error-banner">{{ skillsErrorMessage }}</div>

        <div v-if="skills.length" class="skill-card-list">
          <article v-for="skill in skills" :key="skill.id" class="skill-card" :class="{ 'is-disabled': !skill.enabled }">
            <div class="skill-card-main">
              <div class="skill-card-head">
                <span class="skill-icon-tile" :class="skillSourceClass(skill)" aria-hidden="true">
                  <span class="material-symbols-rounded">{{ skillIcon(skill) }}</span>
                </span>
                <div class="skill-title-block">
                  <div class="skill-title-row">
                    <strong>{{ skill.name }}</strong>
                    <span class="skill-source-badge" :class="skillSourceClass(skill)">
                      {{ skillSourceLabel(skill) }}
                    </span>
                    <span class="skill-source-badge" :class="skillCompatibilityClass(skill)">
                      {{ skillCompatibilityLabel(skill.compatibility_level) }}
                    </span>
                  </div>
                  <p class="skill-card-subtitle">{{ skillSubtitle(skill) }}</p>
                </div>
              </div>

              <p class="skill-card-description">
                {{ skill.description || '暂无技能描述。' }}
              </p>

              <div class="skill-card-meta-grid">
                <div>
                  <span>工具</span>
                  <strong>{{ skillToolCount(skill) }}</strong>
                </div>
                <div>
                  <span>运行域</span>
                  <strong>{{ skillRunTypeLabel(skill) }}</strong>
                </div>
                <div>
                  <span>执行</span>
                  <strong>{{ skillExecutionLabel(skill) }}</strong>
                </div>
                <div>
                  <span>文件</span>
                  <strong>{{ skillSupportFileCount(skill) }}</strong>
                </div>
              </div>

              <div v-if="skillPreviewTools(skill).length" class="skill-chip-row">
                <span v-for="tool in skillPreviewTools(skill)" :key="tool" class="skill-chip">
                  {{ tool }}
                </span>
              </div>
            </div>

            <div class="skill-card-actions">
              <button
                v-if="skill.can_delete"
                type="button"
                class="button ghost small soft-header-button skill-delete-action"
                :disabled="skillsBusy"
                @click="deleteSkill(skill)"
              >
                <span class="material-symbols-rounded" aria-hidden="true">delete</span>
                删除
              </button>
              <span
                v-else
                class="skill-lock-chip"
              >
                <span class="material-symbols-rounded" aria-hidden="true">lock</span>
                不可删除
              </span>
              <button
                type="button"
                class="skill-toggle"
                :class="{ 'is-on': skill.enabled }"
                :disabled="skillsBusy || !canToggleSkill(skill)"
                role="switch"
                :aria-checked="skill.enabled"
                @click="toggleSkill(skill)"
              >
                <span class="skill-toggle-thumb" aria-hidden="true"></span>
                {{ skill.enabled ? '已启用' : '已停用' }}
              </button>
            </div>
          </article>
        </div>

        <div v-else class="empty-state">
          <p>当前还没有可展示的技能。</p>
        </div>
      </section>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useSkillManager } from '@/composables/useSkillManager'
import { api } from '@/services/api'
import { useAppStore } from '@/stores/legacy'
import type { SkillCompatibilityLevel, SkillListItem } from '@/types'

const store = useAppStore()
const { settings, busy, errorMessage } = storeToRefs(store)
const { saveSettings } = store
const {
  skills,
  importInput,
  selectedArchive,
  busy: skillsBusy,
  errorMessage: skillsErrorMessage,
  installedOverview,
  enabledOverview,
  setSkills,
  setImportFile,
  importSkill: submitSkillImport,
  reloadSkills: reloadSkillList,
  toggleSkill: toggleManagedSkill,
  deleteSkill: deleteManagedSkill,
} = useSkillManager()
const skillArchiveInputRef = ref<HTMLInputElement | null>(null)
const providerConfigsText = ref('{}')

function formatProviderConfigs(value: Record<string, Record<string, unknown>> | undefined) {
  return JSON.stringify(value ?? {}, null, 2)
}

async function saveSettingsWithProviderConfigs() {
  try {
    const parsed = JSON.parse(providerConfigsText.value || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('多 AI 服务商配置必须是 JSON 对象。')
    }
    settings.value.llm_provider_configs = parsed
    await saveSettings()
    providerConfigsText.value = formatProviderConfigs(settings.value.llm_provider_configs)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '多 AI 服务商配置格式错误。'
  }
}

function openImportFileDialog() {
  if (skillArchiveInputRef.value) {
    skillArchiveInputRef.value.value = ''
    skillArchiveInputRef.value.click()
  }
}

function resetNativeSkillInput() {
  if (skillArchiveInputRef.value) {
    skillArchiveInputRef.value.value = ''
  }
}

function handleImportInput() {
  if (!importInput.value.trim()) {
    return
  }
  setImportFile(null)
  resetNativeSkillInput()
}

function handleImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0] ?? null
  setImportFile(file)
}

async function importSkill() {
  const imported = await submitSkillImport()
  if (imported) {
    resetNativeSkillInput()
  }
}

async function reloadSkills() {
  await reloadSkillList()
}

async function toggleSkill(skill: SkillListItem) {
  if (!canToggleSkill(skill)) {
    return
  }
  await toggleManagedSkill(skill)
}

async function deleteSkill(skill: SkillListItem) {
  if (!skill.can_delete) {
    return
  }
  await deleteManagedSkill(skill)
}

function canToggleSkill(skill: SkillListItem) {
  return skill.can_disable
}

function skillSourceLabel(skill: SkillListItem) {
  if (skill.role === 'runtime') return '运行时'
  return skill.source === 'builtin' ? '内置' : '用户'
}

function skillSourceClass(skill: SkillListItem) {
  if (skill.role === 'runtime') return 'is-runtime'
  return skill.source === 'builtin' ? 'is-builtin' : 'is-workspace'
}

function skillCompatibilityLabel(level: SkillCompatibilityLevel) {
  if (level === 'native') return '原生兼容'
  if (level === 'needs_attention') return '需校验'
  return '提示词'
}

function skillCompatibilityClass(skill: SkillListItem) {
  return `is-compat-${skill.compatibility_level}`
}

function skillIcon(skill: SkillListItem) {
  if (skill.role === 'runtime') return 'memory'
  if (skill.id.includes('chat')) return 'forum'
  if (skill.id.includes('mx')) return 'monitoring'
  if (skill.source === 'workspace') return 'extension'
  return 'deployed_code'
}

function skillSubtitle(skill: SkillListItem) {
  const segments = [skill.id]
  if (skill.category) segments.push(skill.category)
  if (skill.clawhub_version) segments.push(`v${skill.clawhub_version}`)
  return segments.join(' · ')
}

function skillToolCount(skill: SkillListItem) {
  return skill.tool_names.length
}

function skillRunTypeLabel(skill: SkillListItem) {
  if (!skill.run_types.length) return '全局'
  return skill.run_types.slice(0, 2).join(' / ')
}

function skillExecutionLabel(skill: SkillListItem) {
  if (skill.role === 'runtime') return '底座'
  return skill.has_handler ? '原生' : '运行时'
}

function skillSupportFileCount(skill: SkillListItem) {
  return skill.support_files.length
}

function skillPreviewTools(skill: SkillListItem) {
  return skill.tool_names.slice(0, 4)
}

onMounted(async () => {
  try {
    const payload = await api.getSettingsWorkspace()
    store.applySettings(payload.settings)
    setSkills(payload.skills)
    providerConfigsText.value = formatProviderConfigs(settings.value.llm_provider_configs)
  } catch (error) {
    errorMessage.value = (error as Error).message
  }
})
</script>

<style scoped>
.settings-ai-config {
  display: grid;
  gap: 10px;
  border: 1px solid #ececf1;
  border-radius: 8px;
  background: #fbfbfc;
  padding: 12px;
}

.settings-ai-config strong {
  display: block;
  margin-top: 3px;
  color: #111827;
  font-size: 15px;
}

.settings-ai-config-grid {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 6px 10px;
  color: #6b7280;
  font-size: 12px;
}

.settings-ai-config-grid b {
  overflow: hidden;
  color: #111827;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-model-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.settings-model-chips span {
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  background: #ffffff;
  color: #374151;
  padding: 5px 8px;
  font-size: 12px;
}
</style>
