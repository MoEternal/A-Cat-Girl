<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { VueDraggable } from 'vue-draggable-plus'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  ExternalLink,
  FileDown,
  FolderOpen,
  GripVertical,
  PackageOpen,
  Plug,
  Plus,
  PenLine,
  Power,
  RotateCw,
  RefreshCw,
  Save,
  ShieldAlert,
  Trash2,
  Upload,
} from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import type {
  Character,
  ConversationRecord,
  GroupChatManagementState,
  Plugin,
  PluginConversationStateView,
  PluginSettingDefinition,
  PluginStateResponse,
  RegexFilterState,
  RegexRule,
} from '../types'

const plugins = ref<Plugin[]>([])
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const installing = ref(false)
const error = ref('')
const notice = ref('')
const installInput = ref<HTMLInputElement | null>(null)
const memoryView = ref<PluginConversationStateView>({ items: [], selected_id: null, state: {} })
const memoryLoading = ref(false)
const memoryError = ref('')
const memoryNameDraft = ref('')
const selectedMemoryCharacterId = ref('')
const memoryDetailTab = ref<'profile' | 'social' | 'events' | 'promises' | 'items'>('profile')
const regexState = ref<RegexFilterState>({ global_rules: [], character_rules: {} })
const regexCharacters = ref<Character[]>([])
const regexScope = ref<'global' | 'character'>('global')
const selectedRegexCharacterId = ref('')
const regexLoading = ref(false)
const regexError = ref('')
const groupChatState = ref<GroupChatManagementState>({ version: 2, global_words: [], groups: {} })
const groupChatConversations = ref<ConversationRecord[]>([])
const groupChatScope = ref<'global' | 'group'>('global')
const selectedGroupChatGroupId = ref('')
const groupChatWordDraft = ref('')
const groupChatLoading = ref(false)
const groupChatError = ref('')
const searchModelOptions = ref<Array<{ id: string, name: string }>>([])
const searchModelsLoading = ref(false)
const manualSearchModel = ref(true)
type SettingValue = string | number | boolean
type JsonRecord = Record<string, unknown>

const form = reactive<Record<string, SettingValue>>({})

const selected = computed(() => plugins.value.find((item) => item.id === selectedId.value) ?? null)
const selectedAdminUrl = computed(() => selected.value?.admin_ui
  ? `/api/plugins/${encodeURIComponent(selected.value.id)}/assets/${selected.value.admin_ui.split('/').map(encodeURIComponent).join('/')}`
  : '')
const settingEntries = computed(() => Object.entries(selected.value?.settings_schema.properties ?? {}))
const regexRules = computed(() => {
  if (regexScope.value === 'global') return regexState.value.global_rules
  if (!selectedRegexCharacterId.value) return []
  return regexState.value.character_rules[selectedRegexCharacterId.value] ?? []
})
const groupChatOptions = computed(() => {
  const options = new Map<string, string>()
  for (const groupId of Object.keys(groupChatState.value.groups)) options.set(groupId, groupId)
  for (const record of groupChatConversations.value) {
    const groupId = groupIdFromConversation(record)
    if (!groupId) continue
    const title = record.title.trim()
    options.set(groupId, title && title !== groupId ? `${title} · ${groupId}` : groupId)
  }
  return [...options.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((left, right) => left.id.localeCompare(right.id, 'zh-CN', { numeric: true }))
})
const groupChatWords = computed(() => {
  if (groupChatScope.value === 'global') return groupChatState.value.global_words
  if (!selectedGroupChatGroupId.value) return []
  return groupChatState.value.groups[selectedGroupChatGroupId.value]?.blocked_words ?? []
})
const memoryCharacters = computed(() => recordList(memoryView.value.state.characters)
  .sort((left, right) => numberValue(right.last_turn) - numberValue(left.last_turn))
  .slice(0, 12))
const memoryFiles = computed(() => memoryView.value.memories ?? [])
const memoryRelationships = computed(() => recordList(memoryView.value.state.relationships))
const memoryScene = computed(() => {
  const value = memoryView.value.state.last_scene
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
})
const memoryTables = computed(() => {
  const tables = memoryView.value.state.tables
  if (!tables || typeof tables !== 'object' || Array.isArray(tables)) return []
  const output: Array<{ id: string, title: string, columns: string[] }> = []
  for (const [id, value] of Object.entries(tables)) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const table = value as JsonRecord
    const columns = Array.isArray(table.columns)
      ? table.columns.filter((column): column is string => typeof column === 'string')
      : []
    if (columns.length) output.push({ id, title: textValue(table.title, id), columns })
  }
  return output
})
const selectedMemoryCharacter = computed(() => memoryCharacters.value.find((item) => textValue(item.id) === selectedMemoryCharacterId.value)
  ?? memoryCharacters.value[0]
  ?? null)
const memoryGraphNodes = computed(() => memoryCharacters.value.map((character, index, values) => {
  const angle = (-Math.PI / 2) + (Math.PI * 2 * index / Math.max(values.length, 1))
  const radiusX = values.length < 5 ? 29 : 37
  const radiusY = values.length < 5 ? 28 : 35
  return {
    character,
    id: textValue(character.id, `character-${index}`),
    x: 50 + Math.cos(angle) * radiusX,
    y: 50 + Math.sin(angle) * radiusY,
    tone: index % 5,
  }
}))
const memoryGraphEdges = computed(() => {
  const edges: Array<{ key: string, path: string, label: string, kind: string }> = []
  for (const node of memoryGraphNodes.value) {
    const relationship = textValue(node.character.user_relationship, textValue(node.character.relationship_stage, '关系未明'))
    edges.push({
      key: `user-${node.id}`,
      path: relationshipPath(50, 50, node.x, node.y, edges.length),
      label: relationship,
      kind: 'to-user',
    })
  }
  for (const relation of memoryRelationships.value.slice(-16)) {
    const source = graphNodeForName(textValue(relation.source))
    const target = graphNodeForName(textValue(relation.target))
    if (!source || !target || source.id === target.id) continue
    edges.push({
      key: textValue(relation.id, `${source.id}-${target.id}`),
      path: relationshipPath(source.x, source.y, target.x, target.y, edges.length),
      label: textValue(relation.relation, '关联'),
      kind: 'between-characters',
    })
  }
  return edges
})
const memoryProfileRows = computed(() => {
  const character = selectedMemoryCharacter.value
  if (!character) return []
  return [
    { label: '身体特征', value: textValue(character.physical_traits) },
    { label: '性格', value: textValue(character.personality) },
    { label: '职业 / 身份', value: textValue(character.occupation, textValue(character.cast_role)) },
    { label: '兴趣', value: joinValues(character.hobbies) },
    { label: '喜欢的事物', value: joinValues(character.likes) },
    { label: '住处', value: textValue(character.residence) },
    { label: '当前衣着', value: textValue(character.current_outfit) },
    { label: '状态与情绪', value: [textValue(character.current_state), textValue(character.current_emotion)].filter(Boolean).join(' / ') },
    { label: '当前目标', value: textValue(character.current_goal) },
    { label: '伤势 / 异常', value: joinValues(character.injuries) },
    { label: '重要信息', value: joinValues(character.important_info) },
  ].filter((item) => item.value)
})
const selectedCharacterRelations = computed(() => {
  const character = selectedMemoryCharacter.value
  if (!character) return []
  const name = textValue(character.name)
  const relations = memoryRelationships.value.filter((item) => namesMatch(item.source, name) || namesMatch(item.target, name))
  if (textValue(character.user_relationship) && !relations.some((item) => isUserName(textValue(item.source)) || isUserName(textValue(item.target)))) {
    relations.unshift({
      source: name,
      target: '<user>',
      relation: textValue(character.user_relationship),
      attitude: textValue(character.user_attitude),
      closeness: numberValue(character.affection),
    })
  }
  return relations
})
const selectedCharacterEvents = computed(() => selectedCharacterRecords('events', 'participants'))
const selectedCharacterPromises = computed(() => selectedCharacterRecords('promises', 'parties'))
const selectedCharacterItems = computed(() => {
  const character = selectedMemoryCharacter.value
  if (!character) return []
  const name = textValue(character.name)
  return recordList(memoryView.value.state.items).filter((item) => namesMatch(item.owner, name))
})
const memoryDetailTabs = computed(() => [
  { id: 'profile' as const, label: '人物档案', count: memoryProfileRows.value.length },
  { id: 'social' as const, label: '社交关系', count: selectedCharacterRelations.value.length },
  { id: 'events' as const, label: '重要事件', count: selectedCharacterEvents.value.length },
  { id: 'promises' as const, label: '任务约定', count: selectedCharacterPromises.value.length },
  { id: 'items' as const, label: '关联物品', count: selectedCharacterItems.value.length },
])

let memoryRequest = 0

function recordList(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : []
}

function listLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0
}

function numberValue(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function textValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value ? value : fallback
}

function joinValues(value: unknown): string {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && Boolean(item)).join(' / ') : ''
}

function normalizedName(value: unknown): string {
  return textValue(value).toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]+/g, '')
}

function isUserName(value: string): boolean {
  return ['user', '你', '用户'].includes(normalizedName(value))
}

function namesMatch(left: unknown, right: unknown): boolean {
  const leftKey = normalizedName(left)
  const rightKey = normalizedName(right)
  return Boolean(leftKey && rightKey && leftKey === rightKey)
}

function graphNodeForName(name: string) {
  return memoryGraphNodes.value.find((node) => namesMatch(node.character.name, name))
}

function relationshipPath(x1: number, y1: number, x2: number, y2: number, index: number): string {
  const startX = x1 * 10
  const startY = y1 * 6
  const endX = x2 * 10
  const endY = y2 * 6
  const bend = index % 2 === 0 ? 34 : -34
  return `M ${startX} ${startY} Q ${(startX + endX) / 2 + bend} ${(startY + endY) / 2 - bend} ${endX} ${endY}`
}

function selectMemoryCharacter(character: JsonRecord) {
  selectedMemoryCharacterId.value = textValue(character.id)
  memoryDetailTab.value = 'profile'
}

function selectedCharacterRecords(collection: string, participantKey: string): JsonRecord[] {
  const character = selectedMemoryCharacter.value
  if (!character) return []
  const name = textValue(character.name)
  return recordList(memoryView.value.state[collection])
    .filter((item) => Array.isArray(item[participantKey]) && item[participantKey].some((person) => namesMatch(person, name)))
    .slice(-20)
    .reverse()
}

function memoryRecordLabel(item: PluginConversationStateView['items'][number]): string {
  const title = item.title || item.external_id || item.conversation_id
  const suffix = item.message_count === 0 ? ' · 空聊天' : ''
  return item.is_active ? `${title} · 当前${suffix}` : `${title}${suffix}`
}

function memoryFileLabel(item: NonNullable<PluginConversationStateView['memories']>[number]): string {
  return item.bound_count > 1 ? `${item.name} · ${item.bound_count} 个聊天` : item.name
}

function selectPlugin(plugin: Plugin) {
  selectedId.value = plugin.id
  for (const key of Object.keys(form)) delete form[key]
  for (const [key, value] of Object.entries(plugin.settings)) {
    if (typeof value === 'string') {
      const maxLength = plugin.settings_schema.properties[key]?.maxLength
      form[key] = typeof maxLength === 'number' && Number.isInteger(maxLength)
        ? [...value].slice(0, maxLength).join('')
        : value
    } else if (typeof value === 'number' || typeof value === 'boolean') form[key] = value
  }
  error.value = ''
  notice.value = ''
  searchModelOptions.value = []
  manualSearchModel.value = true
  if (plugin.id === 'memory_system') void loadMemoryVisualization()
  else {
    memoryView.value = { items: [], selected_id: null, state: {} }
    memoryError.value = ''
  }
  if (plugin.id === 'regex_filter') void loadRegexEditor()
  else regexError.value = ''
  if (plugin.id === 'group_chat_management') void loadGroupChatEditor()
  else groupChatError.value = ''
}

watch(
  () => [form.search_model_base_url, form.search_model_api_key],
  () => {
    if (!searchModelOptions.value.length) return
    searchModelOptions.value = []
    manualSearchModel.value = true
  },
)

async function loadSearchModels() {
  if (selected.value?.id !== 'web_search') return
  searchModelsLoading.value = true
  error.value = ''
  try {
    const models = await api<Array<{ id: string, name: string }>>(
      '/api/plugins/web_search/models',
      json('POST', {
        payload: {
          base_url: String(form.search_model_base_url ?? ''),
          api_key: String(form.search_model_api_key ?? ''),
        },
      }),
    )
    if (!models.length) {
      searchModelOptions.value = []
      manualSearchModel.value = true
      error.value = '接口没有返回可用模型，请手动填写模型名称'
      return
    }
    searchModelOptions.value = models
    manualSearchModel.value = false
    notice.value = `已拉取 ${models.length} 个模型`
  } catch (reason) {
    searchModelOptions.value = []
    manualSearchModel.value = true
    error.value = reason instanceof Error ? reason.message : '模型列表拉取失败'
  } finally {
    searchModelsLoading.value = false
  }
}

function openAdminPage() {
  if (!selectedAdminUrl.value) return
  window.open(selectedAdminUrl.value, '_blank', 'noopener,noreferrer')
}

async function openStickerFolder() {
  if (selected.value?.id !== 'sticker_reply') return
  saving.value = true
  error.value = ''
  try {
    await api('/api/plugins/sticker_reply/admin-actions/open-assets-folder', json('POST', { payload: {} }))
    notice.value = '已打开表情文件夹'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '打开表情文件夹失败'
  } finally {
    saving.value = false
  }
}

function normalizeRegexState(value: unknown): RegexFilterState {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
  const normalizeRules = (items: unknown): RegexRule[] => Array.isArray(items)
    ? items.filter((item): item is JsonRecord => Boolean(item) && typeof item === 'object' && !Array.isArray(item)).map((item, index) => ({
      id: textValue(item.id, `rule-${index + 1}`),
      name: textValue(item.name, `正则 ${index + 1}`),
      enabled: item.enabled === true,
      pattern: textValue(item.pattern),
      replacement: typeof item.replacement === 'string' ? item.replacement : '',
      flags: textValue(item.flags),
    }))
    : []
  const characterRules: Record<string, RegexRule[]> = {}
  if (source.character_rules && typeof source.character_rules === 'object' && !Array.isArray(source.character_rules)) {
    for (const [characterId, rules] of Object.entries(source.character_rules)) {
      characterRules[characterId] = normalizeRules(rules)
    }
  }
  return {
    global_rules: normalizeRules(source.global_rules),
    character_rules: characterRules,
  }
}

async function loadRegexEditor() {
  if (selectedId.value !== 'regex_filter') return
  regexLoading.value = true
  regexError.value = ''
  try {
    const [stateResponse, characters] = await Promise.all([
      api<PluginStateResponse<RegexFilterState>>('/api/plugins/regex_filter/state'),
      api<Character[]>('/api/characters'),
    ])
    if (selectedId.value !== 'regex_filter') return
    regexState.value = normalizeRegexState(stateResponse.state)
    regexCharacters.value = characters
    if (!characters.some((item) => item.id === selectedRegexCharacterId.value)) {
      selectedRegexCharacterId.value = characters.find((item) => item.is_active)?.id ?? characters[0]?.id ?? ''
    }
  } catch (reason) {
    regexError.value = reason instanceof Error ? reason.message : '正则配置加载失败'
  } finally {
    regexLoading.value = false
  }
}

function addRegexRule() {
  if (regexScope.value === 'character' && !selectedRegexCharacterId.value) return
  const rule: RegexRule = {
    id: typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `regex-${Date.now()}`,
    name: `正则 ${regexRules.value.length + 1}`,
    enabled: false,
    pattern: '',
    replacement: '',
    flags: '',
  }
  if (regexScope.value === 'global') regexState.value.global_rules.push(rule)
  else {
    const characterId = selectedRegexCharacterId.value
    const rules = regexState.value.character_rules[characterId] ?? []
    regexState.value.character_rules[characterId] = [...rules, rule]
  }
}

function moveRegexRule(index: number, offset: -1 | 1) {
  const target = index + offset
  const rules = regexRules.value
  if (target < 0 || target >= rules.length) return
  const [rule] = rules.splice(index, 1)
  rules.splice(target, 0, rule)
}

function removeRegexRule(index: number) {
  regexRules.value.splice(index, 1)
}

function toggleRegexFlag(rule: RegexRule, flag: 'i' | 'm' | 's') {
  const flags = new Set(rule.flags.split(''))
  if (flags.has(flag)) flags.delete(flag)
  else flags.add(flag)
  rule.flags = ['i', 'm', 's'].filter((item) => flags.has(item)).join('')
}

async function saveRegexState() {
  saving.value = true
  regexError.value = ''
  error.value = ''
  try {
    const response = await api<PluginStateResponse<RegexFilterState>>(
      '/api/plugins/regex_filter/state',
      json('PUT', { state: regexState.value }),
    )
    regexState.value = normalizeRegexState(response.state)
    notice.value = '正则脚本已保存'
  } catch (reason) {
    regexError.value = reason instanceof Error ? reason.message : '正则保存失败'
  } finally {
    saving.value = false
  }
}

function normalizedBlockedWords(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const words: string[] = []
  const seen = new Set<string>()
  for (const item of value) {
    if (typeof item !== 'string') continue
    const word = item.trim()
    const key = word.toLocaleLowerCase()
    if (!word || word.length > 120 || seen.has(key)) continue
    words.push(word)
    seen.add(key)
    if (words.length >= 500) break
  }
  return words
}

function normalizeGroupChatState(value: unknown): GroupChatManagementState {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
  const groups: GroupChatManagementState['groups'] = {}
  if (source.groups && typeof source.groups === 'object' && !Array.isArray(source.groups)) {
    for (const [rawGroupId, entry] of Object.entries(source.groups)) {
      const groupId = rawGroupId.trim()
      if (!groupId || !entry || typeof entry !== 'object' || Array.isArray(entry)) continue
      const words = normalizedBlockedWords((entry as JsonRecord).blocked_words)
      if (words.length) groups[groupId] = { blocked_words: words }
    }
  }
  return {
    version: 2,
    global_words: normalizedBlockedWords(source.global_words),
    groups,
  }
}

function groupIdFromConversation(record: ConversationRecord): string {
  for (const value of [record.id, record.external_id]) {
    const match = value.match(/(?:^|:)group:([^:]+)$/)
    if (match?.[1]) return match[1].trim()
  }
  return ''
}

function selectAvailableGroupChatGroup() {
  if (groupChatOptions.value.some((item) => item.id === selectedGroupChatGroupId.value)) return
  selectedGroupChatGroupId.value = groupChatOptions.value[0]?.id ?? ''
}

async function loadGroupChatEditor() {
  if (selectedId.value !== 'group_chat_management') return
  groupChatLoading.value = true
  groupChatError.value = ''
  try {
    const [stateResponse, conversations] = await Promise.all([
      api<PluginStateResponse<GroupChatManagementState>>('/api/plugins/group_chat_management/state'),
      api<ConversationRecord[]>('/api/runtime/conversations'),
    ])
    if (selectedId.value !== 'group_chat_management') return
    groupChatState.value = normalizeGroupChatState(stateResponse.state)
    groupChatConversations.value = conversations
    selectAvailableGroupChatGroup()
  } catch (reason) {
    groupChatError.value = reason instanceof Error ? reason.message : '屏蔽词加载失败'
  } finally {
    groupChatLoading.value = false
  }
}

function addGroupChatWord() {
  const word = groupChatWordDraft.value.trim()
  if (!word) return
  if (word.length > 120) {
    groupChatError.value = '单个屏蔽词不能超过 120 个字符'
    return
  }
  if (groupChatScope.value === 'group' && !selectedGroupChatGroupId.value) {
    groupChatError.value = '请选择群聊'
    return
  }
  let words = groupChatState.value.global_words
  if (groupChatScope.value === 'group') {
    const groupId = selectedGroupChatGroupId.value
    groupChatState.value.groups[groupId] ??= { blocked_words: [] }
    words = groupChatState.value.groups[groupId].blocked_words
  }
  if (words.some((item) => item.toLocaleLowerCase() === word.toLocaleLowerCase())) {
    groupChatError.value = '屏蔽词已存在'
    return
  }
  if (words.length >= 500) {
    groupChatError.value = '屏蔽词数量已达上限'
    return
  }
  words.push(word)
  groupChatWordDraft.value = ''
  groupChatError.value = ''
}

function removeGroupChatWord(index: number) {
  groupChatWords.value.splice(index, 1)
  groupChatError.value = ''
}

async function saveGroupChatState() {
  saving.value = true
  groupChatError.value = ''
  error.value = ''
  try {
    const response = await api<PluginStateResponse<GroupChatManagementState>>(
      '/api/plugins/group_chat_management/state',
      json('PUT', { state: groupChatState.value }),
    )
    groupChatState.value = normalizeGroupChatState(response.state)
    selectAvailableGroupChatGroup()
    notice.value = '屏蔽词已保存'
  } catch (reason) {
    groupChatError.value = reason instanceof Error ? reason.message : '屏蔽词保存失败'
  } finally {
    saving.value = false
  }
}

async function loadMemoryVisualization(conversationId?: string) {
  if (selectedId.value !== 'memory_system') return
  const requestId = ++memoryRequest
  memoryLoading.value = true
  memoryError.value = ''
  try {
    const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
    const view = await api<PluginConversationStateView>(`/api/plugins/memory_system/conversation-states${query}`)
    if (requestId === memoryRequest && selectedId.value === 'memory_system') {
      memoryView.value = view
      const selectedMemory = (view.memories ?? []).find((item) => item.id === view.selected_memory_id)
      memoryNameDraft.value = selectedMemory?.name ?? ''
      const characters = recordList(view.state.characters)
      if (!characters.some((item) => textValue(item.id) === selectedMemoryCharacterId.value)) {
        selectedMemoryCharacterId.value = textValue(characters[0]?.id)
        memoryDetailTab.value = 'profile'
      }
    }
  } catch (reason) {
    if (requestId === memoryRequest) {
      memoryError.value = reason instanceof Error ? reason.message : '记忆图表加载失败'
    }
  } finally {
    if (requestId === memoryRequest) memoryLoading.value = false
  }
}

function changeMemoryRecord(event: Event) {
  void loadMemoryVisualization((event.target as HTMLSelectElement).value)
}

async function runMemoryAction(action: string, payload: Record<string, unknown>) {
  if (!memoryView.value.selected_id) return
  memoryLoading.value = true
  memoryError.value = ''
  try {
    const response = await api<{ result: PluginConversationStateView }>(
      `/api/plugins/memory_system/admin-actions/${encodeURIComponent(action)}`,
      json('POST', { payload: { conversation_id: memoryView.value.selected_id, ...payload } }),
    )
    memoryView.value = response.result
    const selectedMemory = (response.result.memories ?? []).find(
      (item) => item.id === response.result.selected_memory_id,
    )
    memoryNameDraft.value = selectedMemory?.name ?? ''
  } catch (reason) {
    memoryError.value = reason instanceof Error ? reason.message : '记忆绑定操作失败'
  } finally {
    memoryLoading.value = false
  }
}

function changeMemoryBinding(event: Event) {
  void runMemoryAction('bind-memory', { memory_id: (event.target as HTMLSelectElement).value })
}

function createMemory() {
  void runMemoryAction('create-memory', { name: memoryNameDraft.value.trim() })
}

function renameMemory() {
  if (!memoryView.value.selected_memory_id || !memoryNameDraft.value.trim()) return
  void runMemoryAction('rename-memory', {
    memory_id: memoryView.value.selected_memory_id,
    name: memoryNameDraft.value.trim(),
  })
}

function deleteMemory() {
  const memoryId = memoryView.value.selected_memory_id
  if (!memoryId) return
  const memory = memoryFiles.value.find((item) => item.id === memoryId)
  const name = memory?.name ?? '当前记忆'
  if (!window.confirm(`删除记忆“${name}”？旧文件会移入回收目录，当前聊天会自动绑定一份新的空记忆。`)) return
  void runMemoryAction('delete-memory', { memory_id: memoryId })
}

async function exportMemory() {
  const conversation = memoryView.value.items.find(
    (item) => item.conversation_id === memoryView.value.selected_id,
  ) ?? null
  if (!conversation) return
  memoryError.value = ''
  try {
    if (await exportJsonToFolder(
      `记忆-${conversation.title || conversation.conversation_id}`,
      exportEnvelope('catgirl_memory_system', { conversation, state: memoryView.value.state }),
    )) notice.value = '当前聊天记忆已导出'
  } catch (reason) {
    memoryError.value = reason instanceof Error ? reason.message : '记忆导出失败'
  }
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    plugins.value = await api<Plugin[]>('/api/plugins')
    const target = plugins.value.find((item) => item.id === preferredId)
      ?? plugins.value.find((item) => item.id === selectedId.value)
      ?? plugins.value[0]
    if (target) selectPlugin(target)
    else selectedId.value = ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '插件列表加载失败'
  } finally {
    loading.value = false
  }
}

async function persistOrder() {
  error.value = ''
  try {
    plugins.value = await api<Plugin[]>('/api/plugins/order', json('PUT', {
      plugin_ids: plugins.value.map((item) => item.id),
    }))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '插件排序保存失败'
    await load(selectedId.value)
  }
}

async function saveSettings() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<Plugin>(`/api/plugins/${selected.value.id}`, json('PUT', { settings: { ...form } }))
    await load(updated.id)
    notice.value = '插件设置已保存并重新载入'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function toggleEnabled() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<Plugin>(
      `/api/plugins/${selected.value.id}`,
      json('PUT', { enabled: !selected.value.enabled }),
    )
    await load(updated.id)
    notice.value = updated.enabled ? '插件已启用' : '插件已停用'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '操作失败'
  } finally {
    saving.value = false
  }
}

async function reloadPlugin() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<Plugin>(`/api/plugins/${selected.value.id}/reload`, json('POST'))
    await load(updated.id)
    notice.value = updated.loaded ? '插件已重新载入' : '插件当前处于停用状态'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '重载失败'
  } finally {
    saving.value = false
  }
}

async function installPlugin(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  installing.value = true
  error.value = ''
  try {
    const installed = await api<Plugin>('/api/plugins/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/zip' },
      body: file,
    })
    await load(installed.id)
    notice.value = `插件“${installed.name}”已安装`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '安装失败'
  } finally {
    installing.value = false
  }
}

async function uninstallPlugin() {
  if (!selected.value || selected.value.built_in) return
  if (!window.confirm(`卸载插件“${selected.value.name}”？插件文件、设置和状态都会删除。`)) return
  try {
    await api(`/api/plugins/${selected.value.id}`, json('DELETE'))
    selectedId.value = ''
    await load()
    notice.value = '插件已卸载'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '卸载失败'
  }
}

function numberStep(definition: PluginSettingDefinition): string {
  return definition.type === 'integer' ? '1' : 'any'
}

function settingRange(definition: PluginSettingDefinition): string {
  if (definition.minimum === undefined && definition.maximum === undefined) return ''
  if (definition.minimum !== undefined && definition.maximum !== undefined) {
    return `允许范围：${definition.minimum} - ${definition.maximum}`
  }
  if (definition.minimum !== undefined) return `允许范围：不小于 ${definition.minimum}`
  return `允许范围：不大于 ${definition.maximum}`
}

function settingHelp(key: string, definition: PluginSettingDefinition): string {
  const description = definition.description || `设置“${definition.title || key}”的值。`
  const range = settingRange(definition)
  return range ? `${description}\n${range}` : description
}

function updateTextSetting(key: string, event: Event) {
  form[key] = (event.target as HTMLInputElement | HTMLTextAreaElement).value
}

onMounted(load)
</script>

<template>
  <div class="management-layout plugin-layout">
    <aside class="item-rail plugin-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">PLUGINS</span><strong>{{ plugins.length }} 个已安装插件</strong></div>
        <input ref="installInput" class="visually-hidden" type="file" accept=".zip,application/zip" @change="installPlugin" />
        <button class="icon-button primary-icon" type="button" title="安装 ZIP 插件" :disabled="installing" @click="installInput?.click()">
          <FileDown :size="17" />
        </button>
      </div>
      <VueDraggable v-model="plugins" class="item-list plugin-list" handle=".plugin-drag-handle" :animation="150" @end="persistOrder">
        <button
          v-for="item in plugins"
          :key="item.id"
          type="button"
          :class="['item-row plugin-row', { selected: item.id === selectedId }]"
          @click="selectPlugin(item)"
        >
          <span class="drag-handle plugin-drag-handle" title="拖动排序" @click.stop><GripVertical :size="16" /></span>
          <span :class="['item-symbol plugin-symbol', { running: item.loaded }]"><Plug :size="18" /></span>
          <span><strong>{{ item.name }}</strong><small>{{ item.hide_metadata ? item.status : `v${item.version} · ${item.status}` }}</small></span>
          <i :class="['plugin-state-dot', { running: item.loaded, failed: item.enabled && !item.loaded }]" />
        </button>
      </VueDraggable>
    </aside>

    <section class="editor-pane plugin-editor">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selected">
        <div class="editor-heading plugin-heading">
          <div>
            <div class="heading-with-status">
              <h2>{{ selected.name }}</h2>
              <span v-if="selected.built_in" class="active-badge neutral-badge">内置</span>
              <span :class="['active-badge', { disabled: !selected.loaded }]">{{ selected.status }}</span>
            </div>
            <p>{{ selected.description || '此插件没有提供说明。' }}</p>
          </div>
          <div class="action-row">
            <button v-if="selectedAdminUrl" class="button secondary" type="button" :disabled="!selected.enabled" @click="openAdminPage"><ExternalLink :size="16" />打开管理页</button>
            <button v-if="selected.id === 'sticker_reply'" class="button secondary" type="button" :disabled="!selected.enabled || saving" @click="openStickerFolder"><FolderOpen :size="16" />打开表情文件夹</button>
            <button class="button secondary" type="button" :disabled="saving" @click="reloadPlugin"><RotateCw :size="16" />重载</button>
            <button v-if="selected.id !== 'regex_filter'" :class="['button', selected.enabled ? 'secondary' : 'primary']" type="button" :disabled="saving" @click="toggleEnabled">
              <Power :size="16" />{{ selected.enabled ? '停用' : '启用' }}
            </button>
            <span v-else class="active-badge neutral-badge">脚本独立开关</span>
            <button v-if="!selected.built_in" class="icon-button danger" type="button" title="卸载插件" @click="uninstallPlugin"><Trash2 :size="17" /></button>
          </div>
        </div>

        <div v-if="!selected.hide_metadata" class="plugin-meta-strip">
          <span><strong>版本</strong>{{ selected.version }}</span>
          <span><strong>作者</strong>{{ selected.author || '未署名' }}</span>
          <span><strong>最低程序版本</strong>{{ selected.min_app_version }}</span>
          <span><strong>钩子</strong>{{ selected.hooks.length }}</span>
        </div>

        <div v-if="selected.last_error" class="plugin-runtime-error">
          <AlertTriangle :size="18" />
          <div><strong>最近一次加载或运行错误</strong><code>{{ selected.last_error }}</code></div>
        </div>

        <div class="plugin-security-note">
          <ShieldAlert :size="19" />
          <div><strong>Python 插件拥有服务器进程权限</strong><span>仅安装来源可信的插件。插件不是沙箱程序，可以访问服务账号有权访问的文件与网络。</span></div>
        </div>

        <section v-if="selected.id === 'regex_filter'" class="plugin-section regex-editor-section">
          <div class="plugin-section-heading regex-editor-heading">
            <div><span class="eyebrow">REGEX SCRIPTS</span><h3>正则脚本</h3></div>
            <div class="regex-editor-actions">
              <button class="button secondary" type="button" :disabled="regexLoading" @click="loadRegexEditor"><RotateCw :size="15" />刷新</button>
              <button class="button primary" type="button" :disabled="saving || regexLoading" @click="saveRegexState"><Save :size="15" />保存</button>
            </div>
          </div>

          <div v-if="regexError" class="notice error-notice"><CircleAlert :size="18" />{{ regexError }}</div>

          <div class="regex-toolbar">
            <div class="regex-scope-tabs" role="tablist" aria-label="正则作用范围">
              <button type="button" role="tab" :aria-selected="regexScope === 'global'" :class="{ active: regexScope === 'global' }" @click="regexScope = 'global'">全局正则</button>
              <button type="button" role="tab" :aria-selected="regexScope === 'character'" :class="{ active: regexScope === 'character' }" @click="regexScope = 'character'">角色正则</button>
            </div>
            <label v-if="regexScope === 'character'" class="regex-character-select">
              <span>角色卡</span>
              <select v-model="selectedRegexCharacterId">
                <option v-for="character in regexCharacters" :key="character.id" :value="character.id">{{ character.name }}{{ character.is_active ? ' · 当前' : '' }}</option>
              </select>
            </label>
            <button class="button secondary regex-add-button" type="button" :disabled="regexLoading || (regexScope === 'character' && !selectedRegexCharacterId)" @click="addRegexRule"><Plus :size="15" />添加正则</button>
          </div>

          <div v-if="regexLoading" class="regex-editor-loading">正在加载…</div>
          <div v-else-if="regexRules.length" class="regex-rule-list">
            <article v-for="(rule, index) in regexRules" :key="rule.id" class="regex-rule-item">
              <div class="regex-rule-heading">
                <label class="setting-toggle regex-rule-toggle">
                  <input v-model="rule.enabled" type="checkbox" />
                  <span class="check-control"><Check :size="13" /></span>
                  <span><strong>{{ rule.enabled ? '已启用' : '已关闭' }}</strong></span>
                </label>
                <input v-model="rule.name" class="regex-rule-name" type="text" maxlength="120" aria-label="正则名称" />
                <div class="regex-rule-tools">
                  <button class="icon-button" type="button" title="上移" :disabled="index === 0" @click="moveRegexRule(index, -1)"><ChevronUp :size="16" /></button>
                  <button class="icon-button" type="button" title="下移" :disabled="index === regexRules.length - 1" @click="moveRegexRule(index, 1)"><ChevronDown :size="16" /></button>
                  <button class="icon-button danger" type="button" title="删除正则" @click="removeRegexRule(index)"><Trash2 :size="16" /></button>
                </div>
              </div>

              <div class="regex-rule-fields">
                <label class="field regex-pattern-field">
                  <span>查找表达式</span>
                  <textarea v-model="rule.pattern" rows="4" spellcheck="false" />
                </label>
                <label class="field regex-replacement-field">
                  <span>替换文本</span>
                  <textarea v-model="rule.replacement" rows="4" spellcheck="false" />
                </label>
              </div>

              <div class="regex-flags" aria-label="正则标志">
                <label><input type="checkbox" :checked="rule.flags.includes('i')" @change="toggleRegexFlag(rule, 'i')" /><code>i</code><span>忽略大小写</span></label>
                <label><input type="checkbox" :checked="rule.flags.includes('m')" @change="toggleRegexFlag(rule, 'm')" /><code>m</code><span>多行</span></label>
                <label><input type="checkbox" :checked="rule.flags.includes('s')" @change="toggleRegexFlag(rule, 's')" /><code>s</code><span>点号匹配换行</span></label>
              </div>
            </article>
          </div>
          <div v-else class="plugin-empty-settings"><PackageOpen :size="22" /><span>{{ regexScope === 'global' ? '尚未添加全局正则' : '该角色卡尚未添加正则' }}</span></div>
        </section>

        <section v-if="selected.id === 'group_chat_management'" class="plugin-section group-chat-editor-section">
          <div class="plugin-section-heading group-chat-editor-heading">
            <div><span class="eyebrow">WORD LISTS</span><h3>屏蔽词</h3></div>
            <div class="group-chat-editor-actions">
              <button class="button secondary" type="button" :disabled="groupChatLoading" @click="loadGroupChatEditor"><RotateCw :size="15" />刷新</button>
              <button class="button primary" type="button" :disabled="saving || groupChatLoading" @click="saveGroupChatState"><Save :size="15" />保存</button>
            </div>
          </div>

          <div v-if="groupChatError" class="notice error-notice"><CircleAlert :size="18" />{{ groupChatError }}</div>

          <div class="group-chat-toolbar">
            <div class="regex-scope-tabs" role="tablist" aria-label="屏蔽词范围">
              <button type="button" role="tab" :aria-selected="groupChatScope === 'global'" :class="{ active: groupChatScope === 'global' }" @click="groupChatScope = 'global'">全局屏蔽词</button>
              <button type="button" role="tab" :aria-selected="groupChatScope === 'group'" :class="{ active: groupChatScope === 'group' }" @click="groupChatScope = 'group'">分群屏蔽词</button>
            </div>
            <label v-if="groupChatScope === 'group'" class="group-chat-group-select">
              <span>群聊</span>
              <select v-model="selectedGroupChatGroupId" :disabled="!groupChatOptions.length">
                <option v-if="!groupChatOptions.length" value="">暂无群聊</option>
                <option v-for="group in groupChatOptions" :key="group.id" :value="group.id">{{ group.label }}</option>
              </select>
            </label>
          </div>

          <div class="group-chat-add-row">
            <label class="field">
              <span>屏蔽词</span>
              <input v-model="groupChatWordDraft" type="text" maxlength="120" aria-label="屏蔽词" @keydown.enter.prevent="addGroupChatWord" />
            </label>
            <button class="button secondary" type="button" :disabled="groupChatLoading || !groupChatWordDraft.trim() || (groupChatScope === 'group' && !selectedGroupChatGroupId)" @click="addGroupChatWord"><Plus :size="15" />添加</button>
          </div>

          <div v-if="groupChatLoading" class="group-chat-editor-loading">正在加载…</div>
          <div v-else-if="groupChatWords.length" class="group-chat-word-list">
            <div v-for="(word, index) in groupChatWords" :key="`${word}-${index}`" class="group-chat-word-item">
              <span>{{ word }}</span>
              <button class="icon-button danger" type="button" title="移除屏蔽词" @click="removeGroupChatWord(index)"><Trash2 :size="16" /></button>
            </div>
          </div>
          <div v-else class="plugin-empty-settings"><PackageOpen :size="22" /><span>{{ groupChatScope === 'global' ? '暂无全局屏蔽词' : '本群暂无屏蔽词' }}</span></div>
        </section>

        <section v-if="selected.id === 'memory_system'" class="plugin-section memory-dashboard-section">
          <div class="plugin-section-heading memory-dashboard-heading">
            <div><span class="eyebrow">MEMORY MAP</span><h3>记忆可视化</h3></div>
            <div class="memory-dashboard-actions">
              <label v-if="memoryView.items.length" class="memory-record-select">
                <span>聊天记录</span>
                <select :value="memoryView.selected_id ?? ''" @change="changeMemoryRecord">
                  <option v-for="item in memoryView.items" :key="item.conversation_id" :value="item.conversation_id">
                    {{ memoryRecordLabel(item) }}
                  </option>
                </select>
              </label>
              <label v-if="memoryFiles.length" class="memory-record-select">
                <span>绑定记忆</span>
                <select :value="memoryView.selected_memory_id ?? ''" @change="changeMemoryBinding">
                  <option v-for="item in memoryFiles" :key="item.id" :value="item.id">
                    {{ memoryFileLabel(item) }}
                  </option>
                </select>
              </label>
              <label v-if="memoryView.selected_id" class="memory-name-field">
                <span>记忆名称</span>
                <input v-model="memoryNameDraft" type="text" maxlength="160" />
              </label>
              <button class="icon-button" type="button" title="新建并绑定独立记忆" :disabled="memoryLoading || !memoryView.selected_id" @click="createMemory"><Plus :size="16" /></button>
              <button class="icon-button" type="button" title="保存记忆名称" :disabled="memoryLoading || !memoryView.selected_memory_id || !memoryNameDraft.trim()" @click="renameMemory"><Save :size="16" /></button>
              <button class="icon-button danger" type="button" title="删除当前记忆" :disabled="memoryLoading || !memoryView.selected_memory_id" @click="deleteMemory"><Trash2 :size="16" /></button>
              <button class="button secondary" type="button" :disabled="memoryLoading" @click="loadMemoryVisualization(memoryView.selected_id ?? undefined)">
                <RotateCw :size="15" />刷新
              </button>
              <button class="icon-button" type="button" title="导出当前聊天记忆" :disabled="!memoryView.selected_id" @click="exportMemory"><Upload :size="16" /></button>
            </div>
          </div>

          <div v-if="memoryError" class="notice error-notice"><CircleAlert :size="18" />{{ memoryError }}</div>
          <div v-if="memoryLoading" class="memory-dashboard-loading">正在整理图表…</div>
          <template v-else>
            <div v-if="memoryCharacters.length" class="memory-map-meta">
              <span>已整理 {{ numberValue(memoryView.state.turn) }} 轮</span>
              <span>{{ memoryCharacters.length }} 名可视角色</span>
              <span v-if="textValue(memoryScene.location)">{{ textValue(memoryScene.location) }}</span>
            </div>

            <div v-if="memoryCharacters.length" class="memory-network" aria-label="人物关系网络">
              <svg class="memory-network-links" viewBox="0 0 1000 600" preserveAspectRatio="none" aria-hidden="true">
                <path v-for="edge in memoryGraphEdges" :key="edge.key" :class="edge.kind" :d="edge.path" />
              </svg>
              <div class="memory-user-cell"><strong>你</strong><span>当前视角</span></div>
              <button
                v-for="node in memoryGraphNodes"
                :key="node.id"
                type="button"
                :class="['memory-person-cell', `tone-${node.tone}`, { selected: selectedMemoryCharacterId === node.id }]"
                :style="{ '--cell-x': `${node.x}%`, '--cell-y': `${node.y}%` }"
                :title="`查看 ${textValue(node.character.name, '该角色')} 的记忆档案`"
                @click="selectMemoryCharacter(node.character)"
              >
                <i>{{ textValue(node.character.name, '?').slice(0, 1) }}</i>
                <strong>{{ textValue(node.character.name, '未命名角色') }}</strong>
                <span>{{ textValue(node.character.user_relationship, textValue(node.character.relationship_stage, '关系未明')) }}</span>
              </button>
            </div>

            <article v-if="selectedMemoryCharacter" class="memory-detail-panel">
              <div class="memory-detail-heading">
                <div>
                  <span class="eyebrow">CHARACTER DOSSIER</span>
                  <h4>{{ textValue(selectedMemoryCharacter.name, '未命名角色') }}</h4>
                  <p>{{ textValue(selectedMemoryCharacter.user_relationship, '与当前视角的关系尚未记录') }}<template v-if="textValue(selectedMemoryCharacter.user_attitude)"> · {{ textValue(selectedMemoryCharacter.user_attitude) }}</template></p>
                </div>
                <div class="memory-relation-values">
                  <span>好感 <strong>{{ numberValue(selectedMemoryCharacter.affection) }}</strong></span>
                  <span>信赖 <strong>{{ numberValue(selectedMemoryCharacter.trust) }}</strong></span>
                  <span>嫉妒 <strong>{{ numberValue(selectedMemoryCharacter.jealousy) }}</strong></span>
                </div>
              </div>

              <div class="memory-detail-tabs" role="tablist" aria-label="人物记忆详情">
                <button
                  v-for="tab in memoryDetailTabs"
                  :key="tab.id"
                  :class="{ active: memoryDetailTab === tab.id }"
                  type="button"
                  role="tab"
                  :aria-selected="memoryDetailTab === tab.id"
                  @click="memoryDetailTab = tab.id"
                >{{ tab.label }}<small>{{ tab.count }}</small></button>
              </div>

              <div v-if="memoryDetailTab === 'profile'" class="memory-profile-grid">
                <div v-for="row in memoryProfileRows" :key="row.label"><span>{{ row.label }}</span><strong>{{ row.value }}</strong></div>
                <p v-if="!memoryProfileRows.length" class="memory-detail-empty">还没有足够的明确剧情信息来建立档案。</p>
              </div>

              <div v-else-if="memoryDetailTab === 'social'" class="memory-detail-list">
                <article v-for="relation in selectedCharacterRelations" :key="textValue(relation.id, `${relation.source}-${relation.target}`)">
                  <strong>{{ textValue(relation.source) }} <i>→</i> {{ textValue(relation.target) }}</strong>
                  <span>{{ textValue(relation.relation, '关系未命名') }}<template v-if="textValue(relation.attitude)"> · {{ textValue(relation.attitude) }}</template></span>
                  <small v-if="relation.closeness !== undefined">亲近度 {{ numberValue(relation.closeness) }}</small>
                </article>
                <p v-if="!selectedCharacterRelations.length" class="memory-detail-empty">暂无与其他角色的明确关系记录。</p>
              </div>

              <div v-else-if="memoryDetailTab === 'events'" class="memory-table-wrap">
                <table class="memory-detail-table">
                  <thead><tr><th>事件</th><th>时间与地点</th><th>情节线</th></tr></thead>
                  <tbody>
                    <tr v-for="event in selectedCharacterEvents" :key="textValue(event.id)"><td>{{ textValue(event.summary) }}</td><td>{{ [textValue(event.story_time), textValue(event.location)].filter(Boolean).join(' · ') || '未记录' }}</td><td>{{ textValue(event.arc, '未分类') }}</td></tr>
                    <tr v-if="!selectedCharacterEvents.length"><td colspan="3" class="memory-table-empty">暂无关联的重要事件。</td></tr>
                  </tbody>
                </table>
              </div>

              <div v-else-if="memoryDetailTab === 'promises'" class="memory-table-wrap">
                <table class="memory-detail-table">
                  <thead><tr><th>任务 / 约定</th><th>参与者</th><th>状态</th></tr></thead>
                  <tbody>
                    <tr v-for="promise in selectedCharacterPromises" :key="textValue(promise.id)"><td>{{ textValue(promise.content) }}</td><td>{{ joinValues(promise.parties) }}</td><td>{{ textValue(promise.status, 'pending') }}</td></tr>
                    <tr v-if="!selectedCharacterPromises.length"><td colspan="3" class="memory-table-empty">暂无关联的任务或约定。</td></tr>
                  </tbody>
                </table>
              </div>

              <div v-else class="memory-table-wrap">
                <table class="memory-detail-table">
                  <thead><tr><th>物品</th><th>状态</th><th>位置</th></tr></thead>
                  <tbody>
                    <tr v-for="item in selectedCharacterItems" :key="textValue(item.id)"><td>{{ textValue(item.name) }}</td><td>{{ textValue(item.status, '未记录') }}</td><td>{{ textValue(item.location, '未记录') }}</td></tr>
                    <tr v-if="!selectedCharacterItems.length"><td colspan="3" class="memory-table-empty">暂无关联的重要物品。</td></tr>
                  </tbody>
                </table>
              </div>
            </article>

            <div v-else class="memory-empty-table-grid" aria-label="空白记忆表">
              <article v-for="table in memoryTables" :key="table.id">
                <h4>{{ table.title }}</h4>
                <div class="memory-table-wrap">
                  <table class="memory-detail-table memory-blank-table">
                    <thead><tr><th v-for="column in table.columns" :key="column">{{ column }}</th></tr></thead>
                    <tbody><tr><td v-for="column in table.columns" :key="column">&nbsp;</td></tr></tbody>
                  </table>
                </div>
              </article>
            </div>
          </template>
        </section>

        <section v-if="selected.id !== 'regex_filter'" class="plugin-section">
          <div class="plugin-section-heading">
            <div><span class="eyebrow">SETTINGS</span><h3>插件设置</h3></div>
            <button v-if="settingEntries.length" class="button primary" type="button" :disabled="saving" @click="saveSettings"><Save :size="16" />保存设置</button>
          </div>

          <div v-if="settingEntries.length" class="plugin-settings-grid">
            <template v-for="([key, definition]) in settingEntries" :key="key">
              <label v-if="definition.type === 'boolean'" class="setting-toggle plugin-setting-toggle" :title="settingHelp(key, definition)">
                <input v-model="form[key]" type="checkbox" />
                <span class="check-control"><Check :size="13" /></span>
                <span><strong>{{ definition.title || key }}</strong><small>{{ definition.description || '开关设置' }}</small></span>
              </label>

              <div v-else-if="selected.id === 'web_search' && key === 'search_model_name'" class="field" :title="settingHelp(key, definition)">
                <span>
                  {{ definition.title || key }}
                  <small v-if="searchModelOptions.length">{{ searchModelOptions.length }} 个可选模型</small>
                  <small v-else-if="definition.description">{{ definition.description }}</small>
                </span>
                <div class="model-picker-row">
                  <input v-if="manualSearchModel" v-model="form[key]" aria-label="搜索模型名称" placeholder="搜索接口提供的模型 ID" />
                  <select v-else v-model="form[key]" aria-label="搜索模型名称">
                    <option value="">请选择模型</option>
                    <option v-if="form[key] && !searchModelOptions.some((item) => item.id === form[key])" :value="form[key]">{{ form[key] }}（当前配置）</option>
                    <option v-for="model in searchModelOptions" :key="model.id" :value="model.id">{{ model.name === model.id ? model.id : `${model.name} · ${model.id}` }}</option>
                  </select>
                  <button class="icon-button" type="button" title="拉取搜索模型列表" :disabled="searchModelsLoading || form.engine !== 'model' || !form.search_model_base_url" @click="loadSearchModels">
                    <RefreshCw :size="16" :class="{ spinning: searchModelsLoading }" />
                  </button>
                  <button class="icon-button" type="button" :title="manualSearchModel ? '使用模型列表' : '手动填写模型名称'" :disabled="manualSearchModel && !searchModelOptions.length" @click="manualSearchModel = !manualSearchModel">
                    <PenLine :size="16" />
                  </button>
                </div>
              </div>

              <label v-else class="field" :class="{ 'plugin-textarea-setting': definition.format === 'textarea' }" :title="settingHelp(key, definition)">
                <span>
                  {{ definition.title || key }}
                  <small v-if="definition.description">{{ definition.description }}</small>
                  <small v-if="settingRange(definition)" class="setting-range">{{ settingRange(definition) }}</small>
                </span>
                <select v-if="definition.enum" v-model="form[key]">
                  <option v-for="(option, index) in definition.enum" :key="String(option)" :value="option">{{ definition.enum_names?.[index] || option }}</option>
                </select>
                <textarea
                  v-else-if="definition.type === 'string' && definition.format === 'textarea'"
                  :value="String(form[key] ?? '')"
                  rows="5"
                  @input="updateTextSetting(key, $event)"
                />
                <input
                  v-else-if="definition.type === 'string'"
                  :value="String(form[key] ?? '')"
                  :type="definition.format === 'password' ? 'password' : 'text'"
                  :maxlength="definition.maxLength"
                  :autocomplete="definition.format === 'password' ? 'new-password' : undefined"
                  :placeholder="definition.format === 'password' && selected.secret_settings_configured?.[key] ? '已保存，留空则保持不变' : ''"
                  @input="updateTextSetting(key, $event)"
                />
                <input
                  v-else
                  v-model.number="form[key]"
                  type="number"
                  :step="numberStep(definition)"
                  :min="definition.minimum"
                  :max="definition.maximum"
                />
              </label>
            </template>
          </div>
          <div v-else class="plugin-empty-settings"><PackageOpen :size="22" /><span>此插件没有可配置项</span></div>
        </section>

        <section class="plugin-section permissions-section">
          <div class="plugin-section-heading"><div><span class="eyebrow">CAPABILITIES</span><h3>声明的能力</h3></div></div>
          <div class="permission-list">
            <code v-for="permission in selected.permissions" :key="permission">{{ permission }}</code>
            <span v-if="!selected.permissions.length">未声明额外能力</span>
          </div>
        </section>
      </template>

      <div v-else-if="!loading" class="empty-state"><PackageOpen :size="28" /><strong>暂无插件</strong></div>
    </section>

  </div>
</template>
