import os
import time
import math
import uuid
import asyncio
import lancedb
import pyarrow as pa
import httpx
from typing import List, Dict, Any, Optional

from mcp_servers.mcp_memory_engine.schema import CompanionMemoryPacket, MemorySearchRequest

# Volume path updated: E: drive for local Windows setups, /app/data for Linux/Docker containers
DEFAULT_LANCE_PATH = "E:/app/data/lancedb" if os.name == "nt" else "/app/data/lancedb"
LANCE_PATH = os.getenv("LANCE_DB_PATH", DEFAULT_LANCE_PATH)
LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:8400")
LITELLM_KEY = os.getenv("LITELLM_KEY", "sk-local-dev-key")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed")
EMBEDDING_DOC_PREFIX = os.getenv("EMBEDDING_DOC_PREFIX", "search_document: ")
EMBEDDING_QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "search_query: ")
 
EMBEDDING_DIM = 768  # nomic-embed-text output size
 
# Columns to return when querying memory content (excludes heavy 768-dim float vector)
CONTENT_COLUMNS = [
    "id", "speaker_source", "topic_tags", "graph_pointers",
    "custom_tag_proposed", "classification_reasoning",
    "unix_start", "unix_end", "priority_weight",
    "interaction_type", "tone_vector", "tone_vector_proposed",
    "child_vector_text", "raw_parent_payload",
    "continued_from_parent_id", "chain_root_id"
]
 
MEMORY_SCHEMA = pa.schema([
    pa.field("id", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
    pa.field("speaker_source", pa.string()),
    pa.field("topic_tags", pa.list_(pa.string())),
    pa.field("graph_pointers", pa.list_(pa.string())),
    pa.field("custom_tag_proposed", pa.string()),
    pa.field("classification_reasoning", pa.string()),
    pa.field("unix_start", pa.int64()),
    pa.field("unix_end", pa.int64()),
    pa.field("priority_weight", pa.int64()),
    pa.field("interaction_type", pa.string()),
    pa.field("tone_vector", pa.string()),
    pa.field("tone_vector_proposed", pa.string()),
    pa.field("child_vector_text", pa.string()),
    pa.field("raw_parent_payload", pa.string()),
    pa.field("continued_from_parent_id", pa.string()),
    pa.field("chain_root_id", pa.string()),
])
 
PREFILTER_MULTIPLIER = 6
PREFILTER_FLOOR = 30
 
 
def _sql_escape(value: str) -> str:
    """Escapes single quotes for safe interpolation into a LanceDB filter clause."""
    return value.replace("'", "''")
 
 
class MemoryStore:
    def __init__(self):
        os.makedirs(LANCE_PATH, exist_ok=True)
        self.db = lancedb.connect(LANCE_PATH)
        self.table_name = "companion_memories"
        self._client: Optional[httpx.AsyncClient] = None
        self._table_lock = asyncio.Lock()
 
    async def get_client(self) -> httpx.AsyncClient:
        """Returns or lazily creates an httpx.AsyncClient bound to the active running loop."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
 
    async def close(self):
        """Closes persistent HTTP client on app shutdown."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
 
    async def ensure_table(self):
        """Creates the table up front — call from FastAPI's lifespan startup."""
        async with self._table_lock:
            table_names = await asyncio.to_thread(self.db.table_names)
            if self.table_name not in table_names:
                await asyncio.to_thread(
                    self.db.create_table, self.table_name, schema=MEMORY_SCHEMA
                )
 
    async def get_embedding(self, text: str, prefix: str = "") -> List[float]:
        """
        Fetches vector embedding for text from LiteLLM nomic-embed model.
        Prepend asymmetric task prefixes ('search_document: ' or 'search_query: ')
        to preserve nomic-embed's trained retrieval fidelity.
        """
        formatted_text = f"{prefix}{text}" if prefix else text
        client = await self.get_client()
        headers = {"Authorization": f"Bearer {LITELLM_KEY}"}
        resp = await client.post(
            f"{LITELLM_URL}/v1/embeddings",
            json={"model": EMBEDDING_MODEL, "input": formatted_text},
            headers=headers
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
 
    def calculate_composite_score(
        self,
        vector_score: float,
        qos_priority: int,
        unix_start: int,
        decay_lambda: float = 0.023
    ) -> float:
        """Applies QoS immunity floor + exponential time decay scoring math."""
        qos_floor_map = {5: 0.90, 4: 0.60, 3: 0.35, 2: 0.10, 1: 0.02}
        base_qos = qos_floor_map.get(qos_priority, 0.35)
        current_unix = int(time.time())
        age_in_days = max(0.0, (current_unix - unix_start) / 86400.0)
        recency_factor = base_qos + (1.0 - base_qos) * math.exp(-decay_lambda * age_in_days)
        return vector_score * recency_factor
 
    async def insert_memory(self, packet: CompanionMemoryPacket) -> str:
        """Generates embedding and inserts a 5-Tier memory packet into LanceDB."""
        vector = await self.get_embedding(packet.child_vector_text, prefix=EMBEDDING_DOC_PREFIX)
        memory_id = packet.id or str(uuid.uuid4())
 
        chain_root_id = packet.chain_root_id
        if not chain_root_id:
            if packet.continued_from_parent_id:
                parent = await self.get_memory_by_id(packet.continued_from_parent_id)
                chain_root_id = (
                    parent.get("chain_root_id") or packet.continued_from_parent_id
                    if parent else packet.continued_from_parent_id
                )
            else:
                chain_root_id = memory_id
 
        row = {
            "id": memory_id,
            "vector": vector,
            "speaker_source": packet.tier1.speaker_source,
            "topic_tags": packet.tier2.topic_tags,
            "graph_pointers": packet.tier2.graph_pointers,
            "custom_tag_proposed": packet.tier2.custom_tag_proposed or "",
            "classification_reasoning": packet.tier2.classification_reasoning or "",
            "unix_start": packet.tier3.unix_start,
            "unix_end": packet.tier3.unix_end,
            "priority_weight": packet.tier4.priority_weight,
            "interaction_type": packet.tier5.interaction_type,
            "tone_vector": packet.tier5.tone_vector,
            "tone_vector_proposed": packet.tier5.tone_vector_proposed or "",
            "child_vector_text": packet.child_vector_text,
            "raw_parent_payload": packet.raw_parent_payload,
            "continued_from_parent_id": packet.continued_from_parent_id or "",
            "chain_root_id": chain_root_id,
        }
 
        async with self._table_lock:
            table_names = await asyncio.to_thread(self.db.table_names)
            if self.table_name not in table_names:
                await asyncio.to_thread(
                    self.db.create_table, self.table_name, data=[row], schema=MEMORY_SCHEMA
                )
                return memory_id
 
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        await asyncio.to_thread(tbl.add, [row])
        return memory_id
 
    async def create_vector_index(self):
        """Builds an ANN index on the vector column. Safe to call periodically."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        try:
            await asyncio.to_thread(
                tbl.create_index, metric="cosine", vector_column_name="vector"
            )
        except Exception:
            pass
 
    async def create_fts_index(self):
        """Builds a Tantivy BM25 Full-Text Search index on child_vector_text for hybrid search."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        try:
            await asyncio.to_thread(tbl.create_fts_index, "child_vector_text", replace=True)
        except Exception:
            pass
 
    async def get_memory_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a single memory row by its id (excluding raw vector float payload)."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return None
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance()
            .to_table(filter=f"id = '{_sql_escape(memory_id)}'", columns=CONTENT_COLUMNS)
            .to_pylist()
        )
        return rows[0] if rows else None
 
    async def get_memory_chain(self, memory_id: str) -> List[Dict[str, Any]]:
        """Reassembles a multi-fragment memory in chronological order via chain_root_id."""
        anchor = await self.get_memory_by_id(memory_id)
        if anchor is None:
            return []
 
        root_id = anchor.get("chain_root_id") or anchor["id"]
 
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return [anchor]
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
 
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance()
            .to_table(filter=f"chain_root_id = '{_sql_escape(root_id)}'", columns=CONTENT_COLUMNS)
            .to_pylist()
        )
        if not rows:
            return [anchor]
 
        rows.sort(key=lambda r: r["unix_start"])
        return rows
 
    async def query_memories(self, req: MemorySearchRequest) -> List[Dict[str, Any]]:
        """Queries LanceDB with push-down SQL filters and applies QoS decay scoring."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return []
 
        query_vector = await self.get_embedding(req.query, prefix=EMBEDDING_QUERY_PREFIX)
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
 
        filters = [f"priority_weight >= {req.min_priority}"]
        if req.unix_start:
            filters.append(f"unix_end >= {req.unix_start}")
        if req.unix_end:
            filters.append(f"unix_start <= {req.unix_end}")
        if req.interaction_type:
            filters.append(f"interaction_type = '{_sql_escape(req.interaction_type)}'")
            
        if req.excluded_tones:
            escaped_tones = ", ".join(f"'{_sql_escape(t)}'" for t in req.excluded_tones)
            filters.append(f"tone_vector NOT IN ({escaped_tones})")
 
        where_clause = " AND ".join(filters)
        fetch_count = max(PREFILTER_FLOOR, req.limit * PREFILTER_MULTIPLIER)
 
        raw_results = []
        try:
            # Hybrid search: blends BM25 full-text keywords with vector cosine similarity
            search_builder = (
                tbl.search(req.query, query_type="hybrid")
                .vector(query_vector)
                .where(where_clause)
                .limit(fetch_count)
            )
            raw_results = await asyncio.to_thread(search_builder.to_list)
        except Exception:
            # Seamless fallback to pure vector search if FTS index is still compiling or not initialized
            raw_results = await asyncio.to_thread(
                lambda: (
                    tbl.search(query_vector)
                    .metric("cosine")
                    .where(where_clause)
                    .limit(fetch_count)
                    .to_list()
                )
            )
 
        scored_results = []
        for item in raw_results:
            # Strip 768-dim float array from memory context payload to protect LLM prompt token limits
            item.pop("vector", None)
 
            if req.topic_tags:
                item_tags = set(item.get("topic_tags") or [])
                if not item_tags.intersection(set(req.topic_tags)):
                    continue
 
            if req.graph_pointers:
                item_pointers = set(item.get("graph_pointers") or [])
                if not item_pointers.intersection(set(req.graph_pointers)):
                    continue
 
            distance = item.get("_distance", 0.5)
            similarity = max(0.0, 1.0 - distance)
            final_score = self.calculate_composite_score(
                vector_score=similarity,
                qos_priority=item.get("priority_weight", 3),
                unix_start=item.get("unix_start", int(time.time())),
                decay_lambda=req.decay_lambda
            )
            item["composite_score"] = round(final_score, 4)
            scored_results.append(item)
 
        scored_results.sort(key=lambda x: x["composite_score"], reverse=True)
        return scored_results[:req.limit]
 
    async def search_by_graph_entity(self, entity: str, predicate: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """Finds all memories referencing a specific graph entity (Subject or Object) and optional predicate."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return []
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        
        # Load only metadata and content columns, skipping vector arrays
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance().to_table(columns=CONTENT_COLUMNS).to_pylist()
        )
        
        matches = []
        clean_entity = entity.strip()
        for r in rows:
            pointers = r.get("graph_pointers") or []
            matched = False
            for p in pointers:
                parts = p.split("->")
                if len(parts) == 3:
                    subj, pred, obj = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    s_val = subj.split("(", 1)[1].rstrip(")").lower() if "(" in subj else subj.lower()
                    o_val = obj.split("(", 1)[1].rstrip(")").lower() if "(" in obj else obj.lower()
                    entity_hit = (clean_entity.lower() in [s_val, o_val])
                    predicate_hit = (predicate is None or predicate.strip().lower() == pred.lower())
                    if entity_hit and predicate_hit:
                        matched = True
                        break
            if matched:
                matches.append(r)
                if len(matches) >= limit:
                    break
        return matches
 
    async def get_vault_stats(self) -> Dict[str, Any]:
        """Aggregates memory statistics across total counts, QoS priorities, and interaction types."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return {
                "total_memories": 0,
                "qos_distribution": {},
                "interaction_types": {},
                "unique_chains": 0
            }
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        # Scan only the 3 columns needed for stats calculation
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance()
            .to_table(columns=["priority_weight", "interaction_type", "chain_root_id"])
            .to_pylist()
        )
        
        qos_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        interaction_counts = {"real_chat": 0, "fictional_roleplay": 0}
        chains = set()
        
        for r in rows:
            p = r.get("priority_weight", 3)
            qos_counts[p] = qos_counts.get(p, 0) + 1
            itype = r.get("interaction_type", "real_chat")
            interaction_counts[itype] = interaction_counts.get(itype, 0) + 1
            chain_id = r.get("chain_root_id")
            if chain_id:
                chains.add(chain_id)
                
        return {
            "total_memories": len(rows),
            "qos_distribution": qos_counts,
            "interaction_types": interaction_counts,
            "unique_chains": len(chains)
        }
 
    async def compact_vault(self) -> Dict[str, str]:
        """
        Runs file compaction and manifest cleanup.
        Merges fragmented Arrow data files into optimized chunks and purges old commit logs.
        """
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return {"status": "skipped", "message": "Table not initialized"}
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        
        await asyncio.to_thread(tbl.compact_files)
        try:
            await asyncio.to_thread(tbl.cleanup_old_versions)
        except Exception:
            pass
        # Re-index full text search on compacted data
        await self.create_fts_index()
            
        return {"status": "success", "message": "LanceDB compaction, fragment cleanup, and FTS index refresh complete."}
 
    async def update_memory_tags(self, memory_id: str, new_tags: List[str]):
        """Updates topic tags for an existing memory record."""
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        await asyncio.to_thread(
            tbl.update,
            where=f"id = '{_sql_escape(memory_id)}'",
            values={"topic_tags": new_tags, "custom_tag_proposed": ""}
        )
 
    async def delete_memory_by_id(self, memory_id: str):
        """Deletes a memory record by its primary key."""
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        await asyncio.to_thread(
            tbl.delete,
            where=f"id = '{_sql_escape(memory_id)}'"
        )

    async def get_vault_page(self, limit: int = 50, offset: int = 0, min_priority: int = None, tone: str = None) -> List[Dict[str, Any]]:
        """Fetches paginated records for Lyra Studio Vault with schema aliasing."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return []
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance().to_table(columns=CONTENT_COLUMNS).to_pylist()
        )
        
        # Bridge schema for Studio UI
        for r in rows:
            r["qos_priority_weight"] = r.get("priority_weight", 3)
            r["emotional_tone"] = r.get("tone_vector", "neutral_logistical")

        if min_priority is not None:
            rows = [r for r in rows if r["qos_priority_weight"] >= min_priority]
        if tone:
            rows = [r for r in rows if r["emotional_tone"] == tone]

        rows.sort(key=lambda x: x.get("unix_start", 0), reverse=True)
        return rows[offset : offset + limit]

    async def update_metadata(self, memory_id: str, tags: list = None, priority: int = None, tone: str = None) -> bool:
        """In-place update of metadata columns from Lyra Studio."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return False
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        
        updates = {}
        if tags is not None: updates["topic_tags"] = tags
        if priority is not None: updates["priority_weight"] = priority
        if tone is not None: updates["tone_vector"] = tone

        if not updates:
            return True

        await asyncio.to_thread(
            tbl.update,
            where=f"id = '{_sql_escape(memory_id)}'",
            values=updates
        )
        return True

    async def get_all_graph_nodes(self):
        """Builds distinct nodes and edges for the Lyra Studio Knowledge Graph."""
        table_names = await asyncio.to_thread(self.db.table_names)
        if self.table_name not in table_names:
            return [], []
        tbl = await asyncio.to_thread(self.db.open_table, self.table_name)
        rows = await asyncio.to_thread(
            lambda: tbl.to_lance().to_table(columns=["graph_pointers"]).to_pylist()
        )
        
        nodes_dict = {}
        edges = []
        for r in rows:
            for ptr in (r.get("graph_pointers") or []):
                parts = str(ptr).split("->")
                if len(parts) == 3:
                    s_raw, pred, o_raw = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    # Strip Type(...) wrappers for graph view readability
                    s_clean = s_raw.split("(", 1)[1].rstrip(")") if "(" in s_raw else s_raw
                    o_clean = o_raw.split("(", 1)[1].rstrip(")") if "(" in o_raw else o_raw
                    
                    nodes_dict[s_clean] = {"id": s_clean, "label": s_clean}
                    nodes_dict[o_clean] = {"id": o_clean, "label": o_clean}
                    edges.append({"from": s_clean, "to": o_clean, "label": pred})
                    
        return list(nodes_dict.values()), edges
 
store = MemoryStore()
