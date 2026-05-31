import { createRouter, createWebHistory } from 'vue-router'
import { appNavigation } from '@/config/navigation'
import { getStoredLoginFlag, getStoredToken } from '@/services/api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      redirect: appNavigation[0].path
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue')
    },
    {
      path: '/overview',
      redirect: '/arena'
    },
    {
      path: '/arena',
      name: 'arena',
      component: () => import('@/views/ArenaView.vue')
    },
    {
      path: '/arena/agents/:agentId',
      name: 'arena-agent-detail',
      component: () => import('@/views/ArenaAgentDetailView.vue')
    },
    {
      path: '/stock-analysis',
      name: 'stock-analysis',
      component: () => import('@/views/StockAnalysisView.vue')
    },
    {
      path: '/data-lab',
      name: 'data-lab',
      component: () => import('@/views/DataLabView.vue')
    },
    {
      path: '/tasks',
      redirect: '/arena'
    },
    {
      path: '/chat',
      redirect: '/stock-analysis'
    },
    {
      path: '/schedule',
      redirect: '/data-lab'
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue')
    }
  ]
})

router.beforeEach((to) => {
  const isAuthenticated = getStoredLoginFlag() && !!getStoredToken()

  if (to.path === '/login') {
    if (isAuthenticated) {
      return appNavigation[0].path
    }
    return true
  }

  if (!isAuthenticated) {
    return '/login'
  }

  return true
})

export default router
