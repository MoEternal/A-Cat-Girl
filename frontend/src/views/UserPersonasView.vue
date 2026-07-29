<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Check, CircleAlert, Plus, Save, Trash2, Upload, UserRound, UserRoundCheck } from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import type { Role, UserPersona } from '../types'

const personas = ref<UserPersona[]>([])
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const selected = computed(() => personas.value.find((item) => item.id === selectedId.value) ?? null)
const form = reactive<{
  name: string
  description: string
  injection_position: 0 | 2 | 3 | 4 | 9
  injection_depth: number
  role: Role
}>({ name: '', description: '', injection_position: 0, injection_depth: 2, role: 'system' })
const positions = [
  { value: 0, label: '主提示中的用户人设插槽' },
  { value: 2, label: '作者注释顶部' },
  { value: 3, label: '作者注释底部' },
  { value: 4, label: '聊天中指定深度' },
  { value: 9, label: '不自动插入' },
] as const

function selectPersona(persona: UserPersona) {
  selectedId.value = persona.id
  Object.assign(form, {
    name: persona.name,
    description: persona.description,
    injection_position: persona.injection_position,
    injection_depth: persona.injection_depth,
    role: persona.role,
  })
  notice.value = ''
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    personas.value = await api<UserPersona[]>('/api/user-personas')
    const target = personas.value.find((item) => item.id === preferredId)
      ?? personas.value.find((item) => item.id === selectedId.value)
      ?? personas.value.find((item) => item.is_active)
      ?? personas.value[0]
    if (target) selectPersona(target)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createPersona() {
  try {
    const created = await api<UserPersona>('/api/user-personas', json('POST', {
      name: `新用户人设 ${personas.value.length + 1}`,
      description: '',
      injection_position: 0,
      injection_depth: 2,
      role: 'system',
    }))
    await load(created.id)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function save() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<UserPersona>(`/api/user-personas/${selected.value.id}`, json('PUT', form))
    personas.value[personas.value.findIndex((item) => item.id === updated.id)] = updated
    selectPersona(updated)
    notice.value = '用户人设已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function activate() {
  if (!selected.value) return
  const updated = await api<UserPersona>(`/api/user-personas/${selected.value.id}/activate`, json('POST'))
  personas.value = personas.value.map((item) => ({ ...item, is_active: item.id === updated.id }))
  selectPersona(updated)
  notice.value = '已切换为当前用户人设'
}

async function remove() {
  if (!selected.value || !window.confirm(`删除用户人设“${selected.value.name}”？`)) return
  await api(`/api/user-personas/${selected.value.id}`, json('DELETE'))
  selectedId.value = ''
  await load()
}

async function exportPersona() {
  if (!selected.value) return
  try {
    if (await exportJsonToFolder(
      `用户人设-${selected.value.name}`,
      exportEnvelope('catgirl_user_persona', selected.value),
    )) notice.value = '用户人设已导出'
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
        <div><span class="eyebrow">USER PERSONAS</span><strong>{{ personas.length }} 个用户人设</strong></div>
        <button class="icon-button primary-icon" type="button" title="添加用户人设" @click="createPersona"><Plus :size="17" /></button>
      </div>
      <div class="item-list">
        <button v-for="persona in personas" :key="persona.id" type="button" :class="['item-row', { selected: persona.id === selectedId }]" @click="selectPersona(persona)">
          <span class="avatar-letter user-persona-letter">{{ persona.name.slice(0, 1) }}</span>
          <span><strong>{{ persona.name }}</strong><small>用户人设</small></span>
          <i v-if="persona.is_active" class="active-dot" title="当前使用" />
        </button>
      </div>
    </aside>

    <section class="editor-pane">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selected">
        <div class="editor-heading">
          <div>
            <div class="heading-with-status"><h2>{{ selected.name }}</h2><span v-if="selected.is_active" class="active-badge">当前使用</span></div>
          </div>
          <div class="action-row">
            <button class="icon-button" type="button" title="导出用户人设" @click="exportPersona"><Upload :size="17" /></button>
            <button v-if="!selected.is_active" class="button secondary" type="button" @click="activate"><UserRoundCheck :size="16" />设为当前</button>
            <button class="button primary" type="button" :disabled="saving" @click="save"><Save :size="16" />保存</button>
            <button class="icon-button danger" type="button" title="删除用户人设" @click="remove"><Trash2 :size="17" /></button>
          </div>
        </div>

        <form class="form-grid character-form" @submit.prevent="save">
          <label class="field span-2"><span>用户名称 <small>用于 <code v-text="'{{user}}'" /></small></span><input v-model="form.name" required maxlength="120" /></label>
          <label class="field span-2"><span>用户人设描述 <small>用于 <code v-text="'{{persona}}'" /></small></span><textarea v-model="form.description" rows="12" /></label>
          <label class="field"><span>描述插入位置</span><select v-model.number="form.injection_position"><option v-for="position in positions" :key="position.value" :value="position.value">{{ position.label }}</option></select></label>
          <label v-if="form.injection_position === 4" class="field"><span>聊天插入深度</span><input v-model.number="form.injection_depth" type="number" min="0" max="1000" /></label>
          <label v-if="form.injection_position === 4" class="field span-2"><span>消息角色</span><select v-model="form.role"><option v-for="role in (['system', 'user', 'assistant'] as Role[])" :key="role" :value="role">{{ role }}</option></select></label>
        </form>
      </template>

      <div v-else-if="!loading" class="empty-state"><UserRound :size="28" /><strong>暂无用户人设</strong></div>
    </section>
  </div>
</template>
