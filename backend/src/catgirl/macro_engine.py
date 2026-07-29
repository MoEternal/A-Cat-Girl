from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


SIMPLE_MACRO_PATTERN = re.compile(
    r"{{\s*([.$][A-Za-z_][A-Za-z0-9_.-]*|[A-Za-z_][A-Za-z0-9_.]*|//)\s*(?:(?:::|:|\s+)\s*([^{}]*?))?\s*}}",
    re.IGNORECASE,
)
SCOPED_IF_PATTERN = re.compile(
    r"{{\s*if(?:\s+|::)([^{}]+?)\s*}}((?:(?!{{\s*if(?:\s+|::)|{{\s*/if\s*}}).)*){{\s*/if\s*}}",
    re.IGNORECASE | re.DOTALL,
)
SCOPED_TRIM_PATTERN = re.compile(
    r"{{\s*trim\s*}}([\s\S]*?){{\s*/trim\s*}}",
    re.IGNORECASE,
)


@dataclass
class MacroContext:
    user_name: str = "用户"
    user_persona: str = ""
    character_name: str = "当前角色"
    character_description: str = ""
    character_personality: str = ""
    character_scenario: str = ""
    character_first_message: str = ""
    character_examples: str = ""
    character_prompt: str = ""
    character_instruction: str = ""
    character_depth_prompt: str = ""
    character_creator_notes: str = ""
    character_version: str = ""
    model: str = ""
    group: str = ""
    group_not_muted: str = ""
    not_char: str = ""
    input_text: str = "[当前输入将在运行时插入]"
    original: str = ""
    last_message: str = "[最后一条消息将在运行时插入]"
    last_user_message: str = "[最后一条用户消息将在运行时插入]"
    last_char_message: str = "[最后一条角色消息将在运行时插入]"
    last_message_id: str = "0"
    first_included_message_id: str = "0"
    first_displayed_message_id: str = "0"
    last_swipe_id: str = "1"
    current_swipe_id: str = "1"
    message_count: int = 1
    max_prompt_tokens: int = 125_952
    max_context_tokens: int = 128_000
    max_response_tokens: int = 2_048
    idle_duration: str = "刚刚"
    last_generation_type: str = ""
    is_mobile: bool = False
    now: datetime = field(default_factory=datetime.now)
    random_seed: str = "preview"
    outlets: dict[str, str] = field(default_factory=dict)
    local_variables: dict[str, str | int | float] = field(default_factory=dict)
    global_variables: dict[str, str | int | float] = field(default_factory=dict)
    instruct_values: dict[str, str] = field(default_factory=dict)
    original_consumed: bool = False


@dataclass(frozen=True)
class MacroRenderResult:
    content: str
    unresolved: set[str]


def _catalog(category: str, names: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [
        {"name": name, "syntax": syntax, "category": category}
        for name, syntax in names
    ]


MACRO_CATALOG = [
    *_catalog("名称与人设", [
        ("user", "{{user}}"), ("char", "{{char}}"), ("group", "{{group}}"),
        ("groupNotMuted", "{{groupNotMuted}}"), ("notChar", "{{notChar}}"),
        ("persona", "{{persona}}"), ("charDescription", "{{charDescription}}"),
        ("charPersonality", "{{charPersonality}}"), ("charScenario", "{{charScenario}}"),
        ("charFirstMessage", "{{charFirstMessage}}"), ("mesExamples", "{{mesExamples}}"),
        ("mesExamplesRaw", "{{mesExamplesRaw}}"), ("charPrompt", "{{charPrompt}}"),
        ("charInstruction", "{{charInstruction}}"), ("charDepthPrompt", "{{charDepthPrompt}}"),
        ("charCreatorNotes", "{{charCreatorNotes}}"), ("charVersion", "{{charVersion}}"),
        ("original", "{{original}}"),
    ]),
    *_catalog("聊天上下文", [
        ("input", "{{input}}"), ("lastMessage", "{{lastMessage}}"),
        ("lastUserMessage", "{{lastUserMessage}}"), ("lastCharMessage", "{{lastCharMessage}}"),
        ("lastMessageId", "{{lastMessageId}}"), ("firstIncludedMessageId", "{{firstIncludedMessageId}}"),
        ("firstDisplayedMessageId", "{{firstDisplayedMessageId}}"), ("allChatRange", "{{allChatRange}}"),
        ("lastSwipeId", "{{lastSwipeId}}"), ("currentSwipeId", "{{currentSwipeId}}"),
    ]),
    *_catalog("模型与上下文", [
        ("model", "{{model}}"), ("maxPrompt", "{{maxPrompt}}"),
        ("maxContext", "{{maxContext}}"), ("maxResponse", "{{maxResponse}}"),
        ("lastGenerationType", "{{lastGenerationType}}"), ("isMobile", "{{isMobile}}"),
        ("hasExtension", "{{hasExtension::扩展名}}"),
    ]),
    *_catalog("时间", [
        ("time", "{{time}}"), ("date", "{{date}}"), ("weekday", "{{weekday}}"),
        ("isotime", "{{isotime}}"), ("isodate", "{{isodate}}"),
        ("datetimeformat", "{{datetimeformat::YYYY-MM-DD HH:mm:ss}}"),
        ("idleDuration", "{{idleDuration}}"),
        ("timeDiff", "{{timeDiff::2026-01-01::2026-01-02}}"),
    ]),
    *_catalog("工具", [
        ("space", "{{space::4}}"), ("newline", "{{newline::2}}"), ("trim", "{{trim}}"),
        ("noop", "{{noop}}"), ("reverse", "{{reverse::文本}}"),
        ("random", "{{random::甲::乙::丙}}"), ("pick", "{{pick::甲::乙::丙}}"),
        ("roll", "{{roll::1d20}}"), ("if", "{{if user}}内容{{/if}}"),
        ("banned", "{{banned::词语}}"), ("comment", "{{// 注释}}"),
        ("outlet", "{{outlet::名称}}"),
    ]),
    *_catalog("变量", [
        ("setvar", "{{setvar::名称::值}}"), ("getvar", "{{getvar::名称}}"),
        ("addvar", "{{addvar::名称::值}}"), ("incvar", "{{incvar::名称}}"),
        ("decvar", "{{decvar::名称}}"), ("hasvar", "{{hasvar::名称}}"),
        ("deletevar", "{{deletevar::名称}}"), ("setglobalvar", "{{setglobalvar::名称::值}}"),
        ("getglobalvar", "{{getglobalvar::名称}}"), ("addglobalvar", "{{addglobalvar::名称::值}}"),
        ("incglobalvar", "{{incglobalvar::名称}}"), ("decglobalvar", "{{decglobalvar::名称}}"),
        ("hasglobalvar", "{{hasglobalvar::名称}}"), ("deleteglobalvar", "{{deleteglobalvar::名称}}"),
    ]),
    *_catalog("提示模板", [
        ("systemPrompt", "{{systemPrompt}}"), ("defaultSystemPrompt", "{{defaultSystemPrompt}}"),
        ("instructStoryStringPrefix", "{{instructStoryStringPrefix}}"),
        ("instructStoryStringSuffix", "{{instructStoryStringSuffix}}"),
        ("instructUserPrefix", "{{instructUserPrefix}}"), ("instructUserSuffix", "{{instructUserSuffix}}"),
        ("instructAssistantPrefix", "{{instructAssistantPrefix}}"), ("instructAssistantSuffix", "{{instructAssistantSuffix}}"),
        ("instructSystemPrefix", "{{instructSystemPrefix}}"), ("instructSystemSuffix", "{{instructSystemSuffix}}"),
        ("instructFirstAssistantPrefix", "{{instructFirstAssistantPrefix}}"),
        ("instructLastAssistantPrefix", "{{instructLastAssistantPrefix}}"),
        ("instructFirstUserPrefix", "{{instructFirstUserPrefix}}"),
        ("instructLastUserPrefix", "{{instructLastUserPrefix}}"),
        ("instructStop", "{{instructStop}}"), ("instructUserFiller", "{{instructUserFiller}}"),
        ("instructSystemInstructionPrefix", "{{instructSystemInstructionPrefix}}"),
        ("exampleSeparator", "{{exampleSeparator}}"), ("chatStart", "{{chatStart}}"),
    ]),
]


ALIASES = {
    "charifnotgroup": "group", "description": "chardescription",
    "personality": "charpersonality", "scenario": "charscenario",
    "creatornotes": "charcreatornotes", "greeting": "charfirstmessage",
    "version": "charversion", "char_version": "charversion",
    "maxprompttokens": "maxprompt", "maxcontexttokens": "maxcontext",
    "maxresponsetokens": "maxresponse", "idle_duration": "idleduration",
    "comment": "//",
    "varexists": "hasvar", "flushvar": "deletevar",
    "globalvarexists": "hasglobalvar", "flushglobalvar": "deleteglobalvar",
    "instructinput": "instructuserprefix", "instructoutput": "instructassistantprefix",
    "instructseparator": "instructassistantsuffix",
    "instructfirstoutputprefix": "instructfirstassistantprefix",
    "instructlastoutputprefix": "instructlastassistantprefix",
    "instructfirstinput": "instructfirstuserprefix",
    "instructlastinput": "instructlastuserprefix",
    "instructsystem": "defaultsystemprompt", "instructsystemprompt": "defaultsystemprompt",
    "chatseparator": "exampleseparator",
}

INSTRUCT_NAMES = {
    "instructstorystringprefix", "instructstorystringsuffix", "instructuserprefix",
    "instructusersuffix", "instructassistantprefix", "instructassistantsuffix",
    "instructsystemprefix", "instructsystemsuffix", "instructfirstassistantprefix",
    "instructlastassistantprefix", "instructstop", "instructuserfiller",
    "instructsysteminstructionprefix", "instructfirstuserprefix", "instructlastuserprefix",
    "defaultsystemprompt", "systemprompt", "exampleseparator", "chatstart",
}


def _split_args(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [part.strip() for part in raw.split("::")]


def _number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _add_value(current: Any, value: Any) -> str | int | float:
    left = _number(current)
    right = _number(value)
    if left is not None and right is not None:
        return left + right
    return f"{current or ''}{value}"


def _moment_format(now: datetime, pattern: str) -> str:
    replacements = [
        ("YYYY", "%Y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"),
        ("DD", "%d"), ("dddd", "%A"), ("ddd", "%a"), ("HH", "%H"),
        ("hh", "%I"), ("mm", "%M"), ("ss", "%S"), ("A", "%p"),
    ]
    result = pattern
    for source, target in replacements:
        result = result.replace(source, target)
    try:
        return now.strftime(result)
    except ValueError:
        return now.isoformat(sep=" ", timespec="seconds")


def _human_duration(delta: timedelta) -> str:
    seconds = abs(int(delta.total_seconds()))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        return f"{seconds // 3600} 小时"
    return f"{seconds // 86400} 天"


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _roll(formula: str, rng: random.Random) -> str:
    formula = formula.strip()
    if formula.isdigit():
        formula = f"1d{formula}"
    match = re.fullmatch(r"(\d*)d(\d+)(?:\s*([+-])\s*(\d+))?", formula, re.IGNORECASE)
    if not match:
        return ""
    count = min(100, int(match.group(1) or 1))
    sides = int(match.group(2))
    if sides < 1:
        return ""
    total = sum(rng.randint(1, sides) for _ in range(count))
    if match.group(4):
        modifier = int(match.group(4))
        total += modifier if match.group(3) == "+" else -modifier
    return str(total)


def _condition_truthy(condition: str, context: MacroContext) -> bool:
    inverted = condition.strip().startswith("!")
    key = condition.strip().lstrip("!").strip()
    if key.startswith("."):
        value: Any = context.local_variables.get(key[1:], "")
    elif key.startswith("$"):
        value = context.global_variables.get(key[1:], "")
    else:
        value = _resolve_macro(key, [], context, "", 0)
        if value is None:
            value = key
    result = str(value).strip().casefold() not in {"", "0", "false", "off", "none", "null"}
    return not result if inverted else result


def _resolve_macro(
    raw_name: str,
    args: list[str],
    context: MacroContext,
    raw_content: str,
    offset: int,
) -> str | None:
    name = ALIASES.get(raw_name.casefold(), raw_name.casefold())
    if name.startswith("."):
        return str(context.local_variables.get(raw_name[1:], ""))
    if name.startswith("$"):
        return str(context.global_variables.get(raw_name[1:], ""))
    group = context.group or context.character_name
    values = {
        "user": context.user_name,
        "char": context.character_name,
        "group": group,
        "groupnotmuted": context.group_not_muted or group,
        "notchar": context.not_char or context.user_name,
        "charprompt": context.character_prompt,
        "charinstruction": context.character_instruction,
        "chardescription": context.character_description,
        "charpersonality": context.character_personality,
        "charscenario": context.character_scenario,
        "persona": context.user_persona,
        "mesexamplesraw": context.character_examples or context.character_first_message,
        "mesexamples": context.character_examples or context.character_first_message,
        "chardepthprompt": context.character_depth_prompt,
        "charcreatornotes": context.character_creator_notes,
        "charversion": context.character_version,
        "model": context.model,
        "ismobile": str(context.is_mobile).lower(),
        "input": context.input_text,
        "maxprompt": str(context.max_prompt_tokens),
        "maxcontext": str(context.max_context_tokens),
        "maxresponse": str(context.max_response_tokens),
        "lastmessage": context.last_message,
        "lastmessageid": context.last_message_id,
        "lastusermessage": context.last_user_message,
        "lastcharmessage": context.last_char_message,
        "firstincludedmessageid": context.first_included_message_id,
        "firstdisplayedmessageid": context.first_displayed_message_id,
        "lastswipeid": context.last_swipe_id,
        "currentswipeid": context.current_swipe_id,
        "allchatrange": "" if context.message_count <= 0 else f"0-{context.message_count - 1}",
        "idleduration": context.idle_duration,
        "lastgenerationtype": context.last_generation_type,
    }
    if name in values:
        return values[name]
    if name.startswith("character."):
        project_values = {
            "character.name": context.character_name,
            "character.summary": context.character_description,
            "character.persona": context.character_personality,
            "character.scenario": context.character_scenario,
            "character.first_message": context.character_first_message,
        }
        return project_values.get(name)
    if name == "charfirstmessage":
        return context.character_first_message if not args or args[0] in {"", "0"} else ""
    if name == "original":
        if context.original_consumed:
            return ""
        context.original_consumed = True
        return context.original
    if name == "space":
        return " " * max(0, min(1000, int(args[0] if args else 1)))
    if name == "newline":
        return "\n" * max(0, min(1000, int(args[0] if args else 1)))
    if name in {"noop", "trim", "//", "banned", "else"}:
        return ""
    if name == "reverse":
        return "".join(reversed(args[0] if args else ""))
    if name in {"random", "pick"}:
        choices = args
        if len(choices) == 1:
            choices = [part.strip() for part in choices[0].split(",")]
        choices = [item for item in choices if item]
        if not choices:
            return ""
        if name == "random":
            rng = random.Random(f"{context.random_seed}:{raw_content}:{offset}:random")
        else:
            digest = hashlib.sha256(
                f"{context.random_seed}:{raw_content}:{offset}:pick".encode("utf-8")
            ).digest()
            rng = random.Random(int.from_bytes(digest[:8], "big"))
        return choices[rng.randrange(len(choices))]
    if name == "roll":
        return _roll(args[0] if args else "", random.Random(f"{context.random_seed}:{offset}:roll"))
    if name == "outlet":
        return context.outlets.get(args[0] if args else "", "")
    if name == "hasextension":
        return "false"
    if name == "time":
        now = context.now
        if args and re.fullmatch(r"UTC[+-]\d+", args[0], re.IGNORECASE):
            hours = int(args[0][3:])
            now = now.astimezone(timezone.utc).astimezone(timezone(timedelta(hours=hours)))
        return now.strftime("%H:%M")
    if name == "date":
        return context.now.strftime("%Y年%m月%d日")
    if name == "weekday":
        return ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")[context.now.weekday()]
    if name == "isotime":
        return context.now.strftime("%H:%M")
    if name == "isodate":
        return context.now.strftime("%Y-%m-%d")
    if name == "datetimeformat":
        return _moment_format(context.now, args[0] if args else "YYYY-MM-DD HH:mm:ss")
    if name == "timediff":
        if len(args) < 2:
            return ""
        left, right = _parse_datetime(args[0]), _parse_datetime(args[1])
        return _human_duration(left - right) if left and right else ""
    if name == "if":
        if len(args) < 2:
            return ""
        return args[1] if _condition_truthy(args[0], context) else (args[2] if len(args) > 2 else "")
    if name in INSTRUCT_NAMES:
        return context.instruct_values.get(name, "")
    if name in {"setvar", "addvar", "incvar", "decvar", "getvar", "hasvar", "deletevar",
                "setglobalvar", "addglobalvar", "incglobalvar", "decglobalvar",
                "getglobalvar", "hasglobalvar", "deleteglobalvar"}:
        is_global = "global" in name
        variables = context.global_variables if is_global else context.local_variables
        operation = name.replace("global", "")
        key = args[0] if args else ""
        if not key:
            return ""
        if operation == "setvar":
            variables[key] = args[1] if len(args) > 1 else ""
            return ""
        if operation == "addvar":
            variables[key] = _add_value(variables.get(key, ""), args[1] if len(args) > 1 else "")
            return ""
        if operation in {"incvar", "decvar"}:
            current = _number(variables.get(key, 0)) or 0
            variables[key] = current + (1 if operation == "incvar" else -1)
            return str(variables[key])
        if operation == "getvar":
            return str(variables.get(key, ""))
        if operation == "hasvar":
            return str(key in variables).lower()
        if operation == "deletevar":
            variables.pop(key, None)
            return ""
    return None


def render_macros(content: str, context: MacroContext) -> MacroRenderResult:
    if not content:
        return MacroRenderResult("", set())
    raw_content = content
    replacements = {
        "<USER>": context.user_name,
        "<BOT>": context.character_name,
        "<CHAR>": context.character_name,
        "<CHARIFNOTGROUP>": context.group or context.character_name,
        "<GROUP>": context.group or context.character_name,
    }
    for key, value in replacements.items():
        content = re.sub(re.escape(key), lambda _match, result=value: result, content, flags=re.IGNORECASE)
    content = SCOPED_TRIM_PATTERN.sub(lambda match: match.group(1).strip(), content)
    content = re.sub(r"(?:\r?\n)*{{\s*trim\s*}}(?:\r?\n)*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"{{\s*//\s*}}[\s\S]*?{{\s*///\s*}}", "", content, flags=re.IGNORECASE)
    content = re.sub(r"{{\s*time_UTC([+-]\d+)\s*}}", lambda match: _resolve_macro("time", [f"UTC{match.group(1)}"], context, raw_content, match.start()) or "", content, flags=re.IGNORECASE)

    for _ in range(20):
        match = SCOPED_IF_PATTERN.search(content)
        if not match:
            break
        branch = match.group(2)
        parts = re.split(r"{{\s*else\s*}}", branch, maxsplit=1, flags=re.IGNORECASE)
        then_branch = parts[0]
        else_branch = parts[1] if len(parts) > 1 else ""
        selected = then_branch if _condition_truthy(match.group(1), context) else else_branch
        content = content[:match.start()] + selected.strip() + content[match.end():]

    unresolved: set[str] = set()
    for _ in range(20):
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            name = match.group(1)
            args = _split_args(match.group(2))
            value = _resolve_macro(name, args, context, raw_content, match.start())
            if value is None:
                unresolved.add(name)
                return match.group(0)
            changed = True
            return value

        updated = SIMPLE_MACRO_PATTERN.sub(replace, content)
        content = updated
        if not changed or "{{" not in content:
            break

    for match in SIMPLE_MACRO_PATTERN.finditer(content):
        unresolved.add(match.group(1))
    return MacroRenderResult(content, unresolved)
