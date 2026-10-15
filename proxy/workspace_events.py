"""
Workspace helpers for the memory proxy (standard library only).

Two jobs:

1. Safe file access. The browser can't reach files inside Docker, so the proxy serves workspace images at
   GET /v1/workspace/file?path=... (the frontend's /v1 proxy already forwards there). `resolve_in_workspace`
   makes sure that route, and everything else here, can only touch files inside the AI's workspace folder.

2. Workspace events. When a tool opens or creates something, `workspace_events_for_tool` returns the
   `workspace_item` events the frontend shows in its Inspector panel, marked as "viewed" or "created".

Config (environment):
    WORKSPACE_DIR   The AI's folder, the same value the file server uses. Default: /workspace
"""

import os
import re
import secrets
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")

# Only plain image types are served. SVG is left out on purpose: it can contain scripts.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif"}

# Long files are shortened in the panel (the AI still gets the full text from the tool).
MAX_VIEWED_TEXT_CHARS = 60_000

VIEWED_IMAGE_PREFIX = "LOCAL_WORKSPACE_IMAGE:"


def now_iso() -> str:
    """UTC with an explicit offset, so browsers don't read it as local time."""
    return datetime.now(timezone.utc).isoformat()


def resolve_in_workspace(path: str, base: Optional[str] = None) -> Optional[str]:
    """
    The real, absolute path for `path` if it is inside the workspace folder, otherwise None.
    `path` may be absolute or relative to the workspace folder. Symlinks are resolved first, so a link
    that points outside the folder is refused too.
    """
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None
    base_real = os.path.realpath(base or WORKSPACE_DIR)
    cleaned = path.strip().replace("\\", "/")
    candidate = cleaned if os.path.isabs(cleaned) else os.path.join(base_real, cleaned)
    real = os.path.realpath(candidate)
    if real == base_real or real.startswith(base_real + os.sep):
        return real
    return None


def relative_to_workspace(path: str, base: Optional[str] = None) -> Optional[str]:
    """`path` as a forward-slash path relative to the workspace folder, or None if it is outside (or is the folder itself)."""
    real = resolve_in_workspace(path, base)
    if real is None:
        return None
    rel = os.path.relpath(real, os.path.realpath(base or WORKSPACE_DIR)).replace(os.sep, "/")
    return None if rel == "." else rel


def workspace_file_url(rel_path: str) -> str:
    """The address the browser uses to fetch a workspace image (same origin, through the /v1 proxy)."""
    return "/v1/workspace/file?path=" + urllib.parse.quote(rel_path, safe="/")


def is_servable_image(real_path: str) -> bool:
    return os.path.isfile(real_path) and os.path.splitext(real_path)[1].lower() in IMAGE_EXTENSIONS


def _event(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "workspace_item", "item": item}


def _image_event(path: str, source: str, base: Optional[str]) -> Optional[Dict[str, Any]]:
    real = resolve_in_workspace(path, base)
    if real is None or not is_servable_image(real):
        return None
    rel = relative_to_workspace(real, base)
    if rel is None:
        return None
    return _event(
        {
            "id": f"{'view' if source == 'viewed' else 'new'}-{int(time.time() * 1000)}",
            "type": "image",
            "title": os.path.basename(real),
            "filepath": rel,
            # ?v= is the file's modified time, so an edited image is fetched again instead of coming from the browser cache.
            "url": workspace_file_url(rel) + f"&v={int(os.path.getmtime(real))}",
            "source": source,
            "timestamp": now_iso(),
        }
    )


def _text_event(
    path: str,
    content: str,
    source: str,
    title: Optional[str] = None,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    clean = path.replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    clean = clean.lstrip("/")
    if len(content) > MAX_VIEWED_TEXT_CHARS:
        content = (
            content[:MAX_VIEWED_TEXT_CHARS]
            + f"\n\n\u2026 ({len(content):,} characters in total; the first {MAX_VIEWED_TEXT_CHARS:,} are shown here)"
        )
    item: Dict[str, Any] = {
        "id": f"{'view' if source == 'viewed' else 'new'}-{int(time.time() * 1000)}",
        "type": "text",
        "title": title or os.path.basename(clean) or clean or "file",
        "filepath": clean,
        "content": content,
        "source": source,
        "timestamp": now_iso(),
    }
    if url:
        item["url"] = url
    return _event(item)


def _web_address(value: Any) -> Optional[str]:
    """The address if it is a plain http(s) URL, otherwise None (the panel only links to these)."""
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) and len(text) < 2048 else None


def _page_title(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    title = (parsed.netloc + parsed.path).rstrip("/") or url
    return title if len(title) <= 60 else title[:57] + "\u2026"


def workspace_events_for_tool(
    func_name: str,
    args: Optional[Dict[str, Any]],
    output: Any,
    base: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    The `workspace_item` events to send to the frontend after a tool call. Returns [] for anything else.

        viewing an image  (view_workspace_image)         -> image,  source "viewed"
        reading a file    (manage_workspace_files read)   -> text,   source "viewed"
        generating image  (generate_comfy_image)          -> image,  source "created"
        writing a file    (manage_workspace_files write)  -> text,   source "created"
        reading a page    (fetch_webpage_text)            -> text,   source "viewed"
        web search        (search_google_serper)          -> text,   source "viewed"
    """
    args = args if isinstance(args, dict) else {}
    out = str(output if output is not None else "")
    name = (func_name or "").split("__", 1)[-1]  # "files__view_workspace_image" -> "view_workspace_image"
    events: List[Dict[str, Any]] = []

    # An image the AI was just shown. The file server answers with LOCAL_WORKSPACE_IMAGE:<absolute path>.
    if out.startswith(VIEWED_IMAGE_PREFIX):
        event = _image_event(out.split(":", 1)[1].strip(), "viewed", base)
        if event:
            events.append(event)
        return events

    if name == "generate_comfy_image" and "Successfully generated" in out:
        rel = out.split("workspace as:", 1)[-1].strip() if "workspace as:" in out else out.split("images/")[-1].strip()
        if rel and not rel.startswith("images/") and "/" not in rel:
            rel = f"images/{rel}"
        event = _image_event(rel, "created", base)
        if event:
            events.append(event)
        return events

    if name == "manage_workspace_files":
        action = args.get("action")
        source_path = str(args.get("source_path") or "")
        if action == "read" and source_path and not out.startswith("Error"):
            events.append(_text_event(source_path, out, "viewed"))
        elif action == "write" and source_path and out.startswith("Successfully"):
            events.append(_text_event(source_path, str(args.get("content") or ""), "created"))
        return events

    # Web pages and searches from the surfer server. Failures start with "Error", and show nothing.
    if name == "fetch_webpage_text" and out.strip() and not out.startswith("Error"):
        url = _web_address(args.get("url"))
        events.append(
            _text_event(url or "web page", out, "viewed", title=_page_title(url) if url else "Web page", url=url)
        )
    elif name == "search_google_serper" and out.startswith("=== Google Search Results"):
        query = str(args.get("query") or "").strip()
        label = query if len(query) <= 50 else query[:47] + "\u2026"
        events.append(_text_event(f"search: {query}" if query else "search", out, "viewed", title=f"Search: {label}" if query else "Search"))

    return events


# ---------------------------------------------------------------------------------------------
# Images the user pastes or attaches in the chat
# ---------------------------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def sniff_image_extension(data: bytes) -> Optional[str]:
    """The file type from the first bytes (not from the name the browser sent). Plain raster images only."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return None


def _safe_folder(folder: str) -> str:
    parts = [re.sub(r"[^A-Za-z0-9._-]", "-", p).strip(".-") for p in (folder or "").replace("\\", "/").split("/")]
    return "/".join(p for p in parts if p) or "uploads"


def save_uploaded_image(data: bytes, folder: str, base: Optional[str] = None) -> Dict[str, Any]:
    """
    Save pasted image bytes into `folder` (relative to the workspace folder). Returns
    {"ok": True, "path": absolute path, "rel": relative path, "url": address for the browser} or {"ok": False, "status": int, "error": str}.
    """
    if not data:
        return {"ok": False, "status": 400, "error": "Empty upload."}
    if len(data) > MAX_UPLOAD_BYTES:
        return {"ok": False, "status": 413, "error": "Image is larger than 15 MB."}
    ext = sniff_image_extension(data)
    if ext is None:
        return {"ok": False, "status": 415, "error": "Only PNG, JPEG, GIF, WebP and BMP images can be uploaded."}
    target_dir = resolve_in_workspace(_safe_folder(folder), base)
    if target_dir is None:
        return {"ok": False, "status": 400, "error": "Folder is outside the workspace."}
    try:
        os.makedirs(target_dir, exist_ok=True)
        name = f"pasted-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}{ext}"
        full = os.path.join(target_dir, name)
        with open(full, "xb") as fh:
            fh.write(data)
    except OSError as e:
        return {"ok": False, "status": 500, "error": f"Couldn't write into the workspace folder ({e.strerror or e}). Is it mounted read-only for the proxy?"}
    rel = relative_to_workspace(full, base)
    return {
        "ok": True,
        "path": full,
        "rel": rel,
        "name": name,
        "url": workspace_file_url(rel) + f"&v={int(os.path.getmtime(full))}",
    }
