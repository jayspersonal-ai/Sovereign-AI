# short_term_memory/schemas.py
#
# The ONLY place request/response shapes are defined. main.py imports these;
# it must never declare its own Pydantic models.
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["system", "user", "assistant", "tool"]


# --- Character cards ---------------------------------------------------------
class CharacterCardCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field("Lyra", max_length=64)
    creator: Optional[str] = Field("Josh", max_length=64)
    description: Optional[str] = None
    system_prompt: str
    first_message: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=255)
    tags: List[str] = Field(default_factory=list)


class CharacterCardUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=64)
    creator: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = None
    system_prompt: Optional[str] = None
    first_message: Optional[str] = None
    avatar_url: Optional[str] = Field(None, max_length=255)
    tags: Optional[List[str]] = None


class CharacterCardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    creator: Optional[str] = None
    description: Optional[str] = None
    system_prompt: str
    first_message: Optional[str] = None
    avatar_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    token_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("tags", mode="before")
    @classmethod
    def _null_tags(cls, value):
        return [] if value is None else value


# --- Sessions ----------------------------------------------------------------
class ChatSessionCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    title: str = Field("Main Conversation", max_length=256)
    character_id: Optional[str] = None
    user_name: str = Field("Josh", max_length=64)
    character_name: str = Field("Lyra", max_length=64)
    override_system_prompt: Optional[str] = None
    max_context_tokens: Optional[int] = Field(None, gt=0)


class ChatSessionUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=256)
    character_id: Optional[str] = None
    user_name: Optional[str] = Field(None, max_length=64)
    character_name: Optional[str] = Field(None, max_length=64)
    override_system_prompt: Optional[str] = None
    max_context_tokens: Optional[int] = Field(None, gt=0)


class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    character_id: Optional[str] = None
    user_name: str
    character_name: str
    override_system_prompt: Optional[str] = None
    max_context_tokens: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChatSessionListItem(ChatSessionResponse):
    """Session plus the counts a viewer needs for its session list."""

    message_count: int = 0
    last_message_at: Optional[datetime] = None
    # Messages not yet ingested into the long-term vault (deleted ones excluded)
    pending_ingest_count: int = 0


# --- Messages ----------------------------------------------------------------
class ChatMessageCreate(BaseModel):
    role: Role
    content: str
    name: Optional[str] = Field(None, max_length=128)
    session_type: Optional[str] = Field(
        "casual_chat", description="'casual_chat', 'book_video', or 'scenario_roleplay'"
    )
    speaker: Optional[str] = Field(
        None, description="'user', 'companion', or 'system'. Derived from role if omitted."
    )
    frontend_source: Optional[str] = Field("mission_control")
    book_video_title: Optional[str] = Field(None, max_length=256)
    chapter_timestamp: Optional[str] = Field(None, max_length=64)
    ui_evaluated_tone: Optional[str] = "neutral_logistical"
    ui_stress_level: Optional[int] = Field(1, ge=1, le=5)
    ui_closeness_level: Optional[int] = Field(3, ge=1, le=5)
    generation_telemetry: Optional[Dict[str, Any]] = None
    execution_fingerprint: Optional[str] = "v1.0.0"
    emergent_language: Optional[List[str]] = None
    active_core_beliefs: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = Field(
        None, description="Original timestamp, for historical imports (e.g. SillyTavern)"
    )


class ChatMessageUpdate(BaseModel):
    content: Optional[str] = None
    name: Optional[str] = Field(None, max_length=128)
    session_type: Optional[str] = None
    book_video_title: Optional[str] = Field(None, max_length=256)
    chapter_timestamp: Optional[str] = Field(None, max_length=64)
    ui_evaluated_tone: Optional[str] = None
    ui_stress_level: Optional[int] = Field(None, ge=1, le=5)
    ui_closeness_level: Optional[int] = Field(None, ge=1, le=5)
    generation_telemetry: Optional[Dict[str, Any]] = None
    emergent_language: Optional[List[str]] = None
    active_core_beliefs: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


# Older rows may hold NULL in these columns; the API always returns a value.
_MESSAGE_NULL_DEFAULTS: Dict[str, Any] = {
    "session_type": "casual_chat",
    "speaker": "user",
    "frontend_source": "mission_control",
    "token_count": 0,
    "ui_evaluated_tone": "neutral_logistical",
    "ui_stress_level": 1,
    "ui_closeness_level": 3,
    "generation_telemetry": dict,
    "execution_fingerprint": "v1.0.0",
    "emergent_language": list,
    "active_core_beliefs": list,
    "metadata": dict,
}


class ChatMessageResponse(BaseModel):
    # Do NOT add populate_by_name here: on a SQLAlchemy object, ".metadata" is
    # the table registry, not our column. The column is read via the alias.
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    session_type: str = "casual_chat"
    role: str
    speaker: str = "user"
    frontend_source: str = "mission_control"
    name: Optional[str] = None
    content: str
    token_count: int = 0
    book_video_title: Optional[str] = None
    chapter_timestamp: Optional[str] = None
    ui_evaluated_tone: str = "neutral_logistical"
    ui_stress_level: int = 1
    ui_closeness_level: int = 3
    generation_telemetry: Dict[str, Any] = Field(default_factory=dict)
    execution_fingerprint: str = "v1.0.0"
    emergent_language: List[Any] = Field(default_factory=list)
    active_core_beliefs: List[Any] = Field(default_factory=list)
    is_ingested: bool = False
    ingested_at: Optional[datetime] = None
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    # Stored in the "metadata_json" column, returned to clients as "metadata"
    metadata: Dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator(*_MESSAGE_NULL_DEFAULTS.keys(), mode="before")
    @classmethod
    def _null_to_default(cls, value, info):
        if value is None:
            default = _MESSAGE_NULL_DEFAULTS[info.field_name]
            return default() if callable(default) else default
        return value


class BatchChatMessageCreate(BaseModel):
    messages: List[ChatMessageCreate]
    session_title: Optional[str] = Field(None, max_length=256)
    user_name: Optional[str] = Field(None, max_length=64)
    character_name: Optional[str] = Field(None, max_length=64)


class BatchChatMessageResponse(BaseModel):
    status: str = "success"
    session_id: str
    imported_count: int
    total_tokens: int
    oldest_timestamp: Optional[datetime] = None
    newest_timestamp: Optional[datetime] = None


class MarkIngestedRequest(BaseModel):
    message_ids: List[int] = Field(..., min_length=1)


class SessionStatsResponse(BaseModel):
    session_id: str
    total_logged_messages: int
    pending_vector_queue_count: int
    total_curated_books_videos: int
    rolling_stress_average: float
    rolling_closeness_average: float
    average_token_count: float


# --- Context window ----------------------------------------------------------
class ContextBackfillResponse(BaseModel):
    session_id: str
    max_budget_tokens: int
    used_tokens: int
    system_tokens: int
    message_tokens: int
    system_prompt: str
    character_name: str
    user_name: str
    messages: List[Dict[str, Any]]  # OpenAI-style, plus id / token_count / created_at
    truncated_count: int  # older messages left behind the cutoff


# --- Memory spotlight --------------------------------------------------------
class MemorySpotlightCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    child_summary: str = Field(..., min_length=1, max_length=300)
    parent_data: str = Field(..., min_length=1)
    tone_tags: List[str] = Field(default_factory=list)
    why_cherished: Optional[str] = Field(None, max_length=255)


class MemorySpotlightUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    child_summary: Optional[str] = Field(None, min_length=1, max_length=300)
    parent_data: Optional[str] = Field(None, min_length=1)
    tone_tags: Optional[List[str]] = None
    why_cherished: Optional[str] = Field(None, max_length=255)


class MemorySpotlightBrowseItem(BaseModel):
    """Compact summary for low-token browsing."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    child_summary: str
    tone_tags: List[str] = Field(default_factory=list)
    why_cherished: Optional[str] = None
    token_count: int = 0
    times_recalled: int = 0
    last_recalled_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class MemorySpotlightDetailResponse(MemorySpotlightBrowseItem):
    """Full memory, including the parent narrative."""

    parent_data: str
    updated_at: Optional[datetime] = None
