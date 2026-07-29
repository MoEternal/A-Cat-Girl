<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { BookOpen, Check, CircleAlert, Download, Plus, Save, Trash2, Upload, UserRound, UserRoundCheck } from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import { importSillyTavernFiles } from '../sillytavernImport'
import type { Character, WorldBook } from '../types'

const characters = ref<Character[]>([])
const worldBooks = ref<WorldBook[]>([])
const selectedId = ref('')
const loading = ref(true)
const saving = ref(false)
const importing = ref(false)
const error = ref('')
const notice = ref('')
const importInput = ref<HTMLInputElement | null>(null)
const selected = computed(() => characters.value.find((item) => item.id === selectedId.value) ?? null)
const form = reactive({ name: '', summary: '', persona: '', scenario: '', first_message: '', world_book_ids: [] as string[] })

function selectCharacter(character: Character) {
  selectedId.value = character.id
  Object.assign(form, {
    name: character.name,
    summary: character.summary,
    persona: character.persona,
    scenario: character.scenario,
    first_message: character.first_message,
    world_book_ids: [...character.world_book_ids],
  })
  notice.value = ''
}

async function load(preferredId?: string) {
  loading.value = true
  error.value = ''
  try {
    const [characterData, worldBookData] = await Promise.all([
      api<Character[]>('/api/characters'),
      api<WorldBook[]>('/api/world-books'),
    ])
    characters.value = characterData
    worldBooks.value = worldBookData
    const target = characters.value.find((item) => item.id === preferredId)
      ?? characters.value.find((item) => item.id === selectedId.value)
      ?? characters.value.find((item) => item.is_active)
      ?? characters.value[0]
    if (target) selectCharacter(target)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createCharacter() {
  try {
    const created = await api<Character>('/api/characters', json('POST', {
      name: `新人设 ${characters.value.length + 1}`,
      summary: '',
      persona: '',
      scenario: '',
      first_message: '',
      world_book_ids: [],
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
    const updated = await api<Character>(`/api/characters/${selected.value.id}`, json('PUT', form))
    characters.value[characters.value.findIndex((item) => item.id === updated.id)] = updated
    selectCharacter(updated)
    notice.value = '人设已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function activate() {
  if (!selected.value) return
  const updated = await api<Character>(`/api/characters/${selected.value.id}/activate`, json('POST'))
  characters.value = characters.value.map((item) => ({ ...item, is_active: item.id === updated.id }))
  selectCharacter(updated)
  notice.value = '已切换为当前人设'
}

async function remove() {
  if (!selected.value || !window.confirm(`删除人设“${selected.value.name}”？`)) return
  await api(`/api/characters/${selected.value.id}`, json('DELETE'))
  selectedId.value = ''
  await load()
}

async function exportCharacter() {
  if (!selected.value) return
  try {
    const linkedBooks = selected.value.world_book_ids
      .map((id) => worldBooks.value.find((book) => book.id === id))
      .filter(Boolean)
    if (await exportJsonToFolder(
      `角色卡-${selected.value.name}`,
      exportEnvelope('catgirl_character_bundle', { character: selected.value, world_books: linkedBooks }),
    )) notice.value = '角色卡已导出'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

async function importCharacterCards(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  target.value = ''
  if (!files.length) return
  importing.value = true
  error.value = ''
  try {
    const report = await importSillyTavernFiles(files)
    await load(report.character_ids[0])
    const summary = [
      report.imported_characters ? `${report.imported_characters} 张角色卡` : '',
      report.world_book_ids.length
        ? `${report.world_book_ids.length} 本内嵌世界书 / ${report.imported_world_entries} 个条目`
        : '',
    ].filter(Boolean).join('、')
    notice.value = `已导入${summary ? `：${summary}` : ''}${report.warnings.length ? `；提示：${report.warnings.join('；')}` : ''}`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '角色卡导入失败'
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="management-layout">
    <input
      ref="importInput"
      class="visually-hidden"
      type="file"
      accept=".json,.png,application/json,image/png"
      multiple
      @change="importCharacterCards"
    />
    <aside class="item-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">CHARACTERS</span><strong>{{ characters.length }} 个人设</strong></div>
        <div class="editor-actions">
          <button
            class="icon-button"
            type="button"
            aria-label="导入角色卡"
            :title="importing ? '正在导入角色卡' : '导入角色卡（JSON / PNG）'"
            :disabled="importing"
            @click="importInput?.click()"
          ><Download :size="17" /></button>
          <button class="icon-button primary-icon" type="button" title="添加人设" @click="createCharacter"><Plus :size="17" /></button>
        </div>
      </div>
      <div class="item-list">
        <button
          v-for="character in characters"
          :key="character.id"
          type="button"
          :class="['item-row', { selected: character.id === selectedId }]"
          @click="selectCharacter(character)"
        >
          <span class="avatar-letter">{{ character.name.slice(0, 1) }}</span>
          <span><strong>{{ character.name }}</strong><small>{{ character.summary || '暂无简介' }}</small></span>
          <i v-if="character.is_active" class="active-dot" title="当前使用" />
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
              <h2>{{ selected.name }}</h2>
              <span v-if="selected.is_active" class="active-badge">当前使用</span>
            </div>
            <p>{{ selected.summary || '未填写简介' }}</p>
          </div>
          <div class="action-row">
            <button class="icon-button" type="button" title="导出角色卡" @click="exportCharacter"><Upload :size="17" /></button>
            <button v-if="!selected.is_active" class="button secondary" type="button" @click="activate"><UserRoundCheck :size="16" />设为当前</button>
            <button class="button primary" type="button" :disabled="saving" @click="save"><Save :size="16" />保存</button>
            <button class="icon-button danger" type="button" title="删除人设" @click="remove"><Trash2 :size="17" /></button>
          </div>
        </div>

        <form class="form-grid character-form" @submit.prevent="save">
          <label class="field"><span>人设名称</span><input v-model="form.name" required maxlength="120" /></label>
          <label class="field"><span>简短描述</span><input v-model="form.summary" maxlength="240" /></label>
          <label class="field span-2"><span>角色设定</span><textarea v-model="form.persona" rows="8" /></label>
          <label class="field span-2"><span>场景</span><textarea v-model="form.scenario" rows="5" /></label>
          <label class="field span-2"><span>开场消息</span><textarea v-model="form.first_message" rows="4" /></label>
          <div class="field span-2">
            <span><BookOpen :size="13" />链接到世界书</span>
            <div class="world-book-checks character-world-book-checks">
              <label v-for="book in worldBooks" :key="book.id">
                <input v-model="form.world_book_ids" type="checkbox" :value="book.id" />
                <span>{{ book.name }}</span>
              </label>
              <em v-if="!worldBooks.length">暂无世界书</em>
            </div>
          </div>
        </form>
      </template>

      <div v-else-if="!loading" class="empty-state"><UserRound :size="28" /><strong>暂无人设</strong></div>
    </section>
  </div>
</template>
