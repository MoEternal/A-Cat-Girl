import logging
import sqlite3
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from catgirl.database import ChatMessage, Conversation
from catgirl.main import create_app
from catgirl.token_counter import count_text_tokens


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def test_health_and_seeded_overview(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "catgirl"}
        overview = client.get("/api/overview").json()
        default_character = client.get("/api/characters").json()[0]

    assert overview["counts"] == {
        "presets": 1,
        "world_books": 0,
        "providers": 1,
        "templates": 1,
        "characters": 1,
        "user_personas": 1,
    }
    assert overview["active_preset"]["name"] == "默认预设"
    assert overview["active_provider"]["name"] == "默认接口"
    assert overview["active_template"]["name"] == "默认模板"
    assert overview["active_character"]["name"] == "默认助手"
    assert overview["active_user_persona"]["name"] == "用户"
    assert default_character["persona"] == "你现在是一只猫娘，负责陪伴用户聊天。"
    assert default_character["scenario"] == ""


def test_runtime_logs_can_be_read_and_cleared(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        logging.getLogger("catgirl.test").warning("relay-native-log-body")
        logs = client.get("/api/logs").json()
        assert any(
            item["source"] == "catgirl.test"
            and item["level"] == "WARNING"
            and item["message"] == "relay-native-log-body"
            for item in logs
        )
        assert (tmp_path / "logs" / "catgirl.log").read_text("utf-8").find(
            "relay-native-log-body"
        ) >= 0

        response = client.delete("/api/logs")
        assert response.status_code == 204
        assert client.get("/api/logs").json() == []


def test_provider_secret_is_masked_and_switchable(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/providers",
            json={
                "name": "备用供应商",
                "base_url": "https://example.test/v1/",
                "model": "example-model",
                "api_key": "sk-super-secret-value",
            },
        )
        assert created.status_code == 201
        provider = created.json()
        assert provider["api_key_configured"] is True
        assert provider["api_key_masked"] != "sk-super-secret-value"
        assert "super-secret" not in created.text
        assert provider["base_url"] == "https://example.test/v1"

        updated = client.put(
            f"/api/providers/{provider['id']}",
            json={"model": "new-model"},
        ).json()
        assert updated["api_key_configured"] is True
        assert updated["model"] == "new-model"

        activated = client.post(f"/api/providers/{provider['id']}/activate").json()
        assert activated["is_active"] is True
        providers = client.get("/api/providers").json()
        assert sum(item["is_active"] for item in providers) == 1


def test_provider_supports_all_native_api_protocols(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        for kind in ("openai_compatible", "anthropic", "google_gemini"):
            response = client.post(
                "/api/providers",
                json={"name": kind, "kind": kind, "base_url": "https://api.test", "model": "m"},
            )
            assert response.status_code == 201
            assert response.json()["kind"] == kind

        rejected = client.post(
            "/api/providers",
            json={"name": "invalid", "kind": "unsupported"},
        )
        assert rejected.status_code == 422


def test_provider_priorities_are_editable_and_sorted(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        original = client.get("/api/providers").json()[0]
        later = client.post(
            "/api/providers",
            json={
                "name": "第三备用",
                "base_url": "https://third.test/v1",
                "model": "third-model",
                "priority": 30,
            },
        ).json()
        earlier = client.post(
            "/api/providers",
            json={
                "name": "第二备用",
                "base_url": "https://second.test/v1",
                "model": "second-model",
                "priority": 20,
            },
        ).json()

        providers = client.get("/api/providers").json()
        assert [item["id"] for item in providers] == [original["id"], earlier["id"], later["id"]]
        assert [item["priority"] for item in providers] == [1, 20, 30]

        updated = client.put(
            f"/api/providers/{later['id']}",
            json={"priority": 10},
        ).json()
        assert updated["priority"] == 10
        assert [item["id"] for item in client.get("/api/providers").json()] == [
            original["id"],
            later["id"],
            earlier["id"],
        ]

        rejected = client.put(
            f"/api/providers/{later['id']}",
            json={"priority": 0},
        )
        assert rejected.status_code == 422


def test_provider_explicit_export_contains_complete_connection(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        provider = client.get("/api/providers").json()[0]
        client.put(
            f"/api/providers/{provider['id']}",
            json={
                "name": "可导出接口",
                "base_url": "https://export.test/v1",
                "model": "export-model",
                "api_key": "export-secret",
                "priority": 7,
            },
        )
        exported = client.post(f"/api/providers/{provider['id']}/export")

    assert exported.status_code == 200
    assert exported.json()["api_key"] == "export-secret"
    assert exported.json()["priority"] == 7


def test_provider_saves_chat_source_and_prompt_processing(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/providers",
            json={
                "name": "Claude 接口",
                "chat_completion_source": "claude",
                "prompt_post_processing": "strict",
                "model": "claude-test",
            },
        )
        assert created.status_code == 201, created.text
        provider = created.json()
        assert provider["chat_completion_source"] == "claude"
        assert provider["prompt_post_processing"] == "strict"
        assert provider["kind"] == "anthropic"
        assert provider["base_url"] == "https://api.anthropic.com/v1"

        updated = client.put(
            f"/api/providers/{provider['id']}",
            json={
                "chat_completion_source": "makersuite",
                "prompt_post_processing": "single",
            },
        ).json()
        assert updated["kind"] == "google_gemini"
        assert updated["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
        assert updated["prompt_post_processing"] == "single"

        rejected = client.put(
            f"/api/providers/{provider['id']}",
            json={"prompt_post_processing": "unknown"},
        )
        assert rejected.status_code == 422


def test_existing_provider_table_gets_source_columns(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "catgirl.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE providers (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                kind VARCHAR(40) NOT NULL,
                base_url VARCHAR(500) NOT NULL,
                model VARCHAR(160) NOT NULL,
                api_key_encrypted TEXT NOT NULL,
                enabled BOOLEAN NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    with make_client(tmp_path) as client:
        provider = client.get("/api/providers").json()[0]
        assert provider["chat_completion_source"] == "custom"
        assert provider["prompt_post_processing"] == ""

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(providers)")}
    assert {"chat_completion_source", "prompt_post_processing", "priority"} <= columns
    with sqlite3.connect(database_path) as connection:
        priority = connection.execute("SELECT priority FROM providers").fetchone()[0]
    assert priority == 1


def test_untouched_legacy_default_provider_becomes_neutral(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        provider = client.get("/api/providers").json()[0]
        client.put(
            f"/api/providers/{provider['id']}",
            json={
                "name": "OpenAI 兼容接口",
                "kind": "openai_compatible",
                "base_url": "https://api.openai.com/v1",
                "model": "",
            },
        )

    with make_client(tmp_path) as client:
        migrated = client.get("/api/providers").json()[0]
        assert migrated["name"] == "默认接口"
        assert migrated["base_url"] == ""


def test_provider_models_use_current_form_connection_and_native_format(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-goog-api-key")
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-flash",
                        "displayName": "Gemini Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding",
                        "displayName": "Embedding",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "catgirl.api.httpx.Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with make_client(tmp_path) as client:
        provider = client.get("/api/providers").json()[0]
        response = client.post(
            f"/api/providers/{provider['id']}/models",
            json={
                "kind": "google_gemini",
                "base_url": "https://generativelanguage.test",
                "api_key": "temporary-google-key",
            },
        )
        assert response.status_code == 200
        assert response.json() == [{"id": "gemini-flash", "name": "Gemini Flash"}]
        assert captured["url"] == (
            "https://generativelanguage.test/v1beta/models?pageSize=1000"
        )
        assert captured["api_key"] == "temporary-google-key"


def test_prompt_order_and_character_preview(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        character = client.post(
            "/api/characters",
            json={
                "name": "小满",
                "summary": "测试角色",
                "persona": "冷静且简洁",
                "scenario": "在 QQ 中聊天",
                "first_message": "你好",
            },
        ).json()
        client.post(f"/api/characters/{character['id']}/activate")
        user_persona = client.post(
            "/api/user-personas",
            json={
                "name": "旅行者",
                "description": "来自远方的记录者",
                "injection_position": 0,
            },
        ).json()
        client.post(f"/api/user-personas/{user_persona['id']}/activate")

        template = client.post(
            "/api/prompt-templates",
            json={"name": "测试模板", "description": "排序测试"},
        ).json()
        first = template["blocks"][0]
        client.put(
            f"/api/prompt-blocks/{first['id']}",
            json={
                "title": "人设块",
                "content": "角色={{character.name}}；别名={{char}}；用户={{user}}；用户设定={{persona}}；消息={{lastUserMessage}}；设定={{character.persona}}",
            },
        )
        second = client.post(
            f"/api/prompt-templates/{template['id']}/blocks",
            json={"title": "前置块", "role": "assistant", "content": "示例回复"},
        ).json()

        reordered = client.put(
            f"/api/prompt-templates/{template['id']}/blocks/order",
            json={"block_ids": [second["id"], first["id"]]},
        )
        assert reordered.status_code == 200
        assert [item["title"] for item in reordered.json()] == ["前置块", "人设块"]

        client.post(f"/api/prompt-templates/{template['id']}/activate")
        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        template_messages = [
            item for item in preview["messages"] if item["kind"] == "template"
        ]
        assert [item["role"] for item in template_messages] == ["assistant", "system"]
        assert template_messages[1]["content"] == (
            "角色=小满；别名=小满；用户=旅行者；用户设定=来自远方的记录者；"
            "消息=[最后一条用户消息将在运行时插入]；设定=冷静且简洁"
        )
        assert all(item["token_count"] > 0 for item in template_messages)
        assert preview["total_tokens"] == sum(
            item["token_count"] for item in preview["messages"]
        )
        assert preview["unresolved_variables"] == []


def test_prompt_preview_summarizes_history_and_enabled_plugin_prompts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        template = next(
            item for item in client.get("/api/prompt-templates").json() if item["is_active"]
        )
        model = next(
            item for item in client.get("/api/providers").json() if item["is_active"]
        )["model"]
        client.put("/api/plugins/time_awareness", json={"enabled": False})
        client.put(
            "/api/plugins/segmented_reply",
            json={"settings": {"prompt": "只用于预览的分段提示词"}},
        )
        with client.app.state.database.session_factory() as session:
            session.add(
                Conversation(
                    id="preview-record",
                    channel="private",
                    external_id="qq:preview:private:user",
                    title="预览记录",
                    is_active=True,
                )
            )
            session.add_all(
                [
                    ChatMessage(
                        conversation_id="preview-record",
                        position=0,
                        role="user",
                        content="不会返回到预览的用户消息",
                        status="complete",
                        source="user",
                    ),
                    ChatMessage(
                        conversation_id="preview-record",
                        position=1,
                        role="assistant",
                        content="不会返回到预览的角色回复",
                        status="complete",
                        source="runtime",
                    ),
                ]
            )
            session.commit()

        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        history = next(item for item in preview["messages"] if item["kind"] == "history")
        expected_history_tokens = sum(
            count_text_tokens(content, model)
            for content in ("不会返回到预览的用户消息", "不会返回到预览的角色回复")
        )
        assert history["title"] == "聊天历史 · 预览记录"
        assert history["content"] == ""
        assert history["content_visible"] is False
        assert history["marker"] is False
        assert history["token_count"] == expected_history_tokens
        assert "不会返回到预览" not in str(preview)

        plugin = next(
            item
            for item in preview["messages"]
            if item["kind"] == "plugin" and item["plugin_id"] == "segmented_reply"
        )
        assert plugin["title"] == "插件 · 分段回复"
        assert plugin["content"] == "只用于预览的分段提示词"
        assert plugin["insertion_label"] == "最新用户消息前"
        assert preview["messages"].index(plugin) < preview["messages"].index(history)
        assert not any(
            item.get("plugin_id") == "time_awareness" for item in preview["messages"]
        )

        client.put("/api/plugins/segmented_reply", json={"enabled": False})
        without_plugin = client.get(
            f"/api/prompt-templates/{template['id']}/preview"
        ).json()
        assert not any(
            item.get("plugin_id") == "segmented_reply"
            for item in without_plugin["messages"]
        )


def test_reorder_rejects_missing_blocks(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        template = client.get("/api/prompt-templates").json()[0]
        response = client.put(
            f"/api/prompt-templates/{template['id']}/blocks/order",
            json={"block_ids": [template["blocks"][0]["id"]]},
        )

    assert response.status_code == 400
    assert "全部提示词块" in response.json()["detail"]


def test_stashed_blocks_leave_the_prompt_and_can_be_inserted_back(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        template = client.post(
            "/api/prompt-templates",
            json={"name": "折叠栏模板", "description": "收纳测试"},
        ).json()
        client.post(f"/api/prompt-templates/{template['id']}/activate")
        kept = template["blocks"][0]
        client.put(f"/api/prompt-blocks/{kept['id']}", json={"title": "正式块", "content": "正式内容"})
        stashed = client.post(
            f"/api/prompt-templates/{template['id']}/blocks",
            json={"title": "备用块", "content": "备用内容", "stashed": True},
        ).json()
        assert stashed["stashed"] is True
        assert stashed["enabled"] is True

        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        assert [
            item["content"] for item in preview["messages"] if item["kind"] == "template"
        ] == ["正式内容"]

        inserted = client.put(f"/api/prompt-blocks/{stashed['id']}", json={"stashed": False}).json()
        assert inserted["stashed"] is False
        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        assert [
            item["content"] for item in preview["messages"] if item["kind"] == "template"
        ] == ["正式内容", "备用内容"]

        client.put(f"/api/prompt-blocks/{kept['id']}", json={"stashed": True})
        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        assert [
            item["content"] for item in preview["messages"] if item["kind"] == "template"
        ] == ["备用内容"]

        order = client.put(
            f"/api/prompt-templates/{template['id']}/blocks/order",
            json={"block_ids": [stashed["id"], kept["id"]]},
        )
        assert order.status_code == 200
        assert [item["stashed"] for item in order.json()] == [False, True]


def test_preset_switches_all_resources_and_resource_switch_updates_active_preset(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        provider = client.post(
            "/api/providers",
            json={"name": "预设供应商", "base_url": "https://example.test/v1", "model": "preset-model"},
        ).json()
        template = client.post(
            "/api/prompt-templates",
            json={"name": "预设模板", "description": "组合测试"},
        ).json()
        character = client.post(
            "/api/characters",
            json={"name": "预设人设", "persona": "组合测试人设"},
        ).json()
        user_persona = client.post(
            "/api/user-personas",
            json={"name": "预设用户", "description": "组合测试用户", "injection_position": 4, "injection_depth": 3, "role": "user"},
        ).json()
        preset = client.post(
            "/api/presets",
            json={
                "name": "完整预设",
                "provider_id": provider["id"],
                "prompt_template_id": template["id"],
                "character_id": character["id"],
                "user_persona_id": user_persona["id"],
                "context_length": 190000,
                "max_response_tokens": 20000,
                "candidate_count": 2,
                "temperature": 1.0,
                "frequency_penalty": 0.1,
                "presence_penalty": -0.2,
                "top_p": 0.95,
                "streaming": True,
                "function_calling": True,
                "media_inlining": True,
                "show_thoughts": True,
                "reasoning_effort": "high",
            },
        ).json()

        activated = client.post(f"/api/presets/{preset['id']}/activate").json()
        assert activated["context_length"] == 190000
        assert activated["reasoning_effort"] == "high"
        overview = client.get("/api/overview").json()
        assert overview["active_provider"]["id"] == provider["id"]
        assert overview["active_template"]["id"] == template["id"]
        assert overview["active_character"]["id"] == character["id"]
        assert overview["active_user_persona"]["id"] == user_persona["id"]

        original_provider = client.get("/api/providers").json()[0]
        client.post(f"/api/providers/{original_provider['id']}/activate")
        active_preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
        assert active_preset["provider_id"] == original_provider["id"]

        original_persona = client.get("/api/user-personas").json()[0]
        client.post(f"/api/user-personas/{original_persona['id']}/activate")
        active_preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
        assert active_preset["user_persona_id"] == original_persona["id"]


def test_world_books_default_to_character_scope_and_can_be_relinked(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        characters = client.get("/api/characters").json()
        first = characters[0]
        second = client.post(
            "/api/characters",
            json={"name": "第二角色", "summary": "测试"},
        ).json()
        character_book = client.post(
            "/api/world-books",
            json={"name": "角色世界书", "description": "角色范围"},
        ).json()
        global_book = client.post(
            "/api/world-books",
            json={"name": "全局世界书", "scope": "global"},
        ).json()

        assert character_book["scope"] == "character"
        assert character_book["character_id"] == first["id"]
        assert global_book["scope"] == "global"
        assert global_book["character_id"] is None

        updated = client.put(
            f"/api/characters/{second['id']}",
            json={"world_book_ids": [character_book["id"]]},
        ).json()
        assert updated["world_book_ids"] == [character_book["id"]]
        refreshed_first = next(
            item for item in client.get("/api/characters").json() if item["id"] == first["id"]
        )
        assert refreshed_first["world_book_ids"] == []
        refreshed_book = next(
            item for item in client.get("/api/world-books").json()
            if item["id"] == character_book["id"]
        )
        assert refreshed_book["character_id"] == second["id"]

        overview = client.get("/api/overview").json()
        assert global_book["id"] in overview["active_world_book_ids"]


def test_locked_context_and_response_length_are_validated(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        too_long = client.post(
            "/api/presets",
            json={"name": "超长上下文", "context_length": 200001, "max_context_unlocked": False},
        )
        invalid_response = client.post(
            "/api/presets",
            json={"name": "回复过长", "context_length": 4096, "max_response_tokens": 4097},
        )

    assert too_long.status_code == 400
    assert "200,000" in too_long.json()["detail"]
    assert invalid_response.status_code == 400
    assert "不能大于上下文" in invalid_response.json()["detail"]
