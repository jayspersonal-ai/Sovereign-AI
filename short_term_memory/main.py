# short_term_memory/main.py
#
# Routes only. Tables live in models.py, request/response shapes in schemas.py,
# the engine in database.py, token counting in token_counter.py.
# Do not define tables, Pydantic models, or an engine in this file.
import logging
import os
from contextlib import asynccontextmanager
from datetime import timezone
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Text, and_, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, init_db
from models import CharacterCard, ChatMessage, ChatSession, MemorySpotlight, utcnow
from schemas import (
    BatchChatMessageCreate,
    BatchChatMessageResponse,
    CharacterCardCreate,
    CharacterCardResponse,
    CharacterCardUpdate,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageUpdate,
    ChatSessionCreate,
    ChatSessionListItem,
    ChatSessionResponse,
    ChatSessionUpdate,
    ContextBackfillResponse,
    MarkIngestedRequest,
    MemorySpotlightBrowseItem,
    MemorySpotlightCreate,
    MemorySpotlightDetailResponse,
    MemorySpotlightUpdate,
    SessionStatsResponse,
)
from token_counter import count_tokens

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("short_term_memory")

# --- Settings (all overridable from the environment) -------------------------
# Context budget given to sessions that are created without one
DEFAULT_MAX_CONTEXT_TOKENS = int(os.getenv("DEFAULT_MAX_CONTEXT_TOKENS", "32768"))
# Tokens held back for the system prompt when the session has no prompt of its own
SYSTEM_TOKEN_RESERVE = int(os.getenv("SYSTEM_TOKEN_RESERVE", "800"))
# Tokens held back for the model's next reply
REPLY_TOKEN_RESERVE = int(os.getenv("REPLY_TOKEN_RESERVE", "2000"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

CONTEXT_PAGE_SIZE = 200
SPEAKER_BY_ROLE = {"user": "user", "assistant": "companion"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Lyra Short-Term Memory", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Helpers -----------------------------------------------------------------
async def require_session(db: AsyncSession, session_id: str) -> ChatSession:
    sess = await db.get(ChatSession, session_id)
    if sess is None:
        raise HTTPException(404, "Session not found")
    return sess


async def require_message(db: AsyncSession, message_id: int) -> ChatMessage:
    msg = await db.get(ChatMessage, message_id)
    if msg is None:
        raise HTTPException(404, "Message not found")
    return msg


async def require_character(db: AsyncSession, char_id: str) -> CharacterCard:
    card = await db.get(CharacterCard, char_id)
    if card is None:
        raise HTTPException(404, "Character not found")
    return card


async def require_spotlight(db: AsyncSession, memory_id: int) -> MemorySpotlight:
    item = await db.get(MemorySpotlight, memory_id)
    if item is None:
        raise HTTPException(404, "Spotlight not found")
    return item


async def get_or_create_session(
    db: AsyncSession,
    session_id: str,
    *,
    title: Optional[str] = None,
    user_name: Optional[str] = None,
    character_name: Optional[str] = None,
) -> ChatSession:
    """Posting or importing into an unknown session id creates that session."""
    sess = await db.get(ChatSession, session_id)
    if sess is None:
        sess = ChatSession(
            id=session_id,
            title=title or f"Session {session_id}",
            user_name=user_name or "Josh",
            character_name=character_name or "Lyra",
            max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS,
        )
        db.add(sess)
        await db.flush()
    return sess


def build_message(sess: ChatSession, data: ChatMessageCreate) -> ChatMessage:
    """Turn a request payload into a row, filling in name, speaker and token count."""
    if data.name:
        name = data.name
    elif data.role == "user":
        name = sess.user_name
    elif data.role == "assistant":
        name = sess.character_name
    else:
        name = None

    msg = ChatMessage(
        session_id=sess.id,
        role=data.role,
        speaker=data.speaker or SPEAKER_BY_ROLE.get(data.role, "system"),
        name=name,
        content=data.content,
        token_count=count_tokens(data.content),
        session_type=data.session_type or "casual_chat",
        frontend_source=data.frontend_source or "mission_control",
        book_video_title=data.book_video_title,
        chapter_timestamp=data.chapter_timestamp,
        ui_evaluated_tone=data.ui_evaluated_tone or "neutral_logistical",
        ui_stress_level=data.ui_stress_level or 1,
        ui_closeness_level=data.ui_closeness_level or 3,
        generation_telemetry=data.generation_telemetry or {},
        execution_fingerprint=data.execution_fingerprint or "v1.0.0",
        emergent_language=data.emergent_language or [],
        active_core_beliefs=data.active_core_beliefs or [],
        metadata_json=data.metadata or {},
    )
    if data.created_at is not None:
        stamp = data.created_at
        if stamp.tzinfo is None:  # treat timestamps without a zone as UTC
            stamp = stamp.replace(tzinfo=timezone.utc)
        msg.created_at = stamp
    return msg


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# --- Character cards ---------------------------------------------------------
@app.get("/v1/characters", response_model=List[CharacterCardResponse])
async def list_characters(db: AsyncSession = Depends(get_db)):
    result = await db.scalars(select(CharacterCard).order_by(CharacterCard.created_at.asc()))
    return result.all()


@app.post("/v1/characters")
async def create_character(data: CharacterCardCreate, db: AsyncSession = Depends(get_db)):
    if await db.get(CharacterCard, data.id) is not None:
        raise HTTPException(409, "A character with this id already exists")
    card = CharacterCard(**data.model_dump(), token_count=count_tokens(data.system_prompt))
    db.add(card)
    await db.commit()
    return {"status": "created", "id": card.id}


@app.get("/v1/characters/{char_id}", response_model=CharacterCardResponse)
async def get_character(char_id: str, db: AsyncSession = Depends(get_db)):
    return await require_character(db, char_id)


@app.patch("/v1/characters/{char_id}")
async def update_character(
    char_id: str, data: CharacterCardUpdate, db: AsyncSession = Depends(get_db)
):
    card = await require_character(db, char_id)
    fields = data.model_dump(exclude_unset=True)
    for key, value in fields.items():
        if value is None and key in {"name", "system_prompt"}:
            continue  # these can't be cleared
        setattr(card, key, value)
    if fields.get("system_prompt") is not None:
        card.token_count = count_tokens(card.system_prompt)
    await db.commit()
    return {"status": "updated", "id": card.id}


@app.delete("/v1/characters/{char_id}")
async def delete_character(char_id: str, db: AsyncSession = Depends(get_db)):
    card = await require_character(db, char_id)
    await db.delete(card)  # sessions using this card are unlinked by the database
    await db.commit()
    return {"status": "deleted"}


# --- Memory spotlight --------------------------------------------------------
@app.get("/v1/spotlight/browse", response_model=List[MemorySpotlightBrowseItem])
async def browse_spotlight(
    tone: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(MemorySpotlight)
    if tone:
        stmt = stmt.where(cast(MemorySpotlight.tone_tags, Text).ilike(f"%{tone}%"))
    if search:
        stmt = stmt.where(
            or_(
                MemorySpotlight.title.ilike(f"%{search}%"),
                MemorySpotlight.child_summary.ilike(f"%{search}%"),
            )
        )
    result = await db.scalars(stmt.order_by(MemorySpotlight.created_at.desc()))
    return result.all()


@app.post("/v1/spotlight")
async def create_spotlight(data: MemorySpotlightCreate, db: AsyncSession = Depends(get_db)):
    item = MemorySpotlight(**data.model_dump(), token_count=count_tokens(data.parent_data))
    db.add(item)
    await db.commit()
    return {"status": "created", "id": item.id}


@app.get("/v1/spotlight/{memory_id}", response_model=MemorySpotlightDetailResponse)
async def get_spotlight_detail(
    memory_id: int, track_recall: bool = False, db: AsyncSession = Depends(get_db)
):
    item = await require_spotlight(db, memory_id)
    if track_recall:
        # Counted in the database, and without touching updated_at:
        # being recalled is not an edit.
        await db.execute(
            update(MemorySpotlight)
            .where(MemorySpotlight.id == memory_id)
            .values(
                times_recalled=MemorySpotlight.times_recalled + 1,
                last_recalled_at=utcnow(),
                updated_at=MemorySpotlight.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        await db.refresh(item)
    return item


@app.patch("/v1/spotlight/{memory_id}")
async def update_spotlight(
    memory_id: int, data: MemorySpotlightUpdate, db: AsyncSession = Depends(get_db)
):
    item = await require_spotlight(db, memory_id)
    fields = data.model_dump(exclude_unset=True)
    for key, value in fields.items():
        if value is None and key != "why_cherished":
            continue  # only why_cherished can be cleared
        setattr(item, key, value)
    if fields.get("parent_data") is not None:
        item.token_count = count_tokens(item.parent_data)
    await db.commit()
    return {"status": "updated", "id": item.id}


@app.delete("/v1/spotlight/{memory_id}")
async def delete_spotlight(memory_id: int, db: AsyncSession = Depends(get_db)):
    item = await require_spotlight(db, memory_id)
    await db.delete(item)
    await db.commit()
    return {"status": "deleted"}


# --- Sessions ----------------------------------------------------------------
@app.get("/sessions", response_model=List[ChatSessionListItem])
async def list_sessions(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(
            ChatSession,
            func.count(ChatMessage.id),
            func.max(ChatMessage.created_at),
            func.count(ChatMessage.id).filter(ChatMessage.is_ingested.is_(False)),
        )
        .outerjoin(
            ChatMessage,
            and_(ChatMessage.session_id == ChatSession.id, ChatMessage.is_deleted.is_(False)),
        )
        .group_by(ChatSession.id)
        .order_by(ChatSession.created_at.desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        ChatSessionListItem.model_validate(sess).model_copy(
            update={"message_count": count, "last_message_at": last, "pending_ingest_count": pending}
        )
        for sess, count, last, pending in rows
    ]


@app.post("/sessions")
async def create_session(data: ChatSessionCreate, db: AsyncSession = Depends(get_db)):
    if await db.get(ChatSession, data.id) is not None:
        raise HTTPException(409, "A session with this id already exists")
    if data.character_id is not None:
        await require_character(db, data.character_id)
    fields = data.model_dump()
    fields["max_context_tokens"] = data.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS
    sess = ChatSession(**fields)
    db.add(sess)
    await db.commit()
    return {"status": "created", "id": sess.id}


@app.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    return await require_session(db, session_id)


@app.patch("/sessions/{session_id}")
async def update_session(
    session_id: str, data: ChatSessionUpdate, db: AsyncSession = Depends(get_db)
):
    sess = await require_session(db, session_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("character_id") is not None:
        await require_character(db, fields["character_id"])
    for key, value in fields.items():
        if value is None and key not in {"character_id", "override_system_prompt"}:
            continue  # only the card link and the prompt override can be cleared
        setattr(sess, key, value)
    await db.commit()
    return {"status": "updated", "id": sess.id}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """Permanently deletes the session and, via the database cascade, all its messages."""
    sess = await require_session(db, session_id)
    await db.delete(sess)
    await db.commit()
    return {"status": "deleted"}


# --- Messages ----------------------------------------------------------------
@app.get("/sessions/{session_id}/messages", response_model=List[ChatMessageResponse])
async def list_messages(
    session_id: str,
    limit: Optional[int] = Query(None, ge=1, description="Omit to return everything"),
    offset: int = Query(0, ge=0),
    order: Literal["asc", "desc"] = "asc",
    include_deleted: bool = False,
    ingested: Optional[bool] = Query(None, description="Filter by ingestion state"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
    if not include_deleted:
        stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if ingested is not None:
        stmt = stmt.where(ChatMessage.is_ingested.is_(ingested))
    if order == "desc":
        stmt = stmt.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    else:
        stmt = stmt.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return (await db.scalars(stmt)).all()


@app.post("/sessions/{session_id}/messages")
async def add_message(
    session_id: str, data: ChatMessageCreate, db: AsyncSession = Depends(get_db)
):
    sess = await get_or_create_session(db, session_id)
    msg = build_message(sess, data)
    db.add(msg)
    await db.commit()
    return {"status": "created", "id": msg.id, "token_count": msg.token_count}


@app.post("/sessions/{session_id}/import", response_model=BatchChatMessageResponse)
async def import_messages(
    session_id: str, batch: BatchChatMessageCreate, db: AsyncSession = Depends(get_db)
):
    sess = await get_or_create_session(
        db,
        session_id,
        title=batch.session_title or "Migrated Conversation",
        user_name=batch.user_name,
        character_name=batch.character_name,
    )
    rows = [build_message(sess, item) for item in batch.messages]
    db.add_all(rows)
    await db.commit()

    stamps = [row.created_at for row in rows if row.created_at is not None]
    return BatchChatMessageResponse(
        session_id=session_id,
        imported_count=len(rows),
        total_tokens=sum(row.token_count or 0 for row in rows),
        oldest_timestamp=min(stamps) if stamps else None,
        newest_timestamp=max(stamps) if stamps else None,
    )


@app.post("/sessions/{session_id}/ingested")
async def mark_ingested(
    session_id: str, data: MarkIngestedRequest, db: AsyncSession = Depends(get_db)
):
    """Called by the LanceDB ingestion job once messages are safely embedded."""
    result = await db.execute(
        update(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.id.in_(data.message_ids))
        .values(is_ingested=True, ingested_at=utcnow(), updated_at=ChatMessage.updated_at)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return {"status": "updated", "marked_count": result.rowcount}


@app.patch("/messages/{message_id}")
async def update_message(
    message_id: int, data: ChatMessageUpdate, db: AsyncSession = Depends(get_db)
):
    msg = await require_message(db, message_id)
    fields = data.model_dump(exclude_unset=True)
    if "metadata" in fields:
        msg.metadata_json = fields.pop("metadata")
    if fields.get("content") is None:
        fields.pop("content", None)  # content can't be cleared
    for key, value in fields.items():
        setattr(msg, key, value)
    if "content" in fields:
        msg.token_count = count_tokens(msg.content)
    await db.commit()
    return {"status": "updated", "id": msg.id, "token_count": msg.token_count}


@app.delete("/messages/{message_id}")
async def delete_message(
    message_id: int,
    hard: bool = Query(False, description="true = remove the row permanently"),
    db: AsyncSession = Depends(get_db),
):
    """Soft delete by default: the row is hidden from lists and context but kept."""
    msg = await require_message(db, message_id)
    if hard:
        await db.delete(msg)
    else:
        msg.is_deleted = True
        msg.deleted_at = utcnow()
    await db.commit()
    return {"status": "deleted", "id": message_id, "hard": hard}


@app.post("/messages/{message_id}/restore")
async def restore_message(message_id: int, db: AsyncSession = Depends(get_db)):
    msg = await require_message(db, message_id)
    msg.is_deleted = False
    msg.deleted_at = None
    await db.commit()
    return {"status": "restored", "id": msg.id}


# --- Context window ----------------------------------------------------------
@app.get("/sessions/{session_id}/context", response_model=ContextBackfillResponse)
async def get_session_context(session_id: str, db: AsyncSession = Depends(get_db)):
    sess = await require_session(db, session_id)
    max_tokens = sess.max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS

    # 1. System prompt: session override first, then the linked character card
    system_prompt = sess.override_system_prompt or ""
    if not system_prompt and sess.character_id:
        card = await db.get(CharacterCard, sess.character_id)
        if card is not None:
            system_prompt = card.system_prompt or ""
    system_tokens = count_tokens(system_prompt) if system_prompt else SYSTEM_TOKEN_RESERVE

    budget = max(0, max_tokens - system_tokens - REPLY_TOKEN_RESERVE)

    active = and_(ChatMessage.session_id == session_id, ChatMessage.is_deleted.is_(False))
    total = await db.scalar(select(func.count(ChatMessage.id)).where(active)) or 0

    # 2. Walk backwards from the newest message and STOP at the first one that
    #    doesn't fit, so the window is always one unbroken run of history.
    newest_first = (
        select(ChatMessage)
        .where(active)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
    )
    picked: List[ChatMessage] = []
    used_tokens = 0
    offset = 0
    budget_full = False
    while not budget_full:
        page = (
            await db.scalars(newest_first.limit(CONTEXT_PAGE_SIZE).offset(offset))
        ).all()
        if not page:
            break
        for msg in page:
            cost = msg.token_count or 0
            if used_tokens + cost > budget:
                budget_full = True
                break
            picked.append(msg)
            used_tokens += cost
        offset += CONTEXT_PAGE_SIZE

    # 3. Back to chronological order
    picked.reverse()

    return ContextBackfillResponse(
        session_id=session_id,
        max_budget_tokens=max_tokens,
        used_tokens=used_tokens + system_tokens,
        system_tokens=system_tokens,
        message_tokens=used_tokens,
        system_prompt=system_prompt,
        character_name=sess.character_name,
        user_name=sess.user_name,
        messages=[
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "name": msg.name,
                "token_count": msg.token_count or 0,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
            for msg in picked
        ],
        truncated_count=total - len(picked),
    )


# --- Stats -------------------------------------------------------------------
@app.get("/sessions/{session_id}/stats", response_model=SessionStatsResponse)
async def get_session_stats(
    session_id: str,
    window: int = Query(20, ge=1, description="How many recent messages the mood averages cover"),
    db: AsyncSession = Depends(get_db),
):
    await require_session(db, session_id)
    active = and_(ChatMessage.session_id == session_id, ChatMessage.is_deleted.is_(False))

    total, pending, titles, avg_tokens = (
        await db.execute(
            select(
                func.count(ChatMessage.id),
                func.count(ChatMessage.id).filter(ChatMessage.is_ingested.is_(False)),
                func.count(func.distinct(ChatMessage.book_video_title)),
                func.avg(ChatMessage.token_count),
            ).where(active)
        )
    ).one()

    recent = (
        select(ChatMessage.ui_stress_level, ChatMessage.ui_closeness_level)
        .where(active)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
        .limit(window)
        .subquery()
    )
    stress, closeness = (
        await db.execute(
            select(func.avg(recent.c.ui_stress_level), func.avg(recent.c.ui_closeness_level))
        )
    ).one()

    return SessionStatsResponse(
        session_id=session_id,
        total_logged_messages=total or 0,
        pending_vector_queue_count=pending or 0,
        total_curated_books_videos=titles or 0,
        rolling_stress_average=round(float(stress or 0), 2),
        rolling_closeness_average=round(float(closeness or 0), 2),
        average_token_count=round(float(avg_tokens or 0), 1),
    )
