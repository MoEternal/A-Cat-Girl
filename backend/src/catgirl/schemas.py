from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Role = Literal["system", "user", "assistant"]
ProviderKind = Literal["openai_compatible", "anthropic", "google_gemini"]
ChatCompletionSource = Literal[
    "custom",
    "openai",
    "ai21",
    "aimlapi",
    "azure_openai",
    "chutes",
    "claude",
    "workers_ai",
    "cohere",
    "deepseek",
    "electronhub",
    "fireworks",
    "groq",
    "makersuite",
    "vertexai",
    "mistralai",
    "minimax",
    "moonshot",
    "nanogpt",
    "openrouter",
    "perplexity",
    "pollinations",
    "siliconflow",
    "xai",
    "zai",
]
PromptPostProcessing = Literal[
    "", "merge", "merge_tools", "semi", "semi_tools", "strict", "strict_tools", "single"
]


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: ProviderKind = "openai_compatible"
    chat_completion_source: ChatCompletionSource = "custom"
    prompt_post_processing: PromptPostProcessing = ""
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=160)
    api_key: str = Field(default="", max_length=4000)
    priority: int | None = Field(default=None, ge=1, le=10000)
    enabled: bool = True


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: ProviderKind | None = None
    chat_completion_source: ChatCompletionSource | None = None
    prompt_post_processing: PromptPostProcessing | None = None
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=160)
    api_key: str | None = Field(default=None, max_length=4000)
    priority: int | None = Field(default=None, ge=1, le=10000)
    enabled: bool | None = None


class ProviderOut(BaseModel):
    id: str
    name: str
    kind: str
    chat_completion_source: str
    prompt_post_processing: str
    base_url: str
    model: str
    api_key_configured: bool
    api_key_masked: str
    priority: int
    enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProviderTestOut(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: int
    message: str


class ProviderModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ProviderKind
    chat_completion_source: ChatCompletionSource = "custom"
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)


class ProviderModelOut(BaseModel):
    id: str
    name: str


class PromptBlockCreate(BaseModel):
    title: str = Field(default="新提示词块", min_length=1, max_length=120)
    role: Role = "system"
    content: str = ""
    enabled: bool = True
    stashed: bool = False
    identifier: str | None = Field(default=None, max_length=160)
    marker: bool = False
    injection_position: int = Field(default=0, ge=0, le=1)
    injection_depth: int = Field(default=4, ge=0, le=1000)
    injection_order: int = Field(default=100, ge=-100000, le=100000)


class PromptBlockUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    content: str | None = None
    enabled: bool | None = None
    stashed: bool | None = None
    identifier: str | None = Field(default=None, max_length=160)
    marker: bool | None = None
    injection_position: int | None = Field(default=None, ge=0, le=1)
    injection_depth: int | None = Field(default=None, ge=0, le=1000)
    injection_order: int | None = Field(default=None, ge=-100000, le=100000)


class PromptBlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    template_id: str
    title: str
    role: Role
    content: str
    enabled: bool
    stashed: bool
    position: int
    identifier: str | None
    marker: bool
    injection_position: int
    injection_depth: int
    injection_order: int
    created_at: datetime
    updated_at: datetime


class PromptTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""


class PromptTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class PromptTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    blocks: list[PromptBlockOut]


class PromptOrderUpdate(BaseModel):
    block_ids: list[str] = Field(min_length=1)


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=240)
    persona: str = ""
    scenario: str = ""
    first_message: str = ""
    world_book_ids: list[str] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    summary: str | None = Field(default=None, max_length=240)
    persona: str | None = None
    scenario: str | None = None
    first_message: str | None = None
    world_book_ids: list[str] | None = None


class CharacterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    summary: str
    persona: str
    scenario: str
    first_message: str
    world_book_ids: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


PersonaPosition = Literal[0, 2, 3, 4, 9]


class UserPersonaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    injection_position: PersonaPosition = 0
    injection_depth: int = Field(default=2, ge=0, le=1000)
    role: Role = "system"


class UserPersonaUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    injection_position: PersonaPosition | None = None
    injection_depth: int | None = Field(default=None, ge=0, le=1000)
    role: Role | None = None


class UserPersonaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    injection_position: PersonaPosition
    injection_depth: int
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PreviewMessage(BaseModel):
    block_id: str
    title: str
    role: Role
    content: str
    kind: Literal["template", "history", "persona", "plugin"] = "template"
    plugin_id: str | None = None
    insertion_label: str = ""
    content_visible: bool = True
    marker: bool = False
    identifier: str | None = None
    injection_position: int = 0
    injection_depth: int = 4
    injection_order: int = 100
    token_count: int = 0


class PromptPreviewOut(BaseModel):
    template_id: str
    template_name: str
    character_id: str | None
    character_name: str | None
    user_persona_id: str | None = None
    user_persona_name: str | None = None
    messages: list[PreviewMessage]
    total_tokens: int = 0
    unresolved_variables: list[str]
    supported_macros: list[dict[str, str]] = Field(default_factory=list)


ImageQuality = Literal["auto", "low", "high"]
ReasoningEffort = Literal["auto", "min", "low", "medium", "high", "max"]


class ConfigurationPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    provider_id: str | None = None
    prompt_template_id: str | None = None
    character_id: str | None = None
    user_persona_id: str | None = None
    world_book_ids: list[str] = Field(default_factory=list)
    max_context_unlocked: bool = False
    context_length: int = Field(default=128000, ge=512, le=2_000_000)
    max_response_tokens: int = Field(default=2048, ge=1, le=1_000_000)
    candidate_count: int = Field(default=1, ge=1, le=16)
    streaming: bool = True
    temperature: float = Field(default=1.0, ge=0, le=2)
    frequency_penalty: float = Field(default=0.0, ge=-2, le=2)
    presence_penalty: float = Field(default=0.0, ge=-2, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    quote_wrapping: bool = False
    continue_prefill: bool = False
    squash_system_messages: bool = False
    function_calling: bool = False
    media_inlining: bool = True
    image_quality: ImageQuality = "auto"
    show_thoughts: bool = True
    reasoning_effort: ReasoningEffort = "auto"


class ConfigurationPresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    provider_id: str | None = None
    prompt_template_id: str | None = None
    character_id: str | None = None
    user_persona_id: str | None = None
    world_book_ids: list[str] | None = None
    max_context_unlocked: bool | None = None
    context_length: int | None = Field(default=None, ge=512, le=2_000_000)
    max_response_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    candidate_count: int | None = Field(default=None, ge=1, le=16)
    streaming: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    quote_wrapping: bool | None = None
    continue_prefill: bool | None = None
    squash_system_messages: bool | None = None
    function_calling: bool | None = None
    media_inlining: bool | None = None
    image_quality: ImageQuality | None = None
    show_thoughts: bool | None = None
    reasoning_effort: ReasoningEffort | None = None


class ConfigurationPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    provider_id: str | None
    prompt_template_id: str | None
    character_id: str | None
    user_persona_id: str | None
    world_book_ids: list[str]
    is_active: bool
    max_context_unlocked: bool
    context_length: int
    max_response_tokens: int
    candidate_count: int
    streaming: bool
    temperature: float
    frequency_penalty: float
    presence_penalty: float
    top_p: float
    quote_wrapping: bool
    continue_prefill: bool
    squash_system_messages: bool
    function_calling: bool
    media_inlining: bool
    image_quality: ImageQuality
    show_thoughts: bool
    reasoning_effort: ReasoningEffort
    created_at: datetime
    updated_at: datetime


class WorldBookEntryCreate(BaseModel):
    primary_keys: list[str] = Field(default_factory=list)
    secondary_keys: list[str] = Field(default_factory=list)
    comment: str = Field(default="", max_length=240)
    content: str = ""
    constant: bool = False
    selective: bool = True
    selective_logic: int = Field(default=0, ge=0, le=3)
    enabled: bool = True
    insertion_order: int = Field(default=100, ge=-100000, le=100000)
    position: int = Field(default=0, ge=0, le=7)
    insertion_depth: int = Field(default=4, ge=0, le=1000)
    role: Role = "system"
    probability: int = Field(default=100, ge=0, le=100)
    use_probability: bool = True


class WorldBookEntryUpdate(BaseModel):
    primary_keys: list[str] | None = None
    secondary_keys: list[str] | None = None
    comment: str | None = Field(default=None, max_length=240)
    content: str | None = None
    constant: bool | None = None
    selective: bool | None = None
    selective_logic: int | None = Field(default=None, ge=0, le=3)
    enabled: bool | None = None
    insertion_order: int | None = Field(default=None, ge=-100000, le=100000)
    position: int | None = Field(default=None, ge=0, le=7)
    insertion_depth: int | None = Field(default=None, ge=0, le=1000)
    role: Role | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    use_probability: bool | None = None


class WorldBookEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    world_book_id: str
    uid: int
    primary_keys: list[str]
    secondary_keys: list[str]
    comment: str
    content: str
    constant: bool
    selective: bool
    selective_logic: int
    enabled: bool
    insertion_order: int
    position: int
    insertion_depth: int
    role: Role
    probability: int
    use_probability: bool
    created_at: datetime
    updated_at: datetime


class WorldBookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    scope: Literal["global", "character"] = "character"
    character_id: str | None = None


class WorldBookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    scope: Literal["global", "character"] | None = None
    character_id: str | None = None


class WorldBookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    source_format: str
    scope: Literal["global", "character"]
    character_id: str | None
    created_at: datetime
    updated_at: datetime
    entries: list[WorldBookEntryOut]


class SillyTavernNamedJson(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    data: dict[str, Any]


class SillyTavernImportRequest(BaseModel):
    preset: SillyTavernNamedJson | None = None
    world_books: list[SillyTavernNamedJson] = Field(default_factory=list)
    characters: list[SillyTavernNamedJson] = Field(default_factory=list)
    character_id: str | None = None
    activate: bool = False


class SillyTavernImportReport(BaseModel):
    preset_id: str | None = None
    preset_name: str | None = None
    prompt_template_id: str | None = None
    provider_id: str | None = None
    world_book_ids: list[str] = Field(default_factory=list)
    character_ids: list[str] = Field(default_factory=list)
    imported_characters: int = 0
    imported_prompt_blocks: int = 0
    imported_world_entries: int = 0
    warnings: list[str] = Field(default_factory=list)


class PluginUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class PluginStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any]


class PluginOrderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_ids: list[str] = Field(min_length=1)


class PluginAdminActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeMediaRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image"] = "image"
    ref: str = Field(min_length=1, max_length=1000)
    name: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def reject_inline_data(self) -> RuntimeMediaRef:
        lowered = self.ref.lower()
        normalized = self.ref.replace("\\", "/")
        path = PurePosixPath(normalized)
        if lowered.startswith("data:") or "base64," in lowered:
            raise ValueError("媒体引用不能包含内联 base64")
        if path.is_absolute() or "://" in normalized or any(
            part in {"", ".", ".."} or ":" in part for part in path.parts
        ):
            raise ValueError("媒体引用必须是运行数据目录内的相对路径")
        return self


class RuntimeMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=100_000)
    channel: str = Field(default="internal", min_length=1, max_length=40)
    media: list[RuntimeMediaRef] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def require_content(self) -> RuntimeMessageRequest:
        if not self.text and not self.media:
            raise ValueError("消息文本和媒体不能同时为空")
        return self


class RuntimeReplyOut(BaseModel):
    conversation_id: str
    route_id: str = ""
    consumed: bool
    text: str = ""
    message_id: str | None = None
    model: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    outbound_actions: list[dict[str, Any]] = Field(default_factory=list)


class RuntimeLogOut(BaseModel):
    id: int
    created_at: datetime
    level: str
    source: str
    message: str


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    external_id: str
    title: str
    is_active: bool
    message_count: int = 0
    total_tokens: int = 0
    character_name: str | None = None
    last_message_preview: str = ""
    created_at: datetime
    updated_at: datetime


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=240)


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=240)


class ConversationMessagesDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_ids: list[str] = Field(min_length=1, max_length=1000)


class ConversationMessagesDeleteOut(BaseModel):
    deleted_count: int
    remaining_count: int


class ConversationMessageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=100_000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    position: int
    role: str
    content: str
    status: str
    source: str
    provider_id: str | None
    preset_id: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    token_count: int = 0
    speaker_name: str = ""
    message_metadata: dict[str, Any]
    created_at: datetime


class RuntimeActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plugin_id: str
    conversation_id: str
    turn_id: str | None
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    error: str
    created_at: datetime
    updated_at: datetime


class OneBotConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    connection_mode: Literal["reverse", "forward"] | None = None
    reverse_ws_url: str | None = Field(default=None, max_length=1000)
    forward_ws_url: str | None = Field(default=None, max_length=1000)
    access_token: str | None = Field(default=None, max_length=4000)
    private_messages: bool | None = None
    group_messages: bool | None = None
    private_allowlist: list[str] | None = Field(default=None, max_length=1000)
    group_allowlist: list[str] | None = Field(default=None, max_length=1000)
    api_timeout_seconds: int | None = Field(default=None, ge=3, le=120)

    @field_validator("private_allowlist", "group_allowlist")
    @classmethod
    def validate_qq_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = []
        for value in values:
            item = str(value).strip()
            if not item.isdigit() or len(item) > 40:
                raise ValueError("QQ 号和群号只能包含数字")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @field_validator("reverse_ws_url", "forward_ws_url")
    @classmethod
    def validate_ws_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = urlparse(normalized)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("WebSocket 地址必须是有效的 ws:// 或 wss:// 地址")
        return normalized


class OneBotConfigOut(BaseModel):
    id: int
    enabled: bool
    connection_mode: Literal["reverse", "forward"]
    reverse_ws_url: str
    forward_ws_url: str
    access_token_configured: bool
    access_token_masked: str
    private_messages: bool
    group_messages: bool
    private_allowlist: list[str]
    group_allowlist: list[str]
    api_timeout_seconds: int
    created_at: datetime
    updated_at: datetime


class OneBotStatusOut(BaseModel):
    enabled: bool
    connection_mode: Literal["reverse", "forward"]
    connected: bool
    connections: int
    self_ids: list[str]
    connected_at: datetime | None = None
    last_event_at: datetime | None = None
    pending_actions: int = 0
    failed_actions: int = 0
    connection_error: str = ""
