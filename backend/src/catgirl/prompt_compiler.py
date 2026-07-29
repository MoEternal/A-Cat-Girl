from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from .macro_engine import MacroContext, render_macros


class PromptBlockLike(Protocol):
    id: str
    role: str
    content: str
    enabled: bool
    stashed: bool
    position: int
    identifier: str | None
    marker: bool
    injection_position: int
    injection_depth: int
    injection_order: int


class WorldBookEntryLike(Protocol):
    id: str
    primary_keys: list[str]
    secondary_keys: list[str]
    content: str
    constant: bool
    selective: bool
    selective_logic: int
    enabled: bool
    insertion_order: int
    position: int
    insertion_depth: int
    role: str
    probability: int
    use_probability: bool


class UserPersonaLike(Protocol):
    id: str
    description: str
    injection_position: int
    injection_depth: int
    role: str


@dataclass(frozen=True)
class CompiledMessage:
    role: str
    content: str
    source: str

    def as_api_message(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class DepthInjection:
    role: str
    content: str
    depth: int
    order: int
    source: str


def _contains(text: str, keyword: str) -> bool:
    return keyword.casefold() in text.casefold()


def world_book_entry_matches(entry: WorldBookEntryLike, scan_text: str) -> bool:
    if not entry.enabled:
        return False
    if entry.constant:
        return True
    primary_match = any(_contains(scan_text, key) for key in entry.primary_keys if key)
    if not primary_match:
        return False
    if not entry.selective or not entry.secondary_keys:
        return True

    matches = [_contains(scan_text, key) for key in entry.secondary_keys if key]
    if not matches:
        return True
    if entry.selective_logic == 1:  # NOT_ALL
        return not all(matches)
    if entry.selective_logic == 2:  # NOT_ANY
        return not any(matches)
    if entry.selective_logic == 3:  # AND_ALL
        return all(matches)
    return any(matches)  # AND_ANY


def select_world_book_entries(
    entries: Iterable[WorldBookEntryLike],
    scan_text: str,
    random_value: Callable[[], float] = random.random,
) -> list[WorldBookEntryLike]:
    selected = []
    for entry in entries:
        if not world_book_entry_matches(entry, scan_text):
            continue
        if entry.use_probability and random_value() * 100 > entry.probability:
            continue
        selected.append(entry)
    return sorted(selected, key=lambda item: item.insertion_order, reverse=True)


def insert_at_depth(
    messages: list[CompiledMessage],
    injections: Iterable[DepthInjection],
) -> list[CompiledMessage]:
    result = list(messages)
    ordered = sorted(injections, key=lambda item: (item.depth, -item.order, item.source))
    offsets: dict[int, int] = {}
    original_length = len(messages)
    for injection in ordered:
        depth = max(0, injection.depth)
        base_index = max(0, original_length - depth)
        offset = offsets.get(base_index, 0)
        result.insert(
            base_index + offset,
            CompiledMessage(injection.role, injection.content, injection.source),
        )
        offsets[base_index] = offset + 1
    return result


def compile_prompt_messages(
    blocks: Iterable[PromptBlockLike],
    history: list[dict[str, str]],
    marker_values: dict[str, str],
    world_entries: Iterable[WorldBookEntryLike] = (),
    scan_text: str = "",
    random_value: Callable[[], float] = random.random,
    macro_context: MacroContext | None = None,
    user_persona: UserPersonaLike | None = None,
) -> list[CompiledMessage]:
    selected_world = select_world_book_entries(world_entries, scan_text, random_value)
    before_world = "\n".join(entry.content for entry in selected_world if entry.position == 0 and entry.content)
    after_world = "\n".join(entry.content for entry in selected_world if entry.position == 1 and entry.content)
    marker_content = {
        **marker_values,
        "worldInfoBefore": before_world,
        "worldInfoAfter": after_world,
    }
    if user_persona is not None:
        marker_content["personaDescription"] = (
            user_persona.description if user_persona.injection_position == 0 else ""
        )

    messages: list[CompiledMessage] = []
    injections: list[DepthInjection] = []
    history_inserted = False

    for block in sorted(
        (item for item in blocks if item.enabled and not getattr(item, "stashed", False)),
        key=lambda item: item.position,
    ):
        content = marker_content.get(block.identifier or "", "") if block.marker else block.content
        if block.identifier == "chatHistory" and block.marker:
            messages.extend(
                CompiledMessage(item["role"], item["content"], "chatHistory") for item in history
            )
            history_inserted = True
            continue
        if macro_context is not None:
            content = render_macros(content, macro_context).content
        if not content:
            continue
        if block.injection_position == 1:
            injections.append(
                DepthInjection(
                    role=block.role,
                    content=content,
                    depth=block.injection_depth,
                    order=block.injection_order,
                    source=block.id,
                )
            )
        else:
            messages.append(CompiledMessage(block.role, content, block.id))

    if not history_inserted:
        messages.extend(CompiledMessage(item["role"], item["content"], "chatHistory") for item in history)

    if user_persona is not None and user_persona.description:
        persona_content = user_persona.description
        if macro_context is not None:
            persona_content = render_macros(persona_content, macro_context).content
        if user_persona.injection_position == 2:
            messages.insert(0, CompiledMessage(user_persona.role, persona_content, f"userPersona:{user_persona.id}"))
        elif user_persona.injection_position == 3:
            messages.append(CompiledMessage(user_persona.role, persona_content, f"userPersona:{user_persona.id}"))
        elif user_persona.injection_position == 4:
            injections.append(
                DepthInjection(
                    role=user_persona.role,
                    content=persona_content,
                    depth=user_persona.injection_depth,
                    order=100,
                    source=f"userPersona:{user_persona.id}",
                )
            )

    for entry in selected_world:
        if entry.position == 4 and entry.content:
            entry_content = entry.content
            if macro_context is not None:
                entry_content = render_macros(entry_content, macro_context).content
            injections.append(
                DepthInjection(
                    role=entry.role,
                    content=entry_content,
                    depth=entry.insertion_depth,
                    order=entry.insertion_order,
                    source=f"worldBook:{entry.id}",
                )
            )

    return insert_at_depth(messages, injections)
