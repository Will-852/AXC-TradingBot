#!/usr/bin/env python3
"""
writer.py — Memory write layer

Writes conversations, trades, analyses, signals to:
  1. store/{type}s.jsonl  (raw text, append-only)
  2. index/embeddings.npy (numpy vector matrix)
  3. index/metadata.json  (metadata for each vector)
"""
import json
import os
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

from .embedder import embed

BASE_DIR  = Path(os.environ.get("AXC_HOME", str(Path.home() / ".openclaw")))
STORE_DIR = BASE_DIR / "memory" / "store"
INDEX_DIR = BASE_DIR / "memory" / "index"

STORE_DIR.mkdir(parents=True, exist_ok=True)
INDEX_DIR.mkdir(parents=True, exist_ok=True)

EMB_FILE  = INDEX_DIR / "embeddings.npy"
META_FILE = INDEX_DIR / "metadata.json"


def _load_index():
    """Load existing vector index."""
    if EMB_FILE.exists() and META_FILE.exists():
        try:
            embs  = np.load(str(EMB_FILE))
            metas = json.loads(META_FILE.read_text())
            return embs, metas
        except Exception:
            pass
    return np.zeros((0, 1024), dtype=np.float32), []


def _save_index(embs, metas):
    np.save(str(EMB_FILE), embs)
    META_FILE.write_text(json.dumps(metas, ensure_ascii=False))


def write_memory(content, memory_type, metadata=None):
    """
    Write one memory record:
      1. Append to store/{type}s.jsonl
      2. Embed and add to vector index
    Returns memory ID.
    """
    ts  = datetime.now(timezone.utc).isoformat()
    mid = f"{memory_type}_{int(time.time() * 1000)}"

    record = {
        "id":      mid,
        "type":    memory_type,
        "content": content,
        "ts":      ts,
        **(metadata or {}),
    }

    # 1. Append to JSONL
    store_file = STORE_DIR / f"{memory_type}s.jsonl"
    with open(store_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 2. Embed
    vec = embed(content)

    # 3. Update index
    embs, metas = _load_index()

    if embs.shape[0] == 0 or embs.shape[1] != vec.shape[0]:
        new_embs = vec.reshape(1, -1)
        if embs.shape[1] != vec.shape[0]:
            metas = []
    else:
        new_embs = np.vstack([embs, vec.reshape(1, -1)])

    metas.append({
        "id":      mid,
        "type":    memory_type,
        "ts":      ts,
        "preview": content[:120],
        **(metadata or {}),
    })

    _save_index(new_embs, metas)
    return mid


# ── Convenience helpers ──

def write_conversation(user_msg, bot_reply):
    """Write one conversation turn."""
    content = f"用戶：{user_msg}\n助手：{bot_reply}"
    return write_memory(content, "conversation", {
        "user_msg":  user_msg[:200],
        "bot_reply": bot_reply[:200],
    })


def write_trade(symbol, side, entry, exit_price=None, pnl=None, notes=""):
    """Write a trade record."""
    parts = [f"交易 {symbol} {side} 入場${entry}"]
    if exit_price is not None:
        parts.append(f"出場${exit_price} PnL:{pnl:+.2f}")
    if notes:
        parts.append(f"備注：{notes}")
    content = " ".join(parts)
    return write_memory(content, "trade", {
        "symbol": symbol, "side": side,
        "entry": entry, "exit": exit_price, "pnl": pnl,
    })


def write_analysis(question, analysis, data_snapshot=None):
    """Write an analysis for future reference."""
    content = f"問題：{question}\n分析：{analysis}"
    return write_memory(content, "analysis", {
        "question": question[:200],
    })


def write_signal(line, source="SCAN_LOG"):
    """Write a signal/trigger record."""
    return write_memory(line, "signal", {"source": source})
