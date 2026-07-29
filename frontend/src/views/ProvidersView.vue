<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Bot, Braces, Check, CircleAlert, FlaskConical, KeyRound, PenLine, Plus, RefreshCw, Save, ServerCog, Sparkles, Trash2, Upload } from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import type { ChatCompletionSource, PromptPostProcessing, Provider, ProviderKind } from '../types'

const providerKinds: Array<{ id: ProviderKind; label: string; icon: typeof Braces }> = [
  { id: 'openai_compatible', label: 'OpenAI 兼容', icon: Braces },
  { id: 'anthropic', label: 'Anthropic', icon: Bot },
  { id: 'google_gemini', label: 'Gemini / Google', icon: Sparkles },
]

const chatCompletionSources: Array<{
  id: ChatCompletionSource
  label: string
  kind: ProviderKind
  baseUrl: string
}> = [
  { id: 'custom', label: '自定义', kind: 'openai_compatible', baseUrl: '' },
  { id: 'openai', label: 'OpenAI', kind: 'openai_compatible', baseUrl: 'https://api.openai.com/v1' },
  { id: 'ai21', label: 'AI21', kind: 'openai_compatible', baseUrl: 'https://api.ai21.com/studio/v1' },
  { id: 'aimlapi', label: 'AI/ML API', kind: 'openai_compatible', baseUrl: 'https://api.aimlapi.com/v1' },
  { id: 'azure_openai', label: 'Azure OpenAI', kind: 'openai_compatible', baseUrl: '' },
  { id: 'chutes', label: 'Chutes', kind: 'openai_compatible', baseUrl: 'https://llm.chutes.ai/v1' },
  { id: 'claude', label: 'Claude', kind: 'anthropic', baseUrl: 'https://api.anthropic.com/v1' },
  { id: 'workers_ai', label: 'Cloudflare Workers AI', kind: 'openai_compatible', baseUrl: '' },
  { id: 'cohere', label: 'Cohere', kind: 'openai_compatible', baseUrl: 'https://api.cohere.ai/compatibility/v1' },
  { id: 'deepseek', label: 'DeepSeek', kind: 'openai_compatible', baseUrl: 'https://api.deepseek.com/v1' },
  { id: 'electronhub', label: 'Electron Hub', kind: 'openai_compatible', baseUrl: 'https://api.electronhub.ai/v1' },
  { id: 'fireworks', label: 'Fireworks AI', kind: 'openai_compatible', baseUrl: 'https://api.fireworks.ai/inference/v1' },
  { id: 'groq', label: 'Groq', kind: 'openai_compatible', baseUrl: 'https://api.groq.com/openai/v1' },
  { id: 'makersuite', label: 'Google AI Studio', kind: 'google_gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta' },
  { id: 'vertexai', label: 'Google Vertex AI', kind: 'google_gemini', baseUrl: '' },
  { id: 'mistralai', label: 'MistralAI', kind: 'openai_compatible', baseUrl: 'https://api.mistral.ai/v1' },
  { id: 'minimax', label: 'MiniMax', kind: 'openai_compatible', baseUrl: 'https://api.minimax.io/v1' },
  { id: 'moonshot', label: 'Moonshot AI', kind: 'openai_compatible', baseUrl: 'https://api.moonshot.ai/v1' },
  { id: 'nanogpt', label: 'NanoGPT', kind: 'openai_compatible', baseUrl: 'https://nano-gpt.com/api/v1' },
  { id: 'openrouter', label: 'OpenRouter', kind: 'openai_compatible', baseUrl: 'https://openrouter.ai/api/v1' },
  { id: 'perplexity', label: 'Perplexity', kind: 'openai_compatible', baseUrl: 'https://api.perplexity.ai' },
  { id: 'pollinations', label: 'Pollinations', kind: 'openai_compatible', baseUrl: 'https://gen.pollinations.ai/v1' },
  { id: 'siliconflow', label: 'SiliconFlow', kind: 'openai_compatible', baseUrl: 'https://api.siliconflow.com/v1' },
  { id: 'xai', label: 'xAI (Grok)', kind: 'openai_compatible', baseUrl: 'https://api.x.ai/v1' },
  { id: 'zai', label: 'Z.AI (GLM)', kind: 'openai_compatible', baseUrl: 'https://api.z.ai/api/paas/v4' },
]

const providers = ref<Provider[]>([])
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const testResult = ref<{ ok: boolean; message: string; latency_ms: number } | null>(null)
const modelOptions = ref<Array<{ id: string; name: string }>>([])
const modelsLoading = ref(false)
const manualModel = ref(true)
const selected = computed(() => providers.value.find((item) => item.id === selectedId.value) ?? null)
const form = reactive({
  name: '',
  kind: 'openai_compatible' as ProviderKind,
  chat_completion_source: 'custom' as ChatCompletionSource,
  prompt_post_processing: '' as PromptPostProcessing,
  base_url: '',
  model: '',
  api_key: '',
  priority: 1,
  enabled: true,
})

function selectProvider(provider: Provider) {
  selectedId.value = provider.id
  Object.assign(form, {
    name: provider.name,
    kind: provider.kind,
    chat_completion_source: provider.chat_completion_source,
    prompt_post_processing: provider.prompt_post_processing,
    base_url: provider.base_url,
    model: provider.model,
    api_key: '',
    priority: provider.priority,
    enabled: provider.enabled,
  })
  testResult.value = null
  modelOptions.value = []
  manualModel.value = true
  notice.value = ''
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    providers.value = await api<Provider[]>('/api/providers')
    const target = providers.value.find((item) => item.id === preferredId)
      ?? providers.value.find((item) => item.id === selectedId.value)
      ?? providers.value.find((item) => item.is_active)
      ?? providers.value[0]
    if (target) selectProvider(target)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createProvider() {
  try {
    const created = await api<Provider>('/api/providers', json('POST', {
      name: `新接口 ${providers.value.length + 1}`,
      kind: 'openai_compatible',
      chat_completion_source: 'custom',
      prompt_post_processing: '',
      base_url: '',
      model: '',
    }))
    await load(created.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

const baseUrlPlaceholder = computed(() => {
  const source = chatCompletionSources.find((item) => item.id === form.chat_completion_source)
  if (source?.baseUrl) return source.baseUrl
  if (form.kind === 'anthropic') return 'https://api.anthropic.com/v1'
  if (form.kind === 'google_gemini') return 'https://generativelanguage.googleapis.com/v1beta'
  return 'https://api.example.com/v1'
})

function applyChatCompletionSource() {
  const source = chatCompletionSources.find((item) => item.id === form.chat_completion_source)
  if (!source) return
  form.kind = source.kind
  form.base_url = source.baseUrl
  modelOptions.value = []
  manualModel.value = true
}

watch(
  () => [form.kind, form.base_url],
  () => {
    if (!modelOptions.value.length) return
    modelOptions.value = []
    manualModel.value = true
  },
)

async function save() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const payload: Record<string, unknown> = { ...form }
    if (!form.api_key) delete payload.api_key
    const updated = await api<Provider>(`/api/providers/${selected.value.id}`, json('PUT', payload))
    const index = providers.value.findIndex((item) => item.id === updated.id)
    providers.value[index] = updated
    selectProvider(updated)
    notice.value = '配置已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function activate() {
  if (!selected.value) return
  const updated = await api<Provider>(`/api/providers/${selected.value.id}/activate`, json('POST'))
  providers.value = providers.value.map((item) => ({ ...item, is_active: item.id === updated.id }))
  selectProvider(updated)
  notice.value = '已切换为当前供应商'
}

async function testConnection() {
  if (!selected.value) return
  testResult.value = null
  error.value = ''
  try {
    testResult.value = await api(`/api/providers/${selected.value.id}/test`, json('POST'))
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '测试失败'
  }
}

async function loadModels() {
  if (!selected.value) return
  modelsLoading.value = true
  error.value = ''
  try {
    const payload: Record<string, unknown> = {
      kind: form.kind,
      chat_completion_source: form.chat_completion_source,
      base_url: form.base_url,
    }
    if (form.api_key) payload.api_key = form.api_key
    const values = await api<Array<{ id: string; name: string }>>(
      `/api/providers/${selected.value.id}/models`,
      json('POST', payload),
    )
    if (!values.length) {
      modelOptions.value = []
      manualModel.value = true
      error.value = '接口没有返回可用模型，请手动填写模型名称'
      return
    }
    modelOptions.value = values
    manualModel.value = false
    notice.value = `已拉取 ${values.length} 个模型`
  } catch (reason) {
    modelOptions.value = []
    manualModel.value = true
    error.value = reason instanceof Error ? reason.message : '模型列表拉取失败'
  } finally {
    modelsLoading.value = false
  }
}

async function remove() {
  if (!selected.value || !window.confirm(`删除供应商“${selected.value.name}”？`)) return
  await api(`/api/providers/${selected.value.id}`, json('DELETE'))
  selectedId.value = ''
  await load()
}

async function exportProvider() {
  if (!selected.value) return
  error.value = ''
  try {
    const data = await api<Record<string, unknown>>(`/api/providers/${selected.value.id}/export`, json('POST'))
    if (await exportJsonToFolder(`API配置-${selected.value.name}`, exportEnvelope('catgirl_provider', data))) {
      notice.value = 'API 配置已导出'
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

onMounted(load)
</script>

<template>
  <div class="management-layout">
    <aside class="item-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">PROVIDERS</span><strong>{{ providers.length }} 个配置</strong></div>
        <button class="icon-button primary-icon" type="button" title="添加供应商" @click="createProvider"><Plus :size="17" /></button>
      </div>
      <div class="item-list">
        <button
          v-for="provider in providers"
          :key="provider.id"
          type="button"
          :class="['item-row', { selected: provider.id === selectedId }]"
          @click="selectProvider(provider)"
        >
          <span class="item-symbol"><ServerCog :size="18" /></span>
          <span><strong>#{{ provider.priority }} · {{ provider.name }}</strong><small>{{ provider.model || '未选择模型' }}</small></span>
          <i v-if="provider.is_active" class="active-dot" title="当前使用" />
        </button>
      </div>
    </aside>

    <section class="editor-pane">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selected">
        <div class="editor-heading">
          <div>
            <div class="heading-with-status">
              <h2>API 接口配置</h2>
              <span v-if="selected.is_active" class="active-badge">当前使用</span>
            </div>
            <p>{{ selected.name }}</p>
          </div>
          <div class="action-row">
            <button class="button secondary" type="button" @click="testConnection"><FlaskConical :size="16" />测试连接</button>
            <button class="icon-button" type="button" title="导出 API 配置" @click="exportProvider"><Upload :size="17" /></button>
            <button v-if="!selected.is_active" class="button secondary" type="button" @click="activate"><Check :size="16" />设为当前</button>
            <button class="button primary" type="button" :disabled="saving" @click="save"><Save :size="16" />保存</button>
            <button class="icon-button danger" type="button" title="删除供应商" @click="remove"><Trash2 :size="17" /></button>
          </div>
        </div>

        <div v-if="testResult" :class="['connection-result', { success: testResult.ok }]">
          <FlaskConical :size="17" />
          <strong>{{ testResult.message }}</strong>
          <span>{{ testResult.latency_ms }} ms</span>
        </div>

        <form class="form-grid" @submit.prevent="save">
          <label class="field">
            <span>聊天补全来源</span>
            <select v-model="form.chat_completion_source" aria-label="聊天补全来源" @change="applyChatCompletionSource">
              <option v-for="source in chatCompletionSources" :key="source.id" :value="source.id">{{ source.label }}</option>
            </select>
          </label>
          <label class="field">
            <span>提示词后处理</span>
            <select v-model="form.prompt_post_processing" aria-label="提示词后处理">
              <option value="">不处理</option>
              <optgroup label="保留工具字段">
                <option value="merge_tools">合并相同角色连续发言（含工具）</option>
                <option value="semi_tools">半严格：强制角色交替（含工具）</option>
                <option value="strict_tools">严格：用户最先并强制交替（含工具）</option>
              </optgroup>
              <optgroup label="移除工具字段">
                <option value="merge">合并相同角色连续发言</option>
                <option value="semi">半严格：强制角色交替</option>
                <option value="strict">严格：用户最先并强制交替</option>
                <option value="single">合并为单一用户消息</option>
              </optgroup>
            </select>
          </label>
          <div class="field span-2 provider-kind-field">
            <span>API 协议</span>
            <div class="provider-kind-control" role="radiogroup" aria-label="API 协议">
              <button
                v-for="kind in providerKinds"
                :key="kind.id"
                type="button"
                role="radio"
                :aria-checked="form.kind === kind.id"
                :class="{ active: form.kind === kind.id }"
                @click="form.kind = kind.id"
              >
                <component :is="kind.icon" :size="16" />{{ kind.label }}
              </button>
            </div>
          </div>
          <label class="field"><span>显示名称</span><input v-model="form.name" required maxlength="120" /></label>
          <label class="field">
            <span>故障转移序号</span>
            <input
              v-model.number="form.priority"
              type="number"
              min="1"
              max="10000"
              step="1"
              title="当前组合预设选中的接口始终先尝试；其余已启用接口按此序号从小到大接力"
            />
          </label>
          <label class="field span-2"><span>Base URL</span><input v-model="form.base_url" :placeholder="baseUrlPlaceholder" /></label>
          <div class="field span-2">
            <span>模型名称 <small v-if="modelOptions.length">{{ modelOptions.length }} 个可选模型</small></span>
            <div class="model-picker-row">
              <input v-if="manualModel" v-model="form.model" aria-label="模型名称" placeholder="供应商提供的模型 ID" />
              <select v-else v-model="form.model" aria-label="模型名称">
                <option value="">请选择模型</option>
                <option v-if="form.model && !modelOptions.some((item) => item.id === form.model)" :value="form.model">{{ form.model }}（当前配置）</option>
                <option v-for="model in modelOptions" :key="model.id" :value="model.id">{{ model.name === model.id ? model.id : `${model.name} · ${model.id}` }}</option>
              </select>
              <button class="icon-button" type="button" title="拉取模型列表" :disabled="modelsLoading || !form.base_url" @click="loadModels">
                <RefreshCw :size="16" :class="{ spinning: modelsLoading }" />
              </button>
              <button class="icon-button" type="button" :title="manualModel ? '使用模型列表' : '手动填写模型名称'" :disabled="manualModel && !modelOptions.length" @click="manualModel = !manualModel">
                <PenLine :size="16" />
              </button>
            </div>
          </div>
          <label class="field span-2">
            <span>API Key <small v-if="selected.api_key_configured">已保存 {{ selected.api_key_masked }}</small></span>
            <div class="input-with-icon"><KeyRound :size="16" /><input v-model="form.api_key" type="password" autocomplete="new-password" placeholder="留空则保持原密钥" /></div>
          </label>
          <label class="toggle-field span-2">
            <input v-model="form.enabled" type="checkbox" />
            <span class="toggle-control" />
            <span><strong>启用供应商</strong><small>停用后保留配置，但不参与后续模型调用。</small></span>
          </label>
        </form>
      </template>

      <div v-else-if="!loading" class="empty-state"><ServerCog :size="28" /><strong>暂无供应商</strong></div>
    </section>
  </div>
</template>
