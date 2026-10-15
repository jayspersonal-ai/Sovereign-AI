import os
import re
import json
import time
import httpx
import base64
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from mcp_client import mcp_gateway
from workspace_events import resolve_in_workspace, is_servable_image, workspace_events_for_tool, save_uploaded_image, MAX_UPLOAD_BYTES

# ==============================================================================
# ENVIRONMENT & ENDPOINTS CONFIGURATION
# ==============================================================================

UPSTREAM_LLM_URL = os.getenv("UPSTREAM_LLM_URL", "http://litellm:8400/v1")
UPSTREAM_API_KEY = os.getenv("UPSTREAM_API_KEY", "")  # no default: set it in .env (must match the LiteLLM master key)
MEMORY_ENGINE_URL = os.getenv("MEMORY_ENGINE_URL", "http://memory-engine:8000")
MEMORY_API_KEY = os.getenv("MEMORY_API_KEY", "")  # no default: set it in .env (must match the memory engine's key)

# ==============================================================================
# LIFESPAN CONTEXT (Shared HTTP Client Pool)
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages a shared, persistent httpx.AsyncClient tied to the FastAPI lifecycle.
    Prevents TCP socket exhaustion and reuses connections via HTTP keep-alive.
    """
    for _name, _value in (("UPSTREAM_API_KEY", UPSTREAM_API_KEY), ("MEMORY_API_KEY", MEMORY_API_KEY)):
        if not _value:
            print(f"[Memory-Proxy] WARNING: {_name} is not set. Add it to your .env file; requests that need it will be refused.", flush=True)
    print("[Memory-Proxy] Initializing shared HTTP client pool...", flush=True)
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200)
    )
    try:
        tools = await mcp_gateway.get_all_tools()
        if tools:
            print(f"[Memory-Proxy] MCP Gateway ready! Loaded {len(tools)} tools: {[t['function']['name'] for t in tools]}", flush=True)
        else:
            print("[Memory-Proxy] MCP Gateway: No tools loaded on startup (will query on demand).", flush=True)
    except Exception as e:
        print(f"[Memory-Proxy] MCP Gateway tool discovery error on startup: {e}", flush=True)
    yield
    print("[Memory-Proxy] Closing shared HTTP client pool...", flush=True)
    await app.state.client.aclose()

app = FastAPI(title="Lyra Cognitive Proxy with MCP Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# PASSIVE RETRIEVAL HELPERS (Strictly Read-Only, No Writes)
# ==============================================================================

GREETING_REGEX = re.compile(
    r"^\s*(hi|hello|hey|greetings|good\s*(morning|afternoon|evening|night)|howdy)\b[!?.]*\s*$",
    re.IGNORECASE
)

def is_greeting(text: str) -> bool:
    return bool(GREETING_REGEX.match(text.strip()))

def extract_text_content(content) -> str:
    """Safely extracts text content, preserving multimodal structures."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
        return " ".join(texts)
    return ""

def clean_user_query(text: str) -> str:
    """Strips SillyTavern macro timestamps and directives before vector matching."""
    text = re.sub(r"Current System Time:.*?\n", "", text, flags=re.IGNORECASE)
    # The frontend puts the send time in front of each message: "[2026-10-13 09:44 PDT, Tue] ..."
    text = re.sub(r"^\s*\[\d{4}-\d{2}-\d{2} [^\]\n]{1,40}\]\s*", "", text)
    text = re.sub(r"^>.*?\n", "", text, flags=re.MULTILINE)
    return text.strip()

# Which characters get long-term memories added to their prompt. A comma-separated list of names
# (MEMORY_CHARACTERS=Lyra,Sam). Empty means every character does.
MEMORY_CHARACTERS = {n.strip().lower() for n in os.getenv("MEMORY_CHARACTERS", "").split(",") if n.strip()}


def wants_memory(body: dict) -> bool:
    """True if this request is for a character that should get memories. The frontend sends the character's name as "character"."""
    if not MEMORY_CHARACTERS:
        return True
    if str(body.get("character") or "").strip().lower() in MEMORY_CHARACTERS:
        return True
    # Other frontends (SillyTavern) name the character on the messages instead.
    return any(
        isinstance(m, dict) and str(m.get("name") or "").strip().lower() in MEMORY_CHARACTERS
        for m in body.get("messages", [])
    )

async def fetch_memories(client: httpx.AsyncClient, query: str, limit: int = 4) -> str:
    """
    Passively queries 5-Tier LanceDB via memory-engine REST API (:8000).
    Strictly read-only: does not trigger any ingestion or modification.
    """
    if not query or len(query.strip()) < 2:
        return ""
    try:
        resp = await client.post(
            f"{MEMORY_ENGINE_URL.rstrip('/')}/memories/search",
            headers={"x-api-key": MEMORY_API_KEY},
            json={"query": query, "limit": limit}
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if not data:
                return ""
            lines = []
            for item in data:
                mem_id = item.get("id", "")
                child_text = item.get("child_vector_text", "")
                tags = ", ".join(item.get("topic_tags", []) or [])
                lines.append(f"- [ID: {mem_id}] (Tags: {tags}): {child_text}")
            return "\n".join(lines)
    except Exception as e:
        print(f"[Memory-Proxy] Passive memory search error: {e}", flush=True)
    return ""

def inject_memory_into_messages(messages: list, memory_block: str):
    """Safely injects memory context without breaking multimodal message structures."""
    if not messages:
        messages.append({"role": "system", "content": memory_block})
        return
    if messages[0].get("role") == "system":
        sys_content = messages[0].get("content")
        if isinstance(sys_content, str):
            messages[0]["content"] = sys_content + memory_block
        elif isinstance(sys_content, list):
            messages[0]["content"].append({"type": "text", "text": memory_block})
    else:
        messages.insert(0, {"role": "system", "content": memory_block})

# ==============================================================================
# REASONING INTERCEPTOR (Narrator Trap guard)
# ==============================================================================
# The Narrator Trap: the model decides in its reasoning to use a tool, then writes
# prose (or narrates a made-up result) instead of emitting a structured tool call.
#
# How the guard works:
#   1. Reasoning streams to the client untouched while the proxy keeps a copy.
#   2. Nothing is decided until the reasoning is over. If the next thing the model
#      emits is a real tool call, the guard does nothing at all.
#   3. If the model starts prose instead, the reasoning is checked for intent to
#      use one of the tools advertised for THIS request (body["tools"], which is
#      refreshed from the MCP gateway on every request). On a hit, the prose is
#      dropped before the client sees it and the model is re-asked for the call.
#   4. A tool call leaked as plain text (call:name{...} or <tool_call>{...}) is
#      parsed and executed directly, with no second model pass.
#
# INTERCEPT_MODE (environment variable):
#   auto   (default) clear first-person intent -> forced tool call;
#          a bare mention -> the model is reminded and may decline
#   force  every detected intent -> forced tool call
#   nudge  every detected intent -> reminder only, the model decides
#   off    guard disabled; the proxy behaves exactly as it did before

INTERCEPT_MODE = os.getenv("INTERCEPT_MODE", "auto").strip().lower()
if INTERCEPT_MODE not in ("auto", "force", "nudge", "off"):
    print(f"[Reasoning-Router] Unknown INTERCEPT_MODE '{INTERCEPT_MODE}', using 'auto'.", flush=True)
    INTERCEPT_MODE = "auto"

# Some chat templates pre-fill the opening reasoning tag, so the stream starts
# mid-thought and only the closing tag ever appears. That is picked up on its own
# once the closing tag arrives; set this to 1 to treat the stream as reasoning from
# the very first token instead.
REASONING_ASSUME_OPEN = os.getenv("REASONING_ASSUME_OPEN", "0").strip().lower() in ("1", "true", "yes", "on")

# Delta / message fields that backends use for reasoning kept apart from content.
REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")

# Inline reasoning tag conventions: (opening tag, closing tag), matched without
# regard to case. Add your own without editing this file through the environment:
#   EXTRA_REASONING_TAGS='[["<plan>", "</plan>"], ["[REASON]", "[/REASON]"]]'
REASONING_TAG_PAIRS = [
    ("<think>", "</think>"),                         # DeepSeek R1, Qwen / QwQ, most local reasoning models
    ("<thinking>", "</thinking>"),                   # Claude-style prompts, Reflection-style finetunes
    ("<thought>", "</thought>"),
    ("<thoughts>", "</thoughts>"),
    ("<reasoning>", "</reasoning>"),
    ("<reflection>", "</reflection>"),
    ("<scratchpad>", "</scratchpad>"),
    ("<seed:think>", "</seed:think>"),               # ByteDance Seed-OSS
    ("[THINK]", "[/THINK]"),                         # Mistral Magistral
    ("\u25c1think\u25b7", "\u25c1/think\u25b7"),     # Moonshot Kimi  (◁think▷ ... ◁/think▷)
    ("<|begin_of_thought|>", "<|end_of_thought|>"),  # Sky-T1 and similar "o1-style" finetunes
    ("<|START_THINKING|>", "<|END_THINKING|>"),      # Cohere Command reasoning
    ("<|channel>thought", "<channel|>"),             # Gemma channel format
    ("<|channel|>analysis<|message|>", "<|end|>"),   # OpenAI Harmony (gpt-oss) analysis channel
]

try:
    for _pair in json.loads(os.getenv("EXTRA_REASONING_TAGS", "") or "[]"):
        if isinstance(_pair, (list, tuple)) and len(_pair) == 2 and all(isinstance(p, str) and p for p in _pair):
            REASONING_TAG_PAIRS.append((_pair[0], _pair[1]))
except Exception as _e:
    print(f"[Reasoning-Router] Ignoring EXTRA_REASONING_TAGS (expected a JSON list of [open, close] pairs): {_e}", flush=True)

_OPEN_TAG_RE = re.compile(
    "|".join(f"(?P<t{i}>{re.escape(o)})" for i, (o, _c) in enumerate(REASONING_TAG_PAIRS)), re.IGNORECASE
)
_CLOSE_TAG_RES = [re.compile(re.escape(c), re.IGNORECASE) for _o, c in REASONING_TAG_PAIRS]
_ANY_CLOSE_TAG_RE = re.compile("|".join(re.escape(c) for _o, c in REASONING_TAG_PAIRS), re.IGNORECASE)
_OPEN_TAGS_LOWER = [o.lower() for o, _c in REASONING_TAG_PAIRS]
_MAX_TAG_LEN = max(len(t) for pair in REASONING_TAG_PAIRS for t in pair)


def split_reasoning(content: str, assume_open: bool = False) -> dict:
    """
    Separates inline reasoning from visible text in a content string.
      reasoning      text found inside reasoning tags
      visible        text outside them
      in_block       True while a reasoning block is still open at the end
      visible_start  index just past the last closed reasoning block
      partial        length of a trailing fragment that may be the start of an opening tag
    """
    reasoning, visible = [], []
    pos, in_block, visible_start = 0, False, 0
    close_re = _ANY_CLOSE_TAG_RE

    first_open = _OPEN_TAG_RE.search(content)
    first_close = _ANY_CLOSE_TAG_RE.search(content)
    if first_close and (first_open is None or first_close.start() < first_open.start()):
        # A closing tag with no opening tag before it: the template pre-filled the opener.
        reasoning.append(content[:first_close.start()])
        pos = visible_start = first_close.end()
    elif assume_open and first_close is None:
        return {"reasoning": content, "visible": "", "in_block": True, "visible_start": len(content), "partial": 0}

    while pos < len(content):
        if not in_block:
            m = _OPEN_TAG_RE.search(content, pos)
            if m is None:
                visible.append(content[pos:])
                break
            visible.append(content[pos:m.start()])
            close_re = _CLOSE_TAG_RES[int(m.lastgroup[1:])]
            in_block, pos = True, m.end()
        else:
            m = close_re.search(content, pos)
            if m is None:
                reasoning.append(content[pos:])
                break
            reasoning.append(content[pos:m.start()])
            in_block, pos = False, m.end()
            visible_start = pos

    partial = 0
    if not in_block:
        tail = content[-_MAX_TAG_LEN:].lower()
        for k in range(min(len(tail), _MAX_TAG_LEN - 1), 0, -1):
            frag = tail[-k:]
            if any(len(tag) > k and tag.startswith(frag) for tag in _OPEN_TAGS_LOWER):
                partial = k
                break

    return {
        "reasoning": "".join(reasoning),
        "visible": "".join(visible),
        "in_block": in_block,
        "visible_start": len(content) if in_block else visible_start,
        "partial": partial,
    }


def first_reasoning_field(obj: dict) -> str:
    """Returns the text of whichever separate reasoning field this backend uses, if any."""
    for field in REASONING_FIELDS:
        value = obj.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


# --- Which tools to look for ---------------------------------------------------

def _is_distinctive(name: str) -> bool:
    """True when a tool name can't be mistaken for an ordinary word (has _ - . a digit, or camelCase)."""
    return bool(re.search(r"[_\-.\d]", name)) or bool(re.search(r"[a-z][A-Z]", name))


class ToolMatcher:
    """
    Finds mentions of the tools advertised for one request.
    Full names always count. The part after the last '__' (the tool's own name without
    its server prefix) counts when exactly one tool has it. Names that read like plain
    words ("search", "read") only count in call-like spots such as call:search,
    `search`, or search( so ordinary sentences don't set the guard off.
    """

    def __init__(self, tool_names: list):
        self.names = list(tool_names)
        self.alias = {}       # lowercase text -> full tool name, matched anywhere
        self.code_alias = {}  # lowercase text -> full tool name, matched only in call-like spots

        shorts = {}
        for name in self.names:
            if "__" in name:
                shorts.setdefault(name.split("__")[-1].lower(), []).append(name)
        candidates = [(name, name) for name in self.names]
        candidates += [(short, owners[0]) for short, owners in shorts.items() if len(owners) == 1]

        for text, full in candidates:
            key = text.lower()
            if len(key) < 2 or key in self.alias or key in self.code_alias:
                continue
            (self.alias if _is_distinctive(text) else self.code_alias)[key] = full

        before, after = r"(?<![A-Za-z0-9_\-])", r"(?![A-Za-z0-9_\-])"

        def alternation(keys):
            return "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))

        self._re_any = re.compile(f"{before}({alternation(self.alias)}){after}", re.IGNORECASE) if self.alias else None
        self._re_code = None
        if self.code_alias:
            alt = alternation(self.code_alias)
            self._re_code = re.compile(
                rf"(?:(?<=call:)|(?<=call: )|(?<=`)|(?<=functions\.))({alt}){after}|{before}({alt})(?=[({{])",
                re.IGNORECASE,
            )

    def resolve(self, text: str):
        key = (text or "").strip().lower()
        return self.alias.get(key) or self.code_alias.get(key)

    def all_keys(self):
        return list(self.alias) + list(self.code_alias)

    def mentions(self, text: str) -> list:
        """Returns [(start, end, full_tool_name, written_as_a_call)] in order of appearance."""
        found = {}
        if self._re_any:
            for m in self._re_any.finditer(text):
                found[m.start(1)] = (m.start(1), m.end(1), self.alias[m.group(1).lower()])
        if self._re_code:
            for m in self._re_code.finditer(text):
                g = 1 if m.group(1) is not None else 2
                found.setdefault(m.start(g), (m.start(g), m.end(g), self.code_alias[m.group(g).lower()]))
        out = []
        for start in sorted(found):
            s, e, full = found[start]
            callish = bool(re.search(r"call:\s?$", text[max(0, s - 6):s], re.IGNORECASE)) or text[e:e + 1] in ("(", "{")
            out.append((s, e, full, callish))
        return out


_MATCHER_CACHE = {}

def get_tool_matcher(tools):
    """Builds (and caches) a matcher for the tool list attached to this request."""
    if INTERCEPT_MODE == "off" or not isinstance(tools, list):
        return None
    names = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn["name"]:
            names.append(fn["name"])
    if not names:
        return None
    key = tuple(names)
    matcher = _MATCHER_CACHE.get(key)
    if matcher is None:
        if len(_MATCHER_CACHE) > 32:
            _MATCHER_CACHE.clear()
        matcher = _MATCHER_CACHE[key] = ToolMatcher(names)
    return matcher


# --- Reading intent out of the reasoning text ----------------------------------

_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

# "A or B", "`A` and `B`" - whatever was said about one tool applies to the other.
_COORDINATION_RE = re.compile(r"[\s,`'\"/&]*(?:and/or|and|or|nor|plus|as well as)?[\s,`'\"]*", re.IGNORECASE)

# Wording that says the tool will NOT be used, looked for just before a mention...
_NEG_BEFORE_RE = re.compile(
    r"(?:\bno need|\bnot need|n't need|\bnever need|\bno reason|n't think|\bnot think|\bdoubt\b|"
    r"\bwithout\b|\binstead of\b|\brather than\b|\bskip(?:ping)?\b|\bavoid(?:ing)?\b|"
    r"\bnot (?:going to |gonna )?(?:use|call|invoke|run|using|calling|invoking|running)\b|"
    r"n't (?:going to |gonna )?(?:use|call|invoke|run|have to|want to)\b|"
    r"\bwon't\b|\bwill not\b|\bshouldn't\b|\bshould not\b|\bwouldn't\b|\bwould not\b|"
    r"\bnot (?:needed|required|necessary)\b)",
    re.IGNORECASE,
)
# ...unless it is a double negative ("I can't answer this without search_memory").
_NEG_OVERRIDE_RE = re.compile(
    r"(?:\bcan't|\bcannot|\bcan not|\bcouldn't|\bunable to|\bnot able to|\bimpossible to|\bhard to)\b[^.;!?\n]{0,60}\bwithout\b",
    re.IGNORECASE,
)
# ...and just after a mention ("search_memory isn't needed here").
_NEG_AFTER_RE = re.compile(
    r"^[^.;!?\n]{0,25}?(?:\b(?:unnecessary|irrelevant|overkill|pointless)\b|"
    r"(?:\bnot|n't)\b[^.;!?\n]{0,15}?\b(?:needed|necessary|required|relevant|useful|helpful|appropriate|applicable|worth|help)\b)",
    re.IGNORECASE,
)
# Wording that refers to a call that already happened rather than one being planned.
_PAST_BEFORE_RE = re.compile(
    r"(?:\b(?:already|previously|earlier|just)\s+(?:used|called|ran|invoked|queried|tried)\b[^.;!?\n]{0,15}$|"
    r"\b(?:results?|output|response|data|answer)\s+(?:from|of)\s+(?:the\s+)?(?:tool\s+)?[`'\"]?$)",
    re.IGNORECASE,
)
_PAST_AFTER_RE = re.compile(
    r"^[`'\"]?\s*(?:tool\s+|call\s+)?(?:already\s+)?(?:returned|gave|showed|said|found|came back|responded|was (?:already )?(?:called|used|run))\b",
    re.IGNORECASE,
)
# Clear first-person intent. This is what earns a forced call in "auto" mode.
_INTENT_CUE_RE = re.compile(
    r"\b(?:i'll|i will|i'm going to|i am going to|i'm gonna|let me|let's|lemme|i should|i need to|i must|i have to|"
    r"i want to|i'd like to|i (?:can|could) (?:use|call|check|run|try|query)|"
    r"(?:going|need|needs|have|time|best|want) to (?:use|call|invoke|run|check|query|try|trigger)|"
    r"should (?:use|call|invoke|run|check|query|try)|will (?:use|call|invoke)|"
    r"calling|invoking|i'm using|i am using|now (?:use|call))\b"
    r"|^\W*(?:call|use|invoke|run|query|trigger)\b",
    re.IGNORECASE,
)
# The model repeating its instructions back, not making a plan of its own.
_SECONDHAND_RE = re.compile(
    r"\byou (?:should|must|can|may|need)\b|\bwhen (?:the )?user\b|\bsystem prompt\b|\binstructions?\b|\bavailable tools?\b|\btools? available\b",
    re.IGNORECASE,
)


def detect_intent(reasoning: str, matcher: "ToolMatcher"):
    """
    Decides whether the reasoning shows intent to use a tool that was never called.
    Returns {"tool", "strong", "evidence"} or None.

    The last thing said about a tool wins, so "I'll use X ... actually, no need for X"
    counts as no intent. A sentence that only lists several tools counts as no intent.
    """
    text = reasoning.replace("\u2019", "'").replace("\u2018", "'")
    ments = matcher.mentions(text)
    if not ments:
        return None

    breaks = [m.span() for m in _SENTENCE_BREAK_RE.finditer(text)]
    sent_starts = [0] + [b[1] for b in breaks]
    sent_ends = [b[0] for b in breaks] + [len(text)]

    def sentence_of(pos):
        lo, hi = 0, len(sent_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sent_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return sent_starts[lo], sent_ends[lo]

    n = len(ments)
    spans = [sentence_of(m[0]) for m in ments]
    negated, past = [False] * n, [False] * n

    for i, (s, e, _name, _callish) in enumerate(ments):
        ss, se = spans[i]
        same_sentence_prev = i > 0 and ments[i - 1][0] >= ss
        before = text[(ments[i - 1][1] if same_sentence_prev else ss):s]
        nxt = ments[i + 1][0] if i + 1 < n and ments[i + 1][0] < se else se
        after = text[e:min(nxt, e + 60)]
        if same_sentence_prev and _COORDINATION_RE.fullmatch(before):
            negated[i], past[i] = negated[i - 1], past[i - 1]
        else:
            negated[i] = bool(_NEG_BEFORE_RE.search(before)) and not _NEG_OVERRIDE_RE.search(before)
            past[i] = bool(_PAST_BEFORE_RE.search(before))
        negated[i] = negated[i] or bool(_NEG_AFTER_RE.search(after))
        past[i] = past[i] or bool(_PAST_AFTER_RE.search(after))

    # "X or Y aren't needed": what is said after Y covers X too.
    for i in range(n - 2, -1, -1):
        if ments[i + 1][0] < spans[i][1] and _COORDINATION_RE.fullmatch(text[ments[i][1]:ments[i + 1][0]]):
            negated[i] = negated[i] or negated[i + 1]

    ruled_out = set()
    for i in range(n - 1, -1, -1):
        s, e, name, callish = ments[i]
        if name in ruled_out:
            continue
        if negated[i]:
            ruled_out.add(name)
            continue
        if past[i]:
            continue
        ss, se = spans[i]
        sentence = text[ss:se]
        strong = callish or (bool(_INTENT_CUE_RE.search(sentence)) and not _SECONDHAND_RE.search(sentence))
        live = [j for j in range(n) if ss <= ments[j][0] < se and not negated[j] and not past[j]]
        if not strong and len({ments[j][2] for j in live}) >= 2:
            continue  # just a list of tools, not a plan
        if strong and live:
            name = ments[live[0]][2]  # "I'll use A and then B" -> A comes first
        return {"tool": name, "strong": strong, "evidence": sentence.strip()[:240]}
    return None


# --- Tool calls leaked as plain text -------------------------------------------

_LEAK_CALL_RE = re.compile(r"call:\s*([A-Za-z0-9_\-.]+)\s*\{", re.IGNORECASE)
_LEAK_TAG_RE = re.compile(r"<\|?tool_call\|?>\s*\{", re.IGNORECASE)
_LEAK_PREFIXES = ("<tool_call>", "<|tool_call>")


def _balanced_braces(text: str, start: int):
    """Returns (the {...} group starting at `start`, whether it was closed)."""
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], True
    return text[start:], False


def _loose_json(raw: str):
    """Parses JSON, tolerating Gemma-style <|"|> string quotes and unquoted keys."""
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        fixed = raw.replace('<|"|>', '"')
        fixed = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', fixed)
        return json.loads(fixed)
    except Exception:
        return None


def find_leaked_call(text: str, matcher: "ToolMatcher"):
    """
    Looks for a tool call written out as text. Returns the last one found as
    {"tool", "args" (dict, or None if unreadable), "complete"} or None.
    """
    hits = []
    for m in _LEAK_CALL_RE.finditer(text):
        full = matcher.resolve(m.group(1))
        if not full:
            continue
        raw, complete = _balanced_braces(text, m.end() - 1)
        args = _loose_json(raw) if complete else None
        hits.append((m.start(), {"tool": full, "args": args if isinstance(args, dict) else None, "complete": complete}))
    for m in _LEAK_TAG_RE.finditer(text):
        raw, complete = _balanced_braces(text, m.end() - 1)
        obj = _loose_json(raw) if complete else None
        if isinstance(obj, dict):
            full = matcher.resolve(str(obj.get("name", "")))
            args = obj.get("arguments", obj.get("parameters", {}))
            if isinstance(args, str):
                args = _loose_json(args)
            if full:
                hits.append((m.start(), {"tool": full, "args": args if isinstance(args, dict) else None, "complete": True}))
        elif not complete:
            name_m = re.search(r'"name"\s*:\s*"([^"]+)"', raw)
            full = matcher.resolve(name_m.group(1)) if name_m else None
            if full:
                hits.append((m.start(), {"tool": full, "args": None, "complete": False}))
    return max(hits, key=lambda h: h[0])[1] if hits else None


def leak_possible(visible: str, matcher: "ToolMatcher") -> bool:
    """True while the visible text so far could still be the start of a leaked tool call."""
    v = visible.lstrip().lower()
    if not v:
        return False
    if any(v.startswith(p) or p.startswith(v) for p in _LEAK_PREFIXES):
        return True
    if "call:".startswith(v):
        return True
    if v.startswith("call:"):
        rest = v[5:].lstrip()
        name = re.match(r"[a-z0-9_\-.]*", rest).group(0)
        tail = rest[len(name):]
        if not tail:
            return any(k.startswith(name) for k in matcher.all_keys())
        return matcher.resolve(name) is not None and (not tail.strip() or tail.lstrip().startswith("{"))
    return False


# --- Watching one streamed response ---------------------------------------------

class ReasoningWatch:
    """
    Follows one streamed model response. Reasoning is passed straight through. The
    first visible text is held back just long enough to decide whether the model is
    answering normally (released, and the watch steps aside) or has fallen into the
    Narrator Trap (self.intent is set and the stream is abandoned).
    """

    def __init__(self, matcher: "ToolMatcher"):
        self.matcher = matcher
        self.field_reasoning = ""  # reasoning that arrived in its own field
        self.content = ""          # all content text so far
        self.forwarded = 0         # how much of self.content the client already has
        self.held = []             # raw SSE lines held back while deciding
        self.released = False
        self.reopened = False
        self.intent = None
        self.template = {}

    def reasoning_text(self) -> str:
        if self.field_reasoning:
            return self.field_reasoning
        return self._split()["reasoning"]

    def _split(self) -> dict:
        if self.field_reasoning:
            # The backend separates reasoning itself, so any tags in content are literal text.
            return {"reasoning": "", "visible": self.content, "in_block": False, "visible_start": 0, "partial": 0}
        return split_reasoning(self.content, REASONING_ASSUME_OPEN)

    def _flush(self, line=None) -> list:
        out = self.held + ([line] if line is not None else [])
        self.held = []
        self.forwarded = len(self.content)
        return out

    def _abandon(self, split: dict, intent: dict) -> list:
        """Sets the intent and returns whatever reasoning the client hasn't been sent yet (including its closing tag)."""
        self.intent = intent
        self.held = []
        pending = self.content[self.forwarded:split["visible_start"]]
        self.forwarded = len(self.content)
        if not pending:
            return []
        chunk = dict(self.template)
        chunk.setdefault("object", "chat.completion.chunk")
        chunk["choices"] = [{"index": 0, "delta": {"content": pending}, "finish_reason": None}]
        return [f"data: {json.dumps(chunk)}"]

    def _intent_from_reasoning(self, split: dict):
        reasoning = self.field_reasoning or split["reasoning"]
        if not reasoning.strip():
            return None
        intent = detect_intent(reasoning, self.matcher)
        if intent is None:
            return None
        intent["args"], intent["source"] = None, "reasoning"
        leak = find_leaked_call(reasoning, self.matcher)
        if leak and leak["complete"] and leak["tool"] == intent["tool"] and leak["args"] is not None and INTERCEPT_MODE != "nudge":
            intent["args"] = leak["args"]
        return intent

    def feed(self, line: str, chunk) -> list:
        """Takes one upstream SSE line (and its parsed chunk, if any). Returns the lines to send on."""
        delta = {}
        if isinstance(chunk, dict):
            self.template = {k: chunk[k] for k in ("id", "object", "created", "model") if k in chunk}
            choices = chunk.get("choices") or []
            if choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
        text = delta.get("content") if isinstance(delta.get("content"), str) else ""
        self.field_reasoning += first_reasoning_field(delta)
        self.content += text

        if self.released:
            # One thing can reopen the question: a closing reasoning tag with no opening
            # tag, meaning everything sent so far was reasoning (pre-filled opener).
            if not text or self.reopened or self.field_reasoning:
                return [line]
            window = self.content[max(0, len(self.content) - len(text) - _MAX_TAG_LEN):]
            if not _ANY_CLOSE_TAG_RE.search(window) or _OPEN_TAG_RE.search(self.content):
                return [line]
            self.released, self.reopened = False, True
            self.forwarded = len(self.content) - len(text)

        if not text:
            # Reasoning-field, role, finish or usage chunk.
            if self.held:
                self.held.append(line)
                return []
            return [line]

        split = self._split()
        visible = split["visible"]
        settled = visible[:len(visible) - split["partial"]] if split["partial"] else visible

        if split["in_block"] or not settled.strip():
            if split["partial"] and not split["in_block"]:
                self.held.append(line)  # might be the start of a reasoning tag; wait one more chunk
                return []
            return self._flush(line)    # still reasoning (or whitespace): goes out live

        # First visible text. Is it a tool call written out as text?
        if leak_possible(settled, self.matcher):
            leak = find_leaked_call(settled, self.matcher)
            if leak and leak["complete"]:
                return self._abandon(split, {
                    "tool": leak["tool"], "strong": True, "args": leak["args"],
                    "evidence": settled.strip()[:240], "source": "leaked call",
                })
            self.held.append(line)
            return []

        # It is prose. Did the reasoning plan a tool call that never came?
        intent = self._intent_from_reasoning(split)
        if intent:
            return self._abandon(split, intent)

        self.released = True
        return self._flush(line)

    def finish(self) -> list:
        """Called when the stream ends without a native tool call."""
        if self.released or self.intent:
            return self._flush()
        split = self._split()
        visible = split["visible"]
        if visible.strip() and leak_possible(visible, self.matcher):
            leak = find_leaked_call(visible, self.matcher)
            if leak:
                return self._abandon(split, {
                    "tool": leak["tool"], "strong": True, "args": leak["args"] if leak["complete"] else None,
                    "evidence": visible.strip()[:240], "source": "leaked call",
                })
        if not visible.strip():
            # The model stopped after thinking without saying or calling anything.
            intent = self._intent_from_reasoning(split)
            if intent:
                return self._abandon(split, intent)
        return self._flush()


# --- Asking the model again ------------------------------------------------------

_FORCE_NOTE = (
    "[Automatic tool-routing note added by the proxy, not written by the user]\n"
    "In your reasoning for this turn you decided to use the tool `{tool}`, but no tool call was emitted. "
    "Your reasoning was:\n\"\"\"\n{reasoning}\n\"\"\"\n"
    "Emit the `{tool}` tool call now, with the arguments you planned. "
    "Do not describe the call or its result in prose."
)
_NUDGE_NOTE = (
    "[Automatic tool-routing note added by the proxy, not written by the user]\n"
    "In your reasoning for this turn you mentioned the tool `{tool}`, but no tool call was emitted. "
    "Your reasoning was:\n\"\"\"\n{reasoning}\n\"\"\"\n"
    "If you still intend to use it, emit the tool call now instead of describing it or its result. "
    "If you don't need it, answer normally and do not mention this note."
)

# How a forced call is requested, strictest first. Backends differ in what they accept,
# so the first style the upstream doesn't reject is remembered for later requests.
_FORCE_STYLES = ("named", "required", "nudge")
_force_style_floor = 0


def build_corrective_body(body: dict, tool: str, reasoning: str, style: str) -> dict:
    """
    Copy of the request for the corrective pass. The note rides on the last user
    message of the COPY only, so the real conversation is never altered and no
    half-finished assistant turn is replayed to the model.
    """
    template = _NUDGE_NOTE if style == "nudge" else _FORCE_NOTE
    note = template.format(tool=tool, reasoning=(reasoning or "").strip()[-2000:])
    new_body = dict(body)
    messages = list(body.get("messages", []))
    if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
        last = dict(messages[-1])
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = content + "\n\n" + note
        elif isinstance(content, list):
            last["content"] = content + [{"type": "text", "text": note}]
        else:
            last["content"] = note
        messages[-1] = last
    else:
        messages.append({"role": "user", "content": note})
    new_body["messages"] = messages
    if style == "named":
        new_body["tool_choice"] = {"type": "function", "function": {"name": tool}}
    elif style == "required":
        new_body["tool_choice"] = "required"
    else:
        new_body["tool_choice"] = "auto"
    return new_body


def corrective_styles(intent: dict) -> list:
    """Which request styles to try for this intent, in order."""
    force = INTERCEPT_MODE == "force" or (INTERCEPT_MODE == "auto" and intent.get("strong"))
    if not force:
        return ["nudge"]
    return list(_FORCE_STYLES[min(_force_style_floor, len(_FORCE_STYLES) - 1):])


def remember_force_style(style: str):
    global _force_style_floor
    idx = _FORCE_STYLES.index(style)
    if idx > _force_style_floor:
        _force_style_floor = idx
        print(f"[Reasoning-Router] Upstream rejected stricter tool_choice forms; using '{style}' from now on.", flush=True)


def log_intercept(intent: dict):
    tier = "clear intent" if intent.get("strong") else "mention only"
    print(f"[Reasoning-Router] Narrator Trap caught: tool '{intent['tool']}' ({intent.get('source', 'reasoning')}, {tier}).", flush=True)
    print(f"   Evidence: {intent.get('evidence', '')!r}", flush=True)


def synthesize_tool_call(tool: str, args: dict) -> dict:
    return {"id": f"call_intercept_{int(time.time() * 1000)}", "name": tool, "args": json.dumps(args)}


async def intercept_non_stream(client: httpx.AsyncClient, endpoint: str, headers: dict, body: dict, msg: dict):
    """
    Non-streaming version of the guard. Given a finished assistant message that has
    no tool call, returns a replacement message that does, or None to leave things be.
    """
    try:
        matcher = get_tool_matcher(body.get("tools"))
        if matcher is None:
            return None
        field_reasoning = first_reasoning_field(msg)
        content = msg.get("content") if isinstance(msg.get("content"), str) else ""
        if field_reasoning:
            reasoning, visible = field_reasoning, content
        else:
            split = split_reasoning(content, False)
            reasoning, visible = split["reasoning"], split["visible"]

        intent = None
        if visible.strip() and leak_possible(visible, matcher):
            leak = find_leaked_call(visible, matcher)
            if leak:
                intent = {"tool": leak["tool"], "strong": True, "args": leak["args"] if leak["complete"] else None,
                          "evidence": visible.strip()[:240], "source": "leaked call"}
        if intent is None and reasoning.strip():
            intent = detect_intent(reasoning, matcher)
            if intent:
                intent["args"], intent["source"] = None, "reasoning"
                leak = find_leaked_call(reasoning, matcher)
                if leak and leak["complete"] and leak["tool"] == intent["tool"] and leak["args"] is not None and INTERCEPT_MODE != "nudge":
                    intent["args"] = leak["args"]
        if intent is None:
            return None

        log_intercept(intent)
        if intent["args"] is not None:
            call = synthesize_tool_call(intent["tool"], intent["args"])
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": call["id"], "type": "function", "function": {"name": call["name"], "arguments": call["args"]}}
            ]}

        styles = corrective_styles(intent)
        for style in styles:
            resp = await client.post(endpoint, json=build_corrective_body(body, intent["tool"], reasoning, style), headers=headers)
            if resp.status_code != 200:
                print(f"[Reasoning-Router] Corrective pass ('{style}') rejected by upstream: HTTP {resp.status_code}", flush=True)
                continue
            if styles[0] != "nudge":
                remember_force_style(style)
            new_msg = (resp.json().get("choices") or [{}])[0].get("message") or {}
            if new_msg.get("tool_calls"):
                return new_msg
            print("[Reasoning-Router] Model did not call the tool on the corrective pass; keeping its original reply.", flush=True)
            return None
    except Exception as e:
        print(f"[Reasoning-Router] Non-stream intercept skipped: {e}", flush=True)
    return None


# ==============================================================================
# SINGLE-PASS STREAM GENERATOR
# ==============================================================================

async def relay_pass(upstream_resp, state: dict, watch=None) -> AsyncGenerator[bytes, None]:
    """
    Relays one upstream stream to the client. Tool-call chunks are collected into
    state["collector"] and kept from the client, as before. With a ReasoningWatch
    attached, the first visible text is checked for the Narrator Trap; if the watch
    sets an intent, the relay stops early and the caller takes over.
    """
    async for line in upstream_resp.aiter_lines():
        if not line:
            continue

        chunk = None
        if line.startswith("data: "):
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if delta.get("tool_calls"):
                        if watch is not None and not state["is_tool_call"]:
                            # A real tool call arrived: the model is behaving, so the watch stands down.
                            for held in watch._flush():
                                yield f"{held}\n\n".encode("utf-8")
                            watch.released = True
                        state["is_tool_call"] = True
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index", 0)
                            if idx not in state["collector"]:
                                state["collector"][idx] = {
                                    "id": tc.get("id", f"call_{idx}"),
                                    "name": "",
                                    "args": ""
                                }
                            
                            if tc.get("id"):
                                state["collector"][idx]["id"] = tc["id"]
                                
                            func_data = tc.get("function") or {}
                            if func_data.get("name"):
                                state["collector"][idx]["name"] += func_data["name"]
                            if func_data.get("arguments"):
                                state["collector"][idx]["args"] += func_data["arguments"]
                                
                        # --- Salvage reasoning data before suppressing the chunk ---
                        delta.pop("tool_calls", None)
                        if delta.get("content") or first_reasoning_field(delta):
                            safe_chunk = chunk.copy()
                            safe_chunk["choices"][0]["delta"] = delta
                            yield f"data: {json.dumps(safe_chunk)}\n\n".encode("utf-8")
                        continue
            except Exception:
                pass

        if state["is_tool_call"]:
            # The usage report of a tool-call pass is not forwarded, but its size is remembered: it is the exact
            # prompt size of the saved conversation (before any tool results), which the frontend uses to keep its count honest.
            if isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict):
                state.setdefault("usages", []).append(chunk["usage"])
            continue

        if watch is not None and not (watch.released and (watch.reopened or watch.field_reasoning)):
            try:
                outgoing = watch.feed(line, chunk)
            except Exception as e:
                # The guard must never break a reply: on any internal error it steps aside.
                print(f"[Reasoning-Router] Watch error, guard disabled for this reply: {e}", flush=True)
                outgoing = watch._flush(line)
                watch.released = watch.reopened = True
                watch.intent = None
            for out_line in outgoing:
                yield f"{out_line}\n\n".encode("utf-8")
            if watch.intent:
                return
            continue

        yield f"{line}\n\n".encode("utf-8")

    if watch is not None and not state["is_tool_call"]:
        try:
            outgoing = watch.finish()
        except Exception as e:
            print(f"[Reasoning-Router] Watch error at end of stream: {e}", flush=True)
            outgoing = watch._flush()
            watch.intent = None
        for out_line in outgoing:
            yield f"{out_line}\n\n".encode("utf-8")


async def single_pass_stream(
    client: httpx.AsyncClient,
    body: dict,
    headers: dict
) -> AsyncGenerator[bytes, None]:
    endpoint = f"{UPSTREAM_LLM_URL.rstrip('/')}/chat/completions"
    req = client.build_request("POST", endpoint, json=body, headers=headers)
    upstream_resp = await client.send(req, stream=True)
    
    if upstream_resp.status_code != 200:
        err_body = await upstream_resp.aread()
        yield f"data: {json.dumps({'error': err_body.decode('utf-8', errors='replace')})}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"
        return

    state = {"is_tool_call": False, "collector": {}}

    # --- REASONING INTERCEPTOR ---
    # Watches for the tools attached to this request (refreshed from the MCP gateway
    # on every request). No tools, or INTERCEPT_MODE=off, means no watch.
    matcher = get_tool_matcher(body.get("tools"))
    watch = ReasoningWatch(matcher) if matcher is not None else None

    try:
        try:
            async for out in relay_pass(upstream_resp, state, watch):
                yield out
        finally:
            await upstream_resp.aclose()

        # --- NARRATOR TRAP: the model planned a tool call but started prose instead ---
        if watch is not None and watch.intent and not state["is_tool_call"]:
            intent = watch.intent
            log_intercept(intent)

            if intent.get("args") is not None:
                # The call was written out as text with readable arguments: run it as-is.
                state["collector"][0] = synthesize_tool_call(intent["tool"], intent["args"])
                state["is_tool_call"] = True
            else:
                # Ask again. Reasoning from the first pass is already on the client's screen;
                # the prose that followed it was never sent.
                corrective_resp = None
                last_error = ""
                styles = corrective_styles(intent)
                for style in styles:
                    corrective_body = build_corrective_body(body, intent["tool"], watch.reasoning_text(), style)
                    req_fix = client.build_request("POST", endpoint, json=corrective_body, headers=headers)
                    resp_fix = await client.send(req_fix, stream=True)
                    if resp_fix.status_code == 200:
                        if styles[0] != "nudge":
                            remember_force_style(style)
                        corrective_resp = resp_fix
                        break
                    last_error = (await resp_fix.aread()).decode("utf-8", errors="replace")
                    await resp_fix.aclose()
                    print(f"[Reasoning-Router] Corrective pass ('{style}') rejected by upstream: HTTP {resp_fix.status_code}", flush=True)

                if corrective_resp is None:
                    yield f"data: {json.dumps({'error': f'Tool Intercept Failed: {last_error}'})}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    return

                try:
                    # No watch on this pass, so it can never loop. If the model answers in
                    # prose here instead, that answer streams to the client as a normal reply.
                    async for out in relay_pass(corrective_resp, state, None):
                        yield out
                finally:
                    await corrective_resp.aclose()

                if not state["is_tool_call"]:
                    print("[Reasoning-Router] Model answered without calling the tool on the corrective pass.", flush=True)

        is_tool_call = state["is_tool_call"]
        tool_call_collector = state["collector"]

        if is_tool_call and tool_call_collector:
            first_usage = (state.get("usages") or [None])[0]
            if first_usage and isinstance(first_usage.get("prompt_tokens"), int):
                yield f"data: {json.dumps({'type': 'prompt_usage', 'prompt_tokens': first_usage['prompt_tokens']})}\n\n".encode("utf-8")
            tool_calls_payload = []
            tool_response_messages = []
            
            for idx in sorted(tool_call_collector.keys()):
                tool_call_item = tool_call_collector[idx]
                func_name = tool_call_item["name"]
                raw_args = tool_call_item["args"]
                call_id = tool_call_item.get("id", f"call_{idx}")
                
                try:
                    parsed_args = json.loads(raw_args) if raw_args else {}
                except Exception:
                    parsed_args = {}
                    
                print(f"[MCP-Gateway] Executing tool: '{func_name}' with args: {parsed_args}", flush=True)
                tool_output = await mcp_gateway.call_tool(func_name, parsed_args)
                
                # --- SVELTE WORKSPACE EVENTS ---
                # Items the AI created (written files, generated images) and items it was shown (images it viewed,
                # files it read). See workspace_events.py. Images are fetched by the browser through GET /v1/workspace/file.
                for ws_event in workspace_events_for_tool(func_name, parsed_args, str(tool_output)):
                    yield f"data: {json.dumps(ws_event)}\n\n".encode("utf-8")
                # --- END WORKSPACE EVENTS ---
                
                output_str = str(tool_output)
                
                # --- DIRECT WORKSPACE IMAGE INGESTION ---
                if output_str.startswith("LOCAL_WORKSPACE_IMAGE:"):
                    image_path = output_str.split(":", 1)[1].strip()
                    try:
                        # Proxy reads the raw, uncompressed file directly from the drive
                        with open(image_path, "rb") as f:
                            raw_bytes = f.read()
                        
                        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                        b64_str = base64.b64encode(raw_bytes).decode("utf-8")
                        b64_uri = f"data:{mime};base64,{b64_str}"
                        
                        tool_response_messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": func_name,
                            "content": "Image successfully loaded into the vision pipeline. See the attached visual data in the following user message."
                        })
                        tool_response_messages.append({
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"Visual data returned from tool '{func_name}':"},
                                {"type": "image_url", "image_url": {"url": b64_uri}}
                            ]
                        })
                    except Exception as e:
                        tool_response_messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": func_name,
                            "content": f"Failed to load local image: {e}"
                        })
                else:
                    tool_response_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": output_str
                    })

                tool_calls_payload.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": func_name,
                        "arguments": raw_args
                    }
                })

            body["messages"].append({
                "role": "assistant",
                "content": "",
                "tool_calls": tool_calls_payload
            })
            body["messages"].extend(tool_response_messages)
            body.pop("tool_choice", None)

            req2 = client.build_request("POST", endpoint, json=body, headers=headers)
            followup_resp = await client.send(req2, stream=True)
            
            if followup_resp.status_code != 200:
                err_body = await followup_resp.aread()
                yield f"data: {json.dumps({'error': f'Tool Followup Failed: {err_body.decode()}'})}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return

            try:
                async for line in followup_resp.aiter_lines():
                    if line.startswith("data: "):
                        yield f"{line}\n\n".encode("utf-8")
            finally:
                await followup_resp.aclose()
        else:
            yield b"data: [DONE]\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

# ==============================================================================
# ENDPOINTS
# ==============================================================================

@app.get("/v1/workspace/file")
async def workspace_file(path: str):
    """Serve a workspace image to the browser. Limited to the AI's workspace folder and plain image types."""
    real = resolve_in_workspace(path)
    if real is None or not os.path.isfile(real):
        return JSONResponse(status_code=404, content={"error": "File not found."})
    if not is_servable_image(real):
        return JSONResponse(status_code=415, content={"error": "Only image files can be served."})
    return FileResponse(
        real,
        media_type=mimetypes.guess_type(real)[0] or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/v1/workspace/upload")
async def workspace_upload(request: Request, folder: str = "uploads"):
    """Save an image the user pasted into the chat into the AI's workspace folder (raw image bytes in the body)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        return JSONResponse(status_code=413, content={"error": "Image is larger than 15 MB."})
    data = await request.body()
    result = save_uploaded_image(data, folder)
    if not result["ok"]:
        return JSONResponse(status_code=result["status"], content={"error": result["error"]})
    return {k: result[k] for k in ("path", "rel", "name", "url")}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "memory-proxy",
        "upstream_llm": UPSTREAM_LLM_URL,
        "memory_engine": MEMORY_ENGINE_URL,
        "mcp_servers": list(mcp_gateway.server_map.keys())
    }

@app.get("/v1/models")
async def list_models(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    headers = {"Authorization": f"Bearer {UPSTREAM_API_KEY}"}
    try:
        resp = await client.get(f"{UPSTREAM_LLM_URL.rstrip('/')}/models", headers=headers)
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"error": f"Upstream LLM router unavailable: {str(e)}"}
        )

@app.post("/v1/tools/execute")
async def execute_tool(request: Request):
    body = await request.json()
    tool_name = body.get("tool_name") or body.get("name")
    tool_args = body.get("arguments") or body.get("args") or {}
    if not tool_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'tool_name' field."})
    try:
        output = await mcp_gateway.call_tool(tool_name, tool_args)
        return {"status": "success", "tool": tool_name, "output": output}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    client: httpx.AsyncClient = request.app.state.client
    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    use_memory = wants_memory(body)

    if use_memory and messages:
        last_user_msg = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        if last_user_msg:
            raw_text = extract_text_content(last_user_msg.get("content", ""))
            cleaned_query = clean_user_query(raw_text)
            if cleaned_query and not is_greeting(cleaned_query):
                print(f"[Memory-Proxy] Passive memory search for: '{cleaned_query[:60]}...'", flush=True)
                memories = await fetch_memories(client, cleaned_query)
                if memories:
                    memory_block = (
                        f"\n\n<lyra_long_term_memories>\n"
                        f"Relevant background memories recalled from your vault:\n"
                        f"{memories}\n"
                        f"</lyra_long_term_memories>\n"
                    )
                    inject_memory_into_messages(messages, memory_block)
                    print("[Memory-Proxy] Injected memory block into context.", flush=True)

    try:
        available_tools = await mcp_gateway.get_all_tools()
        if available_tools:
            body["tools"] = available_tools
            body["tool_choice"] = "auto"
    except Exception as e:
        print(f"[Memory-Proxy] Tool attachment skipped: {e}", flush=True)

    headers = {
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
        "Content-Type": "application/json"
    }

    if stream:
        return StreamingResponse(
            single_pass_stream(client, body, headers),
            media_type="text/event-stream"
        )
    else:
        endpoint = f"{UPSTREAM_LLM_URL.rstrip('/')}/chat/completions"
        try:
            resp = await client.post(endpoint, json=body, headers=headers)
            if resp.status_code != 200:
                return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
            resp_json = resp.json()
            choice = resp_json.get("choices", [{}])[0]
            msg = choice.get("message", {})

            # --- REASONING INTERCEPTOR (non-streaming) ---
            if not msg.get("tool_calls"):
                intercepted_msg = await intercept_non_stream(client, endpoint, headers, body, msg)
                if intercepted_msg:
                    msg = intercepted_msg

            if msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                body["messages"].append(msg)
                for tool_call in tool_calls:
                    func_name = tool_call["function"]["name"]
                    func_args_str = tool_call["function"].get("arguments", "{}")
                    try:
                        func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
                    except Exception:
                        func_args = {}
                    print(f"[MCP-Gateway] Executing tool: '{func_name}' with args: {func_args}", flush=True)
                    tool_output = await mcp_gateway.call_tool(func_name, func_args)
                    
                    output_str = str(tool_output)
                    
                    # --- DIRECT WORKSPACE IMAGE INGESTION ---
                    if output_str.startswith("LOCAL_WORKSPACE_IMAGE:"):
                        image_path = output_str.split(":", 1)[1].strip()
                        try:
                            # Proxy reads the raw, uncompressed file directly from the drive
                            with open(image_path, "rb") as f:
                                raw_bytes = f.read()
                            
                            mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
                            b64_str = base64.b64encode(raw_bytes).decode("utf-8")
                            b64_uri = f"data:{mime};base64,{b64_str}"
                            
                            body["messages"].append({
                                "role": "tool",
                                "tool_call_id": tool_call.get("id", "call_default"),
                                "name": func_name,
                                "content": "Image successfully loaded. See the attached visual data in the following user message."
                            })
                            body["messages"].append({
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": f"Visual data returned from tool '{func_name}':"},
                                    {"type": "image_url", "image_url": {"url": b64_uri}}
                                ]
                            })
                        except Exception as e:
                            body["messages"].append({
                                "role": "tool",
                                "tool_call_id": tool_call.get("id", "call_default"),
                                "name": func_name,
                                "content": f"Failed to load local image: {e}"
                            })
                    else:
                        body["messages"].append({
                            "role": "tool",
                            "tool_call_id": tool_call.get("id", "call_default"),
                            "name": func_name,
                            "content": output_str
                        })
                        
                body["tool_choice"] = "none"
                followup_resp = await client.post(endpoint, json=body, headers=headers)
                return Response(content=followup_resp.content, status_code=followup_resp.status_code, media_type="application/json")
            return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": f"Upstream inference error: {str(e)}"}
            )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8401"))
    uvicorn.run(app, host="0.0.0.0", port=port)
