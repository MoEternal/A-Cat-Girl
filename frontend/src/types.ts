export type Role = 'system' | 'user' | 'assistant'
export type ProviderKind = 'openai_compatible' | 'anthropic' | 'google_gemini'
export type ChatCompletionSource = 'custom' | 'openai' | 'ai21' | 'aimlapi' | 'azure_openai' | 'chutes' | 'claude' | 'workers_ai' | 'cohere' | 'deepseek' | 'electronhub' | 'fireworks' | 'groq' | 'makersuite' | 'vertexai' | 'mistralai' | 'minimax' | 'moonshot' | 'nanogpt' | 'openrouter' | 'perplexity' | 'pollinations' | 'siliconflow' | 'xai' | 'zai'
export type PromptPostProcessing = '' | 'merge' | 'merge_tools' | 'semi' | 'semi_tools' | 'strict' | 'strict_tools' | 'single'

export interface AuthStatus {
  setup_required: boolean
  authenticated: boolean
  username: string
}

export interface Provider {
  id: string
  name: string
  kind: ProviderKind
  chat_completion_source: ChatCompletionSource
  prompt_post_processing: PromptPostProcessing
  base_url: string
  model: string
  api_key_configured: boolean
  api_key_masked: string
  priority: number
  enabled: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface PromptBlock {
  id: string
  template_id: string
  title: string
  role: Role
  content: string
  enabled: boolean
  stashed: boolean
  position: number
  identifier: string | null
  marker: boolean
  injection_position: number
  injection_depth: number
  injection_order: number
  created_at: string
  updated_at: string
}

export interface PromptTemplate {
  id: string
  name: string
  description: string
  is_active: boolean
  blocks: PromptBlock[]
  created_at: string
  updated_at: string
}

export interface Character {
  id: string
  name: string
  summary: string
  persona: string
  scenario: string
  first_message: string
  world_book_ids: string[]
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface UserPersona {
  id: string
  name: string
  description: string
  injection_position: 0 | 2 | 3 | 4 | 9
  injection_depth: number
  role: Role
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface PreviewMessage {
  block_id: string
  title: string
  role: Role
  content: string
  kind: 'template' | 'history' | 'persona' | 'plugin'
  plugin_id: string | null
  insertion_label: string
  content_visible: boolean
  marker: boolean
  identifier: string | null
  injection_position: number
  injection_depth: number
  injection_order: number
  token_count: number
}

export interface PromptPreview {
  template_id: string
  template_name: string
  character_id: string | null
  character_name: string | null
  user_persona_id: string | null
  user_persona_name: string | null
  messages: PreviewMessage[]
  total_tokens: number
  unresolved_variables: string[]
  supported_macros: Array<{ name: string; syntax: string; category: string }>
}

export interface Overview {
  counts: {
    presets: number
    world_books: number
    providers: number
    templates: number
    characters: number
    user_personas: number
  }
  active_preset: ConfigurationPreset | null
  active_provider: Provider | null
  active_template: PromptTemplate | null
  active_character: Character | null
  active_user_persona: UserPersona | null
  active_world_book_ids: string[]
}

export interface RuntimeLog {
  id: number
  created_at: string
  level: string
  source: string
  message: string
}

export type ImageQuality = 'auto' | 'low' | 'high'
export type ReasoningEffort = 'auto' | 'min' | 'low' | 'medium' | 'high' | 'max'

export interface ConfigurationPreset {
  id: string
  name: string
  description: string
  provider_id: string | null
  prompt_template_id: string | null
  character_id: string | null
  user_persona_id: string | null
  world_book_ids: string[]
  is_active: boolean
  max_context_unlocked: boolean
  context_length: number
  max_response_tokens: number
  candidate_count: number
  streaming: boolean
  temperature: number
  frequency_penalty: number
  presence_penalty: number
  top_p: number
  quote_wrapping: boolean
  continue_prefill: boolean
  squash_system_messages: boolean
  function_calling: boolean
  media_inlining: boolean
  image_quality: ImageQuality
  show_thoughts: boolean
  reasoning_effort: ReasoningEffort
  created_at: string
  updated_at: string
}

export interface WorldBookEntry {
  id: string
  world_book_id: string
  uid: number
  primary_keys: string[]
  secondary_keys: string[]
  comment: string
  content: string
  constant: boolean
  selective: boolean
  selective_logic: number
  enabled: boolean
  insertion_order: number
  position: number
  insertion_depth: number
  role: Role
  probability: number
  use_probability: boolean
  created_at: string
  updated_at: string
}

export interface WorldBook {
  id: string
  name: string
  description: string
  source_format: string
  scope: 'global' | 'character'
  character_id: string | null
  created_at: string
  updated_at: string
  entries: WorldBookEntry[]
}

export interface SillyTavernImportReport {
  preset_id: string | null
  preset_name: string | null
  prompt_template_id: string | null
  provider_id: string | null
  world_book_ids: string[]
  character_ids: string[]
  imported_characters: number
  imported_prompt_blocks: number
  imported_world_entries: number
  warnings: string[]
}

export interface PluginSettingDefinition {
  type: 'boolean' | 'integer' | 'number' | 'string'
  title?: string
  description?: string
  default?: unknown
  minimum?: number
  maximum?: number
  enum?: Array<string | number>
  enum_names?: string[]
  format?: string
}

export interface Plugin {
  id: string
  name: string
  version: string
  description: string
  entrypoint: string
  author: string
  min_app_version: string
  admin_ui?: string | null
  hide_metadata?: boolean
  permissions: string[]
  hooks: string[]
  settings_schema: {
    type: 'object'
    properties: Record<string, PluginSettingDefinition>
  }
  built_in: boolean
  enabled: boolean
  position: number
  loaded: boolean
  status: string
  settings: Record<string, unknown>
  secret_settings_configured?: Record<string, boolean>
  last_error: string
}

export interface PluginConversationStateSummary {
  conversation_id: string
  title: string
  external_id: string
  is_active: boolean
  updated_at: string
  message_count?: number
  memory_id?: string
  memory_name?: string
}

export interface PluginMemorySummary {
  id: string
  name: string
  created_at: string
  updated_at: string
  bound_count: number
}

export interface PluginConversationStateView {
  items: PluginConversationStateSummary[]
  memories?: PluginMemorySummary[]
  selected_id: string | null
  selected_memory_id?: string | null
  state: Record<string, unknown>
}

export interface RegexRule {
  id: string
  name: string
  enabled: boolean
  pattern: string
  replacement: string
  flags: string
}

export interface RegexFilterState {
  global_rules: RegexRule[]
  character_rules: Record<string, RegexRule[]>
}

export interface PluginStateResponse<T = Record<string, unknown>> {
  state: T
}

export interface OneBotConfig {
  id: number
  enabled: boolean
  connection_mode: 'reverse' | 'forward'
  reverse_ws_url: string
  forward_ws_url: string
  access_token_configured: boolean
  access_token_masked: string
  private_messages: boolean
  group_messages: boolean
  private_allowlist: string[]
  group_allowlist: string[]
  api_timeout_seconds: number
  created_at: string
  updated_at: string
}

export interface OneBotStatus {
  enabled: boolean
  connection_mode: 'reverse' | 'forward'
  connected: boolean
  connections: number
  self_ids: string[]
  connected_at: string | null
  last_event_at: string | null
  pending_actions: number
  failed_actions: number
  connection_error: string
}

export interface ConversationRecord {
  id: string
  channel: string
  external_id: string
  title: string
  is_active: boolean
  message_count: number
  total_tokens: number
  character_name: string | null
  last_message_preview: string
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  conversation_id: string
  position: number
  role: string
  content: string
  status: string
  source: string
  provider_id: string | null
  preset_id: string | null
  model: string
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  token_count: number
  speaker_name: string
  message_metadata: Record<string, unknown>
  created_at: string
}
