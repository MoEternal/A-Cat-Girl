from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update

from .action_executor import ActionExecutor
from .database import (
    Character,
    ChatMessage,
    ConfigurationPreset,
    Conversation,
    ConversationTurn,
    Database,
    PluginConversationState,
    PromptTemplate,
    Provider,
    RuntimeAction,
    UserPersona,
    WorldBook,
    WorldBookEntry,
    new_id,
    utcnow,
)
from .macro_engine import MacroContext, render_macros
from .media import (
    IMAGE_TOKEN_ESTIMATE,
    MediaValidationError,
    NormalizedImage,
    build_history_content,
    build_multimodal_user_content,
    ensure_history_content_safe,
    estimate_message_tokens,
)
from .model_client import (
    ChatCompletionRequest,
    ChatCompletionResult,
    ModelClientError,
    ModelProtocolError,
    OpenAICompatibleClient,
    ProviderConnection,
)
from .plugins.manager import PluginManager
from .plugins.types import PluginAction, PluginEvent
from .prompt_compiler import CompiledMessage, compile_prompt_messages
from .response_parser import parse_model_response
from .security import SecretBox
from .token_counter import count_text_tokens


LOGGER = logging.getLogger("catgirl.runtime")

ProviderFailureNotifier = Callable[[str, str | None, ModelClientError], Awaitable[None]]


class ChatRuntimeError(RuntimeError):
    pass


class RuntimeConfigurationError(ChatRuntimeError):
    pass


class PromptBudgetError(ChatRuntimeError):
    pass


@dataclass
class RuntimeReply:
    conversation_id: str
    route_id: str
    consumed: bool
    text: str = ""
    message_id: str | None = None
    model: str = ""
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    outbound_actions: list[PluginAction] = field(default_factory=list)


@dataclass
class Invocation:
    connection: ProviderConnection
    request: ChatCompletionRequest
    provider_id: str
    preset_id: str
    character_id: str | None
    character_name: str
    user_persona_id: str | None
    user_persona_name: str
    input_text: str
    show_thoughts: bool


@dataclass(frozen=True)
class ProviderTarget:
    id: str
    name: str


class ChatRuntime:
    def __init__(
        self,
        database: Database,
        secret_box: SecretBox,
        plugin_manager: PluginManager,
        action_executor: ActionExecutor,
        model_client: OpenAICompatibleClient | None = None,
    ):
        self.database = database
        self.secret_box = secret_box
        self.plugin_manager = plugin_manager
        self.action_executor = action_executor
        self.model_client = model_client or OpenAICompatibleClient()
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._recall_events: dict[str, asyncio.Event] = {}
        self._recall_turns: dict[str, str] = {}

    def _lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    @asynccontextmanager
    async def _user_turn_lock(self, conversation_id: str):
        while True:
            recall_event = self._recall_events.get(conversation_id)
            if recall_event is not None and not recall_event.is_set():
                await recall_event.wait()
            async with self._lock(conversation_id):
                recall_event = self._recall_events.get(conversation_id)
                if recall_event is not None and not recall_event.is_set():
                    continue
                yield
                return

    async def handle_user_message(
        self,
        conversation_id: str,
        user_id: str,
        text: str,
        channel: str = "internal",
        media_refs: list[dict[str, str]] | None = None,
        model_images: list[NormalizedImage] | None = None,
        turn_id: str | None = None,
        provider_failure_notifier: ProviderFailureNotifier | None = None,
    ) -> RuntimeReply:
        ensure_history_content_safe(text)
        media_refs = list(media_refs or [])
        async with self._user_turn_lock(conversation_id):
            record = self._ensure_conversation(conversation_id, channel)
            if turn_id:
                self.capture_turn_plugin_state(turn_id, record.id)
            event = PluginEvent(
                name="on_user_message",
                conversation_id=conversation_id,
                user_id=user_id,
                text=text,
                media=media_refs,
                metadata={"channel": channel, "record_id": record.id, "turn_id": turn_id},
            )
            plugin_result = await self.plugin_manager.dispatch("on_user_message", event)
            history_content = build_history_content(text, [item["ref"] for item in media_refs])
            user_message = self._append_message(
                record.id,
                "user",
                history_content,
                status="consumed" if plugin_result.consume else "complete",
                source="user",
                message_metadata={
                    "user_id": user_id,
                    "media_refs": media_refs,
                    "turn_id": turn_id,
                },
            )
            if turn_id:
                self.record_turn_user_message(turn_id, user_message.id)
            if plugin_result.consume:
                return RuntimeReply(
                    conversation_id=record.id,
                    route_id=conversation_id,
                    consumed=True,
                )
            return await self._generate_locked(
                conversation_id=record.id,
                route_id=conversation_id,
                input_text=text,
                source="runtime",
                source_plugin_id="runtime",
                trigger_message_id=user_message.id,
                model_images=list(model_images or []),
                turn_id=turn_id,
                provider_failure_notifier=provider_failure_notifier,
            )

    async def generate_from_action(self, plugin_id: str, payload: dict[str, Any]) -> RuntimeReply:
        conversation_id = str(payload.get("conversation_id", "")).strip()
        if not conversation_id:
            raise ChatRuntimeError("request_generation 缺少 conversation_id")
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise ChatRuntimeError("request_generation 缺少临时提示")
        if payload.get("provider_policy", "selected_only") != "selected_only":
            raise ChatRuntimeError("当前只允许 selected_only 供应商策略")
        if payload.get("history_policy", "temporary_prompt") != "temporary_prompt":
            raise ChatRuntimeError("当前只允许 temporary_prompt 历史策略")
        ensure_history_content_safe(prompt)
        async with self._lock(conversation_id):
            record = self._ensure_conversation(conversation_id, "plugin")
            return await self._generate_locked(
                conversation_id=record.id,
                route_id=conversation_id,
                input_text="",
                temporary_prompt=prompt,
                source=f"plugin:{plugin_id}",
                source_plugin_id=plugin_id,
            )

    async def generate_plugin_analysis(
        self,
        plugin_id: str,
        conversation_id: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        del plugin_id
        safe_messages = [
            {
                "role": message["role"],
                "content": ensure_history_content_safe(message["content"]),
            }
            for message in messages
        ]
        requested_tokens = max(1, min(int(max_tokens), 65535))
        provider_targets = self._provider_failover_targets()
        last_error: ModelClientError | None = None
        with self.database.session_factory() as session:
            if session.get(Conversation, conversation_id) is None:
                raise ChatRuntimeError("静默模型分析对应的聊天记录不存在")
        for attempt, target in enumerate(provider_targets, 1):
            with self.database.session_factory() as session:
                preset = session.scalar(
                    select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
                )
                if preset is None:
                    raise RuntimeConfigurationError("没有生效的组合预设")
                provider = session.get(Provider, target.id)
                if provider is None or not provider.enabled:
                    continue
                character = session.get(Character, preset.character_id) if preset.character_id else None
                user_persona = (
                    session.get(UserPersona, preset.user_persona_id)
                    if preset.user_persona_id
                    else None
                )
                prompt_tokens = estimate_message_tokens(safe_messages, provider.model)
                available_tokens = preset.context_length - prompt_tokens
                if available_tokens <= 0:
                    raise PromptBudgetError("静默模型分析输入超过当前上下文长度")
                request = ChatCompletionRequest(
                    messages=safe_messages,
                    max_tokens=min(requested_tokens, available_tokens),
                    temperature=max(0.0, min(float(temperature), 2.0)),
                    stream=False,
                    candidate_count=1,
                    user_name=user_persona.name if user_persona else "用户",
                    character_name=character.name if character else "当前角色",
                )
                connection = self._provider_connection(provider)
            try:
                result = await self.model_client.complete(connection, request)
                if not result.text.strip():
                    raise ModelProtocolError("模型返回了空回复")
                try:
                    return ensure_history_content_safe(result.text)
                except MediaValidationError as exc:
                    raise ModelProtocolError(
                        "模型回复包含不能写入历史的内联图片数据"
                    ) from exc
            except ModelClientError as exc:
                last_error = exc
                self._log_failover(
                    target.id,
                    connection.model,
                    attempt,
                    len(provider_targets),
                    exc,
                )
        if last_error is not None:
            raise last_error
        raise RuntimeConfigurationError("当前预设没有可用的 API 供应商")

    async def generate_plugin_continuation(
        self,
        plugin_id: str,
        conversation_id: str,
        prompt: str,
        inherited_additions: list[dict[str, str]],
    ) -> str:
        del plugin_id
        safe_prompt = ensure_history_content_safe(prompt)
        additions = [
            {
                "role": item["role"],
                "content": ensure_history_content_safe(item["content"]),
            }
            for item in inherited_additions
            if item.get("role") in {"system", "user", "assistant"}
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ]
        additions.append({"role": "system", "content": safe_prompt})
        invocation, result = await self._complete_with_failover(
            conversation_id,
            "",
            additions,
            [],
        )
        return ensure_history_content_safe(result.text)

    async def _generate_locked(
        self,
        conversation_id: str,
        route_id: str,
        input_text: str,
        source: str,
        source_plugin_id: str,
        temporary_prompt: str = "",
        trigger_message_id: str | None = None,
        model_images: list[NormalizedImage] | None = None,
        turn_id: str | None = None,
        provider_failure_notifier: ProviderFailureNotifier | None = None,
    ) -> RuntimeReply:
        pre_compile = await self.plugin_manager.dispatch(
            "before_prompt_compile",
            PluginEvent(
                name="before_prompt_compile",
                conversation_id=route_id,
                text=input_text,
                metadata={
                    "source": source,
                    "temporary": bool(temporary_prompt),
                    "record_id": conversation_id,
                    "trigger_message_id": trigger_message_id,
                    "turn_id": turn_id,
                },
            ),
        )
        if pre_compile.consume:
            return RuntimeReply(
                conversation_id=conversation_id,
                route_id=route_id,
                consumed=True,
            )
        additions = [
            action.payload
            for action in pre_compile.actions
            if action.kind == "prompt_addition" and isinstance(action.payload.get("content"), str)
        ]
        history_exclude_through = max(
            (
                int(action.payload.get("exclude_through_position", -1))
                for action in pre_compile.actions
                if action.kind == "history_filter"
                and isinstance(action.payload.get("exclude_through_position"), int)
            ),
            default=-1,
        )
        if temporary_prompt:
            additions.append({"role": "system", "content": temporary_prompt})
        invocation, result = await self._complete_with_failover(
            conversation_id,
            input_text,
            additions,
            list(model_images or []),
            history_exclude_through=history_exclude_through,
            provider_failure_notifier=provider_failure_notifier,
        )
        transform = await self.plugin_manager.dispatch(
            "transform_model_response",
            PluginEvent(
                name="transform_model_response",
                conversation_id=route_id,
                text=invocation.input_text,
                response_text=result.text,
                metadata={
                    "source": source,
                    "model": invocation.connection.model,
                    "record_id": conversation_id,
                    "trigger_message_id": trigger_message_id,
                    "turn_id": turn_id,
                    "prompt_additions": [
                        {
                            "role": str(item.get("role", "system")),
                            "content": str(item.get("content", "")),
                        }
                        for item in additions
                        if isinstance(item.get("content"), str)
                    ],
                    "prompt_hook_metadata": pre_compile.metadata,
                },
            ),
        )
        transformed_text = result.text
        for action in transform.actions:
            replacement = action.payload.get("text")
            if action.kind == "replace_model_response" and isinstance(replacement, str) and replacement.strip():
                transformed_text = replacement
        try:
            ensure_history_content_safe(transformed_text)
        except MediaValidationError as exc:
            raise ChatRuntimeError("转换后的模型回复包含不能写入历史的内联图片数据") from exc
        parsed_response = parse_model_response(transformed_text)
        response_split = await self.plugin_manager.dispatch(
            "before_response_split",
            PluginEvent(
                name="before_response_split",
                conversation_id=route_id,
                text=invocation.input_text,
                response_text=parsed_response.text,
                metadata={
                    "source": source,
                    "model": invocation.connection.model,
                    "record_id": conversation_id,
                    "character_id": invocation.character_id,
                    "delimiter": "|||",
                    "trigger_message_id": trigger_message_id,
                    "turn_id": turn_id,
                },
            ),
        )

        after_model = await self.plugin_manager.dispatch(
            "after_model_response",
            PluginEvent(
                name="after_model_response",
                conversation_id=route_id,
                text=invocation.input_text,
                response_text=parsed_response.text,
                metadata={
                    "source": source,
                    "model": invocation.connection.model,
                    "record_id": conversation_id,
                    "trigger_message_id": trigger_message_id,
                    "turn_id": turn_id,
                    "sticker_category": parsed_response.sticker_category,
                    "response_split_metadata": response_split.metadata,
                    "prompt_hook_metadata": pre_compile.metadata,
                },
            ),
        )
        response_actions = list(after_model.actions)
        if parsed_response.sticker_category:
            response_actions.insert(
                0,
                PluginAction(
                    kind="replace_response",
                    payload={
                        "text_segments": parsed_response.text_segments,
                        "sticker_category": parsed_response.sticker_category,
                    },
                ),
            )
        final_text, outbound = self._apply_response_actions(
            route_id,
            parsed_response.text,
            response_actions,
            character_id=invocation.character_id,
        )
        history_text = final_text
        if not history_text:
            sticker = parsed_response.sticker_category
            history_text = f"[表情: {sticker}]" if sticker else "（空回复）"
        ensure_history_content_safe(history_text)
        metadata: dict[str, Any] = {
            "finish_reason": result.finish_reason,
            "response_id": result.response_id,
            "token_usage_estimated": bool(result.raw_metadata.get("token_usage_estimated")),
            "trigger_message_id": trigger_message_id,
            "turn_id": turn_id,
            "character_id": invocation.character_id,
            "character_name": invocation.character_name,
            "user_persona_id": invocation.user_persona_id,
            "user_persona_name": invocation.user_persona_name,
        }
        if parsed_response.sticker_category:
            metadata["sticker_category"] = parsed_response.sticker_category
        if invocation.show_thoughts and result.reasoning:
            ensure_history_content_safe(result.reasoning)
            metadata["reasoning"] = result.reasoning
        if trigger_message_id:
            self._stamp_message_identity(trigger_message_id, invocation)
        assistant = self._append_message(
            conversation_id,
            "assistant",
            history_text,
            source=source,
            provider_id=invocation.provider_id,
            preset_id=invocation.preset_id,
            model=invocation.connection.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            message_metadata=metadata,
        )
        if turn_id:
            self.record_turn_assistant_message(turn_id, assistant.id)
        for action in outbound:
            await self.action_executor.submit(source_plugin_id, action, turn_id=turn_id)
        return RuntimeReply(
            conversation_id=conversation_id,
            route_id=route_id,
            consumed=False,
            text=final_text,
            message_id=assistant.id,
            model=invocation.connection.model,
            finish_reason=result.finish_reason,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            outbound_actions=outbound,
        )

    def _build_invocation(
        self,
        conversation_id: str,
        input_text: str,
        additions: list[dict[str, Any]],
        model_images: list[NormalizedImage] | None = None,
        provider_id: str | None = None,
        history_exclude_through: int = -1,
    ) -> Invocation:
        with self.database.session_factory() as session:
            preset = session.scalar(
                select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
            )
            if preset is None:
                raise RuntimeConfigurationError("没有生效的组合预设")
            selected_provider_id = provider_id or preset.provider_id
            provider = session.get(Provider, selected_provider_id) if selected_provider_id else None
            template = session.get(PromptTemplate, preset.prompt_template_id) if preset.prompt_template_id else None
            character = session.get(Character, preset.character_id) if preset.character_id else None
            user_persona = session.get(UserPersona, preset.user_persona_id) if preset.user_persona_id else None
            if provider is None or not provider.enabled:
                raise RuntimeConfigurationError("当前预设没有可用的 API 供应商")
            if template is None:
                raise RuntimeConfigurationError("当前预设没有提示词模板")

            all_history_rows = session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.status == "complete",
                    ChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(ChatMessage.position)
            ).all()
            history_rows = [
                item for item in all_history_rows if item.position > history_exclude_through
            ]
            history = [{"role": item.role, "content": item.content} for item in history_rows]
            last_user = next((item.content for item in reversed(all_history_rows) if item.role == "user"), "")
            last_char = next((item.content for item in reversed(all_history_rows) if item.role == "assistant"), "")
            current_input = input_text or last_user
            world_entries: list[WorldBookEntry] = []
            selected_book_ids: set[str] = set()
            for link in preset.world_book_links:
                if not link.enabled:
                    continue
                world_book = session.get(WorldBook, link.world_book_id)
                if world_book is not None:
                    selected_book_ids.add(world_book.id)
                    world_entries.extend(world_book.entries)
            for world_book in session.scalars(
                select(WorldBook).order_by(WorldBook.created_at)
            ).unique():
                scoped = world_book.scope == "global" or (
                    world_book.scope == "character"
                    and character is not None
                    and world_book.character_id == character.id
                )
                if scoped and world_book.id not in selected_book_ids:
                    selected_book_ids.add(world_book.id)
                    world_entries.extend(world_book.entries)
            marker_values = {
                "charDescription": character.summary if character else "",
                "charPersonality": character.persona if character else "",
                "scenario": character.scenario if character else "",
                "dialogueExamples": character.first_message if character else "",
                "personaDescription": user_persona.description if user_persona else "",
            }
            last_message = history_rows[-1].content if history_rows else current_input
            macro_context = MacroContext(
                user_name=user_persona.name if user_persona else "用户",
                user_persona=user_persona.description if user_persona else "",
                character_name=character.name if character else "当前角色",
                character_description=character.summary if character else "",
                character_personality=character.persona if character else "",
                character_scenario=character.scenario if character else "",
                character_first_message=character.first_message if character else "",
                model=provider.model,
                input_text=current_input,
                original=current_input,
                last_message=last_message,
                last_user_message=last_user or current_input,
                last_char_message=last_char,
                last_message_id=history_rows[-1].id if history_rows else "0",
                first_included_message_id=history_rows[0].id if history_rows else "0",
                first_displayed_message_id=history_rows[0].id if history_rows else "0",
                message_count=len(history_rows),
                max_context_tokens=preset.context_length,
                max_response_tokens=preset.max_response_tokens,
                max_prompt_tokens=preset.context_length - preset.max_response_tokens,
                last_generation_type="plugin" if not input_text else "normal",
                now=datetime.now(timezone.utc),
                random_seed=f"{conversation_id}:{len(history_rows)}",
            )
            scan_text = "\n".join(item.content for item in history_rows[-20:])
            compiled = compile_prompt_messages(
                template.blocks,
                history,
                marker_values,
                world_entries,
                scan_text,
                macro_context=macro_context,
                user_persona=user_persona,
            )
            compiled = self._insert_additions(compiled, additions, macro_context)
            if preset.squash_system_messages:
                compiled = self._squash_system_messages(compiled)
            request_images = list(model_images or []) if preset.media_inlining else []
            api_messages = self._trim_to_budget(
                compiled,
                preset.context_length
                - preset.max_response_tokens
                - len(request_images) * IMAGE_TOKEN_ESTIMATE,
                provider.model,
            )
            if request_images:
                latest_history_content = history_rows[-1].content if history_rows else ""
                target_index = next(
                    (
                        index
                        for index in range(len(api_messages) - 1, -1, -1)
                        if api_messages[index]["role"] == "user"
                        and api_messages[index]["content"] == latest_history_content
                    ),
                    -1,
                )
                if target_index < 0:
                    raise ChatRuntimeError("无法定位当前图片消息在 Prompt 中的位置")
                content = build_multimodal_user_content(current_input, request_images)
                if isinstance(content, list) and preset.image_quality != "auto":
                    for part in content:
                        if part.get("type") == "image_url":
                            part["image_url"]["detail"] = preset.image_quality
                api_messages[target_index]["content"] = content
            connection = self._provider_connection(provider)
            request = ChatCompletionRequest(
                messages=api_messages,
                max_tokens=preset.max_response_tokens,
                temperature=preset.temperature,
                frequency_penalty=preset.frequency_penalty,
                presence_penalty=preset.presence_penalty,
                top_p=preset.top_p,
                candidate_count=preset.candidate_count,
                stream=preset.streaming,
                reasoning_effort=preset.reasoning_effort,
                user_name=user_persona.name if user_persona else "用户",
                character_name=character.name if character else "当前角色",
            )
            return Invocation(
                connection=connection,
                request=request,
                provider_id=provider.id,
                preset_id=preset.id,
                character_id=character.id if character else None,
                character_name=character.name if character else "角色",
                user_persona_id=user_persona.id if user_persona else None,
                user_persona_name=user_persona.name if user_persona else "用户",
                input_text=current_input,
                show_thoughts=preset.show_thoughts,
            )

    def _provider_failover_targets(self) -> list[ProviderTarget]:
        with self.database.session_factory() as session:
            preset = session.scalar(
                select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
            )
            if preset is None:
                raise RuntimeConfigurationError("没有生效的组合预设")
            providers = session.scalars(
                select(Provider)
                .where(Provider.enabled.is_(True))
                .order_by(Provider.priority, Provider.created_at)
            ).all()
            preferred = next(
                (provider for provider in providers if provider.id == preset.provider_id),
                None,
            )
            ordered = ([preferred] if preferred is not None else []) + [
                provider for provider in providers if provider.id != preset.provider_id
            ]
            if not ordered:
                raise RuntimeConfigurationError("当前预设没有可用的 API 供应商")
            return [ProviderTarget(id=provider.id, name=provider.name) for provider in ordered]

    def _provider_connection(self, provider: Provider) -> ProviderConnection:
        return ProviderConnection(
            base_url=provider.base_url,
            model=provider.model,
            api_key=self.secret_box.decrypt(provider.api_key_encrypted),
            kind=provider.kind,
            chat_completion_source=provider.chat_completion_source,
            prompt_post_processing=provider.prompt_post_processing,
        )

    async def _complete_with_failover(
        self,
        conversation_id: str,
        input_text: str,
        additions: list[dict[str, Any]],
        model_images: list[NormalizedImage],
        history_exclude_through: int = -1,
        provider_failure_notifier: ProviderFailureNotifier | None = None,
    ) -> tuple[Invocation, ChatCompletionResult]:
        provider_targets = self._provider_failover_targets()
        last_error: ModelClientError | None = None
        for attempt, target in enumerate(provider_targets, 1):
            invocation = self._build_invocation(
                conversation_id,
                input_text,
                additions,
                model_images,
                provider_id=target.id,
                history_exclude_through=history_exclude_through,
            )
            try:
                result = await self.model_client.complete(invocation.connection, invocation.request)
                if not result.text.strip():
                    raise ModelProtocolError("模型返回了空回复")
                try:
                    ensure_history_content_safe(result.text)
                except MediaValidationError as exc:
                    raise ModelProtocolError(
                        "模型回复包含不能写入历史的内联图片数据"
                    ) from exc
                return invocation, result
            except ModelClientError as exc:
                last_error = exc
                self._log_failover(
                    target.id,
                    invocation.connection.model,
                    attempt,
                    len(provider_targets),
                    exc,
                )
                if provider_failure_notifier is not None:
                    next_name = (
                        provider_targets[attempt].name
                        if attempt < len(provider_targets)
                        else None
                    )
                    try:
                        await provider_failure_notifier(target.name, next_name, exc)
                    except Exception as notice_error:
                        LOGGER.warning(
                            "发送 API 故障转移通知失败 | provider=%s | %s: %s",
                            target.id,
                            type(notice_error).__name__,
                            notice_error,
                        )
        if last_error is not None:
            raise last_error
        raise RuntimeConfigurationError("当前预设没有可用的 API 供应商")

    @staticmethod
    def _log_failover(
        provider_id: str,
        model: str,
        attempt: int,
        total: int,
        error: ModelClientError,
    ) -> None:
        LOGGER.warning(
            "API 调用失败，%s | provider=%s | model=%s | attempt=%s/%s | %s: %s",
            "准备尝试下一个供应商" if attempt < total else "所有供应商均已失败",
            provider_id,
            model,
            attempt,
            total,
            type(error).__name__,
            error,
        )

    @staticmethod
    def _insert_additions(
        compiled: list[CompiledMessage],
        additions: list[dict[str, Any]],
        macro_context: MacroContext | None = None,
    ) -> list[CompiledMessage]:
        macro_context = macro_context or MacroContext()
        result = list(compiled)
        legacy_additions: list[tuple[str, str, str]] = []
        for addition in additions:
            content = render_macros(
                str(addition.get("content", "")),
                macro_context,
            ).content
            content = ensure_history_content_safe(content)
            if not content:
                continue
            role = str(addition.get("role", "system"))
            if role not in {"system", "user", "assistant"}:
                role = "system"
            source = str(
                addition.get("_compiled_source")
                or addition.get("source")
                or ""
            ).strip()
            legacy_additions.append((role, content, source))

        # Plugin additions are inserted immediately before the latest user message.
        insert_at = len(result)
        for index in range(len(result) - 1, -1, -1):
            if result[index].source == "chatHistory" and result[index].role == "user":
                insert_at = index
                break
        for offset, (role, content, source) in enumerate(legacy_additions):
            compiled_source = f"pluginAddition:{source}" if source else "pluginAddition"
            result.insert(insert_at + offset, CompiledMessage(role, content, compiled_source))

        return result

    @staticmethod
    def _squash_system_messages(compiled: list[CompiledMessage]) -> list[CompiledMessage]:
        result: list[CompiledMessage] = []
        for message in compiled:
            if result and result[-1].role == "system" and message.role == "system":
                previous = result[-1]
                result[-1] = CompiledMessage(
                    "system",
                    f"{previous.content}\n\n{message.content}",
                    f"{previous.source}+{message.source}",
                )
            else:
                result.append(message)
        return result

    @staticmethod
    def _trim_to_budget(
        compiled: list[CompiledMessage],
        budget: int,
        model: str = "",
    ) -> list[dict[str, str]]:
        if budget <= 0:
            raise PromptBudgetError("最大回复长度不能大于或等于上下文长度")
        working = list(compiled)
        while estimate_message_tokens([item.as_api_message() for item in working], model) > budget:
            history_indexes = [
                index for index, item in enumerate(working) if item.source == "chatHistory"
            ]
            newest_history = max(history_indexes, default=-1)
            removable = [
                index
                for index in history_indexes
                if index != newest_history
            ]
            if not removable:
                raise PromptBudgetError("固定提示词已超过当前上下文预算")
            working.pop(removable[0])
        return [item.as_api_message() for item in working]

    @staticmethod
    def _apply_response_actions(
        conversation_id: str,
        original_text: str,
        actions: list[PluginAction],
        *,
        character_id: str | None = None,
    ) -> tuple[str, list[PluginAction]]:
        final_text = original_text
        text_segments = [original_text] if original_text else []
        image_ref: str | None = None
        segment_reply: dict[str, Any] | None = None
        for action in actions:
            if action.kind != "replace_response":
                continue
            segments = action.payload.get("text_segments")
            if isinstance(segments, list):
                text_segments = [str(item).strip() for item in segments if str(item).strip()]
                final_text = "\n".join(text_segments)
            if isinstance(action.payload.get("asset_ref"), str):
                image_ref = action.payload["asset_ref"]
            value = action.payload.get("segment_reply")
            if isinstance(value, dict):
                segment_reply = value
        if segment_reply is not None:
            text_segments = ChatRuntime._limit_reply_segments(text_segments, segment_reply)
            final_text = "\n".join(text_segments)
        outbound = []
        for index, segment in enumerate(text_segments):
            delay_seconds = (
                ChatRuntime._reply_segment_delay(segment, segment_reply)
                if segment_reply is not None and index > 0
                else 0.0
            )
            for part_index, part in enumerate(ChatRuntime._split_outbound_text(segment)):
                payload: dict[str, Any] = {"conversation_id": conversation_id, "text": part}
                if character_id:
                    payload["character_id"] = character_id
                if delay_seconds and part_index == 0:
                    payload["delay_seconds"] = delay_seconds
                outbound.append(
                    PluginAction(kind="send_text", payload=payload)
                )
        if image_ref:
            outbound.append(
                PluginAction(kind="send_image", payload={"conversation_id": conversation_id, "asset_ref": image_ref})
            )
        return final_text, outbound

    @staticmethod
    def _limit_reply_segments(segments: list[str], settings: dict[str, Any]) -> list[str]:
        maximum = max(1, min(int(settings.get("max_segments", 5)), 20))
        if len(segments) <= maximum:
            return segments
        if maximum == 1:
            return [" ".join(segments)]
        return [*segments[: maximum - 1], " ".join(segments[maximum - 1 :])]

    @staticmethod
    def _reply_segment_delay(text: str, settings: dict[str, Any]) -> float:
        base = max(0.0, min(float(settings.get("base_delay_seconds", 0.8)), 10.0))
        per_unit = max(0.0, min(float(settings.get("seconds_per_text_unit", 0.18)), 1.0))
        maximum = max(base, min(float(settings.get("max_delay_seconds", 8)), 60.0))
        units = 0.0
        for character in text:
            category = unicodedata.category(character)
            if category.startswith(("P", "S")):
                continue
            if (
                "\u4e00" <= character <= "\u9fff"
                or "\u3040" <= character <= "\u30ff"
                or "\u31f0" <= character <= "\u31ff"
                or "\uac00" <= character <= "\ud7af"
            ):
                units += 1
            else:
                units += 0.5
        return min(maximum, base + units * per_unit)

    @staticmethod
    def _split_outbound_text(text: str, limit: int = 4000) -> list[str]:
        text = text.strip()
        if not text:
            return []
        parts: list[str] = []
        remaining = text
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at < limit // 2:
                split_at = limit
            parts.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            parts.append(remaining)
        return parts

    def begin_qq_turn(
        self,
        conversation_id: str,
        trigger_message_id: str,
        trigger_user_id: str,
        channel: str,
        trigger_message_ids: list[str] | None = None,
    ) -> ConversationTurn:
        record = self._ensure_conversation(conversation_id, channel)
        normalized_trigger_ids = [
            str(message_id)[:120]
            for message_id in (trigger_message_ids or [trigger_message_id])
            if str(message_id).strip()
        ]
        if str(trigger_message_id)[:120] not in normalized_trigger_ids:
            normalized_trigger_ids.insert(0, str(trigger_message_id)[:120])
        with self.database.session_factory() as session:
            turn = ConversationTurn(
                conversation_id=record.id,
                route_id=conversation_id,
                trigger_message_id=str(trigger_message_id)[:120],
                trigger_message_ids=normalized_trigger_ids,
                trigger_user_id=str(trigger_user_id)[:80],
            )
            session.add(turn)
            session.commit()
            session.refresh(turn)
            session.expunge(turn)
            return turn

    def capture_turn_plugin_state(self, turn_id: str, conversation_id: str) -> None:
        captured = False
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is None or turn.status != "active" or turn.plugin_state_snapshot_ready:
                return
            turn.conversation_id = conversation_id
            turn.plugin_state_snapshot = [
                {
                    "plugin_id": row.plugin_id,
                    "state": deepcopy(row.state or {}),
                }
                for row in session.scalars(
                    select(PluginConversationState).where(
                        PluginConversationState.conversation_id == conversation_id
                    )
                ).all()
            ]
            turn.plugin_state_snapshot_ready = True
            session.commit()
            captured = True
        if captured:
            self.plugin_manager.capture_external_conversation_state(conversation_id, turn_id)

    def find_recall_turn(
        self,
        conversation_id: str,
        trigger_message_id: str,
        trigger_user_id: str,
    ) -> ConversationTurn | None:
        with self.database.session_factory() as session:
            turns = session.scalars(
                select(ConversationTurn)
                .where(
                    ConversationTurn.route_id == conversation_id,
                    ConversationTurn.trigger_user_id == str(trigger_user_id),
                    ConversationTurn.status.in_(("active", "completed")),
                )
                .order_by(ConversationTurn.created_at.desc())
            ).all()
            expected = str(trigger_message_id)
            turn = next(
                (
                    item
                    for item in turns
                    if item.trigger_message_id == expected
                    or expected in [str(value) for value in (item.trigger_message_ids or [])]
                ),
                None,
            )
            if turn is not None:
                session.expunge(turn)
            return turn

    def prepare_turn_recall(self, turn_id: str) -> ConversationTurn | None:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is None or turn.status not in {"active", "completed"}:
                return None
            turn.status = "recalling"
            session.commit()
            session.refresh(turn)
            session.expunge(turn)
        event = self._recall_events.get(turn.route_id)
        if event is None or event.is_set():
            event = asyncio.Event()
            self._recall_events[turn.route_id] = event
        self._recall_turns[turn.route_id] = turn.id
        return turn

    def finish_turn_recall(self, conversation_id: str, turn_id: str) -> None:
        if self._recall_turns.get(conversation_id) != turn_id:
            return
        self._recall_turns.pop(conversation_id, None)
        event = self._recall_events.pop(conversation_id, None)
        if event is not None:
            event.set()

    def record_turn_user_message(self, turn_id: str, message_id: str) -> None:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is not None and turn.status == "active":
                turn.user_message_id = message_id
                session.commit()

    def record_turn_assistant_message(self, turn_id: str, message_id: str) -> None:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is not None and turn.status == "active":
                turn.assistant_message_id = message_id
                session.commit()

    def record_turn_sent_message(self, turn_id: str, message_id: str | int) -> None:
        normalized_id = str(message_id).strip()
        if not normalized_id:
            return
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is None or turn.status == "recalled":
                return
            message_ids = [str(item) for item in (turn.sent_message_ids or [])]
            if normalized_id not in message_ids:
                turn.sent_message_ids = [*message_ids, normalized_id]
                session.commit()

    def mark_turn_completed(self, turn_id: str) -> None:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is not None and turn.status == "active":
                turn.status = "completed"
                session.commit()

    def mark_turn_failed(self, turn_id: str) -> None:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is not None and turn.status == "active":
                turn.status = "failed"
                session.commit()

    async def rollback_turn(self, turn_id: str) -> list[str]:
        with self.database.session_factory() as session:
            turn = session.get(ConversationTurn, turn_id)
            if turn is None:
                return []
            conversation_id = turn.conversation_id
            route_id = turn.route_id

        sent_message_ids: list[str] = []
        async with self._lock(route_id):
            with self.database.session_factory() as session:
                turn = session.get(ConversationTurn, turn_id)
                if turn is None or turn.status != "recalling":
                    return []
                message_ids = [
                    message_id
                    for message_id in (turn.user_message_id, turn.assistant_message_id)
                    if message_id
                ]
                if message_ids:
                    for message in session.scalars(
                        select(ChatMessage).where(ChatMessage.id.in_(message_ids))
                    ).all():
                        session.delete(message)
                if turn.plugin_state_snapshot_ready:
                    current_states = {
                        state.plugin_id: state
                        for state in session.scalars(
                            select(PluginConversationState).where(
                                PluginConversationState.conversation_id == conversation_id
                            )
                        ).all()
                    }
                    snapshots = {
                        str(snapshot.get("plugin_id", "")).strip(): snapshot.get("state")
                        for snapshot in (turn.plugin_state_snapshot or [])
                        if str(snapshot.get("plugin_id", "")).strip()
                        and isinstance(snapshot.get("state"), dict)
                    }
                    for plugin_id, current_state in current_states.items():
                        if plugin_id not in snapshots:
                            session.delete(current_state)
                    for plugin_id, state in snapshots.items():
                        current_state = current_states.get(plugin_id)
                        if current_state is None:
                            session.add(
                                PluginConversationState(
                                    plugin_id=plugin_id,
                                    conversation_id=conversation_id,
                                    state=deepcopy(state),
                                )
                            )
                        else:
                            current_state.state = deepcopy(state)
                conversation = session.get(Conversation, conversation_id)
                if conversation is not None:
                    conversation.updated_at = utcnow()
                sent_message_ids = [str(item) for item in (turn.sent_message_ids or [])]
                turn.status = "recalled"
                turn.recalled_at = utcnow()
                session.commit()
            self.plugin_manager.restore_external_conversation_state(conversation_id, turn_id)
            return sent_message_ids

    def _ensure_conversation(self, conversation_id: str, channel: str) -> Conversation:
        created = False
        with self.database.session_factory() as session:
            conversation = session.scalar(
                select(Conversation)
                .where(
                    Conversation.external_id == conversation_id,
                    Conversation.is_active.is_(True),
                )
                .order_by(Conversation.updated_at.desc())
            )
            if conversation is None:
                existing = session.get(Conversation, conversation_id)
                if existing is not None:
                    conversation = existing
                    conversation.external_id = conversation_id
                    conversation.channel = channel
                    conversation.is_active = True
                else:
                    conversation = Conversation(
                        id=conversation_id,
                        channel=channel,
                        external_id=conversation_id,
                        title="默认记录",
                        is_active=True,
                    )
                    session.add(conversation)
                    created = True
                session.commit()
                session.refresh(conversation)
            session.expunge(conversation)
        if created:
            self.plugin_manager.conversation_created(conversation.id)
        return conversation

    def _append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        status: str = "complete",
        source: str = "runtime",
        provider_id: str | None = None,
        preset_id: str | None = None,
        model: str = "",
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        message_metadata: dict[str, Any] | None = None,
    ) -> ChatMessage:
        safe_content = ensure_history_content_safe(content)
        with self.database.session_factory() as session:
            last_position = session.scalar(
                select(func.max(ChatMessage.position)).where(ChatMessage.conversation_id == conversation_id)
            )
            message = ChatMessage(
                conversation_id=conversation_id,
                position=(last_position if last_position is not None else -1) + 1,
                role=role,
                content=safe_content,
                status=status,
                source=source,
                provider_id=provider_id,
                preset_id=preset_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                message_metadata=message_metadata or {},
            )
            session.add(message)
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.updated_at = utcnow()
            session.commit()
            session.refresh(message)
            session.expunge(message)
            return message

    def _stamp_message_identity(self, message_id: str, invocation: Invocation) -> None:
        with self.database.session_factory() as session:
            message = session.get(ChatMessage, message_id)
            if message is None or message.role != "user":
                return
            message.preset_id = invocation.preset_id
            message.message_metadata = {
                **dict(message.message_metadata or {}),
                "character_id": invocation.character_id,
                "character_name": invocation.character_name,
                "user_persona_id": invocation.user_persona_id,
                "user_persona_name": invocation.user_persona_name,
            }
            session.commit()

    @staticmethod
    def _message_speaker_names(
        session,
        messages: list[ChatMessage],
    ) -> dict[str, str]:
        messages_by_id = {message.id: message for message in messages}
        linked_assistants: dict[str, ChatMessage] = {}
        for message in messages:
            if message.role != "assistant":
                continue
            metadata = dict(message.message_metadata or {})
            trigger_id = str(metadata.get("trigger_message_id") or "").strip()
            if trigger_id and trigger_id in messages_by_id:
                linked_assistants[trigger_id] = message
        conversation_ids = {message.conversation_id for message in messages}
        if conversation_ids:
            for turn in session.scalars(
                select(ConversationTurn).where(
                    ConversationTurn.conversation_id.in_(conversation_ids)
                )
            ).all():
                assistant = messages_by_id.get(str(turn.assistant_message_id or ""))
                if turn.user_message_id and assistant is not None:
                    linked_assistants[turn.user_message_id] = assistant

        preset_names: dict[str, tuple[str | None, str | None]] = {}

        def names_for_preset(preset_id: str | None) -> tuple[str | None, str | None]:
            normalized_id = str(preset_id or "").strip()
            if not normalized_id:
                return None, None
            if normalized_id not in preset_names:
                preset = session.get(ConfigurationPreset, normalized_id)
                character = (
                    session.get(Character, preset.character_id)
                    if preset is not None and preset.character_id
                    else None
                )
                user_persona = (
                    session.get(UserPersona, preset.user_persona_id)
                    if preset is not None and preset.user_persona_id
                    else None
                )
                preset_names[normalized_id] = (
                    character.name if character else None,
                    user_persona.name if user_persona else None,
                )
            return preset_names[normalized_id]

        output: dict[str, str] = {}
        for message in messages:
            if message.role not in {"user", "assistant"}:
                output[message.id] = message.role
                continue
            source = message
            if message.role == "user" and not message.preset_id:
                source = linked_assistants.get(message.id, message)
            metadata = dict(source.message_metadata or {})
            character_name, user_persona_name = names_for_preset(source.preset_id)
            if message.role == "user":
                output[message.id] = (
                    str(metadata.get("user_persona_name") or "").strip()
                    or user_persona_name
                    or "用户"
                )
            else:
                output[message.id] = (
                    str(metadata.get("character_name") or "").strip()
                    or character_name
                    or "角色"
                )
        return output

    def list_conversations(self) -> list[dict[str, Any]]:
        with self.database.session_factory() as session:
            items = session.scalars(select(Conversation).order_by(Conversation.updated_at.desc())).all()
            active_preset = session.scalar(
                select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
            )
            active_provider = (
                session.get(Provider, active_preset.provider_id)
                if active_preset and active_preset.provider_id
                else None
            )
            model = active_provider.model if active_provider else ""
            output = []
            for item in items:
                messages = list(
                    session.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.conversation_id == item.id)
                        .order_by(ChatMessage.position)
                    ).all()
                )
                speaker_names = self._message_speaker_names(session, messages)
                latest_assistant = next(
                    (message for message in reversed(messages) if message.role == "assistant"),
                    None,
                )
                total_tokens = sum(
                    count_text_tokens(message.content, model)
                    for message in messages
                )
                last_message = messages[-1] if messages else None
                output.append(
                    {
                        "id": item.id,
                        "channel": item.channel,
                        "external_id": item.external_id,
                        "title": item.title,
                        "is_active": item.is_active,
                        "message_count": len(messages),
                        "total_tokens": total_tokens,
                        "character_name": (
                            speaker_names.get(latest_assistant.id)
                            if latest_assistant is not None
                            else None
                        ),
                        "last_message_preview": last_message.content[:160] if last_message else "",
                        "created_at": item.created_at,
                        "updated_at": item.updated_at,
                    }
                )
            return output

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        with self.database.session_factory() as session:
            active_preset = session.scalar(
                select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
            )
            provider = (
                session.get(Provider, active_preset.provider_id)
                if active_preset and active_preset.provider_id
                else None
            )
            model = provider.model if provider else ""
            items = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.position)
            ).all()
            speaker_names = self._message_speaker_names(session, list(items))
            for item in items:
                item.token_count = count_text_tokens(item.content, model)
                item.speaker_name = speaker_names.get(item.id, item.role)
                session.expunge(item)
            return list(items)

    async def update_conversation_message(
        self,
        conversation_id: str,
        message_id: str,
        content: str,
    ) -> ChatMessage:
        safe_content = ensure_history_content_safe(content)
        if not safe_content.strip():
            raise ChatRuntimeError("聊天消息内容不能为空")
        with self.database.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ChatRuntimeError("聊天记录不存在")
            route_id = conversation.external_id

        async with self._lock(route_id):
            with self.database.session_factory() as session:
                conversation = session.get(Conversation, conversation_id)
                message = session.get(ChatMessage, message_id)
                if conversation is None:
                    raise ChatRuntimeError("聊天记录不存在")
                if message is None or message.conversation_id != conversation_id:
                    raise ChatRuntimeError("聊天消息不存在或不属于当前记录")
                if message.content != safe_content:
                    message.content = safe_content
                    message.message_metadata = {
                        **dict(message.message_metadata or {}),
                        "manually_edited": True,
                        "manually_edited_at": utcnow().isoformat(),
                    }
                    for turn in session.scalars(
                        select(ConversationTurn).where(
                            ConversationTurn.conversation_id == conversation_id
                        )
                    ).all():
                        if message.id not in {turn.user_message_id, turn.assistant_message_id}:
                            continue
                        if turn.status not in {"recalled", "recalling"}:
                            turn.status = "edited"
                    conversation.updated_at = utcnow()
                    session.commit()

        return next(
            message
            for message in self.list_messages(conversation_id)
            if message.id == message_id
        )

    async def delete_conversation_messages(
        self,
        conversation_id: str,
        message_ids: list[str],
    ) -> dict[str, int]:
        normalized_ids = list(
            dict.fromkeys(
                str(message_id).strip()
                for message_id in message_ids
                if str(message_id).strip()
            )
        )
        if not normalized_ids:
            raise ChatRuntimeError("至少选择一条聊天消息")
        if len(normalized_ids) > 1000 or any(len(message_id) > 36 for message_id in normalized_ids):
            raise ChatRuntimeError("选择的聊天消息无效")

        with self.database.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ChatRuntimeError("聊天记录不存在")
            route_id = conversation.external_id

        async with self._lock(route_id):
            with self.database.session_factory() as session:
                conversation = session.get(Conversation, conversation_id)
                if conversation is None:
                    raise ChatRuntimeError("聊天记录不存在")
                messages = list(
                    session.scalars(
                        select(ChatMessage).where(ChatMessage.id.in_(normalized_ids))
                    ).all()
                )
                if len(messages) != len(normalized_ids) or any(
                    message.conversation_id != conversation_id for message in messages
                ):
                    raise ChatRuntimeError("所选消息不存在或不属于当前聊天记录")

                selected_ids = set(normalized_ids)
                for turn in session.scalars(
                    select(ConversationTurn).where(
                        ConversationTurn.conversation_id == conversation_id
                    )
                ).all():
                    affected = (
                        turn.user_message_id in selected_ids
                        or turn.assistant_message_id in selected_ids
                    )
                    if not affected:
                        continue
                    if turn.user_message_id in selected_ids:
                        turn.user_message_id = None
                    if turn.assistant_message_id in selected_ids:
                        turn.assistant_message_id = None
                    if turn.status not in {"recalled", "recalling"}:
                        turn.status = "edited"

                for message in messages:
                    session.delete(message)
                conversation.updated_at = utcnow()
                remaining_count = session.scalar(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation_id,
                        ChatMessage.id.not_in(selected_ids),
                    )
                ) or 0
                session.commit()
                return {
                    "deleted_count": len(messages),
                    "remaining_count": remaining_count,
                }

    def list_actions(self, limit: int = 100) -> list[RuntimeAction]:
        with self.database.session_factory() as session:
            items = session.scalars(
                select(RuntimeAction).order_by(RuntimeAction.created_at.desc()).limit(limit)
            ).all()
            for item in items:
                session.expunge(item)
            return list(items)

    def create_conversation_record(self, route_id: str, title: str = "") -> Conversation:
        route_id = route_id.strip()
        if not route_id:
            raise ChatRuntimeError("聊天路由不能为空")
        with self.database.session_factory() as session:
            base = session.scalar(
                select(Conversation)
                .where(Conversation.external_id == route_id)
                .order_by(Conversation.is_active.desc(), Conversation.created_at)
            )
            conversation = Conversation(
                id=new_id(),
                channel=base.channel if base else "manual",
                external_id=route_id,
                title=title.strip() or f"新记录 {datetime.now().strftime('%m-%d %H:%M')}",
                is_active=base is None,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            session.expunge(conversation)
        self.plugin_manager.conversation_created(conversation.id)
        return conversation

    def rename_conversation_record(self, conversation_id: str, title: str) -> Conversation:
        with self.database.session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ChatRuntimeError("聊天记录不存在")
            previous_title = conversation.title
            conversation.title = title.strip()
            session.commit()
            session.refresh(conversation)
            session.expunge(conversation)
        self.plugin_manager.conversation_renamed(conversation_id, previous_title)
        return conversation

    async def activate_conversation_record(self, conversation_id: str) -> Conversation:
        with self.database.session_factory() as session:
            current = session.get(Conversation, conversation_id)
            if current is None:
                raise ChatRuntimeError("聊天记录不存在")
            route_id = current.external_id
        async with self._lock(route_id):
            with self.database.session_factory() as session:
                conversation = session.get(Conversation, conversation_id)
                if conversation is None:
                    raise ChatRuntimeError("聊天记录不存在")
                session.execute(
                    update(Conversation)
                    .where(Conversation.external_id == conversation.external_id)
                    .values(is_active=False)
                )
                conversation.is_active = True
                conversation.updated_at = utcnow()
                session.commit()
                session.refresh(conversation)
                session.expunge(conversation)
                return conversation

    async def delete_conversation_record(self, conversation_id: str) -> None:
        with self.database.session_factory() as session:
            current = session.get(Conversation, conversation_id)
            if current is None:
                raise ChatRuntimeError("聊天记录不存在")
            route_id = current.external_id
        async with self._lock(route_id):
            with self.database.session_factory() as session:
                conversation = session.get(Conversation, conversation_id)
                if conversation is None:
                    raise ChatRuntimeError("聊天记录不存在")
                siblings = session.scalars(
                    select(Conversation)
                    .where(Conversation.external_id == conversation.external_id)
                    .order_by(Conversation.created_at)
                ).all()
                if len(siblings) <= 1:
                    message_count = session.scalar(
                        select(func.count())
                        .select_from(ChatMessage)
                        .where(ChatMessage.conversation_id == conversation.id)
                    ) or 0
                    if message_count:
                        raise ChatRuntimeError("有消息的 QQ 路由至少保留一份聊天记录")
                    session.delete(conversation)
                    session.commit()
                else:
                    if conversation.is_active:
                        replacement = next(item for item in siblings if item.id != conversation.id)
                        replacement.is_active = True
                    session.delete(conversation)
                    session.commit()
            self.plugin_manager.conversation_deleted(conversation_id)
