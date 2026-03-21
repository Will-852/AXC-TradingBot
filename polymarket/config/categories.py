"""
categories.py — Polymarket 市場分類、過濾規則
定義我哋關注嘅市場類別同埋 tag。
Gamma API 支援 tag-based filtering。

設計決定：只做 Crypto，因為：
- Crypto: 有現有數據源（SCAN_CONFIG BTC/ETH 價格、news_sentiment）
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
}

# Short keywords that need word-boundary matching (avoid "sol" in "resolve")
_SHORT_KEYWORD_LEN = 4  # keywords ≤4 chars get \b word boundaries


# ─── Market Title Blocklist ───
# 包含呢啲詞嘅市場直接跳過（政治敏感、難以量化等）
TITLE_BLOCKLIST = [
    "trump", "biden", "election", "president", "congress",
    "war", "invasion", "assassination",
    "will i", "personal",  # personal prediction markets
    # Sports team names that collide with crypto/general keywords
    "thunder", "heat",
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
