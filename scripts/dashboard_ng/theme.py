"""AXC Dashboard — Theme & styling constants."""

# Color palette (dark-first, Stripe-inspired)
COLORS = {
    'bg_primary': '#0a0e17',
    'bg_card': '#111827',
    'bg_sidebar': '#0f1520',
    'bg_header': '#0b1021',
    'text_primary': '#e5e7eb',
    'text_secondary': '#9ca3af',
    'accent': '#6366f1',       # indigo
    'green': '#22c55e',
    'red': '#ef4444',
    'yellow': '#eab308',
    'blue': '#3b82f6',
}

# Exchange badge colors
EXCHANGE_COLORS = {
    'connected': '#22c55e',
    'disconnected': '#6b7280',
    'error': '#ef4444',
}

# Service status colors
SERVICE_STATUS = {
    'running': '#22c55e',
    'stopped': '#ef4444',
    'unknown': '#6b7280',
}

# Tailwind class presets
CARD_CLASSES = 'bg-gray-800 rounded-lg border border-gray-700'
HEADER_CLASSES = 'bg-[#0b1021] border-b border-gray-800'
SIDEBAR_CLASSES = 'bg-[#0f1520] border-r border-gray-800'
