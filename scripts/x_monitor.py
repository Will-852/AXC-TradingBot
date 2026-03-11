#!/usr/bin/env python3
"""
x_monitor.py — X 帳號推文監察

透過 LunarCrush MCP HTTP endpoint 抓取指定 X 帳號推文，
Haiku 篩選 crypto 相關內容，寫入 shared/news_manual.json。

排程：每小時（LaunchAgent ai.openclaw.xmonitor.plist）
手動：python3 ~/projects/axc-trading/scripts/x_monitor.py
"""

import hashlib
import json
import logging
import os
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
NEWS_MANUAL_FILE = SHARED_DIR / "news_manual.json"

# X accounts to monitor
X_ACCOUNTS = ["WhaleInsider", "BTC_Sunny", "KK_aWSB", "NoLimitGains", "lookonchain", "OnchainLens"]

# LunarCrush MCP endpoint
LUNARCRUSH_MCP_URL = "https://lunarcrush.ai/mcp"

# ── Load .env ──
ENV_PATH = BASE_DIR / "secrets" / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

LUNARCRUSH_API_KEY = os.environ.get("LUNARCRUSH_API_KEY", "")
PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
FALLBACK_MODEL = "gpt-5-mini"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [X_MONITOR] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("x_monitor")


def mcp_init_session() -> str:
    """Initialize MCP session, return session ID."""
    url = f"{LUNARCRUSH_MCP_URL}?key={LUNARCRUSH_API_KEY}"
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "x_monitor", "version": "1.0.0"},
        },
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "x_monitor/1.0 (MCP client)",
    })

    with urllib.request.urlopen(req, timeout=15) as resp:
        session_id = resp.headers.get("Mcp-Session-Id", "")
        # Parse SSE response
        body = resp.read().decode()
        for line in body.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if data.get("result", {}).get("serverInfo"):
                    log.info(f"MCP session: {session_id[:16]}...")
                    return session_id

    raise RuntimeError("Failed to initialize MCP session")


def mcp_call_creator(session_id: str, screen_name: str) -> str:
    """Call Creator tool via MCP, return text content."""
    url = f"{LUNARCRUSH_MCP_URL}?key={LUNARCRUSH_API_KEY}"
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "Creator",
            "arguments": {"screenName": screen_name},
        },
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Session-Id": session_id,
        "User-Agent": "x_monitor/1.0 (MCP client)",
    })

    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
        for line in body.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                content = data.get("result", {}).get("content", [])
                for c in content:
                    if c.get("type") == "text":
                        return c["text"]

    return ""


def call_haiku_filter(tweets_text: str) -> list[dict]:
    """Haiku 篩選 crypto 相關推文，返回結構化結果。"""
    if not PROXY_API_KEY:
        log.warning("PROXY_API_KEY not set, skipping Haiku filter")
        return []

    prompt = f"""以下係 4 個 X 帳號嘅推文摘要。篩選出同加密貨幣相關嘅推文（BTC/ETH/SOL/XRP/市場走勢/鯨魚動態/清算/監管/交易所）。

{tweets_text}

回覆 JSON array ONLY（冇 markdown、冇解釋）：
[
  {{"account": "@帳號名", "text": "推文摘要（中文，50字內）", "importance": "high|medium|low"}}
]

篩選規則：
- 只保留同 crypto/金融市場直接相關嘅推文
- importance: high = 鯨魚異動/監管消息/大額清算/黑天鵝, medium = 市場分析/價格預測, low = 一般 crypto 新聞
- 完全無關 crypto 嘅推文唔放入 array
- 如果冇相關推文，回覆空 array: []"""

    text = ""
    # Try Haiku first, fallback to GPT-5-mini
    for model in [CLAUDE_MODEL, FALLBACK_MODEL]:
        try:
            if model == FALLBACK_MODEL:
                # OpenAI-compatible endpoint
                url = f"{PROXY_BASE_URL}/chat/completions"
                payload = json.dumps({
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode("utf-8")
                req = urllib.request.Request(url, data=payload, method="POST", headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {PROXY_API_KEY}",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                # Anthropic endpoint
                url = f"{PROXY_BASE_URL}/messages"
                payload = json.dumps({
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode("utf-8")
                req = urllib.request.Request(url, data=payload, method="POST", headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {PROXY_API_KEY}",
                    "anthropic-version": "2023-06-01",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        break
            log.info(f"Model {model} succeeded")
            break
        except Exception as e:
            log.warning(f"Model {model} failed: {e}")
            text = ""
            continue

    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        result = json.loads(text)
        if not isinstance(result, list):
            return []
        return result
    except json.JSONDecodeError:
        log.warning(f"Failed to parse Haiku response: {text[:200]}")
        return []


def atomic_write_json(path: Path, data):
    """Atomic JSON write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_to_news_manual(entries: list[dict]) -> int:
    """Append filtered tweets to news_manual.json, dedup by text hash.

    Returns number of new entries actually added.
    """
    existing = {"entries": [], "processed_before": ""}
    if NEWS_MANUAL_FILE.exists():
        try:
            existing = json.loads(NEWS_MANUAL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Build set of existing text hashes for dedup
    seen = {hashlib.md5(e.get("text", "").encode()).hexdigest() for e in existing.get("entries", [])}

    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for e in entries:
        text = f"@{e.get('account', '?').lstrip('@')}: {e.get('text', '')}"
        text_hash = hashlib.md5(text.encode()).hexdigest()
        if text_hash in seen:
            continue
        seen.add(text_hash)
        existing["entries"].append({
            "text": text,
            "submitted_at": now,
            "source": "x-monitor",
        })
        added += 1

    # Keep last 100 entries
    existing["entries"] = existing["entries"][-100:]

    if added > 0:
        atomic_write_json(NEWS_MANUAL_FILE, existing)

    return added


def main():
    log.info("X account monitor starting...")

    # Step 1: Init MCP session
    try:
        session_id = mcp_init_session()
    except Exception as e:
        log.error(f"MCP session init failed: {e}")
        return

    # Step 2: Fetch tweets from all accounts
    all_tweets = []
    for account in X_ACCOUNTS:
        try:
            text = mcp_call_creator(session_id, account)
            if text:
                # Truncate to first 500 chars per account (enough for Haiku)
                all_tweets.append(f"=== @{account} ===\n{text[:500]}\n")
                log.info(f"@{account}: {len(text)} chars")
            else:
                log.warning(f"@{account}: empty response")
        except Exception as e:
            log.warning(f"@{account}: {e}")

    if not all_tweets:
        log.info("No tweets fetched, done")
        return

    tweets_text = "\n".join(all_tweets)

    # Step 3: Haiku filter
    try:
        filtered = call_haiku_filter(tweets_text)
    except Exception as e:
        log.error(f"Haiku filter failed: {e}")
        return

    if not filtered:
        log.info("No crypto-related tweets found")
        return

    # Step 4: Write to news_manual.json
    high_items = [e for e in filtered if e.get("importance") == "high"]
    log.info(f"Filtered: {len(filtered)} tweets ({len(high_items)} high importance)")

    added = append_to_news_manual(filtered)
    if added == 0:
        log.info("All tweets already seen (dedup), skipping write")
        return
    log.info(f"Added {added} new entries to {NEWS_MANUAL_FILE}")

    # Log high importance items
    for item in high_items:
        log.info(f"  ⚠️  {item.get('account', '?')}: {item.get('text', '')}")


if __name__ == "__main__":
    main()
