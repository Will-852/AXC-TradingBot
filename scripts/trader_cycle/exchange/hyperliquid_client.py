"""
HyperLiquid Perps API Client v1.0
Uses official hyperliquid-python-sdk (API Wallet auth).
Interface matches AsterClient/BinanceClient for drop-in use.

Auth: API Wallet private key signs orders; main wallet address for queries.
Symbol: External "BTCUSDT" ↔ HL "BTC" (auto-mapped).
"""

import os
import time
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account

from .exceptions import (
    ExchangeError, TemporaryError, DDosProtection,
    OrderError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)

logger = logging.getLogger(__name__)


# ─── Retry Decorator (reused pattern from aster/binance clients) ───

_BACKOFF_SECONDS = [1, 4, 9, 16, 25]
_MAX_RETRIES = 5


def retry_quadratic(max_retries: int = _MAX_RETRIES):
    """Quadratic backoff retry decorator for exchange operations."""
    from functools import wraps

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (TemporaryError, DDosProtection) as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} after {delay}s: {e}"
                        )
                        time.sleep(delay)
                except (OrderError, InsufficientFundsError, InvalidOrderError):
                    raise
                except AuthenticationError as e:
                    raise CriticalError(f"Auth fatal: {e}")
            raise last_exc
        return wrapper
    return decorator


class HyperLiquidClient:
    """HyperLiquid Perps client via official SDK. Same interface as AsterClient."""

    def __init__(self):
        self.private_key: str = ""
        self.account_address: str = ""  # main wallet — used for ALL queries
        self.info: Optional[Info] = None
        self.exchange: Optional[Exchange] = None
        self._meta_cache: Optional[Dict] = None
        self._load_credentials()
        self._init_sdk()
        self._validate_connection()

    def _load_credentials(self):
        """Load HL_PRIVATE_KEY + HL_ACCOUNT_ADDRESS from env or secrets/.env."""
        self.private_key = os.getenv("HL_PRIVATE_KEY", "")
        self.account_address = os.getenv("HL_ACCOUNT_ADDRESS", "")

        if not self.private_key or not self.account_address:
            secrets_path = Path(
                os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
            ) / "secrets" / ".env"
            if secrets_path.exists():
                load_dotenv(secrets_path)
                self.private_key = os.getenv("HL_PRIVATE_KEY", "")
                self.account_address = os.getenv("HL_ACCOUNT_ADDRESS", "")

        if not self.private_key or not self.account_address:
            raise CriticalError("HL_PRIVATE_KEY/HL_ACCOUNT_ADDRESS missing")

    def _init_sdk(self):
        """Initialize Info (read) and Exchange (write) SDK objects."""
        try:
            self.info = Info(constants.MAINNET_API_URL, skip_ws=True)
            wallet = Account.from_key(self.private_key)
            self.exchange = Exchange(
                wallet, constants.MAINNET_API_URL,
                account_address=self.account_address,
            )
        except Exception as e:
            raise CriticalError(f"HyperLiquid SDK init failed: {e}")

    # ─── Symbol Mapping ───

    @staticmethod
    def _to_hl(symbol: str) -> str:
        """'BTCUSDT' → 'BTC', 'ETHUSDC' → 'ETH'"""
        return symbol.replace("USDT", "").replace("USDC", "")

    @staticmethod
    def _from_hl(coin: str) -> str:
        """'BTC' → 'BTCUSDT'"""
        return f"{coin}USDT"

    # ─── Precision (from meta endpoint) ───

    def _get_meta(self) -> Dict:
        """Cache HL meta (universe + szDecimals)."""
        if not self._meta_cache:
            self._meta_cache = self.info.meta()
        return self._meta_cache

    def _get_sz_decimals(self, coin: str) -> int:
        """Get size decimals for a coin from meta."""
        meta = self._get_meta()
        for asset in meta.get("universe", []):
            if asset["name"] == coin:
                return asset.get("szDecimals", 3)
        return 3  # safe default

    def _round_size(self, sz: float, coin: str) -> float:
        """Round size to correct decimals for the coin."""
        decimals = self._get_sz_decimals(coin)
        return round(sz, decimals)

    # ─── Mid Prices (for markPrice) ───

    def _get_mid_prices(self) -> Dict[str, float]:
        """Get current mid prices for all coins. Used for markPrice in positions."""
        try:
            mids = self.info.all_mids()
            return {k: float(v) for k, v in mids.items()}
        except Exception:
            return {}

    # ─── Error Wrapping ───

    # Keywords specific enough to avoid false positives
    _INSUFFICIENT_KEYWORDS = ("insufficient", "not enough", "below minimum margin")
    _INVALID_KEYWORDS = ("invalid", "unknown coin", "bad size", "bad price")
    _AUTH_KEYWORDS = ("auth", "signature", "not approved", "unauthorized")
    _RATE_KEYWORDS = ("rate limit", "too many requests", "throttl")

    def _wrap_error(self, result: Dict, context: str = "") -> Dict:
        """Convert SDK error responses to our exception hierarchy.

        Uses specific keyword sets to avoid misclassifying errors
        (e.g. "bad gateway" as InvalidOrderError).
        """
        if isinstance(result, dict):
            status = result.get("status", "")
            response = result.get("response", {})
            if status == "err":
                msg = response if isinstance(response, str) else str(response)
                msg_lower = msg.lower()
                if any(kw in msg_lower for kw in self._INSUFFICIENT_KEYWORDS):
                    raise InsufficientFundsError(f"HL {context}: {msg}")
                elif any(kw in msg_lower for kw in self._AUTH_KEYWORDS):
                    raise AuthenticationError(f"HL {context}: {msg}")
                elif any(kw in msg_lower for kw in self._RATE_KEYWORDS):
                    raise DDosProtection(f"HL {context}: {msg}")
                elif any(kw in msg_lower for kw in self._INVALID_KEYWORDS):
                    raise InvalidOrderError(f"HL {context}: {msg}")
                elif "gateway" in msg_lower or "timeout" in msg_lower or "503" in msg_lower:
                    raise TemporaryError(f"HL {context}: {msg}")
                else:
                    raise OrderError(f"HL {context}: {msg}")
        return result

    # ─── Precision (public interface matching Aster/Binance) ───

    def validate_symbol_precision(self, symbol: str) -> Dict[str, float]:
        """Return precision info for a symbol, matching Aster/Binance format.
        HL uses szDecimals from meta; price precision is flexible (5 sig figs).
        """
        coin = self._to_hl(symbol)
        sz_dec = self._get_sz_decimals(coin)
        step_size = 10 ** (-sz_dec)  # e.g. szDecimals=3 → 0.001
        return {
            "price_precision": 0.01,      # HL accepts 5 sig figs, 0.01 is safe default
            "qty_precision": step_size,
            "min_qty": step_size,
            "min_notional": 10.0,         # HL min ~$10 notional
        }

    # ─── Account ───

    @retry_quadratic()
    def get_account_balance(self) -> List[Dict[str, Any]]:
        """Return balance list matching Aster/Binance format."""
        try:
            state = self.info.user_state(self.account_address)
            summary = state["marginSummary"]
            account_val = float(summary.get("accountValue", 0))
            margin_used = float(summary.get("totalMarginUsed", 0))
            available = account_val - margin_used
            return [{
                "asset": "USDT",
                "balance": str(account_val),
                "availableBalance": str(available),
                "crossWalletBalance": str(account_val),
            }]
        except Exception as e:
            raise TemporaryError(f"HL balance error: {e}")

    @retry_quadratic()
    def get_usdt_balance(self) -> float:
        """Get account value (USDT equivalent) from marginSummary."""
        try:
            state = self.info.user_state(self.account_address)
            return float(state["marginSummary"]["accountValue"])
        except KeyError as e:
            raise TemporaryError(f"HL balance parse error: {e}")
        except Exception as e:
            raise TemporaryError(f"HL balance error: {e}")

    @retry_quadratic()
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get positions normalized to Aster-compatible format.

        Returns list of dicts with keys: symbol, positionAmt, entryPrice,
        markPrice, unRealizedProfit, leverage — matching Aster/Binance format.
        """
        try:
            state = self.info.user_state(self.account_address)
        except Exception as e:
            raise TemporaryError(f"HL positions error: {e}")

        # Fetch real mid prices for markPrice (not accountValue)
        mid_prices = self._get_mid_prices()

        positions = []
        for pos in state.get("assetPositions", []):
            item = pos.get("position", {})
            coin = item.get("coin", "")
            szi = float(item.get("szi", 0))
            if szi == 0:
                continue

            sym = self._from_hl(coin)
            if symbol and sym != symbol:
                continue

            mark = mid_prices.get(coin, 0)

            positions.append({
                "symbol": sym,
                "positionAmt": str(szi),
                "entryPrice": item.get("entryPx", "0"),
                "markPrice": str(mark),
                "unRealizedProfit": item.get("unrealizedPnl", "0"),
                "leverage": item.get("leverage", {}).get("value", "1"),
                "liquidationPx": item.get("liquidationPx", None),
                "marginUsed": item.get("marginUsed", "0"),
            })

        return positions

    @retry_quadratic()
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open orders from HL."""
        try:
            orders = self.info.open_orders(self.account_address)
        except Exception as e:
            raise TemporaryError(f"HL open orders error: {e}")

        if symbol:
            coin = self._to_hl(symbol)
            orders = [o for o in orders if o.get("coin") == coin]

        return orders

    # ─── Orders ───

    @retry_quadratic()
    def create_market_order(
        self, symbol: str, side: str, qty: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Market order. reduce_only respects qty for partial close
        (unlike market_close which always closes the full position).
        """
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)

        if reduce_only:
            # IOC limit order with 10% slippage = effective market order
            # but respects qty for partial close (unlike market_close)
            mid_prices = self._get_mid_prices()
            mid = mid_prices.get(coin, 0)
            if mid <= 0:
                raise OrderError(f"Cannot get mid price for {coin}")
            slippage = 0.10  # 10% — generous to ensure fill
            px = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
            px = round(px, 6)
            try:
                order_type = {"limit": {"tif": "Ioc"}}
                result = self.exchange.order(
                    coin, is_buy, sz, px, order_type,
                    reduce_only=True,
                )
            except Exception as e:
                raise TemporaryError(f"HL market order error: {e}")
        else:
            try:
                result = self.exchange.market_open(coin, is_buy, sz)
            except Exception as e:
                raise TemporaryError(f"HL market order error: {e}")

        return self._wrap_error(result, "market_order")

    @retry_quadratic()
    def create_stop_market(
        self, symbol: str, side: str, qty: float,
        stop_price: float, reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """Stop-loss order via SDK trigger order (tpsl='sl')."""
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)

        # HL trigger order: limit_px for trigger orders should be
        # a slippage-adjusted price. For stop market, use a wide trigger.
        trigger_px = stop_price

        try:
            order_type = {"trigger": {"triggerPx": str(trigger_px), "isMarket": True, "tpsl": "sl"}}
            result = self.exchange.order(
                coin, is_buy, sz, 0, order_type, reduce_only=reduce_only,
            )
        except Exception as e:
            raise TemporaryError(f"HL stop order error: {e}")

        return self._wrap_error(result, "stop_market")

    @retry_quadratic()
    def create_take_profit_market(
        self, symbol: str, side: str, qty: float,
        stop_price: float, reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """Take-profit order via SDK trigger order (tpsl='tp')."""
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)

        trigger_px = stop_price

        try:
            order_type = {"trigger": {"triggerPx": str(trigger_px), "isMarket": True, "tpsl": "tp"}}
            result = self.exchange.order(
                coin, is_buy, sz, 0, order_type, reduce_only=reduce_only,
            )
        except Exception as e:
            raise TemporaryError(f"HL take profit error: {e}")

        return self._wrap_error(result, "take_profit")

    @retry_quadratic()
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel an order by coin + order ID."""
        coin = self._to_hl(symbol)
        try:
            oid = int(order_id)
        except (ValueError, TypeError):
            raise InvalidOrderError(f"HL cancel: invalid order_id '{order_id}' (must be numeric)")
        try:
            result = self.exchange.cancel(coin, oid)
        except Exception as e:
            raise TemporaryError(f"HL cancel error: {e}")

        return self._wrap_error(result, "cancel")

    def close_position_market(self, symbol: str) -> Dict[str, Any]:
        """Market close via read-position → create_market_order pattern.
        Matches Aster/Binance: inherits retry from get_positions + create_market_order.
        """
        positions = self.get_positions(symbol)
        if not positions:
            return {"msg": "No position to close"}

        pos = positions[0]
        amt = float(pos.get("positionAmt", 0))
        if amt == 0:
            return {"msg": "No position to close"}

        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)

        return self.create_market_order(symbol, side, qty, reduce_only=True)

    # ─── Leverage / Margin ───

    @retry_quadratic()
    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage while preserving current margin mode (cross/isolated).
        HL combines leverage + margin mode in one call, so we must read
        current mode first to avoid silently switching it.
        """
        coin = self._to_hl(symbol)
        try:
            # Read current margin mode to preserve it
            state = self.info.user_state(self.account_address)
            is_cross = False  # default isolated
            for pos in state.get("assetPositions", []):
                item = pos.get("position", {})
                if item.get("coin") == coin:
                    lev = item.get("leverage", {})
                    is_cross = lev.get("type", "isolated") == "cross"
                    break

            result = self.exchange.update_leverage(leverage, coin, is_cross=is_cross)
        except Exception as e:
            raise TemporaryError(f"HL set leverage error: {e}")

        return self._wrap_error(result, "set_leverage")

    @retry_quadratic()
    def set_margin_mode(
        self, symbol: str, margin_mode: str = "ISOLATED"
    ) -> Dict[str, Any]:
        """Set margin mode. HL uses leverage update with is_cross flag."""
        coin = self._to_hl(symbol)
        is_cross = margin_mode.upper() == "CROSS"

        # Need current leverage to preserve it
        try:
            state = self.info.user_state(self.account_address)
            current_leverage = 1
            for pos in state.get("assetPositions", []):
                item = pos.get("position", {})
                if item.get("coin") == coin:
                    lev = item.get("leverage", {})
                    current_leverage = int(lev.get("value", 1))
                    break

            result = self.exchange.update_leverage(current_leverage, coin, is_cross=is_cross)
        except Exception as e:
            raise TemporaryError(f"HL set margin mode error: {e}")

        return self._wrap_error(result, "set_margin_mode")

    # ─── Income / Fills ───

    @retry_quadratic()
    def get_income(
        self, income_type: Optional[str] = None,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get trade fills normalized to Aster-compatible income format.
        Filters by start_time/end_time (ms epoch) and income_type if provided.
        """
        try:
            fills = self.info.user_fills(self.account_address)
        except Exception as e:
            raise TemporaryError(f"HL fills error: {e}")

        # Normalize to Aster-compatible income format
        results = []
        for fill in fills:
            fill_time = fill.get("time", 0)

            # Time range filtering (HL time is ms epoch)
            if start_time and fill_time < start_time:
                continue
            if end_time and fill_time > end_time:
                continue

            has_pnl = fill.get("closedPnl", "0") != "0"
            fill_type = "REALIZED_PNL" if has_pnl else "TRADE"

            # income_type filtering
            if income_type and fill_type != income_type:
                continue

            results.append({
                "symbol": self._from_hl(fill.get("coin", "")),
                "incomeType": fill_type,
                "income": fill.get("closedPnl", "0"),
                "asset": "USDT",
                "time": fill_time,
                "info": fill.get("oid", ""),
            })

            if len(results) >= limit:
                break

        return results

    # ─── Validation ───

    def _validate_connection(self):
        """Init connection test — query balance."""
        try:
            bal = self.get_usdt_balance()
            logger.info(f"HyperLiquidClient initialized (balance: ${bal:.2f})")
        except Exception as e:
            logger.error(f"HyperLiquidClient init failed: {e}")
            raise CriticalError(f"Cannot connect to HyperLiquid: {e}")


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = HyperLiquidClient()
    print("Balance:", client.get_usdt_balance())
    print("Positions:", client.get_positions())
    print("Open Orders:", client.get_open_orders())
