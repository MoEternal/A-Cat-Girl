<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  Check,
  CircleAlert,
  Copy,
  KeyRound,
  Link,
  Radio,
  RefreshCw,
  Save,
  ShieldAlert,
  Wifi,
  WifiOff,
} from '@lucide/vue'

import { api, json } from '../api'
import type { OneBotConfig, OneBotStatus } from '../types'

const config = ref<OneBotConfig | null>(null)
const status = ref<OneBotStatus | null>(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
let statusTimer: number | undefined

const form = reactive({
  enabled: false,
  connection_mode: 'reverse' as 'reverse' | 'forward',
  reverse_ws_url: '',
  forward_ws_url: '',
  access_token: '',
  private_messages: true,
  group_messages: false,
  private_allowlist: '',
  group_allowlist: '',
  api_timeout_seconds: 15,
})

const defaultReverseUrl = computed(() => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/onebot/v11/ws`
})

const connectionModeLabel = computed(() => form.connection_mode === 'forward' ? '正向 WebSocket' : '反向 WebSocket')

function parseIds(value: string): string[] {
  return value.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean)
}

function applyConfig(value: OneBotConfig) {
  config.value = value
  Object.assign(form, {
    enabled: value.enabled,
    connection_mode: value.connection_mode ?? 'reverse',
    reverse_ws_url: value.reverse_ws_url || defaultReverseUrl.value,
    forward_ws_url: value.forward_ws_url ?? '',
    access_token: '',
    private_messages: value.private_messages,
    group_messages: value.group_messages,
    private_allowlist: value.private_allowlist.join('\n'),
    group_allowlist: value.group_allowlist.join('\n'),
    api_timeout_seconds: value.api_timeout_seconds,
  })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [loadedConfig, loadedStatus] = await Promise.all([
      api<OneBotConfig>('/api/onebot/config'),
      api<OneBotStatus>('/api/onebot/status'),
    ])
    applyConfig(loadedConfig)
    status.value = loadedStatus
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : 'QQ 接入配置加载失败'
  } finally {
    loading.value = false
  }
}

async function refreshStatus() {
  try {
    status.value = await api<OneBotStatus>('/api/onebot/status')
  } catch {
    status.value = null
  }
}

async function save() {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const payload: Record<string, unknown> = {
      enabled: form.enabled,
      connection_mode: form.connection_mode,
      reverse_ws_url: form.reverse_ws_url,
      forward_ws_url: form.forward_ws_url,
      private_messages: form.private_messages,
      group_messages: form.group_messages,
      private_allowlist: parseIds(form.private_allowlist),
      group_allowlist: parseIds(form.group_allowlist),
      api_timeout_seconds: form.api_timeout_seconds,
    }
    if (form.access_token) payload.access_token = form.access_token
    const updated = await api<OneBotConfig>('/api/onebot/config', json('PUT', payload))
    applyConfig(updated)
    await refreshStatus()
    notice.value = 'QQ 接入配置已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function copyUrl() {
  try {
    await navigator.clipboard.writeText(form.reverse_ws_url.trim() || defaultReverseUrl.value)
    notice.value = '反向 WebSocket 地址已复制'
  } catch {
    error.value = '浏览器未允许复制，请手动选择地址'
  }
}

onMounted(async () => {
  await load()
  statusTimer = window.setInterval(refreshStatus, 5000)
})

onUnmounted(() => {
  if (statusTimer !== undefined) window.clearInterval(statusTimer)
})
</script>

<template>
  <div class="page-stack qq-page">
    <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
    <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

    <section class="section-band qq-status-section">
      <div class="section-heading">
        <div><span class="eyebrow">ONEBOT 11</span><h2>连接状态</h2></div>
        <button class="icon-button" type="button" title="刷新状态" @click="refreshStatus"><RefreshCw :size="17" /></button>
      </div>
      <div class="qq-status-content">
        <span :class="['qq-connection-mark', { connected: status?.connected }]">
          <Wifi v-if="status?.connected" :size="22" />
          <WifiOff v-else :size="22" />
        </span>
        <div class="qq-status-primary">
          <strong>{{ status?.connected ? 'NapCat 已连接' : (form.enabled ? (form.connection_mode === 'forward' ? '正在连接 NapCat' : '等待 NapCat 连接') : 'QQ 接入已停用') }}</strong>
          <span>{{ status?.self_ids.length ? `账号 ${status.self_ids.join('、')}` : '暂无在线 QQ 账号' }}</span>
          <span v-if="status?.connection_error" class="danger-text">{{ status.connection_error }}</span>
        </div>
        <div class="qq-status-metrics">
          <span><strong>{{ status?.connections ?? 0 }}</strong>连接</span>
          <span><strong>{{ status?.pending_actions ?? 0 }}</strong>待发送</span>
          <span :class="{ 'danger-text': (status?.failed_actions ?? 0) > 0 }"><strong>{{ status?.failed_actions ?? 0 }}</strong>失败</span>
        </div>
      </div>
    </section>

    <section class="section-band qq-config-section">
      <div class="section-heading">
        <div><span class="eyebrow">{{ connectionModeLabel.toUpperCase() }}</span><h2>接入配置</h2></div>
        <button class="button primary" type="button" :disabled="saving || loading" @click="save"><Save :size="16" />保存配置</button>
      </div>

      <div class="qq-config-body">
        <div v-if="form.enabled && !config?.access_token_configured" class="plugin-security-note qq-token-warning">
          <ShieldAlert :size="19" />
          <div><strong>当前没有访问令牌</strong><span>任何能访问该地址的客户端都可以尝试建立 OneBot 连接。</span></div>
        </div>

        <div class="form-grid qq-config-grid">
          <div class="segmented-control qq-connection-mode span-2" aria-label="WebSocket 接入模式">
            <button :class="{ active: form.connection_mode === 'reverse' }" type="button" @click="form.connection_mode = 'reverse'"><Radio :size="15" />反向 WS</button>
            <button :class="{ active: form.connection_mode === 'forward' }" type="button" @click="form.connection_mode = 'forward'"><Link :size="15" />正向 WS</button>
          </div>
          <label v-if="form.connection_mode === 'reverse'" class="field span-2">
            <span>反向 WebSocket 监听地址</span>
            <div class="input-with-action"><Radio :size="16" /><input v-model.trim="form.reverse_ws_url" type="url" :placeholder="defaultReverseUrl" /><button class="icon-button" type="button" title="复制地址" @click="copyUrl"><Copy :size="16" /></button></div>
          </label>
          <label v-else class="field span-2">
            <span>NapCat 正向 WebSocket 地址</span>
            <div class="input-with-icon"><Link :size="16" /><input v-model.trim="form.forward_ws_url" type="url" placeholder="ws://127.0.0.1:3001" /></div>
          </label>
          <label class="field span-2">
            <span>访问令牌 <small v-if="config?.access_token_configured">已保存 {{ config.access_token_masked }}</small></span>
            <div class="input-with-icon"><KeyRound :size="16" /><input v-model="form.access_token" type="password" autocomplete="new-password" placeholder="留空则保持现有令牌" /></div>
          </label>

          <label class="setting-toggle qq-setting-toggle">
            <input v-model="form.enabled" type="checkbox" /><span class="check-control"><Check :size="13" /></span>
            <span><strong>启用 QQ 接入</strong><small>{{ form.connection_mode === 'forward' ? '由控制台主动连接填写的 NapCat WebSocket 地址。' : '允许 NapCat 建立反向 WebSocket 连接。' }}</small></span>
          </label>
          <label class="setting-toggle qq-setting-toggle">
            <input v-model="form.private_messages" type="checkbox" /><span class="check-control"><Check :size="13" /></span>
            <span><strong>处理私聊消息</strong><small>私聊文本进入当前组合预设。</small></span>
          </label>
          <label class="setting-toggle qq-setting-toggle">
            <input v-model="form.group_messages" type="checkbox" /><span class="check-control"><Check :size="13" /></span>
            <span><strong>处理群聊消息</strong><small>当前会响应允许群中的全部文本消息。</small></span>
          </label>
          <label class="field">
            <span>OneBot 调用超时（秒）</span>
            <input v-model.number="form.api_timeout_seconds" type="number" min="3" max="120" />
          </label>
          <label class="field">
            <span>私聊允许名单 <small>留空表示全部</small></span>
            <textarea v-model="form.private_allowlist" rows="6" placeholder="每行一个 QQ 号" />
          </label>
          <label class="field">
            <span>群聊允许名单 <small>留空表示全部</small></span>
            <textarea v-model="form.group_allowlist" rows="6" placeholder="每行一个群号" />
          </label>
        </div>
      </div>
    </section>
  </div>
</template>
