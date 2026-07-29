<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { VueDraggable } from 'vue-draggable-plus'
import { Archive, Blocks, Check, ChevronDown, CircleAlert, CornerDownLeft, Eye, GripVertical, Plus, Save, Trash2, Upload } from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import type { PromptBlock, PromptPreview, PromptTemplate, Role } from '../types'

const templates = ref<PromptTemplate[]>([])
const selectedTemplateId = ref('')
const blocks = ref<PromptBlock[]>([])
const selectedBlockId = ref('')
const preview = ref<PromptPreview | null>(null)
const error = ref('')
const notice = ref('')
const loading = ref(true)
const saving = ref(false)
const stashOpen = ref(false)
const selectedTemplate = computed(() => templates.value.find((item) => item.id === selectedTemplateId.value) ?? null)
const selectedBlock = computed(() => blocks.value.find((item) => item.id === selectedBlockId.value) ?? null)
const stashedBlocks = computed(() => blocks.value.filter((item) => item.stashed))
const sequenceBlocks = computed({
  get: () => blocks.value.filter((item) => !item.stashed),
  set: (value: PromptBlock[]) => {
    blocks.value = [...value, ...stashedBlocks.value]
  },
})
const templateForm = reactive({ name: '', description: '' })
const blockForm = reactive<{
  title: string
  role: Role
  content: string
  enabled: boolean
  identifier: string | null
  marker: boolean
  injection_position: number
  injection_depth: number
  injection_order: number
}>({
  title: '', role: 'system', content: '', enabled: true, identifier: null, marker: false,
  injection_position: 0, injection_depth: 4, injection_order: 100,
})
const markerOptions = [
  { value: 'worldInfoBefore', label: '世界书（角色定义前）' },
  { value: 'worldInfoAfter', label: '世界书（角色定义后）' },
  { value: 'charDescription', label: '角色简介' },
  { value: 'charPersonality', label: '角色性格' },
  { value: 'scenario', label: '场景' },
  { value: 'personaDescription', label: '用户人格' },
  { value: 'dialogueExamples', label: '示例对话' },
  { value: 'chatHistory', label: '聊天历史' },
]
const macroGroups = computed(() => {
  const groups = new Map<string, Array<{ name: string; syntax: string; category: string }>>()
  for (const macro of preview.value?.supported_macros ?? []) {
    const entries = groups.get(macro.category) ?? []
    entries.push(macro)
    groups.set(macro.category, entries)
  }
  return [...groups.entries()].map(([category, macros]) => ({ category, macros }))
})

function selectBlock(block: PromptBlock | null) {
  selectedBlockId.value = block?.id ?? ''
  Object.assign(blockForm, block
    ? {
        title: block.title, role: block.role, content: block.content, enabled: block.enabled,
        identifier: block.identifier, marker: block.marker,
        injection_position: block.injection_position, injection_depth: block.injection_depth,
        injection_order: block.injection_order,
      }
    : {
        title: '', role: 'system', content: '', enabled: true, identifier: null, marker: false,
        injection_position: 0, injection_depth: 4, injection_order: 100,
      })
}

function selectTemplate(template: PromptTemplate) {
  selectedTemplateId.value = template.id
  Object.assign(templateForm, { name: template.name, description: template.description })
  blocks.value = [...template.blocks].sort((a, b) => a.position - b.position)
  const preferred = blocks.value.find((item) => item.id === selectedBlockId.value) ?? blocks.value[0] ?? null
  selectBlock(preferred)
  notice.value = ''
  void refreshPreview()
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    templates.value = await api<PromptTemplate[]>('/api/prompt-templates')
    const target = templates.value.find((item) => item.id === preferredId)
      ?? templates.value.find((item) => item.id === selectedTemplateId.value)
      ?? templates.value.find((item) => item.is_active)
      ?? templates.value[0]
    if (target) selectTemplate(target)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createTemplate() {
  try {
    const created = await api<PromptTemplate>('/api/prompt-templates', json('POST', {
      name: `新模板 ${templates.value.length + 1}`,
      description: '',
    }))
    await load(created.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function saveTemplate() {
  if (!selectedTemplate.value) return
  const updated = await api<PromptTemplate>(
    `/api/prompt-templates/${selectedTemplate.value.id}`,
    json('PUT', templateForm),
  )
  templates.value[templates.value.findIndex((item) => item.id === updated.id)] = updated
  selectTemplate(updated)
  notice.value = '模板信息已保存'
}

async function activateTemplate() {
  if (!selectedTemplate.value) return
  const updated = await api<PromptTemplate>(
    `/api/prompt-templates/${selectedTemplate.value.id}/activate`,
    json('POST'),
  )
  templates.value = templates.value.map((item) => ({ ...item, is_active: item.id === updated.id }))
  selectTemplate({ ...updated, blocks: blocks.value })
  notice.value = '已切换为当前模板'
}

async function removeTemplate() {
  if (!selectedTemplate.value || !window.confirm(`删除模板“${selectedTemplate.value.name}”？`)) return
  await api(`/api/prompt-templates/${selectedTemplate.value.id}`, json('DELETE'))
  selectedTemplateId.value = ''
  await load()
}

async function exportTemplate() {
  if (!selectedTemplate.value) return
  try {
    if (await exportJsonToFolder(
      `提示词模板-${selectedTemplate.value.name}`,
      exportEnvelope('catgirl_prompt_template', selectedTemplate.value),
    )) notice.value = '提示词模板已导出'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

async function addBlock() {
  if (!selectedTemplate.value) return
  const block = await api<PromptBlock>(
    `/api/prompt-templates/${selectedTemplate.value.id}/blocks`,
    json('POST', {
      title: `提示词块 ${blocks.value.length + 1}`, role: 'system', content: '', enabled: true,
      marker: false, injection_position: 0, injection_depth: 4, injection_order: 100,
    }),
  )
  blocks.value.push(block)
  selectedTemplate.value.blocks = [...blocks.value]
  selectBlock(block)
  await refreshPreview()
}

function replaceBlock(updated: PromptBlock) {
  const index = blocks.value.findIndex((item) => item.id === updated.id)
  if (index >= 0) blocks.value[index] = updated
  if (selectedTemplate.value) selectedTemplate.value.blocks = [...blocks.value]
}

async function saveBlock() {
  if (!selectedBlock.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<PromptBlock>(
      `/api/prompt-blocks/${selectedBlock.value.id}`,
      json('PUT', blockForm),
    )
    replaceBlock(updated)
    selectBlock(updated)
    notice.value = '提示词块已保存'
    await refreshPreview()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function toggleBlock(block: PromptBlock) {
  const updated = await api<PromptBlock>(
    `/api/prompt-blocks/${block.id}`,
    json('PUT', { enabled: !block.enabled }),
  )
  replaceBlock(updated)
  if (selectedBlockId.value === updated.id) selectBlock(updated)
  await refreshPreview()
}

async function setStashed(block: PromptBlock, stashed: boolean) {
  const updated = await api<PromptBlock>(
    `/api/prompt-blocks/${block.id}`,
    json('PUT', { stashed }),
  )
  blocks.value = [
    ...blocks.value.filter((item) => item.id !== updated.id && !item.stashed),
    ...(stashed ? [] : [updated]),
    ...blocks.value.filter((item) => item.id !== updated.id && item.stashed),
    ...(stashed ? [updated] : []),
  ]
  if (selectedBlockId.value === updated.id) selectBlock(updated)
  if (stashed) stashOpen.value = true
  notice.value = stashed ? '提示词块已收进折叠栏' : '提示词块已插入正式序列末尾'
  await persistOrder()
}

async function removeBlock() {
  if (!selectedBlock.value || !window.confirm(`删除提示词块“${selectedBlock.value.title}”？`)) return
  const id = selectedBlock.value.id
  await api(`/api/prompt-blocks/${id}`, json('DELETE'))
  blocks.value = blocks.value.filter((item) => item.id !== id).map((item, index) => ({ ...item, position: index }))
  if (selectedTemplate.value) selectedTemplate.value.blocks = [...blocks.value]
  selectBlock(blocks.value[0] ?? null)
  await refreshPreview()
}

async function persistOrder() {
  if (!selectedTemplate.value || blocks.value.length === 0) return
  blocks.value = await api<PromptBlock[]>(
    `/api/prompt-templates/${selectedTemplate.value.id}/blocks/order`,
    json('PUT', { block_ids: blocks.value.map((item) => item.id) }),
  )
  if (selectedTemplate.value) selectedTemplate.value.blocks = [...blocks.value]
  await refreshPreview()
}

async function refreshPreview() {
  if (!selectedTemplate.value) {
    preview.value = null
    return
  }
  try {
    preview.value = await api<PromptPreview>(`/api/prompt-templates/${selectedTemplate.value.id}/preview`)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '预览失败'
  }
}

function insertMacro(event: Event) {
  const target = event.target as HTMLSelectElement
  if (!target.value) return
  blockForm.content += `${blockForm.content ? '\n' : ''}${target.value}`
  target.value = ''
}

function selectMarker(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  blockForm.identifier = value || null
  if (value) blockForm.marker = true
}

onMounted(load)
</script>

<template>
  <div class="prompt-workbench">
    <aside class="item-rail prompt-template-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">TEMPLATES</span><strong>{{ templates.length }} 个模板</strong></div>
        <button class="icon-button primary-icon" type="button" title="添加模板" @click="createTemplate"><Plus :size="17" /></button>
      </div>
      <div class="item-list">
        <button
          v-for="template in templates"
          :key="template.id"
          type="button"
          :class="['item-row', { selected: template.id === selectedTemplateId }]"
          @click="selectTemplate(template)"
        >
          <span class="item-symbol"><Blocks :size="18" /></span>
          <span><strong>{{ template.name }}</strong><small>{{ template.blocks.length }} 个块</small></span>
          <i v-if="template.is_active" class="active-dot" title="当前使用" />
        </button>
      </div>
    </aside>

    <section v-if="selectedTemplate" class="prompt-main">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <div class="prompt-toolbar">
        <div>
          <div class="heading-with-status">
            <input v-model="templateForm.name" class="title-input" maxlength="120" aria-label="模板名称" />
            <span v-if="selectedTemplate.is_active" class="active-badge">当前使用</span>
          </div>
          <input v-model="templateForm.description" class="description-input" placeholder="模板描述" aria-label="模板描述" />
        </div>
        <div class="action-row">
          <button class="icon-button" type="button" title="导出提示词模板" @click="exportTemplate"><Upload :size="17" /></button>
          <button class="button secondary" type="button" @click="saveTemplate"><Save :size="16" />保存模板</button>
          <button v-if="!selectedTemplate.is_active" class="button secondary" type="button" @click="activateTemplate"><Check :size="16" />设为当前</button>
          <button class="icon-button danger" type="button" title="删除模板" @click="removeTemplate"><Trash2 :size="17" /></button>
        </div>
      </div>

      <div class="prompt-columns">
        <section class="block-panel">
          <div class="panel-heading"><div><span class="eyebrow">ORDER</span><h3>提示词块</h3></div><button class="icon-button" type="button" title="添加提示词块" @click="addBlock"><Plus :size="17" /></button></div>
          <VueDraggable v-model="sequenceBlocks" class="block-list" handle=".drag-handle" :animation="150" @end="persistOrder">
            <article
              v-for="block in sequenceBlocks"
              :key="block.id"
              :class="['prompt-block-row', { selected: block.id === selectedBlockId, disabled: !block.enabled }]"
              @click="selectBlock(block)"
            >
              <button class="drag-handle" type="button" title="拖动排序"><GripVertical :size="17" /></button>
              <div>
                <strong>{{ block.title }}</strong>
                <span :class="['role-label', block.role]">{{ block.role }}</span>
                <span v-if="block.marker" class="injection-label marker-label">marker</span>
                <span v-if="block.injection_position === 1" class="injection-label">深度 {{ block.injection_depth }}</span>
              </div>
              <button class="icon-button stash-button" type="button" title="收进折叠栏" @click.stop="setStashed(block, true)"><Archive :size="15" /></button>
              <button :class="['mini-toggle', { on: block.enabled }]" type="button" :title="block.enabled ? '停用' : '启用'" @click.stop="toggleBlock(block)"><i /></button>
            </article>
          </VueDraggable>

          <section class="prompt-stash">
            <button class="prompt-stash-toggle" type="button" :aria-expanded="stashOpen" @click="stashOpen = !stashOpen">
              <ChevronDown :size="16" :class="['stash-chevron', { open: stashOpen }]" />
              <Archive :size="15" />
              <strong>收纳的提示词</strong>
              <small>{{ stashedBlocks.length }}</small>
            </button>
            <div v-if="stashOpen" class="prompt-stash-list">
              <article
                v-for="block in stashedBlocks"
                :key="block.id"
                :class="['prompt-block-row stashed-row', { selected: block.id === selectedBlockId }]"
                @click="selectBlock(block)"
              >
                <div>
                  <strong>{{ block.title }}</strong>
                  <span :class="['role-label', block.role]">{{ block.role }}</span>
                  <span v-if="block.marker" class="injection-label marker-label">marker</span>
                </div>
                <button class="button secondary stash-insert" type="button" title="插入到正式提示词块" @click.stop="setStashed(block, false)"><CornerDownLeft :size="15" />插入</button>
              </article>
              <p v-if="!stashedBlocks.length" class="prompt-stash-empty">折叠栏是空的。收纳的提示词块不会发送给模型，可随时插回正式序列。</p>
            </div>
          </section>
        </section>

        <section class="block-editor">
          <template v-if="selectedBlock">
            <div class="panel-heading">
              <div><span class="eyebrow">BLOCK</span><h3>块编辑器</h3></div>
              <span v-if="selectedBlock.stashed" class="active-badge disabled">已收纳 · 不发送</span>
              <button class="icon-button danger" type="button" title="删除提示词块" @click="removeBlock"><Trash2 :size="16" /></button>
            </div>
            <label class="field"><span>标题</span><input v-model="blockForm.title" maxlength="120" /></label>
            <div class="field">
              <span>消息角色</span>
              <div class="segmented-control">
                <button v-for="role in (['system', 'user', 'assistant'] as Role[])" :key="role" type="button" :class="{ active: blockForm.role === role }" @click="blockForm.role = role">{{ role }}</button>
              </div>
            </div>
            <div class="prompt-injection-settings">
              <label class="setting-toggle compact-toggle">
                <input v-model="blockForm.marker" type="checkbox" />
                <span class="check-control"><Check :size="13" /></span>
                <span><strong>动态标记</strong><small>运行时由角色、聊天历史或世界书内容替换。</small></span>
              </label>
              <label class="field">
                <span>动态标识符</span>
                <select :value="blockForm.identifier ?? ''" @change="selectMarker">
                  <option value="">自定义 / 无</option>
                  <option v-for="marker in markerOptions" :key="marker.value" :value="marker.value">{{ marker.label }}（{{ marker.value }}）</option>
                </select>
              </label>
              <div class="field">
                <span>插入方式</span>
                <div class="segmented-control insertion-control">
                  <button type="button" :class="{ active: blockForm.injection_position === 0 }" @click="blockForm.injection_position = 0">相对顺序</button>
                  <button type="button" :class="{ active: blockForm.injection_position === 1 }" @click="blockForm.injection_position = 1">聊天中指定深度</button>
                </div>
              </div>
              <div v-if="blockForm.injection_position === 1" class="form-grid depth-settings">
                <label class="field"><span>插入深度</span><input v-model.number="blockForm.injection_depth" type="number" min="0" max="1000" /></label>
                <label class="field"><span>同深度顺序</span><input v-model.number="blockForm.injection_order" type="number" min="-100000" max="100000" /></label>
              </div>
            </div>
            <label class="field grow-field">
              <span>内容 <small v-if="blockForm.marker">动态标记的内容由运行时生成</small></span>
              <textarea v-model="blockForm.content" rows="14" :disabled="blockForm.marker" :placeholder="blockForm.marker ? '动态标记无需填写固定内容' : ''" />
            </label>
            <div class="editor-actions">
              <select aria-label="插入宏" @change="insertMacro">
                <option value="">插入宏</option>
                <optgroup v-for="group in macroGroups" :key="group.category" :label="group.category">
                  <option v-for="macro in group.macros" :key="macro.syntax" :value="macro.syntax">{{ macro.name }} · {{ macro.syntax }}</option>
                </optgroup>
              </select>
              <button class="button primary" type="button" :disabled="saving" @click="saveBlock"><Save :size="16" />保存块</button>
            </div>
          </template>
          <div v-else class="empty-state"><Blocks :size="26" /><strong>暂无提示词块</strong></div>
        </section>

        <section class="preview-panel">
          <div class="panel-heading"><div><span class="eyebrow">COMPILED</span><h3>实际发送预览</h3><small class="preview-total-tokens">总 {{ preview?.total_tokens ?? 0 }} tokens</small></div><button class="icon-button" type="button" title="刷新预览" @click="refreshPreview"><Eye :size="17" /></button></div>
          <div class="preview-context preview-context-stack"><span>角色人设</span><strong>{{ preview?.character_name ?? '未选择' }}</strong><span>用户人设</span><strong>{{ preview?.user_persona_name ?? '未选择' }}</strong></div>
          <div v-if="preview?.unresolved_variables.length" class="unresolved">未解析：{{ preview.unresolved_variables.join(', ') }}</div>
          <div class="preview-list">
            <article v-for="(message, index) in preview?.messages" :key="`${message.block_id}:${index}`" :class="['preview-message', `kind-${message.kind}`]">
              <header>
                <span>{{ index + 1 }}</span><strong>{{ message.title }}</strong>
                <small v-if="message.marker && message.kind === 'template'" class="preview-injection">marker</small>
                <small v-if="message.insertion_label" class="preview-injection">{{ message.insertion_label }}</small>
                <small class="preview-token-count">{{ message.token_count }} tokens</small>
                <i v-if="message.kind !== 'history'" :class="['role-label', message.role]">{{ message.role }}</i>
              </header>
              <pre v-if="message.content_visible">{{ message.content || '（空内容）' }}</pre>
            </article>
          </div>
        </section>
      </div>
    </section>

    <div v-else-if="!loading" class="empty-state"><Blocks :size="28" /><strong>暂无模板</strong></div>
  </div>
</template>
