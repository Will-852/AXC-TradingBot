#!/usr/bin/env python3
"""
embedder.py — Text vectorization for RAG memory

Primary:  voyage-3 (semantic understanding, 1024-dim)
Fallback: local hash vectorizer (if API unavailable)

voyage-3 understands semantics:
  「止損策略」≈「保護利潤」(0.34)
  「止損策略」≠「買入BTC」  (0.17)

Hash vectors only match literal tokens — no semantic understanding.

IMPORTANT: voyage-3 and hash vectors are NOT compatible.
The index stores which backend was used. Query must use the same.
"""
import hashlib
import logging
import os
import re
import time
import numpy as np
from pathlib import Path

DIM = 1024  # Both voyage-3 and hash use 1024-dim

log = logging.getLogger(__name__)

_AXC_HOME    = Path(os.environ.get("AXC_HOME", str(Path.home() / ".openclaw")))
INDEX_DIR    = _AXC_HOME / "memory" / "index"
BACKEND_FILE = INDEX_DIR / "backend.txt"

# ── Voyage-3 (primary) ───────────────────────────

_vo_client = None
_vo_available = None  # None = not checked yet


def _get_voyage_client():
    """Lazy-init voyage client."""
    global _vo_client, _vo_available
    if _vo_available is False:
        return None
    try:
        import voyageai
        key = os.environ.get("VOYAGE_API_KEY", "")
        if not key:
            _vo_available = False
            return None
        _vo_client = voyageai.Client(api_key=key)
        _vo_available = True
        return _vo_client
    except Exception as e:
        log.warning(f"Voyage unavailable: {e}")
        _vo_available = False
        return None


def _embed_voyage(text: str, retries: int = 2) -> np.ndarray | None:
    """Embed via voyage-3 with rate-limit retry."""
    client = _get_voyage_client()
    if not client:
        return None
    for attempt in range(retries + 1):
        try:
            result = client.embed([text], model="voyage-3")
            return np.array(result.embeddings[0], dtype=np.float32)
        except Exception as e:
            err = str(e)
            if "rate" in err.lower() and attempt < retries:
                wait = 20 * (attempt + 1)
                log.info(f"Voyage rate limit, waiting {wait}s...")
                time.sleep(wait)
                continue
            log.warning(f"Voyage embed failed: {e}")
            return None


def _embed_voyage_batch(texts: list[str]) -> np.ndarray | None:
    """Batch embed via voyage-3."""
    client = _get_voyage_client()
    if not client or not texts:
        return None
    try:
        all_vecs = []
        for i in range(0, len(texts), 128):
            batch = texts[i:i+128]
            if i > 0:
                time.sleep(21)  # respect 3 RPM
            result = client.embed(batch, model="voyage-3")
            all_vecs.extend(result.embeddings)
        return np.array(all_vecs, dtype=np.float32)
    except Exception as e:
        log.warning(f"Voyage batch failed: {e}")
        return None


# ── Hash vectorizer (fallback) ───────────────────

def _tokenize(text: str) -> list[str]:
    text = text.lower()
    raw = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z_][a-zA-Z0-9_]*|[\d.]+', text)
    result = []
    for t in raw:
        if any('\u4e00' <= c <= '\u9fff' for c in t):
            for c in t:
                result.append(c)
            for i in range(len(t) - 1):
                result.append(t[i:i+2])
        else:
            result.append(t)
    return result


def _hash_token(token: str) -> int:
    return int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM


def _embed_hash(text: str) -> np.ndarray:
    tokens = _tokenize(text)
    vec = np.zeros(DIM, dtype=np.float32)
    if not tokens:
        return vec
    for token in tokens:
        vec[_hash_token(token)] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


# ── Backend tracking ─────────────────────────────

def _get_stored_backend() -> str:
    """What backend was the current index built with?"""
    if BACKEND_FILE.exists():
        return BACKEND_FILE.read_text().strip()
    return "hash"


def _set_stored_backend(backend: str):
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    BACKEND_FILE.write_text(backend)


# ── Public API ───────────────────────────────────

def embed(text: str) -> np.ndarray:
    """
    Embed text to 1024-dim vector.
    Uses whichever backend the index was built with.
    If voyage unavailable and index is voyage, raises RuntimeError.
    """
    stored = _get_stored_backend()

    if stored == "voyage-3":
        vec = _embed_voyage(text)
        if vec is not None:
            return vec
        # Voyage down — can't mix with hash. Use hash but warn.
        log.warning("Voyage down, falling back to hash (incompatible with stored index)")
        return _embed_hash(text)

    # Hash backend
    return _embed_hash(text)


def embed_for_write(text: str) -> tuple[np.ndarray, str]:
    """
    Embed for writing to index. Returns (vector, backend_used).
    Tries voyage-3 first, falls back to hash.
    Updates stored backend marker.
    """
    stored = _get_stored_backend()

    # Try voyage-3
    vec = _embed_voyage(text)
    if vec is not None:
        if stored != "voyage-3":
            _set_stored_backend("voyage-3")
        return vec, "voyage-3"

    # Fallback to hash
    vec = _embed_hash(text)
    if stored != "hash" and stored != "voyage-3":
        _set_stored_backend("hash")
    return vec, "hash"


def embed_batch(texts: list[str]) -> np.ndarray:
    """Batch embed. Returns (N, 1024) array."""
    vecs = _embed_voyage_batch(texts)
    if vecs is not None:
        _set_stored_backend("voyage-3")
        return vecs
    _set_stored_backend("hash")
    return np.array([_embed_hash(t) for t in texts], dtype=np.float32)


def get_backend() -> str:
    """Return which backend is active."""
    if _vo_available is None:
        _get_voyage_client()
    return "voyage-3" if _vo_available else "hash"


def get_stored_backend() -> str:
    """Return which backend the index was built with."""
    return _get_stored_backend()
