<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  BookOpen,
  Check,
  CircleAlert,
  FileDown,
  KeyRound,
  Plus,
  Save,
  Trash2,
  Upload,
} from '@lucide/vue'

import { api, json } from '../api'
import { exportEnvelope, exportJsonToFolder } from '../exportJson'
import { importSillyTavernFiles } from '../sillytavernImport'
import type { Character, Role, WorldBook, WorldBookEntry } from '../types'

type EntryForm = Omit<
  WorldBookEntry,
  'id' | 'world_book_id' | 'uid' | 'primary_keys' | 'secondary_keys' | 'created_at' | 'updated_at'
> & { primary_keys: string; secondary_keys: string }

const books = ref<WorldBook[]>([])
const characters = ref<Character[]>([])
const selectedBookId = ref('')
const selectedEntryId = ref('')
const loading = ref(true)
const saving = ref(false)
const importing = ref(false)
const error = ref('')
const notice = ref('')
const importInput = ref<HTMLInputElement | null>(null)
const bookForm = reactive({ name: '', description: '', scope: 'character' as 'global' | 'character', character_id: null as string | null })
const entryForm = reactive<EntryForm>({
  primary_keys: '',
  secondary_keys: '',
  comment: '',
  content: '',
  constant: false,
  selective: true,
  selective_logic: 0,
  enabled: true,
  insertion_order: 100,
  position: 0,
  insertion_depth: 4,
  role: 'system',
  probability: 100,
  use_probability: true,
})

const positions = [
  { value: 0, label: '角色定义之前' },
  { value: 1, label: '角色定义之后' },
  { value: 2, label: '作者注释顶部' },
  { value: 3, label: '作者注释底部' },
  { value: 4, label: '聊天中指定深度' },
  { value: 5, label: '示例消息顶部' },
  { value: 6, label: '示例消息底部' },
  { value: 7, label: 'Outlet' },
]
const selectiveLogics = [
  { value: 0, label: '任一辅助关键词' },
  { value: 1, label: '全部辅助关键词' },
  { value: 2, label: '不含任一辅助关键词' },
  { value: 3, label: '不含全部辅助关键词' },
]
const selectedBook = computed(() => books.value.find((item) => item.id === selectedBookId.value) ?? null)
const entries = computed(() => [...(selectedBook.value?.entries ?? [])]
  .sort((a, b) => a.insertion_order - b.insertion_order || a.uid - b.uid))
const selectedEntry = computed(() => entries.value.find((item) => item.id === selectedEntryId.value) ?? null)

function parseKeys(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
}

function selectEntry(entry: WorldBookEntry | null) {
  selectedEntryId.value = entry?.id ?? ''
  Object.assign(entryForm, entry ? {
    primary_keys: entry.primary_keys.join(', '),
    secondary_keys: entry.secondary_keys.join(', '),
    comment: entry.comment,
    content: entry.content,
    constant: entry.constant,
    selective: entry.selective,
    selective_logic: entry.selective_logic,
    enabled: entry.enabled,
    insertion_order: entry.insertion_order,
    position: entry.position,
    insertion_depth: entry.insertion_depth,
    role: entry.role,
    probability: entry.probability,
    use_probability: entry.use_probability,
  } : {
    primary_keys: '', secondary_keys: '', comment: '', content: '', constant: false,
    selective: true, selective_logic: 0, enabled: true, insertion_order: 100,
    position: 0, insertion_depth: 4, role: 'system', probability: 100, use_probability: true,
  })
}

function selectBook(book: WorldBook) {
  selectedBookId.value = book.id
  Object.assign(bookForm, { name: book.name, description: book.description, scope: book.scope, character_id: book.character_id })
  const preferred = book.entries.find((entry) => entry.id === selectedEntryId.value) ?? book.entries[0] ?? null
  selectEntry(preferred)
  error.value = ''
  notice.value = ''
}

async function load(preferredId?: string) {
  loading.value = true
  try {
    const [bookData, characterData] = await Promise.all([
      api<WorldBook[]>('/api/world-books'),
      api<Character[]>('/api/characters'),
    ])
    books.value = bookData
    characters.value = characterData
    const target = books.value.find((item) => item.id === preferredId)
      ?? books.value.find((item) => item.id === selectedBookId.value)
      ?? books.value[0]
    if (target) selectBook(target)
    else {
      selectedBookId.value = ''
      selectEntry(null)
    }
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function createBook() {
  error.value = ''
  try {
    const created = await api<WorldBook>('/api/world-books', json('POST', {
      name: `新世界书 ${books.value.length + 1}`,
      description: '',
      scope: 'character',
      character_id: characters.value.find((item) => item.is_active)?.id ?? characters.value[0]?.id ?? null,
    }))
    await load(created.id)
    notice.value = '世界书已创建'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function saveBook() {
  if (!selectedBook.value) return
  saving.value = true
  try {
    const updated = await api<WorldBook>(
      `/api/world-books/${selectedBook.value.id}`,
      json('PUT', bookForm),
    )
    books.value[books.value.findIndex((item) => item.id === updated.id)] = updated
    selectBook(updated)
    notice.value = '世界书信息已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function removeBook() {
  if (!selectedBook.value || !window.confirm(`删除世界书“${selectedBook.value.name}”及其全部条目？`)) return
  await api(`/api/world-books/${selectedBook.value.id}`, json('DELETE'))
  selectedBookId.value = ''
  await load()
}

async function exportBook() {
  if (!selectedBook.value) return
  try {
    if (await exportJsonToFolder(
      `世界书-${selectedBook.value.name}`,
      exportEnvelope('catgirl_world_book', selectedBook.value),
    )) notice.value = '世界书已导出'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导出失败'
  }
}

async function addEntry() {
  if (!selectedBook.value) return
  try {
    const created = await api<WorldBookEntry>(
      `/api/world-books/${selectedBook.value.id}/entries`,
      json('POST', { comment: `新条目 ${entries.value.length + 1}` }),
    )
    selectedBook.value.entries.push(created)
    selectEntry(created)
    notice.value = '条目已创建'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '创建失败'
  }
}

async function saveEntry() {
  if (!selectedEntry.value) return
  saving.value = true
  error.value = ''
  try {
    const updated = await api<WorldBookEntry>(
      `/api/world-book-entries/${selectedEntry.value.id}`,
      json('PUT', {
        ...entryForm,
        primary_keys: parseKeys(entryForm.primary_keys),
        secondary_keys: parseKeys(entryForm.secondary_keys),
      }),
    )
    if (selectedBook.value) {
      const index = selectedBook.value.entries.findIndex((item) => item.id === updated.id)
      selectedBook.value.entries[index] = updated
    }
    selectEntry(updated)
    notice.value = '世界书条目已保存'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function toggleEntry(entry: WorldBookEntry) {
  const updated = await api<WorldBookEntry>(
    `/api/world-book-entries/${entry.id}`,
    json('PUT', { enabled: !entry.enabled }),
  )
  if (selectedBook.value) {
    const index = selectedBook.value.entries.findIndex((item) => item.id === updated.id)
    selectedBook.value.entries[index] = updated
  }
  if (selectedEntryId.value === updated.id) selectEntry(updated)
}

async function removeEntry() {
  if (!selectedEntry.value || !window.confirm(`删除条目“${selectedEntry.value.comment || `#${selectedEntry.value.uid}`}”？`)) return
  const id = selectedEntry.value.id
  await api(`/api/world-book-entries/${id}`, json('DELETE'))
  if (selectedBook.value) selectedBook.value.entries = selectedBook.value.entries.filter((item) => item.id !== id)
  selectEntry(entries.value[0] ?? null)
}

async function importBooks(event: Event) {
  const target = event.target as HTMLInputElement
  const files = Array.from(target.files ?? [])
  target.value = ''
  if (!files.length) return
  importing.value = true
  error.value = ''
  try {
    const report = await importSillyTavernFiles(files, {
      characterId: bookForm.character_id ?? characters.value.find((item) => item.is_active)?.id ?? null,
    })
    await load(report.world_book_ids[0])
    notice.value = `已导入 ${report.world_book_ids.length} 本世界书、${report.imported_world_entries} 个条目${report.warnings.length ? `；${report.warnings.join('；')}` : ''}`
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '导入失败'
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="management-layout world-book-layout">
    <aside class="item-rail">
      <div class="rail-heading">
        <div><span class="eyebrow">WORLD BOOKS</span><strong>{{ books.length }} 本世界书</strong></div>
        <button class="icon-button primary-icon" type="button" title="新建世界书" @click="createBook"><Plus :size="17" /></button>
      </div>
      <div class="world-book-import">
        <input ref="importInput" class="visually-hidden" type="file" accept=".json,application/json" multiple @change="importBooks" />
        <button class="button secondary" type="button" :disabled="importing" @click="importInput?.click()"><FileDown :size="16" />{{ importing ? '导入中' : '导入世界书' }}</button>
      </div>
      <div class="item-list">
        <button v-for="book in books" :key="book.id" type="button" :class="['item-row', { selected: book.id === selectedBookId }]" @click="selectBook(book)">
          <span class="item-symbol world-book-symbol"><BookOpen :size="18" /></span>
          <span><strong>{{ book.name }}</strong><small>{{ book.scope === 'global' ? '全局' : '角色' }} · {{ book.entries.length }} 个条目</small></span>
        </button>
      </div>
    </aside>

    <section class="world-book-main">
      <div v-if="error" class="notice error-notice"><CircleAlert :size="18" />{{ error }}</div>
      <div v-if="notice" class="notice success-notice"><Check :size="18" />{{ notice }}</div>

      <template v-if="selectedBook">
        <div class="editor-heading world-book-heading">
          <div>
            <div class="heading-with-status"><input v-model="bookForm.name" class="title-input" aria-label="世界书名称" maxlength="120" /></div>
            <input v-model="bookForm.description" class="description-input" aria-label="世界书描述" placeholder="世界书描述" />
          </div>
          <div class="action-row">
            <button class="icon-button" type="button" title="导出世界书" @click="exportBook"><Upload :size="17" /></button>
            <button class="button secondary" type="button" :disabled="saving" @click="saveBook"><Save :size="16" />保存世界书</button>
            <button class="icon-button danger" type="button" title="删除世界书" @click="removeBook"><Trash2 :size="17" /></button>
          </div>
        </div>

        <div class="world-book-scope-bar">
          <label class="field"><span>作用范围</span><select v-model="bookForm.scope"><option value="character">角色</option><option value="global">全局</option></select></label>
          <label v-if="bookForm.scope === 'character'" class="field"><span>关联角色</span><select v-model="bookForm.character_id"><option :value="null">未关联</option><option v-for="character in characters" :key="character.id" :value="character.id">{{ character.name }}</option></select></label>
        </div>

        <div class="world-book-columns">
          <section class="world-entry-list">
            <div class="panel-heading"><div><span class="eyebrow">ENTRIES</span><h3>条目</h3></div><button class="icon-button" type="button" title="添加条目" @click="addEntry"><Plus :size="17" /></button></div>
            <div class="block-list">
              <article v-for="entry in entries" :key="entry.id" :class="['world-entry-row', { selected: entry.id === selectedEntryId, disabled: !entry.enabled }]" @click="selectEntry(entry)">
                <span class="entry-uid">#{{ entry.uid }}</span>
                <div><strong>{{ entry.comment || entry.primary_keys.join(', ') || '无标题条目' }}</strong><small>{{ positions[entry.position]?.label }}<template v-if="entry.position === 4"> · 深度 {{ entry.insertion_depth }}</template></small></div>
                <button :class="['mini-toggle', { on: entry.enabled }]" type="button" :title="entry.enabled ? '停用' : '启用'" @click.stop="toggleEntry(entry)"><i /></button>
              </article>
            </div>
          </section>

          <section class="world-entry-editor">
            <template v-if="selectedEntry">
              <div class="panel-heading"><div><span class="eyebrow">ENTRY #{{ selectedEntry.uid }}</span><h3>条目编辑器</h3></div><button class="icon-button danger" type="button" title="删除条目" @click="removeEntry"><Trash2 :size="16" /></button></div>
              <div class="form-grid">
                <label class="field"><span>条目名称 / 备注</span><input v-model="entryForm.comment" maxlength="240" /></label>
                <label class="field"><span>插入位置</span><select v-model.number="entryForm.position"><option v-for="position in positions" :key="position.value" :value="position.value">{{ position.label }}</option></select></label>
                <label class="field"><span><KeyRound :size="13" />主关键词 <small>逗号或换行分隔</small></span><textarea v-model="entryForm.primary_keys" rows="2" /></label>
                <label class="field"><span><KeyRound :size="13" />辅助关键词 <small>选择性触发时使用</small></span><textarea v-model="entryForm.secondary_keys" rows="2" /></label>
              </div>
              <label class="field entry-content"><span>插入内容</span><textarea v-model="entryForm.content" rows="10" /></label>

              <div class="entry-toggle-grid">
                <label class="setting-toggle"><input v-model="entryForm.constant" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>常驻条目</strong><small>无需关键词，始终尝试插入。</small></span></label>
                <label class="setting-toggle"><input v-model="entryForm.selective" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>选择性触发</strong><small>主关键词命中后再检查辅助关键词。</small></span></label>
                <label class="setting-toggle"><input v-model="entryForm.use_probability" type="checkbox" /><span class="check-control"><Check :size="13" /></span><span><strong>使用概率</strong><small>触发后按设定概率决定是否插入。</small></span></label>
              </div>

              <div class="form-grid entry-settings-grid">
                <label v-if="entryForm.selective" class="field"><span>辅助关键词逻辑</span><select v-model.number="entryForm.selective_logic"><option v-for="logic in selectiveLogics" :key="logic.value" :value="logic.value">{{ logic.label }}</option></select></label>
                <label class="field"><span>插入顺序</span><input v-model.number="entryForm.insertion_order" type="number" min="-100000" max="100000" /></label>
                <label v-if="entryForm.use_probability" class="field"><span>触发概率（%）</span><input v-model.number="entryForm.probability" type="number" min="0" max="100" /></label>
                <template v-if="entryForm.position === 4">
                  <label class="field"><span>聊天插入深度</span><input v-model.number="entryForm.insertion_depth" type="number" min="0" max="1000" /></label>
                  <label class="field"><span>消息角色</span><select v-model="entryForm.role"><option v-for="role in (['system', 'user', 'assistant'] as Role[])" :key="role" :value="role">{{ role }}</option></select></label>
                </template>
              </div>

              <div class="editor-actions world-entry-actions">
                <span class="entry-status">{{ entryForm.constant ? '常驻' : '关键词触发' }} · 顺序 {{ entryForm.insertion_order }}</span>
                <button class="button primary" type="button" :disabled="saving" @click="saveEntry"><Save :size="16" />保存条目</button>
              </div>
            </template>
            <div v-else class="empty-state"><BookOpen :size="26" /><strong>暂无世界书条目</strong></div>
          </section>
        </div>
      </template>

      <div v-else-if="!loading" class="empty-state"><BookOpen :size="28" /><strong>暂无世界书</strong></div>
    </section>
  </div>
</template>
