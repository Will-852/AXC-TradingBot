"""
read_sentiment.py — ReadSentimentStep: 讀取新聞情緒數據
Pipeline Step（插入 calc_indicators 之後）

Phase 1（保守）: Sentiment 只做 information overlay，唔 block 技術信號。
Phase 2（驗證後）: Sentiment 可以做 risk filter。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..core.context import CycleContext

logger = logging.getLogger(__name__)

SENTIMENT_FILE = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))) / "shared" / "news_sentiment.json"
STALE_MINUTES = 30


class ReadSentimentStep:
    """Read news sentiment from shared/news_sentiment.json into ctx."""
    name = "read_sentiment"

    def run(self, ctx: CycleContext) -> CycleContext:
        if not SENTIMENT_FILE.exists():
            if ctx.verbose:
                print("    [SENTIMENT] No sentiment file found, skipping")
            return ctx

        try:
            data = json.loads(SENTIMENT_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to read sentiment: {e}")
            return ctx

        # Check staleness
        updated_at = data.get("updated_at", "")
        is_stale = data.get("stale", False)

        if not is_stale and updated_at:
            try:
                ts = datetime.fromisoformat(updated_at)
                age_min = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                if age_min > STALE_MINUTES:
                    is_stale = True
            except (ValueError, TypeError):
                is_stale = True

        if is_stale:
            if ctx.verbose:
                print(f"    [SENTIMENT] Data stale (>{STALE_MINUTES}min), skipping")
            return ctx

        ctx.news_sentiment = data

        if ctx.verbose:
            sentiment = data.get("overall_sentiment", "?")
            confidence = data.get("confidence", 0)
            articles = data.get("articles_analyzed", 0)
            print(f"    [SENTIMENT] {sentiment} (conf: {confidence:.0%}, {articles} articles)")

            risk_events = data.get("risk_events", [])
            if risk_events:
                print(f"    [SENTIMENT] Risk events: {', '.join(risk_events[:3])}")

        return ctx
