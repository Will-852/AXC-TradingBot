#!/usr/bin/env python3
"""
news_scraper.py — RSS 新聞收集器
零依賴（stdlib only）。Fetch RSS → 按 symbol 過濾 → 原子寫入 shared/news_feed.json

排程：每 15 分鐘 via LaunchAgent（同 news_sentiment.py 串行）
手動：python3 ~/projects/axc-trading/scripts/news_scraper.py
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

BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
OUTPUT_FILE = SHARED_DIR / "news_feed.json"

# 保留最近 6 小時文章（存檔用），sentiment 分析用 1 小時 window
ARCHIVE_WINDOW_HOURS = 6

# RSS sources
# headers: 額外 HTTP headers（如 BlockBeats 語言設定）
RSS_FEEDS = [
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
    },
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
    {
        "name": "BlockBeats",
        "url": "https://api.theblockbeats.news/v2/rss/newsflash",
        "headers": {"language": "cht"},
    },
    {
        "name": "Odaily",
        "url": "https://rss.odaily.news/rss/newsflash",
    },
    {
        "name": "Investing.com 商品",
        "url": "https://www.investing.com/rss/news_11.rss",
    },
    {
        "name": "Investing.com 經濟",
        "url": "https://www.investing.com/rss/news_14.rss",
    },
]

# Symbol keywords for filtering
SYMBOL_KEYWORDS = {
    "BTCUSDT": ["bitcoin", "btc", "比特幣", "比特币"],
    "ETHUSDT": ["ethereum", "eth", "ether", "以太坊"],
    "SOLUSDT": ["solana", "sol"],
    "XRPUSDT": ["xrp", "ripple"],
    "XAGUSDT": ["silver", "xag", "白銀", "白银"],
    "XAUUSDT": ["gold", "xau", "黃金", "黄金"],
    "POLUSDT": ["polymarket", "pol", "polygon"],
}

# General crypto + macro keywords (always include)
GENERAL_KEYWORDS = [
    # Crypto
    "crypto", "defi", "fed", "rate", "regulation",
    "sec", "etf", "liquidation", "whale", "hack",
    "加密", "監管", "清算", "鯨魚",
    # Macro (配合 macro_monitor.py)
    "treasury", "bond", "yield", "國債", "債券",
    "oil", "crude", "wti", "原油", "石油",
    "dollar", "dxy", "美元",
    "vix", "volatility", "恐慌",
    "nikkei", "日經", "carry trade",
    "inflation", "通脹", "通胀",
    "tariff", "關稅", "关税",
]

# ── 影響力評分（零 API 成本預篩）──
# 分數 0-10。只有 >= INFLUENCE_THRESHOLD 嘅文章先餵 Haiku。
# 設計原則：流動性 + 成交量 > 一切。鯨魚動向 > 政策。長期基本面 = 垃圾。
INFLUENCE_THRESHOLD = 5

# (score, keywords) — 由高到低。匹配到最高嗰個 tier 就停。
INFLUENCE_TIERS = [
    (10, [
        "whale", "鯨魚", "鲸鱼", "大額轉", "大额转", "large transfer",
        "liquidat", "清算", "billion", "百億", "百亿",
        "flash crash", "閃崩", "闪崩", "bank run", "擠兌",
    ]),
    (9, [
        "volume spike", "成交量暴", "liquidity", "流動性", "流动性",
        "fund flow", "資金流", "资金流", "outflow", "inflow",
        "exchange reserve", "交易所儲備", "交易所储备",
    ]),
    (8, [
        "tariff", "關稅", "关税", "sanction", "制裁",
        "rate cut", "rate hike", "降息", "加息",
        "fed ", "fomc", "war ", "戰爭", "战争",
        "invasion", "ceasefire", "停火",
    ]),
    (7, [
        "hack", "exploit", "漏洞", "rug pull",
        "etf approv", "etf reject", "etf filing",
        "sec ", "cftc", "regulation", "監管", "监管",
        "ban ", "禁止", "delist", "下架",
    ]),
    (6, [
        "dxy", "美元指數", "vix", "恐慌指數",
        "oil crash", "油價", "油价", "crude",
        "treasury", "國債", "国债", "bond yield",
        "nikkei", "日經", "carry trade",
        "gold", "黃金", "黄金", "silver", "白銀", "白银",
    ]),
    (5, [
        "on-chain", "鏈上", "链上", "funding rate",
        "open interest", "未平倉", "hashrate",
        "difficulty", "mining revenue",
        "stablecoin", "穩定幣", "稳定币",
    ]),
    # Below threshold — noise
    (3, ["partner", "合作", "launch", "上線", "rebrand", "upgrade"]),
    (2, ["airdrop", "空投", "giveaway", "tutorial", "教學"]),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NEWS] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("news_scraper")


def fetch_rss(url: str, timeout: int = 15, extra_headers: dict | None = None) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    articles = []
    try:
        headers = {"User-Agent": "OpenClaw-NewsScraper/1.0"}
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers)
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


def score_influence(article: dict) -> int:
    """Keyword 預篩影響力分數 0-10。匹配最高 tier 即停。

    流動性/鯨魚 > 政策 > 宏觀 > on-chain > noise。
    成本：零（純 keyword match，唔使 API）。
    """
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    for score, keywords in INFLUENCE_TIERS:
        if any(kw in text for kw in keywords):
            return score
    return 1  # default: generic news


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
        articles = fetch_rss(feed["url"], extra_headers=feed.get("headers"))
        for a in articles:
            a["source"] = feed["name"]
        all_articles.extend(articles)
        log.info(f"  {feed['name']}: {len(articles)} articles")

    # Filter relevant articles
    relevant = [a for a in all_articles if is_relevant(a)]

    # Add symbol matches + influence score
    for article in relevant:
        article["symbols"] = match_symbols(article)
        article["influence_score"] = score_influence(article)

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

    # Backfill influence score for old articles missing it
    for a in deduped:
        if "influence_score" not in a:
            a["influence_score"] = score_influence(a)

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

    # Log influence distribution
    high = sum(1 for a in deduped if a.get("influence_score", 0) >= INFLUENCE_THRESHOLD)
    log.info(
        f"Done: {len(deduped)} articles ({new_count} new), "
        f"{high} high-influence (>={INFLUENCE_THRESHOLD}) → {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
