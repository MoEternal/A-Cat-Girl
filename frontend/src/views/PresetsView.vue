<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Blocks,
  BrainCircuit,
  BookOpen,
  Check,
  CircleAlert,
  CopyPlus,
  Download,
  Image,
  MessageSquareText,
  Save,
  ServerCog,
  SlidersHorizontal,
  Trash2,
  Upload,
  UserRoundCog,
} from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import { importSillyTavernFiles } from '../sillytavernImport'
import type {
  Character,
  ConfigurationPreset,
  PromptTemplate,
  Provider,
  UserPersona,
  WorldBook,
} from '../types'

type PresetForm = Omit<ConfigurationPreset, 'id' | 'is_active' | 'created_at' | 'updated_at'>

const presets = ref<ConfigurationPreset[]>([])
const providers = ref<Provider[]>([])
const templates = ref<PromptTemplate[]>([])
const characters = ref<Character[]>([])
const userPersonas = ref<UserPersona[]>([])
const worldBooks = ref<WorldBook[]>([])
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const importing = ref(false)
const importInput = ref<HTMLInputElement | null>(null)
const selected = computed(() => presets.value.find((item) => item.id === selectedId.value) ?? null)
const form = reactive<PresetForm>({
  name: '',
  description: '',
  provider_id: null,
  prompt_template_id: null,
  character_id: null,
  user_persona_id: null,
  world_book_ids: [],
  max_context_unlocked: false,
  context_length: 128000,
  max_response_tokens: 2048,
  candidate_count: 1,
  streaming: true,
  temperature: 1,
  frequency_penalty: 0,
  presence_penalty: 0,
  top_p: 1,
  quote_wrapping: false,
  continue_prefill: false,
  squash_system_messages: false,
  function_calling: false,
  media_inlining: true,
  image_quality: 'auto',
  show_thoughts: true,
  reasoning_effort: 'auto',
})

const contextMaximum = computed(() => form.max_context_unlocked ? 2_000_000 : 200_000)
const currentProvider = computed(() => providers.value.find((item) => item.id === form.provider_id))
const currentTemplate = computed(() => templates.value.find((item) => item.id === form.prompt_template_id))
const currentCharacter = computed(() => characters.value.find((item) => item.id === form.character_id))
const currentUserPersona = computed(() => userPersonas.value.find((item) => item.id === form.user_persona_id))
const currentWorldBooks = computed(() => form.world_book_ids
  .map((id) => worldBooks.value.find((item) => item.id === id))
  .filter((item): item is WorldBook => Boolean(item)))

function selectPreset(preset: ConfigurationPreset) {
  selectedId.value = preset.id
  const { id: _id, is_active: _active, created_at: _created, updated_at: _updated, ...values } = preset
  Object.assign(form, values)
  error.value = ''
  notice.value = ''
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    const [presetData, providerData, templateData, characterData, userPersonaData, worldBookData] = await Promise.all([
      api<ConfigurationPreset[]>('/api/presets'),
      api<Provider[]>('/api/providers'),
      api<PromptTemplate[]>('/api/prompt-templates'),
      api<Character[]>('/api/characters'),
      api<UserPersona[]>('/api/user-personas'),
      api<WorldBook[]>('/api/world-books'),
    ])
    presets.value = presetData
    providers.value = providerData
    templates.value = templateData
    characters.value = characterData
    userPersonas.value = userPersonaData
    worldBooks.value = worldBookData
    const target = presets.value.find((item) => item.id === preferredId)
      ?? presets.value.find((item) => item.id === selectedId.value)
      ?? presets.value.find((item) => item.is_active)
      ?? presets.value[0]
    if (target) selectPreset(target)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function clonePreset() {
  error.value = ''
  try {
    const created = await api<ConfigurationPreset>('/api/presets', json('POST', {
      ...form,
      name: `${form.name || '预设'} 副本`,
    }))
    await load(created.id)
    notice.value = '已创建预设副本'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function save() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<ConfigurationPreset>(`/api/presets/${selected.value.id}`, json('PUT', form))
    presets.value[presets.value.findIndex((item) => item.id === updated.id)] = updated
    selectPreset(updated)
    notice.value = updated.is_active ? '预设已保存并同步当前配置' : '预设已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function activate() {
  if (!selected.value) return
  error.value = ''
  try {
    const updated = await api<ConfigurationPreset>(`/api/presets/${selected.value.id}/activate`, json('POST'))
    presets.value = presets.value.map((item) => ({ ...item, is_active: item.id === updated.id }))
    selectPreset(updated)
    notice.value = '整套预设已切换'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '切换失败'
  }
}

async function remove() {
  if (!selected.value || !window.confirm(`删除预设“${selected.value.name}”？`)) return
  await api(`/api/presets/${selected.value.id}`, json('DELETE'))
  selectedId.value = ''
  await load()
}

async function exportPreset() {
  if (!selected.value) return
  error.value = ''
  try {
    const provider = selected.value.provider_id
      ? await api<Record<string, unknown>>(`/api/providers/${selected.value.provider_id}/export`, json('POST'))
      : null
    const data = {
      preset: selected.value,
      provider,
      prompt_template: templates.value.find((item) => item.id === selected.value?.prompt_template_id) ?? null,
      character: characters.value.find((item) => item.id === selected.value?.character_id) ?? null,
      user_persona: userPersonas.value.find((item) => item.id === selected.value?.user_persona_id) ?? null,
      world_books: worldBooks.value.filter((book) => {
        const character = characters.value.find((item) => item.id === selected.value?.character_id)
        return selected.value?.world_book_ids.includes(book.id)
          || book.scope === 'global'
          || character?.world_book_ids.includes(book.id)
      }),
    }
    if (await exportJsonToFolder(`预设-${selected.value.name}`, exportEnvelope('catgirl_preset_bundle', data))) {
      notice.value = '整套预设已导出'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

function clampContext() {
  form.context_length = Math.min(form.context_length, contextMaximum.value)
  form.max_response_tokens = Math.min(form.max_response_tokens, form.context_length)
}

async function importSillyTavern(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  target.value = ''
  if (!files.length) return
  importing.value = true
  error.value = ''
  try {
    const report = await importSillyTavernFiles(files)
    await load(report.preset_id ?? undefined)
    if (!report.preset_id && report.world_book_ids.length) {
      form.world_book_ids = [...new Set([...form.world_book_ids, ...report.world_book_ids])]
    }
    const summary = [
      report.preset_id ? `预设“${report.preset_name}”` : '',
      report.imported_characters ? `${report.imported_characters} 张角色卡` : '',
      report.imported_prompt_blocks ? `${report.imported_prompt_blocks} 个提示词块` : '',
      report.world_book_ids.length ? `${report.world_book_ids.length} 本世界书 / ${report.imported_world_entries} 个条目` : '',
    ].filter(Boolean).join('、')
    notice.value = `资源已导入：${summary || '无新增资源'}${report.warnings.length ? `；警告：${report.warnings.join('；')}` : ''}`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导入失败'
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="management-layout preset-layout">
    <aside class="item-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">PRESETS</span><strong>{{ presets.length }} 套预设</strong></div>
        <button class="icon-button primary-icon" type="button" title="复制当前预设" @click="clonePreset"><CopyPlus :size="17" /></button>
      </div>
      <div class="item-list">
        <button
          v-for="preset in presets"
          :key="preset.id"
          type="button"
          :class="['item-row', { selected: preset.id === selectedId }]"
          @click="selectPreset(preset)"
        >
          <span class="item-symbol preset-symbol"><SlidersHorizontal :size="18" /></span>
          <span><strong>{{ preset.name }}</strong><small>{{ preset.description || '组合配置' }}</small></span>
          <i v-if="preset.is_active" class="active-dot" title="当前使用" />
        </button>
      </div>
    </aside>

    <section class="editor-pane preset-editor">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selected">
        <div class="editor-heading preset-heading">
          <div>
            <div class="heading-with-status">
              <h2>{{ selected.name }}</h2>
              <span v-if="selected.is_active" class="active-badge">当前使用</span>
            </div>
            <p>{{ selected.description || '供应商、提示词、人设与生成参数的完整组合' }}</p>
          </div>
          <div class="action-row">
            <input ref="importInput" class="visually-hidden" type="file" accept=".json,.png,application/json,image/png" multiple @change="importSillyTavern" />
            <button class="button secondary" type="button" :disabled="importing" @click="importInput?.click()"><Download :size="16" />{{ importing ? '导入中' : '导入配置' }}</button>
            <button class="icon-button" type="button" title="导出整套预设" @click="exportPreset"><Upload :size="17" /></button>
            <button v-if="!selected.is_active" class="button secondary" type="button" @click="activate"><Check :size="16" />切换整套预设</button>
            <button class="button primary" type="button" :disabled="saving" @click="save"><Save :size="16" />保存预设</button>
            <button class="icon-button danger" type="button" title="删除预设" @click="remove"><Trash2 :size="17" /></button>
          </div>
        </div>

        <div class="preset-content">
          <section class="preset-section resource-section">
            <div class="preset-section-heading"><SlidersHorizontal :size="18" /><div><h3>组合资源</h3><span>此预设激活时同时切换供应商、提示词、用户人设、角色人设和世界书</span></div></div>
            <div class="resource-grid">
              <label class="resource-select">
                <span class="resource-icon coral"><ServerCog :size="19" /></span>
                <span><small>API 供应商</small><select v-model="form.provider_id"><option :value="null">未选择</option><option v-for="item in providers" :key="item.id" :value="item.id">{{ item.name }}</option></select><em>{{ currentProvider?.model || '未选择模型' }}</em></span>
              </label>
              <label class="resource-select">
                <span class="resource-icon blue"><Blocks :size="19" /></span>
                <span><small>提示词模板</small><select v-model="form.prompt_template_id"><option :value="null">未选择</option><option v-for="item in templates" :key="item.id" :value="item.id">{{ item.name }}</option></select><em>{{ currentTemplate?.blocks.length ?? 0 }} 个提示词块</em></span>
              </label>
              <label class="resource-select">
                <span class="resource-icon green"><UserRoundCog :size="19" /></span>
                <span><small>角色人设</small><select v-model="form.character_id"><option :value="null">未选择</option><option v-for="item in characters" :key="item.id" :value="item.id">{{ item.name }}</option></select><em>{{ currentCharacter?.summary || '暂无简介' }}</em></span>
              </label>
              <label class="resource-select">
                <span class="resource-icon violet"><UserRoundCog :size="19" /></span>
                <span><small>用户人设</small><select v-model="form.user_persona_id"><option :value="null">未选择</option><option v-for="item in userPersonas" :key="item.id" :value="item.id">{{ item.name }}</option></select><em>{{ currentUserPersona ? '已选择用户人设' : '未选择' }}</em></span>
              </label>
              <div class="resource-select world-book-resource">
                <span class="resource-icon amber"><BookOpen :size="19" /></span>
                <span>
                  <small>世界书（可多选）</small>
                  <div class="world-book-checks">
                    <label v-for="item in worldBooks" :key="item.id">
                      <input v-model="form.world_book_ids" type="checkbox" :value="item.id" />
                      <span>{{ item.name }}</span>
                    </label>
                    <em v-if="!worldBooks.length">暂无世界书，可先导入</em>
                  </div>
                  <em>{{ currentWorldBooks.length }} 本已关联，按勾选顺序生效</em>
                </span>
              </div>
            </div>
          </section>

          <section class="preset-section compact-section">
            <div class="preset-section-heading"><MessageSquareText :size="18" /><div><h3>预设信息</h3></div></div>
            <div class="form-grid preset-name-grid">
              <label class="field"><span>预设名称</span><input v-model="form.name" required maxlength="120" /></label>
              <label class="field"><span>描述</span><input v-model="form.description" maxlength="500" /></label>
            </div>
          </section>

          <div class="preset-two-column">
            <section class="preset-section">
              <div class="preset-section-heading"><MessageSquareText :size="18" /><div><h3>上下文与回复</h3></div></div>
              <label class="setting-toggle">
                <input v-model="form.max_context_unlocked" type="checkbox" @change="clampContext" />
                <span class="check-control"><Check :size="13" /></span>
                <span><strong>解锁上下文长度</strong><small>允许设置超过 200,000 token 的上下文。</small></span>
              </label>
              <label class="range-setting">
                <span><strong>上下文长度</strong><output>{{ form.context_length.toLocaleString() }}</output></span>
                <div><input v-model.number="form.context_length" type="range" min="512" :max="contextMaximum" step="1" /><input v-model.number="form.context_length" type="number" min="512" :max="contextMaximum" step="1" /></div>
              </label>
              <label class="range-setting">
                <span><strong>最大回复长度</strong><output>{{ form.max_response_tokens.toLocaleString() }}</output></span>
                <div><input v-model.number="form.max_response_tokens" type="range" min="1" max="65536" step="1" /><input v-model.number="form.max_response_tokens" type="number" min="1" max="1000000" step="1" /></div>
              </label>
              <label class="field"><span>每次生成候选回复数</span><input v-model.number="form.candidate_count" type="number" min="1" max="16" step="1" /></label>
              <label class="setting-toggle">
                <input v-model="form.streaming" type="checkbox" />
                <span class="check-control"><Check :size="13" /></span>
                <span><strong>流式传输</strong><small>逐段接收生成结果；QQ 端仍会按发送策略输出。</small></span>
              </label>
            </section>

            <section class="preset-section">
              <div class="preset-section-heading"><SlidersHorizontal :size="18" /><div><h3>采样参数</h3></div></div>
              <label class="range-setting"><span><strong>温度</strong><output>{{ form.temperature.toFixed(2) }}</output></span><div><input v-model.number="form.temperature" type="range" min="0" max="2" step="0.01" /><input v-model.number="form.temperature" type="number" min="0" max="2" step="0.01" /></div></label>
              <label class="range-setting"><span><strong>频率惩罚</strong><output>{{ form.frequency_penalty.toFixed(2) }}</output></span><div><input v-model.number="form.frequency_penalty" type="range" min="-2" max="2" step="0.01" /><input v-model.number="form.frequency_penalty" type="number" min="-2" max="2" step="0.01" /></div></label>
              <label class="range-setting"><span><strong>存在惩罚</strong><output>{{ form.presence_penalty.toFixed(2) }}</output></span><div><input v-model.number="form.presence_penalty" type="range" min="-2" max="2" step="0.01" /><input v-model.number="form.presence_penalty" type="number" min="-2" max="2" step="0.01" /></div></label>
              <label class="range-setting"><span><strong>Top P</strong><output>{{ form.top_p.toFixed(2) }}</output></span><div><input v-model.number="form.top_p" type="range" min="0" max="1" step="0.01" /><input v-model.number="form.top_p" type="number" min="0" max="1" step="0.01" /></div></label>
            </section>

            <section class="preset-section">
              <div class="preset-section-heading"><MessageSquareText :size="18" /><div><h3>消息处理</h3></div></div>
              <label class="setting-toggle"><input v-model="form.quote_wrapping" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>用引号包裹用户消息</strong><small>发送前为整条用户消息加上引号。</small></span></label>
              <label class="setting-toggle"><input v-model="form.continue_prefill" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>继续预填充</strong><small>继续生成时保留最后一条 assistant 消息。</small></span></label>
              <label class="setting-toggle"><input v-model="form.squash_system_messages" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>压缩系统消息</strong><small>将连续的 system 消息合并为一条。</small></span></label>
              <label class="setting-toggle"><input v-model="form.function_calling" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>启用函数调用</strong><small>允许模型使用已注册的功能工具。</small></span></label>
            </section>

            <section class="preset-section">
              <div class="preset-section-heading"><BrainCircuit :size="18" /><div><h3>模型能力</h3></div></div>
              <label class="setting-toggle"><input v-model="form.media_inlining" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>发送图片</strong><small>模型支持时，将 QQ 图片加入消息。</small></span></label>
              <label class="inline-select"><span><Image :size="17" /><strong>图片质量</strong></span><select v-model="form.image_quality"><option value="auto">自动</option><option value="low">低</option><option value="high">高</option></select></label>
              <label class="setting-toggle"><input v-model="form.show_thoughts" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>请求思维链</strong><small>向支持推理的模型请求 reasoning 内容。</small></span></label>
              <label class="inline-select"><span><BrainCircuit :size="17" /><strong>推理强度</strong></span><select v-model="form.reasoning_effort"><option value="auto">自动</option><option value="min">最低</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="max">最高</option></select></label>
            </section>
          </div>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state"><SlidersHorizontal :size="28" /><strong>暂无预设</strong></div>
    </section>
  </div>
</template>
