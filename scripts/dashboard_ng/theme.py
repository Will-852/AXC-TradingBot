"""AXC Dashboard — Design System (Trading Terminal).

Reference: IBKR TWS + TradingView dark mode.
Font: JetBrains Mono (data) + Inter (UI)
"""

# ── Fonts + Global CSS ──
FONTS_CSS = '''
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
'''

# Injected via ui.add_css() in layout.py — not in <style> tag
GLOBAL_CSS = '''
:root {
    --bg:       #0a0e1a;
    --surface:  #111827;
    --elevated: #1a1f2e;
    --border:   #1e2d45;
    --accent:   #3b82f6;
    --green:    #10b981;
    --red:      #ef4444;
    --amber:    #f59e0b;
    --text:     #e2e8f0;
    --text-2:   #94a3b8;
    --text-3:   #64748b;
    --text-4:   #475569;
}

body {
    font-family: "Inter", -apple-system, sans-serif !important;
    font-variant-numeric: tabular-nums;
    -webkit-font-smoothing: antialiased;
}
body.body--dark { background: var(--bg) !important; }
.font-mono { font-family: "JetBrains Mono", monospace !important; }

/* Quasar overrides */
.q-drawer { width: 220px !important; }
.q-header { min-height: 48px !important; }

/* Scrollbar — thin dark */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--accent); }
* { scrollbar-width: thin; scrollbar-color: var(--border) var(--bg); }

/* Remove NiceGUI default content padding */
.nicegui-content { padding: 0 !important; max-width: 100% !important; }

/* AG Grid dark override */
.ag-theme-balham-dark {
    --ag-background-color:        #0f172a !important;
    --ag-header-background-color: #1e293b !important;
    --ag-odd-row-background-color:#111827 !important;
    --ag-border-color:            #1e2d45 !important;
    --ag-foreground-color:        #cbd5e1 !important;
    --ag-row-hover-color:         #1d3557 !important;
    --ag-header-foreground-color: #94a3b8 !important;
    --ag-cell-horizontal-padding: 8px !important;
    font-size: 12px !important;
    font-family: "JetBrains Mono", monospace !important;
}
.ag-theme-balham-dark .ag-header-cell-label {
    font-size: 11px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.5px !important;
}

/* Card glow variants */
.card-glow-blue {
    border: 1px solid rgba(59, 130, 246, 0.3) !important;
    box-shadow: 0 0 15px rgba(59, 130, 246, 0.1), 0 4px 20px rgba(0,0,0,0.3) !important;
}
.card-glow-green {
    border: 1px solid rgba(16, 185, 129, 0.3) !important;
    box-shadow: 0 0 15px rgba(16, 185, 129, 0.1) !important;
}
.card-glow-red {
    border: 1px solid rgba(239, 68, 68, 0.3) !important;
    box-shadow: 0 0 15px rgba(239, 68, 68, 0.1) !important;
}

/* Stat value styling */
.stat-value {
    font-family: "JetBrains Mono", monospace;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.02em;
}
.stat-label {
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-3);
}
'''

# ── Layout Constants ──
SIDEBAR_WIDTH = 220
HEADER_HEIGHT = 48
ROW_HEIGHT = 32
CARD_RADIUS = 6

# ── Colors ──
BG_PRIMARY = '#0a0e1a'
BG_SURFACE = '#111827'
BG_ELEVATED = '#1a1f2e'
BG_HOVER = '#1d3557'
BORDER = '#1e2d45'

TEXT_PRIMARY = '#e2e8f0'
TEXT_SECONDARY = '#94a3b8'
TEXT_MUTED = '#64748b'
TEXT_FAINT = '#475569'

GREEN = '#10b981'
RED = '#ef4444'
AMBER = '#f59e0b'
CYAN = '#06b6d4'
ACCENT = '#3b82f6'
ACCENT_HOVER = '#2563eb'

# ── Component Classes ──
CARD = f'p-3 rounded-[{CARD_RADIUS}px] border'
CARD_DARK = f'{CARD} bg-[{BG_SURFACE}] border-[{BORDER}]'
CARD_GLASS = f'p-3 rounded-[{CARD_RADIUS}px] backdrop-blur-xl border border-white/10'

SECTION_HEADER = f'text-[11px] font-semibold tracking-[1px] uppercase text-[{TEXT_MUTED}]'

DATA_VALUE = 'font-mono text-[12px] stat-value'
DATA_VALUE_LG = 'font-mono text-[16px] font-semibold stat-value'
DATA_VALUE_XL = 'font-mono text-[20px] font-bold stat-value'

LABEL_XS = f'text-[10px] text-[{TEXT_FAINT}] stat-label'
LABEL_SM = f'text-[11px] text-[{TEXT_SECONDARY}]'

PNL_POS = f'text-[{GREEN}]'
PNL_NEG = f'text-[{RED}]'

HEADER_CLS = f'bg-[{BG_PRIMARY}] border-b border-[{BORDER}]'
SIDEBAR_CLS = f'bg-[{BG_PRIMARY}] border-r border-[{BORDER}]'
FOOTER_CLS = f'bg-[{BG_PRIMARY}] border-t border-[{BORDER}]'

AGGRID = 'ag-theme-balham-dark'
TABLE_ROW_H = ROW_HEIGHT
