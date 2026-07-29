from __future__ import annotations

from copy import deepcopy
from typing import Any


PROMPT_PLACEHOLDER = "请开始对话。"
PROMPT_POST_PROCESSING_TYPES = frozenset(
    {"", "merge", "merge_tools", "semi", "semi_tools", "strict", "strict_tools", "single"}
)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _starts_with_name(content: Any, name: str) -> bool:
    return bool(name) and _text_content(content).startswith(f"{name}: ")


def _prefix_content(content: Any, prefix: str) -> Any:
    if not prefix:
        return content
    if isinstance(content, list):
        copied = deepcopy(content)
        for part in copied:
            if isinstance(part, dict) and part.get("type") == "text":
                part["text"] = prefix + str(part.get("text", ""))
                return copied
        return [{"type": "text", "text": prefix}, *copied]
    return prefix + str(content or "")


def _merge_content(previous: Any, current: Any) -> Any:
    if isinstance(previous, str) and isinstance(current, str):
        return f"{previous}\n\n{current}"

    def parts(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [deepcopy(part) for part in value if isinstance(part, dict)]
        return [{"type": "text", "text": str(value or "")}]

    output = parts(previous)
    incoming = parts(current)
    if output and isinstance(output[-1], dict) and output[-1].get("type") == "text":
        output[-1]["text"] = str(output[-1].get("text", "")) + "\n\n"
    else:
        output.append({"type": "text", "text": "\n\n"})
    if incoming and output[-1].get("type") == "text" and incoming[0].get("type") == "text":
        output[-1]["text"] += str(incoming[0].get("text", ""))
        incoming = incoming[1:]
    output.extend(incoming)
    return output


def _has_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content)
    return bool(content) if isinstance(content, list) else False


def _merge_messages(
    source_messages: list[dict[str, Any]],
    *,
    strict: bool,
    placeholders: bool,
    single: bool,
    tools: bool,
    user_name: str,
    character_name: str,
) -> list[dict[str, Any]]:
    messages = deepcopy(source_messages)
    merged: list[dict[str, Any]] = []

    for message in messages:
        if not _has_content(message.get("content")):
            message["content"] = ""
        role = str(message.get("role") or "user")
        name = str(message.get("name") or "")
        content = message["content"]

        if role == "system" and name == "example_assistant" and character_name:
            if not _starts_with_name(content, character_name):
                message["content"] = _prefix_content(content, f"{character_name}: ")
        elif role == "system" and name == "example_user" and user_name:
            if not _starts_with_name(content, user_name):
                message["content"] = _prefix_content(content, f"{user_name}: ")
        elif name and role != "system" and not _starts_with_name(content, name):
            message["content"] = _prefix_content(content, f"{name}: ")

        if role == "tool" and not tools:
            role = "user"
        if single:
            if role == "assistant" and character_name and not _starts_with_name(
                message["content"], character_name
            ):
                message["content"] = _prefix_content(
                    message["content"], f"{character_name}: "
                )
            elif role == "user" and user_name and not _starts_with_name(
                message["content"], user_name
            ):
                message["content"] = _prefix_content(message["content"], f"{user_name}: ")
            role = "user"

        message["role"] = role
        message.pop("name", None)
        if not tools:
            message.pop("tool_calls", None)
            message.pop("tool_call_id", None)

    for message in messages:
        if (
            merged
            and merged[-1].get("role") == message.get("role")
            and _has_content(message.get("content"))
            and message.get("role") != "tool"
        ):
            merged[-1]["content"] = _merge_content(
                merged[-1].get("content"), message.get("content")
            )
        else:
            merged.append(message)

    if not merged:
        merged.append({"role": "user", "content": PROMPT_PLACEHOLDER})

    if strict:
        for index, message in enumerate(merged):
            if index > 0 and message.get("role") == "system":
                message["role"] = "user"
        if placeholders:
            if merged[0].get("role") == "system" and (
                len(merged) == 1 or merged[1].get("role") != "user"
            ):
                merged.insert(1, {"role": "user", "content": PROMPT_PLACEHOLDER})
            elif merged[0].get("role") not in {"system", "user"}:
                merged.insert(0, {"role": "user", "content": PROMPT_PLACEHOLDER})
        return _merge_messages(
            merged,
            strict=False,
            placeholders=placeholders,
            single=False,
            tools=tools,
            user_name=user_name,
            character_name=character_name,
        )

    return merged


def post_process_prompt(
    messages: list[dict[str, Any]],
    processing_type: str,
    *,
    user_name: str = "",
    character_name: str = "",
) -> list[dict[str, Any]]:
    if processing_type not in PROMPT_POST_PROCESSING_TYPES:
        raise ValueError(f"Unsupported prompt post-processing type: {processing_type}")
    if not processing_type:
        return deepcopy(messages)

    base_type = processing_type.removesuffix("_tools")
    return _merge_messages(
        messages,
        strict=base_type in {"semi", "strict", "single"},
        placeholders=base_type == "strict",
        single=base_type == "single",
        tools=processing_type.endswith("_tools"),
        user_name=user_name,
        character_name=character_name,
    )
