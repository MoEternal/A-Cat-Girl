<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import {
  Bot,
  BrainCircuit,
  Check,
  ChevronDown,
  ChevronsDown,
  ChevronsUp,
  CircleAlert,
  ListChecks,
  MessageSquareText,
  Pencil,
  Plus,
  Save,
  Trash2,
  Upload,
  UserRound,
  X,
} from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import type { ChatMessage, ConversationRecord } from '../types'

const records = ref<ConversationRecord[]>([])
const messages = ref<ChatMessage[]>([])
const selectedRoute = ref('')
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const deletingMessages = ref(false)
const savingMessage = ref(false)
const error = ref('')
const notice = ref('')
const multiSelect = ref(false)
const selectedMessageIds = ref<string[]>([])
const editingMessageId = ref('')
const editingContent = ref('')
const editingFloorHeight = ref(0)
const messageList = ref<HTMLElement | null>(null)
const floorLimit = ref(100)
const form = reactive({ title: '' })
const routes = computed(() => Array.from(new Set(records.value.map((item) => item.external_id))))
const routeRecords = computed(() => records.value.filter((item) => item.external_id === selectedRoute.value))
const selected = computed(() => records.value.find((item) => item.id === selectedId.value) ?? null)
const visibleMessages = computed(() => (
  floorLimit.value === 0 ? messages.value : messages.value.slice(-floorLimit.value)
))
const allMessagesSelected = computed(() => (
  visibleMessages.value.length > 0
  && visibleMessages.value.every((message) => selectedMessageIds.value.includes(message.id))
))

const floorLimitStorageKey = 'catgirl.chat-history.floor-limit.v1'

function routeLabel(route: string): string {
  const routeRecord = records.value.find((item) => item.external_id === route && item.is_active)
    ?? records.value.find((item) => item.external_id === route)
  return conversationLabel(route, routeRecord?.character_name)
}

function conversationLabel(route: string, characterName?: string | null): string {
  const parts = route.split(':')
  const character = characterName?.trim() || '未选择角色'
  if (parts[0] === 'qq' && parts.length === 4) {
    return parts[2] === 'private'
      ? `私聊 · ${character}`
      : `群聊 · ${character} · 群 ${parts[3]}`
  }
  return route
}

function formatTokenCount(value: number): string {
  return value.toLocaleString('zh-CN')
}

function roleLabel(role: string): string {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '角色'
  return role
}

function speakerName(message: ChatMessage): string {
  return message.speaker_name?.trim() || roleLabel(message.role)
}

function messageReasoning(message: ChatMessage): string {
  const reasoning = message.message_metadata?.reasoning
  return typeof reasoning === 'string' ? reasoning.trim() : ''
}

function formatTime(value: string): string {
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(timestamp))
}

async function selectRecord(record: ConversationRecord) {
  multiSelect.value = false
  selectedMessageIds.value = []
  editingMessageId.value = ''
  editingContent.value = ''
  editingFloorHeight.value = 0
  selectedId.value = record.id
  selectedRoute.value = record.external_id
  form.title = record.title
  error.value = ''
  notice.value = ''
  try {
    messages.value = await api<ChatMessage[]>(`/api/runtime/conversations/${encodeURIComponent(record.id)}/messages`)
    await scrollToLatestMessage()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '聊天消息加载失败'
  }
}

async function load(preferredId?: string, preferredRoute?: string) {
  loading.value = true
  error.value = ''
  try {
    records.value = await api<ConversationRecord[]>('/api/runtime/conversations')
    const route = preferredRoute && routes.value.includes(preferredRoute)
      ? preferredRoute
      : (routes.value.includes(selectedRoute.value) ? selectedRoute.value : routes.value[0] ?? '')
    selectedRoute.value = route
    const target = records.value.find((item) => item.id === preferredId)
      ?? records.value.find((item) => item.id === selectedId.value && item.external_id === route)
      ?? records.value.find((item) => item.external_id === route && item.is_active)
      ?? records.value.find((item) => item.external_id === route)
    if (target) await selectRecord(target)
    else {
      selectedId.value = ''
      messages.value = []
      form.title = ''
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '聊天记录加载失败'
  } finally {
    loading.value = false
  }
}

async function changeRoute() {
  const target = records.value.find((item) => item.external_id === selectedRoute.value && item.is_active)
    ?? routeRecords.value[0]
  if (target) await selectRecord(target)
}

async function createRecord() {
  if (!selectedRoute.value) return
  try {
    const created = await api<ConversationRecord>('/api/runtime/conversations', json('POST', {
      route_id: selectedRoute.value,
      title: `新记录 ${routeRecords.value.length + 1}`,
    }))
    await load(created.id, selectedRoute.value)
    notice.value = '新聊天记录已创建'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function saveTitle() {
  if (!selected.value) return
  saving.value = true
  try {
    const updated = await api<ConversationRecord>(
      `/api/runtime/conversations/${encodeURIComponent(selected.value.id)}`,
      json('PUT', { title: form.title }),
    )
    await load(updated.id, updated.external_id)
    notice.value = '记录名称已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function activateRecord() {
  if (!selected.value) return
  try {
    const updated = await api<ConversationRecord>(
      `/api/runtime/conversations/${encodeURIComponent(selected.value.id)}/activate`,
      json('POST'),
    )
    await load(updated.id, updated.external_id)
    notice.value = '后续消息将使用这份记录'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '切换失败'
  }
}

async function removeRecord() {
  if (!selected.value || !window.confirm(`删除聊天记录“${selected.value.title}”及其中全部消息？`)) return
  const route = selected.value.external_id
  try {
    await api(`/api/runtime/conversations/${encodeURIComponent(selected.value.id)}`, json('DELETE'))
    selectedId.value = ''
    await load(undefined, route)
    notice.value = '聊天记录已删除'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除失败'
  }
}

function toggleMultiSelect() {
  multiSelect.value = !multiSelect.value
  selectedMessageIds.value = []
  editingMessageId.value = ''
  editingContent.value = ''
}

function toggleSelectAll() {
  selectedMessageIds.value = allMessagesSelected.value
    ? []
    : visibleMessages.value.map((message) => message.id)
}

async function removeSelectedMessages() {
  if (!selected.value || selectedMessageIds.value.length === 0) return
  const count = selectedMessageIds.value.length
  if (!window.confirm(`删除已选择的 ${count} 条聊天消息？\n\n只会删除本地聊天记录，不会撤回 QQ 中已经发送的消息。`)) return
  const recordId = selected.value.id
  const route = selected.value.external_id
  deletingMessages.value = true
  error.value = ''
  try {
    await api(
      `/api/runtime/conversations/${encodeURIComponent(recordId)}/messages/delete`,
      json('POST', { message_ids: selectedMessageIds.value }),
    )
    await load(recordId, route)
    notice.value = `已删除 ${count} 条聊天消息`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '删除消息失败'
  } finally {
    deletingMessages.value = false
  }
}

function startEditingMessage(message: ChatMessage, event: MouseEvent) {
  const trigger = event.currentTarget as HTMLElement | null
  const floor = trigger?.closest<HTMLElement>('.history-message')
  editingFloorHeight.value = Math.ceil(floor?.getBoundingClientRect().height ?? 0)
  editingMessageId.value = message.id
  editingContent.value = message.content
  error.value = ''
  notice.value = ''
}

function cancelEditingMessage() {
  editingMessageId.value = ''
  editingContent.value = ''
  editingFloorHeight.value = 0
}

async function scrollToLatestMessage() {
  await nextTick()
  const list = messageList.value
  if (!list || !visibleMessages.value.length) return
  if (list.scrollHeight > list.clientHeight + 1) {
    list.scrollTop = list.scrollHeight
    return
  }
  list.lastElementChild?.scrollIntoView({ block: 'end' })
}

function restoreFloorLimit() {
  try {
    const value = localStorage.getItem(floorLimitStorageKey)
    if (value === null) return
    const stored = Number(value)
    if (Number.isSafeInteger(stored) && stored >= 0) floorLimit.value = stored
  } catch {}
}

function updateFloorLimit(event: Event) {
  const requested = Number((event.target as HTMLInputElement).value)
  floorLimit.value = Number.isFinite(requested)
    ? Math.max(0, Math.min(Math.floor(requested), Number.MAX_SAFE_INTEGER))
    : 100
  selectedMessageIds.value = selectedMessageIds.value.filter((id) => (
    visibleMessages.value.some((message) => message.id === id)
  ))
  try {
    localStorage.setItem(floorLimitStorageKey, String(floorLimit.value))
  } catch {}
  void scrollToLatestMessage()
}

async function saveMessage(message: ChatMessage) {
  if (!selected.value || editingMessageId.value !== message.id) return
  if (!editingContent.value.trim()) {
    error.value = '聊天消息内容不能为空'
    return
  }
  savingMessage.value = true
  error.value = ''
  try {
    const updated = await api<ChatMessage>(
      `/api/runtime/conversations/${encodeURIComponent(selected.value.id)}/messages/${encodeURIComponent(message.id)}`,
      json('PUT', { content: editingContent.value }),
    )
    const index = messages.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) messages.value[index] = updated
    records.value = await api<ConversationRecord[]>('/api/runtime/conversations')
    cancelEditingMessage()
    notice.value = `第 ${message.position + 1} 层已保存`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '编辑消息失败'
  } finally {
    savingMessage.value = false
  }
}

function scrollMessages(boundary: 'top' | 'bottom') {
  const list = messageList.value
  if (!list) return
  if (list.scrollHeight > list.clientHeight + 1) {
    list.scrollTo({ top: boundary === 'top' ? 0 : list.scrollHeight, behavior: 'smooth' })
    return
  }
  const target = boundary === 'top' ? list.firstElementChild : list.lastElementChild
  target?.scrollIntoView({ behavior: 'smooth', block: boundary === 'top' ? 'start' : 'end' })
}

async function exportRecord() {
  if (!selected.value) return
  try {
    const route = routeLabel(selected.value.external_id).replace(' · ', '-')
    if (await exportJsonToFolder(
      `聊天记录-${route}-${selected.value.title}`,
      exportEnvelope('catgirl_chat_record', { conversation: selected.value, messages: messages.value }),
    )) notice.value = '聊天记录已导出'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

onMounted(() => {
  restoreFloorLimit()
  void load()
})
</script>

<template>
  <div class="management-layout history-layout">
    <aside class="item-rail history-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">CHAT RECORDS</span><strong>{{ records.length }} 份记录</strong></div>
        <button class="icon-button primary-icon" type="button" title="新建聊天记录" :disabled="!selectedRoute" @click="createRecord"><Plus :size="17" /></button>
      </div>
      <div v-if="routes.length" class="history-route-select">
        <label class="field"><span>QQ 会话</span><select v-model="selectedRoute" @change="changeRoute"><option v-for="route in routes" :key="route" :value="route">{{ routeLabel(route) }}</option></select></label>
      </div>
      <div class="item-list history-record-list">
        <button
          v-for="record in routeRecords"
          :key="record.id"
          type="button"
          :class="['item-row history-record-row', { selected: record.id === selectedId }]"
          @click="selectRecord(record)"
        >
          <span :class="['item-symbol history-symbol', { active: record.is_active }]"><MessageSquareText :size="18" /></span>
          <span><strong>{{ record.title }}</strong><small>{{ record.message_count }} 条 · {{ formatTime(record.updated_at) }}</small></span>
          <i v-if="record.is_active" class="active-dot" title="当前使用" />
        </button>
      </div>
    </aside>

    <section class="history-main">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selected">
        <div class="editor-heading history-heading">
          <div>
            <div class="heading-with-status">
              <input v-model="form.title" class="title-input" aria-label="聊天记录名称" maxlength="240" />
              <span v-if="selected.is_active" class="active-badge">当前使用</span>
            </div>
            <p>{{ conversationLabel(selected.external_id, selected.character_name) }} · {{ selected.message_count }} 条消息 · 总 {{ formatTokenCount(selected.total_tokens) }} tokens</p>
          </div>
          <div class="action-row">
            <label class="history-floor-limit" title="0 表示显示全部楼层"><span>显示楼层</span><input :value="floorLimit" type="number" min="0" step="1" aria-label="显示楼层" @change="updateFloorLimit" /></label>
            <button class="icon-button" type="button" title="导出聊天记录" @click="exportRecord"><Upload :size="17" /></button>
            <button v-if="!selected.is_active" class="button primary" type="button" @click="activateRecord"><Check :size="16" />使用这份记录</button>
            <button class="button secondary" type="button" :disabled="saving" @click="saveTitle"><Save :size="16" />保存名称</button>
            <template v-if="multiSelect">
              <button class="button secondary" type="button" :disabled="!visibleMessages.length" @click="toggleSelectAll">{{ allMessagesSelected ? '取消全选' : '全选' }}</button>
              <button class="button history-delete-selected" type="button" :disabled="!selectedMessageIds.length || deletingMessages" @click="removeSelectedMessages"><Trash2 :size="16" />删除所选（{{ selectedMessageIds.length }}）</button>
              <button class="icon-button" type="button" title="退出多选" @click="toggleMultiSelect"><X :size="17" /></button>
            </template>
            <button v-else class="button secondary" type="button" :disabled="!visibleMessages.length" @click="toggleMultiSelect"><ListChecks :size="16" />多选</button>
            <button class="icon-button danger" type="button" title="删除聊天记录" @click="removeRecord"><Trash2 :size="17" /></button>
          </div>
        </div>

        <div class="history-message-list-shell">
          <button v-if="visibleMessages.length" class="history-scroll-button history-scroll-top" type="button" title="滚动到最上方" @click="scrollMessages('top')"><ChevronsUp :size="16" /></button>
          <div ref="messageList" class="history-message-list">
            <article
              v-for="message in visibleMessages"
              :key="message.id"
              :class="['history-message', `role-${message.role}`, { selecting: multiSelect, 'selected-for-delete': selectedMessageIds.includes(message.id), 'editing-message': editingMessageId === message.id }]"
              :style="editingMessageId === message.id && editingFloorHeight ? { minHeight: `${editingFloorHeight}px` } : undefined"
            >
              <input v-if="multiSelect" v-model="selectedMessageIds" class="history-message-checkbox" type="checkbox" :value="message.id" :aria-label="`选择第 ${message.position + 1} 条消息`" />
              <span class="history-role-icon"><UserRound v-if="message.role === 'user'" :size="17" /><Bot v-else :size="17" /></span>
              <div class="history-message-body">
                <header>
                  <span class="history-message-identity"><strong>{{ speakerName(message) }}</strong><small>第 {{ message.position + 1 }} 层</small></span>
                  <span class="history-message-meta">
                    <span>{{ formatTime(message.created_at) }}<template v-if="message.model"> · {{ message.model }}</template></span>
                    <button v-if="!multiSelect" class="icon-button history-message-edit-trigger" type="button" :title="`编辑第 ${message.position + 1} 层`" @click="startEditingMessage(message, $event)"><Pencil :size="14" /></button>
                  </span>
                </header>
                <div v-if="editingMessageId === message.id" class="history-message-editor">
                  <textarea v-model="editingContent" rows="5" maxlength="100000" :aria-label="`编辑第 ${message.position + 1} 层文本`" @keydown.ctrl.enter.prevent="saveMessage(message)" />
                  <div class="history-message-editor-actions">
                    <button class="icon-button" type="button" title="取消编辑" :disabled="savingMessage" @click="cancelEditingMessage"><X :size="15" /></button>
                    <button class="icon-button primary-icon" type="button" title="保存本层" :disabled="savingMessage || !editingContent.trim()" @click="saveMessage(message)"><Save :size="15" /></button>
                  </div>
                </div>
                <template v-else>
                  <details v-if="messageReasoning(message)" class="history-reasoning">
                    <summary><BrainCircuit :size="14" /><span>思考过程</span><ChevronDown :size="14" class="history-reasoning-chevron" /></summary>
                    <pre>{{ messageReasoning(message) }}</pre>
                  </details>
                  <p>{{ message.content }}</p>
                </template>
                <small v-if="message.total_tokens" class="history-token-usage">{{ message.total_tokens }} tokens · {{ message.message_metadata.token_usage_estimated ? '本地分词' : 'API usage' }}</small>
              </div>
            </article>
            <div v-if="!messages.length" class="empty-state history-empty"><MessageSquareText :size="28" /><strong>这份记录还没有消息</strong></div>
          </div>
          <button v-if="visibleMessages.length" class="history-scroll-button history-scroll-bottom" type="button" title="滚动到最下方" @click="scrollMessages('bottom')"><ChevronsDown :size="16" /></button>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state"><MessageSquareText :size="28" /><strong>暂无聊天记录</strong></div>
    </section>
  </div>
</template>
