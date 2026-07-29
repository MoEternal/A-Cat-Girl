from dataclasses import dataclass

from catgirl.prompt_compiler import compile_prompt_messages


@dataclass
class Block:
    id: str
    role: str
    content: str
    enabled: bool
    position: int
    identifier: str | None = None
    marker: bool = False
    injection_position: int = 0
    injection_depth: int = 4
    injection_order: int = 100
    stashed: bool = False


@dataclass
class Entry:
    id: str
    primary_keys: list[str]
    secondary_keys: list[str]
    content: str
    constant: bool
    selective: bool
    selective_logic: int
    enabled: bool
    insertion_order: int
    position: int
    insertion_depth: int
    role: str
    probability: int = 100
    use_probability: bool = True


@dataclass
class Persona:
    id: str
    description: str
    injection_position: int
    injection_depth: int
    role: str


def test_prompt_and_world_book_depth_are_applied_to_chat_messages() -> None:
    blocks = [
        Block("main", "system", "主提示", True, 0),
        Block("history", "system", "", True, 1, "chatHistory", True),
        Block("depth", "assistant", "深度提示", True, 2, injection_position=1, injection_depth=1, injection_order=100),
    ]
    entries = [
        Entry("before", ["王城"], [], "王城设定", False, False, 0, True, 100, 0, 4, "system"),
        Entry("depth-book", ["王城"], [], "深度世界书", False, False, 0, True, 90, 4, 2, "user"),
    ]
    history = [
        {"role": "user", "content": "来到王城"},
        {"role": "assistant", "content": "欢迎"},
        {"role": "user", "content": "继续"},
    ]

    compiled = compile_prompt_messages(
        blocks,
        history,
        {"worldInfoBefore": ""},
        entries,
        "来到王城",
        random_value=lambda: 0,
    )

    assert [item.content for item in compiled] == [
        "主提示",
        "来到王城",
        "深度世界书",
        "欢迎",
        "深度提示",
        "继续",
    ]


def test_selective_secondary_keys_follow_sillytavern_logic() -> None:
    blocks = [
        Block("world", "system", "", True, 0, "worldInfoBefore", True),
        Block("history", "system", "", True, 1, "chatHistory", True),
    ]
    entries = [
        Entry("and-all", ["秘密"], ["钥匙", "密室"], "命中", False, True, 3, True, 100, 0, 4, "system"),
    ]

    missed = compile_prompt_messages(blocks, [], {}, entries, "秘密和钥匙", random_value=lambda: 0)
    matched = compile_prompt_messages(blocks, [], {}, entries, "秘密、钥匙、密室", random_value=lambda: 0)

    assert all(item.content != "命中" for item in missed)
    assert any(item.content == "命中" for item in matched)


def test_user_persona_marker_and_depth_injection() -> None:
    blocks = [
        Block("persona", "system", "", True, 0, "personaDescription", True),
        Block("history", "system", "", True, 1, "chatHistory", True),
    ]
    marker_persona = Persona("marker", "用户设定", 0, 2, "system")
    depth_persona = Persona("depth", "深度用户设定", 4, 1, "user")
    history = [{"role": "user", "content": "问题"}, {"role": "assistant", "content": "回答"}]

    marker_messages = compile_prompt_messages(blocks, history, {}, user_persona=marker_persona)
    depth_messages = compile_prompt_messages(blocks, history, {}, user_persona=depth_persona)

    assert [item.content for item in marker_messages] == ["用户设定", "问题", "回答"]
    assert [item.content for item in depth_messages] == ["问题", "深度用户设定", "回答"]


def test_stashed_blocks_are_never_compiled() -> None:
    blocks = [
        Block("main", "system", "正式内容", True, 0),
        Block("stash", "system", "收纳内容", True, 1, stashed=True),
        Block("stash-depth", "user", "收纳的深度内容", True, 2, injection_position=1, injection_depth=1),
        Block("history", "system", "", True, 3, "chatHistory", True),
    ]
    history = [{"role": "user", "content": "问题"}]

    compiled = compile_prompt_messages(blocks, history, {})

    assert [item.content for item in compiled] == ["正式内容", "收纳的深度内容", "问题"]

    blocks[2].stashed = True
    compiled = compile_prompt_messages(blocks, history, {})

    assert [item.content for item in compiled] == ["正式内容", "问题"]
