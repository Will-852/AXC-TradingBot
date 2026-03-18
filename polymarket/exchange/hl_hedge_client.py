"""
hl_hedge_client.py — Hyperliquid hedge client for Polymarket crypto trades

設計決定：
- 隔離 wrapper — 直接用 hyperliquid SDK，唔 import trader_cycle 任何嘢
- 跟 PolymarketClient 同一 pattern：dry_run guard, retry, exception wrap
- 只做 hedge 用例：open_hedge (market order), close_hedge, get_position, get_balance
- BTC only (MVP)，leverage + margin mode 一次性設定
- dry_run=True 時 log 動作但唔落單

依賴：
- hyperliquid-python-sdk (已裝)
- eth_account (hyperliquid SDK 依賴)
- secrets/.env: HL_PRIVATE_KEY, HL_ACCOUNT_ADDRESS
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class HLHedgeError(Exception):
    """Base error for HL hedge operations."""


class HLHedgeClient:
    """Isolated Hyperliquid client for Polymarket hedge trades.

    Only exposes hedge-relevant methods. Does NOT inherit from BaseExchangeClient
    (that's trader_cycle territory — we stay independent).
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._private_key: str = ""
        self._account_address: str = ""
        self._info = None       # hyperliquid Info (read-only)
        self._exchange = None   # hyperliquid Exchange (write)
        self._meta_cache: dict | None = None

        self._load_credentials()
        if not self.dry_run:
            self._init_sdk()

    def _load_credentials(self):
        """Load HL_PRIVATE_KEY + HL_ACCOUNT_ADDRESS from env or secrets/.env."""
        self._private_key = os.getenv("HL_PRIVATE_KEY", "")
        self._account_address = os.getenv("HL_ACCOUNT_ADDRESS", "")

        if not self._private_key or not self._account_address:
            secrets_path = Path(
                os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
            ) / "secrets" / ".env"
            if secrets_path.exists():
                from dotenv import load_dotenv
                load_dotenv(secrets_path)
                self._private_key = os.getenv("HL_PRIVATE_KEY", "")
                self._account_address = os.getenv("HL_ACCOUNT_ADDRESS", "")

        if not self.dry_run and (not self._private_key or not self._account_address):
            raise HLHedgeError(
                "HL_PRIVATE_KEY / HL_ACCOUNT_ADDRESS missing — "
                "fill secrets/.env or set env vars"
            )

    def _init_sdk(self):
        """Initialize Info + Exchange SDK objects."""
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        from eth_account import Account

        try:
            self._info = Info(constants.MAINNET_API_URL, skip_ws=True)
            wallet = Account.from_key(self._private_key)
            self._exchange = Exchange(
                wallet, constants.MAINNET_API_URL,
                account_address=self._account_address,
            )
            # Validate connection
            bal = self.get_balance()
            logger.info("HL hedge client initialized (balance: $%.2f)", bal)
        except HLHedgeError:
            raise
        except Exception as e:
            raise HLHedgeError(f"HL SDK init failed: {e}")

    # ─── Symbol Helpers ───

    @staticmethod
    def _to_coin(symbol: str) -> str:
        """'BTCUSDT' or 'BTC' → 'BTC'"""
        return symbol.replace("USDT", "").replace("USDC", "")

    def _get_sz_decimals(self, coin: str) -> int:
        """Get size precision for a coin from HL meta."""
        if self._info is None:
            return 3  # dry-run fallback
        if not self._meta_cache:
            self._meta_cache = self._info.meta()
        for asset in self._meta_cache.get("universe", []):
            if asset["name"] == coin:
                return asset.get("szDecimals", 3)
        return 3

    def _round_size(self, sz: float, coin: str) -> float:
        return round(sz, self._get_sz_decimals(coin))

    def _get_mid_price(self, coin: str) -> float:
        """Get mid price for a coin."""
        if self._info is None:
            raise HLHedgeError("SDK not initialized (dry_run mode)")
        mids = self._info.all_mids()
        px = mids.get(coin)
        if px is None:
            raise HLHedgeError(f"No mid price for {coin}")
        return float(px)

    # ─── Core Operations ───

    def get_balance(self) -> float:
        """Get account USDC balance (accountValue)."""
        if self.dry_run:
            return 0.0
        if self._info is None:
            raise HLHedgeError("SDK not initialized")
        try:
            state = self._info.user_state(self._account_address)
            return float(state["marginSummary"]["accountValue"])
        except Exception as e:
            raise HLHedgeError(f"Balance fetch failed: {e}")

    def get_position(self, symbol: str = "BTC") -> dict | None:
        """Get current position for a coin. Returns None if no position."""
        coin = self._to_coin(symbol)

        if self.dry_run:
            return None
        if self._info is None:
            raise HLHedgeError("SDK not initialized")

        try:
            state = self._info.user_state(self._account_address)
            for pos in state.get("assetPositions", []):
                p = pos.get("position", {})
                if p.get("coin") == coin:
                    size = float(p.get("szi", "0"))
                    if size == 0:
                        return None
                    return {
                        "coin": coin,
                        "size": size,  # positive = LONG, negative = SHORT
                        "entry_px": float(p.get("entryPx", "0")),
                        "unrealized_pnl": float(p.get("unrealizedPnl", "0")),
                        "leverage": int(float(p.get("leverage", {}).get("value", "1"))),
                        "side": "LONG" if size > 0 else "SHORT",
                    }
            return None
        except Exception as e:
            raise HLHedgeError(f"Position fetch failed: {e}")

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a coin (cross margin mode)."""
        coin = self._to_coin(symbol)

        if self.dry_run:
            logger.info("DRY_RUN: would set %s leverage to %dx", coin, leverage)
            return True

        if self._exchange is None:
            raise HLHedgeError("SDK not initialized")

        try:
            self._exchange.update_leverage(leverage, coin, is_cross=True)
            logger.info("HL leverage set: %s %dx cross", coin, leverage)
            return True
        except Exception as e:
            logger.warning("HL set_leverage failed: %s", e)
            return False

    def open_hedge(
        self,
        direction: str,
        usdc_size: float,
        leverage: int = 20,
        symbol: str = "BTC",
    ) -> dict:
        """Open a hedge position via market order.

        direction: "LONG" or "SHORT"
        usdc_size: notional size in USDC (e.g. $100 at 20x = $5 margin)
        leverage: leverage multiplier
        symbol: HL coin name (default BTC)

        Returns: {"status": "ok"/"dry_run", "coin": ..., "size": ..., ...}
        """
        coin = self._to_coin(symbol)
        is_buy = direction.upper() == "LONG"

        if self.dry_run:
            logger.info(
                "DRY_RUN: would hedge %s %s $%.0f at %dx on HL",
                direction, coin, usdc_size, leverage,
            )
            return {
                "status": "dry_run",
                "coin": coin,
                "direction": direction,
                "usdc_size": usdc_size,
                "leverage": leverage,
            }

        if self._exchange is None or self._info is None:
            raise HLHedgeError("SDK not initialized")

        # Set leverage first
        self.set_leverage(symbol, leverage)

        # Calculate quantity from notional size
        mid_px = self._get_mid_price(coin)
        qty = usdc_size / mid_px
        qty = self._round_size(qty, coin)

        if qty <= 0:
            raise HLHedgeError(f"Calculated qty too small: {qty} {coin}")

        # Market order
        try:
            result = self._exchange.market_open(coin, is_buy, qty)
        except Exception as e:
            raise HLHedgeError(f"HL hedge market order failed: {e}")

        # Parse result
        status = result.get("status", "")
        if status == "err":
            msg = result.get("response", {}).get("payload", str(result))
            raise HLHedgeError(f"HL hedge order rejected: {msg}")

        logger.info(
            "HL hedge opened: %s %s qty=%.6f at ~$%.0f (%dx)",
            direction, coin, qty, mid_px, leverage,
        )

        return {
            "status": "ok",
            "coin": coin,
            "direction": direction,
            "qty": qty,
            "mid_px": mid_px,
            "leverage": leverage,
            "result": result,
        }

    def close_hedge(self, symbol: str = "BTC") -> dict:
        """Close existing hedge position via market order.

        Returns: {"status": "ok"/"no_position"/"dry_run", ...}
        """
        coin = self._to_coin(symbol)

        if self.dry_run:
            logger.info("DRY_RUN: would close %s hedge on HL", coin)
            return {"status": "dry_run", "coin": coin}

        pos = self.get_position(symbol)
        if not pos:
            logger.info("HL close_hedge: no %s position to close", coin)
            return {"status": "no_position", "coin": coin}

        # Reverse direction to close
        size = pos["size"]
        is_buy = size < 0  # SHORT → buy to close; LONG → sell to close
        abs_size = abs(size)

        # IOC limit order with 10% slippage (same pattern as trader_cycle)
        mid_px = self._get_mid_price(coin)
        slippage = 0.10
        px = mid_px * (1 + slippage) if is_buy else mid_px * (1 - slippage)
        px = round(px, 6)

        try:
            order_type = {"limit": {"tif": "Ioc"}}
            result = self._exchange.order(
                coin, is_buy, abs_size, px, order_type, reduce_only=True,
            )
        except Exception as e:
            raise HLHedgeError(f"HL close hedge failed: {e}")

        status = result.get("status", "")
        if status == "err":
            msg = result.get("response", {}).get("payload", str(result))
            raise HLHedgeError(f"HL close hedge rejected: {msg}")

        logger.info(
            "HL hedge closed: %s %.6f %s at ~$%.0f",
            pos["side"], abs_size, coin, mid_px,
        )

        return {
            "status": "ok",
            "coin": coin,
            "closed_side": pos["side"],
            "closed_size": abs_size,
            "mid_px": mid_px,
            "result": result,
        }
