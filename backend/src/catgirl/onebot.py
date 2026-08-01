from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve as websocket_serve
from websockets.exceptions import ConnectionClosed

from .action_executor import ActionExecutor
from .chat_runtime import ChatRuntime
from .database import ConfigurationPreset, ConversationTurn, Database, OneBotConfig, OneBotEvent, RuntimeAction
from .media import ensure_history_content_safe, parse_cq_message, parse_onebot_at_targets
from .media_runtime import MediaReceiver
from .model_client import ModelClientError, ModelHTTPError
from .plugins.types import PluginAction, PluginEvent
from .security import SecretBox


CONVERSATION_PATTERN = re.compile(
    r"^qq:(?:(?P<self_id>\d+):)?(?P<message_type>private|group):(?P<target_id>\d+)$"
)
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_OUTBOUND_IMAGE_BYTES = 32 * 1024 * 1024
API_ERROR_RECALL_SECONDS = 60.0
RECALL_POLL_INTERVAL_SECONDS = 2.0
RECALL_POLL_WINDOW_SECONDS = 120.0
EARLY_RECALL_TTL_SECONDS = 600.0
TEST_INPUTS_REJECTED_PATTERN = re.compile(
    r"<!--\s*Test Inputs Were Rejected\s*-->",
    re.IGNORECASE,
)
LOGGER = logging.getLogger("catgirl.onebot")


class OneBotError(RuntimeError):
    pass


class OneBotUnavailable(OneBotError):
    pass


@dataclass
class OneBotConnection:
    key: str
    websocket: Any
    self_id: str
    mode: str = "reverse"
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_event_at: datetime | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class InboundMessage:
    connection: OneBotConnection
    payload: dict[str, Any]
    event_key: str
    conversation_id: str
    user_id: str
    message_type: str
    text: str
    image_urls: list[str]
    trigger_message_id: str | None
    queued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PendingMessageBatch:
    messages: list[InboundMessage] = field(default_factory=list)
    flush_task: asyncio.Task | None = None


@dataclass(frozen=True)
class EarlyRecall:
    event_key: str
    expires_at: float


class OneBotGateway:
    def __init__(
        self,
        database: Database,
        secret_box: SecretBox,
        chat_runtime: ChatRuntime,
        action_executor: ActionExecutor,
        media_receiver: MediaReceiver,
        allowed_media_roots: list[Path],
    ):
        self.database = database
        self.secret_box = secret_box
        self.chat_runtime = chat_runtime
        self.action_executor = action_executor
        self.media_receiver = media_receiver
        self.allowed_media_roots = [path.resolve() for path in allowed_media_roots]
        self.connections: dict[str, OneBotConnection] = {}
        self.pending_calls: dict[str, tuple[asyncio.Future, str]] = {}
        self.event_tasks: set[asyncio.Task] = set()
        self.turn_tasks: dict[str, asyncio.Task] = {}
        self.pending_message_batches: dict[tuple[str, str], PendingMessageBatch] = {}
        self.early_recalls: dict[tuple[str, str, str], EarlyRecall] = {}
        self.forward_task: asyncio.Task | None = None
        self.reverse_server: Any | None = None
        self.reverse_server_url = ""
        self.forward_error = ""

    def _config(self) -> OneBotConfig:
        with self.database.session_factory() as session:
            config = session.get(OneBotConfig, 1)
            if config is None:
                config = OneBotConfig(id=1)
                session.add(config)
                session.commit()
                session.refresh(config)
            session.expunge(config)
            return config

    async def websocket_endpoint(self, websocket: WebSocket) -> None:
        config = self._config()
        if (
            not config.enabled
            or config.connection_mode != "reverse"
            or not self._authorized(websocket, config)
        ):
            await websocket.close(code=1008, reason="OneBot 接入未启用或令牌无效")
            return
        await websocket.accept()
        connection = OneBotConnection(
            key=str(uuid4()),
            websocket=websocket,
            self_id=str(websocket.headers.get("x-self-id") or websocket.query_params.get("self_id") or ""),
            mode="reverse",
        )
        await self._register_connection(connection)
        try:
            while True:
                payload = await websocket.receive_json()
                await self._consume_payload(connection, payload)
        except WebSocketDisconnect:
            pass
        finally:
            self._remove_connection(connection.key)

    def _authorized(self, websocket: WebSocket, config: OneBotConfig) -> bool:
        expected = self.secret_box.decrypt(config.access_token_encrypted)
        if not expected:
            return True
        authorization = websocket.headers.get("authorization", "")
        provided = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if not provided:
            provided = websocket.query_params.get("access_token", "")
        return hmac.compare_digest(provided, expected)

    async def startup(self) -> None:
        await self.apply_config_change()

    async def apply_config_change(self, reconnect: bool = False) -> None:
        config = self._config()
        if config.enabled and config.connection_mode == "forward":
            if not config.forward_ws_url:
                self.forward_error = "未填写正向 WebSocket 地址"
                await self._close_connections("reverse", 1008, "OneBot 配置已变更")
                await self._stop_reverse_server()
                await self._stop_forward_client()
                return
            if self.forward_task is not None and not self.forward_task.done() and not reconnect:
                return
            await self._close_connections("reverse", 1008, "OneBot 连接模式已变更")
            await self._stop_reverse_server()
            await self._stop_forward_client()
            self.forward_error = ""
            self.forward_task = asyncio.create_task(
                self._run_forward_client(),
                name="onebot-forward-websocket",
            )
            return
        await self._stop_forward_client()
        if not config.enabled:
            await self._stop_reverse_server()
            await self._close_connections("reverse", 1008, "OneBot 配置已变更")
            return
        if reconnect:
            await self._close_connections("reverse", 1008, "OneBot 配置已变更")
            await self._stop_reverse_server()
        await self._start_reverse_server(config)

    async def shutdown(self) -> None:
        await self._stop_forward_client()
        await self._close_connections(None, 1001, "服务关闭")
        await self._stop_reverse_server()
        if self.event_tasks:
            for task in self.event_tasks:
                task.cancel()
            await asyncio.gather(*self.event_tasks, return_exceptions=True)
        for batch in self.pending_message_batches.values():
            for message in batch.messages:
                self._finish_event(message.event_key, "cancelled")
        self.pending_message_batches.clear()
        for recall in self.early_recalls.values():
            self._finish_event(recall.event_key, "cancelled")
        self.early_recalls.clear()
        self.connections.clear()
        self.turn_tasks.clear()
        self.action_executor.outbound_sender = None

    async def _start_reverse_server(self, config: OneBotConfig) -> None:
        raw_url = config.reverse_ws_url.strip()
        if not raw_url:
            await self._stop_reverse_server()
            self.forward_error = ""
            return
        parsed = urlsplit(raw_url)
        path = parsed.path or "/"
        if path.rstrip("/") == "/onebot/v11/ws":
            await self._stop_reverse_server()
            self.forward_error = ""
            return
        if parsed.scheme != "ws":
            await self._stop_reverse_server()
            self.forward_error = "独立反向 WebSocket 监听只支持 ws://；wss:// 需要 TLS 反向代理"
            return
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        if self.reverse_server is not None and self.reverse_server_url == raw_url:
            return
        await self._stop_reverse_server()
        try:
            self.reverse_server = await websocket_serve(
                self._standalone_reverse_endpoint,
                host,
                port,
                ping_interval=20,
                ping_timeout=20,
            )
        except OSError as exc:
            self.forward_error = f"反向 WebSocket 监听失败：{exc}"[:500]
            LOGGER.error("%s | url=%s", self.forward_error, raw_url)
            return
        self.reverse_server_url = raw_url
        self.forward_error = ""
        LOGGER.info("OneBot 反向 WebSocket 开始监听 | url=%s", raw_url)

    async def _stop_reverse_server(self) -> None:
        server = self.reverse_server
        self.reverse_server = None
        self.reverse_server_url = ""
        await self._close_connections("standalone_reverse", 1001, "监听配置已变更")
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _standalone_reverse_endpoint(self, websocket: Any) -> None:
        config = self._config()
        request = websocket.request
        requested = urlsplit(request.path)
        configured = urlsplit(config.reverse_ws_url)
        if (
            not config.enabled
            or config.connection_mode != "reverse"
            or requested.path.rstrip("/") != (configured.path or "/").rstrip("/")
            or not self._standalone_authorized(request.headers, requested.query, config)
        ):
            await websocket.close(code=1008, reason="OneBot 接入未启用、路径错误或令牌无效")
            return
        query = parse_qs(requested.query)
        connection = OneBotConnection(
            key=str(uuid4()),
            websocket=websocket,
            self_id=str(request.headers.get("x-self-id") or (query.get("self_id") or [""])[0]),
            mode="standalone_reverse",
        )
        await self._register_connection(connection)
        try:
            while True:
                raw = await websocket.recv()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    payload = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                await self._consume_payload(connection, payload)
        except ConnectionClosed:
            pass
        finally:
            self._remove_connection(connection.key)

    def _standalone_authorized(
        self,
        headers: Any,
        query_string: str,
        config: OneBotConfig,
    ) -> bool:
        expected = self.secret_box.decrypt(config.access_token_encrypted)
        if not expected:
            return True
        authorization = str(headers.get("authorization", ""))
        provided = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if not provided:
            provided = (parse_qs(query_string).get("access_token") or [""])[0]
        return hmac.compare_digest(provided, expected)

    async def _run_forward_client(self) -> None:
        delay_seconds = 1
        while True:
            config = self._config()
            if not config.enabled or config.connection_mode != "forward" or not config.forward_ws_url:
                return
            token = self.secret_box.decrypt(config.access_token_encrypted)
            headers = {"Authorization": f"Bearer {token}"} if token else None
            connection: OneBotConnection | None = None
            receiver: asyncio.Task | None = None
            try:
                async with websocket_connect(
                    config.forward_ws_url,
                    additional_headers=headers,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                ) as websocket:
                    connection = OneBotConnection(
                        key=str(uuid4()),
                        websocket=websocket,
                        self_id="",
                        mode="forward",
                    )
                    await self._register_connection(connection)
                    receiver = asyncio.create_task(
                        self._receive_forward_messages(connection),
                        name=f"onebot-forward-receive:{connection.key}",
                    )
                    await self._identify_forward_connection(connection)
                    delay_seconds = 1
                    await receiver
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.forward_error = f"{type(exc).__name__}: {exc}"[:500]
                LOGGER.error("OneBot 正向连接失败 | %s", self.forward_error)
            finally:
                if receiver is not None and not receiver.done():
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                if connection is not None:
                    self._remove_connection(connection.key)
            await asyncio.sleep(delay_seconds)
            delay_seconds = min(delay_seconds * 2, 30)

    async def _receive_forward_messages(self, connection: OneBotConnection) -> None:
        while True:
            raw = await connection.websocket.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            await self._consume_payload(connection, payload)

    async def _identify_forward_connection(self, connection: OneBotConnection) -> None:
        try:
            response = await self._call(connection, "get_login_info", {})
        except OneBotError as exc:
            self.forward_error = str(exc)[:500]
            return
        data = response.get("data")
        if isinstance(data, dict) and data.get("user_id") is not None:
            connection.self_id = str(data["user_id"])

    async def _stop_forward_client(self) -> None:
        task = self.forward_task
        self.forward_task = None
        await self._close_connections("forward", 1001, "OneBot 配置已变更")
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _close_connections(self, mode: str | None, code: int, reason: str) -> None:
        for connection in list(self.connections.values()):
            if mode is not None and connection.mode != mode:
                continue
            try:
                if connection.mode in {"forward", "standalone_reverse"}:
                    await connection.websocket.close()
                else:
                    await connection.websocket.close(code=code, reason=reason)
            except (ConnectionClosed, RuntimeError):
                pass

    async def _register_connection(self, connection: OneBotConnection) -> None:
        self.connections[connection.key] = connection
        self.forward_error = ""
        self.action_executor.outbound_sender = self.send_plugin_action
        LOGGER.info(
            "OneBot 已连接 | mode=%s | self_id=%s",
            connection.mode,
            connection.self_id or "unknown",
        )
        await self.action_executor.retry_pending_outbound()

    def _remove_connection(self, connection_key: str) -> None:
        connection = self.connections.pop(connection_key, None)
        self._fail_connection_calls(connection_key)
        if connection is not None:
            LOGGER.info(
                "OneBot 已断开 | mode=%s | self_id=%s",
                connection.mode,
                connection.self_id or "unknown",
            )
        if not self.connections:
            self.action_executor.outbound_sender = None

    async def _consume_payload(self, connection: OneBotConnection, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        connection.last_event_at = datetime.now(timezone.utc)
        if payload.get("echo") is not None:
            self._resolve_call(connection.key, payload)
            return
        if not connection.self_id and payload.get("self_id") is not None:
            connection.self_id = str(payload["self_id"])
        if payload.get("post_type") == "message":
            task = asyncio.create_task(
                self._handle_message_event(connection, payload),
                name=f"onebot-event:{payload.get('message_id', 'unknown')}",
            )
            self.event_tasks.add(task)
            task.add_done_callback(self.event_tasks.discard)
        elif payload.get("post_type") == "notice":
            LOGGER.info(
                "收到 OneBot notice | type=%s | sub_type=%s | payload=%s",
                payload.get("notice_type", "unknown"),
                payload.get("sub_type", ""),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))[:4000],
            )
            if payload.get("notice_type") in {"friend_recall", "group_recall"}:
                task = asyncio.create_task(
                    self._handle_recall_notice(connection, payload),
                    name=f"onebot-recall:{payload.get('message_id', 'unknown')}",
                )
                self.event_tasks.add(task)
                task.add_done_callback(self.event_tasks.discard)

    async def _handle_message_event(
        self,
        connection: OneBotConnection,
        payload: dict[str, Any],
    ) -> None:
        event_key = self._event_key(payload)
        if not self._claim_event(event_key, payload):
            return
        try:
            config = self._config()
            message_type = str(payload.get("message_type", ""))
            user_id = str(payload.get("user_id", ""))
            self_id = str(payload.get("self_id", connection.self_id))
            group_id = ""
            if connection.self_id and self_id != connection.self_id:
                self._finish_event(event_key, "failed", "事件 self_id 与连接账号不一致")
                return
            if user_id and user_id == self_id:
                self._finish_event(event_key, "ignored")
                return
            if message_type == "private":
                if not config.private_messages or (
                    config.private_allowlist and user_id not in config.private_allowlist
                ):
                    self._finish_event(event_key, "ignored")
                    return
                conversation_id = f"qq:{self_id}:private:{user_id}"
            elif message_type == "group":
                group_id = str(payload.get("group_id", ""))
                if not config.group_messages or (
                    config.group_allowlist and group_id not in config.group_allowlist
                ):
                    self._finish_event(event_key, "ignored")
                    return
                conversation_id = f"qq:{self_id}:group:{group_id}"
            else:
                self._finish_event(event_key, "ignored")
                return

            trigger_message_id = payload.get("message_id")
            normalized_trigger_id = (
                str(trigger_message_id) if trigger_message_id is not None else None
            )
            if normalized_trigger_id and self._consume_early_recall(
                conversation_id,
                user_id,
                normalized_trigger_id,
            ):
                self._finish_event(event_key, "recalled")
                LOGGER.info(
                    "QQ 消息在处理前已撤回 | route=%s | message_id=%s",
                    conversation_id,
                    normalized_trigger_id,
                )
                return

            raw_message = str(payload.get("raw_message", ""))
            text, image_urls = parse_cq_message(raw_message)
            at_targets = parse_onebot_at_targets(raw_message, payload.get("message"))
            sender = payload.get("sender")
            sender_role = str(sender.get("role", "")) if isinstance(sender, dict) else ""
            preprocessed = await self.chat_runtime.plugin_manager.dispatch(
                "before_qq_message",
                PluginEvent(
                    name="before_qq_message",
                    conversation_id=conversation_id,
                    user_id=user_id,
                    text=text,
                    metadata={
                        "channel": f"qq_{message_type}",
                        "message_type": message_type,
                        "group_id": group_id,
                        "self_id": self_id,
                        "sender_role": sender_role,
                        "mentioned_self": self_id in at_targets,
                        "has_media": bool(image_urls),
                    },
                ),
            )
            if normalized_trigger_id and self._consume_early_recall(
                conversation_id,
                user_id,
                normalized_trigger_id,
            ):
                self._finish_event(event_key, "recalled")
                LOGGER.info(
                    "QQ 消息在写入前已撤回 | route=%s | message_id=%s",
                    conversation_id,
                    normalized_trigger_id,
                )
                return
            if preprocessed.consume:
                self._finish_event(event_key, "completed" if preprocessed.actions else "ignored")
                return
            inbound_text = preprocessed.metadata.get("inbound_text", text)
            if isinstance(inbound_text, str):
                text = inbound_text
            ensure_history_content_safe(text)
            if not text and not image_urls:
                self._finish_event(event_key, "empty")
                return
            LOGGER.info(
                "收到 QQ 消息 | route=%s | message_id=%s | images=%s",
                conversation_id,
                trigger_message_id if trigger_message_id is not None else "unknown",
                len(image_urls),
            )
            message = InboundMessage(
                connection=connection,
                payload=dict(payload),
                event_key=event_key,
                conversation_id=conversation_id,
                user_id=user_id,
                message_type=message_type,
                text=text,
                image_urls=image_urls,
                trigger_message_id=normalized_trigger_id,
            )
            delay_seconds = self._reply_merge_delay()
            if delay_seconds > 0:
                self._queue_message_batch(message, delay_seconds)
                return
            await self._process_inbound_messages([message])
        except asyncio.CancelledError:
            self._finish_event(event_key, "cancelled")
            raise
        except Exception as exc:
            self._finish_event(event_key, "failed", f"{type(exc).__name__}: {exc}")
            LOGGER.error("QQ 消息接收失败 | %s: %s", type(exc).__name__, exc)

    def _reply_merge_delay(self) -> float:
        manager = self.chat_runtime.plugin_manager
        if not manager.is_enabled("reply_merge"):
            return 0.0
        try:
            value = float(manager.get_settings("reply_merge").get("message_batch_delay", 4))
        except (TypeError, ValueError):
            value = 4.0
        return max(0.0, min(value, 60.0))

    def _queue_message_batch(self, message: InboundMessage, delay_seconds: float) -> None:
        key = (message.conversation_id, message.user_id)
        batch = self.pending_message_batches.setdefault(key, PendingMessageBatch())
        batch.messages.append(message)
        if batch.flush_task is not None and not batch.flush_task.done():
            batch.flush_task.cancel()
        task = asyncio.create_task(
            self._flush_message_batch(key, delay_seconds),
            name=f"onebot-message-batch:{message.conversation_id}:{message.user_id}",
        )
        batch.flush_task = task
        self.event_tasks.add(task)
        task.add_done_callback(self.event_tasks.discard)
        self._finish_event(message.event_key, "buffered")

    async def _flush_message_batch(
        self,
        key: tuple[str, str],
        delay_seconds: float,
    ) -> None:
        await asyncio.sleep(delay_seconds)
        batch = self.pending_message_batches.get(key)
        if batch is None or batch.flush_task is not asyncio.current_task():
            return
        self.pending_message_batches.pop(key, None)
        await self._process_inbound_messages(batch.messages)

    def _find_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        trigger_message_id: str,
    ) -> InboundMessage | None:
        batch = self.pending_message_batches.get((conversation_id, user_id))
        if batch is None:
            return None
        return next(
            (
                message
                for message in batch.messages
                if message.trigger_message_id == trigger_message_id
            ),
            None,
        )

    def _remove_pending_message(
        self,
        conversation_id: str,
        user_id: str,
        trigger_message_id: str,
    ) -> InboundMessage | None:
        key = (conversation_id, user_id)
        batch = self.pending_message_batches.get(key)
        if batch is None:
            return None
        for index, message in enumerate(batch.messages):
            if message.trigger_message_id != trigger_message_id:
                continue
            removed = batch.messages.pop(index)
            self._finish_event(removed.event_key, "recalled")
            if not batch.messages:
                if batch.flush_task is not None and not batch.flush_task.done():
                    batch.flush_task.cancel()
                self.pending_message_batches.pop(key, None)
            return removed
        return None

    @staticmethod
    def _early_recall_key(
        conversation_id: str,
        user_id: str,
        trigger_message_id: str,
    ) -> tuple[str, str, str]:
        return conversation_id, user_id, trigger_message_id

    def _prune_early_recalls(self) -> None:
        now = asyncio.get_running_loop().time()
        expired = [
            key for key, recall in self.early_recalls.items() if recall.expires_at <= now
        ]
        for key in expired:
            recall = self.early_recalls.pop(key)
            self._finish_event(recall.event_key, "ignored")

    def _store_early_recall(
        self,
        conversation_id: str,
        user_id: str,
        trigger_message_id: str,
        event_key: str,
    ) -> None:
        self._prune_early_recalls()
        self.early_recalls[
            self._early_recall_key(conversation_id, user_id, trigger_message_id)
        ] = EarlyRecall(
            event_key=event_key,
            expires_at=asyncio.get_running_loop().time() + EARLY_RECALL_TTL_SECONDS,
        )
        self._finish_event(event_key, "buffered")

    def _consume_early_recall(
        self,
        conversation_id: str,
        user_id: str,
        trigger_message_id: str,
    ) -> bool:
        self._prune_early_recalls()
        recall = self.early_recalls.pop(
            self._early_recall_key(conversation_id, user_id, trigger_message_id),
            None,
        )
        if recall is None:
            return False
        self._finish_event(recall.event_key, "completed")
        return True

    async def _process_inbound_messages(self, messages: list[InboundMessage]) -> None:
        if not messages:
            return
        first = messages[0]
        turn_id: str | None = None
        trigger_ids = [
            message.trigger_message_id
            for message in messages
            if message.trigger_message_id is not None
        ]
        final_api_error_notified = False

        async def notify_provider_failure(
            failed_name: str,
            next_name: str | None,
            error: ModelClientError,
        ) -> None:
            nonlocal final_api_error_notified
            if next_name is not None:
                await self._send_api_failover_notice(
                    first.connection,
                    first.conversation_id,
                    failed_name,
                    next_name,
                )
                return
            await self._send_api_error_notice(
                first.connection,
                first.conversation_id,
                error,
                provider_name=failed_name,
            )
            final_api_error_notified = True
        try:
            if trigger_ids:
                turn = self.chat_runtime.begin_qq_turn(
                    first.conversation_id,
                    trigger_ids[0],
                    first.user_id,
                    f"qq_{first.message_type}",
                    trigger_message_ids=trigger_ids,
                )
                turn_id = turn.id
                current_task = asyncio.current_task()
                if current_task is not None:
                    self.turn_tasks[turn_id] = current_task
                for message in messages:
                    if message.trigger_message_id is None:
                        continue
                    recall_monitor = asyncio.create_task(
                        self._monitor_recall(message.connection, message.payload, turn.id),
                        name=f"onebot-recall-monitor:{message.trigger_message_id}",
                    )
                    self.event_tasks.add(recall_monitor)
                    recall_monitor.add_done_callback(self.event_tasks.discard)
            image_urls = [url for message in messages for url in message.image_urls]
            received_images = (
                await self.media_receiver.download_images(image_urls) if image_urls else []
            )
            await self.chat_runtime.handle_user_message(
                conversation_id=first.conversation_id,
                user_id=first.user_id,
                text="\n".join(message.text or "（图片）" for message in messages),
                channel=f"qq_{first.message_type}",
                media_refs=[
                    {"kind": "image", "ref": item.ref, "name": item.name}
                    for item in received_images
                ],
                model_images=[item.normalized for item in received_images],
                turn_id=turn_id,
                provider_failure_notifier=notify_provider_failure,
            )
            if turn_id:
                self.chat_runtime.mark_turn_completed(turn_id)
                self.turn_tasks.pop(turn_id, None)
            for message in messages:
                self._finish_event(message.event_key, "completed")
            LOGGER.info(
                "QQ 消息处理完成 | route=%s | message_ids=%s | batch_size=%s",
                first.conversation_id,
                ",".join(trigger_ids) or "unknown",
                len(messages),
            )
        except asyncio.CancelledError:
            if turn_id:
                self.turn_tasks.pop(turn_id, None)
            for message in messages:
                self._finish_event(message.event_key, "cancelled")
            raise
        except ModelClientError as exc:
            if turn_id:
                self.turn_tasks.pop(turn_id, None)
                self.chat_runtime.mark_turn_failed(turn_id)
            for message in messages:
                self._finish_event(message.event_key, "failed", f"{type(exc).__name__}: {exc}")
            if not final_api_error_notified:
                try:
                    await self._send_api_error_notice(
                        first.connection,
                        first.conversation_id,
                        exc,
                    )
                except OneBotError as notice_error:
                    LOGGER.error("发送 API 错误通知失败 | %s", notice_error)
        except Exception as exc:
            if turn_id:
                self.turn_tasks.pop(turn_id, None)
                self.chat_runtime.mark_turn_failed(turn_id)
            for message in messages:
                self._finish_event(message.event_key, "failed", f"{type(exc).__name__}: {exc}")
            LOGGER.error(
                "QQ 消息处理失败 | route=%s | %s: %s",
                first.conversation_id,
                type(exc).__name__,
                exc,
            )

    async def _send_api_failover_notice(
        self,
        connection: OneBotConnection,
        conversation_id: str,
        failed_name: str,
        next_name: str,
    ) -> None:
        await self._send_temporary_api_notice(
            connection,
            conversation_id,
            f"API配置·{failed_name} 调用失败，故障转移至 API配置·{next_name}",
        )

    async def _send_api_error_notice(
        self,
        connection: OneBotConnection,
        conversation_id: str,
        error: ModelClientError,
        *,
        provider_name: str = "",
    ) -> None:
        prefix = (
            f"API配置·{provider_name} 调用失败，所有 API 配置均不可用。"
            if provider_name
            else "所有 API 配置均调用失败。"
        )
        await self._send_temporary_api_notice(
            connection,
            conversation_id,
            f"{prefix}\n完整报错：\n{self._format_model_error(error)}",
        )

    async def _send_temporary_api_notice(
        self,
        connection: OneBotConnection,
        conversation_id: str,
        text: str,
    ) -> None:
        match = CONVERSATION_PATTERN.fullmatch(conversation_id)
        if match is None:
            raise OneBotError("API 错误通知的 conversation_id 格式无效")
        message_type = match.group("message_type")
        action = "send_private_msg" if message_type == "private" else "send_group_msg"
        target_key = "user_id" if message_type == "private" else "group_id"
        footer = "（此消息在60秒后自动撤回）"
        parts = ChatRuntime._split_outbound_text(text, 4_000 - len(footer))
        for part in parts:
            response = await self._call(
                connection,
                action,
                {
                    target_key: int(match.group("target_id")),
                    "message": [
                        {"type": "text", "data": {"text": f"{part}{footer}"}}
                    ],
                },
            )
            data = response.get("data")
            message_id = data.get("message_id") if isinstance(data, dict) else None
            if message_id is None:
                LOGGER.warning("API 通知已发送，但 OneBot 未返回 message_id，无法自动撤回")
                continue
            task = asyncio.create_task(
                self._recall_api_error_notice(conversation_id, str(message_id)),
                name=f"onebot-api-error-recall:{message_id}",
            )
            self.event_tasks.add(task)
            task.add_done_callback(self.event_tasks.discard)

    @staticmethod
    def _format_model_error(error: ModelClientError) -> str:
        parts = [f"错误类型：{type(error).__name__}"]
        if isinstance(error, ModelHTTPError):
            parts.append(f"HTTP 状态：{error.status_code}")
        parts.append(f"错误消息：{str(error).strip() or '（空）'}")
        if isinstance(error, ModelHTTPError) and error.body.strip():
            parts.extend(("响应正文：", error.body.strip()))
        return "\n".join(parts)

    async def _recall_api_error_notice(self, conversation_id: str, message_id: str) -> None:
        await asyncio.sleep(API_ERROR_RECALL_SECONDS)
        try:
            await self._delete_message(conversation_id, message_id)
        except OneBotError as exc:
            LOGGER.warning("自动撤回 API 错误通知失败 | message_id=%s | %s", message_id, exc)

    async def _handle_recall_notice(
        self,
        connection: OneBotConnection,
        payload: dict[str, Any],
    ) -> None:
        event_key = self._event_key(payload)
        if not self._claim_event(event_key, payload):
            LOGGER.info(
                "忽略重复 QQ 撤回 | message_id=%s | source=%s",
                payload.get("message_id", "unknown"),
                payload.get("_recall_detection", "notice"),
            )
            return
        prepared_turn = None
        recall_recovery_status = "failed"
        try:
            config = self._config()
            notice_type = str(payload.get("notice_type", ""))
            self_id = str(payload.get("self_id", connection.self_id))
            user_id = str(payload.get("user_id", ""))
            trigger_message_id = payload.get("message_id")
            LOGGER.info(
                "收到 QQ 撤回 | type=%s | message_id=%s | user_id=%s | source=%s",
                notice_type or "unknown",
                trigger_message_id if trigger_message_id is not None else "unknown",
                user_id or "unknown",
                payload.get("_recall_detection", "notice"),
            )
            if connection.self_id and self_id != connection.self_id:
                self._finish_event(event_key, "ignored")
                LOGGER.info(
                    "忽略 QQ 撤回：账号不匹配 | connection=%s | event=%s",
                    connection.self_id,
                    self_id,
                )
                return
            if not user_id or user_id == self_id or trigger_message_id is None:
                self._finish_event(event_key, "ignored")
                LOGGER.info(
                    "忽略 QQ 撤回：用户或消息标识无效 | message_id=%s | user_id=%s",
                    trigger_message_id,
                    user_id or "unknown",
                )
                return
            if notice_type == "friend_recall":
                if not config.private_messages or (
                    config.private_allowlist and user_id not in config.private_allowlist
                ):
                    self._finish_event(event_key, "ignored")
                    LOGGER.info("忽略 QQ 撤回：私聊未启用或不在白名单 | user_id=%s", user_id)
                    return
                conversation_id = f"qq:{self_id}:private:{user_id}"
            elif notice_type == "group_recall":
                group_id = str(payload.get("group_id", ""))
                operator_id = str(payload.get("operator_id", user_id))
                if operator_id != user_id or not config.group_messages or not group_id or (
                    config.group_allowlist and group_id not in config.group_allowlist
                ):
                    self._finish_event(event_key, "ignored")
                    LOGGER.info(
                        "忽略 QQ 撤回：群聊未启用、越权撤回或不在白名单 | group_id=%s | user_id=%s | operator_id=%s",
                        group_id or "unknown",
                        user_id,
                        operator_id,
                    )
                    return
                conversation_id = f"qq:{self_id}:group:{group_id}"
            else:
                self._finish_event(event_key, "ignored")
                LOGGER.info("忽略 QQ 撤回：通知类型不支持 | type=%s", notice_type or "unknown")
                return

            normalized_trigger_id = str(trigger_message_id)
            recall_allowed = False
            pending_message = self._find_pending_message(
                conversation_id,
                user_id,
                normalized_trigger_id,
            )
            if pending_message is not None:
                age_seconds = max(
                    0.0,
                    (datetime.now(timezone.utc) - pending_message.queued_at).total_seconds(),
                )
                decision = await self.chat_runtime.plugin_manager.dispatch(
                    "on_message_recall",
                    PluginEvent(
                        name="on_message_recall",
                        conversation_id=conversation_id,
                        user_id=user_id,
                        metadata={
                            "trigger_message_id": normalized_trigger_id,
                            "notice_type": notice_type,
                            "age_seconds": age_seconds,
                            "pending_batch": True,
                        },
                    ),
                )
                if not decision.metadata.get("allow_rollback"):
                    self._finish_event(event_key, "ignored")
                    return
                recall_allowed = True
                removed = self._remove_pending_message(
                    conversation_id,
                    user_id,
                    normalized_trigger_id,
                )
                if removed is not None:
                    self._finish_event(event_key, "completed")
                    LOGGER.info(
                        "QQ 撤回已从待合并队列移除 | route=%s | message_id=%s",
                        conversation_id,
                        trigger_message_id,
                    )
                    return

            turn = self.chat_runtime.find_recall_turn(
                conversation_id,
                normalized_trigger_id,
                user_id,
            )
            if turn is None:
                if not recall_allowed:
                    decision = await self.chat_runtime.plugin_manager.dispatch(
                        "on_message_recall",
                        PluginEvent(
                            name="on_message_recall",
                            conversation_id=conversation_id,
                            user_id=user_id,
                            metadata={
                                "trigger_message_id": normalized_trigger_id,
                                "notice_type": notice_type,
                                "age_seconds": 0.0,
                                "pending_registration": True,
                            },
                        ),
                    )
                    if not decision.metadata.get("allow_rollback"):
                        self._finish_event(event_key, "ignored")
                        return
                    recall_allowed = True

                pending_message = self._find_pending_message(
                    conversation_id,
                    user_id,
                    normalized_trigger_id,
                )
                if pending_message is not None:
                    removed = self._remove_pending_message(
                        conversation_id,
                        user_id,
                        normalized_trigger_id,
                    )
                    if removed is not None:
                        self._finish_event(event_key, "completed")
                        LOGGER.info(
                            "QQ 撤回已从待合并队列移除 | route=%s | message_id=%s",
                            conversation_id,
                            trigger_message_id,
                        )
                        return

                turn = self.chat_runtime.find_recall_turn(
                    conversation_id,
                    normalized_trigger_id,
                    user_id,
                )
                if turn is None:
                    self._store_early_recall(
                        conversation_id,
                        user_id,
                        normalized_trigger_id,
                        event_key,
                    )
                    LOGGER.info(
                        "QQ 撤回早于消息登记，等待原消息 | route=%s | message_id=%s",
                        conversation_id,
                        trigger_message_id,
                    )
                    return
            age_seconds = max(
                0.0,
                (
                    datetime.now(timezone.utc).replace(tzinfo=None) - turn.created_at
                ).total_seconds(),
            )
            if not recall_allowed:
                decision = await self.chat_runtime.plugin_manager.dispatch(
                    "on_message_recall",
                    PluginEvent(
                        name="on_message_recall",
                        conversation_id=conversation_id,
                        user_id=user_id,
                        metadata={
                            "record_id": turn.conversation_id,
                            "turn_id": turn.id,
                            "trigger_message_id": normalized_trigger_id,
                            "notice_type": notice_type,
                            "age_seconds": age_seconds,
                        },
                    ),
                )
                if not decision.metadata.get("allow_rollback"):
                    self._finish_event(event_key, "ignored")
                    LOGGER.info(
                        "忽略 QQ 撤回：撤回插件未允许回滚 | route=%s | message_id=%s | age=%.2fs",
                        conversation_id,
                        trigger_message_id,
                        age_seconds,
                    )
                    return
            recall_recovery_status = "failed" if turn.status == "active" else turn.status
            prepared_turn = self.chat_runtime.prepare_turn_recall(turn.id)
            if prepared_turn is None:
                self._finish_event(event_key, "ignored")
                LOGGER.info(
                    "忽略 QQ 撤回：回合状态已变化 | route=%s | turn_id=%s",
                    conversation_id,
                    turn.id,
                )
                return

            LOGGER.info(
                "开始回滚 QQ 回合 | route=%s | turn_id=%s | message_id=%s",
                conversation_id,
                prepared_turn.id,
                trigger_message_id,
            )
            generation_task = self.turn_tasks.get(prepared_turn.id)
            if generation_task is not None and not generation_task.done():
                generation_task.cancel()
                await asyncio.gather(generation_task, return_exceptions=True)
            await self.action_executor.cancel_turn_actions(prepared_turn.id)
            await self.action_executor.wait_for_turn_actions(
                prepared_turn.id,
                timeout_seconds=max(1.0, float(config.api_timeout_seconds) + 0.5),
            )
            sent_message_ids = await self.chat_runtime.rollback_turn(prepared_turn.id)
            for message_id in sent_message_ids:
                try:
                    await self._delete_message(conversation_id, message_id)
                except OneBotError as exc:
                    LOGGER.warning(
                        "同步撤回 QQ 回复失败 | route=%s | message_id=%s | %s",
                        conversation_id,
                        message_id,
                        exc,
                    )
            self.action_executor.forget_cancelled_turn(prepared_turn.id)
            self._finish_event(event_key, "completed")
            LOGGER.info(
                "QQ 撤回已回滚 | route=%s | trigger_message_id=%s | recalled_messages=%s",
                conversation_id,
                trigger_message_id,
                len(sent_message_ids),
            )
        except asyncio.CancelledError:
            self._finish_event(event_key, "cancelled")
            raise
        except Exception as exc:
            self._finish_event(event_key, "failed", f"{type(exc).__name__}: {exc}")
            LOGGER.error("QQ 撤回处理失败 | %s: %s", type(exc).__name__, exc)
        finally:
            if prepared_turn is not None:
                self.chat_runtime.recover_turn_recall(
                    prepared_turn.id,
                    recall_recovery_status,
                )
                self.chat_runtime.finish_turn_recall(
                    prepared_turn.route_id,
                    prepared_turn.id,
                )

    async def _monitor_recall(
        self,
        connection: OneBotConnection,
        original_payload: dict[str, Any],
        turn_id: str,
    ) -> None:
        """Fallback for OneBot implementations that do not forward recall notices."""
        message_id = original_payload.get("message_id")
        if message_id is None:
            return
        message_type = str(original_payload.get("message_type", ""))
        if message_type == "group":
            history_action = "get_group_msg_history"
            target_key = "group_id"
            target_id = original_payload.get("group_id")
        elif message_type == "private":
            history_action = "get_friend_msg_history"
            target_key = "user_id"
            target_id = original_payload.get("user_id")
        else:
            return
        if target_id is None:
            return
        history_params = {
            target_key: str(target_id),
            "message_seq": str(message_id),
            "count": 100,
            "reverse_order": False,
            "disable_get_url": True,
            "parse_mult_msg": False,
            "quick_reply": False,
            "reverseOrder": False,
        }
        deadline = asyncio.get_running_loop().time() + RECALL_POLL_WINDOW_SECONDS
        # The inbound OneBot event itself proves that this message existed. Requiring a
        # later API lookup to confirm it breaks the common "send then immediately recall"
        # case because the first poll happens after the recall.
        history_supported: bool | None = None
        history_confirmed_present = True
        history_missing = 0
        history_state_logged = False
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(RECALL_POLL_INTERVAL_SECONDS)
            with self.database.session_factory() as session:
                turn = session.get(ConversationTurn, turn_id)
                if turn is None or turn.status in {"recalled", "recalling", "edited"}:
                    return
            get_msg_recalled = False
            if history_supported is not True:
                try:
                    get_msg_response = await self._call(
                        connection,
                        "get_msg",
                        {"message_id": message_id},
                    )
                except OneBotUnavailable:
                    return
                except OneBotError:
                    pass
                else:
                    get_msg_data = get_msg_response.get("data")
                    get_msg_recalled = (
                        isinstance(get_msg_data, dict)
                        and self._message_looks_recalled(get_msg_data)
                    )

            history_recalled = False
            if not get_msg_recalled and history_supported is not False:
                try:
                    history_response = await self._call(connection, history_action, history_params)
                except OneBotUnavailable:
                    return
                except OneBotError as exc:
                    if history_supported is None:
                        history_supported = False
                        LOGGER.warning(
                            "OneBot 历史消息接口不可用，撤回轮询退回 get_msg 明确标记 | action=%s | message_id=%s | %s",
                            history_action,
                            message_id,
                            exc,
                        )
                else:
                    history_state = self._history_message_state(history_response, message_id)
                    if history_state is None:
                        if history_supported is None:
                            history_supported = False
                            LOGGER.warning(
                                "OneBot 历史消息响应格式无效，撤回轮询退回 get_msg 明确标记 | action=%s | message_id=%s",
                                history_action,
                                message_id,
                            )
                    else:
                        history_supported = True
                        if not history_state_logged:
                            LOGGER.info(
                                "撤回轮询历史状态 | action=%s | message_id=%s | state=%s",
                                history_action,
                                message_id,
                                history_state,
                            )
                            history_state_logged = True
                        if history_state == "recalled":
                            history_recalled = True
                            history_missing = 0
                        elif history_state == "present":
                            history_confirmed_present = True
                            history_missing = 0
                        elif history_confirmed_present:
                            history_missing += 1

            detection = ""
            if get_msg_recalled:
                detection = "get_msg_recall_marker"
            elif history_recalled:
                detection = "history_recall_marker"
            elif history_supported is True and history_confirmed_present and history_missing >= 2:
                detection = "history_missing"
            if not detection:
                continue

            synthetic = dict(original_payload)
            synthetic["post_type"] = "notice"
            synthetic["_recall_detection"] = detection
            if message_type == "group":
                synthetic["notice_type"] = "group_recall"
                synthetic["operator_id"] = original_payload.get("user_id")
            else:
                synthetic["notice_type"] = "friend_recall"
            LOGGER.info(
                "通过 %s 检测到 QQ 撤回 | message_id=%s",
                detection,
                message_id,
            )
            await self._handle_recall_notice(connection, synthetic)
            return

    @staticmethod
    def _history_message_state(response: dict[str, Any], message_id: Any) -> str | None:
        data = response.get("data")
        messages = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return None
        expected = str(message_id)
        matches = [
            item
            for item in messages
            if isinstance(item, dict)
            and str(item.get("message_id", item.get("id", ""))) == expected
        ]
        if not matches:
            return "missing"
        if any(OneBotGateway._message_looks_recalled(item) for item in matches):
            return "recalled"
        return "present"

    @staticmethod
    def _message_looks_recalled(message: dict[str, Any]) -> bool:
        for key in ("recallTime", "recall_time", "recalled_at"):
            if key in message and str(message.get(key) or "0") not in {"0", "", "None"}:
                return True
        if message.get("recalled") is True or message.get("is_recalled") is True:
            return True
        if str(message.get("notice_type", "")) in {"friend_recall", "group_recall"}:
            return True
        segments = message.get("message")
        if isinstance(segments, list) and any(
            isinstance(segment, dict)
            and str(segment.get("type", "")).lower() in {"recall", "revoke", "gray_tip"}
            for segment in segments
        ):
            return True
        # NapCat stores a recalled message as a gray-tip record with the same msgId.
        # Its OneBot history converter drops recallTime and the gray-tip element,
        # leaving an explicitly empty raw_message plus an empty message payload.
        if "raw_message" in message and "message" in message:
            raw_empty = not str(message.get("raw_message") or "").strip()
            content = message.get("message")
            content_empty = content is None or content == "" or content == []
            if raw_empty and content_empty:
                return True
        return False

    def _event_key(self, payload: dict[str, Any]) -> str:
        self_id = str(payload.get("self_id", ""))
        message_id = payload.get("message_id")
        if payload.get("post_type") == "notice" and message_id is not None:
            notice_type = str(payload.get("notice_type", "unknown"))
            return f"notice:{notice_type}:{self_id}:{message_id}"[:240]
        if message_id is not None:
            return f"message:{self_id}:{message_id}"[:240]
        raw = "|".join(
            str(payload.get(key, ""))
            for key in (
                "time",
                "post_type",
                "notice_type",
                "message_type",
                "message_id",
                "user_id",
                "operator_id",
                "group_id",
                "raw_message",
            )
        )
        return f"fallback:{self_id}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"

    def _claim_event(self, event_key: str, payload: dict[str, Any]) -> bool:
        with self.database.session_factory() as session:
            if session.get(OneBotEvent, event_key) is not None:
                return False
            session.add(
                OneBotEvent(
                    event_key=event_key,
                    self_id=str(payload.get("self_id", "")),
                    post_type=str(payload.get("post_type", "")),
                )
            )
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

    def _finish_event(self, event_key: str, status: str, error: str = "") -> None:
        with self.database.session_factory() as session:
            event = session.get(OneBotEvent, event_key)
            if event is not None:
                event.status = status
                event.error = error[:4000]
                session.commit()

    async def send_plugin_action(self, plugin_id: str, action: PluginAction) -> dict[str, Any]:
        match = CONVERSATION_PATTERN.fullmatch(str(action.payload.get("conversation_id", "")))
        if match is None:
            raise OneBotError("QQ 动作的 conversation_id 格式无效")
        message_type = match.group("message_type")
        target_id = int(match.group("target_id"))
        if action.kind == "send_text":
            original_text = str(action.payload.get("text", ""))
            text = await self._filter_qq_text(
                original_text,
                conversation_id=str(action.payload.get("conversation_id", "")),
                source_plugin_id=plugin_id,
                character_id=str(action.payload.get("character_id", "")).strip(),
            )
            if not text:
                LOGGER.info("QQ 文本已被正则完全隐藏 | plugin=%s", plugin_id)
                return {"status": "ok", "retcode": 0, "data": {"skipped": True}}
            message = [{"type": "text", "data": {"text": text}}]
        elif action.kind == "send_image":
            path = self._safe_image_path(str(action.payload.get("asset_ref", "")))
            message = [{"type": "image", "data": {"file": str(path)}}]
        else:
            raise OneBotError(f"不支持的 OneBot 发送动作：{action.kind}")
        connection = self._select_connection(match.group("self_id"))
        action_name = "send_private_msg" if message_type == "private" else "send_group_msg"
        target_key = "user_id" if message_type == "private" else "group_id"
        response = await self._call(
            connection,
            action_name,
            {target_key: target_id, "message": message},
        )
        turn_id = str(action.payload.get("_runtime_turn_id", "")).strip()
        data = response.get("data")
        if turn_id and isinstance(data, dict) and data.get("message_id") is not None:
            self.chat_runtime.record_turn_sent_message(turn_id, data["message_id"])
        return response

    async def _filter_qq_text(
        self,
        text: str,
        *,
        conversation_id: str,
        source_plugin_id: str,
        character_id: str,
    ) -> str:
        text = self._strip_qq_hidden_markers(text)
        resolved_character_id = character_id or self._active_character_id()
        result = await self.chat_runtime.plugin_manager.dispatch(
            "before_send",
            PluginEvent(
                name="before_send",
                conversation_id=conversation_id,
                text=text,
                response_text=text,
                metadata={
                    "character_id": resolved_character_id,
                    "source_plugin_id": source_plugin_id,
                    "channel": "qq",
                },
            ),
        )
        transformed = result.metadata.get("outbound_text")
        if isinstance(transformed, str):
            text = transformed
        return self._strip_qq_hidden_markers(text).strip()

    @staticmethod
    def _strip_qq_hidden_markers(text: str) -> str:
        return TEST_INPUTS_REJECTED_PATTERN.sub("", text)

    def _active_character_id(self) -> str:
        with self.database.session_factory() as session:
            preset = session.scalar(
                select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
            )
            return str(preset.character_id or "") if preset is not None else ""

    async def _delete_message(self, conversation_id: str, message_id: str) -> None:
        match = CONVERSATION_PATTERN.fullmatch(conversation_id)
        if match is None:
            raise OneBotError("QQ 撤回动作的 conversation_id 格式无效")
        connection = self._select_connection(match.group("self_id"))
        normalized_message_id: int | str = int(message_id) if message_id.isdigit() else message_id
        await self._call(connection, "delete_msg", {"message_id": normalized_message_id})

    def _select_connection(self, self_id: str | None) -> OneBotConnection:
        if self_id:
            match = next((item for item in self.connections.values() if item.self_id == self_id), None)
            if match is not None:
                return match
        if len(self.connections) == 1:
            return next(iter(self.connections.values()))
        raise OneBotUnavailable("没有可用于该 QQ 账号的 OneBot 反向 WebSocket 连接")

    def _safe_image_path(self, asset_ref: str) -> Path:
        path = Path(asset_ref).resolve()
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise OneBotError("待发送图片不存在或格式不受支持")
        if not any(path == root or root in path.parents for root in self.allowed_media_roots):
            raise OneBotError("待发送图片不在允许的运行数据或插件目录中")
        if path.stat().st_size > MAX_OUTBOUND_IMAGE_BYTES:
            raise OneBotError("待发送图片超过 32 MB")
        return path

    async def _call(
        self,
        connection: OneBotConnection,
        action: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        echo = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending_calls[echo] = (future, connection.key)
        try:
            async with connection.send_lock:
                await self._send_json(connection, {"action": action, "params": params, "echo": echo})
            timeout = self._config().api_timeout_seconds
            response = await asyncio.wait_for(future, timeout=timeout)
        except (WebSocketDisconnect, ConnectionClosed, OSError, asyncio.TimeoutError) as exc:
            raise OneBotUnavailable(f"OneBot {action} 调用未得到响应") from exc
        finally:
            self.pending_calls.pop(echo, None)
        if response.get("status") != "ok" or response.get("retcode", 0) != 0:
            raise OneBotError(
                f"OneBot {action} 失败：retcode={response.get('retcode', 'unknown')}"
            )
        return response

    async def _send_json(self, connection: OneBotConnection, payload: dict[str, Any]) -> None:
        if connection.mode in {"forward", "standalone_reverse"}:
            await connection.websocket.send(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            return
        await connection.websocket.send_json(payload)

    def _resolve_call(self, connection_key: str, payload: dict[str, Any]) -> None:
        pending = self.pending_calls.get(str(payload.get("echo")))
        if pending is None:
            return
        future, expected_connection_key = pending
        if connection_key != expected_connection_key:
            return
        if not future.done():
            future.set_result(payload)

    def _fail_connection_calls(self, connection_key: str) -> None:
        for future, key in list(self.pending_calls.values()):
            if key == connection_key and not future.done():
                future.set_exception(OneBotUnavailable("OneBot 连接已断开"))

    def status(self) -> dict[str, Any]:
        config = self._config()
        connected_at = min(
            (item.connected_at for item in self.connections.values()),
            default=None,
        )
        last_event_at = max(
            (item.last_event_at for item in self.connections.values() if item.last_event_at),
            default=None,
        )
        with self.database.session_factory() as session:
            pending = session.scalar(
                select(func.count()).select_from(RuntimeAction).where(RuntimeAction.status == "pending")
            ) or 0
            failed = session.scalar(
                select(func.count()).select_from(RuntimeAction).where(RuntimeAction.status == "failed")
            ) or 0
        return {
            "enabled": config.enabled,
            "connection_mode": config.connection_mode,
            "connected": bool(self.connections),
            "connections": len(self.connections),
            "self_ids": sorted(item.self_id for item in self.connections.values() if item.self_id),
            "connected_at": connected_at,
            "last_event_at": last_event_at,
            "pending_actions": pending,
            "failed_actions": failed,
            "connection_error": self.forward_error,
        }
