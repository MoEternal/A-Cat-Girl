from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update

from .database import Database, RuntimeAction
from .plugins.context import sanitize_plugin_data
from .plugins.types import PluginAction


LOGGER = logging.getLogger("catgirl.actions")


GenerationHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]
OutboundSender = Callable[[str, PluginAction], Any | Awaitable[Any]]


class ActionExecutor:
    INTERNAL_ACTIONS = {
        "history_filter",
        "prompt_addition",
        "replace_model_response",
        "replace_response",
        "message_buffered",
        "sleep_started",
    }
    OUTBOUND_ACTIONS = {"send_text", "send_image"}

    def __init__(self, database: Database, outbound_sender: OutboundSender | None = None):
        self.database = database
        self.outbound_sender = outbound_sender
        self.generation_handler: GenerationHandler | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self._cancelled_turns: set[str] = set()

    def set_generation_handler(self, handler: GenerationHandler) -> None:
        self.generation_handler = handler

    async def startup(self) -> None:
        with self.database.session_factory() as session:
            session.execute(
                update(RuntimeAction)
                .where(RuntimeAction.status == "processing")
                .values(status="pending", error="服务重启后重新排队")
            )
            recoverable = session.scalars(
                select(RuntimeAction)
                .where(RuntimeAction.status == "pending")
                .order_by(RuntimeAction.created_at)
            ).all()
            action_ids = [item.id for item in recoverable]
            session.commit()
        self.worker = asyncio.create_task(self._run(), name="runtime-action-executor")
        for action_id in action_ids:
            self.queue.put_nowait(action_id)

    async def shutdown(self) -> None:
        if self.worker is None:
            return
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        self.worker = None

    async def submit(
        self,
        plugin_id: str,
        action: PluginAction,
        *,
        turn_id: str | None = None,
    ) -> str:
        payload = sanitize_plugin_data(action.payload)
        conversation_id = str(payload.get("conversation_id", ""))[:200]
        stored = RuntimeAction(
            plugin_id=plugin_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            kind=action.kind,
            payload=payload,
            status="pending",
        )
        with self.database.session_factory() as session:
            session.add(stored)
            session.commit()
            session.refresh(stored)
            action_id = stored.id
        self.queue.put_nowait(action_id)
        return action_id

    async def retry_pending_outbound(self) -> int:
        with self.database.session_factory() as session:
            action_ids = list(
                session.scalars(
                    select(RuntimeAction.id)
                    .where(
                        RuntimeAction.status == "pending",
                        RuntimeAction.kind.in_(self.OUTBOUND_ACTIONS),
                    )
                    .order_by(RuntimeAction.created_at)
                ).all()
            )
        for action_id in action_ids:
            self.queue.put_nowait(action_id)
        return len(action_ids)

    async def cancel_turn_actions(self, turn_id: str) -> None:
        if not turn_id:
            return
        self._cancelled_turns.add(turn_id)
        with self.database.session_factory() as session:
            session.execute(
                update(RuntimeAction)
                .where(
                    RuntimeAction.turn_id == turn_id,
                    RuntimeAction.status == "pending",
                )
                .values(status="cancelled", error="用户撤回了本轮消息")
            )
            session.commit()

    def forget_cancelled_turn(self, turn_id: str) -> None:
        self._cancelled_turns.discard(turn_id)

    async def wait_for_turn_actions(self, turn_id: str, timeout_seconds: float = 3.0) -> bool:
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_seconds)
        while True:
            with self.database.session_factory() as session:
                processing = session.scalar(
                    select(RuntimeAction.id)
                    .where(
                        RuntimeAction.turn_id == turn_id,
                        RuntimeAction.status == "processing",
                    )
                    .limit(1)
                )
            if processing is None:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.02)

    async def _run(self) -> None:
        while True:
            action_id = await self.queue.get()
            try:
                await self._process(action_id)
            finally:
                self.queue.task_done()

    async def _process(self, action_id: str) -> None:
        with self.database.session_factory() as session:
            action = session.get(RuntimeAction, action_id)
            if action is None or action.status != "pending":
                return
            if action.kind in self.OUTBOUND_ACTIONS and self.outbound_sender is None:
                return
            action.status = "processing"
            action.attempts += 1
            session.commit()
            plugin_id = action.plugin_id
            kind = action.kind
            payload = dict(action.payload or {})
            turn_id = action.turn_id

        try:
            if kind == "request_generation":
                if self.generation_handler is None:
                    raise RuntimeError("模型生成处理器尚未就绪")
                await self.generation_handler(plugin_id, payload)
            elif kind in self.OUTBOUND_ACTIONS:
                delay_seconds = max(0.0, min(float(payload.pop("delay_seconds", 0)), 60.0))
                while delay_seconds > 0:
                    await asyncio.sleep(min(delay_seconds, 0.05))
                    delay_seconds -= 0.05
                    if turn_id and turn_id in self._cancelled_turns:
                        self._mark_cancelled(action_id)
                        return
                with self.database.session_factory() as session:
                    stored = session.get(RuntimeAction, action_id)
                    if stored is None or stored.status != "processing":
                        return
                if turn_id and turn_id in self._cancelled_turns:
                    self._mark_cancelled(action_id)
                    return
                if turn_id:
                    payload["_runtime_turn_id"] = turn_id
                value = self.outbound_sender(plugin_id, PluginAction(kind=kind, payload=payload))
                if inspect.isawaitable(value):
                    await value
            elif kind not in self.INTERNAL_ACTIONS:
                raise RuntimeError(f"没有可执行 {kind} 动作的处理器")
        except Exception as exc:
            LOGGER.error(
                "后台动作执行失败 | plugin=%s | kind=%s | %s: %s",
                plugin_id,
                kind,
                type(exc).__name__,
                exc,
            )
            with self.database.session_factory() as session:
                stored = session.get(RuntimeAction, action_id)
                if stored is not None and stored.status != "cancelled":
                    stored.status = "failed"
                    stored.error = f"{type(exc).__name__}: {exc}"[:4000]
                    session.commit()
            return

        with self.database.session_factory() as session:
            stored = session.get(RuntimeAction, action_id)
            if stored is not None and stored.status == "processing":
                stored.status = "completed"
                stored.error = ""
                session.commit()

    def _mark_cancelled(self, action_id: str) -> None:
        with self.database.session_factory() as session:
            stored = session.get(RuntimeAction, action_id)
            if stored is not None and stored.status == "processing":
                stored.status = "cancelled"
                stored.error = "用户撤回了本轮消息"
                session.commit()
