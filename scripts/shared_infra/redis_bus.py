"""
redis_bus.py — Redis Streams 基礎設施（零業務邏輯）

提供 connection pool + stream helpers。
所有 AXC 組件透過呢個 module 讀寫 Redis Streams。

設計決策：
- Lazy singleton connection pool — 唔 connect 直到第一次用
- 全部 function 同步（caller 用 run_in_executor 如果需要 async）
- is_available() 做 fast health check — caller 決定 fallback
- maxlen 防止 stream 無限增長 + OOM
"""

import json
import logging
import os
import threading
import time
from typing import Any

import redis

__all__ = [
    "get_pool",
    "is_available",
    "health_check",
    "xadd",
    "xread_latest",
    "ensure_group",
    "xreadgroup",
    "xack",
    "stream_len",
    "stream_info",
    # Stream keys
    "STREAM_KLINES",
    "STREAM_TICKER",
    "STREAM_POLL",
]

logger = logging.getLogger(__name__)

# ── Stream keys ──────────────────────────────────────────────
STREAM_KLINES = "market:klines"
STREAM_TICKER = "market:ticker"
STREAM_POLL = "market:poll"

# ── Config ───────────────────────────────────────────────────
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))

# Default maxlen per stream — 防 OOM
DEFAULT_MAXLEN: dict[str, int] = {
    STREAM_KLINES: 10_000,
    STREAM_TICKER: 1_000,
    STREAM_POLL: 5_000,
}

# ── Connection pool (lazy singleton) ─────────────────────────
_pool: redis.ConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> redis.ConnectionPool:
    """Get or create the singleton connection pool (thread-safe)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:  # double-check after acquiring lock
                _pool = redis.ConnectionPool(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    decode_responses=True,
                    max_connections=10,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                    retry_on_timeout=True,
                )
                logger.info("Redis pool created: %s:%s db=%s", REDIS_HOST, REDIS_PORT, REDIS_DB)
    return _pool


def _client() -> redis.Redis:
    """Get a Redis client from the pool."""
    return redis.Redis(connection_pool=get_pool())


# ── Health ───────────────────────────────────────────────────

def is_available() -> bool:
    """Fast check — can we reach Redis? Returns True/False, never raises."""
    try:
        return _client().ping()
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        return False


def health_check() -> dict[str, Any]:
    """Detailed health: ping latency + stream lengths. For heartbeat/monitoring."""
    result: dict[str, Any] = {"ok": False, "latency_ms": -1, "streams": {}}
    try:
        r = _client()
        t0 = time.monotonic()
        r.ping()
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
        result["ok"] = True
        for stream in (STREAM_KLINES, STREAM_TICKER, STREAM_POLL):
            try:
                result["streams"][stream] = r.xlen(stream)
            except redis.ResponseError:
                result["streams"][stream] = 0  # stream 未建
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        result["error"] = str(exc)
    return result


# ── Write ────────────────────────────────────────────────────

def xadd(stream: str, data: dict, maxlen: int | None = None) -> str | None:
    """
    Append entry to stream. Returns entry ID or None on failure.

    maxlen: approximate trim (~ prefix). Defaults per stream from DEFAULT_MAXLEN.
    """
    if maxlen is None:
        maxlen = DEFAULT_MAXLEN.get(stream, 10_000)
    try:
        entry_id = _client().xadd(stream, data, maxlen=maxlen, approximate=True)
        return entry_id
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        logger.warning("xadd %s failed: %s", stream, exc)
        return None


# ── Read ─────────────────────────────────────────────────────

def xread_latest(stream: str, count: int = 1) -> list[dict]:
    """
    Read the latest N entries from stream (no consumer group).
    Returns list of dicts (newest first). Empty list on error.
    """
    try:
        r = _client()
        # xrevrange: newest first
        entries = r.xrevrange(stream, count=count)
        return [{"id": eid, **fields} for eid, fields in entries]
    except (redis.ConnectionError, redis.TimeoutError, redis.ResponseError) as exc:
        logger.warning("xread_latest %s failed: %s", stream, exc)
        return []


# ── Consumer group ───────────────────────────────────────────

def ensure_group(stream: str, group: str, start_id: str = "0") -> bool:
    """
    Create consumer group if it doesn't exist. Creates stream if needed (MKSTREAM).
    Returns True on success, False on error.
    """
    try:
        _client().xgroup_create(stream, group, id=start_id, mkstream=True)
        logger.info("Created consumer group %s on %s", group, stream)
        return True
    except redis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return True  # already exists — fine
        logger.error("ensure_group %s/%s failed: %s", stream, group, exc)
        return False
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        logger.error("ensure_group %s/%s connection failed: %s", stream, group, exc)
        return False


def xreadgroup(
    group: str,
    consumer: str,
    stream: str,
    count: int = 10,
    block_ms: int = 5000,
) -> list[tuple[str, dict]]:
    """
    Blocking read from consumer group. Returns list of (entry_id, fields).
    Empty list on timeout or error.

    block_ms: how long to block waiting for new entries (0 = forever).
    """
    try:
        result = _client().xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
        if not result:
            return []
        # result = [(stream_name, [(id, fields), ...])]
        return [(eid, fields) for eid, fields in result[0][1]]
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        logger.warning("xreadgroup %s/%s failed: %s", group, consumer, exc)
        return []
    except redis.ResponseError as exc:
        logger.error("xreadgroup %s/%s response error: %s", group, consumer, exc)
        return []


def xack(stream: str, group: str, *entry_ids: str) -> int:
    """Acknowledge entries. Returns count of acknowledged entries."""
    try:
        return _client().xack(stream, group, *entry_ids)
    except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
        logger.warning("xack %s/%s failed: %s", stream, group, exc)
        return 0


# ── Info ─────────────────────────────────────────────────────

def stream_len(stream: str) -> int:
    """Get stream length. Returns 0 on error or if stream doesn't exist."""
    try:
        return _client().xlen(stream)
    except (redis.ConnectionError, redis.TimeoutError, redis.ResponseError):
        return 0


def stream_info(stream: str) -> dict[str, Any]:
    """Get stream info (length, groups, first/last entry). Empty dict on error."""
    try:
        info = _client().xinfo_stream(stream)
        return dict(info)
    except (redis.ConnectionError, redis.TimeoutError, redis.ResponseError):
        return {}


# ── Helpers for structured data ──────────────────────────────

def xadd_json(stream: str, key: str, data: dict, maxlen: int | None = None) -> str | None:
    """Convenience: serialize data as JSON string under a single field key."""
    return xadd(stream, {key: json.dumps(data, default=str)}, maxlen=maxlen)


def decode_json_field(entry: dict, key: str) -> dict | None:
    """Convenience: deserialize a JSON field from a stream entry."""
    raw = entry.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
