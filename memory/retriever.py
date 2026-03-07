#!/usr/bin/env python3
"""
retriever.py — Semantic memory retrieval (RAG core)

Uses cosine similarity on hash vectors to find the most
relevant historical memories for a given query.

Unlike "last N messages", this retrieves by RELEVANCE:
  "上次止損策略" → finds 3-week-old discussion (score 0.89)
  not just the 5 most recent messages.
"""
import json
import os
import numpy as np
from pathlib import Path

from .embedder import embed

BASE_DIR  = Path(os.environ.get("AXC_HOME", str(Path.home() / ".openclaw")))
INDEX_DIR = BASE_DIR / "memory" / "index"
STORE_DIR = BASE_DIR / "memory" / "store"

EMB_FILE  = INDEX_DIR / "embeddings.npy"
META_FILE = INDEX_DIR / "metadata.json"


def retrieve(query, top_k=8, memory_type=None, min_score=0.10):
    """
    Semantic search: find top-K most relevant memories.

    Args:
        query: search text
        top_k: max results
        memory_type: filter by type (None = all)
        min_score: minimum cosine similarity threshold

    Returns: list of metadata dicts with _score field
    """
    if not EMB_FILE.exists() or not META_FILE.exists():
        return []

    try:
        embs  = np.load(str(EMB_FILE))
        metas = json.loads(META_FILE.read_text())
    except Exception:
        return []

    if embs.shape[0] == 0:
        return []

    # Filter by type if specified
    if memory_type:
        indices = [i for i, m in enumerate(metas)
                   if m.get("type") == memory_type]
        if not indices:
            return []
        filtered_embs  = embs[indices]
        filtered_metas = [metas[i] for i in indices]
    else:
        filtered_embs  = embs
        filtered_metas = metas

    # Embed query
    query_vec = embed(query)

    # Cosine similarity (vectors are already L2-normalized)
    scores = filtered_embs @ query_vec  # (N,)

    # Sort descending, take top-K
    top_indices = np.argsort(scores)[::-1][:top_k]

    results = []
    for idx in top_indices:
        score = float(scores[idx])
        if score < min_score:
            break
        meta = filtered_metas[idx].copy()
        meta["_score"] = round(score, 4)
        results.append(meta)

    return results


def retrieve_full(query, top_k=8, memory_type=None):
    """
    Retrieve with full content from JSONL store.
    """
    metas = retrieve(query, top_k, memory_type)
    if not metas:
        return []

    results = []
    for meta in metas:
        mem_type   = meta.get("type", "conversation")
        store_file = STORE_DIR / f"{mem_type}s.jsonl"
        mid        = meta.get("id", "")

        full = meta.copy()
        if store_file.exists():
            for line in store_file.read_text().splitlines():
                try:
                    record = json.loads(line)
                    if record.get("id") == mid:
                        full["content"] = record.get("content", "")
                        break
                except Exception:
                    continue

        results.append(full)
    return results


def format_for_prompt(memories, max_chars=2500):
    """Format retrieved memories into a prompt-friendly string."""
    if not memories:
        return ""

    parts = ["## 相關歷史記憶"]
    total = 0

    for m in memories:
        ts      = m.get("ts", "")[:16].replace("T", " ")
        mtype   = m.get("type", "?")
        score   = m.get("_score", 0)
        content = m.get("content", m.get("preview", ""))

        entry = f"\n[{ts}] [{mtype}] (相關度:{score:.2f})\n{content}\n"

        if total + len(entry) > max_chars:
            parts.append("\n... (更多記憶已省略)")
            break

        parts.append(entry)
        total += len(entry)

    return "\n".join(parts)
