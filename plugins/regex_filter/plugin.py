from __future__ import annotations

import logging
import re
from typing import Any

import regex

from catgirl.plugins import PluginEvent, PluginResult


LOGGER = logging.getLogger("catgirl.plugins.regex_filter")
MAX_RULES_PER_SCOPE = 200
MAX_PATTERN_LENGTH = 10_000
REGEX_TIMEOUT_SECONDS = 0.05
SUPPORTED_FLAGS = "ims"


def _text(value: Any, maximum: int) -> str:
    return str(value or "")[:maximum]


def _normalize_rule(value: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    rule_id = _text(value.get("id"), 80).strip() or f"rule-{index + 1}"
    flags = "".join(flag for flag in SUPPORTED_FLAGS if flag in _text(value.get("flags"), 10))
    return {
        "id": rule_id,
        "name": _text(value.get("name"), 120).strip() or f"正则 {index + 1}",
        "enabled": bool(value.get("enabled", False)),
        "pattern": _text(value.get("pattern"), MAX_PATTERN_LENGTH),
        "replacement": _text(value.get("replacement"), 100_000),
        "flags": flags,
    }


def _normalize_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value[:MAX_RULES_PER_SCOPE]):
        rule = _normalize_rule(item, index)
        if rule is None:
            continue
        base_id = rule["id"]
        suffix = 2
        while rule["id"] in seen_ids:
            rule["id"] = f"{base_id}-{suffix}"[:80]
            suffix += 1
        seen_ids.add(rule["id"])
        output.append(rule)
    return output


def _replacement_for_python(value: str) -> str:
    value = re.sub(r"\$\{([A-Za-z_]\w*)\}", r"\\g<\1>", value)
    return re.sub(r"\$(\d+)", r"\\g<\1>", value)


def _regex_flags(value: str) -> int:
    flags = 0
    if "i" in value:
        flags |= regex.IGNORECASE
    if "m" in value:
        flags |= regex.MULTILINE
    if "s" in value:
        flags |= regex.DOTALL
    return flags


class RegexFilterPlugin:
    def normalize_state(self, value: Any) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        character_source = source.get("character_rules")
        character_rules = {}
        if isinstance(character_source, dict):
            for character_id, rules in character_source.items():
                normalized_id = _text(character_id, 80).strip()
                if normalized_id:
                    character_rules[normalized_id] = _normalize_rules(rules)
        return {
            "global_rules": _normalize_rules(source.get("global_rules")),
            "character_rules": character_rules,
        }

    def validate_state(self, state: dict[str, Any]) -> None:
        scopes = [state.get("global_rules", [])]
        character_rules = state.get("character_rules", {})
        if isinstance(character_rules, dict):
            scopes.extend(character_rules.values())
        for rules in scopes:
            for rule in rules if isinstance(rules, list) else []:
                pattern = str(rule.get("pattern", ""))
                if not pattern:
                    continue
                try:
                    regex.compile(pattern, _regex_flags(str(rule.get("flags", ""))))
                except regex.error as exc:
                    raise ValueError(f"正则“{rule.get('name', '未命名')}”无效：{exc}") from exc

    def _apply_rule(self, text: str, rule: dict[str, Any]) -> str:
        pattern = str(rule.get("pattern", ""))
        if not pattern:
            return text
        try:
            compiled = regex.compile(pattern, _regex_flags(str(rule.get("flags", ""))))
            return compiled.sub(
                _replacement_for_python(str(rule.get("replacement", ""))),
                text,
                timeout=REGEX_TIMEOUT_SECONDS,
            )
        except (regex.error, TimeoutError) as exc:
            LOGGER.warning("跳过无效或超时的正则 | rule=%s | %s", rule.get("name"), exc)
            return text

    def _rules_for_event(self, context, event: PluginEvent) -> tuple[list[dict[str, Any]], str]:
        state = self.normalize_state(context.state)
        rules = list(state["global_rules"])
        character_id = str(event.metadata.get("character_id") or "").strip()
        if character_id:
            rules.extend(state["character_rules"].get(character_id, []))
        return rules, character_id

    def before_response_split(self, context, event: PluginEvent) -> PluginResult:
        text = event.response_text or event.text
        delimiter = str(event.metadata.get("delimiter") or "")
        if not text or not delimiter:
            return PluginResult()
        rules, character_id = self._rules_for_event(context, event)
        protected_offsets: set[int] = set()
        for rule in rules:
            if not rule["enabled"]:
                continue
            pattern = str(rule.get("pattern", ""))
            if not pattern:
                continue
            try:
                compiled = regex.compile(pattern, _regex_flags(str(rule.get("flags", ""))))
                for match in compiled.finditer(text, timeout=REGEX_TIMEOUT_SECONDS):
                    offset = text.find(delimiter, match.start(), match.end())
                    while offset >= 0:
                        protected_offsets.add(offset)
                        offset = text.find(delimiter, offset + len(delimiter), match.end())
            except (regex.error, TimeoutError) as exc:
                LOGGER.warning("跳过无效或超时的正则 | rule=%s | %s", rule.get("name"), exc)
        return PluginResult(
            metadata={
                "regex_filter": {
                    "protected_delimiter_offsets": sorted(protected_offsets),
                    "character_id": character_id,
                }
            }
        )

    def before_send(self, context, event: PluginEvent) -> PluginResult:
        rules, character_id = self._rules_for_event(context, event)

        text = event.response_text or event.text
        applied = []
        for rule in rules:
            if not rule["enabled"]:
                continue
            updated = self._apply_rule(text, rule)
            if updated != text:
                applied.append(rule["id"])
                text = updated
        return PluginResult(
            metadata={
                "outbound_text": text,
                "regex_filter": {
                    "applied_rule_ids": applied,
                    "character_id": character_id,
                },
            }
        )


plugin = RegexFilterPlugin()
