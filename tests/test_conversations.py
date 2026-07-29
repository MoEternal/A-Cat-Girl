from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from catgirl.main import create_app
from catgirl.database import ConversationTurn
from catgirl.model_client import OpenAICompatibleClient


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def configure_model(client: TestClient) -> None:
    client.put("/api/plugins/memory_system", json={"enabled": False})
    client.put("/api/plugins/segmented_reply", json={"enabled": False})
    provider = client.get("/api/providers").json()[0]
    client.put(
        f"/api/providers/{provider['id']}",
        json={"base_url": "https://model.test/v1", "model": "history-model", "enabled": True},
    )
    preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
    client.put(f"/api/presets/{preset['id']}", json={"streaming": False})
    client.put("/api/plugins/sticker_reply", json={"settings": {"probability": 0}})


def test_chat_records_are_isolated_and_switch_immediately(tmp_path: Path) -> None:
    model_requests: list[list[dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        model_requests.append(messages)
        latest = next(item["content"] for item in reversed(messages) if item["role"] == "user")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"回复:{latest}"}, "finish_reason": "stop"}]},
        )

    route_id = "qq:90001:private:12345"
    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )

        first = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "记录一消息"},
        ).json()
        assert first["conversation_id"] == route_id
        assert first["route_id"] == route_id

        records = client.get("/api/runtime/conversations").json()
        assert len(records) == 1
        original = records[0]
        assert original["is_active"] is True
        assert original["message_count"] == 2
        assert original["character_name"] == "默认助手"
        assert original["total_tokens"] > 0

        alternate = client.post(
            "/api/runtime/conversations",
            json={"route_id": route_id, "title": "另一条时间线"},
        ).json()
        assert alternate["is_active"] is False
        renamed = client.put(
            f"/api/runtime/conversations/{alternate['id']}",
            json={"title": "第二份记录"},
        ).json()
        assert renamed["title"] == "第二份记录"
        activated = client.post(
            f"/api/runtime/conversations/{alternate['id']}/activate"
        ).json()
        assert activated["is_active"] is True

        second = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "记录二消息"},
        ).json()
        assert second["conversation_id"] == alternate["id"]
        assert all("记录一消息" not in str(item["content"]) for item in model_requests[1])

        client.post(f"/api/runtime/conversations/{original['id']}/activate")
        third = client.post(
            "/api/runtime/messages",
            json={"conversation_id": route_id, "text": "回到记录一"},
        ).json()
        assert third["conversation_id"] == original["id"]
        assert any("记录一消息" in str(item["content"]) for item in model_requests[2])
        assert all("记录二消息" not in str(item["content"]) for item in model_requests[2])

        original_messages = client.get(
            f"/api/runtime/conversations/{original['id']}/messages"
        ).json()
        alternate_messages = client.get(
            f"/api/runtime/conversations/{alternate['id']}/messages"
        ).json()
        assert all(item["token_count"] > 0 for item in original_messages)
        assert [item["speaker_name"] for item in original_messages] == [
            "用户",
            "默认助手",
            "用户",
            "默认助手",
        ]
        assert [item["content"] for item in original_messages if item["role"] == "user"] == [
            "记录一消息",
            "回到记录一",
        ]
        assert [item["content"] for item in alternate_messages if item["role"] == "user"] == [
            "记录二消息"
        ]

        other_character = client.post(
            "/api/characters",
            json={"name": "后来切换的角色"},
        ).json()
        other_persona = client.post(
            "/api/user-personas",
            json={"name": "后来切换的用户"},
        ).json()
        active_preset = next(
            item for item in client.get("/api/presets").json() if item["is_active"]
        )
        changed = client.put(
            f"/api/presets/{active_preset['id']}",
            json={
                "character_id": other_character["id"],
                "user_persona_id": other_persona["id"],
            },
        )
        assert changed.status_code == 200
        historical_names = client.get(
            f"/api/runtime/conversations/{original['id']}/messages"
        ).json()
        assert [item["speaker_name"] for item in historical_names] == [
            "用户",
            "默认助手",
            "用户",
            "默认助手",
        ]

        rejected = client.post(
            f"/api/runtime/conversations/{alternate['id']}/messages/delete",
            json={"message_ids": [alternate_messages[0]["id"], original_messages[0]["id"]]},
        )
        assert rejected.status_code == 400
        assert "不属于当前" in rejected.json()["detail"]
        assert len(client.get(
            f"/api/runtime/conversations/{alternate['id']}/messages"
        ).json()) == 2

        deleted = client.post(
            f"/api/runtime/conversations/{alternate['id']}/messages/delete",
            json={"message_ids": [item["id"] for item in alternate_messages]},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted_count": 2, "remaining_count": 0}
        assert client.get(
            f"/api/runtime/conversations/{alternate['id']}/messages"
        ).json() == []
        refreshed_alternate = next(
            item
            for item in client.get("/api/runtime/conversations").json()
            if item["id"] == alternate["id"]
        )
        assert refreshed_alternate["message_count"] == 0
        assert refreshed_alternate["total_tokens"] == 0

        removed = client.delete(f"/api/runtime/conversations/{alternate['id']}")
        assert removed.status_code == 204
        only_record = client.get("/api/runtime/conversations").json()
        assert [item["id"] for item in only_record] == [original["id"]]
        refused = client.delete(f"/api/runtime/conversations/{original['id']}")
        assert refused.status_code == 400
        assert "至少保留" in refused.json()["detail"]


def test_last_empty_chat_record_can_be_deleted(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        created = client.post(
            "/api/runtime/conversations",
            json={"route_id": "qq:90001:private:54321", "title": "临时空记录"},
        ).json()
        assert created["is_active"] is True
        removed = client.delete(f"/api/runtime/conversations/{created['id']}")
        assert removed.status_code == 204
        assert client.get("/api/runtime/conversations").json() == []


def test_manual_message_delete_marks_the_qq_turn_as_edited(tmp_path: Path) -> None:
    route_id = "qq:90001:private:7788"
    with make_client(tmp_path) as client:
        runtime = client.app.state.chat_runtime
        turn = runtime.begin_qq_turn(route_id, "31001", "7788", "private")
        user_message = runtime._append_message(turn.conversation_id, "user", "需要删除的消息")
        assistant_message = runtime._append_message(turn.conversation_id, "assistant", "保留回复")
        runtime.record_turn_user_message(turn.id, user_message.id)
        runtime.record_turn_assistant_message(turn.id, assistant_message.id)
        runtime.mark_turn_completed(turn.id)

        response = client.post(
            f"/api/runtime/conversations/{turn.conversation_id}/messages/delete",
            json={"message_ids": [user_message.id]},
        )
        assert response.status_code == 200
        remaining = client.get(
            f"/api/runtime/conversations/{turn.conversation_id}/messages"
        ).json()
        assert [item["id"] for item in remaining] == [assistant_message.id]
        assert remaining[0]["position"] == 1
        with client.app.state.database.session_factory() as session:
            saved_turn = session.get(ConversationTurn, turn.id)
            assert saved_turn is not None
            assert saved_turn.status == "edited"
            assert saved_turn.user_message_id is None
            assert saved_turn.assistant_message_id == assistant_message.id
        assert runtime.find_recall_turn(route_id, "31001", "7788") is None


def test_manual_message_edit_preserves_floor_and_marks_qq_turn_as_edited(tmp_path: Path) -> None:
    route_id = "qq:90001:private:8899"
    with make_client(tmp_path) as client:
        runtime = client.app.state.chat_runtime
        turn = runtime.begin_qq_turn(route_id, "32001", "8899", "private")
        user_message = runtime._append_message(turn.conversation_id, "user", "编辑前")
        assistant_message = runtime._append_message(turn.conversation_id, "assistant", "原回复")
        runtime.record_turn_user_message(turn.id, user_message.id)
        runtime.record_turn_assistant_message(turn.id, assistant_message.id)
        runtime.mark_turn_completed(turn.id)

        response = client.put(
            f"/api/runtime/conversations/{turn.conversation_id}/messages/{user_message.id}",
            json={"content": "编辑后的本地记录"},
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["content"] == "编辑后的本地记录"
        assert updated["position"] == 0
        assert updated["message_metadata"]["manually_edited"] is True
        remaining = client.get(
            f"/api/runtime/conversations/{turn.conversation_id}/messages"
        ).json()
        assert [(item["position"], item["content"]) for item in remaining] == [
            (0, "编辑后的本地记录"),
            (1, "原回复"),
        ]
        with client.app.state.database.session_factory() as session:
            saved_turn = session.get(ConversationTurn, turn.id)
            assert saved_turn is not None
            assert saved_turn.status == "edited"
            assert saved_turn.user_message_id == user_message.id
            assert saved_turn.assistant_message_id == assistant_message.id
        assert runtime.find_recall_turn(route_id, "32001", "8899") is None

        other = runtime._ensure_conversation("qq:90001:private:9900", "private")
        rejected = client.put(
            f"/api/runtime/conversations/{other.id}/messages/{user_message.id}",
            json={"content": "不能跨记录编辑"},
        )
        assert rejected.status_code == 400
        assert "不属于当前记录" in rejected.json()["detail"]
