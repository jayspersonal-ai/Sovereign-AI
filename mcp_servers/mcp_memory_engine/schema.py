import re
from typing import List, Optional, Literal, Set
from pydantic import BaseModel, Field, field_validator, model_validator


# ==============================================================================
# 1. IMMUTABLE STRUCTURAL GRAPH TYPES & CLOSED TAXONOMIES
# ==============================================================================

CLOSED_GRAPH_TYPES: Set[str] = {
    "Agent",     # People, AI personalities, pets, organizations
    "Hardware",  # GPUs, CPUs, cooling, power, motherboards, storage, compute
    "Software",  # Frontends, APIs, models, quant formats, OS, tools, containers
    "Location",  # Physical rooms, disk paths, URLs, git repos
    "Event",     # Milestones, crashes, benchmarks, meetings, dates
    "Concept",   # Philosophies, metaphors, logic, rules, abstract terms
}

GRAPH_VALID_PREDICATES: Set[str] = {
    # Relational, Affective & Social
    "likes", "loves", "dislikes", "hates", "desires", "believes",
    "knows", "friends_with", "related_to", "refers_to", "feels_about",

    # Interaction & Discourse
    "discussed", "experienced", "debated", "metaphor_for",

    # Technical, Operational & Ownership
    "owns", "runs", "configures", "created", "modified",
    "tested", "benchmarked", "located_in",

    # Logic, Hierarchy & Dependencies
    "depends_on", "blocks", "resolves", "prevents",
    "precedes", "supersedes", "caused_by", "originates_from",
    "is_a", "part_of", "requires"
}

GRAPH_VALID_ENTITIES: Set[str] = {
    # Agents
    "user-josh", "user-elena",
    "char-lyra", "char-lameria",
    "dog-norla", "dog-aria", "dog-chewy", "dog-tux",

    # Hardware Components & Platforms
    "pc-cpu", "pc-gpu", "pc-storage", "pc-ram", "pc-vram", "pc-psu",
    "pc-motherboard", "pc-cooling", "pc-chassis", "pc-pcie_riser", "pc-os",
    "vera_rubin_spark", "rtx_5070ti", "dual_rtx_5070ti", "rtx_5090",
    "amd_7600x", "ddr5_64g", "ssd_500gb", "ssd_1tb",

    # Software, Platforms & Orchestration
    "windows11", "ubuntu", "docker", "sillytavern", "open_webui",
    "litellm", "comfyui", "llama_cpp", "vllm", "lancedb",
    "mcp_memory", "mcp_monitor", "prometheus", "cadvisor", "dcgm_exporter",

    # AI Concepts, Formats & Acceleration
    "ai-frontend", "ai-backend", "ai-memory_engine", "ai-tts_stt",
    "ai-model", "ai-quant_format", "ai-context_window",
    "gguf", "safetensors", "cuda", "rocm", "nccl", "nvlink", "tensorrt", "unified_memory",
    "openai_api", "anthropic_api", "ollama_api", "huggingface_hub",

    # Specific Model Weights
    "gemma4_12b", "gemma4_26b_moe", "gemma4_31b", "gemstrike_31b",
    "magnum_32b", "magnum_70b", "nomic_embed",

    # SillyTavern Artifacts
    "st-lorebook", "st-char_card", "st-system_prompt",
    "st-chat_history", "st-author_note", "st-vector_storage",

    # Philosophical & Meta Concepts
    "concept-context_window", "concept-riverbed_metaphor",
    "concept-sapience_simulation", "concept-digital_continuity"
}

APPROVED_TOPIC_TAGS: Set[str] = {
    # Hardware & Infrastructure
    "hardware-gpu", "hardware-networking", "hardware-storage", "hardware-cpu",
    "hardware-ram", "hardware-interface", "hardware-power", "hardware-thermal",
    "hardware-specs", "hardware-verarubinspark",

    # Software & Tooling
    "software-scripting", "software-image_generation", "software-database",
    "software-api", "software-telemetry", "software-docker", "software-mcp",

    # AI & Modeling
    "ai-model", "ai-inference", "ai-frontend", "ai-agentic", "ai-rag",
    "ai-container", "ai-stack", "ai-live2d", "ai-tts_stt", "ai-vision",

    # Meta & Context Management
    "meta-prompting", "meta-context_management", "meta-correction",

    # Projects
    "project-memory_engine", "project-monitor_mcp",

    # Intellectual, Analytical & Medical
    "intellectual-philosophy", "intellectual-logic", "intellectual-analysis",
    "intellectual-discovery", "intellectual-medical", "intellectual-psychology",
    "intellectual-ethics",

    # Life, Routine & Personal
    "life-routine", "life-work", "life-health", "life-goals",
    "life-preferences", "life-family", "life-milestone",

    # Social & Relationships
    "social-milestone", "social-gathering", "social-friend",
    "social-wife", "social-pets",

    # Creative & Roleplay
    "roleplay-exploration", "roleplay-intimacy", "roleplay-storytelling", "roleplay-erotic",

    # Sensory Dimensions
    "sensory-touch", "sensory-scent", "sensory-taste", "sensory-sight", "sensory-sound",

    # Media & Interests
    "media-books", "media-movies", "media-videos", "media-games", "media-anime", "media-pictures",

    # Fallback
    "uncategorized-general"
}

APPROVED_TONE_VECTORS: Set[str] = {
    "supportive_grounding",
    "somber_vulnerable",
    "playful_teasing",
    "thoughtful_analytical",
    "sensual_intimate",
    "energetic_collaborative",
    "neutral_logistical",
    "nostalgic_fond",
    "solemn_contemplative"
}

InteractionType = Literal["real_chat", "fictional_roleplay"]

# Regex matching Type(Value) e.g., Agent(user-josh) or Hardware(dual_rtx_5070ti)
TYPED_INSTANCE_REGEX = re.compile(r"^([A-Za-z]+)\(([^)]+)\)$")


# ==============================================================================
# 2. 5-TIER METADATA STRUCTS & VALIDATORS
# ==============================================================================

class Tier1Routing(BaseModel):
    speaker_source: Literal["user", "char", "dialogue"] = "dialogue"


class Tier2AtomicState(BaseModel):
    topic_tags: List[str]
    graph_pointers: List[str] = Field(default_factory=list)
    custom_tag_proposed: Optional[str] = None
    classification_reasoning: Optional[str] = None

    @field_validator("topic_tags")
    @classmethod
    def validate_tags(cls, tags: List[str]) -> List[str]:
        cleaned_tags = []
        for tag in tags:
            tag_clean = tag.strip().lower().replace(" ", "-").replace("/", "_")
            if tag_clean in APPROVED_TOPIC_TAGS or tag_clean.startswith("pending:"):
                cleaned_tags.append(tag_clean)
            else:
                cleaned_tags.append(f"pending:{tag_clean}")
        return cleaned_tags

    @field_validator("graph_pointers")
    @classmethod
    def validate_graph_pointers(cls, pointers: List[str]) -> List[str]:
        """
        Validates 'Type(Value)->Predicate->Type(Value)' triples against closed taxonomies.
        Gracefully queues unapproved entities/predicates with 'pending:' without dropping the node.
        """
        cleaned = []
        for ptr in pointers:
            raw = ptr.strip()
            if raw.startswith("pending:"):
                cleaned.append(raw)
                continue

            parts = raw.split("->")
            if len(parts) == 3:
                s_raw, predicate_raw, o_raw = (p.strip() for p in parts)
                predicate = predicate_raw.lower().replace(" ", "_")

                s_match = TYPED_INSTANCE_REGEX.match(s_raw)
                o_match = TYPED_INSTANCE_REGEX.match(o_raw)

                if s_match and o_match:
                    s_type, s_val = s_match.group(1), s_match.group(2).strip().lower()
                    o_type, o_val = o_match.group(1), o_match.group(2).strip().lower()

                    # Validate Type categories
                    valid_types = s_type in CLOSED_GRAPH_TYPES and o_type in CLOSED_GRAPH_TYPES
                    if valid_types:
                        # Validate or mark pending for inner values
                        s_formatted = s_val if s_val in GRAPH_VALID_ENTITIES else f"pending:{s_val}"
                        o_formatted = o_val if o_val in GRAPH_VALID_ENTITIES else f"pending:{o_val}"
                        pred_formatted = predicate if predicate in GRAPH_VALID_PREDICATES else f"pending:{predicate}"

                        cleaned.append(f"{s_type}({s_formatted})->{pred_formatted}->{o_type}({o_formatted})")
                        continue

            # Fallback if structure does not match Type(Value)->Predicate->Type(Value)
            cleaned.append(f"pending:{raw}")
        return cleaned


class Tier3Chrono(BaseModel):
    unix_start: int
    unix_end: int

    @field_validator("unix_end")
    @classmethod
    def validate_timestamps(cls, v: int, info) -> int:
        start_time = info.data.get("unix_start")
        if start_time is not None and v < start_time:
            return start_time
        return v


class Tier4QoS(BaseModel):
    priority_weight: int = Field(default=3, ge=1, le=5)


class Tier5NarrativeAnchor(BaseModel):
    interaction_type: InteractionType
    tone_vector: str
    tone_vector_proposed: Optional[str] = None

    @field_validator("tone_vector")
    @classmethod
    def normalize_tone(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")

    @model_validator(mode="after")
    def apply_tone_fallback(self):
        if self.tone_vector not in APPROVED_TONE_VECTORS:
            self.tone_vector_proposed = self.tone_vector
            self.tone_vector = "neutral_logistical"
        return self


# ==============================================================================
# 3. ROOT STORAGE & QUERY STRUCTURES
# ==============================================================================

class CompanionMemoryPacket(BaseModel):
    id: Optional[str] = None
    tier1: Tier1Routing = Field(default_factory=Tier1Routing)
    tier2: Tier2AtomicState
    tier3: Tier3Chrono
    tier4: Tier4QoS
    tier5: Tier5NarrativeAnchor
    child_vector_text: str
    raw_parent_payload: str
    continued_from_parent_id: Optional[str] = None
    chain_root_id: Optional[str] = None


class MemorySearchRequest(BaseModel):
    query: str
    min_priority: int = Field(default=1, ge=1, le=5)
    unix_start: Optional[int] = None
    unix_end: Optional[int] = None
    interaction_type: Optional[InteractionType] = None
    topic_tags: Optional[List[str]] = Field(default_factory=list)
    graph_pointers: Optional[List[str]] = Field(default_factory=list)
    excluded_tones: Optional[List[str]] = Field(default_factory=list)
    decay_lambda: float = 0.023
    limit: int = 5
