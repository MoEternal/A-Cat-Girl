<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Blocks, BookOpen, CircleAlert, Contact, Pause, Play, RefreshCw, ServerCog, SlidersHorizontal, Terminal, Trash2, UserRoundCog } from '@lucide/vue'

import { api } from '../api'
import type { Overview, RuntimeLog } from '../types'

const overview = ref<Overview | null>(null)
const loading = ref(true)
const error = ref('')
const logs = ref<RuntimeLog[]>([])
const logsLoading = ref(false)
const logsPaused = ref(false)
const logError = ref('')
const logViewport = ref<HTMLElement | null>(null)
let logTimer: number | undefined

async function load() {
  loading.value = true
  error.value = ''
  try {
    overview.value = await api<Overview>('/api/overview')
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function logTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

function logSource(value: string): string {
  return value.startsWith('catgirl.') ? value.slice(8) : value
}

async function loadLogs(reset = false) {
  if (logsLoading.value) return
  logsLoading.value = true
  logError.value = ''
  try {
    const afterId = reset ? 0 : (logs.value.at(-1)?.id ?? 0)
    const entries = await api<RuntimeLog[]>(`/api/logs?after_id=${afterId}&limit=${reset ? 200 : 500}`)
    logs.value = reset ? entries : [...logs.value, ...entries].slice(-500)
    await nextTick()
    if (logViewport.value) logViewport.value.scrollTop = logViewport.value.scrollHeight
  } catch (reason) {
    logError.value = reason instanceof Error ? reason.message : '日志读取失败'
  } finally {
    logsLoading.value = false
  }
}

async function clearLogs() {
  logError.value = ''
  try {
    await api('/api/logs', { method: 'DELETE' })
    logs.value = []
  } catch (reason) {
    logError.value = reason instanceof Error ? reason.message : '日志清空失败'
  }
}

function toggleLogs() {
  logsPaused.value = !logsPaused.value
  if (!logsPaused.value) void loadLogs()
}

onMounted(async () => {
  await Promise.all([load(), loadLogs(true)])
  logTimer = window.setInterval(() => {
    if (!logsPaused.value) void loadLogs()
  }, 1500)
})

onBeforeUnmount(() => {
  if (logTimer !== undefined) window.clearInterval(logTimer)
})
</script>

<template>
  <div class="page-stack">
    <div v-if="error" class="notice error-notice">
      <CircleAlert :size="18" />
      <span>{{ error }}</span>
      <button class="icon-button" type="button" title="重试" @click="load"><RefreshCw :size="16" /></button>
    </div>

    <section class="stats-grid" aria-label="配置统计">
      <article class="stat-card">
        <span class="stat-icon amber"><SlidersHorizontal :size="20" /></span>
        <div><strong>{{ overview?.counts.presets ?? '—' }}</strong><span>组合预设</span></div>
      </article>
      <article class="stat-card">
        <span class="stat-icon coral"><ServerCog :size="20" /></span>
        <div><strong>{{ overview?.counts.providers ?? '—' }}</strong><span>API 供应商</span></div>
      </article>
      <article class="stat-card">
        <span class="stat-icon blue"><Blocks :size="20" /></span>
        <div><strong>{{ overview?.counts.templates ?? '—' }}</strong><span>提示词模板</span></div>
      </article>
      <article class="stat-card">
        <span class="stat-icon green"><UserRoundCog :size="20" /></span>
        <div><strong>{{ overview?.counts.characters ?? '—' }}</strong><span>角色人设</span></div>
      </article>
      <article class="stat-card">
        <span class="stat-icon violet"><Contact :size="20" /></span>
        <div><strong>{{ overview?.counts.user_personas ?? '—' }}</strong><span>用户人设</span></div>
      </article>
      <article class="stat-card">
        <span class="stat-icon violet"><BookOpen :size="20" /></span>
        <div><strong>{{ overview?.counts.world_books ?? '—' }}</strong><span>世界书</span></div>
      </article>
    </section>

    <section class="section-band">
      <div class="section-heading">
        <div>
          <span class="eyebrow">ACTIVE CONFIGURATION</span>
          <h2>当前生效配置</h2>
        </div>
        <button class="icon-button" type="button" title="刷新" :disabled="loading" @click="load">
          <RefreshCw :size="17" :class="{ spinning: loading }" />
        </button>
      </div>

      <div class="active-rows">
        <RouterLink to="/presets" class="active-row">
          <span class="row-icon amber"><SlidersHorizontal :size="19" /></span>
          <div><small>组合预设</small><strong>{{ overview?.active_preset?.name ?? '未配置' }}</strong></div>
          <span class="row-detail">{{ overview?.active_preset?.context_length ?? 0 }} 上下文</span>
        </RouterLink>
        <RouterLink to="/providers" class="active-row">
          <span class="row-icon coral"><ServerCog :size="19" /></span>
          <div><small>API 供应商</small><strong>{{ overview?.active_provider?.name ?? '未配置' }}</strong></div>
          <span class="row-detail">{{ overview?.active_provider?.model || '未选择模型' }}</span>
        </RouterLink>
        <RouterLink to="/prompts" class="active-row">
          <span class="row-icon blue"><Blocks :size="19" /></span>
          <div><small>提示词模板</small><strong>{{ overview?.active_template?.name ?? '未配置' }}</strong></div>
          <span class="row-detail">{{ overview?.active_template?.blocks.length ?? 0 }} 个块</span>
        </RouterLink>
        <RouterLink to="/characters" class="active-row">
          <span class="row-icon green"><UserRoundCog :size="19" /></span>
          <div><small>当前角色人设</small><strong>{{ overview?.active_character?.name ?? '未配置' }}</strong></div>
          <span class="row-detail">{{ overview?.active_character?.summary || '暂无简介' }}</span>
        </RouterLink>
        <RouterLink to="/user-personas" class="active-row">
          <span class="row-icon violet"><Contact :size="19" /></span>
          <div><small>当前用户人设</small><strong>{{ overview?.active_user_persona?.name ?? '未配置' }}</strong></div>
          <span class="row-detail">{{ overview?.active_user_persona ? '已配置' : '未配置' }}</span>
        </RouterLink>
        <RouterLink to="/world-books" class="active-row">
          <span class="row-icon violet"><BookOpen :size="19" /></span>
          <div><small>生效世界书</small><strong>{{ overview?.active_world_book_ids.length ?? 0 }} 本</strong></div>
          <span class="row-detail">预设、角色与全局范围</span>
        </RouterLink>
      </div>
    </section>

    <section class="section-band log-section">
      <div class="section-heading log-heading">
        <div>
          <span class="eyebrow">RUNTIME LOG</span>
          <h2>日志</h2>
        </div>
        <div class="log-actions">
          <span :class="['log-live-state', { paused: logsPaused }]">{{ logsPaused ? '已暂停' : '实时' }}</span>
          <button class="icon-button" type="button" :title="logsPaused ? '继续刷新日志' : '暂停刷新日志'" @click="toggleLogs">
            <Play v-if="logsPaused" :size="16" />
            <Pause v-else :size="16" />
          </button>
          <button class="icon-button" type="button" title="刷新日志" :disabled="logsLoading" @click="loadLogs(true)">
            <RefreshCw :size="16" :class="{ spinning: logsLoading }" />
          </button>
          <button class="icon-button danger" type="button" title="清空日志窗口" @click="clearLogs">
            <Trash2 :size="16" />
          </button>
        </div>
      </div>
      <div v-if="logError" class="log-inline-error"><CircleAlert :size="15" />{{ logError }}</div>
      <div ref="logViewport" class="log-viewport" role="log" aria-label="运行日志">
        <div v-if="!logs.length" class="log-empty"><Terminal :size="18" />暂无日志</div>
        <div v-for="entry in logs" :key="entry.id" :class="['log-line', `level-${entry.level.toLowerCase()}`]">
          <time :datetime="entry.created_at">{{ logTime(entry.created_at) }}</time>
          <strong>{{ entry.level }}</strong>
          <code>{{ logSource(entry.source) }}</code>
          <span>{{ entry.message }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
