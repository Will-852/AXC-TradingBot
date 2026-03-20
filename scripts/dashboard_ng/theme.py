"""AXC Dashboard — Design System.

Style: Data-Dense Dashboard + OLED Dark Mode
Typography: Fira Code (data) + Fira Sans (UI)
Grid: 8px base gap, 12px card padding
"""

# Google Fonts CDN (injected in layout.py)
FONTS_CSS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700'
    '&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">'
    '<style>'
    'body { font-family: "Fira Sans", sans-serif !important; }'
    '.font-mono, .font-data { font-family: "Fira Code", monospace !important; }'
    '</style>'
)

# ── Color Palette (OLED Dark + Trading Accents) ──
# Base
BG_PRIMARY = '#000000'       # True black (OLED)
BG_SURFACE = '#0a0e17'       # Card/panel surface
BG_ELEVATED = '#111827'      # Elevated surface (hover, modal)
BORDER = '#1e293b'           # Subtle borders (slate-800)

# Text
TEXT_PRIMARY = '#f1f5f9'     # slate-100
TEXT_SECONDARY = '#94a3b8'   # slate-400
TEXT_MUTED = '#64748b'       # slate-500
TEXT_FAINT = '#475569'       # slate-600

# Accents
ACCENT = '#3b82f6'           # blue-500 (primary action)
ACCENT_HOVER = '#2563eb'     # blue-600
GREEN = '#22c55e'            # Profit / bullish
RED = '#ef4444'              # Loss / bearish
AMBER = '#f59e0b'            # Warning / near threshold
CYAN = '#06b6d4'             # Info / neutral highlight

# ── Spacing System (8px base) ──
GAP_XS = 'gap-1'     # 4px
GAP_SM = 'gap-2'     # 8px — grid gap
GAP_MD = 'gap-3'     # 12px
GAP_LG = 'gap-4'     # 16px
GAP_XL = 'gap-6'     # 24px — section gap

# ── Component Classes ──
# Cards
CARD = f'p-3 rounded-lg border'
CARD_DARK = f'{CARD} bg-[{BG_SURFACE}] border-[{BORDER}]'

# Section headers
SECTION_HEADER = f'text-[11px] font-semibold tracking-widest uppercase text-[{TEXT_MUTED}]'

# Data values (monospace for numbers)
DATA_VALUE = 'font-mono text-sm'
DATA_VALUE_LG = 'font-mono text-xl font-bold'
DATA_VALUE_XL = 'font-mono text-2xl font-bold'

# Labels
LABEL_XS = f'text-[10px] text-[{TEXT_FAINT}]'
LABEL_SM = f'text-xs text-[{TEXT_SECONDARY}]'

# PnL colors
PNL_POS = f'text-[{GREEN}]'
PNL_NEG = f'text-[{RED}]'

# Status dot
DOT_GREEN = f'text-[8px] text-[{GREEN}]'
DOT_RED = f'text-[8px] text-[{RED}]'
DOT_GRAY = f'text-[8px] text-[{TEXT_MUTED}]'

# Header/Sidebar
HEADER = f'bg-[{BG_PRIMARY}] border-b border-[{BORDER}]'
SIDEBAR = f'bg-[{BG_PRIMARY}] border-r border-[{BORDER}]'
FOOTER = f'bg-[{BG_PRIMARY}] border-t border-[{BORDER}]'

# AG Grid
AGGRID = 'ag-theme-balham-dark'

# Table row height
TABLE_ROW_H = 36
