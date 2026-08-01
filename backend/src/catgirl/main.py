from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .action_executor import ActionExecutor
from .api import router
from .auth import AuthManager, ManagementAuthMiddleware, router as auth_router
from .chat_runtime import ChatRuntime
from .config import get_settings
from .database import Database
from .model_client import OpenAICompatibleClient
from .media_runtime import MediaReceiver
from .onebot import OneBotGateway
from .plugins.manager import PluginManager
from .runtime_logs import RuntimeLogStore
from .security import SecretBox


LOGGER = logging.getLogger("catgirl.app")


class FrontendStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def create_app(
    data_dir: Path | None = None,
    *,
    allow_unconfigured_management: bool = False,
) -> FastAPI:
    settings = get_settings()
    resolved_data_dir = Path(data_dir) if data_dir is not None else settings.data_dir
    log_store = RuntimeLogStore(resolved_data_dir.parent / "logs" / "catgirl.log")
    database = Database(resolved_data_dir / "catgirl.db")
    auth_manager = AuthManager(database)
    secret_box = SecretBox(resolved_data_dir / "secret.key")
    plugin_manager = PluginManager(
        database=database,
        built_in_dir=settings.built_in_plugins_dir,
        installed_dir=resolved_data_dir / "plugins",
        secret_box=secret_box,
    )
    action_executor = ActionExecutor(database)
    chat_runtime = ChatRuntime(
        database=database,
        secret_box=secret_box,
        plugin_manager=plugin_manager,
        action_executor=action_executor,
        model_client=OpenAICompatibleClient(settings.model_timeout_seconds),
    )
    action_executor.set_generation_handler(chat_runtime.generate_from_action)
    plugin_manager.intent_sink = action_executor.submit
    plugin_manager.analysis_sink = chat_runtime.generate_plugin_analysis
    plugin_manager.context_generation_sink = chat_runtime.generate_plugin_continuation
    media_receiver = MediaReceiver(
        data_dir=resolved_data_dir,
        timeout_seconds=settings.media_download_timeout_seconds,
    )
    onebot_gateway = OneBotGateway(
        database=database,
        secret_box=secret_box,
        chat_runtime=chat_runtime,
        action_executor=action_executor,
        media_receiver=media_receiver,
        allowed_media_roots=[resolved_data_dir, settings.built_in_plugins_dir],
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        log_store.install(settings.log_level)
        LOGGER.info("A Cat Girl 服务启动 | data=%s", resolved_data_dir)
        database.initialize()
        await plugin_manager.startup()
        await action_executor.startup()
        await onebot_gateway.startup()
        try:
            yield
        finally:
            await plugin_manager.shutdown()
            await onebot_gateway.shutdown()
            await action_executor.shutdown()
            LOGGER.info("A Cat Girl 服务停止")
            log_store.uninstall()

    app = FastAPI(
        title="A CAT GIRL API",
        version="1.1.0",
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.auth_manager = auth_manager
    app.state.secret_box = secret_box
    app.state.plugin_manager = plugin_manager
    app.state.action_executor = action_executor
    app.state.chat_runtime = chat_runtime
    app.state.onebot_gateway = onebot_gateway
    app.state.media_receiver = media_receiver
    app.state.log_store = log_store
    app.state.allow_unconfigured_management = allow_unconfigured_management
    app.add_middleware(ManagementAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8733", "http://localhost:8733"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "catgirl"}

    @app.websocket("/onebot/v11/ws")
    async def onebot_reverse_websocket(websocket: WebSocket):
        await onebot_gateway.websocket_endpoint(websocket)

    app.include_router(auth_router)
    app.include_router(router)

    frontend_dist = settings.frontend_dist
    if frontend_dist.is_dir():
        app.mount("/", FrontendStaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
