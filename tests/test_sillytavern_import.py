import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catgirl.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def st_preset() -> dict:
    return {
        "chat_completion_source": "custom",
        "custom_prompt_post_processing": "strict",
        "custom_url": "https://gateway.example/v1",
        "custom_model": "vision-model",
        "temperature": 1.1,
        "frequency_penalty": 0.2,
        "presence_penalty": -0.1,
        "top_p": 0.9,
        "openai_max_context": 190000,
        "openai_max_tokens": 20000,
        "stream_openai": True,
        "n": 2,
        "prompts": [
            {
                "identifier": "main",
                "name": "Main Prompt",
                "role": "system",
                "content": "扮演 {{character.name}}。",
                "system_prompt": True,
            },
            {
                "identifier": "worldInfoBefore",
                "name": "World Info Before",
                "marker": True,
                "system_prompt": True,
            },
            {
                "identifier": "depthPrompt",
                "name": "Depth Prompt",
                "role": "assistant",
                "content": "深度内容",
                "injection_position": 1,
                "injection_depth": 7,
                "injection_order": 42,
                "system_prompt": False,
            },
        ],
        "prompt_order": [
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "worldInfoBefore", "enabled": True},
                    {"identifier": "depthPrompt", "enabled": True},
                ],
            }
        ],
    }


def st_world_book() -> dict:
    return {
        "name": "测试世界书",
        "entries": {
            "0": {
                "uid": 0,
                "key": ["王城"],
                "keysecondary": [],
                "comment": "王城设定",
                "content": "王城位于北方。",
                "constant": True,
                "selective": False,
                "order": 120,
                "position": 0,
                "disable": False,
                "depth": 4,
                "role": 0,
                "probability": 100,
                "useProbability": True,
            },
            "1": {
                "uid": 1,
                "key": ["秘密"],
                "keysecondary": ["钥匙"],
                "comment": "深度秘密",
                "content": "只有钥匙能打开密室。",
                "constant": False,
                "selective": True,
                "selectiveLogic": 0,
                "order": 80,
                "position": 4,
                "disable": False,
                "depth": 6,
                "role": 2,
            },
        },
    }


def test_sillytavern_preset_and_world_book_import_as_one_bundle(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={
                "preset": {"name": "兼容预设", "data": st_preset()},
                "world_books": [{"name": "book.json", "data": st_world_book()}],
                "activate": True,
            },
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["imported_prompt_blocks"] == 3
        assert report["imported_world_entries"] == 2
        assert len(report["world_book_ids"]) == 1

        preset = next(item for item in client.get("/api/presets").json() if item["id"] == report["preset_id"])
        assert preset["is_active"] is True
        assert preset["world_book_ids"] == report["world_book_ids"]
        assert preset["context_length"] == 190000
        assert preset["max_response_tokens"] == 20000
        provider = next(
            item for item in client.get("/api/providers").json()
            if item["id"] == report["provider_id"]
        )
        assert provider["chat_completion_source"] == "custom"
        assert provider["prompt_post_processing"] == "strict"

        template = next(
            item for item in client.get("/api/prompt-templates").json()
            if item["id"] == report["prompt_template_id"]
        )
        marker = next(block for block in template["blocks"] if block["identifier"] == "worldInfoBefore")
        depth_prompt = next(block for block in template["blocks"] if block["identifier"] == "depthPrompt")
        assert marker["marker"] is True
        assert depth_prompt["injection_position"] == 1
        assert depth_prompt["injection_depth"] == 7
        assert depth_prompt["injection_order"] == 42

        world_book = next(
            item for item in client.get("/api/world-books").json()
            if item["id"] == report["world_book_ids"][0]
        )
        assert len(world_book["entries"]) == 2
        depth_entry = next(entry for entry in world_book["entries"] if entry["uid"] == 1)
        assert depth_entry["position"] == 4
        assert depth_entry["insertion_depth"] == 6
        assert depth_entry["role"] == "assistant"

        preview = client.get(f"/api/prompt-templates/{template['id']}/preview").json()
        world_marker = next(item for item in preview["messages"] if item["identifier"] == "worldInfoBefore")
        assert "王城位于北方" in world_marker["content"]


def test_preset_import_without_world_book_reports_missing_content(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        report = client.post(
            "/api/import/sillytavern",
            json={"preset": {"name": "只有预设", "data": st_preset()}},
        ).json()

    assert report["preset_id"]
    assert any("没有正文" in warning for warning in report["warnings"])


def test_import_clamps_third_party_values_instead_of_failing(tmp_path: Path) -> None:
    preset_data = st_preset()
    preset_data.update(
        {
            "temperature": 99,
            "frequency_penalty": -99,
            "top_p": "invalid",
            "openai_max_context": 99,
            "openai_max_tokens": 9999999,
            "n": 100,
            "inline_image_quality": "original",
            "reasoning_effort": "extreme",
        }
    )
    preset_data["prompts"][2].update(
        {"injection_position": 8, "injection_depth": 5000, "injection_order": -999999}
    )
    book_data = st_world_book()
    book_data["entries"]["1"].update(
        {"selectiveLogic": 99, "order": -999999, "depth": 5000, "probability": 999}
    )

    with make_client(tmp_path) as client:
        report = client.post(
            "/api/import/sillytavern",
            json={
                "preset": {"name": "越界预设", "data": preset_data},
                "world_books": [{"name": "越界世界书", "data": book_data}],
            },
        )
        assert report.status_code == 200, report.text
        imported = report.json()
        preset = next(item for item in client.get("/api/presets").json() if item["id"] == imported["preset_id"])
        template = next(
            item for item in client.get("/api/prompt-templates").json()
            if item["id"] == imported["prompt_template_id"]
        )
        world_book = next(
            item for item in client.get("/api/world-books").json()
            if item["id"] == imported["world_book_ids"][0]
        )

    assert preset["temperature"] == 2
    assert preset["frequency_penalty"] == -2
    assert preset["top_p"] == 1
    assert preset["context_length"] == 512
    assert preset["max_response_tokens"] == 512
    assert preset["candidate_count"] == 16
    assert preset["image_quality"] == "auto"
    assert preset["reasoning_effort"] == "auto"
    depth_prompt = next(block for block in template["blocks"] if block["identifier"] == "depthPrompt")
    assert (depth_prompt["injection_position"], depth_prompt["injection_depth"], depth_prompt["injection_order"]) == (1, 1000, -100000)
    depth_entry = next(entry for entry in world_book["entries"] if entry["uid"] == 1)
    assert (depth_entry["selective_logic"], depth_entry["insertion_order"], depth_entry["insertion_depth"], depth_entry["probability"]) == (3, -100000, 1000, 100)


def test_world_book_import_rejects_non_object_nested_data_cleanly(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={"world_books": [{"name": "损坏世界书", "data": {"data": "invalid"}}]},
        )

    assert response.status_code == 400
    assert "世界书" in response.json()["detail"]


def test_import_migrates_legacy_provider_generation_columns(tmp_path: Path) -> None:
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
                temperature FLOAT NOT NULL,
                max_tokens INTEGER NOT NULL,
                enabled BOOLEAN NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

    with TestClient(create_app(data_dir, allow_unconfigured_management=True)) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={"preset": {"name": "旧库导入", "data": st_preset()}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["provider_id"]

    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(providers)")}
    assert "temperature" not in columns
    assert "max_tokens" not in columns


def test_chat_completion_order_prefers_100001_and_stashes_omitted_prompts(
    tmp_path: Path,
) -> None:
    preset_data = st_preset()
    preset_data["prompts"].append(
        {
            "identifier": "foldedPrompt",
            "name": "Folded Prompt",
            "role": "system",
            "content": "这段内容应当保持收纳。",
        }
    )
    preset_data["prompt_order"] = [
        {
            "character_id": 100000,
            "order": [
                {"identifier": "foldedPrompt", "enabled": True},
                {"identifier": "main", "enabled": True},
            ],
        },
        {
            "character_id": 100001,
            "order": [
                {"identifier": "main", "enabled": True},
                {"identifier": "worldInfoBefore", "enabled": True},
                {"identifier": "depthPrompt", "enabled": False},
            ],
        },
    ]

    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={"preset": {"name": "折叠兼容预设", "data": preset_data}},
        )
        assert response.status_code == 200, response.text
        report = response.json()
        template = next(
            item
            for item in client.get("/api/prompt-templates").json()
            if item["id"] == report["prompt_template_id"]
        )
        preview = client.get(
            f"/api/prompt-templates/{template['id']}/preview"
        ).json()

    assert [block["identifier"] for block in template["blocks"][:3]] == [
        "main",
        "worldInfoBefore",
        "depthPrompt",
    ]
    folded = next(
        block for block in template["blocks"] if block["identifier"] == "foldedPrompt"
    )
    assert folded["enabled"] is True
    assert folded["stashed"] is True
    assert "foldedPrompt" not in {
        message["identifier"] for message in preview["messages"]
    }


def test_imports_v1_character_card_fields(tmp_path: Path) -> None:
    card = {
        "name": "玲奈",
        "description": "银发的王族继承人。",
        "personality": "谨慎而温柔",
        "scenario": "旧王城钟楼",
        "first_mes": "你终于来了。",
        "mes_example": "<START>\n{{char}}：别出声。",
    }

    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={"characters": [{"name": "lingnai.json", "data": card}]},
        )
        assert response.status_code == 200, response.text
        report = response.json()
        character = next(
            item
            for item in client.get("/api/characters").json()
            if item["id"] == report["character_ids"][0]
        )

    assert report["imported_characters"] == 1
    assert character["name"] == "玲奈"
    assert character["summary"] == "银发的王族继承人。"
    assert "<personality>\n谨慎而温柔\n</personality>" in character["persona"]
    assert "<example_dialogues>" in character["persona"]
    assert character["scenario"] == "旧王城钟楼"
    assert character["first_message"] == "你终于来了。"


@pytest.mark.parametrize("card_spec", ["chara_card_v2", "chara_card_v3"])
def test_imports_v2_v3_character_embedded_book_and_binds_same_batch_preset(
    tmp_path: Path,
    card_spec: str,
) -> None:
    card = {
        "spec": card_spec,
        "spec_version": "2.0",
        "__catgirl_source_format": "png",
        "data": {
            "name": "雪音",
            "description": "王城调查官。",
            "personality": "冷静",
            "scenario": "北塔",
            "first_mes": "请出示通行证。",
            "system_prompt": "保持调查官的口吻。",
            "post_history_instructions": "不要泄露结论。",
            "alternate_greetings": ["站住。"],
            "tags": ["调查官"],
            "character_book": {
                "name": "雪音线索",
                "entries": [
                    {
                        "id": 7,
                        "keys": ["徽记"],
                        "content": "徽记来自旧王族。",
                        "enabled": True,
                    }
                ],
            },
        },
    }

    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={
                "preset": {"name": "角色绑定预设", "data": st_preset()},
                "characters": [{"name": "xueyin.png", "data": card}],
                "activate": True,
            },
        )
        assert response.status_code == 200, response.text
        report = response.json()
        preset = next(
            item
            for item in client.get("/api/presets").json()
            if item["id"] == report["preset_id"]
        )
        character = next(
            item
            for item in client.get("/api/characters").json()
            if item["id"] == report["character_ids"][0]
        )
        world_book = next(
            item
            for item in client.get("/api/world-books").json()
            if item["id"] == report["world_book_ids"][0]
        )

    assert preset["character_id"] == character["id"]
    assert preset["world_book_ids"] == [world_book["id"]]
    assert character["is_active"] is True
    assert "<character_system_prompt>" in character["persona"]
    assert "<post_history_instructions>" in character["persona"]
    assert world_book["name"] == "雪音线索"
    assert world_book["entries"][0]["primary_keys"] == ["徽记"]
    assert report["imported_world_entries"] == 1
    assert any("PNG 立绘未保存" in warning for warning in report["warnings"])
    assert any("备用开场" in warning for warning in report["warnings"])


def test_invalid_character_card_returns_clean_error(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/import/sillytavern",
            json={"characters": [{"name": "broken.json", "data": {"name": "空卡"}}]},
        )

    assert response.status_code == 400
    assert "角色卡 broken.json 导入失败" in response.json()["detail"]
