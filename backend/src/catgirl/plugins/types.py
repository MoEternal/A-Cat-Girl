from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
SUPPORTED_HOOKS = {
    "on_startup",
    "on_shutdown",
    "before_qq_message",
    "on_user_message",
    "before_prompt_compile",
    "transform_model_response",
    "before_response_split",
    "after_model_response",
    "on_message_recall",
    "before_send",
    "after_send",
}


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=1000)
    entrypoint: str = Field(default="plugin.py", min_length=1, max_length=240)
    author: str = Field(default="", max_length=120)
    min_app_version: str = Field(default="1.0.0", max_length=40)
    admin_ui: str | None = Field(default=None, max_length=240)
    hide_metadata: bool = False
    default_enabled: bool = False
    permissions: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    settings_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not PLUGIN_ID_PATTERN.fullmatch(value):
            raise ValueError("插件 ID 只能使用小写字母、数字和下划线，且必须以字母开头")
        return value

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("entrypoint 必须是插件目录内的相对路径")
        if not normalized.endswith(".py"):
            raise ValueError("entrypoint 必须指向 Python 文件")
        return normalized

    @field_validator("admin_ui")
    @classmethod
    def validate_admin_ui(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("admin_ui 必须是插件目录内的相对路径")
        if not normalized.endswith(".html"):
            raise ValueError("admin_ui 必须指向 HTML 文件")
        return normalized

    @field_validator("hooks")
    @classmethod
    def validate_hooks(cls, values: list[str]) -> list[str]:
        unknown = sorted(set(values) - SUPPORTED_HOOKS)
        if unknown:
            raise ValueError(f"不支持的插件钩子：{', '.join(unknown)}")
        if len(values) != len(set(values)):
            raise ValueError("插件钩子不能重复")
        return values

    @model_validator(mode="after")
    def validate_settings_schema(self) -> PluginManifest:
        if not isinstance(self.initial_state, dict):
            raise ValueError("initial_state 必须是对象")
        schema = self.settings_schema
        if schema.get("type", "object") != "object":
            raise ValueError("settings_schema 顶层类型必须是 object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("settings_schema.properties 必须是对象")
        allowed_types = {"boolean", "integer", "number", "string"}
        for key, definition in properties.items():
            if not isinstance(key, str) or not key:
                raise ValueError("设置项名称不能为空")
            if not isinstance(definition, dict) or definition.get("type") not in allowed_types:
                raise ValueError(f"设置项 {key} 使用了不支持的类型")
            if "enum" in definition and not isinstance(definition["enum"], list):
                raise ValueError(f"设置项 {key} 的 enum 必须是数组")
            max_length = definition.get("maxLength")
            if max_length is not None and (
                definition.get("type") != "string"
                or not isinstance(max_length, int)
                or isinstance(max_length, bool)
                or max_length < 0
            ):
                raise ValueError(f"设置项 {key} 的 maxLength 必须是非负整数")
        return self

    def default_settings(self) -> dict[str, Any]:
        return {
            key: definition["default"]
            for key, definition in self.settings_schema.get("properties", {}).items()
            if "default" in definition
        }


class PluginMediaRef(BaseModel):
    kind: Literal["image"] = "image"
    ref: str = Field(min_length=1, max_length=1000)
    name: str = Field(default="", max_length=240)


class PluginEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    conversation_id: str = "default"
    user_id: str = ""
    text: str = ""
    response_text: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    media: list[PluginMediaRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PluginAction(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)


class PluginResult(BaseModel):
    actions: list[PluginAction] = Field(default_factory=list)
    consume: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class PluginProtocol(Protocol):
    def on_startup(self, context: Any, event: PluginEvent) -> PluginResult | None: ...
