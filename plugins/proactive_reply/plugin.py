from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


def _now(event: PluginEvent | None = None) -> datetime:
    value = event.created_at if event is not None else datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class ProactiveReplyPlugin:
    def on_startup(self, context, _event: PluginEvent) -> None:
        interval = int(context.settings.get("check_interval_seconds", 60))
        context.schedule_interval("idle-check", interval, self.check_idle)

    def on_shutdown(self, context, _event: PluginEvent) -> None:
        context.cancel_schedule("idle-check")

    def on_user_message(self, context, event: PluginEvent) -> PluginResult:
        settings = context.settings
        state = context.state
        text = event.text.strip()
        if text == settings.get("pause_command", "/暂停"):
            state["paused"] = True
            context.replace_state(state)
            return PluginResult(
                actions=[PluginAction(kind="send_text", payload={"conversation_id": event.conversation_id, "text": "主动回复已暂停"})],
                consume=True,
            )
        if text == settings.get("resume_command", "/恢复"):
            state["paused"] = False
            self._touch_conversation(state, event.conversation_id, settings, _now(event))
            context.replace_state(state)
            return PluginResult(
                actions=[PluginAction(kind="send_text", payload={"conversation_id": event.conversation_id, "text": "主动回复已恢复"})],
                consume=True,
            )

        self._touch_conversation(state, event.conversation_id, settings, _now(event))
        context.replace_state(state)
        return PluginResult()

    @staticmethod
    def _touch_conversation(state: dict, conversation_id: str, settings: dict, now: datetime) -> None:
        minimum = int(settings.get("min_minutes", 10))
        maximum = max(minimum, int(settings.get("max_minutes", 60)))
        delay = random.uniform(minimum, maximum)
        conversations = state.setdefault("conversations", {})
        conversations[conversation_id] = {
            "last_user_message_at": now.isoformat(),
            "next_due_at": (now + timedelta(minutes=delay)).isoformat(),
            "proactive_count": 0,
        }

    def check_idle(self, context) -> PluginResult:
        settings = context.settings
        state = context.state
        if state.get("paused", False):
            return PluginResult()
        now = datetime.now(timezone.utc)
        actions: list[PluginAction] = []
        maximum_count = int(settings.get("max_messages", 2))
        minimum = int(settings.get("min_minutes", 10))
        maximum = max(minimum, int(settings.get("max_minutes", 60)))
        for conversation_id, item in state.get("conversations", {}).items():
            if context.get_runtime_value(f"conversation:{conversation_id}:sleeping", False):
                continue
            count = int(item.get("proactive_count", 0))
            due_at = item.get("next_due_at")
            if count >= maximum_count or not due_at or now < _parse(due_at):
                continue
            prompt_key = "first_prompt" if count == 0 else "second_prompt"
            actions.append(
                PluginAction(
                    kind="request_generation",
                    payload={
                        "conversation_id": conversation_id,
                        "purpose": "proactive_reply",
                        "provider_policy": "selected_only",
                        "history_policy": "temporary_prompt",
                        "prompt": str(settings.get(prompt_key, "")),
                    },
                )
            )
            item["proactive_count"] = count + 1
            item["next_due_at"] = (now + timedelta(minutes=random.uniform(minimum, maximum))).isoformat()
        context.replace_state(state)
        return PluginResult(actions=actions)


plugin = ProactiveReplyPlugin()
