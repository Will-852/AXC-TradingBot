#!/usr/bin/env python3
"""
news_sentiment.py — 新聞情緒分析
讀 shared/news_feed.json → LLM 情緒分類（fallback chain: Haiku → gpt-5-mini） → 原子寫入 shared/news_sentiment.json

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
NEWS_MANUAL_FILE = SHARED_DIR / "news_manual.json"

# Only analyze articles from last 1 hour (fresh news only)
ANALYSIS_WINDOW_HOURS = 1
# Only send articles with influence >= threshold to LLM (save API cost)
INFLUENCE_THRESHOLD = 5

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
MODEL_CHAIN = ["claude-haiku-4-5-20251001", "gpt-5-mini"]  # try in order

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


def load_manual_entries() -> list[dict]:
    """Load unprocessed manual news entries from Telegram submissions."""
    if not NEWS_MANUAL_FILE.exists():
        return []
    try:
        raw = json.loads(NEWS_MANUAL_FILE.read_text(encoding="utf-8"))
        entries = raw.get("entries", [])
        processed_before = raw.get("processed_before", "")
        # Only return entries submitted after last processing
        return [
            e for e in entries
            if e.get("submitted_at", "") > processed_before
        ]
    except (json.JSONDecodeError, OSError):
        return []


def mark_manual_processed():
    """Update processed_before timestamp so entries aren't re-analyzed."""
    if not NEWS_MANUAL_FILE.exists():
        return
    try:
        raw = json.loads(NEWS_MANUAL_FILE.read_text(encoding="utf-8"))
        raw["processed_before"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(NEWS_MANUAL_FILE, raw)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Failed to update manual processed timestamp: {e}")


_HKT = timezone(timedelta(hours=8))

def _parse_pub_time_hkt(raw: str) -> str:
    """Parse various date formats → HH:MM (HKT). Returns '' on failure."""
    from email.utils import parsedate_to_datetime
    try:
        # Try ISO format first: "2026-03-11 09:12:58" or "2026-03-11T09:12:58+00:00"
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # naive → assume UTC
        return dt.astimezone(_HKT).strftime("%H:%M")
    except (ValueError, TypeError):
        pass
    try:
        # RFC 2822: "Wed, 11 Mar 2026 09:17:31 GMT" or "+0800"
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(_HKT).strftime("%H:%M")
    except Exception:
        pass
    return ""


def call_llm(articles: list[dict], manual_entries: list[dict] | None = None) -> dict:
    """Call LLM for sentiment classification, with model fallback chain.

    Tries MODEL_CHAIN in order. Returns structured sentiment data.
    """
    if not PROXY_API_KEY:
        raise ValueError("PROXY_API_KEY not set")

    # Build article summaries for prompt
    article_texts = []
    for i, a in enumerate(articles[:20], 1):  # max 20 articles per call
        symbols_str = ", ".join(a.get("symbols", [])) or "general"
        pub_time = a.get("pub_date", "") or a.get("published", "")
        # Parse HH:MM (HKT) from multiple date formats
        time_str = ""
        if pub_time:
            time_str = _parse_pub_time_hkt(pub_time)
        article_texts.append(
            f"{i}. [{a.get('source', '?')}] {a.get('title', '?')} "
            f"(symbols: {symbols_str}){' [' + time_str + ']' if time_str else ''}"
        )

    # Append manual entries
    if manual_entries:
        for j, m in enumerate(manual_entries[:20], len(article_texts) + 1):
            article_texts.append(
                f"{j}. [USER SUBMITTED] {m.get('text', '?')} "
                f"(symbols: general)"
            )

    articles_block = "\n".join(article_texts)

    prompt = f"""Analyze the sentiment AND market impact of these crypto/macro headlines.
These are PRE-FILTERED high-influence items only. Focus on actionable 4H trading signals.

{articles_block}

PRIORITY HIERARCHY (嚴格遵守):
1. 🐳 鯨魚/大戶動向 = impact 80-100（真金白銀，方向跟佢）
2. 💧 流動性/成交量異常 = impact 70-90（資金流向決定一切）
3. 🏛️ 政策衝擊（關稅/制裁/利率）= impact 50-80
4. 📊 宏觀指標（DXY/VIX/油/金）= impact 40-70
5. 其他 = impact 10-40

TRUMP PATTERN（必須識別）:
特朗普經常宣布強硬政策（關稅、戰爭威脅）→ 市場恐慌大跌 → 2 週內軟化/取消 → 市場反彈。
如果偵測到此模式，risk_events 標注「⚠️ Trump 政策反覆模式：短期恐慌可能係入場機會」。

FILTER（嚴格執行，違反即失敗）:
- 只報已發生嘅事實同已確認嘅行動，唔好報推測
- 禁止用：「可能」「暗示」「預示」「或將」「料將」「預計」「恐將」
- 錯誤示例：❌「暗示風險資產可能面臨避險資金流出」→ 呢啲係猜測，15分鐘後真發生先報
- 正確示例：✅「SEC 主席宣布啟動跨機構監管協調」→ 已發生嘅事實
- 鏈上數據（鯨魚錢包異動、大額轉帳、交易所淨流入流出）= 最高優先級，必須包含

Respond in JSON format ONLY (no markdown, no explanation):
{{
  "overall_sentiment": "bullish|bearish|neutral|mixed",
  "overall_impact": 0-100,
  "confidence": 0.0-1.0,
  "sentiment_by_symbol": {{
    "BTCUSDT": {{"sentiment": "bullish|bearish|neutral", "impact": 0-100}},
    "ETHUSDT": {{"sentiment": "bullish|bearish|neutral", "impact": 0-100}}
  }},
  "key_narratives": [{{"text": "narrative1", "time": "HH:MM", "src": "CoinDesk", "s": "bullish"}}, {{"text": "narrative2", "time": "HH:MM", "src": "Reuters", "s": "bearish"}}],
  "risk_events": [{{"text": "event1", "time": "HH:MM", "src": "CoinTelegraph", "s": "bearish"}}],
  "summary": "One sentence overall market sentiment summary"
}}

IMPORTANT: All text values MUST be in Traditional Chinese (香港繁體中文).
overall_impact: 0=noise, 50=moderate, 100=extreme.
Only include symbols mentioned in articles. time: HH:MM in UTC+8.
src: the source name from the article (e.g. CoinDesk, Reuters, CoinTelegraph, Bloomberg). Use the [source] tag from each article.
s: per-item sentiment, one of "bullish", "bearish", "neutral". Every narrative and risk_event MUST have "s".
Each narrative/risk_event "text" should be 30-80 characters (Chinese). Include key detail — e.g. numbers, asset names, direction. More informative than a bare headline, but still concise."""

    # Try each model in chain until one succeeds AND parses as JSON
    result = None
    for model in MODEL_CHAIN:
        is_anthropic = model.startswith("claude-")
        endpoint = "messages" if is_anthropic else "chat/completions"
        url = f"{PROXY_BASE_URL}/{endpoint}"

        if is_anthropic:
            body = {"model": model, "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {PROXY_API_KEY}",
                       "anthropic-version": "2023-06-01"}
        else:
            body = {"model": model, "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {PROXY_API_KEY}"}

        req = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            if is_anthropic:
                text = next((b["text"] for b in data.get("content", [])
                             if b.get("type") == "text"), "")
            else:
                text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not text:
                log.warning("Model %s returned empty content", model)
                continue
            log.info("Model %s returned %d chars", model, len(text))
        except Exception as e:
            log.warning("Model %s failed: %s", model, e)
            continue

        # Try to parse JSON from this model's response
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        try:
            result = json.loads(cleaned)
            if not isinstance(result, dict):
                log.warning("Model %s returned non-dict JSON: %s", model, type(result).__name__)
                result = None
                continue
            log.info("Model %s JSON parsed OK", model)
            break
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Model %s returned non-JSON (first 200 chars): %s",
                        model, text[:200])
            log.warning("Model %s parse error: %s", model, e)
            continue

    if result is None:
        raise RuntimeError("All models in chain failed or returned non-JSON")

    # Normalize: ensure overall_impact exists
    result.setdefault("overall_impact", 50)
    try:
        result["overall_impact"] = int(result["overall_impact"])
    except (ValueError, TypeError):
        result["overall_impact"] = 50

    # Normalize: per-symbol values may be string (old format) or dict (new)
    syms = result.get("sentiment_by_symbol", {})
    for sym, val in syms.items():
        if isinstance(val, str):
            syms[sym] = {"sentiment": val, "impact": 50}
        elif isinstance(val, dict):
            val.setdefault("sentiment", "neutral")
            try:
                val["impact"] = int(val.get("impact", 50))
            except (ValueError, TypeError):
                val["impact"] = 50

    return result


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

    # Pre-filter: only high-influence articles go to LLM (save API cost)
    # Low-influence = noise (partnerships, airdrops, generic) → skip
    high_influence = [
        a for a in fresh_articles
        if a.get("influence_score", 1) >= INFLUENCE_THRESHOLD
    ]
    skipped = len(fresh_articles) - len(high_influence)
    if skipped > 0:
        log.info(f"Pre-filter: {skipped} low-influence articles skipped (< score {INFLUENCE_THRESHOLD})")

    # Load manual entries from Telegram / x_monitor / macro_monitor
    # These are already pre-filtered, always include
    manual_entries = load_manual_entries()
    if manual_entries:
        log.info(f"Manual entries: {len(manual_entries)}")

    log.info(
        f"Total: {len(articles)} | Fresh (<{ANALYSIS_WINDOW_HOURS}h): {len(fresh_articles)} | "
        f"High-influence: {len(high_influence)} | Manual: {len(manual_entries)}"
    )

    if not high_influence and not manual_entries:
        log.info("No fresh articles or manual entries within analysis window")
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

    # Call LLM for sentiment (only high-influence + manual entries)
    try:
        sentiment = call_llm(high_influence, manual_entries=manual_entries or None)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse LLM response: {e}")
        return
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return

    # LLM 收到真實 [HH:MM] 時間，應該 echo 返。冇嘅先用 run_time fallback
    run_time = datetime.now(_HKT).strftime("%H:%M")
    for n in sentiment.get("key_narratives", []):
        if isinstance(n, dict) and not n.get("time"):
            n["time"] = run_time
    for r in sentiment.get("risk_events", []):
        if isinstance(r, dict) and not r.get("time"):
            r["time"] = run_time

    # Mark manual entries as processed
    if manual_entries:
        mark_manual_processed()

    # Track analyzed hashes (union of old + new — include all fresh, not just high)
    all_analyzed = analyzed_hashes | {a.get("url_hash") for a in fresh_articles}

    # ── 24h rolling accumulation ──
    # Load existing items within 24h, append new ones, dedup by text
    ACCUMULATE_HOURS = 24
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=ACCUMULATE_HOURS)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    existing = {}
    if SENTIMENT_FILE.exists():
        try:
            existing = json.loads(SENTIMENT_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    # Keep old items within 24h window
    old_narratives = [
        n for n in existing.get("key_narratives", [])
        if isinstance(n, dict) and n.get("added_at", "") > cutoff_24h
    ]
    old_risks = [
        r for r in existing.get("risk_events", [])
        if isinstance(r, dict) and r.get("added_at", "") > cutoff_24h
    ]

    # Stamp new items with added_at
    for n in sentiment.get("key_narratives", []):
        if isinstance(n, dict):
            n["added_at"] = now_iso
    for r in sentiment.get("risk_events", []):
        if isinstance(r, dict):
            r["added_at"] = now_iso

    # Fuzzy dedup: same first 20 chars = duplicate (near-identical headlines from different runs)
    DEDUP_PREFIX_LEN = 20
    old_narrative_prefixes = {n.get("text", "")[:DEDUP_PREFIX_LEN] for n in old_narratives if n.get("text")}
    new_narratives = [
        n for n in sentiment.get("key_narratives", [])
        if isinstance(n, dict) and n.get("text") and n["text"][:DEDUP_PREFIX_LEN] not in old_narrative_prefixes
    ]
    old_risk_prefixes = {r.get("text", "")[:DEDUP_PREFIX_LEN] for r in old_risks if r.get("text")}
    new_risks = [
        r for r in sentiment.get("risk_events", [])
        if isinstance(r, dict) and r.get("text") and r["text"][:DEDUP_PREFIX_LEN] not in old_risk_prefixes
    ]

    # Merge: newest first
    merged_narratives = new_narratives + old_narratives
    merged_risks = new_risks + old_risks

    log.info(
        f"Accumulate: +{len(new_narratives)} narratives, +{len(new_risks)} risks "
        f"(total: {len(merged_narratives)}N {len(merged_risks)}R, 24h window)"
    )

    # Build output — overall_sentiment from latest batch only (唔用累積)
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
        "articles_analyzed": len(high_influence) + len(manual_entries),
        "articles_skipped_low_influence": skipped,
        "analysis_window_hours": ANALYSIS_WINDOW_HOURS,
        "overall_sentiment": sentiment.get("overall_sentiment", "neutral"),
        "overall_impact": sentiment.get("overall_impact", 50),
        "confidence": sentiment.get("confidence", 0.0),
        "sentiment_by_symbol": sentiment.get("sentiment_by_symbol", {}),
        "key_narratives": merged_narratives[:20],   # cap 20
        "risk_events": merged_risks[:15],            # cap 15
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
