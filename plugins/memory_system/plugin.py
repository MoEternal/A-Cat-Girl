from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from catgirl.database import ChatMessage, Conversation
from catgirl.plugins import PluginAction, PluginEvent, PluginResult
from catgirl.plugins.file_memory import FileMemoryStore


COMMAND_PREFIX = "/记忆"
LOGGER = logging.getLogger("catgirl.plugins.memory_system")
STAGES = {"陌生", "相识", "在意", "暧昧", "信赖", "恋人", "挚爱", "决裂"}
ARC_KINDS = {"romance", "battle", "exploration", "mystery", "daily", "social", "growth", "other"}
EVENT_KINDS = ARC_KINDS | {"promise", "secret", "relationship"}
ARC_STATUSES = {"active", "paused", "completed", "failed"}
PROMISE_STATUSES = {"pending", "kept", "broken", "cancelled"}
WORD_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u9fff]+")


def _empty_tables() -> dict[str, dict[str, Any]]:
    """Create the blank table layout stored with each conversation's memory state."""
    return {
        "spacetime": {
            "title": "时空定位",
            "columns": ["日期", "时间", "地点", "在场角色", "当前描述"],
            "rows": [],
        },
        "characters": {
            "title": "角色档案",
            "columns": ["角色名", "身体特征", "性格", "职业或身份", "兴趣", "喜欢的事物", "住处", "其他重要信息"],
            "rows": [],
        },
        "user_relationships": {
            "title": "与当前视角的关系",
            "columns": ["角色名", "关系", "当前态度", "好感", "信赖", "嫉妒"],
            "rows": [],
        },
        "tasks": {
            "title": "任务与约定",
            "columns": ["参与角色", "任务或约定", "地点", "持续时间", "状态"],
            "rows": [],
        },
        "events": {
            "title": "重要事件",
            "columns": ["参与角色", "事件简述", "日期或时间", "地点", "情绪或后果"],
            "rows": [],
        },
        "items": {
            "title": "重要物品",
            "columns": ["拥有人", "物品名", "状态", "所在位置", "重要原因"],
            "rows": [],
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _real_time_label(event: PluginEvent) -> str:
    prompt_metadata = event.metadata.get("prompt_hook_metadata")
    if not isinstance(prompt_metadata, dict):
        return ""
    time_context = prompt_metadata.get("time_awareness")
    if not isinstance(time_context, dict):
        return ""
    return _text(time_context.get("memory_time"), 120)


def _apply_real_time(payload: dict[str, Any], label: str) -> None:
    if not label:
        return
    scene = payload.get("scene")
    if not isinstance(scene, dict):
        scene = {}
        payload["scene"] = scene
    scene["story_time"] = label
    events = payload.get("events")
    if isinstance(events, list):
        for item in events:
            if isinstance(item, dict):
                item["story_time"] = label


def _name_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", _text(value).lower())


def _string_list(value: Any, *, limit: int = 30, item_limit: int = 120) -> list[str]:
    source = value if isinstance(value, list) else [value]
    output: list[str] = []
    seen: set[str] = set()
    for item in source:
        cleaned = _text(item, item_limit)
        key = _name_key(cleaned)
        if not cleaned or key in seen:
            continue
        output.append(cleaned)
        seen.add(key)
        if len(output) >= limit:
            break
    return output


def _split_names(value: str) -> list[str]:
    return _string_list(re.split(r"[,，、\n]", value), limit=40, item_limit=80)


def _clamp(value: Any, low: int, high: int, fallback: int = 0) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _default_state() -> dict[str, Any]:
    return {
        "version": 2,
        "turn": 0,
        "lead_overrides": [],
        "main_arc": {
            "summary": "",
            "phase": "",
            "central_conflict": "",
            "next_pressure": "",
            "last_turn": 0,
        },
        "last_scene": {"story_time": "", "location": "", "summary": ""},
        "tables": _empty_tables(),
        "characters": [],
        "relationships": [],
        "arcs": [],
        "events": [],
        "memories": [],
        "facts": [],
        "promises": [],
        "items": [],
        "archives": [],
        "saga_summary": "",
        "pinned": [],
        "counters": {},
        "last_analysis_error": "",
        "updated_at": "",
    }


def _normalize_state(value: Any) -> dict[str, Any]:
    state = _default_state()
    if isinstance(value, dict):
        state.update(value)
    for key in (
        "characters",
        "relationships",
        "arcs",
        "events",
        "memories",
        "facts",
        "promises",
        "items",
        "archives",
        "pinned",
    ):
        if not isinstance(state.get(key), list):
            state[key] = []
    if not isinstance(state.get("main_arc"), dict):
        state["main_arc"] = _default_state()["main_arc"]
    if not isinstance(state.get("last_scene"), dict):
        state["last_scene"] = _default_state()["last_scene"]
    if not isinstance(state.get("counters"), dict):
        state["counters"] = {}
    tables = state.get("tables")
    if not isinstance(tables, dict):
        state["tables"] = _empty_tables()
    else:
        state["tables"] = {
            key: {
                "title": definition["title"],
                "columns": definition["columns"],
                "rows": value.get("rows", []) if isinstance(value, dict) and isinstance(value.get("rows"), list) else [],
            }
            for key, definition in _empty_tables().items()
            for value in [tables.get(key)]
        }
    legacy = _text(state.pop("heroine_override", ""), 80)
    if not isinstance(state.get("lead_overrides"), list):
        state["lead_overrides"] = []
    if legacy and not state["lead_overrides"]:
        state["lead_overrides"] = [legacy]
    state["turn"] = max(0, _clamp(state.get("turn"), 0, 10_000_000))
    return state


def _memory_store(context) -> FileMemoryStore:
    return FileMemoryStore(
        Path(context.memory_path),
        default_factory=_default_state,
        normalize=_normalize_state,
    )


def _memory_name_for_conversation(conversation: Conversation) -> str:
    route = str(conversation.external_id or "")
    parts = route.split(":")
    if len(parts) == 4 and parts[0] == "qq":
        route = ("私聊" if parts[2] == "private" else "群聊") + f" {parts[3]}"
    title = str(conversation.title or conversation.external_id or "聊天记录")
    prefix = f"{route} · " if route and route != title else ""
    return f"{prefix}{title} 的记忆"


def _conversation_name(context, conversation_id: str) -> str:
    with context._manager.database.session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            return "独立记忆"
        return _memory_name_for_conversation(conversation)


def _empty_memory_state(state: dict[str, Any]) -> bool:
    return _normalize_state(state) == _normalize_state(_default_state())


def _reconcile_memories(context, conversations: list[Conversation]) -> dict[str, Any]:
    store = _memory_store(context)
    store.reconcile(
        {conversation.id for conversation in conversations},
        delete_if_unbound=_empty_memory_state,
    )
    for conversation in conversations:
        store.sync_bound_name(
            conversation.id,
            _memory_name_for_conversation(conversation),
            legacy_suffixes=(" 的记忆",),
        )
    return store.overview()


def _migrate_file_state(context, conversation_id: str) -> tuple[str, dict[str, Any]]:
    legacy = context._manager.get_database_conversation_state(context.plugin_id, conversation_id)
    memory_id, state = _memory_store(context).migrate_conversation_state(
        conversation_id,
        legacy,
        name=_conversation_name(context, conversation_id),
    )
    context._manager.delete_database_conversation_state(context.plugin_id, conversation_id)
    return memory_id, state


def _next_id(state: dict[str, Any], kind: str) -> str:
    counters = state["counters"]
    counters[kind] = _clamp(counters.get(kind), 0, 10_000_000) + 1
    return f"{kind}_{counters[kind]:06d}"


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型记忆结果不是 JSON 对象")
    return value


def _terms(text: str) -> list[str]:
    tokens: list[str] = []
    for match in WORD_PATTERN.findall(text.lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", match):
            tokens.extend(match)
            tokens.extend(match[index : index + 2] for index in range(len(match) - 1))
        else:
            tokens.append(match)
    return [token for token in tokens if token.strip()]


def _record_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "name",
        "owner",
        "subject",
        "detail",
        "summary",
        "content",
        "title",
        "location",
        "status",
        "current_state",
        "current_emotion",
        "current_goal",
        "current_outfit",
        "physical_traits",
        "personality",
        "occupation",
        "residence",
        "user_relationship",
        "user_attitude",
    ):
        value = record.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("aliases", "participants", "known_by", "keywords", "parties", "open_threads", "injuries", "hobbies", "likes", "important_info"):
        value = record.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return " ".join(parts)


def _rank_records(
    query: str,
    records: list[dict[str, Any]],
    limit: int,
    turn: int,
) -> list[dict[str, Any]]:
    if not records or limit <= 0:
        return []
    query_terms = _terms(query)
    if not query_terms:
        return sorted(records, key=lambda item: int(item.get("turn", 0)), reverse=True)[:limit]
    documents = [_terms(_record_text(item)) for item in records]
    frequencies = [Counter(document) for document in documents]
    document_frequency = Counter(term for document in documents for term in set(document))
    average_length = sum(len(document) for document in documents) / max(1, len(documents))
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, (record, document, frequency) in enumerate(zip(records, documents, frequencies)):
        score = 0.0
        for term in set(query_terms):
            count = frequency.get(term, 0)
            if not count:
                continue
            inverse = math.log(1 + (len(records) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            length_norm = 1 - 0.75 + 0.75 * len(document) / max(1.0, average_length)
            score += inverse * (count * 2.5) / (count + 1.5 * length_norm)
        importance = _clamp(record.get("importance"), 1, 5, 3)
        record_turn = record.get("turn", record.get("last_turn", turn))
        age = max(0, turn - _clamp(record_turn, 0, turn, turn))
        score += importance * 0.22 + 0.8 / (1 + age / 20)
        if record.get("pinned"):
            score += 20
        scored.append((score, index, record))
    return [item[2] for item in sorted(scored, key=lambda value: (-value[0], -value[1]))[:limit]]


def _find_named(records: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = _name_key(name)
    if not wanted:
        return None
    for record in records:
        names = [record.get("name", ""), *(record.get("aliases") or [])]
        if wanted in {_name_key(item) for item in names}:
            return record
    return None


def _lead_names(context, state: dict[str, Any]) -> list[str]:
    overrides = _string_list(state.get("lead_overrides"), limit=20, item_limit=80)
    if overrides:
        return overrides
    configured = _split_names(str(context.settings.get("lead_character_names", "")))
    if configured:
        return configured
    return _string_list(
        [
            item.get("name", "")
            for item in state.get("characters", [])
            if item.get("cast_role") in {"lead", "main_cast"}
        ],
        limit=8,
        item_limit=80,
    )


def _main_cast_names(context) -> set[str]:
    return {_name_key(item) for item in _split_names(str(context.settings.get("main_cast_names", "")))}


def _character_role(context, state: dict[str, Any], name: str, proposed: str) -> str:
    if _name_key(name) in {_name_key(item) for item in _lead_names(context, state)}:
        return "lead"
    if _name_key(name) in _main_cast_names(context):
        return "main_cast"
    return proposed if proposed in {"lead", "main_cast", "supporting", "protagonist", "other"} else "other"


def _merge_character(
    context,
    state: dict[str, Any],
    item: dict[str, Any],
    *,
    apply_relationship_deltas: bool = True,
) -> None:
    name = _text(item.get("name"), 80)
    if not name:
        return
    aliases = _string_list(item.get("aliases"), limit=12, item_limit=80)
    record = _find_named(state["characters"], name)
    if record is None:
        record = {
            "id": _next_id(state, "char"),
            "name": name,
            "aliases": aliases,
            "cast_role": "other",
            "relationship_stage": "陌生",
            "affection": 0,
            "trust": 0,
            "jealousy": 0,
            "current_state": "",
            "current_emotion": "",
            "current_goal": "",
            "current_outfit": "",
            "physical_traits": "",
            "personality": "",
            "occupation": "",
            "hobbies": [],
            "likes": [],
            "residence": "",
            "important_info": [],
            "user_relationship": "",
            "user_attitude": "",
            "injuries": [],
            "milestones": [],
            "last_turn": state["turn"],
        }
        state["characters"].append(record)
    record["aliases"] = _string_list([*(record.get("aliases") or []), *aliases], limit=12, item_limit=80)
    proposed_role = _text(item.get("cast_role"), 40) or _text(record.get("cast_role"), 40)
    record["cast_role"] = _character_role(context, state, name, proposed_role)
    stage = _text(item.get("relationship_stage"), 20)
    if stage in STAGES:
        record["relationship_stage"] = stage
    for field in (
        "current_state",
        "current_emotion",
        "current_goal",
        "current_outfit",
        "physical_traits",
        "personality",
        "occupation",
        "residence",
        "user_relationship",
        "user_attitude",
    ):
        value = _text(item.get(field), 400)
        if value:
            record[field] = value
    for field in ("hobbies", "likes", "important_info"):
        if isinstance(item.get(field), list):
            record[field] = _string_list(item.get(field), limit=12, item_limit=240)
    if "injuries" in item and isinstance(item.get("injuries"), list):
        record["injuries"] = _string_list(item.get("injuries"), limit=20, item_limit=240)
    for field in ("affection", "trust", "jealousy"):
        delta = _clamp(item.get(f"{field}_delta"), -10, 10) if apply_relationship_deltas else 0
        record[field] = _clamp(record.get(field), -100, 100) + delta
        record[field] = _clamp(record[field], -100, 100)
    milestone = _text(item.get("milestone"), 400)
    if milestone and milestone not in record["milestones"]:
        record["milestones"] = [*record["milestones"][-11:], milestone]
    record["last_turn"] = state["turn"]


def _merge_relationship(state: dict[str, Any], item: dict[str, Any]) -> None:
    source = _text(item.get("source"), 80)
    target = _text(item.get("target"), 80)
    if not source or not target or _name_key(source) == _name_key(target):
        return
    record = next(
        (
            entry
            for entry in state["relationships"]
            if _name_key(entry.get("source")) == _name_key(source)
            and _name_key(entry.get("target")) == _name_key(target)
        ),
        None,
    )
    if record is None:
        record = {"id": _next_id(state, "relation"), "source": source, "target": target}
        state["relationships"].append(record)
    for field, limit in (("relation", 160), ("attitude", 240), ("evidence", 300)):
        value = _text(item.get(field), limit)
        if value:
            record[field] = value
    if isinstance(item.get("known_by"), list):
        record["known_by"] = _string_list(item.get("known_by"), limit=40, item_limit=80)
    if "closeness" in item:
        record["closeness"] = _clamp(item.get("closeness"), -100, 100, record.get("closeness", 0))
    record["last_turn"] = state["turn"]


def _merge_arc(state: dict[str, Any], item: dict[str, Any]) -> None:
    title = _text(item.get("title"), 120)
    if not title:
        return
    record = next(
        (entry for entry in state["arcs"] if _name_key(entry.get("title")) == _name_key(title)),
        None,
    )
    if record is None:
        record = {"id": _next_id(state, "arc"), "title": title}
        state["arcs"].append(record)
    kind = _text(item.get("kind"), 30)
    status = _text(item.get("status"), 30)
    record.update(
        {
            "kind": kind if kind in ARC_KINDS else record.get("kind", "other"),
            "status": status if status in ARC_STATUSES else record.get("status", "active"),
            "summary": _text(item.get("summary"), 1200) or record.get("summary", ""),
            "importance": _clamp(item.get("importance"), 1, 5, record.get("importance", 3)),
            "last_turn": state["turn"],
        }
    )
    if isinstance(item.get("participants"), list):
        record["participants"] = _string_list(item.get("participants"), limit=30, item_limit=80)
    if isinstance(item.get("open_threads"), list):
        record["open_threads"] = _string_list(item.get("open_threads"), limit=20, item_limit=240)


def _append_unique(
    state: dict[str, Any],
    key: str,
    record: dict[str, Any],
    signature: str,
    *,
    recent_window: int = 80,
) -> None:
    normalized = _name_key(signature)
    if not normalized:
        return
    for existing in state[key][-recent_window:]:
        if key == "events":
            existing_signature = existing.get("summary", "")
        elif key == "memories":
            existing_signature = f"{existing.get('owner', '')}{existing.get('content', '')}"
        elif key == "facts":
            existing_signature = f"{existing.get('subject', '')}{existing.get('detail', '')}"
        else:
            existing_signature = _record_text(existing)
        if _name_key(existing_signature) == normalized:
            return
    state[key].append(record)


def _merge_payload(
    context,
    state: dict[str, Any],
    payload: dict[str, Any],
    *,
    advance_turn: bool = True,
    apply_relationship_deltas: bool = True,
) -> None:
    if advance_turn:
        state["turn"] += 1
    scene = payload.get("scene") if isinstance(payload.get("scene"), dict) else {}
    previous_scene = state.get("last_scene", {})
    state["last_scene"] = {
        "story_time": _text(scene.get("story_time"), 120) or previous_scene.get("story_time", ""),
        "location": _text(scene.get("location"), 160) or previous_scene.get("location", ""),
        "summary": _text(scene.get("summary"), 1200) or previous_scene.get("summary", ""),
    }
    main = payload.get("main_arc") if isinstance(payload.get("main_arc"), dict) else {}
    for field, limit in (("summary", 1600), ("phase", 120), ("central_conflict", 800), ("next_pressure", 800)):
        value = _text(main.get(field), limit)
        if value:
            state["main_arc"][field] = value
    if any(_text(main.get(field)) for field in ("summary", "phase", "central_conflict", "next_pressure")):
        state["main_arc"]["last_turn"] = state["turn"]

    for item in payload.get("characters", []) if isinstance(payload.get("characters"), list) else []:
        if isinstance(item, dict):
            _merge_character(
                context,
                state,
                item,
                apply_relationship_deltas=apply_relationship_deltas,
            )
    for item in payload.get("relationships", []) if isinstance(payload.get("relationships"), list) else []:
        if isinstance(item, dict):
            _merge_relationship(state, item)
    for item in payload.get("arcs", []) if isinstance(payload.get("arcs"), list) else []:
        if isinstance(item, dict):
            _merge_arc(state, item)

    for item in payload.get("events", []) if isinstance(payload.get("events"), list) else []:
        if not isinstance(item, dict):
            continue
        summary = _text(item.get("summary"), 1200)
        if not summary:
            continue
        kind = _text(item.get("kind"), 30)
        record = {
            "id": _next_id(state, "event"),
            "summary": summary,
            "kind": kind if kind in EVENT_KINDS else "other",
            "arc": _text(item.get("arc"), 120),
            "participants": _string_list(item.get("participants"), limit=30, item_limit=80),
            "known_by": _string_list(item.get("known_by"), limit=40, item_limit=80),
            "story_time": _text(item.get("story_time"), 120) or state["last_scene"]["story_time"],
            "location": _text(item.get("location"), 160) or state["last_scene"]["location"],
            "importance": _clamp(item.get("importance"), 1, 5, 3),
            "keywords": _string_list(item.get("keywords"), limit=20, item_limit=80),
            "evidence": _text(item.get("evidence"), 300),
            "turn": state["turn"],
        }
        _append_unique(state, "events", record, summary)

    for item in payload.get("memories", []) if isinstance(payload.get("memories"), list) else []:
        if not isinstance(item, dict):
            continue
        owner = _text(item.get("owner"), 80)
        content = _text(item.get("content"), 1000)
        if not owner or not content:
            continue
        record = {
            "id": _next_id(state, "memory"),
            "owner": owner,
            "content": content,
            "known_by": _string_list(item.get("known_by"), limit=40, item_limit=80) or [owner],
            "private": bool(item.get("private", False)),
            "emotion": _text(item.get("emotion"), 120),
            "importance": _clamp(item.get("importance"), 1, 5, 3),
            "keywords": _string_list(item.get("keywords"), limit=20, item_limit=80),
            "evidence": _text(item.get("evidence"), 300),
            "turn": state["turn"],
        }
        _append_unique(state, "memories", record, f"{owner}{content}")

    for item in payload.get("facts", []) if isinstance(payload.get("facts"), list) else []:
        if not isinstance(item, dict):
            continue
        subject = _text(item.get("subject"), 120)
        detail = _text(item.get("detail"), 1000)
        if not detail:
            continue
        record = {
            "id": _next_id(state, "fact"),
            "subject": subject,
            "category": _text(item.get("category"), 40) or "other",
            "detail": detail,
            "known_by": _string_list(item.get("known_by"), limit=40, item_limit=80),
            "importance": _clamp(item.get("importance"), 1, 5, 3),
            "keywords": _string_list(item.get("keywords"), limit=20, item_limit=80),
            "evidence": _text(item.get("evidence"), 300),
            "turn": state["turn"],
        }
        _append_unique(state, "facts", record, f"{subject}{detail}")

    for item in payload.get("promises", []) if isinstance(payload.get("promises"), list) else []:
        if not isinstance(item, dict):
            continue
        content = _text(item.get("content"), 700)
        parties = _string_list(item.get("parties"), limit=12, item_limit=80)
        if not content:
            continue
        existing = next(
            (
                entry for entry in state["promises"]
                if _name_key(entry.get("content")) == _name_key(content)
            ),
            None,
        )
        status = _text(item.get("status"), 30)
        if existing is None:
            existing = {
                "id": _next_id(state, "promise"),
                "content": content,
                "parties": parties,
                "known_by": _string_list(item.get("known_by"), limit=40, item_limit=80),
                "importance": _clamp(item.get("importance"), 1, 5, 4),
                "evidence": _text(item.get("evidence"), 300),
                "turn": state["turn"],
            }
            state["promises"].append(existing)
        existing["status"] = status if status in PROMISE_STATUSES else existing.get("status", "pending")
        existing["last_turn"] = state["turn"]

    for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 120)
        if not name:
            continue
        existing = next((entry for entry in state["items"] if _name_key(entry.get("name")) == _name_key(name)), None)
        if existing is None:
            existing = {"id": _next_id(state, "item"), "name": name}
            state["items"].append(existing)
        existing.update(
            {
                "owner": _text(item.get("owner"), 80) or existing.get("owner", ""),
                "status": _text(item.get("status"), 200) or existing.get("status", ""),
                "location": _text(item.get("location"), 160) or existing.get("location", ""),
                "importance": _clamp(item.get("importance"), 1, 5, existing.get("importance", 3)),
                "evidence": _text(item.get("evidence"), 300) or existing.get("evidence", ""),
                "last_turn": state["turn"],
            }
        )
    state["last_analysis_error"] = ""
    state["updated_at"] = _now()


def _catalog(state: dict[str, Any], lead_names: list[str]) -> str:
    characters = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "aliases": item.get("aliases", []),
            "role": item.get("cast_role"),
            "stage": item.get("relationship_stage"),
            "state": item.get("current_state"),
            "goal": item.get("current_goal"),
            "profile": {
                key: item.get(key)
                for key in ("physical_traits", "personality", "occupation", "hobbies", "likes", "residence", "important_info")
                if item.get(key)
            },
        }
        for item in state["characters"][-40:]
    ]
    arcs = [
        {key: item.get(key) for key in ("id", "title", "kind", "status", "summary", "open_threads")}
        for item in state["arcs"][-30:]
    ]
    promises = [
        {key: item.get(key) for key in ("id", "content", "parties", "status")}
        for item in state["promises"]
        if item.get("status", "pending") == "pending"
    ][-30:]
    relationships = [
        {key: item.get(key) for key in ("source", "target", "relation", "attitude", "closeness")}
        for item in state["relationships"][-80:]
    ]
    return json.dumps(
        {
            "core_characters": lead_names,
            "turn": state["turn"],
            "main_arc": state["main_arc"],
            "characters": characters,
            "active_arcs": arcs,
            "pending_promises": promises,
            "relationships": relationships,
        },
        ensure_ascii=False,
    )


def _analysis_messages(context, state: dict[str, Any], event: PluginEvent) -> list[dict[str, str]]:
    lead_names = _lead_names(context, state)
    pace = str(context.settings.get("relationship_pace", "balanced"))
    real_time = _real_time_label(event)
    system = f"""你是长篇角色剧情的结构化记忆整理器，不是续写模型。
只记录本轮用户消息和助手正文中明确发生、明确陈述或能够直接确认的事实；不得把计划、猜测、修辞、系统提示当成既成事实。
故事结构：情感/恋爱主线 + 任意战斗、探索、日常或悬疑支线；当前指定核心角色为“{'、'.join(lead_names) or '未指定，由剧情自动识别'}”，恋爱节奏为 {pace}。
必须区分每个角色知道什么。known_by 只填写明确在场、被告知或本来就知道的人；秘密和内心活动不得自动共享给其他角色。
单人剧情完整追踪该角色；多人剧情允许多个角色同等重要，每人必须保留独立动机、关系线、主观认知和完整支线。指定核心角色只提高记忆优先级，不代表必胜或预定结局。
战斗、探索等支线的伤势、物品、线索与后果必须记录。
关系分数只有出现清晰证据时才变化，单轮 delta 范围 -10 到 10；普通寒暄填 0。
每轮必须逐项检查：角色当前衣着、伤势/异常状态、能力变化、稳定档案（体貌、性格、职业、兴趣、住处）、物品归属与数量、承诺、秘密、线索、地点、故事时间、在场人物和未解决事项。没有变化就不要编造记录。
relationships 只记录剧情中有明确依据的“源角色对目标角色”的关系；目标可以是 <user>，它是用户本人。关系不明就留空，不根据同场自动推断。
events、memories、facts、promises、items 中每个非空条目都尽量填写 evidence，使用不超过80字的原文依据或紧贴原文的事实短句，便于后续审计。
只输出一个合法 JSON 对象，不要 Markdown。所有数组允许为空，结构固定为：
{{
  "scene": {{"story_time":"", "location":"", "summary":"本轮客观摘要"}},
  "main_arc": {{"summary":"恋爱主线当前状态", "phase":"", "central_conflict":"", "next_pressure":"尚未解决的压力，不写必然未来"}},
  "characters": [{{"name":"", "aliases":[], "cast_role":"lead|main_cast|supporting|protagonist|other", "relationship_stage":"陌生|相识|在意|暧昧|信赖|恋人|挚爱|决裂", "affection_delta":0, "trust_delta":0, "jealousy_delta":0, "current_state":"", "current_emotion":"", "current_goal":"", "current_outfit":"", "injuries":[], "physical_traits":"", "personality":"", "occupation":"", "hobbies":[], "likes":[], "residence":"", "important_info":[], "user_relationship":"", "user_attitude":"", "milestone":""}}],
  "relationships": [{{"source":"角色名", "target":"角色名或<user>", "relation":"关系称谓或状态", "attitude":"当前态度", "closeness":0, "known_by":[], "evidence":""}}],
  "arcs": [{{"title":"", "kind":"romance|battle|exploration|mystery|daily|social|growth|other", "status":"active|paused|completed|failed", "summary":"", "participants":[], "open_threads":[], "importance":3}}],
  "events": [{{"summary":"一条独立事件", "kind":"romance|battle|exploration|mystery|daily|social|growth|promise|secret|relationship|other", "arc":"", "participants":[], "known_by":[], "story_time":"", "location":"", "importance":3, "keywords":[], "evidence":""}}],
  "memories": [{{"owner":"记忆持有者", "content":"该角色的主观记忆或认知", "known_by":[], "private":false, "emotion":"", "importance":3, "keywords":[], "evidence":""}}],
  "facts": [{{"subject":"", "category":"appearance|injury|ability|clue|relationship|location|time|identity|other", "detail":"稳定事实或状态变化", "known_by":[], "importance":3, "keywords":[], "evidence":""}}],
  "promises": [{{"content":"", "parties":[], "known_by":[], "status":"pending|kept|broken|cancelled", "importance":4, "evidence":""}}],
  "items": [{{"name":"", "owner":"", "status":"含数量和变化", "location":"", "importance":3, "evidence":""}}]
}}"""
    if real_time:
        system += (
            f"\n时间感知已启用。本轮现实时间为“{real_time}”。"
            "scene.story_time 和每条 events.story_time 必须使用该现实时间，不得采用虚构日期、虚拟历法或剧情内时间。"
        )
    user = (
        "【现有记忆目录，仅用于消歧和增量更新】\n"
        + _catalog(state, lead_names)
        + "\n\n【本轮用户消息】\n"
        + _text(event.text, 30000)
        + "\n\n【本轮助手正文】\n"
        + _text(event.response_text, 50000)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _audit_messages(
    context,
    state: dict[str, Any],
    history: list[dict[str, Any]],
    current_response: str,
) -> list[dict[str, str]]:
    transcript = "\n\n".join(
        f"[{item.get('position', '?')}][{item.get('role', 'unknown')}]\n{_text(item.get('content'), 12000)}"
        for item in history
    )
    if current_response.strip():
        transcript += "\n\n[当前 assistant]\n" + _text(current_response, 30000)
    lead_names = _lead_names(context, state)
    system = """你是剧情记忆补漏审计器。对照现有记忆目录重新阅读近期原文，只输出第一次整理可能遗漏或需要纠正的内容。
重点寻找：衣着变化、伤势与异常状态、能力与资源消耗、稳定角色档案、物品数量/归属/位置、明确承诺、未公开秘密、线索、地点时间、在场人物、角色关系转折和支线后果。
禁止重复已有记录，禁止把计划或猜测写成事实。known_by 必须遵守角色知情边界。只输出与主整理器完全相同结构的 JSON 对象，不要解释。"""
    user = (
        "【现有记忆目录】\n"
        + _catalog(state, lead_names)
        + "\n\n【近期原文】\n"
        + transcript
        + "\n\nJSON 必须包含 scene、main_arc、characters、relationships、arcs、events、memories、facts、promises、items；没有补漏的字段使用空对象或空数组。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _format_known(record: dict[str, Any]) -> str:
    known = _string_list(record.get("known_by"), limit=40, item_limit=80)
    return " / ".join(known) if known else "知情范围未明"


def _format_character_state(item: dict[str, Any]) -> str:
    return (
        f"- {item.get('name')} [{item.get('cast_role')}/{item.get('relationship_stage')}]｜"
        f"好感 {item.get('affection')}｜信赖 {item.get('trust')}｜嫉妒 {item.get('jealousy')}\n"
        f"  状态={item.get('current_state') or '未记录'}；情绪={item.get('current_emotion') or '未记录'}；"
        f"目标={item.get('current_goal') or '未记录'}；衣着={item.get('current_outfit') or '未记录'}；"
        f"伤势={' / '.join(item.get('injuries', [])) or '无明确记录'}"
        + (
            f"；关键节点={' / '.join(item.get('milestones', [])[-6:])}"
            if item.get("milestones") else ""
        )
    )


def _build_injection(context, state: dict[str, Any], query: str) -> str:
    lead_names = _lead_names(context, state)
    lead_characters = [
        item for name in lead_names if (item := _find_named(state["characters"], name)) is not None
    ]
    recent_count = _clamp(context.settings.get("recent_event_count"), 2, 30, 8)
    recall_count = _clamp(context.settings.get("recalled_event_count"), 2, 40, 12)
    memory_count = _clamp(context.settings.get("recalled_memory_count"), 2, 50, 16)
    turn = state["turn"]
    recent = state["events"][-recent_count:]
    recent_ids = {item.get("id") for item in recent}
    recalled = [
        item for item in _rank_records(query, state["events"], recall_count + recent_count, turn)
        if item.get("id") not in recent_ids
    ][:recall_count]
    memories = _rank_records(query, state["memories"] + state["facts"], memory_count, turn)
    relevant_characters = _rank_records(query, state["characters"], 8, turn)
    lead_ids = {item.get("id") for item in lead_characters}
    relevant_characters = [
        *lead_characters,
        *[item for item in relevant_characters if item.get("id") not in lead_ids],
    ]
    arcs = _rank_records(query, [item for item in state["arcs"] if item.get("status") == "active"], 8, turn)
    archives = _rank_records(query, state["archives"], 5, turn)
    promises = [item for item in state["promises"] if item.get("status", "pending") == "pending"]
    important_items = [item for item in state["items"] if _clamp(item.get("importance"), 1, 5, 3) >= 5]
    item_ids = {item.get("id") for item in important_items}
    items = [
        *important_items,
        *[item for item in _rank_records(query, state["items"], 8, turn) if item.get("id") not in item_ids],
    ][:12]

    sections = ["<catgirl_long_term_memory>"]
    if bool(context.settings.get("inject_genre_guardrails", True)):
        sections.append(
            "【使用规则】以下内容是已发生事实与角色认知，不是要求复述的台词。严格维持各角色 known_by 知情边界；"
            "单人和多人剧情都必须保持每个角色的独立动机与关系连续性；核心角色只提高召回优先级，不代表预定结局；"
            "不得凭空提升关系，战斗、探索等支线按自身因果推进。"
        )
    main = state["main_arc"]
    if any(main.get(key) for key in ("summary", "phase", "central_conflict", "next_pressure")):
        sections.append(
            "【情感主线】\n"
            f"核心角色：{' / '.join(lead_names) or '由剧情自动识别'}\n"
            f"阶段：{main.get('phase') or '未记录'}\n"
            f"当前状态：{main.get('summary') or '未记录'}\n"
            f"核心矛盾：{main.get('central_conflict') or '无明确记录'}\n"
            f"未解决压力：{main.get('next_pressure') or '无明确记录'}"
        )
    if lead_characters:
        sections.append(
            "【核心角色状态台账】\n"
            + "\n".join(_format_character_state(item) for item in lead_characters)
        )
    other_characters = [item for item in relevant_characters if item.get("id") not in lead_ids]
    if other_characters:
        sections.append(
            "【相关角色状态台账】\n"
            + "\n".join(_format_character_state(item) for item in other_characters)
        )
    relevant_names = {_name_key(item.get("name")) for item in relevant_characters}
    relationships = [
        item
        for item in state["relationships"]
        if _name_key(item.get("source")) in relevant_names or _name_key(item.get("target")) in relevant_names
    ][-20:]
    if relationships:
        sections.append(
            "【角色关系】\n"
            + "\n".join(
                f"- {item.get('source')} -> {item.get('target')}：{item.get('relation') or '关系未命名'}"
                + (f"；态度={item.get('attitude')}" if item.get("attitude") else "")
                for item in relationships
            )
        )
    if arcs:
        sections.append(
            "【当前支线】\n"
            + "\n".join(
                f"- {item.get('title')} [{item.get('kind')}/{item.get('status')}]：{item.get('summary')}"
                + (f"；未解决：{' / '.join(item.get('open_threads', []))}" if item.get("open_threads") else "")
                for item in arcs
            )
        )
    if promises:
        sections.append(
            "【未完成承诺】\n"
            + "\n".join(
                f"- {' / '.join(item.get('parties', []))}：{item.get('content')}（知情：{_format_known(item)}）"
                for item in promises[-20:]
            )
        )
    if recent or recalled:
        sections.append(
            "【相关事件】\n"
            + "\n".join(
                f"- [T{item.get('turn')}｜{item.get('story_time') or '时间未明'}｜{item.get('location') or '地点未明'}] "
                f"{item.get('summary')}（知情：{_format_known(item)}）"
                for item in [*recalled, *recent]
            )
        )
    if memories:
        lines = []
        for item in memories:
            if "content" in item:
                lines.append(
                    f"- {item.get('owner')}的主观记忆：{item.get('content')}（知情：{_format_known(item)}）"
                )
            else:
                lines.append(
                    f"- 事实｜{item.get('subject') or '未分类'}：{item.get('detail')}（知情：{_format_known(item)}）"
                )
        sections.append("【人物记忆与事实】\n" + "\n".join(lines))
    if items:
        sections.append(
            "【相关物品】\n"
            + "\n".join(
                f"- {item.get('name')}：持有者 {item.get('owner') or '未明'}；状态 {item.get('status') or '未明'}；"
                f"位置 {item.get('location') or '未明'}"
                for item in items
            )
        )
    if archives:
        sections.append(
            "【久远篇章】\n" + "\n".join(f"- {item.get('summary')}" for item in archives)
        )
    if state.get("saga_summary"):
        sections.append("【全篇远景】\n" + _text(state["saga_summary"], 12000))
    if state["pinned"]:
        sections.append("【永久固定事实】\n" + "\n".join(f"- {item.get('content')}" for item in state["pinned"][-30:]))
    sections.append("</catgirl_long_term_memory>")
    limit = _clamp(context.settings.get("injection_char_limit"), 2000, 50000, 14000)
    output = "\n\n".join(part for part in sections if part.strip())
    return output[:limit]


async def _compact_if_needed(context, record_id: str, state: dict[str, Any]) -> None:
    limit = _clamp(context.settings.get("active_event_limit"), 40, 1000, 160)
    if len(state["events"]) <= limit:
        return
    batch_size = _clamp(context.settings.get("compact_batch_size"), 10, 100, 30)
    batch = state["events"][: min(batch_size, len(state["events"]) - limit + batch_size)]
    prompt = (
        "把以下旧事件压缩成一条可长期召回的篇章记忆。保留人物关系转折、承诺、秘密知情范围、伤势、物品、线索、"
        "战斗与探索结果；禁止添加新事实。只输出 JSON："
        '{"summary":"不超过1500字","participants":[],"keywords":[],"importance":1到5}。\n\n'
        + json.dumps(batch, ensure_ascii=False)
    )
    raw = await context.generate_text(
        record_id,
        [{"role": "system", "content": "你是长期剧情档案压缩器，只做无损事实压缩。"}, {"role": "user", "content": prompt}],
        max_tokens=min(1800, _clamp(context.settings.get("analysis_max_tokens"), 512, 8192, 2400)),
        temperature=0.1,
    )
    value = _parse_json_object(raw)
    summary = _text(value.get("summary"), 6000)
    if not summary:
        raise ValueError("旧事件压缩结果为空")
    state["archives"].append(
        {
            "id": _next_id(state, "archive"),
            "summary": summary,
            "participants": _string_list(value.get("participants"), limit=40, item_limit=80),
            "keywords": _string_list(value.get("keywords"), limit=30, item_limit=80),
            "importance": _clamp(value.get("importance"), 1, 5, 4),
            "turn_start": batch[0].get("turn", 0),
            "turn_end": batch[-1].get("turn", 0),
            "turn": batch[-1].get("turn", 0),
        }
    )
    removed = {item.get("id") for item in batch}
    state["events"] = [item for item in state["events"] if item.get("id") not in removed]
    if len(state["archives"]) > 100:
        old = state["archives"][:-80]
        state["saga_summary"] = _text(
            (state.get("saga_summary", "") + "\n" + "\n".join(item.get("summary", "") for item in old)).strip(),
            12000,
        )
        state["archives"] = state["archives"][-80:]


def _status_text(context, state: dict[str, Any]) -> str:
    leads = " / ".join(_lead_names(context, state)) or "自动识别"
    active_arcs = sum(1 for item in state["arcs"] if item.get("status") == "active")
    pending = sum(1 for item in state["promises"] if item.get("status", "pending") == "pending")
    return (
        f"长期记忆状态\n核心角色：{leads}\n已整理轮次：{state['turn']}\n"
        f"人物：{len(state['characters'])}｜活动支线：{active_arcs}｜事件：{len(state['events'])}｜远期篇章：{len(state['archives'])}\n"
        f"人物主观记忆：{len(state['memories'])}｜事实：{len(state['facts'])}｜未完成承诺：{pending}｜固定事实：{len(state['pinned'])}"
    )


class StoryMemoryPlugin:
    def __init__(self) -> None:
        self._analysis_locks: dict[str, asyncio.Lock] = {}

    def on_startup(self, context, _event: PluginEvent) -> PluginResult:
        if context.state:
            context.replace_state({})
        with context._manager.database.session_factory() as session:
            conversations = list(session.scalars(select(Conversation)).all())
        for conversation in conversations:
            _migrate_file_state(context, conversation.id)
        _reconcile_memories(context, conversations)
        return PluginResult()

    def on_conversation_created(self, context, conversation_id: str) -> None:
        _memory_store(context).ensure_bound(
            conversation_id,
            name=_conversation_name(context, conversation_id),
            automatic_name=True,
        )

    def on_conversation_deleted(self, context, _conversation_id: str) -> None:
        with context._manager.database.session_factory() as session:
            conversations = list(session.scalars(select(Conversation)).all())
        _reconcile_memories(context, conversations)

    def on_conversation_renamed(
        self,
        context,
        conversation_id: str,
        _previous_title: str,
    ) -> None:
        _memory_store(context).sync_bound_name(
            conversation_id,
            _conversation_name(context, conversation_id),
            legacy_suffixes=(" 的记忆",),
        )

    def conversation_state_get(self, context, conversation_id: str) -> dict[str, Any]:
        return _migrate_file_state(context, conversation_id)[1]

    def conversation_state_set(
        self,
        context,
        conversation_id: str,
        state: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> None:
        _memory_store(context).write_for_conversation(
            conversation_id,
            state,
            name=_conversation_name(context, conversation_id),
            automatic_name=True,
            snapshot_turn_id=turn_id,
        )

    def conversation_state_delete(
        self,
        context,
        conversation_id: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        _memory_store(context).reset_conversation(
            conversation_id,
            name=_conversation_name(context, conversation_id),
            automatic_name=True,
            snapshot_turn_id=turn_id,
        )

    def capture_conversation_snapshot(self, context, conversation_id: str, turn_id: str) -> None:
        _memory_store(context).capture_snapshot(conversation_id, turn_id)

    def restore_conversation_snapshot(self, context, conversation_id: str, turn_id: str) -> None:
        if not _memory_store(context).restore_snapshot(conversation_id, turn_id):
            LOGGER.warning(
                "撤回时记忆已经被其他聊天或手动编辑，已保留当前文件并归档冲突快照 | conversation=%s | turn=%s",
                conversation_id,
                turn_id,
            )

    def inspect_conversation_states(
        self,
        context,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        with context._manager.database.session_factory() as session:
            conversations = list(
                session.scalars(
                    select(Conversation).order_by(
                        Conversation.is_active.desc(),
                        Conversation.updated_at.desc(),
                        Conversation.created_at.desc(),
                    )
                ).all()
            )
            if conversation_id and not any(item.id == conversation_id for item in conversations):
                raise ValueError("聊天记录不存在")
            message_counts = {
                item_id: int(count)
                for item_id, count in session.execute(
                    select(ChatMessage.conversation_id, func.count(ChatMessage.id)).group_by(
                        ChatMessage.conversation_id
                    )
                ).all()
            }

        for conversation in conversations:
            _migrate_file_state(context, conversation.id)
        store = _memory_store(context)
        overview = _reconcile_memories(context, conversations)
        memory_by_id = {str(item.get("id") or ""): item for item in overview["memories"]}
        selected = (
            next((item for item in conversations if item.id == conversation_id), None)
            if conversation_id
            else next(iter(conversations), None)
        )
        items = []
        for conversation in conversations:
            memory_id = str(overview["bindings"].get(conversation.id) or "")
            metadata = memory_by_id.get(memory_id, {})
            items.append(
                {
                    "conversation_id": conversation.id,
                    "title": conversation.title,
                    "external_id": conversation.external_id,
                    "is_active": bool(conversation.is_active),
                    "updated_at": str(metadata.get("updated_at") or conversation.updated_at.isoformat()),
                    "message_count": message_counts.get(conversation.id, 0),
                    "memory_id": memory_id,
                    "memory_name": str(metadata.get("name") or "未命名记忆"),
                }
            )
        selected_memory_id = (
            str(overview["bindings"].get(selected.id) or "") if selected is not None else ""
        )
        return {
            "items": items,
            "memories": overview["memories"],
            "selected_id": selected.id if selected is not None else None,
            "selected_memory_id": selected_memory_id or None,
            "state": store.read(selected_memory_id) if selected_memory_id else _default_state(),
        }

    def admin_action(self, context, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        conversation_id = _text(payload.get("conversation_id"), 200)
        with context._manager.database.session_factory() as session:
            conversation = session.get(Conversation, conversation_id) if conversation_id else None
            if conversation is None:
                raise ValueError("聊天记录不存在")
            default_name = _memory_name_for_conversation(conversation)

        store = _memory_store(context)
        if action == "create-memory":
            requested_name = _text(payload.get("name"), 160)
            store.create_and_bind(
                conversation_id,
                name=requested_name or default_name,
                automatic_name=not requested_name,
            )
        elif action == "bind-memory":
            store.bind(conversation_id, _text(payload.get("memory_id"), 80))
        elif action == "rename-memory":
            store.rename(
                _text(payload.get("memory_id"), 80),
                _text(payload.get("name"), 160),
            )
        elif action == "reset-memory":
            store.reset_conversation(
                conversation_id,
                name=default_name,
                automatic_name=True,
            )
        elif action == "delete-memory":
            store.delete_memory(
                _text(payload.get("memory_id"), 80),
                conversation_id=conversation_id,
                replacement_name=default_name,
                replacement_automatic_name=True,
            )
        else:
            raise ValueError("未知的记忆管理动作")
        return self.inspect_conversation_states(context, conversation_id)

    def _record_id(self, event: PluginEvent) -> str:
        return _text(event.metadata.get("record_id"), 200)

    def _send(self, event: PluginEvent, text: str) -> PluginResult:
        return PluginResult(
            actions=[PluginAction(kind="send_text", payload={"conversation_id": event.conversation_id, "text": text})],
            consume=True,
        )

    def on_user_message(self, context, event: PluginEvent) -> PluginResult:
        text = event.text.strip()
        if not text.startswith(COMMAND_PREFIX):
            return PluginResult()
        record_id = self._record_id(event)
        if not record_id:
            return self._send(event, "当前消息没有对应的聊天记录，无法管理记忆。")
        state = _normalize_state(context.get_conversation_state(record_id))
        argument = text[len(COMMAND_PREFIX) :].strip()
        if argument in {"", "帮助"}:
            return self._send(
                event,
                "记忆命令：\n/记忆状态\n/记忆角色\n/记忆支线\n/记忆核心 名称1,名称2\n/记忆核心 清除\n/记忆固定 事实内容\n/记忆清除 确认",
            )
        if argument == "状态":
            return self._send(event, _status_text(context, state))
        if argument == "角色":
            if not state["characters"]:
                return self._send(event, "当前还没有整理出角色记忆。")
            lines = [
                f"- {item.get('name')} [{item.get('cast_role')}/{item.get('relationship_stage')}] "
                f"好感{item.get('affection')} 信赖{item.get('trust')} 嫉妒{item.get('jealousy')}"
                for item in sorted(state["characters"], key=lambda value: value.get("last_turn", 0), reverse=True)
            ]
            return self._send(event, "角色关系：\n" + "\n".join(lines[:40]))
        if argument == "支线":
            active = [item for item in state["arcs"] if item.get("status") == "active"]
            if not active:
                return self._send(event, "当前没有活动支线。")
            return self._send(
                event,
                "活动支线：\n" + "\n".join(
                    f"- {item.get('title')} [{item.get('kind')}]：{item.get('summary')}" for item in active[:30]
                ),
            )
        if argument == "核心 清除":
            state["lead_overrides"] = []
            context.replace_conversation_state(record_id, state)
            return self._send(event, "已取消当前记录的核心角色覆盖，后续按插件设置或剧情自动识别。")
        if argument.startswith("核心 "):
            names = _split_names(argument[3:])
            if not names:
                return self._send(event, "请在命令后填写一个或多个核心角色名称。")
            state["lead_overrides"] = names
            wanted = {_name_key(name) for name in names}
            for existing in state["characters"]:
                if _name_key(existing.get("name")) in wanted:
                    existing["cast_role"] = "lead"
            context.replace_conversation_state(record_id, state)
            return self._send(event, f"当前聊天记录的核心角色已设为：{' / '.join(names)}")
        if argument.startswith("固定 "):
            content = _text(argument[3:], 2000)
            if not content:
                return self._send(event, "请在命令后填写需要永久固定的事实。")
            if all(_name_key(item.get("content")) != _name_key(content) for item in state["pinned"]):
                state["pinned"].append({"id": _next_id(state, "pin"), "content": content, "created_at": _now()})
                state["pinned"] = state["pinned"][-100:]
                context.replace_conversation_state(record_id, state)
            return self._send(event, "已固定这条事实，后续每轮都会优先注入。")
        if argument == "清除 确认":
            context.delete_conversation_state(record_id)
            return self._send(event, "当前这份聊天记录的长期记忆已清除，其他记录不受影响。")
        return self._send(event, "无法识别该记忆命令。发送 /记忆帮助 查看用法。")

    def before_prompt_compile(self, context, event: PluginEvent) -> PluginResult:
        record_id = self._record_id(event)
        if not record_id:
            return PluginResult()
        state = _normalize_state(context.get_conversation_state(record_id))
        content = _build_injection(context, state, event.text)
        if not content:
            return PluginResult()
        return PluginResult(
            actions=[
                PluginAction(
                    kind="prompt_addition",
                    payload={
                        "conversation_id": event.conversation_id,
                        "role": "system",
                        "content": content,
                    },
                )
            ]
        )

    async def after_model_response(self, context, event: PluginEvent) -> PluginResult:
        if not bool(context.settings.get("auto_analyze", True)) or not event.text.strip():
            return PluginResult()
        record_id = self._record_id(event)
        if not record_id:
            return PluginResult()
        store = _memory_store(context)
        memory_id, _ = store.ensure_bound(
            record_id,
            name=_conversation_name(context, record_id),
            automatic_name=True,
        )
        lock = self._analysis_locks.setdefault(memory_id, asyncio.Lock())
        async with lock:
            state = _normalize_state(store.read(memory_id))
            real_time = _real_time_label(event)
            try:
                raw = await context.generate_text(
                    record_id,
                    _analysis_messages(context, state, event),
                    max_tokens=_clamp(context.settings.get("analysis_max_tokens"), 512, 8192, 2400),
                    temperature=float(context.settings.get("analysis_temperature", 0.15)),
                )
                payload = _parse_json_object(raw)
                _apply_real_time(payload, real_time)
                _merge_payload(context, state, payload)
                audit_interval = _clamp(context.settings.get("detail_audit_interval"), 0, 50, 8)
                if audit_interval and state["turn"] % audit_interval == 0:
                    lookback = _clamp(context.settings.get("detail_audit_lookback_rounds"), 2, 20, 6)
                    history = context.get_conversation_messages(record_id, limit=lookback * 2)
                    audit_raw = await context.generate_text(
                        record_id,
                        _audit_messages(context, state, history, event.response_text),
                        max_tokens=_clamp(context.settings.get("analysis_max_tokens"), 512, 8192, 2400),
                        temperature=0.1,
                    )
                    audit_payload = _parse_json_object(audit_raw)
                    _apply_real_time(audit_payload, real_time)
                    _merge_payload(
                        context,
                        state,
                        audit_payload,
                        advance_turn=False,
                        apply_relationship_deltas=False,
                    )
                await _compact_if_needed(context, record_id, state)
            except Exception as exc:
                state["last_analysis_error"] = f"{type(exc).__name__}: {exc}"[:1000]
                state["updated_at"] = _now()
            store.write(
                memory_id,
                state,
                snapshot_conversation_id=record_id,
                snapshot_turn_id=str(event.metadata.get("turn_id") or "") or None,
            )
        return PluginResult()


plugin = StoryMemoryPlugin()
