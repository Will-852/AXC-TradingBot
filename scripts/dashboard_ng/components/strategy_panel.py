"""strategy_panel.py — Squeeze/Burst status + Strategy matrix + Signal journal + PnL reset.

4 new panels for the AXC dashboard:
  1. Squeeze/Burst Ready status per coin (reads indicator_cache.json)
  2. Per-coin strategy matrix (reads config/coins/)
  3. Signal journal feed (reads shared/signal_journal.jsonl)
  4. Strategy PnL tracking from new baseline (reads signal_journal.jsonl outcomes)
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from nicegui import ui

from scripts.dashboard_ng.theme import (
    CARD_DARK, LABEL_XS, LABEL_SM, DATA_VALUE, DATA_VALUE_LG,
    TEXT_SECONDARY, TEXT_MUTED, TEXT_PRIMARY,
    GREEN, RED, AMBER, CYAN, ACCENT, BG_SURFACE, BG_ELEVATED, BORDER,
    SECTION_HEADER,
)

_BASE = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
_CACHE = _BASE / "shared" / "indicator_cache.json"
_JOURNAL = _BASE / "shared" / "signal_journal.jsonl"

_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
_COIN_SHORT = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "XRPUSDT": "XRP"}
_COIN_COLORS = {"BTC": "#f7931a", "ETH": "#627eea", "SOL": "#9945ff", "XRP": "#23292f"}

# Squeeze thresholds (must match indicator_engine + squeeze_strategy)
_SQZ_BB_MAX = 30.0
_SQZ_ADX_MAX = 25.0
_SQZ_VOL_MAX = 0.80


def _read_cache() -> dict:
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return {}


def _read_journal_tail(n: int = 10) -> list[dict]:
    try:
        lines = _JOURNAL.read_text().strip().split("\n")
        return [json.loads(l) for l in lines[-n:]][::-1]  # newest first
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# 1. SQUEEZE / BURST STATUS
# ═══════════════════════════════════════════════════════════════

def render_squeeze_status():
    """Per-coin squeeze/burst readiness panel."""
    card = ui.card().classes(f'{CARD_DARK} flex-1 min-w-[350px]')
    with card:
        ui.label('SQUEEZE / BURST STATUS').classes(SECTION_HEADER)
        container = ui.column().classes('gap-1 mt-2 w-full')

        def update():
            container.clear()
            cache = _read_cache()
            with container:
                for sym in _COINS:
                    short = _COIN_SHORT[sym]
                    h1 = cache.get(sym, {}).get("1h", {})

                    bb_pctl = h1.get("bb_width_pctl")
                    adx = h1.get("adx")
                    vol = h1.get("volume_ratio")
                    price = h1.get("price")
                    bb_upper = h1.get("bb_upper")
                    bb_lower = h1.get("bb_lower")

                    # Squeeze check
                    squeeze_ready = (
                        bb_pctl is not None and bb_pctl < _SQZ_BB_MAX
                        and (adx is None or adx < _SQZ_ADX_MAX)
                        and (vol is None or vol < _SQZ_VOL_MAX)
                    )

                    # Breakout check
                    breakout = ""
                    if price and bb_upper and price > bb_upper:
                        breakout = "LONG ⚡"
                    elif price and bb_lower and price < bb_lower:
                        breakout = "SHORT ⚡"

                    # Burst check (volume > 2.5x)
                    burst_active = vol is not None and vol >= 2.5

                    with ui.row().classes('items-center gap-2 w-full'):
                        # Coin badge
                        color = _COIN_COLORS.get(short, ACCENT)
                        ui.label(short).classes(
                            f'text-[11px] font-bold px-2 py-0.5 rounded '
                            f'bg-[{color}]/20 text-[{color}] min-w-[40px] text-center'
                        )

                        # Squeeze status
                        if squeeze_ready:
                            sqz_color = GREEN if not breakout else AMBER
                            sqz_text = f'SQUEEZE READY (pctl={bb_pctl:.0f}%)'
                            if breakout:
                                sqz_text = f'BREAKOUT {breakout}'
                            ui.label(sqz_text).classes(
                                f'text-[11px] font-mono font-semibold text-[{sqz_color}]'
                            )
                        else:
                            reasons = []
                            if bb_pctl is not None and bb_pctl >= _SQZ_BB_MAX:
                                reasons.append(f'pctl={bb_pctl:.0f}%')
                            if adx is not None and adx >= _SQZ_ADX_MAX:
                                reasons.append(f'adx={adx:.0f}')
                            ui.label(f'No squeeze ({", ".join(reasons)})').classes(
                                f'text-[11px] font-mono text-[{TEXT_MUTED}]'
                            )

                        ui.element('div').classes('flex-1')  # spacer

                        # Burst indicator
                        if burst_active:
                            ui.label(f'BURST {vol:.1f}x').classes(
                                f'text-[10px] font-bold px-1.5 py-0.5 rounded '
                                f'bg-[{RED}]/20 text-[{RED}]'
                            )

                        # Vol ratio bar
                        if vol is not None:
                            bar_pct = min(vol / 3.0 * 100, 100)
                            bar_color = RED if vol >= 2.5 else (AMBER if vol >= 1.0 else TEXT_MUTED)
                            with ui.element('div').classes(
                                f'w-[60px] h-[6px] rounded-full bg-[{BG_ELEVATED}]'
                            ):
                                ui.element('div').classes(
                                    f'h-full rounded-full bg-[{bar_color}]'
                                ).style(f'width: {bar_pct}%')

        ui.timer(5, update)


# ═══════════════════════════════════════════════════════════════
# 2. STRATEGY MATRIX
# ═══════════════════════════════════════════════════════════════

def render_strategy_matrix():
    """Per-coin strategy enabled/disabled matrix."""
    card = ui.card().classes(f'{CARD_DARK} flex-1 min-w-[350px]')
    with card:
        ui.label('STRATEGY MATRIX').classes(SECTION_HEADER)

        strategies = ["range", "trend", "crash", "squeeze", "burst"]
        coins = ["btc", "eth", "sol", "xrp"]

        with ui.element('div').classes('mt-2 overflow-x-auto'):
            # Header row
            with ui.row().classes('gap-0 items-center'):
                ui.label('').classes('min-w-[45px]')  # corner cell
                for s in strategies:
                    ui.label(s[:3].upper()).classes(
                        f'text-[9px] font-bold text-[{TEXT_MUTED}] '
                        f'w-[42px] text-center uppercase tracking-wider'
                    )

            # Coin rows
            for coin in coins:
                try:
                    mod = __import__(f'config.coins.{coin}.config', fromlist=['COIN'])
                    cfg = mod.COIN.get("strategies", {})
                except Exception:
                    cfg = {}

                with ui.row().classes('gap-0 items-center'):
                    color = _COIN_COLORS.get(coin.upper(), ACCENT)
                    ui.label(coin.upper()).classes(
                        f'text-[11px] font-bold min-w-[45px] text-[{color}]'
                    )
                    for s in strategies:
                        enabled = cfg.get(s, {}).get("enabled", False)
                        if enabled:
                            ui.label('●').classes(
                                f'w-[42px] text-center text-[14px] text-[{GREEN}]'
                            )
                        else:
                            ui.label('○').classes(
                                f'w-[42px] text-center text-[14px] text-[{TEXT_MUTED}]/30'
                            )


# ═══════════════════════════════════════════════════════════════
# 3. SIGNAL JOURNAL FEED
# ═══════════════════════════════════════════════════════════════

def render_signal_journal():
    """Latest signals from signal_journal.jsonl."""
    card = ui.card().classes(f'{CARD_DARK} flex-1 min-w-[400px]')
    with card:
        with ui.row().classes('items-center gap-2'):
            ui.label('SIGNAL JOURNAL').classes(SECTION_HEADER)
            count_label = ui.label('').classes(f'text-[10px] text-[{TEXT_MUTED}]')
        container = ui.column().classes('gap-1 mt-2 w-full max-h-[300px] overflow-y-auto')

        def update():
            entries = _read_journal_tail(8)
            count_label.text = f'({len(entries)} recent)'
            container.clear()
            with container:
                if not entries:
                    ui.label('No signals yet').classes(f'text-[11px] text-[{TEXT_MUTED}] italic')
                    return

                for e in entries:
                    sym = _COIN_SHORT.get(e.get("symbol", ""), e.get("symbol", "?"))
                    strat = e.get("strategy", "?")
                    direction = e.get("direction", "?")
                    conf = e.get("confidence", 0)
                    ts = e.get("ts", 0)
                    price = e.get("price", 0)
                    reasons = e.get("reasons", [])
                    funding = e.get("funding_rate")
                    vol_trigger = e.get("vol_trigger_active", False)

                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "?"
                    dir_color = GREEN if direction == "LONG" else RED
                    conf_color = GREEN if conf >= 0.6 else (AMBER if conf >= 0.4 else TEXT_MUTED)

                    with ui.card().classes(
                        f'p-2 rounded bg-[{BG_ELEVATED}] border border-[{BORDER}] w-full'
                    ):
                        with ui.row().classes('items-center gap-2 w-full'):
                            ui.label(time_str).classes(f'text-[10px] text-[{TEXT_MUTED}] font-mono')
                            ui.label(sym).classes(f'text-[11px] font-bold text-[{_COIN_COLORS.get(sym, ACCENT)}]')
                            ui.label(direction).classes(f'text-[10px] font-bold text-[{dir_color}]')
                            ui.label(strat).classes(
                                f'text-[9px] px-1.5 py-0.5 rounded '
                                f'bg-[{ACCENT}]/15 text-[{ACCENT}]'
                            )
                            ui.element('div').classes('flex-1')
                            ui.label(f'conf={conf:.0%}').classes(
                                f'text-[10px] font-mono text-[{conf_color}]'
                            )
                            if vol_trigger:
                                ui.label('VOL⚡').classes(
                                    f'text-[9px] font-bold text-[{AMBER}]'
                                )

                        # Second line: key indicators
                        if reasons:
                            reason_text = reasons[0][:60] if reasons else ""
                            ui.label(reason_text).classes(
                                f'text-[9px] font-mono text-[{TEXT_MUTED}] mt-0.5 truncate'
                            )

        ui.timer(5, update)


# ═══════════════════════════════════════════════════════════════
# 4. STRATEGY PNL (new baseline)
# ═══════════════════════════════════════════════════════════════

def render_strategy_pnl():
    """Strategy PnL from signal journal baseline — separate from legacy PnL."""
    card = ui.card().classes(f'{CARD_DARK} flex-1 min-w-[250px]')
    with card:
        ui.label('STRATEGY PNL (NEW)').classes(SECTION_HEADER)
        ui.label('From squeeze/burst baseline').classes(f'text-[9px] text-[{TEXT_MUTED}] mb-2')
        container = ui.column().classes('gap-1 w-full')

        def update():
            entries = _read_journal_tail(100)
            container.clear()

            # Count signals per strategy
            strat_counts: dict[str, dict] = {}
            for e in entries:
                s = e.get("strategy", "?")
                if s not in strat_counts:
                    strat_counts[s] = {"signals": 0, "selected": 0, "executed": 0}
                strat_counts[s]["signals"] += 1
                if e.get("selected"):
                    strat_counts[s]["selected"] += 1
                if e.get("executed"):
                    strat_counts[s]["executed"] += 1

            with container:
                if not strat_counts:
                    ui.label('Collecting data...').classes(f'text-[11px] text-[{TEXT_MUTED}] italic')
                    return

                for strat, counts in sorted(strat_counts.items()):
                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.label(strat.upper()).classes(
                            f'text-[11px] font-bold text-[{ACCENT}] min-w-[65px]'
                        )
                        ui.label(f'{counts["signals"]} signals').classes(
                            f'text-[10px] font-mono text-[{TEXT_SECONDARY}]'
                        )
                        if counts["selected"] > 0:
                            ui.label(f'{counts["selected"]} selected').classes(
                                f'text-[10px] font-mono text-[{GREEN}]'
                            )
                        if counts["executed"] > 0:
                            ui.label(f'{counts["executed"]} executed').classes(
                                f'text-[10px] font-mono text-[{CYAN}]'
                            )

                # Kelly status
                ui.separator().classes(f'my-1 bg-[{BORDER}]')
                ui.label('KELLY STATUS').classes(f'text-[9px] text-[{TEXT_MUTED}] mt-1')
                for strat in ["squeeze", "burst"]:
                    n = strat_counts.get(strat, {}).get("executed", 0)
                    needed = 20
                    pct = min(n / needed * 100, 100)
                    with ui.row().classes('items-center gap-2 w-full'):
                        ui.label(strat[:3].upper()).classes(
                            f'text-[10px] font-mono text-[{TEXT_MUTED}] min-w-[30px]'
                        )
                        with ui.element('div').classes(
                            f'flex-1 h-[4px] rounded-full bg-[{BG_ELEVATED}]'
                        ):
                            bar_color = GREEN if pct >= 100 else AMBER
                            ui.element('div').classes(
                                f'h-full rounded-full bg-[{bar_color}]'
                            ).style(f'width: {pct}%')
                        ui.label(f'{n}/{needed}').classes(
                            f'text-[9px] font-mono text-[{TEXT_MUTED}]'
                        )

        ui.timer(10, update)


# ═══════════════════════════════════════════════════════════════
# COMBINED RENDER
# ═══════════════════════════════════════════════════════════════

def render_strategy_panels():
    """Render all 4 strategy panels as a new dashboard section."""
    # Row A: Squeeze status + Strategy matrix
    with ui.row().classes('gap-2 w-full items-start'):
        render_squeeze_status()
        render_strategy_matrix()
        render_strategy_pnl()

    # Row B: Signal journal (full width)
    render_signal_journal()
