from __future__ import annotations

from datetime import datetime, timezone

from catgirl.plugins import PluginAction, PluginEvent, PluginResult


WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
HOLIDAYS = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (3, 12): "植树节",
    (4, 1): "愚人节",
    (5, 1): "劳动节",
    (6, 1): "儿童节",
    (10, 1): "国庆节",
    (10, 31): "万圣节",
    (12, 24): "平安夜",
    (12, 25): "圣诞节",
}
DEFAULT_PROMPT = (
    "<Time_Awareness>\n"
    "# 时间感知\n"
    "- 你能自然感知现实中的当前日期、星期、时段（清晨/上午/深夜等）。\n"
    "</Time_Awareness>"
)


def _period(hour: int) -> str:
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "深夜"


def _offset_text(moment: datetime) -> str:
    offset = moment.utcoffset()
    if offset is None:
        return "本地时区"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _local_moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


def _message_timeline(context, record_id: str) -> str:
    if not record_id:
        return ""
    messages = context.get_conversation_messages(record_id, limit=12)
    lines = []
    for message in messages:
        moment = _local_moment(str(message.get("created_at", "")))
        if moment is None:
            continue
        role = "用户" if message.get("role") == "user" else "角色"
        lines.append(f"- {role}：{moment.strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


class TimeAwarenessPlugin:
    def before_prompt_compile(self, context, event: PluginEvent) -> PluginResult:
        moment = event.created_at.astimezone()
        weekday = WEEKDAYS[moment.weekday()]
        period = _period(moment.hour)
        timezone_name = _offset_text(moment)
        holiday = HOLIDAYS.get((moment.month, moment.day), "")
        display = (
            f"{moment.year}年{moment.month:02d}月{moment.day:02d}日 "
            f"{weekday} {period} {moment.strftime('%H:%M')}（{timezone_name}）"
        )
        memory_time = (
            f"{moment.strftime('%Y-%m-%d %H:%M')}（{weekday} {period}，{timezone_name}）"
        )
        timeline = _message_timeline(
            context,
            str(event.metadata.get("record_id", "")).strip(),
        )
        dynamic_parts = [f"- 当前现实时间：{display}。"]
        if holiday:
            dynamic_parts.append(f"- 今天是{holiday}。")
        if timeline:
            dynamic_parts.extend(("- 最近消息的现实时间：", timeline))
        prompt = str(context.settings.get("prompt", DEFAULT_PROMPT)).strip()
        dynamic_context = "\n".join(dynamic_parts)
        closing_tag = "</Time_Awareness>"
        if closing_tag in prompt:
            content = prompt.replace(closing_tag, f"\n## 当前时间背景\n{dynamic_context}\n{closing_tag}", 1)
        else:
            content = "\n".join(part for part in (prompt, dynamic_context) if part)
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
            ],
            metadata={
                "time_awareness": {
                    "iso": moment.isoformat(timespec="seconds"),
                    "display": display,
                    "memory_time": memory_time,
                }
            },
        )


plugin = TimeAwarenessPlugin()
