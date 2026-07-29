from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from catgirl.main import create_app
from catgirl.model_client import (
    ChatCompletionRequest,
    ModelHTTPError,
    OpenAICompatibleClient,
    ProviderConnection,
    provider_headers,
)
from catgirl.plugins import PluginAction, PluginEvent
from catgirl.response_parser import parse_model_response


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "data", allow_unconfigured_management=True))


def configure_runtime(client: TestClient, *, streaming: bool = False) -> tuple[dict, dict]:
    client.put("/api/plugins/memory_system", json={"enabled": False})
    client.put("/api/plugins/segmented_reply", json={"enabled": False})
    provider = client.get("/api/providers").json()[0]
    provider = client.put(
        f"/api/providers/{provider['id']}",
        json={
            "base_url": "https://model.test/v1",
            "model": "test-model",
            "api_key": "sk-runtime-secret",
            "enabled": True,
        },
    ).json()
    preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
    preset = client.put(
        f"/api/presets/{preset['id']}",
        json={"streaming": streaming, "context_length": 4096, "max_response_tokens": 256},
    ).json()
    sticker = next(item for item in client.get("/api/plugins").json() if item["id"] == "sticker_reply")
    client.put(f"/api/plugins/{sticker['id']}", json={"settings": {"probability": 0}})
    return provider, preset


def test_openai_compatible_non_stream_request_and_secret_redaction() -> None:
    captured: dict = {}

    async def success_handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "choices": [
                    {
                        "message": {"content": "回复", "reasoning_content": "思考"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    request = ChatCompletionRequest(
        messages=[{"role": "user", "content": "你好"}],
        max_tokens=123,
        stream=False,
        reasoning_effort="high",
    )
    connection = ProviderConnection("https://model.test/v1", "model-a", "sk-secret")
    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(success_handler)).complete(connection, request)
    )

    assert captured["authorization"] == "Bearer sk-secret"
    assert captured["payload"]["model"] == "model-a"
    assert captured["payload"]["max_tokens"] == 123
    assert captured["payload"]["reasoning_effort"] == "high"
    assert result.text == "回复"
    assert result.reasoning == "思考"
    assert result.total_tokens == 12

    async def failure_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid sk-secret"}})

    with pytest.raises(ModelHTTPError) as raised:
        asyncio.run(
            OpenAICompatibleClient(transport=httpx.MockTransport(failure_handler)).complete(
                connection, request
            )
        )
    assert raised.value.status_code == 401
    assert "sk-secret" not in str(raised.value)
    assert "[已隐藏]" in str(raised.value)


def test_openai_compatible_uses_local_tokenizer_when_usage_is_missing() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "本地分词回复"}, "finish_reason": "stop"}]},
        )

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection("https://model.test/v1", "gpt-4o-mini"),
            ChatCompletionRequest(
                messages=[{"role": "user", "content": "请计算这一段文本"}],
                max_tokens=32,
                stream=False,
            ),
        )
    )

    assert result.prompt_tokens and result.prompt_tokens > 0
    assert result.completion_tokens and result.completion_tokens > 0
    assert result.total_tokens == result.prompt_tokens + result.completion_tokens
    assert result.raw_metadata["token_usage_estimated"] is True


def test_model_client_applies_provider_prompt_post_processing() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["messages"] = json.loads(request.content)["messages"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "完成"}, "finish_reason": "stop"}]},
        )

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection(
                "https://model.test/v1",
                "test-model",
                prompt_post_processing="merge",
            ),
            ChatCompletionRequest(
                messages=[
                    {"role": "system", "content": "规则一"},
                    {"role": "system", "content": "规则二"},
                    {"role": "user", "content": "问题"},
                ],
                max_tokens=32,
                stream=False,
            ),
        )
    )

    assert captured["messages"] == [
        {"role": "system", "content": "规则一\n\n规则二"},
        {"role": "user", "content": "问题"},
    ]
    assert result.text == "完成"


def test_source_specific_authentication_headers() -> None:
    assert provider_headers("openai_compatible", "secret", "azure_openai") == {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "api-key": "secret",
    }
    assert provider_headers("google_gemini", "token", "vertexai")[
        "Authorization"
    ] == "Bearer token"


def test_runtime_api_logs_native_model_error_body(tmp_path: Path) -> None:
    native_body = {"error": {"message": "relay says quota exhausted", "type": "relay_limit"}}

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json=native_body)

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "internal:error-log", "text": "触发错误"},
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "relay says quota exhausted"
        logs = client.get("/api/logs").json()
        error = next(item for item in logs if "模型 API 返回 HTTP 429" in item["message"])
        assert json.dumps(native_body, ensure_ascii=False, separators=(",", ":")) in error["message"]


def test_runtime_silently_falls_back_in_priority_order(tmp_path: Path) -> None:
    attempted_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        attempted_models.append(model)
        if model == "backup-three":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "备用接口成功"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                },
            )
        return httpx.Response(
            503,
            json={"error": {"message": f"{model} unavailable"}},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        backup_three = client.post(
            "/api/providers",
            json={
                "name": "第三接口",
                "base_url": "https://third.test/v1",
                "model": "backup-three",
                "priority": 30,
            },
        ).json()
        client.post(
            "/api/providers",
            json={
                "name": "第二接口",
                "base_url": "https://second.test/v1",
                "model": "backup-two",
                "priority": 20,
            },
        )
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )

        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "internal:provider-failover", "text": "测试接力"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "备用接口成功"
        assert response.json()["model"] == "backup-three"
        messages = client.get(
            "/api/runtime/conversations/internal:provider-failover/messages"
        ).json()

    assert attempted_models == ["test-model", "backup-two", "backup-three"]
    assert messages[-1]["provider_id"] == backup_three["id"]
    assert messages[-1]["model"] == "backup-three"


def test_openai_compatible_sse_stream_is_assembled() -> None:
    body = "\n".join(
        [
            'data: {"id":"chat-stream","choices":[{"index":0,"delta":{"reasoning_content":"想"},"finish_reason":null}]}',
            'data: {"id":"chat-stream","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}',
            'data: {"id":"chat-stream","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
            "data: [DONE]",
            "",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection("https://model.test/v1", "stream-model"),
            ChatCompletionRequest(
                messages=[{"role": "user", "content": "问"}],
                max_tokens=32,
                stream=True,
            ),
        )
    )
    assert result.text == "你好"
    assert result.reasoning == "想"
    assert result.finish_reason == "stop"
    assert result.total_tokens == 6


def test_anthropic_native_non_stream_request_and_usage() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "msg-anthropic",
                "content": [{"type": "text", "text": "Anthropic 回复"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 18, "output_tokens": 4},
            },
        )

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection(
                "https://api.anthropic.test",
                "claude-test",
                "anthropic-secret",
                "anthropic",
            ),
            ChatCompletionRequest(
                messages=[
                    {"role": "system", "content": "系统规则"},
                    {"role": "user", "content": "你好"},
                ],
                max_tokens=64,
                stream=False,
            ),
        )
    )

    assert captured["url"] == "https://api.anthropic.test/v1/messages"
    assert captured["headers"]["x-api-key"] == "anthropic-secret"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["system"] == "系统规则"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "你好"}]
    assert result.text == "Anthropic 回复"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (18, 4, 22)


def test_anthropic_native_stream_is_assembled() -> None:
    body = "\n".join(
        [
            'data: {"type":"message_start","message":{"id":"msg-stream","usage":{"input_tokens":9}}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"你"}}',
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}',
            'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}',
            "",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection("https://api.anthropic.test/v1", "claude-test", kind="anthropic"),
            ChatCompletionRequest(
                messages=[{"role": "user", "content": "你好"}],
                max_tokens=32,
                stream=True,
            ),
        )
    )
    assert result.text == "你好"
    assert result.finish_reason == "end_turn"
    assert result.total_tokens == 11


def test_gemini_native_non_stream_request_and_usage() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Gemini 回复"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 14,
                    "candidatesTokenCount": 3,
                    "totalTokenCount": 17,
                },
            },
        )

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection(
                "https://generativelanguage.test",
                "gemini-test",
                "google-secret",
                "google_gemini",
            ),
            ChatCompletionRequest(
                messages=[
                    {"role": "system", "content": "系统规则"},
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "在"},
                ],
                max_tokens=64,
                stream=False,
            ),
        )
    )

    assert captured["url"] == (
        "https://generativelanguage.test/v1beta/models/gemini-test:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "google-secret"
    assert captured["payload"]["systemInstruction"]["parts"] == [{"text": "系统规则"}]
    assert captured["payload"]["contents"][1]["role"] == "model"
    assert result.text == "Gemini 回复"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (14, 3, 17)


def test_gemini_native_stream_is_assembled() -> None:
    body = "\n".join(
        [
            'data: {"candidates":[{"content":{"parts":[{"text":"你"}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"text":"好"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":2,"totalTokenCount":10}}',
            "",
        ]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith(":streamGenerateContent?alt=sse")
        return httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})

    result = asyncio.run(
        OpenAICompatibleClient(transport=httpx.MockTransport(handler)).complete(
            ProviderConnection(
                "https://generativelanguage.test/v1beta",
                "gemini-test",
                kind="google_gemini",
            ),
            ChatCompletionRequest(
                messages=[{"role": "user", "content": "你好"}],
                max_tokens=32,
                stream=True,
            ),
        )
    )
    assert result.text == "你好"
    assert result.finish_reason == "STOP"
    assert result.total_tokens == 10


def test_runtime_compiles_calls_model_and_persists_safe_history(tmp_path: Path) -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "runtime-1",
                "choices": [
                    {
                        "message": {"content": "模型回复", "reasoning_content": "内部推理"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35},
            },
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:private:1", "user_id": "1", "text": "你好呀"},
        )
        assert response.status_code == 200
        reply = response.json()
        assert reply["text"] == "模型回复"
        assert reply["model"] == "test-model"
        assert reply["total_tokens"] == 35
        assert reply["outbound_actions"][0]["kind"] == "send_text"
        assert captured["headers"]["authorization"] == "Bearer sk-runtime-secret"
        assert captured["payload"]["messages"][-1] == {"role": "user", "content": "你好呀"}
        assert any(message["role"] == "system" for message in captured["payload"]["messages"])

        client.portal.call(client.app.state.action_executor.queue.join)
        messages = client.get("/api/runtime/conversations/qq:private:1/messages").json()
        assert [(item["role"], item["content"], item["status"]) for item in messages] == [
            ("user", "你好呀", "complete"),
            ("assistant", "模型回复", "complete"),
        ]
        assert messages[1]["message_metadata"]["reasoning"] == "内部推理"
        actions = client.get("/api/runtime/actions").json()
        outbound = next(item for item in actions if item["kind"] == "send_text")
        assert outbound["status"] == "pending"
        assert "base64" not in json.dumps(messages, ensure_ascii=False)


def test_consumed_plugin_command_skips_model_and_inline_media_is_rejected(tmp_path: Path) -> None:
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with make_client(tmp_path) as client:
        client.put("/api/plugins/proactive_reply", json={"enabled": True})
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        paused = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:command", "user_id": "admin", "text": "/暂停"},
        )
        assert paused.status_code == 200
        assert paused.json()["consumed"] is True
        assert calls == 0
        messages = client.get("/api/runtime/conversations/qq:command/messages").json()
        assert messages[0]["status"] == "consumed"

        rejected = client.post(
            "/api/runtime/messages",
            json={
                "conversation_id": "qq:unsafe",
                "text": "图片",
                "media": [{"kind": "image", "ref": "data:image/jpeg;base64,AAAA"}],
            },
        )
        assert rejected.status_code == 422
        assert client.get("/api/runtime/conversations/qq:unsafe/messages").json() == []

        escaped = client.post(
            "/api/runtime/messages",
            json={
                "conversation_id": "qq:unsafe-path",
                "text": "图片",
                "media": [{"kind": "image", "ref": "../secret.key"}],
            },
        )
        assert escaped.status_code == 422


def test_plugin_generation_action_uses_temporary_prompt_without_persisting_it(tmp_path: Path) -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "主动消息"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            },
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.put("/api/plugins/sticker_reply", json={"enabled": True})
        runtime = client.app.state.chat_runtime
        runtime.model_client = OpenAICompatibleClient(transport=httpx.MockTransport(handler))
        runtime._ensure_conversation("qq:idle", "qq")
        runtime._append_message("qq:idle", "user", "之前的消息", source="user")
        action = PluginAction(
            kind="request_generation",
            payload={
                "conversation_id": "qq:idle",
                "prompt": "这是一条不得写入历史的临时提示",
                "provider_policy": "selected_only",
                "history_policy": "temporary_prompt",
            },
        )
        client.portal.call(client.app.state.action_executor.submit, "test_plugin", action)
        client.portal.call(client.app.state.action_executor.queue.join)

        assert any(
            message["content"] == "这是一条不得写入历史的临时提示"
            for message in requests[0]["messages"]
        )
        messages = client.get("/api/runtime/conversations/qq:idle/messages").json()
        assert [item["content"] for item in messages] == ["之前的消息", "主动消息"]
        assert messages[1]["source"] == "plugin:test_plugin"
        actions = client.get("/api/runtime/actions").json()
        generation = next(item for item in actions if item["kind"] == "request_generation")
        assert generation["status"] == "completed"
        assert next(item for item in actions if item["kind"] == "send_text")["status"] == "pending"


def test_plugin_temporary_prompt_macros_are_rendered_in_the_model_request(tmp_path: Path) -> None:
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "主动消息"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        runtime = client.app.state.chat_runtime
        runtime.model_client = OpenAICompatibleClient(transport=httpx.MockTransport(handler))
        runtime._ensure_conversation("qq:macro", "qq")

        client.portal.call(
            runtime.generate_from_action,
            "proactive_reply",
            {
                "conversation_id": "qq:macro",
                "prompt": "{{user}}已经有一段时间没有说话。",
                "provider_policy": "selected_only",
                "history_policy": "temporary_prompt",
            },
        )

        contents = [message["content"] for message in requests[0]["messages"]]
        assert "用户已经有一段时间没有说话。" in contents
        assert all("{{user}}" not in str(content) for content in contents)


def test_same_conversation_model_requests_are_serialized(tmp_path: Path) -> None:
    active = 0
    max_active = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        payload = json.loads(request.content)
        latest_user = next(
            message["content"] for message in reversed(payload["messages"]) if message["role"] == "user"
        )
        await asyncio.sleep(0.03)
        active -= 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"回复:{latest_user}"}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        runtime = client.app.state.chat_runtime
        runtime.model_client = OpenAICompatibleClient(transport=httpx.MockTransport(handler))

        async def run_both():
            return await asyncio.gather(
                runtime.handle_user_message("qq:serial", "1", "第一条"),
                runtime.handle_user_message("qq:serial", "1", "第二条"),
            )

        replies = client.portal.call(run_both)
        assert [reply.text for reply in replies] == ["回复:第一条", "回复:第二条"]
        assert max_active == 1
        messages = runtime.list_messages("qq:serial")
        assert [(item.role, item.content) for item in messages] == [
            ("user", "第一条"),
            ("assistant", "回复:第一条"),
            ("user", "第二条"),
            ("assistant", "回复:第二条"),
        ]


def test_prompt_budget_trims_oldest_history_but_keeps_latest_user(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        preset = next(item for item in client.get("/api/presets").json() if item["is_active"])
        client.put(
            f"/api/presets/{preset['id']}",
            json={"context_length": 512, "max_response_tokens": 64},
        )
        runtime = client.app.state.chat_runtime
        runtime._ensure_conversation("qq:budget", "qq")
        for index in range(5):
            runtime._append_message("qq:budget", "user", f"旧问题{index}:" + "甲" * 260)
            runtime._append_message("qq:budget", "assistant", f"旧回答{index}:" + "乙" * 260)
        latest = "必须保留的当前消息"
        runtime._append_message("qq:budget", "user", latest)

        invocation = runtime._build_invocation("qq:budget", latest, [])
        contents = [str(item["content"]) for item in invocation.request.messages]
        assert latest in contents
        assert all("旧问题0" not in item and "旧回答0" not in item for item in contents)


def test_model_inline_image_output_never_reaches_history_or_outbox(tmp_path: Path) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "data:image/jpeg;base64," + "A" * 1000},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:model-unsafe", "text": "生成图片数据"},
        )
        assert response.status_code == 502
        assert "内联图片" in response.json()["detail"]
        messages = client.get("/api/runtime/conversations/qq:model-unsafe/messages").json()
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "生成图片数据")
        ]
        actions = client.get("/api/runtime/actions").json()
        assert all(
            item["conversation_id"] != "qq:model-unsafe"
            for item in actions
            if item["kind"] in {"send_text", "send_image"}
        )


def test_sticker_delimiter_and_long_text_create_separate_outbound_actions(tmp_path: Path) -> None:
    parsed = parse_model_response(
        '第一段|||第二段<sticker name="happy"/><sticker name="sad"/>'
    )
    assert parsed.text == "第一段|||第二段"
    assert parsed.text_segments == ["第一段", "第二段"]
    assert parsed.sticker_category == "happy"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '第一段|||第二段<sticker name="happy"/>'
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.put("/api/plugins/sticker_reply", json={"enabled": True})
        sticker_record = client.app.state.plugin_manager.records["sticker_reply"]
        sticker_root = tmp_path / "data" / "sticker-test-assets"
        (sticker_root / "assets" / "happy").mkdir(parents=True)
        (sticker_root / "memes_data.json").write_text(
            (sticker_record.path / "memes_data.json").read_text("utf-8"),
            "utf-8",
        )
        (sticker_root / "assets" / "happy" / "happy.png").write_bytes(b"test-image")
        sticker_record.path = sticker_root
        runtime = client.app.state.chat_runtime
        runtime.model_client = OpenAICompatibleClient(transport=httpx.MockTransport(handler))
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:segments", "text": "分段测试"},
        )
        assert response.status_code == 200
        reply = response.json()
        assert reply["text"] == "第一段\n第二段"
        assert [item["kind"] for item in reply["outbound_actions"]] == [
            "send_text",
            "send_text",
            "send_image",
        ]
        assert [item["payload"].get("text") for item in reply["outbound_actions"][:2]] == [
            "第一段",
            "第二段",
        ]
        messages = runtime.list_messages("qq:segments")
        assert messages[-1].content == "第一段\n第二段"

        client.put("/api/plugins/sticker_reply", json={"enabled": False})
        without_plugin = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:core-sticker-parser", "text": "核心解析测试"},
        ).json()
        assert without_plugin["text"] == "第一段\n第二段"
        assert [item["kind"] for item in without_plugin["outbound_actions"]] == [
            "send_text",
            "send_text",
        ]
        core_messages = runtime.list_messages("qq:core-sticker-parser")
        assert core_messages[-1].content == "第一段\n第二段"
        assert core_messages[-1].message_metadata["sticker_category"] == "happy"

        final_text, actions = runtime._apply_response_actions(
            "qq:long",
            "甲" * 8001,
            [],
        )
        assert len(final_text) == 8001
        assert [len(item.payload["text"]) for item in actions] == [4000, 4000, 1]


def test_segmented_response_delays_each_following_message(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        runtime = client.app.state.chat_runtime
        final_text, actions = runtime._apply_response_actions(
            "qq:segmented",
            "第一段|||第二段|||第三段",
            [
                PluginAction(
                    kind="replace_response",
                    payload={
                        "text_segments": ["第一段", "第二段", "第三段"],
                        "segment_reply": {
                            "max_segments": 5,
                            "base_delay_seconds": 1,
                            "seconds_per_text_unit": 0.25,
                            "max_delay_seconds": 8,
                        },
                    },
                )
            ],
        )
        assert final_text == "第一段\n第二段\n第三段"
        assert [item.payload["text"] for item in actions] == ["第一段", "第二段", "第三段"]
        assert "delay_seconds" not in actions[0].payload
        assert actions[1].payload["delay_seconds"] > 1
        assert actions[2].payload["delay_seconds"] > 1


def test_segmented_reply_ignores_delimiters_inside_enabled_regex_matches(tmp_path: Path) -> None:
    response_text = "<SiWeiLian>我不能用|||分隔符</SiWeiLian>你好|||晚安"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": response_text},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.put("/api/plugins/segmented_reply", json={"enabled": True})
        regex_state = {
            "global_rules": [
                {
                    "id": "hide-custom-reasoning",
                    "name": "隐藏自定义思维链",
                    "enabled": True,
                    "pattern": r"<\s*SiWeiLian\b[^>]*>.*?<\s*/\s*SiWeiLian\s*>",
                    "replacement": "",
                    "flags": "is",
                }
            ],
            "character_rules": {},
        }
        assert client.put("/api/plugins/regex_filter/state", json={"state": regex_state}).status_code == 200
        runtime = client.app.state.chat_runtime
        runtime.model_client = OpenAICompatibleClient(transport=httpx.MockTransport(handler))

        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:regex-segments", "text": "测试隐藏分段"},
        )
        assert response.status_code == 200, response.text
        reply = response.json()
        text_actions = [
            item for item in reply["outbound_actions"] if item["kind"] == "send_text"
        ]
        assert [item["payload"]["text"] for item in text_actions] == [
            "<SiWeiLian>我不能用|||分隔符</SiWeiLian>你好",
            "晚安",
        ]

        filtered = []
        for item in text_actions:
            result = client.portal.call(
                client.app.state.plugin_manager.dispatch,
                "before_send",
                PluginEvent(
                    name="before_send",
                    conversation_id="qq:regex-segments",
                    response_text=item["payload"]["text"],
                ),
            )
            filtered.append(result.metadata["outbound_text"])
        assert filtered == ["你好", "晚安"]


def test_web_search_replaces_intermediate_tag_with_contextual_answer(tmp_path: Path) -> None:
    model_requests: list[list[dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        messages = json.loads(request.content)["messages"]
        model_requests.append(messages)
        content = '<search query="A Cat Girl latest"/>' if len(model_requests) == 1 else "最终答案 [1]"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
        )

    with make_client(tmp_path) as client:
        configure_runtime(client, streaming=False)
        client.put("/api/plugins/web_search", json={"enabled": True})
        manager = client.app.state.plugin_manager
        record = manager.records["web_search"]
        module = sys.modules[record.instance.__class__.__module__]

        async def fake_search(settings: dict, query: str):
            assert settings["engine"] == "duckduckgo"
            assert query == "A Cat Girl latest"
            return [
                module.SearchResult(
                    title="Project page",
                    url="https://example.test/project",
                    snippet="Current project information",
                )
            ]

        record.instance._search = fake_search
        client.app.state.chat_runtime.model_client = OpenAICompatibleClient(
            transport=httpx.MockTransport(handler)
        )
        response = client.post(
            "/api/runtime/messages",
            json={"conversation_id": "qq:web-search", "text": "查一下最新信息"},
        )
        assert response.status_code == 200
        assert response.json()["text"] == "最终答案 [1]"
        assert len(model_requests) == 2
        first_prompt = "\n".join(str(item.get("content", "")) for item in model_requests[0])
        second_prompt = "\n".join(str(item.get("content", "")) for item in model_requests[1])
        assert "<search query=" in first_prompt
        assert "<web_search_context>" in second_prompt
        assert "https://example.test/project" in second_prompt

        messages = client.get(
            "/api/runtime/conversations/qq:web-search/messages"
        ).json()
        assert [(item["role"], item["content"]) for item in messages] == [
            ("user", "查一下最新信息"),
            ("assistant", "最终答案 [1]"),
        ]
        serialized = json.dumps(messages, ensure_ascii=False)
        assert "<search" not in serialized
        assert "web_search_context" not in serialized
