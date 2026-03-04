#!/usr/bin/env python3
"""
embedder.py — Local text vectorization (zero API cost)

Uses hash vectorization with numpy: maps token hashes to a
fixed-dimension vector. No external API needed.

For the trading domain (BTC, XAG, 止損, LONG, etc.), hash
vectors work well because key terms are highly distinguishing.
"""
import hashlib
import re
import numpy as np

DIM = 1024  # Fixed vector dimension


def _tokenize(text: str) -> list[str]:
    """Tokenize Chinese + English + numbers.

    For Chinese: emit each character + bigrams (overlap = good recall).
    For English: emit each word.
    Trading terms like BTC, LONG, SL are kept as whole tokens.
    """
    text = text.lower()
    # Extract Chinese runs, English words, numbers
    raw = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z_][a-zA-Z0-9_]*|[\d.]+', text)

    result = []
    for t in raw:
        if any('\u4e00' <= c <= '\u9fff' for c in t):
            # Chinese: individual chars + bigrams
            for c in t:
                result.append(c)
            for i in range(len(t) - 1):
                result.append(t[i:i+2])
        else:
            result.append(t)
    return result


def _hash_token(token: str) -> int:
    """Hash a token to a bucket index in [0, DIM)."""
    h = int(hashlib.md5(token.encode()).hexdigest(), 16)
    return h % DIM


def embed(text: str) -> np.ndarray:
    """
    Convert text to a fixed-dimension float32 vector.
    Uses hash vectorization — each token hashes to a bucket,
    then L2-normalize for cosine similarity.
    """
    tokens = _tokenize(text)
    vec = np.zeros(DIM, dtype=np.float32)

    if not tokens:
        return vec

    for token in tokens:
        idx = _hash_token(token)
        vec[idx] += 1.0

    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    return vec


def embed_batch(texts: list[str]) -> np.ndarray:
    """Batch embed multiple texts. Returns (N, DIM) array."""
    return np.array([embed(t) for t in texts], dtype=np.float32)
