#!/usr/bin/env python3
"""
memory_init.py — Import existing data into RAG memory

Imports:
  1. SCAN_LOG.md trigger lines → signal memories
  2. TRADE_STATE.md snapshot → conversation memory
  3. SIGNAL.md snapshot → signal memory

Run once to seed the memory system, safe to re-run
(uses dedup by checking existing store count).
"""
import sys
import re
from pathlib import Path

BASE_DIR = Path.home() / ".openclaw"
sys.path.insert(0, str(BASE_DIR))

from memory.writer import write_signal, write_conversation


def import_scan_log():
    """Import TRIGGER lines from SCAN_LOG.md as signal memories."""
    scan_log = BASE_DIR / "workspace/agents/aster_trader/logs/SCAN_LOG.md"
    if not scan_log.exists():
        print("  No SCAN_LOG.md found, skipping")
        return 0

    text = scan_log.read_text(encoding="utf-8")
    # Only import TRIGGER lines (not NO_SIGNAL/HEARTBEAT)
    trigger_lines = [
        l.strip() for l in text.splitlines()
        if "TRIGGER:" in l and l.strip().startswith("[")
    ]

    # Sample: import last 20 unique triggers (not all)
    seen = set()
    unique = []
    for line in reversed(trigger_lines):
        # Extract key part (pair + reason) for dedup
        key = re.search(r"TRIGGER:\S+\s+REASON:\S+", line)
        k = key.group() if key else line[:80]
        if k not in seen:
            seen.add(k)
            unique.append(line)
        if len(unique) >= 20:
            break

    count = 0
    for line in reversed(unique):
        write_signal(line, source="SCAN_LOG_INIT")
        count += 1

    return count


def import_trade_state():
    """Import current TRADE_STATE as a conversation memory."""
    ts_path = BASE_DIR / "shared/TRADE_STATE.md"
    if not ts_path.exists():
        print("  No TRADE_STATE.md found, skipping")
        return 0

    text = ts_path.read_text(encoding="utf-8")
    write_conversation(
        "系統初始化：當前交易狀態快照",
        f"TRADE_STATE.md 內容：\n{text[:1500]}",
    )
    return 1


def import_signal():
    """Import current SIGNAL.md as a signal memory."""
    sig_path = BASE_DIR / "shared/SIGNAL.md"
    if not sig_path.exists():
        print("  No SIGNAL.md found, skipping")
        return 0

    text = sig_path.read_text(encoding="utf-8")
    write_signal(f"初始化快照：{text[:500]}", source="SIGNAL_INIT")
    return 1


def main():
    # Check if already initialized
    store_dir = BASE_DIR / "memory" / "store"
    existing = sum(
        1 for f in store_dir.glob("*.jsonl")
        for _ in f.read_text().splitlines()
    ) if store_dir.exists() else 0

    if existing > 10:
        print(f"Memory already has {existing} records. Skipping init.")
        print("To force re-init, delete memory/store/*.jsonl and memory/index/*")
        return

    print("=== OpenClaw Memory Init ===\n")

    n1 = import_scan_log()
    print(f"  Scan triggers imported: {n1}")

    n2 = import_trade_state()
    print(f"  Trade state imported:   {n2}")

    n3 = import_signal()
    print(f"  Signal imported:        {n3}")

    total = n1 + n2 + n3
    print(f"\n  Total memories created: {total}")
    print("  Done! Memory system ready for RAG.")


if __name__ == "__main__":
    main()
