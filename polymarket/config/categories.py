"""
categories.py — Polymarket 市場分類、過濾規則
定義我哋關注嘅市場類別同埋 tag。
Gamma API 支援 tag-based filtering。

設計決定：只做 Crypto + Weather，因為：
- Crypto: 有現有數據源（SCAN_CONFIG BTC/ETH 價格、news_sentiment）
- Weather: 有免費 forecast API (Open-Meteo)
- 其他類別（政治、體育）需要專門知識，暫時唔做
"""

import re
from dataclasses import dataclass, field


# ─── Crypto 15M Binary Market Title Regex ───
# Matches: "Bitcoin Up or Down - March 17, 5:15PM-5:30PM ET"
# 設計決定：regex 放 categories.py 而非 crypto_15m.py，因為 match_category() 需要用
_RE_CRYPTO_15M = re.compile(
    r"(Bitcoin|Ethereum|Solana|XRP|Dogecoin|BNB|HYPE)\s+"
    r"Up\s+or\s+Down\s*[-–—]\s*"
    r"(\w+)\s+(\d{1,2}),?\s*"
    r"(\d{1,2}):(\d{2})\s*(AM|PM)\s*[-–—]\s*"
    r"(\d{1,2}):(\d{2})\s*(AM|PM)\s+(ET|EST|EDT)",
    re.IGNORECASE,
)

# Coin name → Binance symbol mapping for 15M markets
CRYPTO_15M_COINS: dict[str, str] = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "xrp": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
    "bnb": "BNBUSDT",
    "hype": "HYPEUSDT",
}


# ─── Weather City Coordinates ───
# (lat, lon, unit) — unit determines bucket interpretation:
#   "F" = US cities, °F 2°F range buckets
#   "C" = International, °C 1°C exact buckets
WEATHER_CITIES: dict[str, tuple[float, float, str]] = {
    # US (°F, 2°F buckets) — airport coordinates for Wunderground match
    "atlanta": (33.637, -84.428, "F"),       # KATL
    "chicago": (41.974, -87.907, "F"),       # KORD O'Hare
    "new york": (40.640, -73.779, "F"),      # KJFK
    "seattle": (47.449, -122.309, "F"),      # KSEA
    "dallas": (32.847, -96.852, "F"),        # KDAL Love Field (Polymarket resolution station)
    "miami": (25.796, -80.287, "F"),         # KMIA
    "los angeles": (33.943, -118.408, "F"),  # KLAX
    "phoenix": (33.437, -112.008, "F"),      # KPHX Sky Harbor
    # International (°C, 1°C buckets) — airport coordinates for Wunderground match
    "tokyo": (35.553, 139.781, "C"),         # RJTT Haneda
    "hong kong": (22.309, 113.915, "C"),     # VHHH
    "taipei": (25.080, 121.232, "C"),        # RCTP Taoyuan
    "singapore": (1.350, 103.994, "C"),      # WSSS Changi
    "wellington": (-41.327, 174.805, "C"),   # NZWN
    "paris": (49.013, 2.551, "C"),           # LFPG CDG
    "milan": (45.630, 8.723, "C"),           # LIMC Malpensa
    "ankara": (40.128, 32.995, "C"),         # LTAC Esenboga
    "toronto": (43.677, -79.631, "C"),       # CYYZ Pearson
    "shanghai": (31.143, 121.805, "C"),      # ZSPD Pudong
    "sao paulo": (-23.435, -46.473, "C"),    # SBGR Guarulhos
    "london": (51.505, 0.055, "C"),          # EGLC London City (Polymarket resolution station)
    "sydney": (-33.946, 151.177, "C"),       # YSSY Kingsford Smith
    "seoul": (37.469, 126.451, "C"),         # RKSI Incheon
}


@dataclass
class CategoryConfig:
    """Single category configuration."""
    name: str
    slug: str                           # Gamma API tag slug
    keywords: list[str] = field(default_factory=list)  # title keyword filters
    # Keywords ≤3 chars use word-boundary matching to avoid substring false positives
    # (e.g., "sol" matching "resolve", "eth" matching "method")
    max_exposure_pct: float = 0.20      # max bankroll % for this category
    enabled: bool = True


# ─── Active Categories ───
CATEGORIES: dict[str, CategoryConfig] = {
    "crypto": CategoryConfig(
        name="Crypto",
        slug="crypto",
        keywords=[
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
            "crypto", "cryptocurrency", "defi", "nft",
            "altcoin", "memecoin", "stablecoin",
            "blockchain", "token",
        ],
        max_exposure_pct=0.20,
    ),
    "crypto_15m": CategoryConfig(
        name="Crypto 15M",
        slug="crypto",          # same Gamma tag — title regex 區分
        keywords=[],            # regex-only, no keyword matching
        max_exposure_pct=0.10,  # 快市場 → 更保守
    ),
    "weather": CategoryConfig(
        name="Weather",
        slug="weather",
        keywords=[
            "temperature", "weather", "hurricane", "tornado",
            "rainfall", "snow", "heatwave", "storm",
            "celsius", "fahrenheit", "drought", "flood",
        ],
        max_exposure_pct=0.15,
    ),
}

# Short keywords that need word-boundary matching (avoid "sol" in "resolve")
_SHORT_KEYWORD_LEN = 4  # keywords ≤4 chars get \b word boundaries


# ─── Market Title Blocklist ───
# 包含呢啲詞嘅市場直接跳過（政治敏感、難以量化等）
TITLE_BLOCKLIST = [
    "trump", "biden", "election", "president", "congress",
    "war", "invasion", "assassination",
    "will i", "personal",  # personal prediction markets
    # Sports team names that collide with weather keywords
    "hurricanes", "thunder", "lightning", "heat",
    "avalanche", "flames", "blizzard",
    # Sports context markers
    "moneyline", "spread", "o/u", "over/under", "1h ",
    "vs.", "eagles vs", "match",
]


def _keyword_matches(keyword: str, text: str) -> bool:
    """Check if keyword matches in text.

    Short keywords (≤4 chars) use word-boundary regex to avoid
    false positives like "sol" matching "resolve".
    Longer keywords use simple substring match (faster, safe).
    """
    if len(keyword) <= _SHORT_KEYWORD_LEN:
        return bool(re.search(rf'\b{re.escape(keyword)}\b', text))
    return keyword in text


def match_category(title: str, description: str = "") -> str | None:
    """Match a market to a category by title/description keywords.

    Returns category key (e.g., "crypto") or None if no match.
    Only matches on title — descriptions are too noisy (contain
    boilerplate like "this market will resolve" which triggers "sol").
    Blocklist checked on full text (title + description).
    """
    title_lower = title.lower()
    full_text = f"{title} {description}".lower()

    # Blocklist check on full text
    for blocked in TITLE_BLOCKLIST:
        if blocked in full_text:
            return None

    # Crypto 15M: regex check BEFORE keyword matching (more specific wins)
    if _RE_CRYPTO_15M.search(title_lower):
        return "crypto_15m"

    # Category matching — TITLE ONLY to avoid description noise
    for key, cat in CATEGORIES.items():
        if not cat.enabled:
            continue
        for kw in cat.keywords:
            if _keyword_matches(kw, title_lower):
                return key

    return None


def get_active_categories() -> list[CategoryConfig]:
    """Return list of enabled categories."""
    return [c for c in CATEGORIES.values() if c.enabled]


def get_category(key: str) -> CategoryConfig | None:
    """Get category config by key."""
    return CATEGORIES.get(key)
