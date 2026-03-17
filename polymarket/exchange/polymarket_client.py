"""
polymarket_client.py — Polymarket CLOB SDK adapter

獨立 class，唔繼承 BaseExchangeClient。原因：
- 13 個 BaseExchangeClient abstract method 有 7 個唔適用
  （set_leverage, set_margin_mode, create_stop_market, create_take_profit_market,
   get_income, close_position_market, get_open_interest）
- 預測市場同 perp futures 根本性唔同：二元結果、價格=概率、冇 leverage

Auth 流程：
1. Private key → ClobClient L1
2. Derive API credentials (EIP-712 signed)
3. Upgrade to L2 (HMAC-signed operations)
4. Cache creds to avoid re-deriving every cycle
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    BalanceAllowanceParams,
    AssetType,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    OpenOrderParams,
    BookParams,
    TradeParams,
)
from py_clob_client.constants import POLYGON

from shared_infra.exchange.exceptions import (
    CriticalError,
    TemporaryError,
    OrderError,
    InsufficientFundsError,
    InvalidOrderError,
    AuthenticationError,
    DDosProtection,
)
from shared_infra.exchange.retry import retry_quadratic

from ..config.settings import (
    CLOB_HOST,
    CHAIN_ID,
    SECRETS_PATH,
    POLY_CREDS_CACHE_PATH,
)

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Polymarket CLOB client via py-clob-client SDK.

    Adapter pattern (same as HyperLiquidClient):
    - SDK backend, not raw HTTP
    - Credential from POLY_PRIVATE_KEY env/secrets
    - API creds cached to avoid repeated EIP-712 signing
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.private_key: str = ""
        self.client: Optional[ClobClient] = None
        self._load_credentials()
        self._init_client()

    # ─── Auth ───

    def _load_credentials(self):
        """Load POLY_PRIVATE_KEY from env or secrets/.env."""
        self.private_key = os.getenv("POLY_PRIVATE_KEY", "")

        if not self.private_key:
            if os.path.exists(SECRETS_PATH):
                load_dotenv(SECRETS_PATH)
                self.private_key = os.getenv("POLY_PRIVATE_KEY", "")

        if not self.private_key:
            raise CriticalError(
                "POLY_PRIVATE_KEY missing — set in env or secrets/.env"
            )

    def _init_client(self):
        """Initialize ClobClient: L1 → derive creds → L2."""
        try:
            # Start as L1 (can sign orders)
            self.client = ClobClient(
                CLOB_HOST,
                chain_id=CHAIN_ID,
                key=self.private_key,
            )

            # Try loading cached API creds
            creds = self._load_cached_creds()
            if creds:
                self.client.set_api_creds(creds)
                logger.info("PolymarketClient: loaded cached API creds")
            else:
                # Derive new creds (EIP-712 signed request)
                creds = self.client.create_or_derive_api_creds()
                self.client.set_api_creds(creds)
                self._save_cached_creds(creds)
                logger.info("PolymarketClient: derived new API creds")

        except CriticalError:
            raise
        except Exception as e:
            raise CriticalError(f"Polymarket SDK init failed: {e}")

    def _load_cached_creds(self) -> Optional[ApiCreds]:
        """Load API creds from cache file (avoid re-deriving every cycle)."""
        try:
            if not os.path.exists(POLY_CREDS_CACHE_PATH):
                return None
            with open(POLY_CREDS_CACHE_PATH, "r") as f:
                data = json.load(f)
            return ApiCreds(
                api_key=data["api_key"],
                api_secret=data["api_secret"],
                api_passphrase=data["api_passphrase"],
            )
        except (json.JSONDecodeError, KeyError, IOError) as e:
            logger.warning("Failed to load cached poly creds: %s", e)
            return None

    def _save_cached_creds(self, creds: ApiCreds):
        """Atomic save API creds to cache (tempfile + os.replace)."""
        data = {
            "api_key": creds.api_key,
            "api_secret": creds.api_secret,
            "api_passphrase": creds.api_passphrase,
        }
        cache_dir = os.path.dirname(POLY_CREDS_CACHE_PATH)
        os.makedirs(cache_dir, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, POLY_CREDS_CACHE_PATH)
        except IOError as e:
            logger.warning("Failed to cache poly creds: %s", e)

    # ─── Error Wrapping ───

    _RATE_KEYWORDS = ("rate limit", "too many requests", "429", "throttl")
    _AUTH_KEYWORDS = ("auth", "signature", "unauthorized", "forbidden", "invalid api")
    _INSUFFICIENT_KEYWORDS = ("insufficient", "not enough", "balance")
    _INVALID_KEYWORDS = ("invalid", "bad request", "tick size", "min order")

    def _wrap_error(self, error: Exception, context: str = "") -> None:
        """Map SDK exceptions to our exception hierarchy."""
        msg = str(error).lower()
        if any(kw in msg for kw in self._RATE_KEYWORDS):
            raise DDosProtection(f"Polymarket {context}: {error}")
        elif any(kw in msg for kw in self._AUTH_KEYWORDS):
            raise AuthenticationError(f"Polymarket {context}: {error}")
        elif any(kw in msg for kw in self._INSUFFICIENT_KEYWORDS):
            raise InsufficientFundsError(f"Polymarket {context}: {error}")
        elif any(kw in msg for kw in self._INVALID_KEYWORDS):
            raise InvalidOrderError(f"Polymarket {context}: {error}")
        elif any(kw in msg for kw in ("timeout", "connection", "503", "502")):
            raise TemporaryError(f"Polymarket {context}: {error}")
        else:
            raise OrderError(f"Polymarket {context}: {error}")

    # ─── Balance ───

    @retry_quadratic()
    def get_usdc_balance(self) -> float:
        """Get USDC collateral balance."""
        try:
            result = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            return float(result.get("balance", 0)) / 1e6  # USDC has 6 decimals
        except Exception as e:
            self._wrap_error(e, "get_balance")

    # ─── Positions (from open orders + trades) ───

    @retry_quadratic()
    def get_orders(self, market: str = "") -> list[dict]:
        """Get all open orders, optionally filtered by market."""
        try:
            params = OpenOrderParams(market=market) if market else None
            return self.client.get_orders(params) or []
        except Exception as e:
            self._wrap_error(e, "get_orders")

    @retry_quadratic()
    def get_trades(self, market: str = "") -> list[dict]:
        """Get trade history."""
        try:
            params = TradeParams(market=market) if market else None
            return self.client.get_trades(params) or []
        except Exception as e:
            self._wrap_error(e, "get_trades")

    # ─── Order Book ───

    @retry_quadratic()
    def get_order_book(self, token_id: str) -> dict:
        """Get order book for a token.

        Returns dict with 'bids' and 'asks' lists,
        each entry has 'price' and 'size'.
        """
        try:
            book = self.client.get_order_book(token_id)
            return {
                "bids": [{"price": float(b.price), "size": float(b.size)}
                         for b in (book.bids or [])],
                "asks": [{"price": float(a.price), "size": float(a.size)}
                         for a in (book.asks or [])],
                "asset_id": book.asset_id,
                "market": book.market,
            }
        except Exception as e:
            self._wrap_error(e, "get_order_book")

    @retry_quadratic()
    def get_midpoint(self, token_id: str) -> float:
        """Get mid price for a token.

        SDK returns dict {"mid": "0.725"} — extract and convert.
        """
        try:
            mid = self.client.get_midpoint(token_id)
            if isinstance(mid, dict):
                return float(mid.get("mid", 0))
            return float(mid) if mid else 0.0
        except Exception as e:
            self._wrap_error(e, "get_midpoint")

    @retry_quadratic()
    def get_spread(self, token_id: str) -> float:
        """Get bid-ask spread for a token."""
        try:
            spread = self.client.get_spread(token_id)
            return float(spread) if spread else 0.0
        except Exception as e:
            self._wrap_error(e, "get_spread")

    # ─── Market Buy (spend USDC to buy shares) ───

    @retry_quadratic()
    def buy_shares(self, token_id: str, amount_usdc: float,
                   price: float = 0) -> dict:
        """Buy shares of an outcome token.

        Args:
            token_id: Conditional token ID
            amount_usdc: Dollar amount to spend
            price: Limit price (0 = market order, auto-price from book)
        """
        if self.dry_run:
            logger.info("DRY_RUN: buy_shares %s $%.2f @ %.4f", token_id[:10], amount_usdc, price)
            return {"dry_run": True, "token_id": token_id, "amount": amount_usdc}

        try:
            if price > 0:
                # Limit order
                tick_size = self.client.get_tick_size(token_id)
                size = amount_usdc / price  # shares = dollars / price_per_share
                args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=round(size, 2),
                    side="BUY",
                )
                signed = self.client.create_order(args)
                result = self.client.post_order(signed, OrderType.GTC)
            else:
                # Market order (FOK)
                args = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usdc,
                    side="BUY",
                )
                signed = self.client.create_market_order(args)
                result = self.client.post_order(signed, OrderType.FOK)

            logger.info("buy_shares: %s $%.2f → %s", token_id[:10], amount_usdc, result)
            return result if isinstance(result, dict) else {"result": str(result)}

        except Exception as e:
            self._wrap_error(e, "buy_shares")

    # ─── Sell Shares ───

    @retry_quadratic()
    def sell_shares(self, token_id: str, shares: float,
                    price: float = 0) -> dict:
        """Sell shares of an outcome token.

        Args:
            token_id: Conditional token ID
            shares: Number of shares to sell
            price: Limit price (0 = market order)
        """
        if self.dry_run:
            logger.info("DRY_RUN: sell_shares %s %.2f shares @ %.4f", token_id[:10], shares, price)
            return {"dry_run": True, "token_id": token_id, "shares": shares}

        try:
            if price > 0:
                args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=round(shares, 2),
                    side="SELL",
                )
                signed = self.client.create_order(args)
                result = self.client.post_order(signed, OrderType.GTC)
            else:
                args = MarketOrderArgs(
                    token_id=token_id,
                    amount=shares,  # SELL amount = shares
                    side="SELL",
                )
                signed = self.client.create_market_order(args)
                result = self.client.post_order(signed, OrderType.FOK)

            logger.info("sell_shares: %s %.2f shares → %s", token_id[:10], shares, result)
            return result if isinstance(result, dict) else {"result": str(result)}

        except Exception as e:
            self._wrap_error(e, "sell_shares")

    # ─── Cancel Orders ───

    @retry_quadratic()
    def cancel_order(self, order_id: str) -> dict:
        """Cancel a single order."""
        if self.dry_run:
            logger.info("DRY_RUN: cancel_order %s", order_id)
            return {"dry_run": True, "order_id": order_id}

        try:
            result = self.client.cancel(order_id)
            return result if isinstance(result, dict) else {"result": str(result)}
        except Exception as e:
            self._wrap_error(e, "cancel_order")

    @retry_quadratic()
    def cancel_all(self) -> dict:
        """Cancel all open orders."""
        if self.dry_run:
            logger.info("DRY_RUN: cancel_all")
            return {"dry_run": True}

        try:
            result = self.client.cancel_all()
            return result if isinstance(result, dict) else {"result": str(result)}
        except Exception as e:
            self._wrap_error(e, "cancel_all")

    # ─── Market Info ───

    @retry_quadratic()
    def get_market(self, condition_id: str) -> dict:
        """Get market details from CLOB."""
        try:
            return self.client.get_market(condition_id) or {}
        except Exception as e:
            self._wrap_error(e, "get_market")

    # ─── Connection Validation ───

    def validate_connection(self) -> bool:
        """Validate SDK connection works. Returns True if OK."""
        try:
            bal = self.get_usdc_balance()
            logger.info("PolymarketClient connected (USDC balance: $%.2f)", bal)
            return True
        except Exception as e:
            logger.error("PolymarketClient connection failed: %s", e)
            return False


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = PolymarketClient(dry_run=True)
    if client.validate_connection():
        print(f"Balance: ${client.get_usdc_balance():.2f}")
    else:
        print("Connection failed")
