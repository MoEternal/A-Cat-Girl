<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { Blocks, BookOpen, Bot, Contact, LayoutDashboard, LogOut, MessageSquareText, Plug, Radio, ServerCog, SlidersHorizontal, UserRound, UserRoundCog } from '@lucide/vue'
import { api, AUTH_REQUIRED_EVENT, json } from './api'
import type { AuthStatus } from './types'
import AuthView from './components/AuthView.vue'

const route = useRoute()
const online = ref(false)
const auth = ref<AuthStatus | null>(null)
const authLoading = ref(true)
const authLoadError = ref('')
const loggingOut = ref(false)
const title = computed(() => String(route.meta.title ?? 'A CAT GIRL'))
const themeStorageKey = 'catgirl.console.theme-stage.v1'
const themeStages = [
  { name: '红色', accent: '#be3030', accentRgb: '190, 48, 48', light: '#e66962', text: '#d2a6a2', textRgb: '210, 166, 162' },
  { name: '橙色', accent: '#d87a36', accentRgb: '216, 122, 54', light: '#f4ae6b', text: '#dbc1aa', textRgb: '219, 193, 170' },
  { name: '金色', accent: '#e8b84a', accentRgb: '232, 184, 74', light: '#f7d58b', text: '#d9c99f', textRgb: '217, 201, 159' },
  { name: '绿色', accent: '#469d68', accentRgb: '70, 157, 104', light: '#7ecf9a', text: '#b6d5be', textRgb: '182, 213, 190' },
  { name: '青色', accent: '#39c5bb', accentRgb: '57, 197, 187', light: '#82e4da', text: '#b1d8d4', textRgb: '177, 216, 212' },
  { name: '蓝色', accent: '#4b82ca', accentRgb: '75, 130, 202', light: '#89b5e8', text: '#b6c9df', textRgb: '182, 201, 223' },
  { name: '紫色', accent: '#8d6ac8', accentRgb: '141, 106, 200', light: '#be9de8', text: '#cabadd', textRgb: '202, 186, 221' },
]
const themeStage = ref(1)
const currentTheme = computed(() => themeStages[themeStage.value] ?? themeStages[1])
const navigation = [
  { to: '/', label: '配置总览', icon: LayoutDashboard },
  { to: '/qq-connection', label: 'QQ连接', icon: Radio },
  { to: '/presets', label: '预设配置', icon: SlidersHorizontal },
  { to: '/providers', label: 'API配置', icon: ServerCog },
  { to: '/prompts', label: '提示词编辑', icon: Blocks },
  { to: '/user-personas', label: '用户人设', icon: Contact },
  { to: '/characters', label: '角色人设', icon: UserRoundCog },
  { to: '/chat-history', label: '聊天记录', icon: MessageSquareText },
  { to: '/world-books', label: '世界书', icon: BookOpen },
  { to: '/plugins', label: '插件', icon: Plug },
]

onMounted(async () => {
  try {
    const stored = localStorage.getItem(themeStorageKey)
    const saved = stored === null ? Number.NaN : Number(stored)
    if (Number.isInteger(saved) && saved >= 0 && saved < themeStages.length) themeStage.value = saved
  } catch {}
  applyTheme(themeStage.value)
  window.addEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired)
  await loadAuthStatus()
  try {
    const response = await fetch('/health')
    online.value = response.ok
  } catch {
    online.value = false
  }
})

onBeforeUnmount(() => window.removeEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired))

async function loadAuthStatus() {
  authLoading.value = true
  authLoadError.value = ''
  try {
    auth.value = await api<AuthStatus>('/api/auth/status')
  } catch (reason) {
    authLoadError.value = reason instanceof Error ? reason.message : '登录状态加载失败'
  } finally {
    authLoading.value = false
  }
}

function handleAuthRequired() {
  auth.value = { setup_required: false, authenticated: false, username: '' }
}

function handleAuthenticated(status: AuthStatus) {
  auth.value = status
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    await api<void>('/api/auth/logout', json('POST'))
  } finally {
    loggingOut.value = false
    auth.value = { setup_required: false, authenticated: false, username: '' }
  }
}

function applyTheme(stage: number) {
  const theme = themeStages[stage] ?? themeStages[1]
  const root = document.documentElement.style
  root.setProperty('--coral', theme.accent)
  root.setProperty('--accent', theme.accent)
  root.setProperty('--accent-rgb', theme.accentRgb)
  root.setProperty('--accent-light', theme.light)
  root.setProperty('--text', theme.text)
  root.setProperty('--text-rgb', theme.textRgb)
}

function updateTheme(event: Event) {
  setThemeStage(Number((event.target as HTMLInputElement).value))
}

function setThemeStage(value: number) {
  const stage = Math.max(0, Math.min(themeStages.length - 1, value))
  themeStage.value = stage
  applyTheme(stage)
  try {
    localStorage.setItem(themeStorageKey, String(stage))
  } catch {}
}
</script>

<template>
  <main v-if="authLoading || authLoadError" class="auth-screen auth-state-screen">
    <div v-if="authLoadError" class="auth-state-message">
      <strong>无法连接管理服务</strong>
      <span>{{ authLoadError }}</span>
      <button class="button secondary" type="button" @click="loadAuthStatus">重试</button>
    </div>
    <span v-else class="auth-loading-indicator" aria-label="正在加载" />
  </main>
  <AuthView
    v-else-if="!auth?.authenticated"
    :setup-required="auth?.setup_required ?? true"
    :theme-stages="themeStages"
    :theme-stage="themeStage"
    @authenticated="handleAuthenticated"
    @theme-change="setThemeStage"
  />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark"><img src="/catgirl-logo.png" alt="" /></span>
        <div>
          <strong>一只猫娘</strong>
          <span>管理控制台</span>
        </div>
      </div>

      <nav class="main-nav" aria-label="主导航">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to">
          <component :is="item.icon" :size="18" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="sidebar-footer">
        <Bot :size="18" />
        <div>
          <span class="status-line"><i :class="{ online }" />{{ online ? '服务正常' : '服务离线' }}</span>
          <small>v1.0.0</small>
        </div>
      </div>
    </aside>

    <section class="workspace">
      <header class="topbar">
        <div>
          <span class="eyebrow">管理控制台</span>
          <h1>{{ title }}</h1>
        </div>
        <div class="topbar-state">
          <div class="theme-stage-control" title="切换控制台主题色">
            <span class="visually-hidden">主题色：{{ currentTheme.name }}</span>
            <div class="theme-stage-track">
              <span class="theme-stage-line" aria-hidden="true" />
              <span class="theme-stage-dots" aria-hidden="true">
                <i v-for="(stage, index) in themeStages" :key="stage.name" :style="{ '--stage-left': `${index * 100 / (themeStages.length - 1)}%`, '--stage-color': stage.accent }" />
              </span>
              <input
                class="theme-stage-slider"
                type="range"
                min="0"
                :max="themeStages.length - 1"
                step="1"
                :value="themeStage"
                :aria-label="`主题色：${currentTheme.name}`"
                :title="`主题色：${currentTheme.name}`"
                @input="updateTheme"
              />
            </div>
          </div>
          <span :class="['connection-state', { online }]">
            <i />{{ online ? '已连接' : '未连接' }}
          </span>
          <span class="admin-session"><UserRound :size="15" /><span>{{ auth.username }}</span></span>
          <button class="icon-button" type="button" title="退出登录" aria-label="退出登录" :disabled="loggingOut" @click="logout"><LogOut :size="16" /></button>
        </div>
      </header>
      <main class="main-content">
        <RouterView />
      </main>
    </section>

  </div>
</template>
