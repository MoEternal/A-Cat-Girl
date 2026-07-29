from __future__ import annotations

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


DELIMITER = "|||"


def _segments(text: str, maximum: int, protected_offsets: set[int] | None = None) -> list[str]:
    protected = protected_offsets or set()
    values = []
    start = 0
    while True:
        offset = text.find(DELIMITER, start)
        while offset in protected:
            offset = text.find(DELIMITER, offset + len(DELIMITER))
        if offset < 0:
            values.append(text[start:])
            break
        values.append(text[start:offset])
        start = offset + len(DELIMITER)
    values = [item.strip() for item in values if item.strip()]
    if len(values) <= maximum:
        return values
    if maximum <= 1:
        return [" ".join(values)]
    return [*values[: maximum - 1], " ".join(values[maximum - 1 :])]


class SegmentedReplyPlugin:
    def before_prompt_compile(self, context, event: PluginEvent) -> PluginResult:
        content = str(context.settings.get("prompt", "")).strip()
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

    def after_model_response(self, context, event: PluginEvent) -> PluginResult:
        if DELIMITER not in event.response_text:
            return PluginResult()
        maximum = max(1, min(int(context.settings.get("max_segments", 5)), 20))
        split_metadata = event.metadata.get("response_split_metadata", {})
        regex_metadata = split_metadata.get("regex_filter", {}) if isinstance(split_metadata, dict) else {}
        raw_offsets = regex_metadata.get("protected_delimiter_offsets", []) if isinstance(regex_metadata, dict) else []
        protected_offsets = {
            offset
            for offset in raw_offsets
            if isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset < len(event.response_text)
        }
        segments = _segments(event.response_text, maximum, protected_offsets)
        if len(segments) < 2:
            return PluginResult()
        return PluginResult(
            actions=[
                PluginAction(
                    kind="replace_response",
                    payload={
                        "text_segments": segments,
                        "segment_reply": {
                            "max_segments": maximum,
                            "base_delay_seconds": float(context.settings.get("base_delay_seconds", 0.8)),
                            "seconds_per_text_unit": float(context.settings.get("seconds_per_text_unit", 0.18)),
                            "max_delay_seconds": float(context.settings.get("max_delay_seconds", 8)),
                        },
                    },
                )
            ]
        )


plugin = SegmentedReplyPlugin()
