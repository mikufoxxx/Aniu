<template>
  <div :class="['page-shell', { 'is-login-shell': isLoginPage, 'is-sidebar-collapsed': sidebarCollapsed }]">
    <aside v-if="!isLoginPage" class="app-sidebar">
      <div class="app-brand">
        <img class="app-brand-logo" src="/aniu.ico" alt="Aniu logo" />
        <div class="app-brand-copy">
          <strong>Aniu</strong>
          <span>AI Trading Lab</span>
        </div>
        <button class="sidebar-collapse-button" type="button" @click="sidebarCollapsed = !sidebarCollapsed">
          {{ sidebarCollapsed ? '>' : '<' }}
        </button>
      </div>

      <nav class="app-header-nav">
        <router-link
          v-for="tab in sidebarNavigation"
          :key="tab.id"
          :to="tab.path"
          class="tab-button"
          active-class="active"
        >
          {{ tab.name }}
        </router-link>
      </nav>

      <div class="app-sidebar-footer">
        <small>v{{ appVersion }}</small>
        <RouterLink
          v-if="isArenaAgentDetail"
          class="header-action-button"
          to="/arena"
        >
          返回
        </RouterLink>
        <button v-else class="header-action-button" type="button" @click="handleLogout">退出</button>
      </div>
    </aside>

    <main class="stack-layout">
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
const arenaDetailNavigation = computed(() => {
  const path = route.path
  return [
    { id: 'morning', name: '早盘', path: `${path}?section=morning` },
    { id: 'intraday', name: '盘中', path: `${path}?section=intraday` },
    { id: 'closing', name: '收盘', path: `${path}?section=closing` },
    { id: 'learning', name: '学习', path: `${path}?section=learning` },
  ]
})
const sidebarNavigation = computed(() => isArenaAgentDetail.value ? arenaDetailNavigation.value : appNavigation)

function handleLogout() {
  clearStoredToken()
  clearStoredLoginFlag()
  clearStoredLoginNotice()
  clearStoredLoginRedirect()
  store.resetState()
  router.replace('/login')
}
</script>
