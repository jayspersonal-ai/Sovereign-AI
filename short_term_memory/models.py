# short_term_memory/models.py
#
# The ONLY place tables are defined. main.py imports these; it must never
# declare its own Base or its own table classes.
#
# Table names match the live database:
#   sessions, messages      -> hold the real chat history
#   character_cards         -> persona cards
#   memory_spotlight        -> curated memories
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CharacterCard(Base):
    """Persona definitions: system prompt, greeting, and display details."""

    __tablename__ = "character_cards"

    id = Column(String(64), primary_key=True)  # e.g. "lyra_default", "lyra_code"
    name = Column(String(64), nullable=False, default="Lyra")
    creator = Column(String(64), default="Josh")
    description = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=False)
    first_message = Column(Text, nullable=True)
    token_count = Column(Integer, nullable=False, default=0)  # tokens in system_prompt
    avatar_url = Column(String(255), nullable=True)
    tags = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ChatSession(Base):
    """A conversation: who is talking, which card is active, and the context budget."""

    __tablename__ = "sessions"

    id = Column(String(128), primary_key=True)  # e.g. "lyra_main", "workspace_dev"
    title = Column(String(256), nullable=False, default="Main Conversation")
    character_id = Column(
        String(64), ForeignKey("character_cards.id", ondelete="SET NULL"), nullable=True
    )
    user_name = Column(String(64), nullable=False, default="Josh")
    character_name = Column(String(64), nullable=False, default="Lyra")
    override_system_prompt = Column(Text, nullable=True)
    max_context_tokens = Column(Integer, nullable=False, default=32768)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class ChatMessage(Base):
    """
    One chat message, with token caching, mood telemetry, media anchors,
    soft delete, and the flag that gates ingestion into long-term LanceDB.
    """

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(128),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_type = Column(String(64), default="casual_chat")  # 'casual_chat', 'book_video', 'scenario_roleplay'

    # Dialogue & source
    role = Column(String(32), nullable=False)  # 'system', 'user', 'assistant', 'tool'
    speaker = Column(String(64), default="user")  # 'user', 'companion', 'system'
    frontend_source = Column(String(64), default="mission_control")
    name = Column(String(128), nullable=True)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)

    # Media anchors (null during casual chat)
    book_video_title = Column(String(256), nullable=True)
    chapter_timestamp = Column(String(64), nullable=True)  # 'Chapter 4' or '01:23:45'

    # Mood telemetry
    ui_evaluated_tone = Column(String(64), default="neutral_logistical")
    ui_stress_level = Column(Integer, default=1)  # 1 to 5
    ui_closeness_level = Column(Integer, default=3)  # 1 to 5

    # Curation & tracking
    generation_telemetry = Column(JSON, nullable=True)  # tokens/sec, eval count, GPU metrics
    execution_fingerprint = Column(String(64), default="v1.0.0")
    emergent_language = Column(JSON, nullable=True)  # inside jokes / vocab
    active_core_beliefs = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)  # exposed as "metadata" by the API

    # Ingestion into long-term LanceDB
    is_ingested = Column(Boolean, nullable=False, default=False, server_default=false())
    ingested_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted = Column(Boolean, nullable=False, default=False, server_default=false())
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


Index(
    "idx_messages_session_active",
    ChatMessage.session_id,
    ChatMessage.is_deleted,
    ChatMessage.created_at,
    ChatMessage.id,
)
Index("idx_messages_session_uningested", ChatMessage.session_id, ChatMessage.is_ingested)


class MemorySpotlight(Base):
    """
    Curated, cherished memories: a short child summary for low-token browsing
    and the full parent narrative for when a memory is explicitly recalled.
    """

    __tablename__ = "memory_spotlight"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(120), nullable=False)
    child_summary = Column(String(300), nullable=False)
    parent_data = Column(Text, nullable=False)
    tone_tags = Column(JSON, nullable=False, default=list)  # ["tender", "triumphant"]
    why_cherished = Column(String(255), nullable=True)
    token_count = Column(Integer, nullable=False, default=0)  # tokens in parent_data

    times_recalled = Column(Integer, nullable=False, default=0)
    last_recalled_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


Index("idx_spotlight_recalled", MemorySpotlight.times_recalled.desc())
