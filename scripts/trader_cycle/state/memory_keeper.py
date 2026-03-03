"""
memory_keeper.py — Append noteworthy events to MEMORY.md
Pipeline step: runs after write_state, before send_reports.

Only writes when something significant happens:
- Market mode transition (RANGE → TREND or vice versa)
- Trade signal found
- Trade executed (Phase 3)
- Critical errors
"""

from __future__ import annotations
import os
import re
from datetime import datetime

from ..config.settings import WORKSPACE, HKT
from ..core.context import CycleContext


MEMORY_PATH = os.path.join(WORKSPACE, "memory/MEMORY.md")


class WriteMemoryStep:
    """Pipeline step: append noteworthy events to MEMORY.md."""
    name = "write_memory"

    def run(self, ctx: CycleContext) -> CycleContext:
        events = []

        # ─── Mode transition ───
        if (ctx.market_mode != "UNKNOWN"
                and ctx.prev_mode != "UNKNOWN"
                and ctx.market_mode != ctx.prev_mode
                and ctx.mode_confirmed):
            events.append(
                f"Market mode 轉換: {ctx.prev_mode} → {ctx.market_mode}"
            )

        # ─── Signal found ───
        if ctx.selected_signal:
            sig = ctx.selected_signal
            action = "DRY_RUN" if ctx.dry_run else "LIVE"
            events.append(
                f"信號: {sig.pair} {sig.direction} ({sig.strategy}) "
                f"entry=${sig.entry_price:.2f} [{action}]"
            )

        # ─── Trade executed (Phase 3) ───
        if ctx.order_result and ctx.order_result.success:
            events.append(
                f"交易執行: {ctx.order_result.symbol} {ctx.order_result.side} "
                f"@ ${ctx.order_result.price:.2f} x{ctx.order_result.quantity}"
            )

        # ─── Critical errors ───
        for err in ctx.errors:
            if "CRITICAL" in err.upper():
                events.append(f"錯誤: {err[:120]}")

        # ─── Nothing to record ───
        if not events:
            if ctx.verbose:
                print("    write_memory: no events to record")
            return ctx

        # ─── Append to MEMORY.md ───
        ts = ctx.timestamp_str
        _append_memory_events(ts, events)

        if ctx.verbose:
            print(f"    write_memory: recorded {len(events)} event(s)")
            for e in events:
                print(f"      - {e}")

        return ctx


def _append_memory_events(ts: str, events: list[str]) -> bool:
    """
    Append events to the system history section of MEMORY.md.
    Looks for '## System History' or '## Critical Changes Made' section.
    Appends at the end of the section (before next ## heading).
    """
    if not os.path.exists(MEMORY_PATH):
        return False

    with open(MEMORY_PATH, "r") as f:
        lines = f.readlines()

    # Find the target section
    target_sections = ["## Critical Changes Made", "## System History"]
    insert_idx = None
    in_section = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Check if we're entering a target section
        for target in target_sections:
            if stripped.startswith(target):
                in_section = True
                insert_idx = i + 1  # start after the heading
                continue

        # If in section, update insert point until we hit next heading
        if in_section and stripped.startswith("## ") and not any(
            stripped.startswith(t) for t in target_sections
        ):
            # We've reached the next section heading
            break

        if in_section:
            insert_idx = i + 1  # keep moving to end of section

    if insert_idx is None:
        # No target section found — append to end of file
        insert_idx = len(lines)
        lines.append("\n## System History\n")
        insert_idx = len(lines)

    # Build event lines
    event_lines = []
    for event in events:
        event_lines.append(f"- {ts}: {event}\n")

    # Insert
    for j, el in enumerate(event_lines):
        lines.insert(insert_idx + j, el)

    with open(MEMORY_PATH, "w") as f:
        f.writelines(lines)

    return True
