<template>
  <div :class="['page-shell', { 'is-login-shell': isLoginPage, 'is-sidebar-collapsed': sidebarCollapsed }]">
    <aside v-if="!isLoginPage" class="app-sidebar">
      <div class="app-sidebar-top">
        <button
          class="sidebar-collapse-button"
          type="button"
          :aria-label="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <span class="material-symbols-rounded" aria-hidden="true">
            {{ sidebarCollapsed ? 'chevron_right' : 'chevron_left' }}
          </span>
        </button>
      </div>

      <nav class="app-header-nav">
        <router-link
          v-for="tab in sidebarNavigation"
          :key="tab.id"
          :to="tab.path"
          :class="['tab-button', { active: isSidebarTabActive(tab) }]"
          :title="tab.name"
        >
          <span class="material-symbols-rounded tab-button-icon" aria-hidden="true">{{ tab.icon }}</span>
          <span class="tab-button-label">{{ tab.name }}</span>
        </router-link>
      </nav>

      <div class="app-sidebar-footer">
        <small>v{{ appVersion }}</small>
        <RouterLink
          v-if="isArenaAgentDetail"
          class="header-action-button"
          to="/arena"
        >
          <span class="material-symbols-rounded" aria-hidden="true">arrow_back</span>
          <span>返回</span>
        </RouterLink>
        <button v-else class="header-action-button" type="button" @click="handleLogout">
          <span class="material-symbols-rounded" aria-hidden="true">logout</span>
          <span>退出</span>
        </button>
      </div>
    </aside>

    <main :class="['stack-layout', { 'is-market-map-route': isMarketMapPage }]">
      <div v-if="errorMessage && !isLoginPage" class="hero-error app-shell-error">
        {{ errorMessage }}
      </div>
      <router-view></router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { storeToRefs } from 'pinia'
import appPackage from '../package.json'
import { appNavigation } from '@/config/navigation'
import { useAppStore } from '@/stores/legacy'
import {
  clearStoredLoginFlag,
  clearStoredLoginNotice,
  clearStoredLoginRedirect,
  clearStoredToken,
} from '@/services/api'

const store = useAppStore()
const router = useRouter()
const route = useRoute()
const { errorMessage } = storeToRefs(store)
const appVersion = appPackage.version
const sidebarCollapsed = ref(false)

const isLoginPage = computed(() => route.path === '/login')
const isArenaAgentDetail = computed(() => route.name === 'arena-agent-detail')
const isMarketMapPage = computed(() => route.name === 'market-map')
const arenaDetailNavigation = computed(() => {
  const path = route.path
  return [
    { id: 'morning', name: '早盘', path: `${path}?section=morning`, icon: 'wb_twilight' },
    { id: 'intraday', name: '盘中', path: `${path}?section=intraday`, icon: 'monitoring' },
    { id: 'closing', name: '收盘', path: `${path}?section=closing`, icon: 'fact_check' },
    { id: 'learning', name: '学习', path: `${path}?section=learning`, icon: 'school' },
  ]
})
const sidebarNavigation = computed(() => isArenaAgentDetail.value ? arenaDetailNavigation.value : appNavigation)

function isSidebarTabActive(tab: { id: string; path: string; icon: string }): boolean {
  if (isArenaAgentDetail.value) {
    return (route.query.section ?? 'morning') === tab.id
  }
  return route.path === tab.path
}

function handleLogout() {
  clearStoredToken()
  clearStoredLoginFlag()
  clearStoredLoginNotice()
  clearStoredLoginRedirect()
  store.resetState()
  router.replace('/login')
}
</script>
