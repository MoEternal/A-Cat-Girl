from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _aware(parsed)


def _keywords(settings: dict) -> list[str]:
    return [item.strip() for item in re.split(r"[,，\n]", str(settings.get("keywords", ""))) if item.strip()]


class GoodNightPlugin:
    def on_startup(self, context, _event: PluginEvent) -> None:
        for conversation_id, item in context.state.get("conversations", {}).items():
            if item.get("sleeping"):
                context.set_runtime_value(f"conversation:{conversation_id}:sleeping", True)
        context.schedule_interval("wake-check", int(context.settings.get("check_interval_seconds", 60)), self.check_wake)

    def on_shutdown(self, context, _event: PluginEvent) -> None:
        context.cancel_schedule("wake-check")

    def on_user_message(self, context, event: PluginEvent) -> PluginResult:
        state = context.state
        conversation = state.get("conversations", {}).get(event.conversation_id)
        if not conversation or not conversation.get("sleeping"):
            return PluginResult()
        now = _aware(event.created_at)
        if now >= _parse(conversation["wake_at"]):
            return self._wake(context, state, event.conversation_id, now)

        safe_text = context.ensure_text_safe(event.text)
        pending = conversation.setdefault("pending_messages", [])
        pending.append(
            {
                "at": now.isoformat(),
                "text": safe_text,
                "media": [item.model_dump() for item in event.media],
            }
        )
        conversation["pending_messages"] = pending[-100:]
        context.replace_state(state)
        return PluginResult(
            actions=[PluginAction(kind="message_buffered", payload={"conversation_id": event.conversation_id, "pending_count": len(conversation["pending_messages"])})],
            consume=True,
        )

    def after_model_response(self, context, event: PluginEvent) -> PluginResult:
        settings = context.settings
        keywords = _keywords(settings)
        if not keywords or not any(item in event.text for item in keywords) or not any(item in event.response_text for item in keywords):
            return PluginResult()

        now = _aware(event.created_at)
        minimum = float(settings.get("min_sleep_hours", 7.5))
        maximum = max(minimum, float(settings.get("max_sleep_hours", 9.0)))
        wake_at = now + timedelta(hours=random.uniform(minimum, maximum))
        state = context.state
        state.setdefault("conversations", {})[event.conversation_id] = {
            "sleeping": True,
            "sleep_started_at": now.isoformat(),
            "wake_at": wake_at.isoformat(),
            "pending_messages": [],
        }
        context.replace_state(state)
        context.set_runtime_value(f"conversation:{event.conversation_id}:sleeping", True)
        return PluginResult(
            actions=[PluginAction(kind="sleep_started", payload={"conversation_id": event.conversation_id, "wake_at": wake_at.isoformat()})]
        )

    def check_wake(self, context) -> PluginResult:
        state = context.state
        now = datetime.now(timezone.utc)
        actions: list[PluginAction] = []
        for conversation_id, conversation in list(state.get("conversations", {}).items()):
            if conversation.get("sleeping") and now >= _parse(conversation["wake_at"]):
                result = self._wake(context, state, conversation_id, now, persist=False)
                actions.extend(result.actions)
        context.replace_state(state)
        return PluginResult(actions=actions)

    def _wake(self, context, state: dict, conversation_id: str, now: datetime, persist: bool = True) -> PluginResult:
        settings = context.settings
        conversation = state.setdefault("conversations", {}).setdefault(conversation_id, {})
        pending = list(conversation.get("pending_messages", []))
        conversation.update({"sleeping": False, "woke_at": now.isoformat(), "pending_messages": []})
        context.set_runtime_value(f"conversation:{conversation_id}:sleeping", False)
        actions = [
            PluginAction(
                kind="request_generation",
                payload={
                    "conversation_id": conversation_id,
                    "purpose": "wake_greeting",
                    "sequence": 0,
                    "provider_policy": "selected_only",
                    "history_policy": "temporary_prompt",
                    "prompt": str(settings.get("wake_greeting_prompt", "")),
                },
            )
        ]
        if pending:
            lines = [f"[{item['at']}] {item['text'] or '（图片）'}" for item in pending]
            media_refs = [media for item in pending for media in item.get("media", [])]
            actions.append(
                PluginAction(
                    kind="request_generation",
                    payload={
                        "conversation_id": conversation_id,
                        "purpose": "sleep_pending_reply",
                        "sequence": 1,
                        "provider_policy": "selected_only",
                        "history_policy": "temporary_prompt",
                        "prompt": f"{settings.get('pending_reply_prompt', '')}\n\n" + "\n".join(lines),
                        "media_refs": media_refs,
                    },
                )
            )
        if persist:
            context.replace_state(state)
        return PluginResult(actions=actions, consume=True)


plugin = GoodNightPlugin()
