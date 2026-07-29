from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, event, inspect, select, text, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="openai_compatible", nullable=False)
    chat_completion_source: Mapped[str] = mapped_column(String(40), default="custom", nullable=False)
    prompt_post_processing: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    blocks: Mapped[list[PromptBlock]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="PromptBlock.position",
    )


class PromptBlock(Base):
    __tablename__ = "prompt_blocks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="system", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stashed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    identifier: Mapped[str | None] = mapped_column(String(160), nullable=True)
    marker: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    injection_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    injection_depth: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    injection_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    template: Mapped[PromptTemplate] = relationship(back_populates="blocks")


class UserPersona(Base):
    __tablename__ = "user_personas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    injection_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    injection_depth: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="system", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    persona: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scenario: Mapped[str] = mapped_column(Text, default="", nullable=False)
    first_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    world_books: Mapped[list[WorldBook]] = relationship(back_populates="character")

    @property
    def world_book_ids(self) -> list[str]:
        return [book.id for book in self.world_books if book.scope == "character"]


class WorldBook(Base):
    __tablename__ = "world_books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_format: Mapped[str] = mapped_column(String(40), default="native", nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scope: Mapped[str] = mapped_column(String(20), default="character", nullable=False)
    character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    entries: Mapped[list[WorldBookEntry]] = relationship(
        back_populates="world_book",
        cascade="all, delete-orphan",
        order_by="WorldBookEntry.insertion_order",
    )
    character: Mapped[Character | None] = relationship(back_populates="world_books")


class WorldBookEntry(Base):
    __tablename__ = "world_book_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    world_book_id: Mapped[str] = mapped_column(
        ForeignKey("world_books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    secondary_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    comment: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    constant: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selective: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    selective_logic: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    insertion_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    insertion_depth: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="system", nullable=False)
    probability: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    use_probability: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    world_book: Mapped[WorldBook] = relationship(back_populates="entries")


class PresetWorldBook(Base):
    __tablename__ = "preset_world_books"

    preset_id: Mapped[str] = mapped_column(
        ForeignKey("configuration_presets.id", ondelete="CASCADE"), primary_key=True
    )
    world_book_id: Mapped[str] = mapped_column(
        ForeignKey("world_books.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ConfigurationPreset(Base):
    __tablename__ = "configuration_presets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompt_templates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_persona_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_personas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    max_context_unlocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, default=128000, nullable=False)
    max_response_tokens: Mapped[int] = mapped_column(Integer, default=2048, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    streaming: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    temperature: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    frequency_penalty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    presence_penalty: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    top_p: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    quote_wrapping: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    continue_prefill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    squash_system_messages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    function_calling: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    media_inlining: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    image_quality: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    show_thoughts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    world_book_links: Mapped[list[PresetWorldBook]] = relationship(
        cascade="all, delete-orphan",
        order_by="PresetWorldBook.position",
    )

    @property
    def world_book_ids(self) -> list[str]:
        return [link.world_book_id for link in self.world_book_links if link.enabled]


class OneBotConfig(Base):
    __tablename__ = "onebot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connection_mode: Mapped[str] = mapped_column(String(20), default="reverse", nullable=False)
    reverse_ws_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    forward_ws_url: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    private_messages: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    group_messages: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    private_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    group_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    api_timeout_seconds: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class OneBotEvent(Base):
    __tablename__ = "onebot_events"

    event_key: Mapped[str] = mapped_column(String(240), primary_key=True)
    self_id: Mapped[str] = mapped_column(String(40), default="", nullable=False, index=True)
    post_type: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="processing", nullable=False, index=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AdminAccount(Base):
    __tablename__ = "admin_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), default="internal", nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    title: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.position",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="complete", nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="runtime", nullable=False)
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    preset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    route_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    trigger_message_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    trigger_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    trigger_user_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False, index=True)
    user_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assistant_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sent_message_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    plugin_state_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    plugin_state_snapshot_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    recalled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RuntimeAction(Base):
    __tablename__ = "runtime_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plugin_id: Mapped[str] = mapped_column(String(80), default="runtime", nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(200), default="", nullable=False, index=True)
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PluginInstallation(Base):
    __tablename__ = "plugin_installations"

    plugin_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    default_profile_version: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class PluginConversationState(Base):
    __tablename__ = "plugin_conversation_states"

    plugin_id: Mapped[str] = mapped_column(
        ForeignKey("plugin_installations.plugin_id", ondelete="CASCADE"),
        primary_key=True,
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )

        @event.listens_for(self.engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate_legacy_provider_columns()
        self._migrate_provider_columns()
        self._migrate_configuration_preset_columns()
        self._migrate_prompt_block_columns()
        self._migrate_world_book_columns()
        self._migrate_conversation_columns()
        self._migrate_onebot_config_columns()
        self._migrate_runtime_action_columns()
        self._migrate_conversation_turn_columns()
        self._migrate_plugin_installation_columns()
        with self.session_factory() as session:
            self._seed_defaults(session)

    def _migrate_legacy_provider_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("providers")}
        legacy_columns = ("temperature", "max_tokens")
        with self.engine.begin() as connection:
            for column in legacy_columns:
                if column in columns:
                    connection.execute(text(f"ALTER TABLE providers DROP COLUMN {column}"))

    def _migrate_provider_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("providers")}
        migrations = {
            "chat_completion_source": (
                "ALTER TABLE providers ADD COLUMN chat_completion_source "
                "VARCHAR(40) NOT NULL DEFAULT 'custom'"
            ),
            "prompt_post_processing": (
                "ALTER TABLE providers ADD COLUMN prompt_post_processing "
                "VARCHAR(40) NOT NULL DEFAULT ''"
            ),
            "priority": (
                "ALTER TABLE providers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0"
            ),
        }
        with self.engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))
            rows = connection.execute(
                text("SELECT id, priority FROM providers ORDER BY created_at, id")
            ).all()
            next_priority = max((int(row.priority or 0) for row in rows), default=0) + 1
            for index, row in enumerate(rows, 1):
                if int(row.priority or 0) > 0:
                    continue
                assigned = index if "priority" not in columns else next_priority
                connection.execute(
                    text("UPDATE providers SET priority = :priority WHERE id = :id"),
                    {"priority": assigned, "id": row.id},
                )
                next_priority = max(next_priority, assigned + 1)

    def _migrate_prompt_block_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("prompt_blocks")}
        migrations = {
            "identifier": "ALTER TABLE prompt_blocks ADD COLUMN identifier VARCHAR(160)",
            "marker": "ALTER TABLE prompt_blocks ADD COLUMN marker BOOLEAN NOT NULL DEFAULT 0",
            "injection_position": "ALTER TABLE prompt_blocks ADD COLUMN injection_position INTEGER NOT NULL DEFAULT 0",
            "injection_depth": "ALTER TABLE prompt_blocks ADD COLUMN injection_depth INTEGER NOT NULL DEFAULT 4",
            "injection_order": "ALTER TABLE prompt_blocks ADD COLUMN injection_order INTEGER NOT NULL DEFAULT 100",
            "stashed": "ALTER TABLE prompt_blocks ADD COLUMN stashed BOOLEAN NOT NULL DEFAULT 0",
        }
        with self.engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))

    def _migrate_world_book_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("world_books")}
        migrations = {
            "scope": (
                "ALTER TABLE world_books ADD COLUMN scope "
                "VARCHAR(20) NOT NULL DEFAULT 'character'"
            ),
            "character_id": (
                "ALTER TABLE world_books ADD COLUMN character_id VARCHAR(36) "
                "REFERENCES characters(id) ON DELETE SET NULL"
            ),
        }
        with self.engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_world_books_character_id "
                    "ON world_books (character_id)"
                )
            )

    def _migrate_configuration_preset_columns(self) -> None:
        columns = {
            column["name"]
            for column in inspect(self.engine).get_columns("configuration_presets")
        }
        with self.engine.begin() as connection:
            if "user_persona_id" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE configuration_presets "
                        "ADD COLUMN user_persona_id VARCHAR(36) "
                        "REFERENCES user_personas(id) ON DELETE SET NULL"
                    )
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_configuration_presets_user_persona_id "
                    "ON configuration_presets (user_persona_id)"
                )
            )

    def _migrate_conversation_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("conversations")}
        with self.engine.begin() as connection:
            if "is_active" not in columns:
                connection.execute(
                    text("ALTER TABLE conversations ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_conversations_is_active "
                    "ON conversations (is_active)"
                )
            )

    def _migrate_onebot_config_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("onebot_config")}
        migrations = {
            "connection_mode": "ALTER TABLE onebot_config ADD COLUMN connection_mode VARCHAR(20) NOT NULL DEFAULT 'reverse'",
            "reverse_ws_url": "ALTER TABLE onebot_config ADD COLUMN reverse_ws_url VARCHAR(1000) NOT NULL DEFAULT ''",
            "forward_ws_url": "ALTER TABLE onebot_config ADD COLUMN forward_ws_url VARCHAR(1000) NOT NULL DEFAULT ''",
        }
        with self.engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))

    def _migrate_runtime_action_columns(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("runtime_actions")}
        with self.engine.begin() as connection:
            if "turn_id" not in columns:
                connection.execute(text("ALTER TABLE runtime_actions ADD COLUMN turn_id VARCHAR(36)"))
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_runtime_actions_turn_id ON runtime_actions (turn_id)")
            )

    def _migrate_conversation_turn_columns(self) -> None:
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("conversation_turns")
        }
        with self.engine.begin() as connection:
            if "trigger_message_ids" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE conversation_turns ADD COLUMN "
                        "trigger_message_ids JSON NOT NULL DEFAULT '[]'"
                    )
                )

    def _migrate_plugin_installation_columns(self) -> None:
        columns = {
            column["name"] for column in inspect(self.engine).get_columns("plugin_installations")
        }
        migrations = {
            "position": "ALTER TABLE plugin_installations ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
            "default_profile_version": (
                "ALTER TABLE plugin_installations "
                "ADD COLUMN default_profile_version VARCHAR(40) NOT NULL DEFAULT ''"
            ),
        }
        with self.engine.begin() as connection:
            for column, statement in migrations.items():
                if column not in columns:
                    connection.execute(text(statement))
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_plugin_installations_position "
                    "ON plugin_installations (position)"
                )
            )

    @staticmethod
    def _seed_defaults(session: Session) -> None:
        session.execute(
            update(Provider)
            .where(
                Provider.name == "OpenAI 兼容接口",
                Provider.kind == "openai_compatible",
                Provider.base_url == "https://api.openai.com/v1",
                Provider.model == "",
                Provider.api_key_encrypted == "",
            )
            .values(name="默认接口", base_url="")
        )
        session.execute(
            update(PromptTemplate)
            .where(PromptTemplate.description == "从 SillyTavern Chat Completion 预设导入")
            .values(description="通过兼容格式导入")
        )
        session.execute(
            update(ConfigurationPreset)
            .where(ConfigurationPreset.description == "从 SillyTavern 预设导入")
            .values(description="通过兼容格式导入")
        )

        if session.scalar(select(Provider.id).limit(1)) is None:
            session.add(
                Provider(
                    name="默认接口",
                    base_url="",
                    model="",
                    is_active=True,
                )
            )

        if session.get(OneBotConfig, 1) is None:
            session.add(OneBotConfig(id=1))

        if session.scalar(select(Character.id).limit(1)) is None:
            session.add(
                Character(
                    name="默认助手",
                    summary="通用对话角色",
                    persona="你现在是一只猫娘，负责陪伴用户聊天。",
                    scenario="",
                    is_active=True,
                )
            )
        else:
            session.execute(
                update(Character)
                .where(
                    Character.name == "默认助手",
                    Character.summary == "通用对话角色",
                    Character.persona == "表达自然、准确，遵循当前对话中的明确要求。",
                    Character.scenario == "通过 QQ 与用户交流。",
                )
                .values(
                    persona="你现在是一只猫娘，负责陪伴用户聊天。",
                    scenario="",
                )
            )

        if session.scalar(select(UserPersona.id).limit(1)) is None:
            session.add(
                UserPersona(
                    name="用户",
                    description="",
                    injection_position=0,
                    injection_depth=2,
                    role="system",
                    is_active=True,
                )
            )

        if session.scalar(select(PromptTemplate.id).limit(1)) is None:
            template = PromptTemplate(
                name="默认模板",
                description="基础规则与当前人设",
                is_active=True,
            )
            template.blocks.extend(
                [
                    PromptBlock(
                        title="基础规则",
                        role="system",
                        content="请遵循当前角色设定，保持回答连贯，并优先执行用户的明确要求。",
                        position=0,
                    ),
                    PromptBlock(
                        title="当前人设",
                        role="system",
                        content=(
                            "你现在扮演 {{character.name}}。\n"
                            "角色设定：{{character.persona}}\n"
                            "当前场景：{{character.scenario}}"
                        ),
                        position=1,
                    ),
                ]
            )
            session.add(template)

        session.flush()
        default_user_persona = session.scalar(select(UserPersona).order_by(UserPersona.created_at))
        if default_user_persona is not None:
            session.execute(
                update(ConfigurationPreset)
                .where(ConfigurationPreset.user_persona_id.is_(None))
                .values(user_persona_id=default_user_persona.id)
            )

        if session.scalar(select(ConfigurationPreset.id).limit(1)) is None:
            provider = session.scalar(select(Provider).where(Provider.is_active.is_(True)))
            template = session.scalar(select(PromptTemplate).where(PromptTemplate.is_active.is_(True)))
            character = session.scalar(select(Character).where(Character.is_active.is_(True)))
            user_persona = session.scalar(select(UserPersona).where(UserPersona.is_active.is_(True)))
            session.add(
                ConfigurationPreset(
                    name="默认预设",
                    description="默认供应商、提示词和人设组合",
                    provider_id=provider.id if provider else None,
                    prompt_template_id=template.id if template else None,
                    character_id=character.id if character else None,
                    user_persona_id=user_persona.id if user_persona else None,
                    max_response_tokens=2048,
                    temperature=1.0,
                    is_active=True,
                )
            )

        session.flush()
        active_preset = session.scalar(
            select(ConfigurationPreset).where(ConfigurationPreset.is_active.is_(True))
        )
        if active_preset is not None:
            if active_preset.user_persona_id is None:
                user_persona = session.scalar(select(UserPersona).order_by(UserPersona.created_at))
                active_preset.user_persona_id = user_persona.id if user_persona else None
            for model, active_id in (
                (Provider, active_preset.provider_id),
                (PromptTemplate, active_preset.prompt_template_id),
                (Character, active_preset.character_id),
                (UserPersona, active_preset.user_persona_id),
            ):
                session.execute(update(model).values(is_active=False))
                if active_id:
                    active_item = session.get(model, active_id)
                    if active_item is not None:
                        active_item.is_active = True

        for link in session.scalars(
            select(PresetWorldBook).order_by(PresetWorldBook.position)
        ).all():
            world_book = session.get(WorldBook, link.world_book_id)
            preset = session.get(ConfigurationPreset, link.preset_id)
            if (
                world_book is not None
                and world_book.scope == "character"
                and world_book.character_id is None
                and preset is not None
                and preset.character_id
            ):
                world_book.character_id = preset.character_id

        session.commit()

    def sessions(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            yield session
