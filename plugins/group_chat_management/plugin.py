from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from typing import Any

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


STATE_VERSION = 2
MAX_WORDS_PER_SCOPE = 500
MAX_WORD_LENGTH = 120
GROUP_ADMIN_ROLES = {"owner", "admin"}
COMMAND_PLACEHOLDER = "xxx"
DEFAULT_COMMANDS = {
    "add_command": "/添加屏蔽词 xxx",
    "remove_command": "/移除屏蔽词 xxx",
    "list_command": "/屏蔽词列表",
    "clear_command": "/清空屏蔽词",
}


def _normalized_words(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    words: list[str] = []
    seen: set[str] = set()
    for raw_word in value:
        word = str(raw_word).strip()
        key = word.casefold()
        if not word or len(word) > MAX_WORD_LENGTH or key in seen:
            continue
        if any(character in "\r\n\x00" for character in word):
            continue
        words.append(word)
        seen.add(key)
        if len(words) >= MAX_WORDS_PER_SCOPE:
            break
    return words


def _normalized_state(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    groups: dict[str, dict[str, list[str]]] = {}
    raw_groups = source.get("groups")
    if isinstance(raw_groups, dict):
        for raw_group_id, raw_entry in raw_groups.items():
            group_id = str(raw_group_id).strip()
            if not group_id or not isinstance(raw_entry, dict):
                continue
            words = _normalized_words(raw_entry.get("blocked_words"))
            if words:
                groups[group_id] = {"blocked_words": words}
    return {
        "version": STATE_VERSION,
        "global_words": _normalized_words(source.get("global_words")),
        "groups": groups,
    }


def _group_id(event: PluginEvent) -> str:
    group_id = str(event.metadata.get("group_id") or "").strip()
    if group_id:
        return group_id
    marker = ":group:"
    return event.conversation_id.rsplit(marker, 1)[-1] if marker in event.conversation_id else ""


def _send(event: PluginEvent, text: str) -> PluginResult:
    return PluginResult(
        consume=True,
        actions=[
            PluginAction(
                kind="send_text",
                payload={"conversation_id": event.conversation_id, "text": text},
            )
        ],
    )


def _commands(settings: dict[str, Any]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for key, default in DEFAULT_COMMANDS.items():
        value = str(settings.get(key, default)).strip()
        if not value or len(value) > MAX_WORD_LENGTH or any(char in value for char in "\r\n\x00"):
            value = default
        if key in {"add_command", "remove_command"}:
            marker_index = value.casefold().find(COMMAND_PLACEHOLDER)
            if marker_index >= 0:
                command_text = value[:marker_index] + value[marker_index + len(COMMAND_PLACEHOLDER) :]
                if not command_text.strip():
                    value = default
        commands[key] = value
    return commands


def _command_stem(template: str) -> str:
    marker_index = template.casefold().find(COMMAND_PLACEHOLDER)
    if marker_index < 0:
        return template.rstrip()
    return template[:marker_index].rstrip()


def _starts_with_command(text: str, template: str) -> bool:
    stem = _command_stem(template)
    if text == stem:
        return True
    return text.startswith(stem) and bool(text[len(stem) :]) and text[len(stem)].isspace()


def _is_management_command(text: str, commands: dict[str, str]) -> bool:
    clear_command = commands["clear_command"]
    return (
        text
        in {
            commands["list_command"],
            clear_command,
            f"{clear_command} 确认",
        }
        or _starts_with_command(text, commands["add_command"])
        or _starts_with_command(text, commands["remove_command"])
    )


def _match_value_command(text: str, template: str) -> str | None:
    marker_index = template.casefold().find(COMMAND_PLACEHOLDER)
    if marker_index < 0:
        match = re.fullmatch(re.escape(template) + r"\s+([\s\S]+)", text)
    else:
        before = template[:marker_index]
        after = template[marker_index + len(COMMAND_PLACEHOLDER) :]
        match = re.fullmatch(
            re.escape(before) + r"([\s\S]+?)" + re.escape(after),
            text,
        )
    return match.group(1) if match else None


def _command_usage(template: str) -> str:
    if COMMAND_PLACEHOLDER in template.casefold():
        return template
    return f"{template} {COMMAND_PLACEHOLDER}"


def _validate_word(raw_word: str) -> str:
    word = raw_word.strip()
    if not word:
        raise ValueError("屏蔽词不能为空")
    if len(word) > MAX_WORD_LENGTH:
        raise ValueError(f"单个屏蔽词不能超过 {MAX_WORD_LENGTH} 个字符")
    if any(character in "\r\n\x00" for character in word):
        raise ValueError("屏蔽词不能包含换行或空字符")
    return word


def _combined_words(state: dict[str, Any], group_id: str) -> list[str]:
    combined: list[str] = []
    seen: set[str] = set()
    group_words = (state["groups"].get(group_id) or {}).get("blocked_words") or []
    for word in [*state["global_words"], *group_words]:
        key = word.casefold()
        if key in seen:
            continue
        combined.append(word)
        seen.add(key)
    return combined


def _replacement_symbol(value: Any) -> str:
    replacement = str(value) if value is not None else "*"
    return replacement[:1]


def _replace_blocked_words(text: str, words: list[str], symbol: str) -> str:
    if not text or not words:
        return text
    ordered = sorted(words, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(word) for word in ordered), re.IGNORECASE)
    return pattern.sub(lambda match: symbol * len(match.group(0)), text)


class GroupChatManagementPlugin:
    def __init__(self) -> None:
        self._state_lock = asyncio.Lock()

    @staticmethod
    def normalize_state(state: Any) -> dict[str, Any]:
        return _normalized_state(state)

    async def _handle_command(
        self,
        context,
        event: PluginEvent,
        group_id: str,
        command: str,
        commands: dict[str, str],
    ) -> PluginResult:
        if str(event.metadata.get("sender_role") or "") not in GROUP_ADMIN_ROLES:
            return _send(event, "仅群主或群管理员可以管理屏蔽词。")

        async with self._state_lock:
            state = _normalized_state(context.state)
            groups = state["groups"]
            entry = deepcopy(groups.get(group_id) or {"blocked_words": []})
            words = list(entry["blocked_words"])

            if command == commands["list_command"]:
                if not words:
                    return _send(event, "本群尚未设置屏蔽词。")
                listing = "\n".join(f"{index}. {word}" for index, word in enumerate(words, 1))
                return _send(event, f"本群屏蔽词（{len(words)} 个）：\n{listing}")

            clear_command = commands["clear_command"]
            if command == clear_command:
                return _send(
                    event,
                    f"该操作会清空本群全部屏蔽词，请发送 {clear_command} 确认。",
                )
            if command == f"{clear_command} 确认":
                groups.pop(group_id, None)
                context.replace_state(state)
                return _send(event, "已清空本群全部屏蔽词。")

            raw_word = _match_value_command(command, commands["add_command"])
            if raw_word is not None:
                try:
                    word = _validate_word(raw_word)
                except ValueError as exc:
                    return _send(event, str(exc))
                if any(existing.casefold() == word.casefold() for existing in words):
                    return _send(event, f"屏蔽词“{word}”已经存在。")
                if len(words) >= MAX_WORDS_PER_SCOPE:
                    return _send(event, f"本群最多设置 {MAX_WORDS_PER_SCOPE} 个屏蔽词。")
                words.append(word)
                groups[group_id] = {"blocked_words": words}
                context.replace_state(state)
                return _send(event, f"已添加屏蔽词“{word}”。")

            raw_word = _match_value_command(command, commands["remove_command"])
            if raw_word is not None:
                try:
                    word = _validate_word(raw_word)
                except ValueError as exc:
                    return _send(event, str(exc))
                remaining = [item for item in words if item.casefold() != word.casefold()]
                if len(remaining) == len(words):
                    return _send(event, f"未找到屏蔽词“{word}”。")
                if remaining:
                    groups[group_id] = {"blocked_words": remaining}
                else:
                    groups.pop(group_id, None)
                context.replace_state(state)
                return _send(event, f"已移除屏蔽词“{word}”。")

        if _starts_with_command(command, commands["add_command"]):
            return _send(event, f"用法：{_command_usage(commands['add_command'])}")
        if _starts_with_command(command, commands["remove_command"]):
            return _send(event, f"用法：{_command_usage(commands['remove_command'])}")
        return _send(event, "无法识别群聊管理命令。")

    async def before_qq_message(self, context, event: PluginEvent) -> PluginResult:
        if str(event.metadata.get("message_type") or "") != "group":
            return PluginResult()
        group_id = _group_id(event)
        if not group_id:
            return PluginResult()

        text = event.text.strip()
        commands = _commands(context.settings)
        if _is_management_command(text, commands):
            return await self._handle_command(context, event, group_id, text, commands)

        if bool(context.settings.get("require_mention", False)) and not bool(
            event.metadata.get("mentioned_self", False)
        ):
            return PluginResult(consume=True, metadata={"discard_reason": "mention_required"})

        wake_prefix = str(context.settings.get("wake_prefix", "")).strip()
        if wake_prefix:
            candidate = event.text.lstrip()
            if not candidate.startswith(wake_prefix):
                return PluginResult(consume=True, metadata={"discard_reason": "wake_prefix_required"})
            text = candidate[len(wake_prefix) :].lstrip()
        else:
            text = event.text

        state = _normalized_state(context.state)
        words = _combined_words(state, group_id)
        symbol = _replacement_symbol(context.settings.get("censor_replacement", "*"))
        filtered = _replace_blocked_words(text, words, symbol)
        if not filtered.strip() and not bool(event.metadata.get("has_media", False)):
            return PluginResult(consume=True, metadata={"discard_reason": "empty_after_censor"})
        return PluginResult(metadata={"inbound_text": filtered})


plugin = GroupChatManagementPlugin()
