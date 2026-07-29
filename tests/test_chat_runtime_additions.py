from catgirl.chat_runtime import ChatRuntime
from catgirl.prompt_compiler import CompiledMessage


def _contents(messages: list[CompiledMessage]) -> list[str]:
    return [message.content for message in messages]


def test_additions_without_native_depth_keep_existing_position() -> None:
    compiled = [
        CompiledMessage("system", "主系统提示", "main"),
        CompiledMessage("user", "旧问题", "chatHistory"),
        CompiledMessage("assistant", "旧回答", "chatHistory"),
        CompiledMessage("user", "最新问题", "chatHistory"),
    ]

    result = ChatRuntime._insert_additions(
        compiled,
        [{"role": "system", "content": "普通插件提示"}],
    )

    assert _contents(result) == [
        "主系统提示",
        "旧问题",
        "旧回答",
        "普通插件提示",
        "最新问题",
    ]
