# short_term_memory/token_counter.py
import os
from typing import Optional

try:
    import tiktoken
    # Use cl100k_base or o200k_base depending on model archetype
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODER = None

def count_tokens(text: Optional[str]) -> int:
    """
    Calculates exact or highly accurate token count for a text string.
    Uses tiktoken cl100k_base if available, with a character/word fallback heuristic.
    """
    if not text:
        return 0
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    # Fallback heuristic: ~4 characters per token on average for English / code
    return max(1, len(text) // 4)
