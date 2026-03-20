"""AXC Dashboard — Design System (IBKR-inspired).

Reference: IBKR TWS/Desktop + ThinkorSwim + Bloomberg dark mode.
Sidebar: 220px | Header: 48px | Row: 32px | Card padding: 12px | Radius: 4px
"""

# Google Fonts CDN
FONTS_CSS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700'
    '&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">'
    '<style>'
    'body { font-family: "Inter", -apple-system, sans-serif !important;'
    '  font-variant-numeric: tabular-nums; -webkit-font-smoothing: antialiased; }'
    '.font-mono, .font-data { font-family: "IBM Plex Mono", monospace !important; }'
    '.q-drawer { width: 220px !important; }'
    '.q-header { min-height: 48px !important; }'
    '.q-page-container { padding-top: 48px !important; }'
    '</style>'
)

# ── Layout Constants ──
SIDEBAR_WIDTH = 220       # px (IBKR Desktop: 220-240)
HEADER_HEIGHT = 48        # px
ROW_HEIGHT = 32           # px (table rows)
CARD_RADIUS = 4           # px (sharp — professional)

# ── Color Palette (IBKR Dark) ──
BG_PRIMARY = '#0D0E11'    # Base canvas (darkest)
BG_SURFACE = '#141414'    # Card/panel
BG_ELEVATED = '#1E1E1E'   # Elevated (hover, modal)
BG_HOVER = '#2D2D2D'      # Interactive hover
BORDER = '#2A2A2A'        # Dividers

# Text
TEXT_PRIMARY = '#F0F0F0'   # Main text
TEXT_SECONDARY = '#8B8B8B' # Secondary
TEXT_MUTED = '#606060'     # Labels/timestamps
TEXT_FAINT = '#404040'     # Barely visible

# Semantic
GREEN = '#00C087'          # Profit (IBKR green)
RED = '#FF4D4D'            # Loss
AMBER = '#F59E0B'          # Warning
CYAN = '#30D5C8'           # Info highlight
ACCENT = '#2962FF'         # Primary action (TradingView blue)
ACCENT_HOVER = '#1E50E5'

# ── Typography (px) ──
FONT_DATA = 'text-[12px]'     # Prices, P&L numbers
FONT_LABEL = 'text-[11px]'    # Column headers, uppercase
FONT_SECTION = 'text-[13px]'  # Section headers
FONT_METRIC = 'text-[20px]'   # Account equity headline
FONT_TINY = 'text-[10px]'     # Timestamps, sub-labels

# ── Component Classes ──
CARD = f'p-3 rounded-[{CARD_RADIUS}px] border'
CARD_DARK = f'{CARD} bg-[{BG_SURFACE}] border-[{BORDER}]'

SECTION_HEADER = f'{FONT_LABEL} font-semibold tracking-[1px] uppercase text-[{TEXT_MUTED}]'

DATA_VALUE = 'font-mono text-[12px]'
DATA_VALUE_LG = 'font-mono text-[16px] font-semibold'
DATA_VALUE_XL = 'font-mono text-[20px] font-bold'

LABEL_XS = f'{FONT_TINY} text-[{TEXT_FAINT}]'
LABEL_SM = f'text-[11px] text-[{TEXT_SECONDARY}]'

PNL_POS = f'text-[{GREEN}]'
PNL_NEG = f'text-[{RED}]'

DOT_GREEN = f'text-[6px] text-[{GREEN}]'
DOT_RED = f'text-[6px] text-[{RED}]'

HEADER_CLS = f'bg-[{BG_PRIMARY}] border-b border-[{BORDER}]'
SIDEBAR_CLS = f'bg-[{BG_PRIMARY}] border-r border-[{BORDER}]'
FOOTER_CLS = f'bg-[{BG_PRIMARY}] border-t border-[{BORDER}]'

AGGRID = 'ag-theme-balham-dark'
TABLE_ROW_H = ROW_HEIGHT
