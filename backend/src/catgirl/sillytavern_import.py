from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .prompt_post_processing import PROMPT_POST_PROCESSING_TYPES
from .provider_sources import chat_completion_source_spec, SUPPORTED_CHAT_COMPLETION_SOURCES


MARKER_NAMES = {
    "worldInfoBefore": "世界书（角色定义前）",
    "worldInfoAfter": "世界书（角色定义后）",
    "charDescription": "角色描述",
    "charPersonality": "角色性格",
    "scenario": "场景",
    "personaDescription": "用户人格",
    "dialogueExamples": "示例对话",
    "chatHistory": "聊天历史",
}

SOURCE_MODEL_FIELDS = {
    "openai": "openai_model",
    "claude": "claude_model",
    "openrouter": "openrouter_model",
    "custom": "custom_model",
    "makersuite": "google_model",
    "vertexai": "vertexai_model",
    "mistralai": "mistralai_model",
    "cohere": "cohere_model",
    "perplexity": "perplexity_model",
    "groq": "groq_model",
    "deepseek": "deepseek_model",
    "xai": "xai_model",
    "moonshot": "moonshot_model",
    "zai": "zai_model",
    "siliconflow": "siliconflow_model",
}

IMAGE_QUALITIES = {"auto", "low", "high"}
REASONING_EFFORTS = {"auto", "min", "low", "medium", "high", "max"}


@dataclass
class NormalizedPreset:
    name: str
    provider_name: str
    provider_source: str
    provider_kind: str
    provider_base_url: str
    provider_model: str
    provider_prompt_post_processing: str
    settings: dict[str, Any]
    blocks: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizedWorldBook:
    name: str
    description: str
    entries: list[dict[str, Any]]
    raw_data: dict[str, Any]
    source_format: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizedCharacter:
    name: str
    summary: str
    persona: str
    scenario: str
    first_message: str
    embedded_world_book: NormalizedWorldBook | None = None
    warnings: list[str] = field(default_factory=list)


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _role(value: Any) -> str:
    if value in (1, "user"):
        return "user"
    if value in (2, "assistant"):
        return "assistant"
    return "system"


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if result == result and result not in {float("inf"), float("-inf")} else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: int | float, minimum: int | float, maximum: int | float):
    return min(maximum, max(minimum, value))


def _prompt_order(data: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_order = data.get("prompt_order")
    if not isinstance(prompt_order, list) or not prompt_order:
        return []
    if all(isinstance(item, dict) and "identifier" in item for item in prompt_order):
        return prompt_order
    candidates = [item for item in prompt_order if isinstance(item, dict) and isinstance(item.get("order"), list)]
    if not candidates:
        return []
    completion_order = next(
        (item for item in candidates if str(item.get("character_id")) == "100001"),
        None,
    )
    legacy_order = next(
        (item for item in candidates if str(item.get("character_id")) == "100000"),
        None,
    )
    return (completion_order or legacy_order or candidates[0])["order"]


def normalize_sillytavern_preset(name: str, data: dict[str, Any]) -> NormalizedPreset:
    if not isinstance(data.get("prompts"), list) and not isinstance(data.get("prompt_order"), list):
        raise ValueError("Not a SillyTavern Chat Completion preset")

    source = str(data.get("chat_completion_source") or "custom")
    if source not in SUPPORTED_CHAT_COMPLETION_SOURCES:
        source = "custom"
    source_spec = chat_completion_source_spec(source)
    model_field = SOURCE_MODEL_FIELDS.get(source, "custom_model")
    model = str(data.get(model_field) or data.get("custom_model") or "")
    base_url = str(data.get("custom_url") or source_spec.base_url)
    prompt_post_processing = str(data.get("custom_prompt_post_processing") or "")
    if prompt_post_processing == "claude":
        prompt_post_processing = "merge"
    if prompt_post_processing not in PROMPT_POST_PROCESSING_TYPES:
        prompt_post_processing = ""
    provider_name = f"{source} / {model}" if model else source
    warnings: list[str] = []
    if not source_spec.base_url and source != "custom":
        warnings.append(f"供应商 {source} 需要在导入后手动确认兼容 Base URL 和 API Key")
    if data.get("reverse_proxy") or data.get("proxy_password"):
        warnings.append("反向代理地址和密码未导入；请在供应商页面单独配置")

    prompts = {
        str(prompt.get("identifier")): prompt
        for prompt in data.get("prompts", [])
        if isinstance(prompt, dict) and prompt.get("identifier")
    }
    order = _prompt_order(data)
    blocks: list[dict[str, Any]] = []
    used: set[str] = set()

    for position, order_item in enumerate(order):
        if not isinstance(order_item, dict):
            continue
        identifier = str(order_item.get("identifier") or "")
        if not identifier:
            continue
        prompt = prompts.get(identifier, {})
        marker = bool(prompt.get("marker")) or identifier in MARKER_NAMES
        blocks.append(
            {
                "title": str(prompt.get("name") or MARKER_NAMES.get(identifier) or identifier),
                "role": _role(prompt.get("role")),
                "content": str(prompt.get("content") or ""),
                "enabled": bool(order_item.get("enabled", True)),
                "position": position,
                "identifier": identifier,
                "marker": marker,
                "injection_position": _clamp(_int(prompt.get("injection_position"), 0), 0, 1),
                "injection_depth": _clamp(_int(prompt.get("injection_depth"), 4), 0, 1000),
                "injection_order": _clamp(_int(prompt.get("injection_order"), 100), -100000, 100000),
            }
        )
        used.add(identifier)

    for prompt in data.get("prompts", []):
        if not isinstance(prompt, dict):
            continue
        identifier = str(prompt.get("identifier") or "")
        if not identifier or identifier in used:
            continue
        blocks.append(
            {
                "title": str(prompt.get("name") or identifier),
                "role": _role(prompt.get("role")),
                "content": str(prompt.get("content") or ""),
                "enabled": True,
                "stashed": True,
                "position": len(blocks),
                "identifier": identifier,
                "marker": bool(prompt.get("marker")),
                "injection_position": _clamp(_int(prompt.get("injection_position"), 0), 0, 1),
                "injection_depth": _clamp(_int(prompt.get("injection_depth"), 4), 0, 1000),
                "injection_order": _clamp(_int(prompt.get("injection_order"), 100), -100000, 100000),
            }
        )

    if not blocks:
        warnings.append("预设没有可导入的 prompts/prompt_order")

    image_quality = str(data.get("inline_image_quality") or "auto")
    reasoning_effort = str(data.get("reasoning_effort") or "auto")
    settings = {
        "max_context_unlocked": bool(data.get("max_context_unlocked", False)),
        "context_length": _clamp(_int(data.get("openai_max_context"), 128000), 512, 2_000_000),
        "max_response_tokens": _clamp(_int(data.get("openai_max_tokens"), 2048), 1, 1_000_000),
        "candidate_count": _clamp(_int(data.get("n"), 1), 1, 16),
        "streaming": bool(data.get("stream_openai", True)),
        "temperature": _clamp(_float(data.get("temperature"), 1.0), 0, 2),
        "frequency_penalty": _clamp(_float(data.get("frequency_penalty"), 0.0), -2, 2),
        "presence_penalty": _clamp(_float(data.get("presence_penalty"), 0.0), -2, 2),
        "top_p": _clamp(_float(data.get("top_p"), 1.0), 0, 1),
        "quote_wrapping": bool(data.get("wrap_in_quotes", False)),
        "continue_prefill": bool(data.get("continue_prefill", False)),
        "squash_system_messages": bool(data.get("squash_system_messages", False)),
        "function_calling": bool(data.get("function_calling", False)),
        "media_inlining": bool(data.get("media_inlining", True)),
        "image_quality": image_quality if image_quality in IMAGE_QUALITIES else "auto",
        "show_thoughts": bool(data.get("show_thoughts", True)),
        "reasoning_effort": reasoning_effort if reasoning_effort in REASONING_EFFORTS else "auto",
    }
    if not settings["max_context_unlocked"] and settings["context_length"] > 200000:
        settings["max_context_unlocked"] = True
        warnings.append("上下文超过 200,000，已自动启用解锁上下文")
    if settings["max_response_tokens"] > settings["context_length"]:
        settings["max_response_tokens"] = settings["context_length"]
        warnings.append("最大回复长度超过上下文，已裁剪到上下文长度")

    if any(block["identifier"] in {"worldInfoBefore", "worldInfoAfter"} for block in blocks):
        warnings.append("已导入世界书动态插槽；需同时导入并关联世界书 JSON 才有正文")

    return NormalizedPreset(
        name=name,
        provider_name=provider_name,
        provider_source=source,
        provider_kind=source_spec.kind,
        provider_base_url=base_url.rstrip("/"),
        provider_model=model,
        provider_prompt_post_processing=prompt_post_processing,
        settings=settings,
        blocks=blocks,
        warnings=warnings,
    )


def normalize_sillytavern_world_book(name: str, data: dict[str, Any]) -> NormalizedWorldBook:
    source_data = data
    entries_value = data.get("entries")
    if not isinstance(entries_value, (dict, list)):
        nested_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        character_book = data.get("character_book") or nested_data.get("character_book")
        if isinstance(character_book, dict):
            data = character_book
            entries_value = data.get("entries")
    if not isinstance(entries_value, (dict, list)):
        raise ValueError("Not a SillyTavern world book")

    source_format = "character_book" if isinstance(entries_value, list) else "sillytavern_world_info"
    iterable = entries_value.items() if isinstance(entries_value, dict) else enumerate(entries_value)
    normalized_entries: list[dict[str, Any]] = []
    warnings: list[str] = []

    for fallback_uid, entry in iterable:
        if not isinstance(entry, dict):
            warnings.append(f"条目 {fallback_uid} 不是对象，已跳过")
            continue
        extensions = entry.get("extensions") if isinstance(entry.get("extensions"), dict) else {}
        uid = _int(entry.get("uid", entry.get("id", fallback_uid)), _int(fallback_uid, len(normalized_entries)))
        raw_position = extensions.get("position", entry.get("position", 0))
        if raw_position == "before_char":
            raw_position = 0
        elif raw_position == "after_char":
            raw_position = 1
        position = _int(raw_position, 0)
        enabled = bool(entry.get("enabled", not entry.get("disable", False)))
        normalized_entries.append(
            {
                "uid": uid,
                "primary_keys": _as_string_list(entry.get("key", entry.get("keys", []))),
                "secondary_keys": _as_string_list(entry.get("keysecondary", entry.get("secondary_keys", []))),
                "comment": str(entry.get("comment") or entry.get("name") or "")[:240],
                "content": str(entry.get("content") or entry.get("entry") or ""),
                "constant": bool(entry.get("constant", False)),
                "selective": bool(entry.get("selective", True)),
                "selective_logic": _clamp(_int(entry.get("selectiveLogic", extensions.get("selectiveLogic")), 0), 0, 3),
                "enabled": enabled,
                "insertion_order": _clamp(_int(entry.get("order", entry.get("insertion_order")), 100), -100000, 100000),
                "position": min(7, max(0, position)),
                "insertion_depth": _clamp(_int(entry.get("depth", extensions.get("depth")), 4), 0, 1000),
                "role": _role(entry.get("role", extensions.get("role", 0))),
                "probability": _clamp(_int(entry.get("probability", extensions.get("probability")), 100), 0, 100),
                "use_probability": bool(entry.get("useProbability", extensions.get("use_probability", True))),
                "raw_data": entry,
            }
        )

    return NormalizedWorldBook(
        name=str(data.get("name") or name),
        description=str(data.get("description") or ""),
        entries=normalized_entries,
        raw_data=source_data,
        source_format=source_format,
        warnings=warnings,
    )


def normalize_sillytavern_character(name: str, data: dict[str, Any]) -> NormalizedCharacter:
    source = data
    nested = data.get("data")
    spec = str(data.get("spec") or "").lower()
    if isinstance(nested, dict) and (
        spec.startswith("chara_card_")
        or any(key in nested for key in ("description", "personality", "scenario", "first_mes"))
    ):
        data = nested

    card_name = str(data.get("name") or name).strip()
    has_character_fields = any(
        key in data
        for key in (
            "description",
            "personality",
            "scenario",
            "first_mes",
            "first_message",
            "mes_example",
            "system_prompt",
            "post_history_instructions",
        )
    )
    if not card_name or (not spec.startswith("chara_card_") and not has_character_fields):
        raise ValueError("Not a SillyTavern character card")

    description = str(data.get("description") or "").strip()
    personality = str(data.get("personality") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip()
    post_history = str(data.get("post_history_instructions") or "").strip()
    examples = str(data.get("mes_example") or "").strip()
    persona_parts = []
    if description:
        persona_parts.append(description)
    if personality:
        persona_parts.append(f"<personality>\n{personality}\n</personality>")
    if system_prompt:
        persona_parts.append(f"<character_system_prompt>\n{system_prompt}\n</character_system_prompt>")
    if post_history:
        persona_parts.append(
            f"<post_history_instructions>\n{post_history}\n</post_history_instructions>"
        )
    if examples:
        persona_parts.append(f"<example_dialogues>\n{examples}\n</example_dialogues>")

    summary_source = description or personality or card_name
    summary = next((line.strip() for line in summary_source.splitlines() if line.strip()), card_name)
    warnings: list[str] = []
    if system_prompt or post_history:
        warnings.append(f"角色卡“{card_name}”的角色专属指令已合并到角色设定")
    alternate_greetings = data.get("alternate_greetings")
    if isinstance(alternate_greetings, list) and alternate_greetings:
        warnings.append(f"角色卡“{card_name}”包含备用开场；当前仅导入首个开场消息")
    if data.get("creator_notes") or data.get("tags"):
        warnings.append(f"角色卡“{card_name}”的作者备注或标签未映射到角色字段")
    source_format = str(source.get("__catgirl_source_format") or "")
    if source_format == "png":
        warnings.append(f"角色卡“{card_name}”的 PNG 立绘未保存，仅导入卡片文本数据")

    embedded_world_book = None
    character_book = data.get("character_book")
    if isinstance(character_book, dict) and isinstance(character_book.get("entries"), (dict, list)):
        book_data = {**character_book}
        book_data.setdefault("name", f"{card_name} 世界书")
        embedded_world_book = normalize_sillytavern_world_book(
            f"{card_name} 世界书",
            book_data,
        )

    return NormalizedCharacter(
        name=card_name[:120],
        summary=summary[:240],
        persona="\n\n".join(persona_parts),
        scenario=str(data.get("scenario") or ""),
        first_message=str(data.get("first_mes") or data.get("first_message") or ""),
        embedded_world_book=embedded_world_book,
        warnings=warnings,
    )
