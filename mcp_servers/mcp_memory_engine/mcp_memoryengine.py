# mcp_servers/mcp_memory_engine/mcp_memoryengine.py
#
# Long-term memory MCP server. This file owns its tools: everything they need
# is defined here, and nothing is imported from a shared tools module.
#
# It exposes ONE tool, recall_long_term_memory, with four actions:
#   search - hybrid search of the vault
#   graph  - memories linked to a knowledge-graph entity
#   chain  - the full conversation a memory fragment belongs to
#   stats  - vault statistics (counts, priority breakdown)
import os
from typing import Optional, List
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

ALLOWED_CHARACTER_NAME = os.getenv("MAIN_COMPANION_NAME", "Lyra")
MEMORY_ENGINE_URL = os.getenv("MEMORY_ENGINE_URL", "http://memory-engine:8000")
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY", "lyra_multipass")

TOOL_NAME = "recall_long_term_memory"

mcp = FastMCP(
    "CompanionLongTermMemory",
    dependencies=["httpx"],
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)
mcp.settings.host = os.getenv("FASTMCP_HOST", "0.0.0.0")
mcp.settings.port = int(os.getenv("FASTMCP_PORT", os.getenv("MCP_PORT", "8300")))


def _engine_headers() -> dict:
    return {"x-api-key": MEMORY_API_KEY}


def _chain_hint(memory_id: str) -> str:
    """The call that expands a memory into its full conversation."""
    return f"{TOOL_NAME}(action='chain', memory_id='{memory_id}')"


async def _search(
    query: str,
    limit: int,
    min_priority: int,
    interaction_type: Optional[str],
    topic_tags: Optional[List[str]],
) -> str:
    payload = {
        "query": query,
        "limit": limit,
        "min_priority": min_priority
    }
    if interaction_type:
        payload["interaction_type"] = interaction_type
    if topic_tags:
        payload["topic_tags"] = topic_tags

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{MEMORY_ENGINE_URL}/memories/search",
                json=payload,
                headers=_engine_headers()
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return "No matching memories found in long-term memory vault."

            lines = [f"Retrieved {len(data)} relevant long-term memory record(s):"]
            for idx, item in enumerate(data, 1):
                mem_id = item.get("id", "")
                summary = item.get("child_vector_text", "")
                parent = item.get("raw_parent_payload", "")
                tags = item.get("topic_tags", [])
                score = item.get("composite_score", 0.0)

                lines.append(f"\n[Memory #{idx} | ID: {mem_id} | Score: {score}]")
                lines.append(f"Summary: {summary}")
                if parent:
                    # Provide immediate context snippet
                    snippet = f"{parent[:350]}..." if len(parent) > 350 else parent
                    lines.append(f"Snippet: {snippet}")
                if tags:
                    lines.append(f"Tags: {', '.join(tags)}")
                lines.append(f"-> To expand full verbatim dialogue chain, invoke {_chain_hint(mem_id)}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error querying memory engine: {str(e)}"


async def _graph(entity: str, predicate: Optional[str], limit: int) -> str:
    params = {"entity": entity, "limit": limit}
    if predicate:
        params["predicate"] = predicate

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{MEMORY_ENGINE_URL}/memories/graph/search",
                params=params,
                headers=_engine_headers()
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return f"No graph memories found linked to entity '{entity}'."

            lines = [f"Found {len(data)} memory link(s) for graph entity '{entity}':"]
            for idx, item in enumerate(data, 1):
                mem_id = item.get("id", "")
                summary = item.get("child_vector_text", "")
                pointers = item.get("graph_pointers", [])
                lines.append(f"\n[Link #{idx} | ID: {mem_id}]")
                lines.append(f"Relationships: {', '.join(pointers)}")
                lines.append(f"Summary: {summary}")
                lines.append(f"-> To expand full dialogue, invoke {_chain_hint(mem_id)}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error querying graph memories: {str(e)}"


async def _chain(memory_id: str) -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{MEMORY_ENGINE_URL}/memories/{memory_id}/chain",
                headers=_engine_headers()
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if not data:
                return f"No memory chain found for fragment ID: {memory_id}"

            lines = [f"Reassembled Dialogue Chain ({len(data)} fragment(s)):"]
            for idx, part in enumerate(data, 1):
                speaker = part.get("speaker_source", "speaker")
                content = part.get("raw_parent_payload") or part.get("child_vector_text", "")
                t_start = part.get("unix_start", "")
                lines.append(f"\n[Part {idx} | {speaker} | Timestamp: {t_start}]")
                lines.append(content)
            return "\n".join(lines)
        except Exception as e:
            return f"Error resolving memory chain: {str(e)}"


async def _stats() -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(f"{MEMORY_ENGINE_URL}/memories/stats", headers=_engine_headers())
            resp.raise_for_status()
            stats = resp.json().get("stats", {})

            total = stats.get("total_memories", 0)
            chains = stats.get("unique_chains", 0)
            qos = stats.get("qos_distribution", {})
            interactions = stats.get("interaction_types", {})

            summary = [
                "=== Lyra Long-Term Memory Vault Diagnostics ===",
                f"Total Memory Fragments: {total}",
                f"Multi-turn Dialogue Chains: {chains}",
                "",
                "QoS Priority Weight Breakdown (1=Transient, 5=Core Identity):",
                f"  • Level 5 (Foundational): {qos.get('5', qos.get(5, 0))}",
                f"  • Level 4 (Important):    {qos.get('4', qos.get(4, 0))}",
                f"  • Level 3 (Standard):     {qos.get('3', qos.get(3, 0))}",
                f"  • Level 2 (Casual):       {qos.get('2', qos.get(2, 0))}",
                f"  • Level 1 (Ephemeral):    {qos.get('1', qos.get(1, 0))}",
                "",
                f"Real Chat Interactions:      {interactions.get('real_chat', 0)}",
                f"Fictional Roleplay Scenarios: {interactions.get('fictional_roleplay', 0)}"
            ]
            return "\n".join(summary)
        except Exception as e:
            return f"Failed to retrieve memory vault stats from engine: {str(e)}"


@mcp.tool()
async def recall_long_term_memory(
    action: str = "search",
    query: Optional[str] = None,
    entity: Optional[str] = None,
    predicate: Optional[str] = None,
    memory_id: Optional[str] = None,
    limit: int = 5,
    min_priority: int = 1,
    interaction_type: Optional[str] = None,
    topic_tags: Optional[List[str]] = None,
    character_calling: str = "Lyra"
) -> str:
    """
    Unified long-term memory tool for the 5-Tier companion LanceDB vault: search it,
    look up memories by knowledge-graph entity, expand a memory into its full
    conversation, and inspect the vault's statistics.

    Typical workflow:
    - Start with 'search' (or 'graph'). Results are summaries, preview snippets, and memory IDs.
    - Evaluate whether the summary provides sufficient context to respond.
    - If exact verbatim dialogue or chronological context is needed, call again with
      action='chain' and the memory ID from the result.

    Args:
        action: Choose from:
            - 'search': Hybrid search of the vault by meaning and keywords. Requires 'query'.
            - 'graph': Memories linked to an entity in the knowledge graph. Requires 'entity'.
            - 'chain': Reassembles the full multi-part conversation a memory belongs to. Requires 'memory_id'.
            - 'stats': Vault statistics: total memories, dialogue chains, QoS priority breakdown,
              and real chat vs roleplay counts. Needs no other arguments.
        query: Required for 'search'. Natural language phrase or keywords describing the memory to pull.
        entity: Required for 'graph'. The entity's exact stored name, lowercase
                (e.g. 'user-josh', 'char-lyra', 'pc-gpu', 'docker').
        predicate: Optional for 'graph'. Relationship to match (e.g. 'owns', 'likes', 'runs').
        memory_id: Required for 'chain'. The ID string of any memory fragment in the conversation.
        limit: Optional for 'search' and 'graph'. Max number of memories to return (default 5).
        min_priority: Optional for 'search'. Minimum QoS priority weight (1-5, default 1).
        interaction_type: Optional for 'search'. Filter by 'real_chat' or 'fictional_roleplay'.
        topic_tags: Optional for 'search'. List of tags to filter by (e.g. ['hardware-gpu']).
        character_calling: Character identity checking (defaults to 'Lyra').
    """
    locked = {
        "search": "Long-term memory storage",
        "graph": "Graph memory storage",
        "chain": "Chain validation",
        "stats": "Long-term memory storage",
    }
    clean_action = (action or "search").strip().lower()

    if character_calling.strip().lower() != ALLOWED_CHARACTER_NAME.lower():
        what = locked.get(clean_action, "Long-term memory storage")
        return f"Access Denied: {what} is locked to {ALLOWED_CHARACTER_NAME}."

    if clean_action == "search":
        if not query or not query.strip():
            return "Error: 'query' is required for a 'search' action."
        return await _search(query, limit, min_priority, interaction_type, topic_tags)

    if clean_action == "graph":
        if not entity or not entity.strip():
            return "Error: 'entity' is required for a 'graph' action."
        return await _graph(entity, predicate, limit)

    if clean_action == "chain":
        if not memory_id or not memory_id.strip():
            return "Error: 'memory_id' is required for a 'chain' action."
        return await _chain(memory_id.strip())

    if clean_action == "stats":
        return await _stats()

    return f"Error: Unsupported action '{action}'. Choose from: 'search', 'graph', 'chain', 'stats'."


if __name__ == "__main__":
    print(f"[FastMCP-Memory] 🚀 Starting SSE server on {mcp.settings.host}:{mcp.settings.port}/sse ...", flush=True)
    mcp.run(transport="sse")
