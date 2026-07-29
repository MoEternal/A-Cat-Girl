from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote

import httpx

from .media import estimate_message_tokens
from .prompt_post_processing import post_process_prompt
from .token_counter import count_text_tokens, tokenizer_name


LOGGER = logging.getLogger("catgirl.model")
SUPPORTED_PROVIDER_KINDS = {"openai_compatible", "anthropic", "google_gemini"}


class ModelClientError(RuntimeError):
    pass


class ModelConfigurationError(ModelClientError):
    pass


class ModelHTTPError(ModelClientError):
    def __init__(self, status_code: int, message: str, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ModelProtocolError(ModelClientError):
    pass


@dataclass(frozen=True)
class ProviderConnection:
    base_url: str
    model: str
    api_key: str = ""
    kind: str = "openai_compatible"
    chat_completion_source: str = "custom"
    prompt_post_processing: str = ""


@dataclass(frozen=True)
class ChatCompletionRequest:
    messages: list[dict[str, Any]]
    max_tokens: int
    temperature: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    top_p: float = 1.0
    candidate_count: int = 1
    stream: bool = True
    reasoning_effort: str = "auto"
    user_name: str = ""
    character_name: str = ""

    def payload(self, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self.messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "top_p": self.top_p,
            "n": self.candidate_count,
            "stream": self.stream,
        }
        if self.reasoning_effort != "auto":
            payload["reasoning_effort"] = self.reasoning_effort
        if self.stream:
            payload["stream_options"] = {"include_usage": True}
        return payload


@dataclass
class ChatCompletionResult:
    text: str
    reasoning: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    response_id: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


def provider_api_base(base_url: str, kind: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        return ""
    path = httpx.URL(base).path.rstrip("/").lower()
    if kind == "anthropic" and not re.search(r"/v\d+(?:beta\d*)?$", path):
        return f"{base}/v1"
    if kind == "google_gemini" and not re.search(r"/v\d+(?:beta\d*)?$", path):
        return f"{base}/v1beta"
    return base


def provider_headers(kind: str, api_key: str, chat_completion_source: str = "custom") -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if not api_key:
        return headers
    if chat_completion_source == "azure_openai":
        headers["api-key"] = api_key
    elif chat_completion_source == "vertexai":
        headers["Authorization"] = f"Bearer {api_key}"
    elif kind == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    elif kind == "google_gemini":
        headers["x-goog-api-key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def provider_models_url(base_url: str, kind: str) -> str:
    return f"{provider_api_base(base_url, kind)}/models"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def _usage_values(usage: Any) -> tuple[int | None, int | None, int | None]:
    if not isinstance(usage, dict):
        return None, None, None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    return (
        prompt if isinstance(prompt, int) else None,
        completion if isinstance(completion, int) else None,
        total if isinstance(total, int) else None,
    )


def _data_uri(value: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", value, re.DOTALL | re.IGNORECASE)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _anthropic_content(content: Any) -> str | list[dict[str, Any]]:
    if isinstance(content, str):
        return content
    output: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            output.append({"type": "text", "text": str(part.get("text", ""))})
        elif part.get("type") in {"image_url", "image"}:
            image = part.get("image_url", {})
            url = str(image.get("url", "")) if isinstance(image, dict) else ""
            parsed = _data_uri(url)
            if parsed:
                output.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": parsed[0], "data": parsed[1]},
                    }
                )
    return output


def _anthropic_payload(request: ChatCompletionRequest, model: str) -> dict[str, Any]:
    system = [
        _content_text(message.get("content"))
        for message in request.messages
        if message.get("role") == "system" and _content_text(message.get("content"))
    ]
    messages = [
        {
            "role": "assistant" if message.get("role") == "assistant" else "user",
            "content": _anthropic_content(message.get("content")),
        }
        for message in request.messages
        if message.get("role") != "system"
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "stream": request.stream,
    }
    if system:
        payload["system"] = "\n\n".join(system)
    return payload


def _gemini_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"text": content}]
    output: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            output.append({"text": str(part.get("text", ""))})
        elif part.get("type") in {"image_url", "image"}:
            image = part.get("image_url", {})
            url = str(image.get("url", "")) if isinstance(image, dict) else ""
            parsed = _data_uri(url)
            if parsed:
                output.append({"inlineData": {"mimeType": parsed[0], "data": parsed[1]}})
    return output


def _gemini_payload(request: ChatCompletionRequest) -> dict[str, Any]:
    system_parts = [
        {"text": _content_text(message.get("content"))}
        for message in request.messages
        if message.get("role") == "system" and _content_text(message.get("content"))
    ]
    contents = [
        {
            "role": "model" if message.get("role") == "assistant" else "user",
            "parts": _gemini_parts(message.get("content")),
        }
        for message in request.messages
        if message.get("role") != "system"
    ]
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": request.max_tokens,
            "temperature": request.temperature,
            "topP": request.top_p,
            "frequencyPenalty": request.frequency_penalty,
            "presencePenalty": request.presence_penalty,
            "candidateCount": request.candidate_count,
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    return payload


class OpenAICompatibleClient:
    def __init__(
        self,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
        self.transport = transport

    async def complete(
        self,
        connection: ProviderConnection,
        request: ChatCompletionRequest,
    ) -> ChatCompletionResult:
        kind = connection.kind.strip() or "openai_compatible"
        request = replace(
            request,
            messages=post_process_prompt(
                request.messages,
                connection.prompt_post_processing,
                user_name=request.user_name,
                character_name=request.character_name,
            ),
        )
        base_url = provider_api_base(connection.base_url, kind)
        model = connection.model.strip()
        if kind not in SUPPORTED_PROVIDER_KINDS:
            raise ModelConfigurationError("当前 API 供应商协议不受支持")
        if not base_url:
            raise ModelConfigurationError("当前 API 供应商没有 Base URL")
        if not model:
            raise ModelConfigurationError("当前 API 供应商没有模型名称")
        headers = provider_headers(kind, connection.api_key, connection.chat_completion_source)
        estimated_prompt_tokens = estimate_message_tokens(request.messages, model)
        LOGGER.info(
            "调用模型 | source=%s | protocol=%s | model=%s | messages=%s | input_tokens=%s（%s 本地分词）",
            connection.chat_completion_source,
            kind,
            model,
            len(request.messages),
            estimated_prompt_tokens,
            tokenizer_name(model),
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                if kind == "anthropic":
                    result = await self._complete_anthropic(
                        client, base_url, headers, model, request, (connection.api_key,)
                    )
                elif kind == "google_gemini":
                    result = await self._complete_gemini(
                        client, base_url, headers, model, request, (connection.api_key,)
                    )
                else:
                    result = await self._complete_openai(
                        client, base_url, headers, model, request, (connection.api_key,)
                    )
                self._fill_and_log_usage(
                    result,
                    model=model,
                    estimated_prompt_tokens=estimated_prompt_tokens,
                )
                return result
        except ModelClientError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            LOGGER.error("模型 API 请求失败 | %s: %s", type(exc).__name__, exc)
            raise ModelClientError(f"模型请求失败：{type(exc).__name__}") from exc

    async def _complete_openai(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        model: str,
        request: ChatCompletionRequest,
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        url = f"{base_url}/chat/completions"
        payload = request.payload(model)
        if request.stream:
            return await self._complete_openai_stream(
                client, url, headers, payload, sensitive_values
            )
        response = await client.post(url, headers=headers, json=payload)
        await self._raise_for_status(response, sensitive_values)
        return self._parse_openai_response(response.json())

    async def _complete_anthropic(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        model: str,
        request: ChatCompletionRequest,
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        url = f"{base_url}/messages"
        payload = _anthropic_payload(request, model)
        if request.stream:
            return await self._complete_anthropic_stream(
                client, url, headers, payload, sensitive_values
            )
        response = await client.post(url, headers=headers, json=payload)
        await self._raise_for_status(response, sensitive_values)
        return self._parse_anthropic_response(response.json())

    async def _complete_gemini(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        model: str,
        request: ChatCompletionRequest,
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        method = "streamGenerateContent" if request.stream else "generateContent"
        url = f"{base_url}/models/{quote(model, safe='-._')}:{method}"
        if request.stream:
            url = f"{url}?alt=sse"
            return await self._complete_gemini_stream(
                client, url, headers, _gemini_payload(request), sensitive_values
            )
        response = await client.post(url, headers=headers, json=_gemini_payload(request))
        await self._raise_for_status(response, sensitive_values)
        return self._parse_gemini_response(response.json())

    @staticmethod
    def _fill_and_log_usage(
        result: ChatCompletionResult,
        *,
        model: str,
        estimated_prompt_tokens: int,
    ) -> None:
        estimated_completion_tokens = count_text_tokens(result.text, model)
        estimated = result.prompt_tokens is None or result.completion_tokens is None
        if result.prompt_tokens is None:
            result.prompt_tokens = estimated_prompt_tokens
        if result.completion_tokens is None:
            result.completion_tokens = estimated_completion_tokens
        if result.total_tokens is None:
            result.total_tokens = result.prompt_tokens + result.completion_tokens
        result.raw_metadata["token_usage_estimated"] = estimated
        LOGGER.info(
            "收到模型回复 | model=%s | tokens=%s + %s = %s（%s） | finish_reason=%s",
            model,
            result.prompt_tokens,
            result.completion_tokens,
            result.total_tokens,
            "本地分词" if estimated else "API usage",
            result.finish_reason or "unknown",
        )

    async def _complete_openai_stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str | None = None
        response_id = ""
        usage: Any = None
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            await self._raise_for_status(response, sensitive_values)
            async for data in self._sse_data(response):
                if data == "[DONE]":
                    break
                chunk = self._json_chunk(data)
                if isinstance(chunk.get("id"), str):
                    response_id = chunk["id"]
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = next(
                    (
                        item
                        for item in choices
                        if isinstance(item, dict) and item.get("index", 0) == 0
                    ),
                    {},
                )
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                text_parts.append(_content_text(delta.get("content")))
                reasoning = delta.get("reasoning_content", delta.get("reasoning"))
                if isinstance(reasoning, str):
                    reasoning_parts.append(reasoning)
                if isinstance(choice.get("finish_reason"), str):
                    finish_reason = choice["finish_reason"]
        prompt_tokens, completion_tokens, total_tokens = _usage_values(usage)
        return ChatCompletionResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_id=response_id,
        )

    async def _complete_anthropic_stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        text_parts: list[str] = []
        finish_reason: str | None = None
        response_id = ""
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            await self._raise_for_status(response, sensitive_values)
            async for data in self._sse_data(response):
                chunk = self._json_chunk(data)
                event_type = chunk.get("type")
                if event_type == "message_start":
                    message = chunk.get("message", {})
                    if isinstance(message, dict):
                        response_id = str(message.get("id", ""))
                        usage = message.get("usage", {})
                        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                            prompt_tokens = usage["input_tokens"]
                elif event_type == "content_block_delta":
                    delta = chunk.get("delta", {})
                    if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                        text_parts.append(delta["text"])
                elif event_type == "message_delta":
                    delta = chunk.get("delta", {})
                    if isinstance(delta, dict) and isinstance(delta.get("stop_reason"), str):
                        finish_reason = delta["stop_reason"]
                    usage = chunk.get("usage", {})
                    if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
                        completion_tokens = usage["output_tokens"]
        total = (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        )
        return ChatCompletionResult(
            text="".join(text_parts),
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            response_id=response_id,
        )

    async def _complete_gemini_stream(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        sensitive_values: tuple[str, ...],
    ) -> ChatCompletionResult:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            await self._raise_for_status(response, sensitive_values)
            async for data in self._sse_data(response):
                chunk = self._json_chunk(data)
                if isinstance(chunk.get("usageMetadata"), dict):
                    usage = chunk["usageMetadata"]
                candidates = chunk.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    continue
                candidate = candidates[0] if isinstance(candidates[0], dict) else {}
                content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
                parts = content.get("parts", []) if isinstance(content, dict) else []
                for part in parts:
                    if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                        continue
                    (reasoning_parts if part.get("thought") else text_parts).append(part["text"])
                if isinstance(candidate.get("finishReason"), str):
                    finish_reason = candidate["finishReason"]
        return ChatCompletionResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            finish_reason=finish_reason,
            prompt_tokens=usage.get("promptTokenCount") if isinstance(usage.get("promptTokenCount"), int) else None,
            completion_tokens=usage.get("candidatesTokenCount") if isinstance(usage.get("candidatesTokenCount"), int) else None,
            total_tokens=usage.get("totalTokenCount") if isinstance(usage.get("totalTokenCount"), int) else None,
        )

    @staticmethod
    async def _sse_data(response: httpx.Response):
        async for line in response.aiter_lines():
            line = line.strip()
            if line.startswith("data:"):
                yield line[5:].strip()

    @staticmethod
    def _json_chunk(data: str) -> dict[str, Any]:
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelProtocolError("模型流式响应包含无效 JSON") from exc
        if not isinstance(value, dict):
            raise ModelProtocolError("模型流式响应不是 JSON 对象")
        return value

    @staticmethod
    async def _raise_for_status(
        response: httpx.Response,
        sensitive_values: tuple[str, ...] = (),
    ) -> None:
        if response.status_code < 400:
            return
        body = await response.aread()
        raw = body.decode("utf-8", errors="replace").strip()
        for value in sensitive_values:
            if value:
                raw = raw.replace(value, "[已隐藏]")
        LOGGER.error(
            "模型 API 返回 HTTP %s | %s",
            response.status_code,
            raw[:20_000] or "（空响应正文）",
        )
        detail = ""
        try:
            payload = json.loads(raw)
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = error["message"]
        except json.JSONDecodeError:
            pass
        native_message = detail.strip() or raw or f"HTTP {response.status_code}"
        raise ModelHTTPError(response.status_code, native_message[:4_000], raw[:20_000])

    @staticmethod
    def _parse_openai_response(payload: Any) -> ChatCompletionResult:
        if not isinstance(payload, dict):
            raise ModelProtocolError("模型响应不是 JSON 对象")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelProtocolError("模型响应缺少 choices")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ModelProtocolError("模型响应缺少 message")
        prompt_tokens, completion_tokens, total_tokens = _usage_values(payload.get("usage"))
        reasoning = message.get("reasoning_content", message.get("reasoning", ""))
        return ChatCompletionResult(
            text=_content_text(message.get("content")),
            reasoning=reasoning if isinstance(reasoning, str) else "",
            finish_reason=choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_id=payload.get("id") if isinstance(payload.get("id"), str) else "",
        )

    @staticmethod
    def _parse_anthropic_response(payload: Any) -> ChatCompletionResult:
        if not isinstance(payload, dict):
            raise ModelProtocolError("Anthropic 响应不是 JSON 对象")
        content = payload.get("content")
        if not isinstance(content, list):
            raise ModelProtocolError("Anthropic 响应缺少 content")
        usage = payload.get("usage", {})
        prompt = usage.get("input_tokens") if isinstance(usage, dict) else None
        completion = usage.get("output_tokens") if isinstance(usage, dict) else None
        return ChatCompletionResult(
            text="".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ),
            finish_reason=payload.get("stop_reason") if isinstance(payload.get("stop_reason"), str) else None,
            prompt_tokens=prompt if isinstance(prompt, int) else None,
            completion_tokens=completion if isinstance(completion, int) else None,
            total_tokens=(prompt + completion) if isinstance(prompt, int) and isinstance(completion, int) else None,
            response_id=payload.get("id") if isinstance(payload.get("id"), str) else "",
        )

    @staticmethod
    def _parse_gemini_response(payload: Any) -> ChatCompletionResult:
        if not isinstance(payload, dict):
            raise ModelProtocolError("Gemini 响应不是 JSON 对象")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise ModelProtocolError("Gemini 响应缺少 candidates")
        candidate = candidates[0]
        content = candidate.get("content", {})
        parts = content.get("parts", []) if isinstance(content, dict) else []
        usage = payload.get("usageMetadata", {})
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                continue
            (reasoning_parts if part.get("thought") else text_parts).append(part["text"])
        return ChatCompletionResult(
            text="".join(text_parts),
            reasoning="".join(reasoning_parts),
            finish_reason=candidate.get("finishReason") if isinstance(candidate.get("finishReason"), str) else None,
            prompt_tokens=usage.get("promptTokenCount") if isinstance(usage.get("promptTokenCount"), int) else None,
            completion_tokens=usage.get("candidatesTokenCount") if isinstance(usage.get("candidatesTokenCount"), int) else None,
            total_tokens=usage.get("totalTokenCount") if isinstance(usage.get("totalTokenCount"), int) else None,
        )
