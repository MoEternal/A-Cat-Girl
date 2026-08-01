from __future__ import annotations

import logging
import re
from collections.abc import Generator
from time import perf_counter

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import (
    Character,
    ChatMessage,
    ConfigurationPreset,
    Conversation,
    OneBotConfig,
    PresetWorldBook,
    PromptBlock,
    PromptTemplate,
    Provider,
    UserPersona,
    WorldBook,
    WorldBookEntry,
)
from .chat_runtime import ChatRuntime, ChatRuntimeError, PromptBudgetError, RuntimeConfigurationError
from .schemas import (
    ChatMessageOut,
    CharacterCreate,
    CharacterOut,
    CharacterUpdate,
    ConfigurationPresetCreate,
    ConfigurationPresetOut,
    ConfigurationPresetUpdate,
    ConversationCreate,
    ConversationMessageUpdate,
    ConversationMessagesDelete,
    ConversationMessagesDeleteOut,
    ConversationOut,
    ConversationUpdate,
    OneBotConfigOut,
    OneBotConfigUpdate,
    OneBotStatusOut,
    PreviewMessage,
    PromptBlockCreate,
    PromptBlockOut,
    PromptBlockUpdate,
    PromptOrderUpdate,
    PromptPreviewOut,
    PromptTemplateCreate,
    PromptTemplateOut,
    PromptTemplateUpdate,
    PluginUpdate,
    PluginStateUpdate,
    PluginOrderUpdate,
    PluginAdminActionRequest,
    ProviderCreate,
    ProviderModelOut,
    ProviderModelsRequest,
    ProviderOut,
    ProviderTestOut,
    ProviderUpdate,
    RuntimeActionOut,
    RuntimeLogOut,
    RuntimeMessageRequest,
    RuntimeReplyOut,
    SillyTavernChatImportReport,
    SillyTavernImportReport,
    SillyTavernImportRequest,
    UserPersonaCreate,
    UserPersonaOut,
    UserPersonaUpdate,
    WorldBookCreate,
    WorldBookEntryCreate,
    WorldBookEntryOut,
    WorldBookEntryUpdate,
    WorldBookOut,
    WorldBookUpdate,
)
from .macro_engine import MACRO_CATALOG, MacroContext, render_macros
from .media import MediaValidationError
from .model_client import (
    ModelClientError,
    ModelConfigurationError,
    ModelHTTPError,
    provider_headers,
    provider_models_url,
)
from .onebot import OneBotGateway
from .plugins.manager import MAX_ARCHIVE_BYTES, PluginError, PluginManager
from .plugins.types import PluginEvent
from .prompt_compiler import CompiledMessage, DepthInjection, insert_at_depth
from .provider_sources import chat_completion_source_spec
from .runtime_logs import RuntimeLogStore
from .security import SecretBox
from .sillytavern_import import (
    normalize_sillytavern_character,
    normalize_sillytavern_preset,
    normalize_sillytavern_world_book,
)
from .sillytavern_chat_import import MAX_CHAT_IMPORT_BYTES, SillyTavernChatImportError
from .token_counter import count_text_tokens


router = APIRouter(prefix="/api")
VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}")
LOGGER = logging.getLogger("catgirl.api")


def get_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.database.session_factory() as session:
        yield session


def get_secret_box(request: Request) -> SecretBox:
    return request.app.state.secret_box


def get_plugin_manager(request: Request) -> PluginManager:
    return request.app.state.plugin_manager


def get_chat_runtime(request: Request) -> ChatRuntime:
    return request.app.state.chat_runtime


def get_onebot_gateway(request: Request) -> OneBotGateway:
    return request.app.state.onebot_gateway


def get_log_store(request: Request) -> RuntimeLogStore:
    return request.app.state.log_store


def onebot_config_out(config: OneBotConfig, secret_box: SecretBox) -> OneBotConfigOut:
    return OneBotConfigOut(
        id=config.id,
        enabled=config.enabled,
        connection_mode=config.connection_mode,
        reverse_ws_url=config.reverse_ws_url,
        forward_ws_url=config.forward_ws_url,
        access_token_configured=bool(config.access_token_encrypted),
        access_token_masked=secret_box.masked(config.access_token_encrypted),
        private_messages=config.private_messages,
        group_messages=config.group_messages,
        private_allowlist=config.private_allowlist or [],
        group_allowlist=config.group_allowlist or [],
        api_timeout_seconds=config.api_timeout_seconds,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def get_or_404(session: Session, model, item_id: str):
    item = session.get(model, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


def provider_out(provider: Provider, secret_box: SecretBox) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        kind=provider.kind,
        chat_completion_source=provider.chat_completion_source,
        prompt_post_processing=provider.prompt_post_processing,
        base_url=provider.base_url,
        model=provider.model,
        api_key_configured=bool(provider.api_key_encrypted),
        api_key_masked=secret_box.masked(provider.api_key_encrypted),
        priority=provider.priority,
        enabled=provider.enabled,
        is_active=provider.is_active,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def active_configuration_preset(session: Session) -> ConfigurationPreset | None:
    return session.scalar(select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True)))


def next_provider_priority(session: Session) -> int:
    return min(10000, (session.scalar(select(func.max(Provider.priority))) or 0) + 1)


def sync_resource_actives(session: Session, preset: ConfigurationPreset) -> None:
    for model, active_id in (
        (Provider, preset.provider_id),
        (PromptTemplate, preset.prompt_template_id),
        (Character, preset.character_id),
        (UserPersona, preset.user_persona_id),
    ):
        session.execute(update(model).values(is_active=False))
        if active_id:
            item = session.get(model, active_id)
            if item is not None:
                item.is_active = True
                if isinstance(item, Provider):
                    item.enabled = True


def activate_resource(session: Session, model, item_id: str, preset_field: str):
    item = get_or_404(session, model, item_id)
    session.execute(update(model).values(is_active=False))
    item.is_active = True
    if isinstance(item, Provider):
        item.enabled = True
    preset = active_configuration_preset(session)
    if preset is not None:
        setattr(preset, preset_field, item.id)
    session.commit()
    session.refresh(item)
    return item


def activate_replacement(session: Session, model):
    order = (model.priority, model.created_at) if model is Provider else (model.created_at,)
    replacement = session.scalars(select(model).order_by(*order).limit(1)).first()
    if replacement is not None:
        replacement.is_active = True
    return replacement


@router.get("/overview")
def overview(
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    active_provider = session.scalar(select(Provider).where(Provider.is_active.is_(True)))
    active_template = session.scalar(select(PromptTemplate).where(PromptTemplate.is_active.is_(True)))
    active_character = session.scalar(select(Character).where(Character.is_active.is_(True)))
    active_user_persona = session.scalar(select(UserPersona).where(UserPersona.is_active.is_(True)))
    active_preset = active_configuration_preset(session)
    active_world_book_ids = set(active_preset.world_book_ids if active_preset else [])
    for world_book in session.scalars(select(WorldBook)).unique().all():
        if world_book.scope == "global" or (
            world_book.scope == "character"
            and active_character is not None
            and world_book.character_id == active_character.id
        ):
            active_world_book_ids.add(world_book.id)
    return {
        "counts": {
            "presets": session.scalar(select(func.count()).select_from(ConfigurationPreset)) or 0,
            "world_books": session.scalar(select(func.count()).select_from(WorldBook)) or 0,
            "providers": session.scalar(select(func.count()).select_from(Provider)) or 0,
            "templates": session.scalar(select(func.count()).select_from(PromptTemplate)) or 0,
            "characters": session.scalar(select(func.count()).select_from(Character)) or 0,
            "user_personas": session.scalar(select(func.count()).select_from(UserPersona)) or 0,
        },
        "active_preset": ConfigurationPresetOut.model_validate(active_preset) if active_preset else None,
        "active_provider": provider_out(active_provider, secret_box) if active_provider else None,
        "active_template": PromptTemplateOut.model_validate(active_template) if active_template else None,
        "active_character": CharacterOut.model_validate(active_character) if active_character else None,
        "active_user_persona": UserPersonaOut.model_validate(active_user_persona) if active_user_persona else None,
        "active_world_book_ids": sorted(active_world_book_ids),
    }


@router.get("/logs", response_model=list[RuntimeLogOut])
def list_runtime_logs(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    log_store: RuntimeLogStore = Depends(get_log_store),
):
    return log_store.read(after_id=after_id, limit=limit)


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
def clear_runtime_logs(log_store: RuntimeLogStore = Depends(get_log_store)) -> None:
    log_store.clear()


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    providers = session.scalars(
        select(Provider).order_by(Provider.priority, Provider.created_at)
    ).all()
    return [provider_out(item, secret_box) for item in providers]


@router.post("/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreate,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    has_provider = session.scalar(select(Provider.id).limit(1)) is not None
    source_spec = chat_completion_source_spec(payload.chat_completion_source)
    kind = payload.kind if "kind" in payload.model_fields_set else source_spec.kind
    base_url = payload.base_url if "base_url" in payload.model_fields_set else source_spec.base_url
    provider = Provider(
        name=payload.name,
        kind=kind,
        chat_completion_source=payload.chat_completion_source,
        prompt_post_processing=payload.prompt_post_processing,
        base_url=base_url.strip().rstrip("/"),
        model=payload.model.strip(),
        api_key_encrypted=secret_box.encrypt(payload.api_key.strip()),
        priority=payload.priority or next_provider_priority(session),
        enabled=payload.enabled,
        is_active=not has_provider,
    )
    session.add(provider)
    session.flush()
    if provider.is_active:
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.provider_id = provider.id
    session.commit()
    session.refresh(provider)
    return provider_out(provider, secret_box)


@router.put("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: str,
    payload: ProviderUpdate,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    provider = get_or_404(session, Provider, provider_id)
    values = payload.model_dump(exclude_unset=True, exclude={"api_key"})
    if "chat_completion_source" in values:
        source_spec = chat_completion_source_spec(str(values["chat_completion_source"]))
        values.setdefault("kind", source_spec.kind)
        values.setdefault("base_url", source_spec.base_url)
    for key, value in values.items():
        if value is not None:
            setattr(provider, key, value.strip() if isinstance(value, str) else value)
    if "base_url" in values and provider.base_url:
        provider.base_url = provider.base_url.rstrip("/")
    if "api_key" in payload.model_fields_set:
        provider.api_key_encrypted = secret_box.encrypt((payload.api_key or "").strip())
    session.commit()
    session.refresh(provider)
    return provider_out(provider, secret_box)


@router.post("/providers/{provider_id}/activate", response_model=ProviderOut)
def activate_provider(
    provider_id: str,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    provider = activate_resource(session, Provider, provider_id, "provider_id")
    return provider_out(provider, secret_box)


@router.post("/providers/{provider_id}/export")
def export_provider(
    provider_id: str,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    provider = get_or_404(session, Provider, provider_id)
    return {
        "name": provider.name,
        "kind": provider.kind,
        "chat_completion_source": provider.chat_completion_source,
        "prompt_post_processing": provider.prompt_post_processing,
        "base_url": provider.base_url,
        "model": provider.model,
        "api_key": secret_box.decrypt(provider.api_key_encrypted),
        "priority": provider.priority,
        "enabled": provider.enabled,
    }


@router.post("/providers/{provider_id}/test", response_model=ProviderTestOut)
def test_provider(
    provider_id: str,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    provider = get_or_404(session, Provider, provider_id)
    if not provider.base_url:
        raise HTTPException(status_code=400, detail="请先填写 Base URL")
    api_key = secret_box.decrypt(provider.api_key_encrypted)
    url = provider_models_url(provider.base_url, provider.kind)
    headers = provider_headers(provider.kind, api_key, provider.chat_completion_source)
    started = perf_counter()
    try:
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=True) as client:
            response = client.get(url, headers=headers)
        latency = round((perf_counter() - started) * 1000)
        ok = 200 <= response.status_code < 300
        raw = response.text.strip()
        if api_key:
            raw = raw.replace(api_key, "[已隐藏]")
        if not ok:
            LOGGER.error(
                "供应商连接测试返回 HTTP %s | protocol=%s | %s",
                response.status_code,
                provider.kind,
                raw[:20_000] or "（空响应正文）",
            )
        return ProviderTestOut(
            ok=ok,
            status_code=response.status_code,
            latency_ms=latency,
            message="连接成功" if ok else (raw[:4_000] or f"HTTP {response.status_code}"),
        )
    except httpx.HTTPError as exc:
        latency = round((perf_counter() - started) * 1000)
        LOGGER.error("供应商连接测试失败 | protocol=%s | %s: %s", provider.kind, type(exc).__name__, exc)
        return ProviderTestOut(ok=False, latency_ms=latency, message=f"连接失败：{type(exc).__name__}")


def _model_options(payload: object, kind: str) -> tuple[list[dict[str, str]], str]:
    if isinstance(payload, list):
        values = payload
        payload = {}
    elif isinstance(payload, dict):
        values = payload.get("models") if kind == "google_gemini" else payload.get("data")
    else:
        return [], ""
    if not isinstance(values, list):
        return [], ""
    output: list[dict[str, str]] = []
    for value in values:
        if isinstance(value, str):
            model_id = value.strip()
            name = model_id
        elif isinstance(value, dict):
            methods = value.get("supportedGenerationMethods")
            if (
                kind == "google_gemini"
                and isinstance(methods, list)
                and "generateContent" not in methods
            ):
                continue
            model_id = str(value.get("id") or value.get("name") or "").strip()
            if kind == "google_gemini" and model_id.startswith("models/"):
                model_id = model_id[7:]
            name = str(
                value.get("display_name")
                or value.get("displayName")
                or value.get("name")
                or model_id
            ).strip()
            if name.startswith("models/"):
                name = name[7:]
        else:
            continue
        if model_id:
            output.append({"id": model_id[:160], "name": (name or model_id)[:240]})
    next_token = ""
    if kind == "google_gemini":
        next_token = str(payload.get("nextPageToken") or "").strip()
    elif kind == "anthropic" and payload.get("has_more"):
        next_token = str(payload.get("last_id") or "").strip()
    return output, next_token


@router.post("/providers/{provider_id}/models", response_model=list[ProviderModelOut])
def list_provider_models(
    provider_id: str,
    payload: ProviderModelsRequest,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    provider = get_or_404(session, Provider, provider_id)
    if not payload.base_url.strip():
        raise HTTPException(status_code=400, detail="请先填写 Base URL")
    api_key = (
        payload.api_key.strip()
        if payload.api_key is not None
        else secret_box.decrypt(provider.api_key_encrypted)
    )
    url = provider_models_url(payload.base_url, payload.kind)
    headers = provider_headers(payload.kind, api_key, payload.chat_completion_source)
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    page_token = ""
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=True,
        ) as client:
            for _ in range(10):
                params: dict[str, str | int] = {}
                if payload.kind == "google_gemini":
                    params["pageSize"] = 1000
                    if page_token:
                        params["pageToken"] = page_token
                elif payload.kind == "anthropic":
                    params["limit"] = 100
                    if page_token:
                        params["after_id"] = page_token
                response = client.get(url, headers=headers, params=params)
                if response.status_code >= 400:
                    raw = response.text.strip()
                    if api_key:
                        raw = raw.replace(api_key, "[已隐藏]")
                    LOGGER.error(
                        "拉取模型列表返回 HTTP %s | protocol=%s | %s",
                        response.status_code,
                        payload.kind,
                        raw[:20_000] or "（空响应正文）",
                    )
                    detail = raw
                    try:
                        body = response.json()
                        error = body.get("error") if isinstance(body, dict) else None
                        if isinstance(error, dict) and isinstance(error.get("message"), str):
                            detail = error["message"]
                    except ValueError:
                        pass
                    raise HTTPException(
                        status_code=502,
                        detail=(detail or f"HTTP {response.status_code}")[:4_000],
                    )
                try:
                    values, next_token = _model_options(response.json(), payload.kind)
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="模型列表响应不是有效 JSON") from exc
                for item in values:
                    key = item["id"].casefold()
                    if key not in seen:
                        seen.add(key)
                        options.append(item)
                if not next_token or len(options) >= 2_000:
                    break
                page_token = next_token
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        LOGGER.error("拉取模型列表失败 | protocol=%s | %s: %s", payload.kind, type(exc).__name__, exc)
        raise HTTPException(status_code=502, detail=f"拉取模型列表失败：{type(exc).__name__}") from exc
    return sorted(options[:2_000], key=lambda item: (item["name"].casefold(), item["id"].casefold()))


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(provider_id: str, session: Session = Depends(get_session)) -> None:
    provider = get_or_404(session, Provider, provider_id)
    was_active = provider.is_active
    session.delete(provider)
    session.flush()
    if was_active:
        replacement = activate_replacement(session, Provider)
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.provider_id = replacement.id if replacement else None
    session.commit()


@router.get("/prompt-templates", response_model=list[PromptTemplateOut])
def list_templates(session: Session = Depends(get_session)):
    return session.scalars(select(PromptTemplate).order_by(PromptTemplate.created_at)).unique().all()


@router.post("/prompt-templates", response_model=PromptTemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(payload: PromptTemplateCreate, session: Session = Depends(get_session)):
    has_template = session.scalar(select(PromptTemplate.id).limit(1)) is not None
    template = PromptTemplate(
        name=payload.name,
        description=payload.description,
        is_active=not has_template,
    )
    template.blocks.append(PromptBlock(title="系统提示", role="system", position=0))
    session.add(template)
    session.flush()
    if template.is_active:
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.prompt_template_id = template.id
    session.commit()
    session.refresh(template)
    return template


@router.put("/prompt-templates/{template_id}", response_model=PromptTemplateOut)
def update_template(
    template_id: str,
    payload: PromptTemplateUpdate,
    session: Session = Depends(get_session),
):
    template = get_or_404(session, PromptTemplate, template_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(template, key, value)
    session.commit()
    session.refresh(template)
    return template


@router.post("/prompt-templates/{template_id}/activate", response_model=PromptTemplateOut)
def activate_template(template_id: str, session: Session = Depends(get_session)):
    return activate_resource(session, PromptTemplate, template_id, "prompt_template_id")


@router.delete("/prompt-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, session: Session = Depends(get_session)) -> None:
    template = get_or_404(session, PromptTemplate, template_id)
    was_active = template.is_active
    session.delete(template)
    session.flush()
    if was_active:
        replacement = activate_replacement(session, PromptTemplate)
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.prompt_template_id = replacement.id if replacement else None
    session.commit()


@router.post(
    "/prompt-templates/{template_id}/blocks",
    response_model=PromptBlockOut,
    status_code=status.HTTP_201_CREATED,
)
def create_block(
    template_id: str,
    payload: PromptBlockCreate,
    session: Session = Depends(get_session),
):
    get_or_404(session, PromptTemplate, template_id)
    last_position = session.scalar(
        select(func.max(PromptBlock.position)).where(PromptBlock.template_id == template_id)
    )
    block = PromptBlock(
        template_id=template_id,
        title=payload.title,
        role=payload.role,
        content=payload.content,
        enabled=payload.enabled,
        stashed=payload.stashed,
        identifier=payload.identifier,
        marker=payload.marker,
        injection_position=payload.injection_position,
        injection_depth=payload.injection_depth,
        injection_order=payload.injection_order,
        position=(last_position if last_position is not None else -1) + 1,
    )
    session.add(block)
    session.commit()
    session.refresh(block)
    return block


@router.put("/prompt-blocks/{block_id}", response_model=PromptBlockOut)
def update_block(
    block_id: str,
    payload: PromptBlockUpdate,
    session: Session = Depends(get_session),
):
    block = get_or_404(session, PromptBlock, block_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(block, key, value)
    session.commit()
    session.refresh(block)
    return block


@router.delete("/prompt-blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_block(block_id: str, session: Session = Depends(get_session)) -> None:
    block = get_or_404(session, PromptBlock, block_id)
    template_id = block.template_id
    session.delete(block)
    session.flush()
    blocks = session.scalars(
        select(PromptBlock).where(PromptBlock.template_id == template_id).order_by(PromptBlock.position)
    ).all()
    for position, item in enumerate(blocks):
        item.position = position
    session.commit()


@router.put("/prompt-templates/{template_id}/blocks/order", response_model=list[PromptBlockOut])
def reorder_blocks(
    template_id: str,
    payload: PromptOrderUpdate,
    session: Session = Depends(get_session),
):
    get_or_404(session, PromptTemplate, template_id)
    blocks = session.scalars(select(PromptBlock).where(PromptBlock.template_id == template_id)).all()
    by_id = {block.id: block for block in blocks}
    if len(payload.block_ids) != len(set(payload.block_ids)) or set(payload.block_ids) != set(by_id):
        raise HTTPException(status_code=400, detail="排序列表必须包含该模板的全部提示词块且不能重复")
    for position, block_id in enumerate(payload.block_ids):
        by_id[block_id].position = position
    session.commit()
    return [by_id[block_id] for block_id in payload.block_ids]


def render_dynamic_marker(
    identifier: str | None,
    character: Character | None,
    user_persona: UserPersona | None,
    world_entries: list[WorldBookEntry],
) -> str:
    if identifier == "charDescription":
        return character.summary if character else ""
    if identifier == "charPersonality":
        return character.persona if character else ""
    if identifier == "scenario":
        return character.scenario if character else ""
    if identifier == "dialogueExamples":
        return character.first_message if character else ""
    if identifier == "personaDescription":
        if user_persona and user_persona.injection_position == 0:
            return user_persona.description
        return ""
    if identifier == "chatHistory":
        return "[聊天历史将在运行时插入]"
    if identifier in {"worldInfoBefore", "worldInfoAfter"}:
        target_position = 0 if identifier == "worldInfoBefore" else 1
        matching = [entry for entry in world_entries if entry.enabled and entry.position == target_position]
        constant = [entry.content for entry in matching if entry.constant and entry.content]
        conditional_count = sum(not entry.constant for entry in matching)
        parts = constant
        if conditional_count:
            parts.append(f"[{conditional_count} 条世界书内容将在运行时按关键词激活]")
        return "\n".join(parts)
    return ""


@router.get("/prompt-templates/{template_id}/preview", response_model=PromptPreviewOut)
async def preview_template(
    template_id: str,
    character_id: str | None = Query(default=None),
    session: Session = Depends(get_session),
    manager: PluginManager = Depends(get_plugin_manager),
):
    template = get_or_404(session, PromptTemplate, template_id)
    if character_id:
        character = get_or_404(session, Character, character_id)
    else:
        character = session.scalar(select(Character).where(Character.is_active.is_(True)))
    preset = session.scalar(
        select(ConfigurationPreset)
        .where(ConfigurationPreset.prompt_template_id == template_id)
        .order_by(ConfigurationPreset.is_active.desc(), ConfigurationPreset.created_at)
    )
    world_entries: list[WorldBookEntry] = []
    selected_book_ids: set[str] = set()
    if preset is not None:
        for link in preset.world_book_links:
            if not link.enabled:
                continue
            world_book = session.get(WorldBook, link.world_book_id)
            if world_book is not None:
                selected_book_ids.add(world_book.id)
                world_entries.extend(world_book.entries)
    for world_book in session.scalars(select(WorldBook).order_by(WorldBook.created_at)).unique():
        scoped = world_book.scope == "global" or (
            world_book.scope == "character"
            and character is not None
            and world_book.character_id == character.id
        )
        if scoped and world_book.id not in selected_book_ids:
            selected_book_ids.add(world_book.id)
            world_entries.extend(world_book.entries)
    if preset and preset.user_persona_id:
        user_persona = session.get(UserPersona, preset.user_persona_id)
    else:
        user_persona = session.scalar(select(UserPersona).where(UserPersona.is_active.is_(True)))
    provider = session.get(Provider, preset.provider_id) if preset and preset.provider_id else None
    conversation = session.scalar(
        select(Conversation)
        .where(Conversation.is_active.is_(True))
        .order_by(Conversation.updated_at.desc())
    )
    all_history_rows = []
    if conversation is not None:
        all_history_rows = list(
            session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.status == "complete",
                    ChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(ChatMessage.position)
            ).all()
        )
    last_user = next(
        (message.content for message in reversed(all_history_rows) if message.role == "user"),
        "",
    )
    preview_actions = await manager.preview_prompt_actions(
        PluginEvent(
            name="before_prompt_compile",
            conversation_id=conversation.external_id if conversation is not None else "preview",
            text=last_user,
            metadata={
                "source": "preview",
                "preview": True,
                "record_id": conversation.id if conversation is not None else "",
                "character_id": character.id if character is not None else "",
            },
        )
    )
    history_exclude_through = max(
        (
            int(item["action"].payload.get("exclude_through_position", -1))
            for item in preview_actions
            if item["action"].kind == "history_filter"
            and isinstance(item["action"].payload.get("exclude_through_position"), int)
        ),
        default=-1,
    )
    history_rows = [
        message for message in all_history_rows if message.position > history_exclude_through
    ]
    model = provider.model if provider else ""
    history_token_count = sum(count_text_tokens(message.content, model) for message in history_rows)
    macro_context = MacroContext(
        user_name=user_persona.name if user_persona else "用户",
        user_persona=user_persona.description if user_persona else "",
        character_name=character.name if character else "当前角色",
        character_description=character.summary if character else "",
        character_personality=character.persona if character else "",
        character_scenario=character.scenario if character else "",
        character_first_message=character.first_message if character else "",
        model=provider.model if provider else "",
        max_context_tokens=preset.context_length if preset else 128000,
        max_response_tokens=preset.max_response_tokens if preset else 2048,
        max_prompt_tokens=(preset.context_length - preset.max_response_tokens) if preset else 125952,
        message_count=len(history_rows),
    )
    compiled: list[CompiledMessage] = []
    injections: list[DepthInjection] = []
    preview_by_source: dict[str, PreviewMessage] = {}
    unresolved: set[str] = set()
    history_inserted = False
    for block in sorted(template.blocks, key=lambda item: item.position):
        if not block.enabled or block.stashed:
            continue
        if block.marker and block.identifier == "chatHistory":
            history_inserted = True
            history_title = "聊天历史"
            if conversation is not None and conversation.title.strip():
                history_title = f"{history_title} · {conversation.title.strip()}"
            preview_by_source["chatHistory"] = PreviewMessage(
                block_id=block.id,
                title=history_title,
                role=block.role,
                content="",
                kind="history",
                insertion_label="动态标记位置",
                content_visible=False,
                marker=False,
                identifier=block.identifier,
                injection_position=block.injection_position,
                injection_depth=block.injection_depth,
                injection_order=block.injection_order,
                token_count=history_token_count,
            )
            compiled.append(CompiledMessage(block.role, "[chat-history-preview]", "chatHistory"))
            continue
        source_content = render_dynamic_marker(block.identifier, character, user_persona, world_entries) if block.marker else block.content
        rendered = render_macros(source_content, macro_context)
        content = rendered.content
        unresolved.update(rendered.unresolved)
        if not content:
            continue
        source = f"template:{block.id}"
        preview_by_source[source] = PreviewMessage(
            block_id=block.id,
            title=block.title,
            role=block.role,
            content=content,
            kind="template",
            insertion_label=(
                f"聊天深度 {block.injection_depth} · 顺序 {block.injection_order}"
                if block.injection_position == 1
                else ""
            ),
            marker=block.marker,
            identifier=block.identifier,
            injection_position=block.injection_position,
            injection_depth=block.injection_depth,
            injection_order=block.injection_order,
            token_count=count_text_tokens(content, model),
        )
        if block.injection_position == 1:
            injections.append(
                DepthInjection(
                    role=block.role,
                    content=content,
                    depth=block.injection_depth,
                    order=block.injection_order,
                    source=source,
                )
            )
        else:
            compiled.append(CompiledMessage(block.role, content, source))
    if not history_inserted and conversation is not None:
        preview_by_source["chatHistory"] = PreviewMessage(
            block_id="chat-history:auto",
            title=f"聊天历史 · {conversation.title.strip() or '当前记录'}",
            role="user",
            content="",
            kind="history",
            insertion_label="模板末尾（运行时自动追加）",
            content_visible=False,
            token_count=history_token_count,
        )
        compiled.append(CompiledMessage("user", "[chat-history-preview]", "chatHistory"))
    if user_persona and user_persona.description and user_persona.injection_position in {2, 3, 4}:
        position_name = {2: "用户人设（作者注释顶部）", 3: "用户人设（作者注释底部）", 4: "用户人设（指定深度）"}[user_persona.injection_position]
        source = f"userPersona:{user_persona.id}"
        persona_message = PreviewMessage(
            block_id=f"user-persona:{user_persona.id}",
            title=position_name,
            role=user_persona.role,
            content=render_macros(user_persona.description, macro_context).content,
            kind="persona",
            insertion_label=(
                f"聊天深度 {user_persona.injection_depth} · 顺序 100"
                if user_persona.injection_position == 4
                else ("提示词顶部" if user_persona.injection_position == 2 else "提示词末尾")
            ),
            injection_position=1 if user_persona.injection_position == 4 else 0,
            injection_depth=user_persona.injection_depth,
            injection_order=100,
        )
        persona_message.token_count = count_text_tokens(
            persona_message.content,
            model,
        )
        preview_by_source[source] = persona_message
        if user_persona.injection_position == 2:
            compiled.insert(0, CompiledMessage(user_persona.role, persona_message.content, source))
        elif user_persona.injection_position == 4:
            injections.append(
                DepthInjection(
                    role=user_persona.role,
                    content=persona_message.content,
                    depth=user_persona.injection_depth,
                    order=100,
                    source=source,
                )
            )
        else:
            compiled.append(CompiledMessage(user_persona.role, persona_message.content, source))
    compiled = insert_at_depth(compiled, injections)

    plugin_names: dict[str, str] = {}
    plugin_notes: dict[str, str] = {}
    additions: list[dict[str, object]] = []
    for item in preview_actions:
        action = item["action"]
        if action.kind != "prompt_addition":
            continue
        plugin_id = str(item["plugin_id"])
        plugin_names[plugin_id] = str(item["plugin_name"])
        payload = dict(action.payload)
        payload["_compiled_source"] = plugin_id
        note = str(payload.get("preview_note") or "").strip()
        if note:
            plugin_notes[plugin_id] = note
        additions.append(payload)
    compiled = ChatRuntime._insert_additions(compiled, additions, macro_context)

    messages: list[PreviewMessage] = []
    for index, message in enumerate(compiled):
        existing = preview_by_source.get(message.source)
        if existing is not None:
            messages.append(existing)
            continue
        if not message.source.startswith("pluginAddition"):
            continue
        source_parts = message.source.split(":", 3)
        plugin_id = source_parts[1] if len(source_parts) > 1 else ""
        depth = source_parts[2] if len(source_parts) > 3 else ""
        insertion_label = f"聊天深度 {depth}" if depth else (
            "最新用户消息前"
            if any(history.role == "user" for history in history_rows)
            else "提示词末尾"
        )
        if plugin_notes.get(plugin_id):
            insertion_label = f"{insertion_label} · {plugin_notes[plugin_id]}"
        messages.append(
            PreviewMessage(
                block_id=f"plugin:{plugin_id or 'unknown'}:{index}",
                title=f"插件 · {plugin_names.get(plugin_id, plugin_id or '未知插件')}",
                role=message.role,
                content=message.content,
                kind="plugin",
                plugin_id=plugin_id or None,
                insertion_label=insertion_label,
                injection_position=1 if depth else 2,
                injection_depth=int(depth) if depth.isdigit() else 0,
                token_count=count_text_tokens(message.content, model),
            )
        )
    return PromptPreviewOut(
        template_id=template.id,
        template_name=template.name,
        character_id=character.id if character else None,
        character_name=character.name if character else None,
        user_persona_id=user_persona.id if user_persona else None,
        user_persona_name=user_persona.name if user_persona else None,
        messages=messages,
        total_tokens=sum(message.token_count for message in messages),
        unresolved_variables=sorted(unresolved),
        supported_macros=MACRO_CATALOG,
    )


@router.get("/characters", response_model=list[CharacterOut])
def list_characters(session: Session = Depends(get_session)):
    return session.scalars(select(Character).order_by(Character.created_at)).all()


def set_character_world_books(
    session: Session,
    character: Character,
    world_book_ids: list[str],
) -> None:
    if len(world_book_ids) != len(set(world_book_ids)):
        raise HTTPException(status_code=400, detail="角色关联世界书不能包含重复项")
    selected = set(world_book_ids)
    for world_book in session.scalars(select(WorldBook)).unique().all():
        if world_book.id in selected:
            world_book.scope = "character"
            world_book.character_id = character.id
        elif world_book.character_id == character.id:
            world_book.character_id = None
    missing = selected - set(session.scalars(select(WorldBook.id)).all())
    if missing:
        raise HTTPException(status_code=400, detail="角色关联了不存在的世界书")


@router.post("/characters", response_model=CharacterOut, status_code=status.HTTP_201_CREATED)
def create_character(payload: CharacterCreate, session: Session = Depends(get_session)):
    has_character = session.scalar(select(Character.id).limit(1)) is not None
    values = payload.model_dump()
    world_book_ids = values.pop("world_book_ids")
    character = Character(**values, is_active=not has_character)
    session.add(character)
    session.flush()
    set_character_world_books(session, character, world_book_ids)
    if character.is_active:
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.character_id = character.id
    session.commit()
    session.refresh(character)
    return character


@router.put("/characters/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    session: Session = Depends(get_session),
):
    character = get_or_404(session, Character, character_id)
    values = payload.model_dump(exclude_unset=True)
    world_book_ids = values.pop("world_book_ids", None)
    for key, value in values.items():
        if value is not None:
            setattr(character, key, value)
    if world_book_ids is not None:
        set_character_world_books(session, character, world_book_ids)
    session.commit()
    session.refresh(character)
    return character


@router.post("/characters/{character_id}/activate", response_model=CharacterOut)
def activate_character(character_id: str, session: Session = Depends(get_session)):
    return activate_resource(session, Character, character_id, "character_id")


@router.delete("/characters/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_character(character_id: str, session: Session = Depends(get_session)) -> None:
    character = get_or_404(session, Character, character_id)
    was_active = character.is_active
    session.delete(character)
    session.flush()
    if was_active:
        replacement = activate_replacement(session, Character)
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.character_id = replacement.id if replacement else None
    session.commit()


@router.get("/user-personas", response_model=list[UserPersonaOut])
def list_user_personas(session: Session = Depends(get_session)):
    return session.scalars(select(UserPersona).order_by(UserPersona.created_at)).all()


@router.post("/user-personas", response_model=UserPersonaOut, status_code=status.HTTP_201_CREATED)
def create_user_persona(payload: UserPersonaCreate, session: Session = Depends(get_session)):
    has_persona = session.scalar(select(UserPersona.id).limit(1)) is not None
    persona = UserPersona(**payload.model_dump(), is_active=not has_persona)
    session.add(persona)
    session.flush()
    if persona.is_active:
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.user_persona_id = persona.id
    session.commit()
    session.refresh(persona)
    return persona


@router.put("/user-personas/{persona_id}", response_model=UserPersonaOut)
def update_user_persona(
    persona_id: str,
    payload: UserPersonaUpdate,
    session: Session = Depends(get_session),
):
    persona = get_or_404(session, UserPersona, persona_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(persona, key, value)
    session.commit()
    session.refresh(persona)
    return persona


@router.post("/user-personas/{persona_id}/activate", response_model=UserPersonaOut)
def activate_user_persona(persona_id: str, session: Session = Depends(get_session)):
    return activate_resource(session, UserPersona, persona_id, "user_persona_id")


@router.delete("/user-personas/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_persona(persona_id: str, session: Session = Depends(get_session)) -> None:
    persona = get_or_404(session, UserPersona, persona_id)
    was_active = persona.is_active
    session.delete(persona)
    session.flush()
    if was_active:
        replacement = activate_replacement(session, UserPersona)
        preset = active_configuration_preset(session)
        if preset is not None:
            preset.user_persona_id = replacement.id if replacement else None
    session.commit()


PRESET_RESOURCE_FIELDS = {
    "provider_id": Provider,
    "prompt_template_id": PromptTemplate,
    "character_id": Character,
    "user_persona_id": UserPersona,
}


def set_preset_world_books(
    session: Session,
    preset: ConfigurationPreset,
    world_book_ids: list[str],
) -> None:
    if len(world_book_ids) != len(set(world_book_ids)):
        raise HTTPException(status_code=400, detail="世界书列表不能包含重复项")
    for world_book_id in world_book_ids:
        if session.get(WorldBook, world_book_id) is None:
            raise HTTPException(status_code=400, detail="预设引用的世界书不存在")
    preset.world_book_links.clear()
    for position, world_book_id in enumerate(world_book_ids):
        preset.world_book_links.append(
            PresetWorldBook(world_book_id=world_book_id, position=position, enabled=True)
        )


def validate_preset_resources(session: Session, values: dict) -> None:
    for field, model in PRESET_RESOURCE_FIELDS.items():
        if field not in values:
            continue
        item_id = values[field]
        if item_id is not None and session.get(model, item_id) is None:
            raise HTTPException(status_code=400, detail=f"{field} 引用的资源不存在")


def validate_preset_limits(values: dict) -> None:
    if not values["max_context_unlocked"] and values["context_length"] > 200_000:
        raise HTTPException(status_code=400, detail="未解锁上下文长度时不能超过 200,000")
    if values["max_response_tokens"] > values["context_length"]:
        raise HTTPException(status_code=400, detail="最大回复长度不能大于上下文长度")


@router.get("/presets", response_model=list[ConfigurationPresetOut])
def list_configuration_presets(session: Session = Depends(get_session)):
    return session.scalars(select(ConfigurationPreset).order_by(ConfigurationPreset.created_at)).all()


@router.post("/presets", response_model=ConfigurationPresetOut, status_code=status.HTTP_201_CREATED)
def create_configuration_preset(
    payload: ConfigurationPresetCreate,
    session: Session = Depends(get_session),
):
    values = payload.model_dump()
    world_book_ids = values.pop("world_book_ids")
    active = active_configuration_preset(session)
    for field in PRESET_RESOURCE_FIELDS:
        if values[field] is None and active is not None:
            values[field] = getattr(active, field)
    validate_preset_resources(session, values)
    validate_preset_limits(values)
    has_preset = session.scalar(select(ConfigurationPreset.id).limit(1)) is not None
    preset = ConfigurationPreset(**values, is_active=not has_preset)
    session.add(preset)
    session.flush()
    set_preset_world_books(session, preset, world_book_ids)
    if preset.is_active:
        sync_resource_actives(session, preset)
    session.commit()
    session.refresh(preset)
    return preset


@router.put("/presets/{preset_id}", response_model=ConfigurationPresetOut)
def update_configuration_preset(
    preset_id: str,
    payload: ConfigurationPresetUpdate,
    session: Session = Depends(get_session),
):
    preset = get_or_404(session, ConfigurationPreset, preset_id)
    values = payload.model_dump(exclude_unset=True)
    world_book_ids = values.pop("world_book_ids", None)
    validate_preset_resources(session, values)
    merged_values = {
        field: values.get(field, getattr(preset, field))
        for field in ("max_context_unlocked", "context_length", "max_response_tokens")
    }
    validate_preset_limits(merged_values)
    for key, value in values.items():
        setattr(preset, key, value)
    if world_book_ids is not None:
        set_preset_world_books(session, preset, world_book_ids)
    if preset.is_active:
        sync_resource_actives(session, preset)
    session.commit()
    session.refresh(preset)
    return preset


@router.post("/presets/{preset_id}/activate", response_model=ConfigurationPresetOut)
def activate_configuration_preset(preset_id: str, session: Session = Depends(get_session)):
    preset = get_or_404(session, ConfigurationPreset, preset_id)
    session.execute(update(ConfigurationPreset).values(is_active=False))
    preset.is_active = True
    sync_resource_actives(session, preset)
    session.commit()
    session.refresh(preset)
    return preset


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_configuration_preset(preset_id: str, session: Session = Depends(get_session)) -> None:
    preset = get_or_404(session, ConfigurationPreset, preset_id)
    was_active = preset.is_active
    session.delete(preset)
    session.flush()
    if was_active:
        replacement = activate_replacement(session, ConfigurationPreset)
        if replacement is not None:
            sync_resource_actives(session, replacement)
    session.commit()


@router.get("/world-books", response_model=list[WorldBookOut])
def list_world_books(session: Session = Depends(get_session)):
    return session.scalars(select(WorldBook).order_by(WorldBook.created_at)).unique().all()


@router.post("/world-books", response_model=WorldBookOut, status_code=status.HTTP_201_CREATED)
def create_world_book(payload: WorldBookCreate, session: Session = Depends(get_session)):
    character_id = payload.character_id
    if payload.scope == "character" and not character_id:
        active_character = session.scalar(select(Character).where(Character.is_active.is_(True)))
        character_id = active_character.id if active_character else None
    if character_id and session.get(Character, character_id) is None:
        raise HTTPException(status_code=400, detail="世界书关联的角色不存在")
    world_book = WorldBook(
        name=payload.name,
        description=payload.description,
        scope=payload.scope,
        character_id=character_id if payload.scope == "character" else None,
    )
    session.add(world_book)
    session.commit()
    session.refresh(world_book)
    return world_book


@router.put("/world-books/{world_book_id}", response_model=WorldBookOut)
def update_world_book(
    world_book_id: str,
    payload: WorldBookUpdate,
    session: Session = Depends(get_session),
):
    world_book = get_or_404(session, WorldBook, world_book_id)
    values = payload.model_dump(exclude_unset=True)
    target_scope = values.get("scope", world_book.scope)
    target_character_id = values.get("character_id", world_book.character_id)
    if target_scope == "global":
        target_character_id = None
    elif target_character_id is None:
        active_character = session.scalar(select(Character).where(Character.is_active.is_(True)))
        target_character_id = active_character.id if active_character else None
    if target_character_id and session.get(Character, target_character_id) is None:
        raise HTTPException(status_code=400, detail="世界书关联的角色不存在")
    values["scope"] = target_scope
    values["character_id"] = target_character_id
    for key, value in values.items():
        if key == "character_id" or value is not None:
            setattr(world_book, key, value)
    session.commit()
    session.refresh(world_book)
    return world_book


@router.delete("/world-books/{world_book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_world_book(world_book_id: str, session: Session = Depends(get_session)) -> None:
    world_book = get_or_404(session, WorldBook, world_book_id)
    session.delete(world_book)
    session.commit()


@router.post(
    "/world-books/{world_book_id}/entries",
    response_model=WorldBookEntryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_world_book_entry(
    world_book_id: str,
    payload: WorldBookEntryCreate,
    session: Session = Depends(get_session),
):
    get_or_404(session, WorldBook, world_book_id)
    max_uid = session.scalar(
        select(func.max(WorldBookEntry.uid)).where(WorldBookEntry.world_book_id == world_book_id)
    )
    entry = WorldBookEntry(
        world_book_id=world_book_id,
        uid=(max_uid if max_uid is not None else -1) + 1,
        **payload.model_dump(),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.put("/world-book-entries/{entry_id}", response_model=WorldBookEntryOut)
def update_world_book_entry(
    entry_id: str,
    payload: WorldBookEntryUpdate,
    session: Session = Depends(get_session),
):
    entry = get_or_404(session, WorldBookEntry, entry_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(entry, key, value)
    session.commit()
    session.refresh(entry)
    return entry


@router.delete("/world-book-entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_world_book_entry(entry_id: str, session: Session = Depends(get_session)) -> None:
    entry = get_or_404(session, WorldBookEntry, entry_id)
    session.delete(entry)
    session.commit()


def unique_resource_name(session: Session, model, requested: str) -> str:
    existing = set(session.scalars(select(model.name)).all())
    if requested not in existing:
        return requested
    index = 2
    while f"{requested} ({index})" in existing:
        index += 1
    return f"{requested} ({index})"


@router.post("/import/sillytavern", response_model=SillyTavernImportReport)
def import_sillytavern_bundle(
    payload: SillyTavernImportRequest,
    session: Session = Depends(get_session),
):
    report = SillyTavernImportReport()
    imported_world_book_ids: list[str] = []
    imported_character_ids: list[str] = []

    def store_world_book(normalized_book, character_id: str | None = None) -> str:
        book_name = unique_resource_name(session, WorldBook, normalized_book.name)
        world_book = WorldBook(
            name=book_name,
            description=normalized_book.description,
            source_format=normalized_book.source_format,
            raw_data=normalized_book.raw_data,
            scope="character",
            character_id=character_id,
        )
        session.add(world_book)
        session.flush()
        for entry_values in normalized_book.entries:
            world_book.entries.append(WorldBookEntry(**entry_values))
        imported_world_book_ids.append(world_book.id)
        report.imported_world_entries += len(normalized_book.entries)
        report.warnings.extend(normalized_book.warnings)
        return world_book.id

    for named_character in payload.characters:
        try:
            normalized_character = normalize_sillytavern_character(
                named_character.name,
                named_character.data,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"角色卡 {named_character.name} 导入失败：{exc}",
            ) from exc
        character = Character(
            name=unique_resource_name(session, Character, normalized_character.name),
            summary=normalized_character.summary,
            persona=normalized_character.persona,
            scenario=normalized_character.scenario,
            first_message=normalized_character.first_message,
            is_active=False,
        )
        session.add(character)
        session.flush()
        imported_character_ids.append(character.id)
        report.imported_characters += 1
        report.warnings.extend(normalized_character.warnings)
        if normalized_character.embedded_world_book is not None:
            store_world_book(normalized_character.embedded_world_book, character.id)

    import_character_id = (
        imported_character_ids[0]
        if imported_character_ids
        else payload.character_id
    )
    if import_character_id is None:
        active_character = session.scalar(select(Character).where(Character.is_active.is_(True)))
        import_character_id = active_character.id if active_character else None
    if import_character_id and session.get(Character, import_character_id) is None:
        raise HTTPException(status_code=400, detail="导入时指定的人设不存在")

    for named_book in payload.world_books:
        try:
            normalized_book = normalize_sillytavern_world_book(named_book.name, named_book.data)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"世界书 {named_book.name} 导入失败：{exc}") from exc
        store_world_book(normalized_book, import_character_id)

    report.world_book_ids = imported_world_book_ids
    report.character_ids = imported_character_ids

    if payload.preset is not None:
        try:
            normalized_preset = normalize_sillytavern_preset(payload.preset.name, payload.preset.data)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"预设导入失败：{exc}") from exc

        provider = session.scalar(
            select(Provider).where(
                Provider.chat_completion_source == normalized_preset.provider_source,
                Provider.prompt_post_processing
                == normalized_preset.provider_prompt_post_processing,
                Provider.base_url == normalized_preset.provider_base_url,
                Provider.model == normalized_preset.provider_model,
            )
        )
        if provider is None:
            provider = Provider(
                name=unique_resource_name(session, Provider, normalized_preset.provider_name),
                kind=normalized_preset.provider_kind,
                chat_completion_source=normalized_preset.provider_source,
                prompt_post_processing=normalized_preset.provider_prompt_post_processing,
                base_url=normalized_preset.provider_base_url,
                model=normalized_preset.provider_model,
                priority=next_provider_priority(session),
                enabled=True,
                is_active=False,
            )
            session.add(provider)
            session.flush()

        template = PromptTemplate(
            name=unique_resource_name(session, PromptTemplate, normalized_preset.name),
            description="通过兼容格式导入",
            is_active=False,
        )
        session.add(template)
        session.flush()
        for block_values in normalized_preset.blocks:
            template.blocks.append(PromptBlock(**block_values))

        character = session.get(Character, payload.character_id) if payload.character_id else None
        if payload.character_id and character is None:
            raise HTTPException(status_code=400, detail="导入时指定的人设不存在")
        if character is None and imported_character_ids:
            character = session.get(Character, imported_character_ids[0])
            if len(imported_character_ids) > 1:
                report.warnings.append("同批导入了多张角色卡；预设已关联第一张")
        if character is None:
            character = session.scalar(select(Character).where(Character.is_active.is_(True)))
        user_persona = session.scalar(select(UserPersona).where(UserPersona.is_active.is_(True)))

        preset = ConfigurationPreset(
            name=unique_resource_name(session, ConfigurationPreset, normalized_preset.name),
            description="通过兼容格式导入",
            provider_id=provider.id,
            prompt_template_id=template.id,
            character_id=character.id if character else None,
            user_persona_id=user_persona.id if user_persona else None,
            is_active=False,
            **normalized_preset.settings,
        )
        session.add(preset)
        session.flush()
        set_preset_world_books(session, preset, imported_world_book_ids)

        should_activate = payload.activate or active_configuration_preset(session) is None
        if should_activate:
            session.execute(update(ConfigurationPreset).values(is_active=False))
            preset.is_active = True
            sync_resource_actives(session, preset)

        report.preset_id = preset.id
        report.preset_name = preset.name
        report.prompt_template_id = template.id
        report.provider_id = provider.id
        report.imported_prompt_blocks = len(normalized_preset.blocks)
        report.warnings.extend(normalized_preset.warnings)
        if not imported_world_book_ids and any(
            block["identifier"] in {"worldInfoBefore", "worldInfoAfter"}
            for block in normalized_preset.blocks
        ):
            report.warnings.append("本次未选择世界书文件，世界书插槽已保留但没有正文")

    if payload.preset is None and payload.activate and imported_character_ids:
        character = session.get(Character, imported_character_ids[0])
        session.execute(update(Character).values(is_active=False))
        character.is_active = True
        active_preset = active_configuration_preset(session)
        if active_preset is not None:
            active_preset.character_id = character.id
            if imported_world_book_ids:
                set_preset_world_books(session, active_preset, imported_world_book_ids)

    if payload.preset is None and not imported_world_book_ids and not imported_character_ids:
        raise HTTPException(status_code=400, detail="没有可导入的预设、角色卡或世界书")

    session.commit()
    return report


@router.get("/plugins")
def list_plugins(manager: PluginManager = Depends(get_plugin_manager)):
    return manager.list_plugins()


@router.put("/plugins/order")
def reorder_plugins(
    payload: PluginOrderUpdate,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        manager.reorder(payload.plugin_ids)
    except PluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return manager.list_plugins()


@router.get("/plugins/{plugin_id}/assets/{asset_path:path}")
def get_plugin_admin_asset(
    plugin_id: str,
    asset_path: str,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        return FileResponse(manager.resolve_admin_asset(plugin_id, asset_path))
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/plugins/{plugin_id}/admin-actions/{action}")
async def run_plugin_admin_action(
    plugin_id: str,
    action: str,
    payload: PluginAdminActionRequest,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        return {"result": await manager.admin_action(plugin_id, action, payload.payload)}
    except (PluginError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/plugins/web_search/models", response_model=list[ProviderModelOut])
def list_web_search_models(
    payload: PluginAdminActionRequest,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        settings = manager.get_settings("web_search")
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    base_url = str(
        payload.payload.get("base_url") or settings.get("search_model_base_url", "")
    ).strip()
    if not base_url:
        raise HTTPException(status_code=400, detail="请先填写搜索模型 API 地址")
    api_key = str(
        payload.payload.get("api_key") or settings.get("search_model_api_key", "")
    ).strip()
    url = provider_models_url(base_url, "openai_compatible")
    headers = provider_headers("openai_compatible", api_key)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=True,
        ) as client:
            response = client.get(url, headers=headers)
        if response.status_code >= 400:
            raw = response.text.strip()
            if api_key:
                raw = raw.replace(api_key, "[已隐藏]")
            raise HTTPException(
                status_code=502,
                detail=(raw or f"HTTP {response.status_code}")[:4000],
            )
        try:
            options, _next_token = _model_options(response.json(), "openai_compatible")
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="模型列表响应不是有效 JSON") from exc
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        LOGGER.error("拉取搜索模型列表失败 | %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502, detail=f"拉取模型列表失败：{type(exc).__name__}") from exc
    unique: dict[str, dict[str, str]] = {}
    for item in options:
        unique.setdefault(item["id"].casefold(), item)
    return sorted(
        unique.values(),
        key=lambda item: (item["name"].casefold(), item["id"].casefold()),
    )[:2000]


@router.get("/plugins/{plugin_id}/state")
def get_plugin_state(
    plugin_id: str,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        return {"state": manager.get_state(plugin_id)}
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/plugins/{plugin_id}/state")
def replace_plugin_state(
    plugin_id: str,
    payload: PluginStateUpdate,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        manager.set_state(plugin_id, payload.state)
        return {"state": manager.get_state(plugin_id)}
    except (PluginError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/plugins/{plugin_id}/conversation-states")
def inspect_plugin_conversation_states(
    plugin_id: str,
    conversation_id: str | None = Query(default=None, min_length=1, max_length=200),
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        return manager.inspect_conversation_states(plugin_id, conversation_id)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/plugins/{plugin_id}")
async def configure_plugin(
    plugin_id: str,
    payload: PluginUpdate,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        await manager.configure(plugin_id, enabled=payload.enabled, settings=payload.settings)
    except PluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return next(item for item in manager.list_plugins() if item["id"] == plugin_id)


@router.post("/plugins/install", status_code=status.HTTP_201_CREATED)
async def install_plugin(
    request: Request,
    manager: PluginManager = Depends(get_plugin_manager),
):
    archive = bytearray()
    async for chunk in request.stream():
        archive.extend(chunk)
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise HTTPException(status_code=413, detail="插件压缩包超过 32 MB")
    try:
        plugin_id = await manager.install_zip(bytes(archive))
    except PluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return next(item for item in manager.list_plugins() if item["id"] == plugin_id)


@router.post("/plugins/{plugin_id}/reload")
async def reload_plugin(
    plugin_id: str,
    manager: PluginManager = Depends(get_plugin_manager),
):
    try:
        await manager.reload(plugin_id)
    except PluginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return next(item for item in manager.list_plugins() if item["id"] == plugin_id)


@router.delete("/plugins/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_plugin(
    plugin_id: str,
    manager: PluginManager = Depends(get_plugin_manager),
) -> None:
    try:
        await manager.uninstall(plugin_id)
    except PluginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runtime/messages", response_model=RuntimeReplyOut)
async def process_runtime_message(
    payload: RuntimeMessageRequest,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    try:
        reply = await runtime.handle_user_message(
            conversation_id=payload.conversation_id,
            user_id=payload.user_id,
            text=payload.text,
            channel=payload.channel,
            media_refs=[item.model_dump() for item in payload.media],
        )
    except (RuntimeConfigurationError, PromptBudgetError, ModelConfigurationError, MediaValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModelHTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ModelClientError, ChatRuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return RuntimeReplyOut(
        conversation_id=reply.conversation_id,
        route_id=reply.route_id,
        consumed=reply.consumed,
        text=reply.text,
        message_id=reply.message_id,
        model=reply.model,
        finish_reason=reply.finish_reason,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        total_tokens=reply.total_tokens,
        outbound_actions=[item.model_dump() for item in reply.outbound_actions],
    )


@router.get("/runtime/conversations", response_model=list[ConversationOut])
def list_runtime_conversations(runtime: ChatRuntime = Depends(get_chat_runtime)):
    return runtime.list_conversations()


@router.post(
    "/runtime/conversations/import/sillytavern",
    response_model=SillyTavernChatImportReport,
    status_code=status.HTTP_201_CREATED,
)
async def import_sillytavern_chat_record(
    request: Request,
    route_id: str = Query(min_length=1, max_length=200),
    file_name: str = Query(min_length=1, max_length=260),
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > MAX_CHAT_IMPORT_BYTES:
                raise HTTPException(status_code=413, detail="聊天记录文件不能超过 32 MB")
        except ValueError:
            raise HTTPException(status_code=400, detail="聊天记录文件大小无效") from None
    data = await request.body()
    if len(data) > MAX_CHAT_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="聊天记录文件不能超过 32 MB")
    try:
        return await runtime.import_sillytavern_chat_record(route_id, file_name, data)
    except (ChatRuntimeError, SillyTavernChatImportError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/runtime/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_runtime_conversation(
    payload: ConversationCreate,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    return runtime.create_conversation_record(payload.route_id, payload.title)


@router.put("/runtime/conversations/{conversation_id}", response_model=ConversationOut)
def update_runtime_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    try:
        return runtime.rename_conversation_record(conversation_id, payload.title)
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runtime/conversations/{conversation_id}/activate", response_model=ConversationOut)
async def activate_runtime_conversation(
    conversation_id: str,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    try:
        return await runtime.activate_conversation_record(conversation_id)
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/runtime/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime_conversation(
    conversation_id: str,
    runtime: ChatRuntime = Depends(get_chat_runtime),
) -> None:
    try:
        await runtime.delete_conversation_record(conversation_id)
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/runtime/conversations/{conversation_id}/messages",
    response_model=list[ChatMessageOut],
)
def list_runtime_messages(
    conversation_id: str,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    return runtime.list_messages(conversation_id)


@router.put(
    "/runtime/conversations/{conversation_id}/messages/{message_id}",
    response_model=ChatMessageOut,
)
async def update_runtime_message(
    conversation_id: str,
    message_id: str,
    payload: ConversationMessageUpdate,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    try:
        return await runtime.update_conversation_message(
            conversation_id,
            message_id,
            payload.content,
        )
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/runtime/conversations/{conversation_id}/messages/delete",
    response_model=ConversationMessagesDeleteOut,
)
async def delete_runtime_messages(
    conversation_id: str,
    payload: ConversationMessagesDelete,
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    try:
        return await runtime.delete_conversation_messages(conversation_id, payload.message_ids)
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runtime/actions", response_model=list[RuntimeActionOut])
def list_runtime_actions(
    limit: int = Query(default=100, ge=1, le=500),
    runtime: ChatRuntime = Depends(get_chat_runtime),
):
    return runtime.list_actions(limit)


@router.get("/onebot/config", response_model=OneBotConfigOut)
def get_onebot_config(
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
):
    config = session.get(OneBotConfig, 1)
    if config is None:
        config = OneBotConfig(id=1)
        session.add(config)
        session.commit()
        session.refresh(config)
    return onebot_config_out(config, secret_box)


@router.put("/onebot/config", response_model=OneBotConfigOut)
async def update_onebot_config(
    payload: OneBotConfigUpdate,
    session: Session = Depends(get_session),
    secret_box: SecretBox = Depends(get_secret_box),
    gateway: OneBotGateway = Depends(get_onebot_gateway),
):
    config = session.get(OneBotConfig, 1)
    if config is None:
        config = OneBotConfig(id=1)
        session.add(config)
        session.flush()
    values = payload.model_dump(exclude_unset=True, exclude={"access_token"})
    for key, value in values.items():
        if value is not None:
            setattr(config, key, value)
    token_changed = "access_token" in payload.model_fields_set
    connection_changed = bool(
        {"enabled", "connection_mode", "reverse_ws_url", "forward_ws_url", "access_token"}
        & payload.model_fields_set
    )
    if token_changed:
        config.access_token_encrypted = secret_box.encrypt((payload.access_token or "").strip())
    if config.enabled and config.connection_mode == "forward" and not config.forward_ws_url:
        raise HTTPException(status_code=400, detail="启用正向 WebSocket 前必须填写 NapCat WebSocket 地址")
    session.commit()
    session.refresh(config)
    output = onebot_config_out(config, secret_box)
    await gateway.apply_config_change(reconnect=connection_changed)
    return output


@router.get("/onebot/status", response_model=OneBotStatusOut)
def get_onebot_status(gateway: OneBotGateway = Depends(get_onebot_gateway)):
    return gateway.status()
