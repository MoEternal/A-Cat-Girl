from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..media import DATA_IMAGE_PATTERN, MediaValidationError, ensure_history_content_safe

if TYPE_CHECKING:
    from .manager import PluginManager


MAX_PLUGIN_STATE_STRING = 4 * 1024 * 1024


def sanitize_plugin_data(value: Any) -> Any:
    """Keep plugin state JSON-only and prevent request-time image data persistence."""
    if isinstance(value, str):
        if DATA_IMAGE_PATTERN.search(value):
            raise MediaValidationError("Base64 图片数据不能写入插件状态")
        if len(value) > MAX_PLUGIN_STATE_STRING:
            raise ValueError("插件状态中的单个文本字段过长")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [sanitize_plugin_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_plugin_data(item) for key, item in value.items()}
    raise TypeError(f"插件状态不支持 {type(value).__name__} 类型")


class PluginContext:
    def __init__(
        self,
        manager: PluginManager,
        plugin_id: str,
        plugin_path: Path,
        *,
        turn_id: str | None = None,
        read_only: bool = False,
    ):
        self._manager = manager
        self.plugin_id = plugin_id
        self.plugin_path = plugin_path.resolve()
        self.turn_id = str(turn_id or "").strip() or None
        self._read_only = read_only

    def _ensure_writable(self) -> None:
        if self._read_only:
            raise RuntimeError("提示词预览上下文不允许写入或调用模型")

    @property
    def memory_path(self) -> Path:
        """Writable, user-visible plugin data kept separate from plugin source files."""
        return (self._manager.installed_dir.parent / "plugin_memory" / self.plugin_id).resolve()

    @property
    def settings(self) -> dict[str, Any]:
        return deepcopy(self._manager.get_settings(self.plugin_id))

    @property
    def state(self) -> dict[str, Any]:
        return deepcopy(self._manager.get_state(self.plugin_id))

    def replace_state(self, state: dict[str, Any]) -> None:
        self._ensure_writable()
        self._manager.set_state(self.plugin_id, sanitize_plugin_data(state))

    def patch_state(self, **values: Any) -> dict[str, Any]:
        state = self.state
        state.update(sanitize_plugin_data(values))
        self.replace_state(state)
        return state

    def get_conversation_state(self, conversation_id: str) -> dict[str, Any]:
        return deepcopy(self._manager.get_conversation_state(self.plugin_id, conversation_id))

    def replace_conversation_state(self, conversation_id: str, state: dict[str, Any]) -> None:
        self._ensure_writable()
        self._manager.set_conversation_state(
            self.plugin_id,
            conversation_id,
            sanitize_plugin_data(state),
            turn_id=self.turn_id,
        )

    def patch_conversation_state(self, conversation_id: str, **values: Any) -> dict[str, Any]:
        state = self.get_conversation_state(conversation_id)
        state.update(sanitize_plugin_data(values))
        self.replace_conversation_state(conversation_id, state)
        return state

    def delete_conversation_state(self, conversation_id: str) -> None:
        self._ensure_writable()
        self._manager.delete_conversation_state(
            self.plugin_id,
            conversation_id,
            turn_id=self.turn_id,
        )

    async def generate_text(
        self,
        conversation_id: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        self._ensure_writable()
        safe_messages = []
        for message in messages:
            role = str(message.get("role", "user"))
            if role not in {"system", "user", "assistant"}:
                raise ValueError("静默模型消息角色无效")
            content = self.ensure_text_safe(str(message.get("content", "")))
            safe_messages.append({"role": role, "content": content})
        if not safe_messages:
            raise ValueError("静默模型消息不能为空")
        return await self._manager.generate_text(
            self.plugin_id,
            conversation_id,
            safe_messages,
            max(1, min(int(max_tokens), 65535)),
            max(0.0, min(float(temperature), 2.0)),
        )

    async def generate_with_context(
        self,
        conversation_id: str,
        prompt: str,
        *,
        inherited_additions: list[dict[str, str]] | None = None,
    ) -> str:
        self._ensure_writable()
        safe_prompt = self.ensure_text_safe(prompt)
        if not safe_prompt.strip():
            raise ValueError("上下文续写提示不能为空")
        additions = []
        for item in list(inherited_additions or [])[:30]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "system"))
            if role not in {"system", "user", "assistant"}:
                continue
            content = self.ensure_text_safe(str(item.get("content", "")))
            if content.strip():
                additions.append({"role": role, "content": content})
        return await self._manager.generate_with_context(
            self.plugin_id,
            conversation_id,
            safe_prompt,
            additions,
        )

    def get_conversation_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return deepcopy(
            self._manager.get_conversation_messages(
                self.plugin_id,
                conversation_id,
                max(1, min(int(limit), 200)),
            )
        )

    def ensure_text_safe(self, text: str) -> str:
        return ensure_history_content_safe(text)

    def resolve_asset(self, relative_path: str) -> Path:
        candidate = (self.plugin_path / relative_path).resolve()
        if candidate != self.plugin_path and self.plugin_path not in candidate.parents:
            raise ValueError("插件资源路径越界")
        if not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def schedule_interval(
        self,
        name: str,
        seconds: float,
        callback: Callable[[PluginContext], Any | Awaitable[Any]],
    ) -> None:
        self._ensure_writable()
        self._manager.schedule_interval(self.plugin_id, name, seconds, callback)

    def cancel_schedule(self, name: str) -> None:
        self._ensure_writable()
        self._manager.cancel_schedule(self.plugin_id, name)

    def get_runtime_value(self, key: str, default: Any = None) -> Any:
        return deepcopy(self._manager.runtime_values.get(key, default))

    def set_runtime_value(self, key: str, value: Any) -> None:
        self._ensure_writable()
        self._manager.runtime_values[key] = sanitize_plugin_data(value)
