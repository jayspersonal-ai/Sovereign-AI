# mcp_servers/mcp_memory_engine/main.py
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Literal, Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# The memory engine lives in mcp_servers/mcp_memory_engine and nowhere else.
# If these imports fail, the service should refuse to start rather than fall
# back to a copy somewhere else.
from mcp_servers.mcp_memory_engine.schema import (
    CompanionMemoryPacket,
    MemorySearchRequest,
    InteractionType,
    Tier2AtomicState,
    APPROVED_TONE_VECTORS,
    APPROVED_TOPIC_TAGS,
)
from mcp_servers.mcp_memory_engine.storage import store
from mcp_servers.mcp_memory_engine.evaluator import (
    evaluate_mood_trajectory,
    bootstrap_mood_state,
    get_excluded_tones_for_query,
)

API_KEY = os.getenv("MEMORY_API_KEY", "lyra_multipass")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize connection layouts and ensure LanceDB table exists
    await store.ensure_table()
    await store.create_fts_index()
    # Boot memory cache directly on launch into app.state RAM
    app.state.mood_state = bootstrap_mood_state()
    yield
    await store.close()

app = FastAPI(title="Companion Memory Engine API", version="1.2.0", lifespan=lifespan)

os.makedirs("/workspace", exist_ok=True)
app.mount("/workspace", StaticFiles(directory="/workspace"), name="workspace")

# Maintain recent context directly in RAM routing (kept at maxlen=6 per your specification)
app.state.active_chat_window = deque(maxlen=6)

class LiveChatTurn(BaseModel):
    speaker: str
    text: str
    interaction_type: InteractionType = "real_chat"

    @field_validator("speaker")
    @classmethod
    def normalize_speaker(cls, v: str) -> str:
        """
        Maps SillyTavern and Open-WebUI speaker identifiers seamlessly:
        'Josh', 'user', 'human' -> 'user'
        'Lyra', 'assistant', 'char', 'bot' -> 'char'
        """
        clean = v.strip().lower()
        if clean in ["josh", "user", "human", "me"]:
            return "user"
        elif clean in ["lyra", "char", "character", "assistant", "bot"]:
            return "char"
        # Fallback to avoid 422 drops from webhooks
        return "char" if clean != "user" else "user"

async def require_api_key(x_api_key: str = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")

@app.get("/health")
def health_check():
    return {"status": "online", "database": "lancedb"}

@app.post("/chat/intercept", dependencies=[Depends(require_api_key)])
async def intercept_chat_turn(turn: LiveChatTurn, background_tasks: BackgroundTasks):
    try:
        now_ts = time.time()
        app.state.active_chat_window.append({
            "speaker": turn.speaker,
            "text": turn.text,
            "timestamp": now_ts
        })
        
        current_history = list(app.state.active_chat_window)
        
        # Passes history, app.state, and the turn timestamp to prevent race conditions
        background_tasks.add_task(evaluate_mood_trajectory, current_history, app.state, now_ts)
        
        return {"status": "tracked", "speaker_added": turn.speaker}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tracking intercept failed: {str(e)}")

@app.get("/chat/retrieval-filter", dependencies=[Depends(require_api_key)])
async def get_retrieval_filter():
    """
    Reads directly out of RAM memory workspace. Zero disk I/O lag.
    Called by retrieval tools to construct structural database filter requests.
    """
    current_state = app.state.mood_state
    excluded_tones = get_excluded_tones_for_query(current_state)
    
    return {
        "active_trajectory": current_state.active_trajectory,
        "stress_level": current_state.stress_level,
        "closeness_score": current_state.closeness_score,
        "excluded_tones": excluded_tones
    }

# --- DATABASE MEMORY PASS-THROUGHS ---
@app.post("/memories/insert", dependencies=[Depends(require_api_key)])
async def insert_memory(packet: CompanionMemoryPacket):
    return {"status": "success", "id": await store.insert_memory(packet)}

@app.post("/memories/search", dependencies=[Depends(require_api_key)])
async def search_memories(req: MemorySearchRequest):
    # Retrieve RAM state and append active exclusions to request payload
    mood_excluded = get_excluded_tones_for_query(app.state.mood_state)
    req.excluded_tones = list(set((req.excluded_tones or []) + mood_excluded))

    return {"status": "success", "data": await store.query_memories(req)}

@app.get("/memories/graph/search", dependencies=[Depends(require_api_key)])
async def search_graph_memories(
    entity: str = Query(..., description="Subject or Object entity to search for"),
    predicate: Optional[str] = Query(None, description="Optional relationship predicate (e.g. Owns, Likes)"),
    limit: int = Query(5, ge=1, le=20)
):
    """Retrieves memories associated with specific entity relations in the property graph."""
    results = await store.search_by_graph_entity(entity=entity, predicate=predicate, limit=limit)
    return {"status": "success", "data": results}

@app.get("/memories/stats", dependencies=[Depends(require_api_key)])
async def get_memory_stats():
    """Returns database telemetry and distribution counts across QoS and interaction types."""
    return {"status": "success", "stats": await store.get_vault_stats()}

@app.post("/system/compact", dependencies=[Depends(require_api_key)])
async def compact_memory_vault():
    """Maintenance endpoint to merge LanceDB Arrow fragments and purge old version manifests."""
    result = await store.compact_vault()
    return result

@app.get("/memories/{memory_id}/chain", dependencies=[Depends(require_api_key)])
async def get_memory_chain(memory_id: str):
    return {"status": "success", "data": await store.get_memory_chain(memory_id)}

@app.patch("/memories/{memory_id}/tags", dependencies=[Depends(require_api_key)])
async def update_tags(memory_id: str, tags: list[str]):
    validated_tags = Tier2AtomicState(topic_tags=tags).topic_tags
    await store.update_memory_tags(memory_id, validated_tags)
    return {"status": "success"}

@app.delete("/memories/{memory_id}", dependencies=[Depends(require_api_key)])
async def delete_memory(memory_id: str):
    if await store.get_memory_by_id(memory_id) is None:
        raise HTTPException(404, "Memory fragment not found")
    await store.delete_memory_by_id(memory_id)
    return {"status": "success"}

class StudioMetadataUpdate(BaseModel):
    topic_tags: Optional[List[str]] = None
    qos_priority_weight: Optional[int] = Field(default=None, ge=1, le=5)
    emotional_tone: Optional[str] = None

@app.get("/memories/taxonomy", dependencies=[Depends(require_api_key)])
async def get_taxonomy():
    """The approved tones and topic tags from schema.py, so Lyra Studio never keeps its own copy."""
    return {
        "tones": sorted(APPROVED_TONE_VECTORS),
        "topic_tags": sorted(APPROVED_TOPIC_TAGS),
    }

@app.get("/memories/vault", dependencies=[Depends(require_api_key)])
async def get_vault(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_priority: Optional[int] = None,
    tone: Optional[str] = None
):
    results = await store.get_vault_page(limit=limit, offset=offset, min_priority=min_priority, tone=tone)
    return {"data": results}

@app.patch("/memories/{memory_id}/metadata", dependencies=[Depends(require_api_key)])
async def update_memory_metadata(memory_id: str, update_data: StudioMetadataUpdate):
    if await store.get_memory_by_id(memory_id) is None:
        raise HTTPException(404, "Memory fragment not found")

    # Tags go through the same validator as ingestion: cleaned up, and any tag
    # that isn't in the approved list is stored with a "pending:" prefix.
    tags = None
    if update_data.topic_tags is not None:
        typed = list(dict.fromkeys(t for t in update_data.topic_tags if t and t.strip()))
        tags = list(dict.fromkeys(Tier2AtomicState(topic_tags=typed).topic_tags))
        if not tags:
            raise HTTPException(
                422, "A memory needs at least one topic tag. Use 'uncategorized-general' if nothing fits."
            )

    # A tone outside the approved list would never be matched by the mood filter.
    tone = None
    if update_data.emotional_tone is not None:
        tone = update_data.emotional_tone.strip().lower().replace(" ", "_")
        if tone not in APPROVED_TONE_VECTORS:
            raise HTTPException(
                422,
                f"'{tone}' is not an approved tone. Approved tones: {', '.join(sorted(APPROVED_TONE_VECTORS))}"
            )

    success = await store.update_metadata(
        memory_id=memory_id,
        tags=tags,
        priority=update_data.qos_priority_weight,
        tone=tone
    )
    if not success:
        raise HTTPException(404, "Memory fragment not found or update failed")

    # Send back what was actually stored, in the field names Lyra Studio uses
    saved = await store.get_memory_by_id(memory_id) or {}
    saved["qos_priority_weight"] = saved.get("priority_weight", 3)
    saved["emotional_tone"] = saved.get("tone_vector", "neutral_logistical")
    return {"status": "updated", "id": memory_id, "data": saved}

@app.get("/memories/graph/all", dependencies=[Depends(require_api_key)])
async def get_full_graph():
    nodes, edges = await store.get_all_graph_nodes()
    return {"nodes": nodes, "edges": edges}