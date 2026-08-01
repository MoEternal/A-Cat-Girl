from __future__ import annotations

import asyncio
import importlib.util
import inspect as pyinspect
import json
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from .. import __version__
from ..database import (
    ChatMessage,
    Conversation,
    Database,
    PluginConversationState,
    PluginInstallation,
)
from ..security import SecretBox
from .context import PluginContext, sanitize_plugin_data
from .types import PLUGIN_ID_PATTERN, PluginAction, PluginEvent, PluginManifest, PluginResult


LOGGER = logging.getLogger("catgirl.plugins")
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_FILES = 1200
ADMIN_ASSET_SUFFIXES = {
    ".css", ".gif", ".html", ".ico", ".jpeg", ".jpg", ".js", ".json", ".png", ".webp", ".woff2",
}
BUILT_IN_DEFAULT_PROFILE_VERSION = "20260728-v1"
BUILT_IN_DEFAULT_ORDER = (
    "group_chat_management",
    "regex_filter",
    "recall",
    "memory_system",
    "reply_merge",
    "segmented_reply",
    "proactive_reply",
    "sticker_reply",
    "time_awareness",
    "good_night",
    "web_search",
)
BUILT_IN_DEFAULT_ENABLED = {
    "memory_system": False,
    "recall": True,
    "regex_filter": True,
    "reply_merge": True,
    "segmented_reply": True,
    "time_awareness": True,
    "good_night": False,
    "proactive_reply": False,
    "sticker_reply": False,
    "web_search": False,
}
BUILT_IN_LEGACY_SETTINGS = {
    "sticker_reply": {
        "positive_categories": {
            "happy,like,shy,meow,color,fool,see,surprised,morning",
        },
    },
    "proactive_reply": {
        "first_prompt": {
            "用户已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事，不要提及这条系统提示。",
            "用户已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事。不要提及这条系统提示。",
            "{{user}}已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事，不要提及这条系统提示。",
            "{{user}}已经有一段时间没有说话。请自然地主动开启话题，可以表达好奇、关心或分享此刻想说的事。不要提及这条系统提示。",
        },
        "second_prompt": {
            "用户在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨，但不要提及这条系统提示。",
            "用户在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨。不要提及这条系统提示。",
            "{{user}}在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨，但不要提及这条系统提示。",
            "{{user}}在你主动联系后仍没有回复。请更明显地表达不满、担心或带有人设特点的抱怨。不要提及这条系统提示。",
        },
    },
    "segmented_reply": {
        "prompt": {
            "模拟真人聊天，在自然需要连续发送多条消息时使用 ||| 分隔回复（代替换行符）。",
            "在自然需要连续发送多条消息时使用 ||| 分隔回复（代替换行符，且不要在思考时使用分隔符）。",
        },
    },
    "time_awareness": {
        "prompt": {
            "自然感知现实日期、星期、时段和消息间隔；除非用户询问或时间与当前对话有关，不要刻意报时。\n"
            "时间感知已启用：现实时间优先于角色人设或世界设定中的虚拟时间；长期记忆必须按本轮现实时间记录。",
        },
    },
    "web_search": {
        "prompt": {
            "当用户询问时效性信息、新闻、近期事件，或你对事实没有把握且联网能显著提高准确性时，把整条回复写成且只写成 "
            "<search query=\"简洁搜索词\"/>。一次最多请求一次搜索；普通闲聊、创作和已有可靠答案不搜索。",
        },
        "search_model_prompt": {
            (
                "<Search_Model>\n"
                "# 网络检索\n"
                "- 你是独立的联网搜索模型，只负责检索和整理可核验资料，不代替聊天角色回答用户。\n"
                "- 当前现实时间：{{current_time}}\n"
                "- 本次查询：{{query}}\n\n"
                "## 时间规则\n"
                "- 必须以当前现实时间解释\"今天\"、\"昨天\"、\"最近\"等相对时间，不得把搜索引擎收录时间当成新闻发生时间。\n"
                "- 用户询问\"今天\"时，优先且仅将当前本地自然日发布的可靠报道列为今日新闻；没有可靠的当日结果就明确说明。\n"
                "- 严格区分报道发布时间、事件发生时间和数据发布时间。今天发布但描述昨天事件的内容，必须明确标注为\"今天报道、事件发生于昨天\"，不能直接说成今天发生。\n"
                "- 旧事件出现当日新进展时，只把新进展归为今天，并注明原事件发生时间。\n\n"
                "## 结果要求\n"
                "- 每条结果提供：标题、来源、原始链接、报道发布时间、事件或数据对应时间、摘要。\n"
                "- 优先政府机构、官方公告、通讯社、主流媒体和事件直接相关方；交叉核验关键事实。\n"
                "- 不确定的日期或事实必须明确标注，不得猜测、补写或把旧闻包装成最新消息。\n"
                "- 只返回检索材料，不使用聊天角色口吻。\n"
                "</Search_Model>"
            ),
        },
        "timeout_seconds": {15},
    },
    "good_night": {
        "wake_greeting_prompt": {
            "你刚刚睡醒。请按照当前人设自然地向用户问候，不要提及这条系统提示。",
            "你刚刚睡醒。请按照当前人设自然地向用户问候。",
        },
        "pending_reply_prompt": {
            "以下是用户在你休息期间发来的消息。请结合时间顺序统一回复，不要声称自己实时看到了这些消息。",
            "以下是用户在你休息期间发来的消息，结合时间顺序统一回复。",
        },
    },
}
HIDE_THINKING_RULE = {
    "id": "hide-thinking",
    "name": "隐藏 Thinking",
    "enabled": True,
    "pattern": r"<\s*thinking\b[^>]*>.*?<\s*/\s*thinking\s*>",
    "replacement": "",
    "flags": "is",
}


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*", value)
    if match is None:
        raise PluginError(f"插件最低程序版本格式无效：{value}")
    return tuple(int(part) for part in match.groups())


def _ensure_app_version_supported(manifest: PluginManifest) -> None:
    if _version_tuple(manifest.min_app_version) > _version_tuple(__version__):
        raise PluginError(
            f"插件 {manifest.name} 需要一只猫娘 {manifest.min_app_version} 或更高版本，当前为 {__version__}"
        )


class PluginError(RuntimeError):
    pass


def _apply_builtin_upgrade_migrations(
    row: PluginInstallation,
    record: PluginRecord,
    previous_version: str,
) -> None:
    if not record.built_in:
        return
    legacy_settings = BUILT_IN_LEGACY_SETTINGS.get(record.manifest.id, {})
    if legacy_settings:
        settings = dict(row.settings or {})
        defaults = record.manifest.default_settings()
        for key, legacy_values in legacy_settings.items():
            if settings.get(key) in legacy_values:
                settings[key] = defaults[key]
        if record.manifest.id == "sticker_reply":
            old_limit = settings.pop("max_asset_bytes", None)
            if "max_asset_mb" not in settings and isinstance(old_limit, (int, float)):
                settings["max_asset_mb"] = max(0.1, min(float(old_limit) / 1024 / 1024, 32.0))
        if record.manifest.id == "time_awareness":
            prompt = str(settings.get("prompt", ""))
            prompt = re.sub(
                r"<\s*time_awareness\s*>",
                "<Time_Awareness>",
                prompt,
                flags=re.IGNORECASE,
            )
            prompt = re.sub(
                r"<\s*/\s*time_awareness\s*>",
                "</Time_Awareness>",
                prompt,
                flags=re.IGNORECASE,
            )
            settings["prompt"] = prompt
        row.settings = settings
    if (
        record.manifest.id == "regex_filter"
        and previous_version != record.manifest.version
        and previous_version in {"", "1.0.0", "1.0.1"}
    ):
        state = dict(row.state or {})
        rules = list(state.get("global_rules") or [])
        if not any(
            isinstance(rule, dict) and str(rule.get("id")) == HIDE_THINKING_RULE["id"]
            for rule in rules
        ):
            rules.append(dict(HIDE_THINKING_RULE))
        state["global_rules"] = rules
        row.state = state


@dataclass
class PluginRecord:
    manifest: PluginManifest
    path: Path
    built_in: bool
    instance: Any = None
    loaded: bool = False


class PluginManager:
    def __init__(
        self,
        database: Database,
        built_in_dir: Path,
        installed_dir: Path,
        secret_box: SecretBox,
        intent_sink: Callable[..., Any | Awaitable[Any]] | None = None,
        analysis_sink: Callable[
            [str, str, list[dict[str, str]], int, float], Awaitable[str]
        ] | None = None,
        context_generation_sink: Callable[
            [str, str, str, list[dict[str, str]]], Awaitable[str]
        ] | None = None,
    ):
        self.database = database
        self.built_in_dir = built_in_dir
        self.installed_dir = installed_dir
        self.secret_box = secret_box
        self.intent_sink = intent_sink
        self.analysis_sink = analysis_sink
        self.context_generation_sink = context_generation_sink
        self.records: dict[str, PluginRecord] = {}
        self.tasks: dict[tuple[str, str], asyncio.Task] = {}
        self.runtime_values: dict[str, Any] = {}
        self.recent_actions: deque[dict[str, Any]] = deque(maxlen=100)
        self._started = False

    async def startup(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.discover()
        for plugin_id in self._ordered_plugin_ids():
            if self._installation(plugin_id).enabled:
                await self._load_and_start(plugin_id)
        self._started = True

    async def shutdown(self) -> None:
        for plugin_id in list(self.records):
            if self.records[plugin_id].loaded:
                await self._invoke(plugin_id, "on_shutdown", PluginEvent(name="on_shutdown"))
        for task in list(self.tasks.values()):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.runtime_values.clear()
        self._started = False

    def discover(self) -> None:
        previous = self.records
        discovered: dict[str, PluginRecord] = {}
        for root, built_in in ((self.built_in_dir, True), (self.installed_dir, False)):
            if not root.is_dir():
                continue
            for path in sorted(item for item in root.iterdir() if item.is_dir()):
                manifest_path = path / "plugin.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = PluginManifest.model_validate_json(manifest_path.read_text("utf-8"))
                    _ensure_app_version_supported(manifest)
                    if path.name != manifest.id:
                        raise PluginError("插件目录名必须与插件 ID 相同")
                    entrypoint = (path / manifest.entrypoint).resolve()
                    if path.resolve() not in entrypoint.parents or not entrypoint.is_file():
                        raise PluginError("插件入口文件不存在或越过插件目录")
                    if manifest.id in discovered:
                        raise PluginError(f"插件 ID 与内置插件冲突：{manifest.id}")
                    record = PluginRecord(manifest, path.resolve(), built_in)
                    old = previous.get(manifest.id)
                    if old is not None and old.path == record.path and old.manifest.version == manifest.version:
                        record.instance = old.instance
                        record.loaded = old.loaded
                    discovered[manifest.id] = record
                except (OSError, ValueError, ValidationError, PluginError) as exc:
                    LOGGER.error("无法读取插件 %s：%s", path, exc)

        self.records = discovered
        with self.database.session_factory() as session:
            rows = {row.plugin_id: row for row in session.scalars(select(PluginInstallation)).all()}
            profile_needs_update = not all(
                rows.get(plugin_id) is not None
                and rows[plugin_id].default_profile_version == BUILT_IN_DEFAULT_PROFILE_VERSION
                for plugin_id in BUILT_IN_DEFAULT_ENABLED
                if plugin_id in discovered
            )
            next_position = max((row.position for row in rows.values()), default=0) + 1
            for plugin_id in sorted(discovered):
                record = discovered[plugin_id]
                row = rows.get(plugin_id)
                defaults = record.manifest.default_settings()
                if row is None:
                    row = PluginInstallation(
                        plugin_id=plugin_id,
                        enabled=(
                            BUILT_IN_DEFAULT_ENABLED.get(plugin_id, record.manifest.default_enabled)
                            if record.built_in
                            else record.manifest.default_enabled
                        ),
                        built_in=record.built_in,
                        version=record.manifest.version,
                        position=next_position,
                        default_profile_version=(
                            BUILT_IN_DEFAULT_PROFILE_VERSION
                            if record.built_in and plugin_id in BUILT_IN_DEFAULT_ENABLED
                            else ""
                        ),
                        settings=defaults,
                        state=record.manifest.initial_state,
                    )
                    session.add(row)
                    rows[plugin_id] = row
                    next_position += 1
                else:
                    previous_version = str(row.version or "")
                    row.built_in = record.built_in
                    _apply_builtin_upgrade_migrations(row, record, previous_version)
                    row.settings = {**defaults, **(row.settings or {})}
                    row.state = {**record.manifest.initial_state, **(row.state or {})}
                    row.version = record.manifest.version
                    if row.position <= 0:
                        row.position = next_position
                        next_position += 1
            visible_rows = [row for plugin_id, row in rows.items() if plugin_id in discovered]
            if profile_needs_update:
                preferred_positions = {
                    plugin_id: position
                    for position, plugin_id in enumerate(BUILT_IN_DEFAULT_ORDER)
                }
                ordered_rows = sorted(
                    visible_rows,
                    key=lambda row: (
                        preferred_positions.get(row.plugin_id, len(preferred_positions)),
                        row.position,
                        row.plugin_id,
                    ),
                )
            else:
                ordered_rows = sorted(visible_rows, key=lambda row: (row.position, row.plugin_id))
            for position, row in enumerate(ordered_rows, 1):
                row.position = position
            for plugin_id, enabled in BUILT_IN_DEFAULT_ENABLED.items():
                row = rows.get(plugin_id)
                if row is not None and row.built_in and row.default_profile_version != BUILT_IN_DEFAULT_PROFILE_VERSION:
                    row.enabled = enabled
                    row.default_profile_version = BUILT_IN_DEFAULT_PROFILE_VERSION
            session.commit()

    def _ordered_plugin_ids(self) -> list[str]:
        with self.database.session_factory() as session:
            rows = session.scalars(
                select(PluginInstallation)
                .where(PluginInstallation.plugin_id.in_(self.records))
                .order_by(PluginInstallation.position, PluginInstallation.plugin_id)
            ).all()
        ordered = [row.plugin_id for row in rows if row.plugin_id in self.records]
        return ordered + sorted(set(self.records) - set(ordered))

    def reorder(self, plugin_ids: list[str]) -> None:
        expected = set(self.records)
        submitted = set(plugin_ids)
        if len(plugin_ids) != len(submitted) or submitted != expected:
            raise PluginError("插件排序必须包含且仅包含当前全部插件")
        with self.database.session_factory() as session:
            for position, plugin_id in enumerate(plugin_ids, 1):
                row = session.get(PluginInstallation, plugin_id)
                if row is None:
                    raise PluginError("插件未安装")
                row.position = position
            session.commit()

    def resolve_admin_asset(self, plugin_id: str, asset_path: str) -> Path:
        record = self.records.get(plugin_id)
        if record is None or not record.manifest.admin_ui:
            raise PluginError("插件没有管理页面")
        normalized = PurePosixPath(asset_path.replace("\\", "/"))
        if normalized.is_absolute() or any(part in {"", ".", ".."} for part in normalized.parts):
            raise PluginError("插件管理资源路径无效")
        admin_entry = (record.path / record.manifest.admin_ui).resolve()
        admin_root = admin_entry.parent
        target = record.path.joinpath(*normalized.parts).resolve()
        if target != admin_root and admin_root not in target.parents:
            raise PluginError("插件管理资源路径越界")
        if target.suffix.lower() not in ADMIN_ASSET_SUFFIXES or not target.is_file():
            raise PluginError("插件管理资源不存在")
        return target

    async def admin_action(
        self,
        plugin_id: str,
        action: str,
        payload: dict[str, Any],
    ) -> Any:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件不存在")
        row = self._installation(plugin_id)
        if not row.enabled or not record.loaded or record.instance is None:
            raise PluginError("插件尚未启用")
        handler = getattr(record.instance, "admin_action", None)
        if not callable(handler):
            raise PluginError("插件不支持管理动作")
        safe_payload = sanitize_plugin_data(payload)
        context = PluginContext(self, plugin_id, record.path)
        try:
            value = handler(context, action, safe_payload)
            if pyinspect.isawaitable(value):
                value = await value
            return sanitize_plugin_data(value)
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError(f"管理动作执行失败：{type(exc).__name__}: {exc}") from exc

    def _installation(self, plugin_id: str) -> PluginInstallation:
        with self.database.session_factory() as session:
            row = session.get(PluginInstallation, plugin_id)
            if row is None:
                raise PluginError("插件未安装")
            session.expunge(row)
            return row

    def get_settings(self, plugin_id: str) -> dict[str, Any]:
        settings = dict(self._installation(plugin_id).settings or {})
        record = self.records.get(plugin_id)
        if record is None:
            return settings
        for key, definition in record.manifest.settings_schema.get("properties", {}).items():
            if definition.get("format") == "password":
                settings[key] = self.secret_box.decrypt(str(settings.get(key, "")))
        return settings

    def is_enabled(self, plugin_id: str) -> bool:
        if plugin_id not in self.records:
            return False
        return self._installation(plugin_id).enabled

    def get_state(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id not in self.records:
            raise PluginError("插件不存在")
        return dict(self._installation(plugin_id).state or {})

    def set_state(self, plugin_id: str, state: dict[str, Any]) -> None:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件不存在")
        normalized_state = state
        normalizer = getattr(record.instance, "normalize_state", None) if record.loaded else None
        if callable(normalizer):
            normalized_state = normalizer(state)
            if not isinstance(normalized_state, dict):
                raise PluginError("插件状态规范化结果必须是对象")
        validator = getattr(record.instance, "validate_state", None) if record.loaded else None
        if callable(validator):
            validator(normalized_state)
        safe_state = sanitize_plugin_data(normalized_state)
        with self.database.session_factory() as session:
            row = session.get(PluginInstallation, plugin_id)
            if row is None:
                raise PluginError("插件未安装")
            row.state = safe_state
            session.commit()

    def get_conversation_state(self, plugin_id: str, conversation_id: str) -> dict[str, Any]:
        record = self.records.get(plugin_id)
        handler = (
            getattr(record.instance, "conversation_state_get", None)
            if record is not None and record.loaded and record.instance is not None
            else None
        )
        if callable(handler):
            context = PluginContext(self, plugin_id, record.path)
            try:
                value = handler(context, conversation_id)
                if not isinstance(value, dict):
                    raise PluginError("插件的文件记忆读取结果必须是对象")
                return dict(sanitize_plugin_data(value))
            except PluginError:
                raise
            except Exception as exc:
                raise PluginError(f"文件记忆读取失败：{type(exc).__name__}: {exc}") from exc
        return self.get_database_conversation_state(plugin_id, conversation_id)

    def get_database_conversation_state(self, plugin_id: str, conversation_id: str) -> dict[str, Any]:
        with self.database.session_factory() as session:
            row = session.get(PluginConversationState, (plugin_id, conversation_id))
            return dict(row.state or {}) if row is not None else {}

    def inspect_conversation_states(
        self,
        plugin_id: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Return record metadata plus one selected state for the admin interface."""
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件不存在")
        handler = (
            getattr(record.instance, "inspect_conversation_states", None)
            if record.loaded and record.instance is not None
            else None
        )
        if callable(handler):
            context = PluginContext(self, plugin_id, record.path)
            try:
                value = handler(context, conversation_id)
                if not isinstance(value, dict):
                    raise PluginError("插件的记忆概览必须是对象")
                return dict(sanitize_plugin_data(value))
            except PluginError:
                raise
            except Exception as exc:
                raise PluginError(f"记忆概览读取失败：{type(exc).__name__}: {exc}") from exc
        with self.database.session_factory() as session:
            rows = session.execute(
                select(PluginConversationState, Conversation)
                .join(Conversation, Conversation.id == PluginConversationState.conversation_id)
                .where(PluginConversationState.plugin_id == plugin_id)
                .order_by(PluginConversationState.updated_at.desc())
            ).all()
            items = [
                {
                    "conversation_id": state_row.conversation_id,
                    "title": conversation.title,
                    "external_id": conversation.external_id,
                    "is_active": conversation.is_active,
                    "updated_at": state_row.updated_at,
                }
                for state_row, conversation in rows
            ]
            selected = None
            if conversation_id:
                selected = next(
                    (state_row for state_row, _ in rows if state_row.conversation_id == conversation_id),
                    None,
                )
                if selected is None:
                    raise PluginError("插件在该聊天记录中没有状态")
            elif rows:
                selected = rows[0][0]
            return {
                "items": items,
                "selected_id": selected.conversation_id if selected is not None else None,
                "state": dict(selected.state or {}) if selected is not None else self.get_state(plugin_id),
            }

    def set_conversation_state(
        self,
        plugin_id: str,
        conversation_id: str,
        state: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> None:
        safe_state = sanitize_plugin_data(state)
        record = self.records.get(plugin_id)
        handler = (
            getattr(record.instance, "conversation_state_set", None)
            if record is not None and record.loaded and record.instance is not None
            else None
        )
        if callable(handler):
            context = PluginContext(self, plugin_id, record.path)
            try:
                if "turn_id" in pyinspect.signature(handler).parameters:
                    handler(
                        context,
                        conversation_id,
                        safe_state,
                        turn_id=turn_id,
                    )
                else:
                    handler(context, conversation_id, safe_state)
                self.delete_database_conversation_state(plugin_id, conversation_id)
                return
            except PluginError:
                raise
            except Exception as exc:
                raise PluginError(f"文件记忆写入失败：{type(exc).__name__}: {exc}") from exc
        self.set_database_conversation_state(plugin_id, conversation_id, safe_state)

    def set_database_conversation_state(
        self,
        plugin_id: str,
        conversation_id: str,
        state: dict[str, Any],
    ) -> None:
        safe_state = sanitize_plugin_data(state)
        with self.database.session_factory() as session:
            row = session.get(PluginConversationState, (plugin_id, conversation_id))
            if row is None:
                row = PluginConversationState(
                    plugin_id=plugin_id,
                    conversation_id=conversation_id,
                    state=safe_state,
                )
                session.add(row)
            else:
                row.state = safe_state
            session.commit()

    def delete_conversation_state(
        self,
        plugin_id: str,
        conversation_id: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        record = self.records.get(plugin_id)
        handler = (
            getattr(record.instance, "conversation_state_delete", None)
            if record is not None and record.loaded and record.instance is not None
            else None
        )
        if callable(handler):
            context = PluginContext(self, plugin_id, record.path)
            try:
                if "turn_id" in pyinspect.signature(handler).parameters:
                    handler(context, conversation_id, turn_id=turn_id)
                else:
                    handler(context, conversation_id)
                self.delete_database_conversation_state(plugin_id, conversation_id)
                return
            except PluginError:
                raise
            except Exception as exc:
                raise PluginError(f"文件记忆重置失败：{type(exc).__name__}: {exc}") from exc
        self.delete_database_conversation_state(plugin_id, conversation_id)

    def delete_database_conversation_state(self, plugin_id: str, conversation_id: str) -> None:
        with self.database.session_factory() as session:
            row = session.get(PluginConversationState, (plugin_id, conversation_id))
            if row is not None:
                session.delete(row)
                session.commit()

    def conversation_created(self, conversation_id: str) -> None:
        """Let enabled file-backed plugins create their default per-record binding."""
        for plugin_id in self._ordered_plugin_ids():
            record = self.records.get(plugin_id)
            if record is None or not record.loaded or record.instance is None:
                continue
            handler = getattr(record.instance, "on_conversation_created", None)
            if not callable(handler):
                continue
            try:
                handler(PluginContext(self, plugin_id, record.path), conversation_id)
            except Exception as exc:
                self._set_error(plugin_id, f"创建聊天记忆失败：{type(exc).__name__}: {exc}")
                LOGGER.exception("插件 %s 创建聊天记忆失败", plugin_id)

    def conversation_deleted(self, conversation_id: str) -> None:
        """Let file-backed plugins remove bindings left by a deleted chat record."""
        for plugin_id in self._ordered_plugin_ids():
            record = self.records.get(plugin_id)
            if record is None or not record.loaded or record.instance is None:
                continue
            handler = getattr(record.instance, "on_conversation_deleted", None)
            if not callable(handler):
                continue
            try:
                handler(PluginContext(self, plugin_id, record.path), conversation_id)
            except Exception as exc:
                self._set_error(plugin_id, f"清理聊天记忆绑定失败：{type(exc).__name__}: {exc}")
                LOGGER.exception("插件 %s 清理聊天记忆绑定失败", plugin_id)

    def conversation_renamed(self, conversation_id: str, previous_title: str) -> None:
        """Let file-backed plugins refresh generated memory names after a rename."""
        for plugin_id in self._ordered_plugin_ids():
            record = self.records.get(plugin_id)
            if record is None or not record.loaded or record.instance is None:
                continue
            handler = getattr(record.instance, "on_conversation_renamed", None)
            if not callable(handler):
                continue
            try:
                handler(
                    PluginContext(self, plugin_id, record.path),
                    conversation_id,
                    previous_title,
                )
            except Exception as exc:
                self._set_error(plugin_id, f"同步聊天记忆名称失败：{type(exc).__name__}: {exc}")
                LOGGER.exception("插件 %s 同步聊天记忆名称失败", plugin_id)

    def capture_external_conversation_state(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> None:
        for plugin_id in self._ordered_plugin_ids():
            record = self.records.get(plugin_id)
            if record is None or not record.loaded or record.instance is None:
                continue
            handler = getattr(record.instance, "capture_conversation_snapshot", None)
            if not callable(handler):
                continue
            try:
                handler(PluginContext(self, plugin_id, record.path), conversation_id, turn_id)
            except Exception:
                LOGGER.exception("插件 %s 创建文件状态快照失败", plugin_id)

    def restore_external_conversation_state(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> None:
        for plugin_id in self._ordered_plugin_ids():
            record = self.records.get(plugin_id)
            if record is None or not record.loaded or record.instance is None:
                continue
            handler = getattr(record.instance, "restore_conversation_snapshot", None)
            if not callable(handler):
                continue
            try:
                handler(PluginContext(self, plugin_id, record.path), conversation_id, turn_id)
            except Exception:
                LOGGER.exception("插件 %s 恢复文件状态快照失败", plugin_id)

    async def generate_text(
        self,
        plugin_id: str,
        conversation_id: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        record = self.records.get(plugin_id)
        if record is None or "model.generate.selected_provider" not in record.manifest.permissions:
            raise PluginError("插件没有声明当前供应商生成权限")
        if self.analysis_sink is None:
            raise PluginError("静默模型分析处理器尚未就绪")
        return await self.analysis_sink(
            plugin_id,
            conversation_id,
            messages,
            max(1, min(int(max_tokens), 65535)),
            temperature,
        )

    async def generate_with_context(
        self,
        plugin_id: str,
        conversation_id: str,
        prompt: str,
        inherited_additions: list[dict[str, str]],
    ) -> str:
        record = self.records.get(plugin_id)
        if record is None or "model.generate.with_context" not in record.manifest.permissions:
            raise PluginError("插件没有声明上下文续写权限")
        if self.context_generation_sink is None:
            raise PluginError("上下文续写处理器尚未就绪")
        return await self.context_generation_sink(
            plugin_id,
            conversation_id,
            prompt,
            inherited_additions,
        )

    def get_conversation_messages(
        self,
        plugin_id: str,
        conversation_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        record = self.records.get(plugin_id)
        if record is None or "message.history.read" not in record.manifest.permissions:
            raise PluginError("插件没有声明聊天历史读取权限")
        normalized_limit = max(1, min(int(limit), 200))
        with self.database.session_factory() as session:
            rows = list(
                session.scalars(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation_id,
                        ChatMessage.status == "complete",
                        ChatMessage.role.in_(("user", "assistant")),
                    )
                    .order_by(ChatMessage.position.desc())
                    .limit(normalized_limit)
                ).all()
            )
        return [
            {
                "id": row.id,
                "position": row.position,
                "role": row.role,
                "content": row.content,
                "created_at": row.created_at.isoformat(),
            }
            for row in reversed(rows)
        ]

    def _set_error(self, plugin_id: str, error: str) -> None:
        with self.database.session_factory() as session:
            row = session.get(PluginInstallation, plugin_id)
            if row is not None:
                row.last_error = error[:4000]
                session.commit()

    async def _load_and_start(self, plugin_id: str) -> None:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件文件不存在")
        try:
            module_name = f"catgirl_runtime_plugin_{plugin_id}"
            sys.modules.pop(module_name, None)
            spec = importlib.util.spec_from_file_location(module_name, record.path / record.manifest.entrypoint)
            if spec is None or spec.loader is None:
                raise PluginError("无法创建插件模块")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            instance = module.create_plugin() if callable(getattr(module, "create_plugin", None)) else getattr(module, "plugin", None)
            if instance is None:
                raise PluginError("入口必须导出 plugin 对象或 create_plugin()")
            for hook in record.manifest.hooks:
                if not callable(getattr(instance, hook, None)):
                    raise PluginError(f"清单声明了 {hook}，但入口未实现该方法")
            record.instance = instance
            record.loaded = True
            self._set_error(plugin_id, "")
            await self._invoke(plugin_id, "on_startup", PluginEvent(name="on_startup"))
        except Exception as exc:
            record.instance = None
            record.loaded = False
            message = f"{type(exc).__name__}: {exc}"
            self._set_error(plugin_id, message)
            LOGGER.exception("插件 %s 加载失败", plugin_id)

    async def _invoke(self, plugin_id: str, hook: str, event: PluginEvent) -> PluginResult:
        record = self.records.get(plugin_id)
        if record is None or not record.loaded or hook not in record.manifest.hooks:
            return PluginResult()
        method = getattr(record.instance, hook)
        context = PluginContext(
            self,
            plugin_id,
            record.path,
            turn_id=str(event.metadata.get("turn_id") or "") or None,
        )
        try:
            value = method(context, event)
            if pyinspect.isawaitable(value):
                value = await value
            result = self._normalize_result(value)
            for action in result.actions:
                await self._emit(
                    plugin_id,
                    action,
                    turn_id=str(event.metadata.get("turn_id") or "") or None,
                )
            return result
        except Exception as exc:
            message = f"{hook}: {type(exc).__name__}: {exc}"
            self._set_error(plugin_id, message)
            LOGGER.exception("插件 %s 执行 %s 失败", plugin_id, hook)
            return PluginResult(metadata={"error": message})

    @staticmethod
    def _normalize_result(value: Any) -> PluginResult:
        if value is None:
            return PluginResult()
        if isinstance(value, PluginResult):
            return value
        return PluginResult.model_validate(value)

    async def _emit(
        self,
        plugin_id: str,
        action: PluginAction,
        *,
        turn_id: str | None = None,
    ) -> None:
        safe_payload = sanitize_plugin_data(action.payload)
        safe_action = PluginAction(kind=action.kind, payload=safe_payload)
        self.recent_actions.append({"plugin_id": plugin_id, **safe_action.model_dump()})
        if self.intent_sink is not None:
            if turn_id:
                value = self.intent_sink(plugin_id, safe_action, turn_id=turn_id)
            else:
                value = self.intent_sink(plugin_id, safe_action)
            if pyinspect.isawaitable(value):
                await value

    async def dispatch(self, hook: str, event: PluginEvent | dict[str, Any]) -> PluginResult:
        normalized_event = event if isinstance(event, PluginEvent) else PluginEvent.model_validate(event)
        actions: list[PluginAction] = []
        consume = False
        metadata: dict[str, Any] = {}
        for plugin_id in self._ordered_plugin_ids():
            row = self._installation(plugin_id)
            if not row.enabled or not self.records[plugin_id].loaded:
                continue
            result = await self._invoke(plugin_id, hook, normalized_event)
            actions.extend(result.actions)
            consume = consume or result.consume
            metadata.update(result.metadata)
            if hook == "before_qq_message":
                inbound_text = result.metadata.get("inbound_text")
                if isinstance(inbound_text, str):
                    normalized_event = normalized_event.model_copy(update={"text": inbound_text})
            if result.consume:
                break
        return PluginResult(actions=actions, consume=consume, metadata=metadata)

    async def preview_prompt_actions(
        self,
        event: PluginEvent | dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Collect prompt-related hook output without emitting actions or allowing context writes."""
        normalized_event = event if isinstance(event, PluginEvent) else PluginEvent.model_validate(event)
        output: list[dict[str, Any]] = []
        for plugin_id in self._ordered_plugin_ids():
            row = self._installation(plugin_id)
            record = self.records[plugin_id]
            if (
                not row.enabled
                or not record.loaded
                or record.instance is None
                or "before_prompt_compile" not in record.manifest.hooks
            ):
                continue
            handler = getattr(record.instance, "preview_prompt_compile", None)
            if not callable(handler):
                handler = getattr(record.instance, "before_prompt_compile", None)
            if not callable(handler):
                continue
            context = PluginContext(self, plugin_id, record.path, read_only=True)
            try:
                value = handler(context, normalized_event)
                if pyinspect.isawaitable(value):
                    value = await value
                result = self._normalize_result(value)
                for action in result.actions:
                    if action.kind not in {"prompt_addition", "history_filter"}:
                        continue
                    payload = sanitize_plugin_data(action.payload)
                    if action.kind == "prompt_addition":
                        payload.setdefault("source", plugin_id)
                    output.append(
                        {
                            "plugin_id": plugin_id,
                            "plugin_name": record.manifest.name,
                            "action": PluginAction(kind=action.kind, payload=payload),
                        }
                    )
            except Exception as exc:
                LOGGER.warning(
                    "插件 %s 的提示词预览已跳过：%s: %s",
                    plugin_id,
                    type(exc).__name__,
                    exc,
                )
        return output

    def list_plugins(self) -> list[dict[str, Any]]:
        output = []
        for plugin_id in self._ordered_plugin_ids():
            record = self.records[plugin_id]
            row = self._installation(plugin_id)
            settings = dict(row.settings or {})
            secret_settings_configured: dict[str, bool] = {}
            for key, definition in record.manifest.settings_schema.get("properties", {}).items():
                if definition.get("format") == "password":
                    secret_settings_configured[key] = bool(settings.get(key))
                    settings[key] = ""
            output.append(
                {
                    **record.manifest.model_dump(),
                    "built_in": record.built_in,
                    "enabled": row.enabled,
                    "position": row.position,
                    "loaded": record.loaded,
                    "status": "运行中" if record.loaded else ("已停用" if not row.enabled else "加载失败"),
                    "settings": settings,
                    "secret_settings_configured": secret_settings_configured,
                    "last_error": row.last_error,
                }
            )
        return output

    def validate_settings(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件不存在")
        properties = record.manifest.settings_schema.get("properties", {})
        unknown = sorted(set(values) - set(properties))
        if unknown:
            raise PluginError(f"未知设置项：{', '.join(unknown)}")
        normalized = {**record.manifest.default_settings(), **values}
        for key, definition in properties.items():
            if key not in normalized:
                continue
            value = normalized[key]
            expected = definition["type"]
            valid = (
                (expected == "boolean" and isinstance(value, bool))
                or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
                or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                or (expected == "string" and isinstance(value, str))
            )
            if not valid:
                raise PluginError(f"设置项 {key} 类型无效")
            if "enum" in definition and value not in definition["enum"]:
                raise PluginError(f"设置项 {key} 不在允许范围内")
            if isinstance(value, (int, float)):
                if "minimum" in definition and value < definition["minimum"]:
                    raise PluginError(f"设置项 {key} 不能小于 {definition['minimum']}")
                if "maximum" in definition and value > definition["maximum"]:
                    raise PluginError(f"设置项 {key} 不能大于 {definition['maximum']}")
            if isinstance(value, str) and "maxLength" in definition:
                if len(value) > definition["maxLength"]:
                    raise PluginError(f"设置项 {key} 不能超过 {definition['maxLength']} 个字符")
        return normalized

    async def configure(
        self,
        plugin_id: str,
        enabled: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        row = self._installation(plugin_id)
        was_enabled = row.enabled
        normalized = self.validate_settings(plugin_id, settings) if settings is not None else None
        if was_enabled and (enabled is False or normalized is not None):
            await self.stop(plugin_id)
        with self.database.session_factory() as session:
            stored = session.get(PluginInstallation, plugin_id)
            if stored is None:
                raise PluginError("插件未安装")
            if enabled is not None:
                stored.enabled = enabled
            if normalized is not None:
                stored_settings = dict(normalized)
                properties = self.records[plugin_id].manifest.settings_schema.get("properties", {})
                for key, definition in properties.items():
                    if definition.get("format") != "password":
                        continue
                    new_value = str((settings or {}).get(key, "")).strip()
                    if new_value:
                        stored_settings[key] = self.secret_box.encrypt(new_value)
                    else:
                        stored_settings[key] = str((stored.settings or {}).get(key, ""))
                stored.settings = stored_settings
            session.commit()
        should_enable = enabled if enabled is not None else was_enabled
        if should_enable and (not was_enabled or normalized is not None):
            await self._load_and_start(plugin_id)

    async def stop(self, plugin_id: str) -> None:
        record = self.records.get(plugin_id)
        if record is not None and record.loaded:
            await self._invoke(plugin_id, "on_shutdown", PluginEvent(name="on_shutdown"))
        for key in [key for key in self.tasks if key[0] == plugin_id]:
            self.cancel_schedule(*key)
        if record is not None:
            record.loaded = False
            record.instance = None

    async def reload(self, plugin_id: str) -> None:
        row = self._installation(plugin_id)
        await self.stop(plugin_id)
        self.discover()
        if row.enabled and plugin_id in self.records:
            await self._load_and_start(plugin_id)

    def schedule_interval(
        self,
        plugin_id: str,
        name: str,
        seconds: float,
        callback: Callable[[PluginContext], Any | Awaitable[Any]],
    ) -> None:
        if seconds < 1:
            raise ValueError("定时任务间隔不能小于 1 秒")
        key = (plugin_id, name)
        self.cancel_schedule(plugin_id, name)

        async def runner() -> None:
            while True:
                await asyncio.sleep(seconds)
                try:
                    context = PluginContext(self, plugin_id, self.records[plugin_id].path)
                    value = callback(context)
                    if pyinspect.isawaitable(value):
                        value = await value
                    result = self._normalize_result(value)
                    for action in result.actions:
                        await self._emit(plugin_id, action)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._set_error(plugin_id, f"定时任务 {name}: {type(exc).__name__}: {exc}")
                    LOGGER.exception("插件 %s 的定时任务 %s 失败", plugin_id, name)

        self.tasks[key] = asyncio.create_task(runner(), name=f"plugin:{plugin_id}:{name}")

    def cancel_schedule(self, plugin_id: str, name: str) -> None:
        task = self.tasks.pop((plugin_id, name), None)
        if task is not None:
            task.cancel()

    async def install_zip(self, archive: bytes) -> str:
        if not archive or len(archive) > MAX_ARCHIVE_BYTES:
            raise PluginError("插件压缩包为空或超过 32 MB")
        try:
            bundle = zipfile.ZipFile(BytesIO(archive))
        except zipfile.BadZipFile as exc:
            raise PluginError("文件不是有效的 ZIP 压缩包") from exc
        with bundle:
            files = [item for item in bundle.infolist() if not item.is_dir()]
            if not files or len(files) > MAX_ARCHIVE_FILES:
                raise PluginError("插件压缩包文件数量无效")
            if sum(item.file_size for item in files) > MAX_EXTRACTED_BYTES:
                raise PluginError("插件解压后超过 128 MB")
            normalized: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            seen_paths: set[PurePosixPath] = set()
            for item in files:
                name = item.filename.replace("\\", "/")
                path = PurePosixPath(name)
                mode = item.external_attr >> 16
                if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                    raise PluginError("插件压缩包包含越界路径")
                if stat.S_ISLNK(mode):
                    raise PluginError("插件压缩包不能包含符号链接")
                if item.flag_bits & 0x1:
                    raise PluginError("插件压缩包不能包含加密文件")
                if path in seen_paths:
                    raise PluginError("插件压缩包包含重复路径")
                seen_paths.add(path)
                normalized.append((item, path))
            manifest_paths = [path for _, path in normalized if path.name == "plugin.json"]
            if len(manifest_paths) != 1:
                raise PluginError("压缩包必须且只能包含一个 plugin.json")
            manifest_path = manifest_paths[0]
            prefix = manifest_path.parent
            if any(path.parts[: len(prefix.parts)] != prefix.parts for _, path in normalized):
                raise PluginError("plugin.json 必须位于压缩包根目录或唯一的顶层目录中")
            manifest_info = next(item for item, path in normalized if path == manifest_path)
            try:
                manifest = PluginManifest.model_validate_json(bundle.read(manifest_info))
            except (UnicodeDecodeError, ValidationError) as exc:
                raise PluginError(f"插件清单无效：{exc}") from exc
            _ensure_app_version_supported(manifest)
            if manifest.id in self.records and self.records[manifest.id].built_in:
                raise PluginError("不能覆盖内置插件")

            self.installed_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="catgirl-plugin-", dir=self.installed_dir.parent) as temp:
                stage = Path(temp) / manifest.id
                stage.mkdir()
                for item, path in normalized:
                    relative_parts = path.parts[len(prefix.parts):]
                    if not relative_parts:
                        continue
                    destination = stage.joinpath(*relative_parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(bundle.read(item))
                parsed = PluginManifest.model_validate_json((stage / "plugin.json").read_text("utf-8"))
                if parsed.id != manifest.id or not (stage / parsed.entrypoint).is_file():
                    raise PluginError("插件入口文件不存在")

                target = (self.installed_dir / manifest.id).resolve()
                if target.parent != self.installed_dir.resolve() or not PLUGIN_ID_PATTERN.fullmatch(manifest.id):
                    raise PluginError("插件安装路径无效")
                await self.stop(manifest.id)
                backup = target.with_name(f".{manifest.id}.backup")
                if backup.exists():
                    shutil.rmtree(backup)
                if target.exists():
                    os.replace(target, backup)
                try:
                    shutil.copytree(stage, target)
                except Exception:
                    if backup.exists():
                        os.replace(backup, target)
                    raise
                if backup.exists():
                    shutil.rmtree(backup)

        self.discover()
        row = self._installation(manifest.id)
        if row.enabled:
            await self._load_and_start(manifest.id)
        return manifest.id

    async def uninstall(self, plugin_id: str) -> None:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError("插件不存在")
        if record.built_in:
            raise PluginError("内置插件不能卸载，可以将其停用")
        await self.stop(plugin_id)
        target = record.path.resolve()
        if target.parent != self.installed_dir.resolve():
            raise PluginError("插件目录不在安装区")
        shutil.rmtree(target)
        with self.database.session_factory() as session:
            row = session.get(PluginInstallation, plugin_id)
            if row is not None:
                session.delete(row)
                session.commit()
        self.discover()
