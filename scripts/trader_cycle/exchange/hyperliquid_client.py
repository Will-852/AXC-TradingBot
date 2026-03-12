"""
HyperLiquid Perps API Client — implements BaseExchangeClient via SDK adapter.

Uses official hyperliquid-python-sdk (API Wallet auth).
Adapter pattern: implements BaseExchangeClient interface but delegates to SDK
instead of HTTP+HMAC (unlike Aster/Binance which use HmacExchangeClient).

Auth: API Wallet private key signs orders; main wallet address for queries.
Symbol: External "BTCUSDT" <-> HL "BTC" (auto-mapped).
"""

import os
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path

from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants
from eth_account import Account

from .base_client import BaseExchangeClient
from .exceptions import (
    ExchangeError, TemporaryError, DDosProtection,
    OrderError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)
from .retry import retry_quadratic

logger = logging.getLogger(__name__)


class HyperLiquidClient(BaseExchangeClient):
    """HyperLiquid Perps client via official SDK.
    Implements BaseExchangeClient interface (adapter pattern — SDK backend,
    not HTTP+HMAC like Aster/Binance).
    """

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
        """'BTCUSDT' -> 'BTC'"""
        return symbol.replace("USDT", "").replace("USDC", "")

    @staticmethod
    def _from_hl(coin: str) -> str:
        """'BTC' -> 'BTCUSDT'"""
        return f"{coin}USDT"

    # ─── Precision ───

    def _get_meta(self) -> Dict:
        if not self._meta_cache:
            self._meta_cache = self.info.meta()
        return self._meta_cache

    def _get_sz_decimals(self, coin: str) -> int:
        meta = self._get_meta()
        for asset in meta.get("universe", []):
            if asset["name"] == coin:
                return asset.get("szDecimals", 3)
        return 3

    def _round_size(self, sz: float, coin: str) -> float:
        return round(sz, self._get_sz_decimals(coin))

    def _get_mid_prices(self) -> Dict[str, float]:
        try:
            mids = self.info.all_mids()
            return {k: float(v) for k, v in mids.items()}
        except Exception:
            return {}

    # ─── Error Wrapping ───

    _INSUFFICIENT_KEYWORDS = ("insufficient", "not enough", "below minimum margin")
    _INVALID_KEYWORDS = ("invalid", "unknown coin", "bad size", "bad price")
    _AUTH_KEYWORDS = ("auth", "signature", "not approved", "unauthorized")
    _RATE_KEYWORDS = ("rate limit", "too many requests", "throttl")

    def _wrap_error(self, result: Dict, context: str = "") -> Dict:
        """Convert SDK error responses to our exception hierarchy."""
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

    # ─── Precision (public interface) ───

    def validate_symbol_precision(self, symbol: str) -> Dict[str, float]:
        coin = self._to_hl(symbol)
        sz_dec = self._get_sz_decimals(coin)
        step_size = 10 ** (-sz_dec)
        return {
            "price_precision": 0.01,
            "qty_precision": step_size,
            "min_qty": step_size,
            "min_notional": 10.0,
        }

    # ─── Account ───

    @retry_quadratic()
    def get_account_balance(self) -> List[Dict[str, Any]]:
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
        try:
            state = self.info.user_state(self.account_address)
            return float(state["marginSummary"]["accountValue"])
        except KeyError as e:
            raise TemporaryError(f"HL balance parse error: {e}")
        except Exception as e:
            raise TemporaryError(f"HL balance error: {e}")

    @retry_quadratic()
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            state = self.info.user_state(self.account_address)
        except Exception as e:
            raise TemporaryError(f"HL positions error: {e}")

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
    def create_market_order(self, symbol: str, side: str, qty: float,
                            reduce_only: bool = False) -> Dict[str, Any]:
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)

        if reduce_only:
            mid_prices = self._get_mid_prices()
            mid = mid_prices.get(coin, 0)
            if mid <= 0:
                raise OrderError(f"Cannot get mid price for {coin}")
            slippage = 0.10
            px = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
            px = round(px, 6)
            try:
                order_type = {"limit": {"tif": "Ioc"}}
                result = self.exchange.order(
                    coin, is_buy, sz, px, order_type, reduce_only=True,
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
    def create_limit_order(self, symbol: str, side: str, qty: float,
                           price: float, reduce_only: bool = False) -> Dict[str, Any]:
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)
        px = round(float(price), 6)
        try:
            order_type = {"limit": {"tif": "Gtc"}}
            result = self.exchange.order(
                coin, is_buy, sz, px, order_type, reduce_only=reduce_only,
            )
        except Exception as e:
            raise TemporaryError(f"HL limit order error: {e}")
        return self._wrap_error(result, "limit_order")

    @retry_quadratic()
    def create_stop_market(self, symbol: str, side: str, qty: float,
                           stop_price: float, reduce_only: bool = True) -> Dict[str, Any]:
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)
        try:
            order_type = {"trigger": {"triggerPx": str(stop_price), "isMarket": True, "tpsl": "sl"}}
            result = self.exchange.order(
                coin, is_buy, sz, 0, order_type, reduce_only=reduce_only,
            )
        except Exception as e:
            raise TemporaryError(f"HL stop order error: {e}")
        return self._wrap_error(result, "stop_market")

    @retry_quadratic()
    def create_take_profit_market(self, symbol: str, side: str, qty: float,
                                  stop_price: float, reduce_only: bool = True) -> Dict[str, Any]:
        coin = self._to_hl(symbol)
        is_buy = side.upper() == "BUY"
        sz = self._round_size(qty, coin)
        try:
            order_type = {"trigger": {"triggerPx": str(stop_price), "isMarket": True, "tpsl": "tp"}}
            result = self.exchange.order(
                coin, is_buy, sz, 0, order_type, reduce_only=reduce_only,
            )
        except Exception as e:
            raise TemporaryError(f"HL take profit error: {e}")
        return self._wrap_error(result, "take_profit")

    @retry_quadratic()
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
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
        positions = self.get_positions(symbol)
        if not positions:
            return {"msg": "No position to close"}
        pos = positions[0]
        amt = float(pos.get("positionAmt", 0))
        if amt == 0:
            return {"msg": "No position to close"}
        side = "SELL" if amt > 0 else "BUY"
        return self.create_market_order(symbol, side, abs(amt), reduce_only=True)

    # ─── Leverage / Margin ───

    @retry_quadratic()
    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Set leverage while preserving current margin mode.
        HL combines leverage + margin mode in one call.
        """
        coin = self._to_hl(symbol)
        try:
            state = self.info.user_state(self.account_address)
            is_cross = False
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
    def set_margin_mode(self, symbol: str, margin_mode: str = "ISOLATED") -> Dict[str, Any]:
        coin = self._to_hl(symbol)
        is_cross = margin_mode.upper() == "CROSS"
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
    def get_income(self, income_type: Optional[str] = None,
                   start_time: Optional[int] = None, end_time: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        try:
            fills = self.info.user_fills(self.account_address)
        except Exception as e:
            raise TemporaryError(f"HL fills error: {e}")

        results = []
        for fill in fills:
            fill_time = fill.get("time", 0)
            if start_time and fill_time < start_time:
                continue
            if end_time and fill_time > end_time:
                continue

            has_pnl = fill.get("closedPnl", "0") != "0"
            fill_type = "REALIZED_PNL" if has_pnl else "TRADE"
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
