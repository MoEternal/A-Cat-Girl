from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from catgirl.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def jsonl(*items: dict) -> bytes:
    return ("\ufeff" + "\r\n".join(json.dumps(item, ensure_ascii=False) for item in items)).encode(
        "utf-8"
    )


def import_chat(client: TestClient, route_id: str, file_name: str, content: bytes):
    return client.post(
        "/api/runtime/conversations/import/sillytavern",
        params={"route_id": route_id, "file_name": file_name},
        content=content,
        headers={"Content-Type": "application/x-ndjson"},
    )


def test_sillytavern_jsonl_chat_import_creates_an_inactive_record(tmp_path: Path) -> None:
    route_id = "qq:90001:private:334455"
    payload = jsonl(
        {
            "user_name": "墨墨",
            "character_name": "柏柏",
            "create_date": "2026-07-01T12:00:00Z",
            "chat_metadata": {},
        },
        {
            "name": "墨墨",
            "is_user": True,
            "is_system": False,
            "send_date": "2026-07-01T12:01:00Z",
            "mes": "你好",
            "extra": {},
        },
        {
            "name": "柏柏",
            "is_user": False,
            "is_system": False,
            "send_date": "2026-07-01T12:02:00Z",
            "mes": "",
            "swipes": ["旧回复", {"message": "当前回复"}],
            "swipe_id": 1,
            "swipe_info": [
                {"extra": {"reasoning": "旧思考"}},
                {
                    "extra": {
                        "reasoning": "当前思考",
                        "model": "tavern-model",
                        "api": "openai",
                    }
                },
            ],
            "extra": {"reasoning": "备用思考"},
        },
        {
            "name": "System",
            "is_user": False,
            "is_system": True,
            "send_date": "2026-07-01T12:03:00Z",
            "mes": "场景切换",
        },
        {
            "name": "柏柏",
            "is_user": False,
            "is_system": False,
            "mes": "",
            "extra": {"image": "data:image/png;base64,ignored-outside-message"},
        },
    )

    with make_client(tmp_path) as client:
        original = client.post(
            "/api/runtime/conversations",
            json={"route_id": route_id, "title": "当前记录"},
        ).json()
        response = import_chat(client, route_id, "柏柏-酒馆记录.jsonl", payload)
        assert response.status_code == 201, response.text
        report = response.json()
        imported = report["conversation"]
        assert imported["title"] == "柏柏-酒馆记录"
        assert imported["is_active"] is False
        assert imported["message_count"] == 3
        assert imported["character_name"] == "柏柏"
        assert report["imported_messages"] == 3
        assert report["skipped_messages"] == 1
        assert report["user_name"] == "墨墨"
        assert report["character_name"] == "柏柏"

        records = [
            item
            for item in client.get("/api/runtime/conversations").json()
            if item["external_id"] == route_id
        ]
        assert len(records) == 2
        assert next(item for item in records if item["id"] == original["id"])["is_active"] is True
        assert next(item for item in records if item["id"] == imported["id"])["is_active"] is False

        messages = client.get(
            f"/api/runtime/conversations/{imported['id']}/messages"
        ).json()
        assert [item["role"] for item in messages] == ["user", "assistant", "system"]
        assert [item["content"] for item in messages] == ["你好", "当前回复", "场景切换"]
        assert [item["position"] for item in messages] == [0, 1, 2]
        assert messages[0]["speaker_name"] == "墨墨"
        assert messages[1]["speaker_name"] == "柏柏"
        assert messages[1]["source"] == "import:sillytavern"
        assert messages[1]["model"] == "tavern-model"
        assert messages[1]["message_metadata"]["reasoning"] == "当前思考"
        assert messages[1]["message_metadata"]["sillytavern_swipe_id"] == 1
        assert messages[1]["message_metadata"]["sillytavern_swipe_count"] == 2
        assert messages[1]["created_at"].startswith("2026-07-01T12:02:00")


def test_sillytavern_json_array_and_chub_message_objects_are_supported(tmp_path: Path) -> None:
    route_id = "qq:90001:group:7788"
    payload = json.dumps(
        [
            {"user_name": "用户", "character_name": "角色", "chat_metadata": {}},
            {"name": "用户", "is_user": True, "mes": {"message": "数组用户消息"}},
            {"name": "角色", "is_user": False, "mes": {"message": "数组角色消息"}},
        ],
        ensure_ascii=False,
    ).encode("utf-8")

    with make_client(tmp_path) as client:
        client.post(
            "/api/runtime/conversations",
            json={"route_id": route_id, "title": "群聊当前记录"},
        )
        response = import_chat(client, route_id, "数组记录.json", payload)
        assert response.status_code == 201, response.text
        report = response.json()
        messages = client.get(
            f"/api/runtime/conversations/{report['conversation']['id']}/messages"
        ).json()
        assert [item["content"] for item in messages] == ["数组用户消息", "数组角色消息"]


def test_invalid_or_unsafe_sillytavern_chat_is_rejected_atomically(tmp_path: Path) -> None:
    route_id = "qq:90001:private:556677"
    with make_client(tmp_path) as client:
        client.post(
            "/api/runtime/conversations",
            json={"route_id": route_id, "title": "原记录"},
        )
        before = len(client.get("/api/runtime/conversations").json())

        broken = (
            '{"user_name":"用户","character_name":"角色"}\n'
            '{"name":"用户","is_user":true,"mes":"有效前半段"}\n'
            "{broken-json}\n"
        ).encode("utf-8")
        response = import_chat(client, route_id, "损坏记录.jsonl", broken)
        assert response.status_code == 400
        assert "第 3 行" in response.json()["detail"]
        assert len(client.get("/api/runtime/conversations").json()) == before

        unsafe = jsonl(
            {"user_name": "用户", "character_name": "角色"},
            {
                "name": "角色",
                "is_user": False,
                "mes": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB",
            },
        )
        response = import_chat(client, route_id, "图片记录.jsonl", unsafe)
        assert response.status_code == 400
        assert "base64" in response.json()["detail"]
        assert len(client.get("/api/runtime/conversations").json()) == before


def test_sillytavern_chat_import_requires_an_existing_target_route(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = import_chat(
            client,
            "qq:90001:private:not-found",
            "记录.jsonl",
            jsonl({"name": "用户", "is_user": True, "mes": "消息"}),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "目标会话不存在"
        assert client.get("/api/runtime/conversations").json() == []
