from datetime import datetime

from catgirl.macro_engine import MACRO_CATALOG, MacroContext, render_macros


def test_official_environment_chat_state_and_time_macros_resolve() -> None:
    context = MacroContext(
        user_name="旅行者",
        user_persona="来自远方的记录者",
        character_name="小满",
        character_description="测试角色",
        character_personality="冷静",
        character_scenario="旅馆",
        character_first_message="你好",
        character_examples="示例对话",
        character_prompt="主提示覆盖",
        character_instruction="历史后指令",
        character_depth_prompt="深度注释",
        character_creator_notes="作者备注",
        character_version="1.2",
        model="example-model",
        input_text="当前输入",
        original="原始文本",
        last_message="最后消息",
        last_user_message="用户最后消息",
        last_char_message="角色最后消息",
        message_count=4,
        max_prompt_tokens=7000,
        max_context_tokens=8192,
        max_response_tokens=1192,
        now=datetime(2026, 7, 24, 21, 45, 30),
    )
    content = "|".join(
        [
            "{{user}}", "{{char}}", "{{persona}}", "{{description}}", "{{personality}}",
            "{{scenario}}", "{{greeting}}", "{{mesExamples}}", "{{charPrompt}}",
            "{{charInstruction}}", "{{charDepthPrompt}}", "{{creatorNotes}}", "{{charVersion}}",
            "{{model}}", "{{input}}", "{{original}}", "{{lastMessage}}", "{{lastUserMessage}}",
            "{{lastCharMessage}}", "{{lastMessageId}}", "{{allChatRange}}", "{{maxPromptTokens}}",
            "{{maxContextTokens}}", "{{maxResponseTokens}}", "{{time}}", "{{isodate}}",
            "{{weekday}}", "{{datetimeformat::YYYY-MM-DD HH:mm:ss}}", "{{isMobile}}",
            "{{hasExtension::example}}", "<USER>", "<CHAR>",
        ]
    )

    result = render_macros(content, context)

    assert result.unresolved == set()
    assert "旅行者|小满|来自远方的记录者|测试角色|冷静|旅馆|你好" in result.content
    assert "example-model|当前输入|原始文本|最后消息|用户最后消息|角色最后消息" in result.content
    assert "0-3|7000|8192|1192|21:45|2026-07-24|星期五|2026-07-24 21:45:30" in result.content
    assert result.content.endswith("false|false|旅行者|小满")


def test_utility_random_condition_and_variable_macros_execute_without_residue() -> None:
    context = MacroContext(user_name="旅行者", random_seed="stable")
    content = (
        "{{setvar::score::2}}{{addvar::score::3}}{{incvar::score}}"
        "{{getvar::score}}/{{.score}}|{{hasvar::score}}|{{if user}}有用户{{else}}无用户{{/if}}|"
        "{{space::2}}X{{newline::2}}Y|{{reverse::abc}}|{{random::甲::乙}}|"
        "{{pick::甲::乙}}|{{roll::1d6}}|{{// 注释}}{{noop}}|{{trim}}  已裁剪  {{/trim}}|"
        "{{deletevar::score}}{{hasvar::score}}|{{systemPrompt}}|{{outlet::missing}}"
    )

    result = render_macros(content, context)

    assert result.unresolved == set()
    assert result.content.startswith("66/6|true|有用户|  X\n\nY|cba|")
    assert "{{" not in result.content
    assert "|已裁剪|false||" in result.content


def test_unknown_extension_macro_remains_visible_and_reported() -> None:
    result = render_macros("前{{extensionMacro::value}}后", MacroContext())

    assert result.content == "前{{extensionMacro::value}}后"
    assert result.unresolved == {"extensionMacro"}


def test_every_catalog_macro_and_official_alias_is_recognized() -> None:
    for macro in MACRO_CATALOG:
        result = render_macros(macro["syntax"], MacroContext())
        assert result.unresolved == set(), macro

    aliases = "|".join(
        [
            "{{charIfNotGroup}}", "{{description}}", "{{personality}}", "{{scenario}}",
            "{{creatorNotes}}", "{{greeting}}", "{{version}}", "{{char_version}}",
            "{{maxPromptTokens}}", "{{maxContextTokens}}", "{{maxResponseTokens}}",
            "{{idle_duration}}", "{{varexists::x}}", "{{flushvar::x}}",
            "{{globalvarexists::x}}", "{{flushglobalvar::x}}", "{{instructInput}}",
            "{{instructOutput}}", "{{instructSeparator}}", "{{instructFirstOutputPrefix}}",
            "{{instructLastOutputPrefix}}", "{{instructFirstInput}}", "{{instructLastInput}}",
            "{{instructSystem}}", "{{instructSystemPrompt}}", "{{chatSeparator}}",
            "{{time_UTC+2}}", "{{comment::hidden}}",
        ]
    )
    result = render_macros(aliases, MacroContext())

    assert result.unresolved == set()
    assert "{{" not in result.content
