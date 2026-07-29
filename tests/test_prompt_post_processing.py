from copy import deepcopy

from catgirl.prompt_post_processing import PROMPT_PLACEHOLDER, post_process_prompt


def test_merge_combines_roles_and_does_not_mutate_input() -> None:
    messages = [
        {"role": "system", "content": "规则一"},
        {"role": "system", "content": "规则二"},
        {"role": "user", "content": "问题一", "name": "访客"},
        {"role": "user", "content": "问题二"},
        {"role": "assistant", "content": "回答", "tool_calls": [{"id": "call-1"}]},
    ]
    original = deepcopy(messages)

    processed = post_process_prompt(messages, "merge")

    assert messages == original
    assert processed == [
        {"role": "system", "content": "规则一\n\n规则二"},
        {"role": "user", "content": "访客: 问题一\n\n问题二"},
        {"role": "assistant", "content": "回答"},
    ]


def test_semi_forces_alternation_without_placeholder() -> None:
    processed = post_process_prompt(
        [
            {"role": "system", "content": "首条规则"},
            {"role": "assistant", "content": "示例回答"},
            {"role": "system", "content": "中段规则"},
            {"role": "user", "content": "问题"},
        ],
        "semi",
    )

    assert processed == [
        {"role": "system", "content": "首条规则"},
        {"role": "assistant", "content": "示例回答"},
        {"role": "user", "content": "中段规则\n\n问题"},
    ]


def test_strict_adds_user_placeholder_before_assistant() -> None:
    processed = post_process_prompt(
        [
            {"role": "system", "content": "规则"},
            {"role": "assistant", "content": "示例"},
        ],
        "strict",
    )

    assert processed == [
        {"role": "system", "content": "规则"},
        {"role": "user", "content": PROMPT_PLACEHOLDER},
        {"role": "assistant", "content": "示例"},
    ]


def test_single_preserves_speakers_and_media_in_one_user_message() -> None:
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    processed = post_process_prompt(
        [
            {"role": "user", "content": [{"type": "text", "text": "看图"}, image]},
            {"role": "assistant", "content": "看到了"},
        ],
        "single",
        user_name="小明",
        character_name="小夏",
    )

    assert len(processed) == 1
    assert processed[0]["role"] == "user"
    assert processed[0]["content"] == [
        {"type": "text", "text": "小明: 看图"},
        image,
        {"type": "text", "text": "\n\n小夏: 看到了"},
    ]


def test_tool_modes_preserve_or_remove_tool_fields() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "调用",
            "tool_calls": [{"id": "call-1", "function": {"name": "lookup"}}],
        },
        {"role": "tool", "content": "结果", "tool_call_id": "call-1"},
    ]

    with_tools = post_process_prompt(messages, "merge_tools")
    without_tools = post_process_prompt(messages, "merge")

    assert with_tools[0]["tool_calls"][0]["id"] == "call-1"
    assert with_tools[1] == {"role": "tool", "content": "结果", "tool_call_id": "call-1"}
    assert without_tools == [
        {"role": "assistant", "content": "调用"},
        {"role": "user", "content": "结果"},
    ]
