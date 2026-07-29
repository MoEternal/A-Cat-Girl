import { api, json } from './api'
import type { SillyTavernImportReport } from './types'

type NamedJson = { name: string; data: Record<string, unknown> }

function fileStem(name: string): string {
  return name.replace(/\.[^/.]+$/, '')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isCharacterCard(data: Record<string, unknown>): boolean {
  const nested = isRecord(data.data) ? data.data : null
  const candidate = nested ?? data
  const spec = typeof data.spec === 'string' ? data.spec.toLowerCase() : ''
  const characterFields = [
    'description',
    'personality',
    'scenario',
    'first_mes',
    'first_message',
    'mes_example',
    'system_prompt',
    'post_history_instructions',
  ]
  return spec.startsWith('chara_card_')
    || (typeof candidate.name === 'string' && characterFields.some((key) => key in candidate))
}

function bytesToString(bytes: Uint8Array, encoding: 'utf-8' | 'latin1' = 'utf-8'): string {
  return new TextDecoder(encoding).decode(bytes)
}

function decodeBase64Utf8(value: string): string {
  const binary = atob(value.replace(/\s/g, ''))
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
  return bytesToString(bytes)
}

function parseCardMetadata(value: string): Record<string, unknown> {
  const trimmed = value.trim()
  const candidates = [trimmed]
  try {
    candidates.push(decodeBase64Utf8(trimmed))
  } catch {
    // Some V3 writers store JSON directly instead of base64.
  }
  for (const candidate of candidates) {
    try {
      const parsed: unknown = JSON.parse(candidate)
      if (isRecord(parsed)) return parsed
    } catch {
      // Try the next supported encoding.
    }
  }
  throw new Error('PNG 中的角色卡元数据不是有效 JSON 或 base64 JSON')
}

function sliceBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('当前浏览器不支持压缩 PNG 角色卡元数据')
  }
  const stream = new Blob([sliceBuffer(bytes)])
    .stream()
    .pipeThrough(new DecompressionStream('deflate'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

function findNull(bytes: Uint8Array, start: number): number {
  for (let index = start; index < bytes.length; index += 1) {
    if (bytes[index] === 0) return index
  }
  return -1
}

async function textChunkValue(type: string, chunk: Uint8Array): Promise<{ keyword: string; value: string } | null> {
  const keywordEnd = findNull(chunk, 0)
  if (keywordEnd < 1) return null
  const keyword = bytesToString(chunk.subarray(0, keywordEnd), 'latin1')

  if (type === 'tEXt') {
    return { keyword, value: bytesToString(chunk.subarray(keywordEnd + 1), 'latin1') }
  }
  if (type === 'zTXt') {
    if (chunk[keywordEnd + 1] !== 0) return null
    const value = bytesToString(await inflate(chunk.subarray(keywordEnd + 2)))
    return { keyword, value }
  }
  if (type === 'iTXt') {
    const compressionFlag = chunk[keywordEnd + 1]
    const compressionMethod = chunk[keywordEnd + 2]
    const languageEnd = findNull(chunk, keywordEnd + 3)
    if (languageEnd < 0) return null
    const translatedEnd = findNull(chunk, languageEnd + 1)
    if (translatedEnd < 0) return null
    const payload = chunk.subarray(translatedEnd + 1)
    if (compressionFlag === 1 && compressionMethod !== 0) return null
    const value = bytesToString(compressionFlag === 1 ? await inflate(payload) : payload)
    return { keyword, value }
  }
  return null
}

async function readPngCharacterCard(file: File): Promise<Record<string, unknown>> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  const signature = [137, 80, 78, 71, 13, 10, 26, 10]
  if (bytes.length < 12 || !signature.every((value, index) => bytes[index] === value)) {
    throw new Error(`${file.name} 不是有效 PNG`)
  }

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const metadata = new Map<string, string>()
  let offset = 8
  while (offset + 12 <= bytes.length) {
    const length = view.getUint32(offset)
    const dataStart = offset + 8
    const dataEnd = dataStart + length
    if (dataEnd + 4 > bytes.length) throw new Error(`${file.name} 的 PNG 数据不完整`)
    const type = bytesToString(bytes.subarray(offset + 4, offset + 8), 'latin1')
    if (type === 'tEXt' || type === 'zTXt' || type === 'iTXt') {
      const text = await textChunkValue(type, bytes.subarray(dataStart, dataEnd))
      if (text && (text.keyword === 'chara' || text.keyword === 'ccv3')) {
        metadata.set(text.keyword, text.value)
      }
    }
    offset = dataEnd + 4
    if (type === 'IEND') break
  }

  const encoded = metadata.get('ccv3') ?? metadata.get('chara')
  if (!encoded) throw new Error(`${file.name} 不含 chara 或 ccv3 角色卡元数据`)
  return { ...parseCardMetadata(encoded), __catgirl_source_format: 'png' }
}

async function readImportFile(file: File): Promise<Record<string, unknown>> {
  if (file.type === 'image/png' || file.name.toLowerCase().endsWith('.png')) {
    return readPngCharacterCard(file)
  }
  try {
    const parsed: unknown = JSON.parse(await file.text())
    if (isRecord(parsed)) return parsed
  } catch {
    // Report one consistent file-level error below.
  }
  throw new Error(`${file.name} 不是有效 JSON`)
}

export async function importSillyTavernFiles(
  files: File[],
  options: { activate?: boolean; characterId?: string | null } = {},
): Promise<SillyTavernImportReport> {
  let preset: NamedJson | null = null
  const characters: NamedJson[] = []
  const worldBooks: NamedJson[] = []

  for (const file of files) {
    const data = await readImportFile(file)
    const nestedData = isRecord(data.data) ? data.data : null
    const isPreset = Boolean(data.prompts || data.prompt_order || data.chat_completion_source)
    const isWorldBook = Boolean(data.entries || data.character_book || nestedData?.character_book)
    if (isCharacterCard(data)) {
      characters.push({ name: fileStem(file.name), data })
    } else if (isPreset) {
      if (preset) throw new Error('一次只能导入一个预设 JSON')
      preset = { name: fileStem(file.name), data }
    } else if (isWorldBook) {
      worldBooks.push({ name: fileStem(file.name), data })
    } else {
      throw new Error(`${file.name} 不是可识别的预设、角色卡或世界书`)
    }
  }

  return api<SillyTavernImportReport>('/api/import/sillytavern', json('POST', {
    preset,
    characters,
    world_books: worldBooks,
    character_id: options.characterId ?? null,
    activate: options.activate ?? false,
  }))
}
