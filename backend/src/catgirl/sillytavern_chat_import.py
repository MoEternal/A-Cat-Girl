from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Literal

from .media import MediaValidationError, ensure_history_content_safe


MAX_CHAT_IMPORT_BYTES = 32 * 1024 * 1024
MAX_CHAT_IMPORT_MESSAGES = 50_000
MAX_CHAT_IMPORT_MESSAGE_CHARS = 100_000
_MAX_WARNINGS = 20
_DATA_BASE64_PATTERN = re.compile(r"data:[^,\s]{1,160};base64,", re.IGNORECASE)
_LEGACY_DATE_PATTERN = re.compile(
    r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*@\s*"
    r"(\d{1,2})h\s*(\d{1,2})m\s*(\d{1,2})s(?:\s*(\d{1,3})ms)?\s*$"
)


class SillyTavernChatImportError(ValueError):
    pass


@dataclass(frozen=True)
class SillyTavernChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str
    created_at: datetime | None
    model: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SillyTavernChat:
    title: str
    user_name: str
    character_name: str
    created_at: datetime | None
    messages: tuple[SillyTavernChatMessage, ...]
    skipped_messages: int
    warnings: tuple[str, ...]


def _append_warning(warnings: list[str], message: str) -> None:
    if len(warnings) < _MAX_WARNINGS:
        warnings.append(message)
    elif len(warnings) == _MAX_WARNINGS:
        warnings.append("其余导入提示已省略")


def _decode_records(data: bytes) -> list[dict[str, Any]]:
    if not data:
        raise SillyTavernChatImportError("聊天记录文件为空")
    if len(data) > MAX_CHAT_IMPORT_BYTES:
        raise SillyTavernChatImportError("聊天记录文件不能超过 32 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SillyTavernChatImportError("聊天记录文件必须使用 UTF-8 编码") from exc
    if not text.strip():
        raise SillyTavernChatImportError("聊天记录文件为空")

    parsed: Any
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, RecursionError) as exc:
                raise SillyTavernChatImportError(
                    f"聊天记录第 {line_number} 行不是有效 JSON"
                ) from exc
            if not isinstance(item, dict):
                raise SillyTavernChatImportError(
                    f"聊天记录第 {line_number} 行格式无效"
                )
            records.append(item)
            if len(records) > MAX_CHAT_IMPORT_MESSAGES + 1:
                raise SillyTavernChatImportError(
                    f"单份聊天记录最多导入 {MAX_CHAT_IMPORT_MESSAGES:,} 条消息"
                )
        return records

    if isinstance(parsed, list):
        raw_records = parsed
    elif isinstance(parsed, dict):
        message_list = parsed.get("messages")
        if not isinstance(message_list, list):
            message_list = parsed.get("chat")
        if isinstance(message_list, list):
            header = {
                key: parsed[key]
                for key in ("user_name", "character_name", "create_date", "chat_metadata")
                if key in parsed
            }
            raw_records = ([header] if header else []) + message_list
        else:
            raw_records = [parsed]
    else:
        raise SillyTavernChatImportError("无法识别聊天记录文件结构")

    if len(raw_records) > MAX_CHAT_IMPORT_MESSAGES + 1:
        raise SillyTavernChatImportError(
            f"单份聊天记录最多导入 {MAX_CHAT_IMPORT_MESSAGES:,} 条消息"
        )
    records = []
    for index, item in enumerate(raw_records, 1):
        if not isinstance(item, dict):
            raise SillyTavernChatImportError(f"聊天记录第 {index} 项格式无效")
        records.append(item)
    return records


def _looks_like_message(item: dict[str, Any]) -> bool:
    return bool(
        {"mes", "is_user", "is_system", "swipes", "role", "content"}.intersection(item)
    )


def _bounded_name(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _title_from_file_name(file_name: str) -> str:
    normalized = str(file_name or "").replace("\\", "/")
    name = PurePosixPath(normalized).name.strip()
    lowered = name.lower()
    for suffix in (".jsonl", ".json"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return (name.strip() or "导入记录")[:240]


def _local_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        local_zone = datetime.now().astimezone().tzinfo or timezone.utc
        value = value.replace(tzinfo=local_zone)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 100_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    source = value.strip()
    if not source or len(source) > 160:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", source):
        try:
            return _parse_timestamp(float(source))
        except ValueError:
            return None
    legacy = _LEGACY_DATE_PATTERN.fullmatch(source)
    if legacy is not None:
        year, month, day, hour, minute, second, milliseconds = legacy.groups()
        try:
            parsed = datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                int(second),
                int(milliseconds or 0) * 1000,
            )
            return _local_to_utc(parsed)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(source[:-1] + "+00:00" if source.endswith("Z") else source)
        return _local_to_utc(parsed)
    except ValueError:
        pass
    for pattern in (
        "%B %d, %Y %I:%M%p",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y %I:%M%p",
        "%b %d, %Y %I:%M %p",
    ):
        try:
            return _local_to_utc(datetime.strptime(source, pattern))
        except ValueError:
            continue
    return None


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("message", "content"):
            nested = value.get(key)
            if isinstance(nested, str):
                return nested
    return ""


def _swipe_index(item: dict[str, Any]) -> int:
    value = item.get("swipe_id", 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _selected_mapping(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, list) or not 0 <= index < len(value):
        return {}
    selected = value[index]
    return selected if isinstance(selected, dict) else {}


def _first_string(*values: Any, limit: int | None = None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            result = value.strip()
            return result[:limit] if limit is not None else result
    return ""


def _safe_message_text(value: str, label: str) -> str:
    if len(value) > MAX_CHAT_IMPORT_MESSAGE_CHARS:
        raise SillyTavernChatImportError(
            f"{label}超过 {MAX_CHAT_IMPORT_MESSAGE_CHARS:,} 个字符"
        )
    if "\x00" in value:
        raise SillyTavernChatImportError(f"{label}包含不支持的空字符")
    if _DATA_BASE64_PATTERN.search(value):
        raise SillyTavernChatImportError(f"{label}包含不能写入聊天记录的 base64 数据")
    try:
        return ensure_history_content_safe(value)
    except MediaValidationError as exc:
        raise SillyTavernChatImportError(f"{label}包含不能写入聊天记录的图片数据") from exc


def _message_role(item: dict[str, Any], user_name: str) -> Literal["system", "user", "assistant"] | None:
    role = item.get("role")
    if role in {"system", "user", "assistant"}:
        return role
    if item.get("is_system") is True:
        return "system"
    if "is_user" in item:
        return "user" if item.get("is_user") is True else "assistant"
    if "mes" in item or "swipes" in item:
        name = _bounded_name(item.get("name"))
        return "user" if user_name and name == user_name else "assistant"
    return None


def parse_sillytavern_chat(data: bytes, file_name: str) -> SillyTavernChat:
    records = _decode_records(data)
    if not records:
        raise SillyTavernChatImportError("聊天记录文件中没有可导入内容")

    warnings: list[str] = []
    header: dict[str, Any] = {}
    start_index = 0
    if not _looks_like_message(records[0]):
        header = records[0]
        start_index = 1
    user_name = _bounded_name(header.get("user_name"))
    character_name = _bounded_name(header.get("character_name"))
    source_created_at = _parse_timestamp(header.get("create_date"))
    if header.get("create_date") not in (None, "") and source_created_at is None:
        _append_warning(warnings, "未能识别记录创建时间，已按导入时间保存")

    messages: list[SillyTavernChatMessage] = []
    skipped_messages = 0
    for source_index, item in enumerate(records[start_index:], start_index + 1):
        role = _message_role(item, user_name)
        if role is None:
            skipped_messages += 1
            _append_warning(warnings, f"第 {source_index} 项不是聊天消息，已跳过")
            continue

        swipe_id = _swipe_index(item)
        swipes = item.get("swipes")
        content = _message_text(item.get("mes", item.get("content")))
        if not content.strip() and isinstance(swipes, list) and 0 <= swipe_id < len(swipes):
            content = _message_text(swipes[swipe_id])
        if not content.strip():
            skipped_messages += 1
            _append_warning(warnings, f"第 {source_index} 条消息没有文本，已跳过")
            continue
        content = _safe_message_text(content, f"第 {source_index} 条消息")

        name = _bounded_name(item.get("name"))
        if role == "user" and not user_name and name:
            user_name = name
        if role == "assistant" and not character_name and name:
            character_name = name

        selected_swipe = _selected_mapping(item.get("swipe_info"), swipe_id)
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        selected_extra = (
            selected_swipe.get("extra")
            if isinstance(selected_swipe.get("extra"), dict)
            else {}
        )
        reasoning = _first_string(
            selected_extra.get("reasoning"),
            extra.get("reasoning"),
            item.get("reasoning"),
        )
        if reasoning:
            reasoning = _safe_message_text(reasoning, f"第 {source_index} 条消息的思考内容")
        model = _first_string(
            selected_extra.get("model"),
            extra.get("model"),
            item.get("model"),
            limit=160,
        )

        send_date = item.get("send_date")
        created_at = _parse_timestamp(send_date)
        if send_date not in (None, "") and created_at is None:
            _append_warning(warnings, f"第 {source_index} 条消息的时间无法识别")

        metadata: dict[str, Any] = {"import_format": "sillytavern_chat"}
        if name:
            metadata["sillytavern_name"] = name
        if role == "user" and (name or user_name):
            metadata["user_persona_name"] = name or user_name
        elif role == "assistant" and (name or character_name):
            metadata["character_name"] = name or character_name
        if role == "system" and name:
            metadata["speaker_name"] = name
        if reasoning:
            metadata["reasoning"] = reasoning
        if isinstance(swipes, list):
            metadata["sillytavern_swipe_id"] = swipe_id
            metadata["sillytavern_swipe_count"] = len(swipes)
        api_name = _first_string(selected_extra.get("api"), extra.get("api"), limit=80)
        if api_name:
            metadata["sillytavern_api"] = api_name
        gen_id = item.get("gen_id", extra.get("gen_id"))
        if isinstance(gen_id, (str, int)) and not isinstance(gen_id, bool):
            metadata["sillytavern_gen_id"] = str(gen_id)[:160]
        if send_date not in (None, ""):
            metadata["sillytavern_send_date"] = str(send_date)[:160]

        messages.append(
            SillyTavernChatMessage(
                role=role,
                content=content,
                created_at=created_at,
                model=model,
                metadata=metadata,
            )
        )
        if len(messages) > MAX_CHAT_IMPORT_MESSAGES:
            raise SillyTavernChatImportError(
                f"单份聊天记录最多导入 {MAX_CHAT_IMPORT_MESSAGES:,} 条消息"
            )

    if not messages:
        raise SillyTavernChatImportError("聊天记录文件中没有可导入的文本消息")
    return SillyTavernChat(
        title=_title_from_file_name(file_name),
        user_name=user_name,
        character_name=character_name,
        created_at=source_created_at,
        messages=tuple(messages),
        skipped_messages=skipped_messages,
        warnings=tuple(warnings),
    )
