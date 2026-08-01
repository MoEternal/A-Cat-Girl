from __future__ import annotations

import asyncio
import concurrent.futures
import json
import socket
import time
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from threading import Event

import httpx
import pytest
import catgirl.onebot as onebot_module
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect
from websockets.sync.client import connect as websocket_sync_connect

from catgirl.database import ConversationTurn, OneBotEvent, RuntimeAction, utcnow
from catgirl.main import create_app
from catgirl.model_client import OpenAICompatibleClient
from catgirl.plugins import PluginAction


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def configure_model(client: TestClient) -> None:
    client.put("/api/plugins/memory_system", json={"enabled": False})
    client.put("/api/plugins/reply_merge", json={"enabled": False})
    client.put("/api/plugins/segmented_reply", json={"enabled": False})
    provider = client.get("/api/providers").json()[0]
    client.put(
        f"/api/providers/{provider['id']}",
        json={
            "base_url": "https://model.test/v1",
            "model": "qq-model",
            "api_key": "sk-onebot-test",
            "enabled": True,
        },
    )
    preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
    client.put(f"/api/presets/{preset['id']}", json={"streaming": False})
    client.put("/api/plugins/sticker_reply", json={"settings": {"probability": 0}})


def private_event(message_id: int = 1001, text: str = "你好") -> dict:
    return {
        "time": 1784900000,
        "self_id": 90001,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": 12345,
        "raw_message": text,
    }


def friend_recall_event(message_id: int, user_id: int = 12345) -> dict:
    return {
        "time": 1784900001,
        "self_id": 90001,
        "post_type": "notice",
        "notice_type": "friend_recall",
        "user_id": user_id,
        "message_id": message_id,
    }


def group_event(
    message_id: int,
    text: str = "群消息",
    *,
    sender_role: str = "member",
    message: list[dict] | None = None,
) -> dict:
    event = {
        "time": 1784900000,
        "self_id": 90001,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": message_id,
        "group_id": 7788,
        "user_id": 12345,
        "raw_message": text,
        "sender": {"user_id": 12345, "role": sender_role},
    }
    if message is not None:
        event["message"] = message
    return event


def group_recall_event(message_id: int) -> dict:
    return {
        "time": 1784900001,
        "self_id": 90001,
        "post_type": "notice",
        "notice_type": "group_recall",
        "group_id": 7788,
        "user_id": 12345,
        "operator_id": 12345,
        "message_id": message_id,
    }


def wait_for_turn_status(client: TestClient, message_id: int, status: str) -> ConversationTurn:
    for _ in range(100):
        with client.app.state.database.session_factory() as session:
            turn = session.scalar(
                select(ConversationTurn)
                .where(ConversationTurn.trigger_message_id == str(message_id))
                .order_by(ConversationTurn.created_at.desc())
            )
            if turn is not None and turn.status == status:
                session.expunge(turn)
                return turn
        client.portal.call(asyncio.sleep, 0.01)
    raise AssertionError(f"turn {message_id} did not reach {status}")


class FakeForwardSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        payload = json.loads(raw)
        self.sent.append(payload)
        if payload["action"] == "get_login_info":
            self.incoming.put_nowait(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"user_id": 90001},
                        "echo": payload["echo"],
                    }
                )
            )
        elif payload.get("echo") is not None:
            self.incoming.put_nowait(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": 2001},
                        "echo": payload["echo"],
                    }
                )
            )

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        self.closed = True


class FakeForwardContext:
    def __init__(self, socket: FakeForwardSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> FakeForwardSocket:
        return self.socket

    async def __aexit__(self, _exc_type, _exc_value, _traceback) -> None:
        await self.socket.close()


class FakeForwardConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.sockets: list[FakeForwardSocket] = []

    def __call__(self, url: str, **kwargs) -> FakeForwardContext:
        socket = FakeForwardSocket()
        self.calls.append((url, kwargs))
        self.sockets.append(socket)
        return FakeForwardContext(socket)


def test_onebot_config_is_disabled_by_default_and_masks_token(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        config = client.get("/api/onebot/config").json()
        assert config["enabled"] is False
        assert config["reverse_ws_url"] == ""
        assert config["private_messages"] is True
        assert config["group_messages"] is False

        updated = client.put(
            "/api/onebot/config",
            json={
                "enabled": True,
                "reverse_ws_url": "wss://bot.example.test/onebot/v11/ws",
                "access_token": "onebot-super-secret",
                "private_allowlist": ["12345", "12345"],
            },
        ).json()
        assert updated["access_token_configured"] is True
        assert updated["reverse_ws_url"] == "wss://bot.example.test/onebot/v11/ws"
        assert updated["access_token_masked"] != "onebot-super-secret"
        assert updated["private_allowlist"] == ["12345"]
        assert "onebot-super-secret" not in json.dumps(updated)

        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/onebot/v11/ws",
                headers={"Authorization": "Bearer wrong", "X-Self-ID": "90001"},
            ):
                pass
        assert rejected.value.code == 1008


def test_onebot_without_access_token_accepts_an_unauthenticated_connection(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        response = client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": ""},
        )

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
        assert response.json()["access_token_configured"] is False
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"X-Self-ID": "90001"},
        ):
            status = client.get("/api/onebot/status").json()
            assert status["connected"] is True
            assert status["self_ids"] == ["90001"]


def test_reverse_websocket_can_listen_on_napcat_client_url(tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    reverse_url = f"ws://127.0.0.1:{port}/ws/"

    with make_client(tmp_path) as client:
        updated = client.put(
            "/api/onebot/config",
            json={
                "enabled": True,
                "connection_mode": "reverse",
                "reverse_ws_url": reverse_url,
                "access_token": "",
            },
        )
        assert updated.status_code == 200, updated.text

        with websocket_sync_connect(
            reverse_url,
            additional_headers={
                "X-Self-ID": "90001",
            },
        ) as websocket:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if client.get("/api/onebot/status").json()["connected"]:
                    break
                time.sleep(0.02)
            assert client.get("/api/onebot/status").json()["self_ids"] == ["90001"]

            gateway = client.app.state.onebot_gateway
            connection = next(iter(gateway.connections.values()))
            assert client.portal is not None
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.portal.call,
                    gateway._call,
                    connection,
                    "get_status",
                    {},
                )
                action = json.loads(websocket.recv(timeout=2))
                assert action["action"] == "get_status"
                websocket.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"online": True},
                            "echo": action["echo"],
                        }
                    )
                )
                assert pending.result(timeout=2)["data"] == {"online": True}

        disabled = client.put("/api/onebot/config", json={"enabled": False})
        assert disabled.status_code == 200

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_forward_websocket_connects_receives_and_sends(tmp_path: Path, monkeypatch) -> None:
    connector = FakeForwardConnector()
    monkeypatch.setattr("catgirl.onebot.websocket_connect", connector)

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "正向回复"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        response = client.put(
            "/api/onebot/config",
            json={
                "enabled": True,
                "connection_mode": "forward",
                "forward_ws_url": "ws://napcat.test:3001",
                "access_token": "forward-token",
                "private_messages": True,
            },
        )
        assert response.status_code == 200

        for _ in range(40):
            if client.get("/api/onebot/status").json()["connected"]:
                break
            client.portal.call(asyncio.sleep, 0.01)
        status = client.get("/api/onebot/status").json()
        assert status["connection_mode"] == "forward"
        assert status["connected"] is True
        assert status["self_ids"] == ["90001"]
        assert connector.calls[0][0] == "ws://napcat.test:3001"
        assert connector.calls[0][1]["additional_headers"] == {"Authorization": "Bearer forward-token"}

        socket = connector.sockets[0]
        socket.incoming.put_nowait(json.dumps(private_event(message_id=2002)))
        for _ in range(40):
            if any(item["action"] == "send_private_msg" for item in socket.sent):
                break
            client.portal.call(asyncio.sleep, 0.01)
        client.portal.call(client.app.state.action_executor.queue.join)
        sent = next(item for item in socket.sent if item["action"] == "send_private_msg")
        assert sent["params"]["user_id"] == 12345
        assert sent["params"]["message"] == [{"type": "text", "data": {"text": "正向回复"}}]


def test_private_message_runs_model_and_sends_onebot_action_once(tmp_path: Path) -> None:
    model_calls = 0

    async def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        payload = json.loads(request.content)
        assert payload["messages"][-1] == {"role": "user", "content": "你好"}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "QQ 回复"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "reverse-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer reverse-token", "X-Self-ID": "90001"},
        ) as websocket:
            status = client.get("/api/onebot/status").json()
            assert status["connected"] is True
            assert status["self_ids"] == ["90001"]

            event = private_event()
            websocket.send_json(event)
            request = websocket.receive_json()
            assert request["action"] == "send_private_msg"
            assert request["params"]["user_id"] == 12345
            assert request["params"]["message"] == [
                {"type": "text", "data": {"text": "QQ 回复"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 2001},
                    "echo": request["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

            messages = client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json()
            assert [(item["role"], item["content"]) for item in messages] == [
                ("user", "你好"),
                ("assistant", "QQ 回复"),
            ]
            action = next(
                item for item in client.get("/api/runtime/actions").json() if item["kind"] == "send_text"
            )
            assert action["status"] == "completed"

            websocket.send_json(event)
            client.portal.call(asyncio.sleep, 0.05)
            assert model_calls == 1
            with client.app.state.database.session_factory() as session:
                stored_event = session.get(OneBotEvent, "message:90001:1001")
                assert stored_event.status == "completed"

        assert client.get("/api/onebot/status").json()["connected"] is False


def test_group_management_requires_real_at_and_prefix_then_censors_input(tmp_path: Path) -> None:
    model_calls = 0

    async def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        payload = json.loads(request.content)
        assert payload["messages"][-1] == {"role": "user", "content": "包含**的消息"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "群聊回复"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        configured = client.put(
            "/api/plugins/group_chat_management",
            json={
                "enabled": True,
                "settings": {
                    "wake_prefix": "/",
                    "require_mention": True,
                    "censor_replacement": "*",
                },
            },
        )
        assert configured.status_code == 200, configured.text
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "group-token", "group_messages": True},
        )

        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer group-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(
                group_event(5101, "/添加屏蔽词 秘密", sender_role="admin")
            )
            command_reply = websocket.receive_json()
            assert command_reply["action"] == "send_group_msg"
            assert command_reply["params"]["message"] == [
                {"type": "text", "data": {"text": "已添加屏蔽词“秘密”。"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 6101},
                    "echo": command_reply["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

            # A plain @ character does not satisfy the real OneBot at requirement.
            websocket.send_json(group_event(5102, "/普通@机器人 包含秘密的消息"))
            # A real at without the configured wake prefix also remains silent.
            websocket.send_json(
                group_event(
                    5103,
                    "[CQ:at,qq=90001] 包含秘密的消息",
                    message=[
                        {"type": "at", "data": {"qq": "90001"}},
                        {"type": "text", "data": {"text": " 包含秘密的消息"}},
                    ],
                )
            )
            client.portal.call(asyncio.sleep, 0.1)
            assert model_calls == 0
            assert client.get("/api/runtime/conversations").json() == []

            websocket.send_json(
                group_event(
                    5104,
                    "[CQ:at,qq=90001] /包含秘密的消息",
                    message=[
                        {"type": "at", "data": {"qq": 90001}},
                        {"type": "text", "data": {"text": " /包含秘密的消息"}},
                    ],
                )
            )
            reply = websocket.receive_json()
            assert reply["action"] == "send_group_msg"
            assert reply["params"]["group_id"] == 7788
            assert reply["params"]["message"] == [
                {"type": "text", "data": {"text": "群聊回复"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 6104},
                    "echo": reply["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

        assert model_calls == 1
        records = client.get("/api/runtime/conversations").json()
        assert len(records) == 1
        messages = client.get(
            f"/api/runtime/conversations/{records[0]['id']}/messages"
        ).json()
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "包含**的消息"),
            ("assistant", "群聊回复"),
        ]
        assert "秘密" not in str(messages)


def test_reply_merge_batches_rapid_messages_and_second_id_can_recall_turn(
    tmp_path: Path,
) -> None:
    model_calls = 0

    async def model_handler(request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        payload = json.loads(request.content)
        assert payload["messages"][-1] == {"role": "user", "content": "第一条\n第二条"}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "合并回复"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        response = client.put(
            "/api/plugins/reply_merge",
            json={"enabled": True, "settings": {"message_batch_delay": 0.05}},
        )
        assert response.status_code == 200, response.text
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "batch-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer batch-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(4001, "第一条"))
            websocket.send_json(private_event(4002, "第二条"))
            request = websocket.receive_json()
            assert request["action"] == "send_private_msg"
            assert request["params"]["message"] == [
                {"type": "text", "data": {"text": "合并回复"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 5001},
                    "echo": request["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

            assert model_calls == 1
            with client.app.state.database.session_factory() as session:
                turn = session.scalar(
                    select(ConversationTurn).where(
                        ConversationTurn.trigger_message_id == "4001"
                    )
                )
                assert turn is not None
                assert turn.trigger_message_ids == ["4001", "4002"]
                for message_id in (4001, 4002):
                    event = session.get(OneBotEvent, f"message:90001:{message_id}")
                    assert event is not None and event.status == "completed"

            websocket.send_json(friend_recall_event(4002))
            delete_request = websocket.receive_json()
            assert delete_request["action"] == "delete_msg"
            assert delete_request["params"] == {"message_id": 5001}
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {},
                    "echo": delete_request["echo"],
                }
            )
            wait_for_turn_status(client, 4001, "recalled")


def test_reply_merge_zero_processes_messages_separately(tmp_path: Path) -> None:
    model_calls = 0

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": f"第{model_calls}次回复"}, "finish_reason": "stop"}
                ]
            },
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.put(
            "/api/plugins/reply_merge",
            json={"enabled": True, "settings": {"message_batch_delay": 0}},
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "zero-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer zero-token", "X-Self-ID": "90001"},
        ) as websocket:
            for index in range(2):
                websocket.send_json(private_event(4101 + index, f"第{index + 1}条"))
                request = websocket.receive_json()
                assert request["action"] == "send_private_msg"
                websocket.send_json(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": 5101 + index},
                        "echo": request["echo"],
                    }
                )
            client.portal.call(client.app.state.action_executor.queue.join)
            assert model_calls == 2


def test_recall_during_reply_merge_wait_removes_pending_message(tmp_path: Path) -> None:
    model_calls = 0

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "不应生成"}}]})

    with make_client(tmp_path) as client:
        configure_model(client)
        client.put(
            "/api/plugins/reply_merge",
            json={"enabled": True, "settings": {"message_batch_delay": 0.2}},
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "pending-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer pending-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(4201, "马上撤回"))
            client.portal.call(asyncio.sleep, 0.02)
            websocket.send_json(friend_recall_event(4201))
            client.portal.call(asyncio.sleep, 0.3)

        assert model_calls == 0
        with client.app.state.database.session_factory() as session:
            inbound = session.get(OneBotEvent, "message:90001:4201")
            recall = session.get(OneBotEvent, "notice:friend_recall:90001:4201")
            turn = session.scalar(
                select(ConversationTurn).where(ConversationTurn.trigger_message_id == "4201")
            )
            assert inbound is not None and inbound.status == "recalled"
            assert recall is not None and recall.status == "completed"
            assert turn is None


def test_recall_overtaking_message_preprocessing_blocks_old_turn_and_allows_resend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_calls = 0

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"第{model_calls}次回复"}}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.put(
            "/api/plugins/reply_merge",
            json={"enabled": True, "settings": {"message_batch_delay": 0.03}},
        )
        manager = client.app.state.plugin_manager
        original_dispatch = manager.dispatch

        async def delayed_dispatch(hook_name, event):
            if hook_name == "before_qq_message" and event.text == "撤回后重发":
                await asyncio.sleep(0.08)
            return await original_dispatch(hook_name, event)

        monkeypatch.setattr(manager, "dispatch", delayed_dispatch)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "early-recall", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer early-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(4251, "撤回后重发"))
            client.portal.call(asyncio.sleep, 0.01)
            websocket.send_json(friend_recall_event(4251))
            client.portal.call(asyncio.sleep, 0.15)

            assert model_calls == 0
            with client.app.state.database.session_factory() as session:
                inbound = session.get(OneBotEvent, "message:90001:4251")
                recall = session.get(OneBotEvent, "notice:friend_recall:90001:4251")
                old_turn = session.scalar(
                    select(ConversationTurn).where(
                        ConversationTurn.trigger_message_id == "4251"
                    )
                )
                assert inbound is not None and inbound.status == "recalled"
                assert recall is not None and recall.status == "completed"
                assert old_turn is None

            websocket.send_json(private_event(4252, "撤回后重发"))
            request = websocket.receive_json()
            assert request["action"] == "send_private_msg"
            assert request["params"]["message"] == [
                {"type": "text", "data": {"text": "第1次回复"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 5252},
                    "echo": request["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

        records = client.get("/api/runtime/conversations").json()
        assert len(records) == 1
        messages = client.get(
            f"/api/runtime/conversations/{records[0]['id']}/messages"
        ).json()
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "撤回后重发"),
            ("assistant", "第1次回复"),
        ]


def test_qq_regex_filters_preserve_console_history_and_skip_empty_text(tmp_path: Path) -> None:
    raw_reply = (
        "保留全局隐藏角色隐藏"
        "<Thinking source='model'>QQ 不应看到\n第二行</Thinking>"
        "<!-- Test Inputs Were Rejected -->结尾"
    )

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": raw_reply}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        character = client.get("/api/characters").json()[0]
        response = client.put(
            "/api/plugins/regex_filter/state",
            json={
                "state": {
                    "global_rules": [
                        {
                            "id": "hide-global",
                            "name": "隐藏全局片段",
                            "enabled": True,
                            "pattern": "全局隐藏",
                            "replacement": "",
                            "flags": "",
                        },
                        {
                            "id": "hide-thinking",
                            "name": "隐藏 Thinking",
                            "enabled": True,
                            "pattern": r"<\s*thinking\b[^>]*>.*?<\s*/\s*thinking\s*>",
                            "replacement": "",
                            "flags": "is",
                        }
                    ],
                    "character_rules": {
                        character["id"]: [
                            {
                                "id": "hide-character",
                                "name": "隐藏角色片段",
                                "enabled": True,
                                "pattern": "角色隐藏",
                                "replacement": "",
                                "flags": "",
                            }
                        ]
                    },
                }
            },
        )
        assert response.status_code == 200, response.text
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "regex-token", "private_messages": True},
        )

        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer regex-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(1060, "测试正则"))
            request = websocket.receive_json()
            assert request["action"] == "send_private_msg"
            assert request["params"]["message"] == [
                {"type": "text", "data": {"text": "保留结尾"}}
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 2060},
                    "echo": request["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)

        messages = client.get(
            "/api/runtime/conversations/qq:90001:private:12345/messages"
        ).json()
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == raw_reply
        action = next(
            item for item in client.get("/api/runtime/actions").json() if item["kind"] == "send_text"
        )
        assert action["payload"]["text"] == raw_reply
        assert action["payload"]["character_id"] == character["id"]

        skipped = client.portal.call(
            client.app.state.onebot_gateway.send_plugin_action,
            "runtime",
            PluginAction(
                kind="send_text",
                payload={
                    "conversation_id": "qq:90001:private:12345",
                    "text": "<Thinking>全部隐藏</Thinking><!-- Test Inputs Were Rejected -->",
                    "character_id": character["id"],
                },
            ),
        )
        assert skipped == {"status": "ok", "retcode": 0, "data": {"skipped": True}}


def test_provider_failover_notices_each_failed_hop_and_recalls_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("catgirl.onebot.API_ERROR_RECALL_SECONDS", 0.01)
    attempted_models: list[str] = []

    async def model_handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        attempted_models.append(model)
        if model == "third-model":
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "第三接口成功"}, "finish_reason": "stop"}
                    ]
                },
            )
        return httpx.Response(503, json={"error": {"message": f"{model} unavailable"}})

    with make_client(tmp_path) as client:
        configure_model(client)
        client.post(
            "/api/providers",
            json={
                "name": "新接口3",
                "base_url": "https://third.test/v1",
                "model": "third-model",
                "priority": 30,
            },
        )
        client.post(
            "/api/providers",
            json={
                "name": "新接口2",
                "base_url": "https://second.test/v1",
                "model": "second-model",
                "priority": 20,
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "failover-token", "private_messages": True},
        )

        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer failover-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(1049, "触发故障转移"))
            expected_notices = {
                "API配置·默认接口 调用失败，故障转移至 API配置·新接口2（此消息在60秒后自动撤回）": 5041,
                "API配置·新接口2 调用失败，故障转移至 API配置·新接口3（此消息在60秒后自动撤回）": 5042,
            }
            sent_notice_ids: set[int] = set()
            recalled_notice_ids: set[int] = set()
            reply_seen = False
            while not reply_seen or recalled_notice_ids != sent_notice_ids or len(sent_notice_ids) < 2:
                request = websocket.receive_json()
                if request["action"] == "send_private_msg":
                    text = request["params"]["message"][0]["data"]["text"]
                    if text in expected_notices:
                        message_id = expected_notices[text]
                        sent_notice_ids.add(message_id)
                    else:
                        assert text == "第三接口成功"
                        message_id = 5043
                        reply_seen = True
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": message_id},
                            "echo": request["echo"],
                        }
                    )
                elif request["action"] == "delete_msg":
                    recalled_notice_ids.add(request["params"]["message_id"])
                    websocket.send_json(
                        {"status": "ok", "retcode": 0, "data": {}, "echo": request["echo"]}
                    )
                else:
                    pytest.fail(f"unexpected OneBot action: {request['action']}")

            assert sent_notice_ids == {5041, 5042}
            assert recalled_notice_ids == sent_notice_ids

        assert attempted_models == ["qq-model", "second-model", "third-model"]
        messages = client.get(
            "/api/runtime/conversations/qq:90001:private:12345/messages"
        ).json()
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "触发故障转移"),
            ("assistant", "第三接口成功"),
        ]


def test_model_api_error_is_sent_then_recalled_without_entering_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr("catgirl.onebot.API_ERROR_RECALL_SECONDS", 0.01)
    first_error = {"error": {"message": "primary unavailable", "code": "primary"}}
    last_error = {"error": {"message": "last relay quota exhausted", "code": "quota"}}
    attempted_models: list[str] = []

    async def model_handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        attempted_models.append(model)
        return httpx.Response(429, json=last_error if model == "last-model" else first_error)

    with make_client(tmp_path) as client:
        configure_model(client)
        client.post(
            "/api/providers",
            json={
                "name": "最后备用接口",
                "base_url": "https://last.test/v1",
                "model": "last-model",
                "priority": 2,
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "error-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer error-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(1050, "触发中转站错误"))
            failover_notice = websocket.receive_json()
            assert failover_notice["action"] == "send_private_msg"
            assert failover_notice["params"]["message"] == [
                {
                    "type": "text",
                    "data": {
                        "text": (
                            "API配置·默认接口 调用失败，故障转移至 "
                            "API配置·最后备用接口（此消息在60秒后自动撤回）"
                        )
                    },
                }
            ]
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 5049},
                    "echo": failover_notice["echo"],
                }
            )

            error_notice = websocket.receive_json()
            assert error_notice["action"] == "send_private_msg"
            error_text = error_notice["params"]["message"][0]["data"]["text"]
            assert error_text == (
                "API配置·最后备用接口 调用失败，所有 API 配置均不可用。\n"
                "完整报错：\n"
                "错误类型：ModelHTTPError\n"
                "HTTP 状态：429\n"
                "错误消息：last relay quota exhausted\n"
                "响应正文：\n"
                f"{json.dumps(last_error, ensure_ascii=False, separators=(',', ':'))}"
                "（此消息在60秒后自动撤回）"
            )
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 5050},
                    "echo": error_notice["echo"],
                }
            )

            deleted_ids = set()
            while deleted_ids != {5049, 5050}:
                deletion = websocket.receive_json()
                assert deletion["action"] == "delete_msg"
                deleted_ids.add(deletion["params"]["message_id"])
                websocket.send_json(
                    {"status": "ok", "retcode": 0, "data": {}, "echo": deletion["echo"]}
                )

            with client.app.state.database.session_factory() as session:
                event = session.get(OneBotEvent, "message:90001:1050")
                turn = session.scalar(
                    select(ConversationTurn).where(
                        ConversationTurn.trigger_message_id == "1050"
                    )
                )
                assert event.status == "failed"
                assert turn.status == "failed"

            messages = client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json()
            assert [(item["role"], item["content"]) for item in messages] == [
                ("user", "触发中转站错误")
            ]
            logs = client.get("/api/logs").json()
            assert any(
                "模型 API 返回 HTTP 429" in item["message"]
                and json.dumps(last_error, ensure_ascii=False, separators=(",", ":"))
                in item["message"]
                for item in logs
            )
            assert attempted_models == ["qq-model", "last-model"]


def test_non_model_processing_error_marks_the_qq_turn_failed(tmp_path: Path) -> None:
    async def fail_download(_urls):
        raise RuntimeError("download failed")

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.media_receiver.download_images = fail_download
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "failure-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer failure-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(
                private_event(
                    1051,
                    "图片[CQ:image,file=x,url=https://qq-image.test/failure.png]",
                )
            )
            turn = wait_for_turn_status(client, 1051, "failed")

        assert turn.status == "failed"
        with client.app.state.database.session_factory() as session:
            event = session.get(OneBotEvent, "message:90001:1051")
            assert event is not None and event.status == "failed"


def test_connected_gateway_sends_safe_plugin_image_by_file_path(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "image-token"},
        )
        asset = tmp_path / "data" / "plugin-test-assets" / "sample.png"
        asset.parent.mkdir(parents=True)
        Image.new("RGB", (4, 4), (30, 120, 220)).save(asset, format="PNG")
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer image-token", "X-Self-ID": "90001"},
        ) as websocket:
            action = PluginAction(
                kind="send_image",
                payload={
                    "conversation_id": "qq:90001:private:12345",
                    "asset_ref": str(asset),
                },
            )
            client.portal.call(client.app.state.action_executor.submit, "sticker_reply", action)
            request = websocket.receive_json()
            assert request["action"] == "send_private_msg"
            segment = request["params"]["message"][0]
            assert segment["type"] == "image"
            assert Path(segment["data"]["file"]) == asset.resolve()
            assert "base64" not in json.dumps(request)
            websocket.send_json({"status": "ok", "retcode": 0, "echo": request["echo"]})
            client.portal.call(client.app.state.action_executor.queue.join)

            stored = next(
                item for item in client.get("/api/runtime/actions").json() if item["kind"] == "send_image"
            )
            assert stored["status"] == "completed"


def test_incoming_qq_image_is_inline_only_for_current_model_request(tmp_path: Path) -> None:
    output = BytesIO()
    Image.new("RGB", (32, 24), (30, 120, 220)).save(output, format="PNG")
    image_bytes = output.getvalue()
    model_payloads: list[dict] = []

    async def image_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=image_bytes, headers={"Content-Type": "image/png"})

    async def model_handler(request: httpx.Request) -> httpx.Response:
        model_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "这是一张图片"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.media_receiver.transport = httpx.MockTransport(image_handler)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put("/api/onebot/config", json={"enabled": True, "access_token": "media-token"})
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer media-token", "X-Self-ID": "90001"},
        ) as websocket:
            event = private_event(1002, "看图[CQ:image,file=x,url=https://qq-image.test/one.png]")
            websocket.send_json(event)
            action = websocket.receive_json()
            assert action["params"]["message"][0]["data"]["text"] == "这是一张图片"
            websocket.send_json({"status": "ok", "retcode": 0, "echo": action["echo"]})
            client.portal.call(client.app.state.action_executor.queue.join)

        current_content = model_payloads[0]["messages"][-1]["content"]
        assert isinstance(current_content, list)
        assert current_content[0]["type"] == "image_url"
        assert current_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert current_content[-1] == {"type": "text", "text": "看图"}

        messages = client.get(
            "/api/runtime/conversations/qq:90001:private:12345/messages"
        ).json()
        assert messages[0]["content"].startswith("[图片1: ")
        assert messages[0]["content"].endswith("\n看图")
        assert "base64" not in json.dumps(messages, ensure_ascii=False)
        media_ref = messages[0]["message_metadata"]["media_refs"][0]["ref"]
        assert (tmp_path / "data" / media_ref).is_file()


def test_recall_before_model_reply_cancels_generation_and_rolls_back_history(tmp_path: Path) -> None:
    model_started = Event()

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        model_started.set()
        await asyncio.Event().wait()
        return httpx.Response(500)

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "recall-token", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer recall-token", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(3101, "撤回前不应回复"))
            for _ in range(100):
                if model_started.is_set():
                    break
                client.portal.call(asyncio.sleep, 0.01)
            assert model_started.is_set()
            websocket.send_json(friend_recall_event(3101))
            turn = wait_for_turn_status(client, 3101, "recalled")

            messages = client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json()
            assert messages == []
            with client.app.state.database.session_factory() as session:
                outbound = session.scalars(
                    select(RuntimeAction).where(
                        RuntimeAction.turn_id == turn.id,
                        RuntimeAction.kind.in_(("send_text", "send_image")),
                    )
                ).all()
            assert outbound == []


def test_recall_polling_fallback_rolls_back_when_notice_is_not_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onebot_module, "RECALL_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(onebot_module, "RECALL_POLL_WINDOW_SECONDS", 2.0)

    model_calls = 0

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        content = "这条回复应被撤回" if model_calls == 1 else "{}"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        conversation = client.app.state.chat_runtime._ensure_conversation(
            "qq:90001:private:12345", "qq_private"
        )
        client.put(
            "/api/plugins/memory_system",
            json={
                "enabled": True,
                "settings": {"auto_analyze": True, "detail_audit_interval": 0},
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "poll-recall", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer poll-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(3151, "通过轮询识别撤回"))
            get_msg_calls = 0
            history_saw_present = False
            history_recall_marker_seen = False
            reply_sent = False
            sent_message_id = 4251
            deletion_seen = False
            for _ in range(60):
                request = websocket.receive_json()
                action = request["action"]
                if action == "get_msg":
                    get_msg_calls += 1
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {
                                "message_id": 3151,
                                "raw_message": "通过轮询识别撤回",
                            },
                            "echo": request["echo"],
                        }
                    )
                elif action == "get_friend_msg_history":
                    assert request["params"] == {
                        "user_id": "12345",
                        "message_seq": "3151",
                        "count": 100,
                        "reverse_order": False,
                        "disable_get_url": True,
                        "parse_mult_msg": False,
                        "quick_reply": False,
                        "reverseOrder": False,
                    }
                    if not history_saw_present or not reply_sent:
                        history_saw_present = True
                        messages = [
                            {
                                "message_id": 3151,
                                "raw_message": "通过轮询识别撤回",
                                "message": [
                                    {
                                        "type": "text",
                                        "data": {"text": "通过轮询识别撤回"},
                                    }
                                ],
                            }
                        ]
                    else:
                        history_recall_marker_seen = True
                        messages = [
                            {
                                "message_id": 3151,
                                "raw_message": "",
                                "message": [],
                            }
                        ]
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"messages": messages},
                            "echo": request["echo"],
                        }
                    )
                elif action == "send_private_msg":
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": sent_message_id},
                            "echo": request["echo"],
                        }
                    )
                    reply_sent = True
                    assert client.app.state.plugin_manager.get_conversation_state(
                        "memory_system", conversation.id
                    )["turn"] == 1
                elif action == "delete_msg":
                    assert request["params"] == {"message_id": sent_message_id}
                    assert client.app.state.plugin_manager.get_conversation_state(
                        "memory_system", conversation.id
                    )["turn"] == 0
                    websocket.send_json(
                        {"status": "ok", "retcode": 0, "data": {}, "echo": request["echo"]}
                    )
                    deletion_seen = True
                    break
            assert get_msg_calls == 1
            assert history_saw_present
            assert history_recall_marker_seen
            assert deletion_seen
            wait_for_turn_status(client, 3151, "recalled")
            assert client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json() == []
            assert client.app.state.plugin_manager.get_conversation_state(
                "memory_system", conversation.id
            )["turn"] == 0


def test_recall_polling_handles_immediate_recall_before_history_ever_sees_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onebot_module, "RECALL_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(onebot_module, "RECALL_POLL_WINDOW_SECONDS", 2.0)
    model_started = Event()

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        model_started.set()
        await asyncio.Event().wait()
        return httpx.Response(500)

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "instant-recall", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer instant-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(3161, "发出后立即撤回"))
            history_calls = 0
            get_msg_calls = 0
            while history_calls < 2:
                request = websocket.receive_json()
                if request["action"] == "get_msg":
                    get_msg_calls += 1
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {
                                "message_id": 3161,
                                "raw_message": "发出后立即撤回",
                                "message": [
                                    {"type": "text", "data": {"text": "发出后立即撤回"}}
                                ],
                            },
                            "echo": request["echo"],
                        }
                    )
                elif request["action"] == "get_friend_msg_history":
                    assert request["params"]["message_seq"] == "3161"
                    history_calls += 1
                    websocket.send_json(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"messages": []},
                            "echo": request["echo"],
                        }
                    )

            assert get_msg_calls == 1
            assert model_started.wait(timeout=1)
            wait_for_turn_status(client, 3161, "recalled")
            assert client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json() == []


def test_recall_during_segmented_reply_stops_and_deletes_sent_segments(tmp_path: Path) -> None:
    async def model_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "第一段|||第二段|||第三段"}, "finish_reason": "stop"}
                ]
            },
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.put(
            "/api/plugins/segmented_reply",
            json={
                "enabled": True,
                "settings": {
                    "max_segments": 5,
                    "base_delay_seconds": 1,
                    "seconds_per_text_unit": 0,
                    "max_delay_seconds": 1,
                },
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "segment-recall", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer segment-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(3201, "请分段回复"))
            first_send = websocket.receive_json()
            assert first_send["action"] == "send_private_msg"
            assert first_send["params"]["message"][0]["data"]["text"] == "第一段"
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 4201},
                    "echo": first_send["echo"],
                }
            )
            websocket.send_json(friend_recall_event(3201))
            deletion = websocket.receive_json()
            assert deletion["action"] == "delete_msg"
            assert deletion["params"] == {"message_id": 4201}
            websocket.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": deletion["echo"]}
            )
            turn = wait_for_turn_status(client, 3201, "recalled")

            with client.app.state.database.session_factory() as session:
                actions = list(
                    session.scalars(
                        select(RuntimeAction)
                        .where(
                            RuntimeAction.turn_id == turn.id,
                            RuntimeAction.kind == "send_text",
                        )
                        .order_by(RuntimeAction.created_at)
                    ).all()
                )
            assert [item.status for item in actions] == ["completed", "cancelled", "cancelled"]
            assert client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json() == []


def test_group_recall_after_reply_deletes_output_and_restores_memory(tmp_path: Path) -> None:
    model_calls = 0

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal model_calls
        model_calls += 1
        content = "群聊回复" if model_calls == 1 else "{}"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.put(
            "/api/plugins/memory_system",
            json={
                "enabled": True,
                "settings": {"auto_analyze": True, "detail_audit_interval": 0},
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.app.state.chat_runtime._ensure_conversation(
            "qq:90001:group:7788", "qq_group"
        )
        client.app.state.plugin_manager.set_conversation_state(
            "recall", "qq:90001:group:7788", {"marker": "before"}
        )
        client.put(
            "/api/onebot/config",
            json={
                "enabled": True,
                "access_token": "group-recall",
                "group_messages": True,
                "group_allowlist": ["7788"],
            },
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer group-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(group_event(3301))
            sent = websocket.receive_json()
            assert sent["action"] == "send_group_msg"
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 4301},
                    "echo": sent["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)
            memory = client.app.state.plugin_manager.get_conversation_state(
                "memory_system", "qq:90001:group:7788"
            )
            assert memory["turn"] == 1
            client.app.state.plugin_manager.set_conversation_state(
                "recall", "qq:90001:group:7788", {"marker": "changed"}
            )

            websocket.send_json(group_recall_event(3301))
            deletion = websocket.receive_json()
            assert deletion["action"] == "delete_msg"
            assert deletion["params"] == {"message_id": 4301}
            websocket.send_json(
                {"status": "ok", "retcode": 0, "data": {}, "echo": deletion["echo"]}
            )
            wait_for_turn_status(client, 3301, "recalled")

            assert client.get(
                "/api/runtime/conversations/qq:90001:group:7788/messages"
            ).json() == []
            assert client.app.state.plugin_manager.get_conversation_state(
                "memory_system", "qq:90001:group:7788"
            )["turn"] == 0
            assert client.app.state.plugin_manager.get_conversation_state(
                "recall", "qq:90001:group:7788"
            ) == {"marker": "before"}


def test_expired_recall_is_silent_and_does_not_cancel_generation(tmp_path: Path) -> None:
    model_started = Event()
    release_holder: dict[str, asyncio.Event] = {}

    async def model_handler(_request: httpx.Request) -> httpx.Response:
        release = asyncio.Event()
        release_holder["event"] = release
        model_started.set()
        await release.wait()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "正常完成"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_model(client)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(model_handler)
        )
        client.put(
            "/api/onebot/config",
            json={"enabled": True, "access_token": "expired-recall", "private_messages": True},
        )
        with client.websocket_connect(
            "/onebot/v11/ws",
            headers={"Authorization": "Bearer expired-recall", "X-Self-ID": "90001"},
        ) as websocket:
            websocket.send_json(private_event(3401, "过期撤回"))
            for _ in range(100):
                if model_started.is_set():
                    break
                client.portal.call(asyncio.sleep, 0.01)
            assert model_started.is_set()
            with client.app.state.database.session_factory() as session:
                turn = session.scalar(
                    select(ConversationTurn).where(
                        ConversationTurn.trigger_message_id == "3401"
                    )
                )
                turn.created_at = utcnow() - timedelta(seconds=120)
                session.commit()

            websocket.send_json(friend_recall_event(3401))
            for _ in range(100):
                with client.app.state.database.session_factory() as session:
                    recall_event = session.get(
                        OneBotEvent, "notice:friend_recall:90001:3401"
                    )
                    if recall_event is not None and recall_event.status == "ignored":
                        break
                client.portal.call(asyncio.sleep, 0.01)
            assert recall_event.status == "ignored"
            assert any(
                item.trigger_message_id == "3401"
                for item in [wait_for_turn_status(client, 3401, "active")]
            )

            client.portal.call(release_holder["event"].set)
            sent = websocket.receive_json()
            assert sent["action"] == "send_private_msg"
            websocket.send_json(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {"message_id": 4401},
                    "echo": sent["echo"],
                }
            )
            client.portal.call(client.app.state.action_executor.queue.join)
            wait_for_turn_status(client, 3401, "completed")
            messages = client.get(
                "/api/runtime/conversations/qq:90001:private:12345/messages"
            ).json()
            assert [item["role"] for item in messages] == ["user", "assistant"]
