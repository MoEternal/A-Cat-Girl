from __future__ import annotations

from catgirl.plugins import PluginEvent, PluginResult


class RecallPlugin:
    def on_message_recall(self, context, event: PluginEvent) -> PluginResult:
        try:
            age_seconds = float(event.metadata.get("age_seconds", -1))
        except (TypeError, ValueError):
            return PluginResult()
        window_seconds = max(1, min(int(context.settings.get("recall_window_seconds", 120)), 600))
        if age_seconds < 0 or age_seconds >= window_seconds:
            return PluginResult()
        return PluginResult(metadata={"allow_rollback": True})


plugin = RecallPlugin()
