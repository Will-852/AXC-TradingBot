#!/usr/bin/env python3
"""
news_scraper.py — RSS 新聞收集器
零依賴（stdlib only）。Fetch RSS → 按 symbol 過濾 → 原子寫入 shared/news_feed.json

排程：每 15 分鐘 via LaunchAgent（同 news_sentiment.py 串行）
手動：python3 ~/.openclaw/scripts/news_scraper.py
"""

import hashlib
import json
import logging
import os
import sys
import tempfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path.home() / ".openclaw"
SHARED_DIR = BASE_DIR / "shared"
OUTPUT_FILE = SHARED_DIR / "news_feed.json"

# 保留最近 6 小時文章（存檔用），sentiment 分析用 1 小時 window
ARCHIVE_WINDOW_HOURS = 6

# RSS sources
RSS_FEEDS = [
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
    },
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
]

# Symbol keywords for filtering
SYMBOL_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc"],
    "ETHUSDT": ["ethereum", "eth", "ether"],
    "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"],
    "XAGUSDT": ["silver", "xag"],
}

# General crypto keywords (always include)
GENERAL_KEYWORDS = [
    "crypto", "defi", "fed", "rate", "regulation",
    "sec", "etf", "liquidation", "whale", "hack",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEWS] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("news_scraper")


def fetch_rss(url: str, timeout: int = 15) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    articles = []
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "OpenClaw-NewsScraper/1.0",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)

        # Handle both RSS 2.0 and Atom formats
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items:
            title = _get_text(item, "title") or _get_text(item, "{http://www.w3.org/2005/Atom}title")
            link = _get_text(item, "link") or _get_attr(item, "{http://www.w3.org/2005/Atom}link", "href")
            pub_date = _get_text(item, "pubDate") or _get_text(item, "{http://www.w3.org/2005/Atom}published")
            description = _get_text(item, "description") or _get_text(item, "{http://www.w3.org/2005/Atom}summary")

            if not title or not link:
                continue

            articles.append({
                "title": title.strip(),
                "link": link.strip(),
                "pub_date": pub_date or "",
                "description": (description or "")[:500],
                "url_hash": hashlib.md5(link.strip().encode()).hexdigest()[:12],
            })

    except Exception as e:
        log.warning(f"RSS fetch failed for {url}: {e}")

    return articles


def _get_text(elem, tag: str) -> str:
    """Get text content from XML element."""
    child = elem.find(tag)
    if child is not None and child.text:
        return child.text
    return ""


def _get_attr(elem, tag: str, attr: str) -> str:
    """Get attribute from XML element."""
    child = elem.find(tag)
    if child is not None:
        return child.get(attr, "")
    return ""


def match_symbols(article: dict) -> list[str]:
    """Match article to trading symbols based on keywords."""
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    matched = []

    for symbol, keywords in SYMBOL_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matched.append(symbol)

    return matched


def is_relevant(article: dict) -> bool:
    """Check if article is relevant to crypto trading."""
    text = (article.get("title", "") + " " + article.get("description", "")).lower()

    # Match specific symbols
    if match_symbols(article):
        return True

    # Match general crypto keywords
    return any(kw in text for kw in GENERAL_KEYWORDS)


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
    log.info("News scraper starting...")

    # Load existing feed for dedup
    existing_hashes = set()
    if OUTPUT_FILE.exists():
        try:
            existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            existing_hashes = {a.get("url_hash") for a in existing.get("articles", [])}
        except Exception:
            pass

    # Fetch all feeds
    all_articles = []
    for feed in RSS_FEEDS:
        articles = fetch_rss(feed["url"])
        for a in articles:
            a["source"] = feed["name"]
        all_articles.extend(articles)
        log.info(f"  {feed['name']}: {len(articles)} articles")

    # Filter relevant articles
    relevant = [a for a in all_articles if is_relevant(a)]

    # Add symbol matches
    for article in relevant:
        article["symbols"] = match_symbols(article)

    # Dedup by URL hash
    seen = set()
    deduped = []
    for a in relevant:
        h = a["url_hash"]
        if h not in seen:
            seen.add(h)
            a["fetched_at"] = datetime.now(timezone.utc).isoformat()
            deduped.append(a)

    new_count = sum(1 for a in deduped if a["url_hash"] not in existing_hashes)

    # Merge with existing (keep within archive window)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ARCHIVE_WINDOW_HOURS)
    cutoff_str = cutoff.isoformat()

    if OUTPUT_FILE.exists():
        try:
            old_data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            old_articles = [
                a for a in old_data.get("articles", [])
                if a.get("fetched_at", "") > cutoff_str
            ]
            # Merge: old (still fresh) + new (deduped)
            old_hashes = {a["url_hash"] for a in old_articles}
            for a in deduped:
                if a["url_hash"] not in old_hashes:
                    old_articles.append(a)
            deduped = old_articles
        except Exception:
            pass

    # Sort by fetch time (newest first)
    deduped.sort(key=lambda a: a.get("fetched_at", ""), reverse=True)

    # Write output
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(deduped),
        "new_this_run": new_count,
        "sources": [f["name"] for f in RSS_FEEDS],
        "articles": deduped,
    }

    atomic_write_json(OUTPUT_FILE, output)
    log.info(f"Done: {len(deduped)} articles ({new_count} new) → {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
