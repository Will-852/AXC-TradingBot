#!/usr/bin/env python3
"""
news_sentiment.py — 新聞情緒分析
讀 shared/news_feed.json → Claude Haiku 情緒分類 → 原子寫入 shared/news_sentiment.json

只分析最近 1 小時嘅文章（避免分析過期舊聞影響決策）。
已分析文章 URL hash 記錄在 sentiment 輸出，下次跳過。

排程：每 15 分鐘（news_scraper.py 之後）
手動：python3 ~/projects/axc-trading/scripts/news_sentiment.py
"""

import json
import logging
import os
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
NEWS_FILE = SHARED_DIR / "news_feed.json"
SENTIMENT_FILE = SHARED_DIR / "news_sentiment.json"

# Only analyze articles from last 1 hour (fresh news only)
ANALYSIS_WINDOW_HOURS = 1

# ── Load .env ──
ENV_PATH = BASE_DIR / "secrets" / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
CLAUDE_MODEL = "claude-haiku-4-5-20251001"  # tier2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SENTIMENT] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("news_sentiment")


def load_analyzed_hashes() -> set:
    """Load previously analyzed article hashes to avoid re-analysis."""
    if not SENTIMENT_FILE.exists():
        return set()
    try:
        data = json.loads(SENTIMENT_FILE.read_text(encoding="utf-8"))
        return set(data.get("analyzed_hashes", []))
    except Exception:
        return set()


def call_haiku(articles: list[dict]) -> dict:
    """Call Claude Haiku for sentiment classification.

    Returns structured sentiment data.
    """
    if not PROXY_API_KEY:
        raise ValueError("PROXY_API_KEY not set")

    # Build article summaries for prompt
    article_texts = []
    for i, a in enumerate(articles[:20], 1):  # max 20 articles per call
        symbols_str = ", ".join(a.get("symbols", [])) or "general"
        article_texts.append(
            f"{i}. [{a.get('source', '?')}] {a.get('title', '?')} "
            f"(symbols: {symbols_str})"
        )

    articles_block = "\n".join(article_texts)

    prompt = f"""Analyze the sentiment of these crypto news headlines.

{articles_block}

Respond in JSON format ONLY (no markdown, no explanation):
{{
  "overall_sentiment": "bullish|bearish|neutral|mixed",
  "confidence": 0.0-1.0,
  "sentiment_by_symbol": {{
    "BTCUSDT": "bullish|bearish|neutral",
    "ETHUSDT": "bullish|bearish|neutral"
  }},
  "key_narratives": ["narrative1", "narrative2"],
  "risk_events": ["event1"],
  "summary": "One sentence overall market sentiment summary"
}}

Only include symbols that appear in the articles. If no articles mention a symbol, omit it.
risk_events: regulatory actions, hacks, major liquidations, black swan events.
key_narratives: dominant themes (ETF flows, rate decisions, adoption, etc.)."""

    url = f"{PROXY_BASE_URL}/messages"
    payload = json.dumps({
        "model": CLAUDE_MODEL,
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

    # Extract text
    content = data.get("content", [])
    text = ""
    for block in content:
        if block.get("type") == "text":
            text = block.get("text", "")
            break

    # Parse JSON from response
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)


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


def main():
    log.info("News sentiment analysis starting...")

    # Load news feed
    if not NEWS_FILE.exists():
        log.warning(f"No news feed found at {NEWS_FILE}")
        return

    try:
        feed = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"Failed to read news feed: {e}")
        return

    articles = feed.get("articles", [])
    if not articles:
        log.info("No articles to analyze")
        return

    # Filter to analysis window (1 hour) only
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ANALYSIS_WINDOW_HOURS)
    cutoff_str = cutoff.isoformat()
    fresh_articles = [
        a for a in articles
        if a.get("fetched_at", "") > cutoff_str
    ]

    # Skip already analyzed
    analyzed_hashes = load_analyzed_hashes()
    new_articles = [
        a for a in fresh_articles
        if a.get("url_hash") not in analyzed_hashes
    ]

    log.info(f"Total: {len(articles)} | Fresh (<{ANALYSIS_WINDOW_HOURS}h): {len(fresh_articles)} | New: {len(new_articles)}")

    if not fresh_articles:
        log.info("No fresh articles within analysis window")
        # Preserve existing sentiment but mark as stale
        if SENTIMENT_FILE.exists():
            try:
                existing = json.loads(SENTIMENT_FILE.read_text(encoding="utf-8"))
                existing["stale"] = True
                existing["updated_at"] = datetime.now(timezone.utc).isoformat()
                atomic_write_json(SENTIMENT_FILE, existing)
            except Exception:
                pass
        return

    # Call Haiku for sentiment
    try:
        sentiment = call_haiku(fresh_articles)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Haiku response: {e}")
        return
    except Exception as e:
        log.error(f"Haiku API call failed: {e}")
        return

    # Track analyzed hashes (union of old + new)
    all_analyzed = analyzed_hashes | {a.get("url_hash") for a in fresh_articles}

    # Build output
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "articles_analyzed": len(fresh_articles),
        "analysis_window_hours": ANALYSIS_WINDOW_HOURS,
        "overall_sentiment": sentiment.get("overall_sentiment", "neutral"),
        "confidence": sentiment.get("confidence", 0.0),
        "sentiment_by_symbol": sentiment.get("sentiment_by_symbol", {}),
        "key_narratives": sentiment.get("key_narratives", []),
        "risk_events": sentiment.get("risk_events", []),
        "summary": sentiment.get("summary", ""),
        "analyzed_hashes": list(all_analyzed)[-200:],  # keep last 200
    }

    atomic_write_json(SENTIMENT_FILE, output)
    log.info(
        f"Sentiment: {output['overall_sentiment']} "
        f"(confidence: {output['confidence']:.1%}) "
        f"→ {SENTIMENT_FILE}"
    )


if __name__ == "__main__":
    main()
