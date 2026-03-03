"""
Aster DEX Futures API Client v1.0
✓ P0-P2漏洞全修復 ✓ Quadratic backoff ✓ Precision validation
✓ Margin mode ✓ Timestamp sync ✓ reduceOnly安全
Base URL: https://fapi.asterdex.com
"""

import os
import time
import hmac
import hashlib
import json
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, List
from functools import wraps
import logging
from pathlib import Path

from dotenv import load_dotenv
from .exceptions import (
    ExchangeError, TemporaryError, DDosProtection,
    OrderError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)

logger = logging.getLogger(__name__)


# ─── Retry Decorator (standalone, not instance method) ───
# Fix #1: Extracted from class to work as proper decorator

_BACKOFF_SECONDS = [1, 4, 9, 16, 25]  # n² standard sequence
_MAX_RETRIES = 5


def retry_quadratic(max_retries: int = _MAX_RETRIES):
    """Quadratic backoff retry decorator for exchange operations."""
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
                    raise  # non-retryable
                except AuthenticationError as e:
                    raise CriticalError(f"Auth fatal: {e}")
            raise last_exc
        return wrapper
    return decorator


class AsterClient:
    """Aster DEX Futures API client with HMAC-SHA256 authentication."""

    BASE_URL = "https://fapi.asterdex.com"
    RECV_WINDOW = 10000  # 10s 生產標準

    def __init__(self):
        self.api_key: str = ""
        self.secret_key: str = ""
        self.time_offset: int = 0
        self._exchange_info: Optional[Dict] = None
        self._load_credentials()
        self._sync_time()
        self._validate_connection()

    def _load_credentials(self):
        """優先環境變量，fallback .env"""
        self.api_key = os.getenv("ASTER_API_KEY", "")
        # Fix #2: Use ASTER_API_SECRET (matching .env file)
        self.secret_key = os.getenv("ASTER_API_SECRET", "")

        if not self.api_key or not self.secret_key:
            secrets_path = Path.home() / ".openclaw/secrets/.env"
            if secrets_path.exists():
                load_dotenv(secrets_path)
                self.api_key = os.getenv("ASTER_API_KEY", "")
                self.secret_key = os.getenv("ASTER_API_SECRET", "")

        if not self.api_key or not self.secret_key:
            raise CriticalError("ASTER_API_KEY/ASTER_API_SECRET missing")

    def _sync_time(self):
        """伺服器時間校正"""
        try:
            response = self._public_request("GET", "/fapi/v1/time")
            server_time = response["serverTime"]
            self.time_offset = server_time - int(time.time() * 1000)
            logger.info(f"Time synced, offset: {self.time_offset}ms")
        except Exception as e:
            logger.warning(f"Time sync failed: {e}, using 0 offset")

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000) + self.time_offset

    # ─── HTTP Layer ───

    def _public_request(self, method: str, endpoint: str) -> Dict[str, Any]:
        """無簽名公共端點"""
        url = f"{self.BASE_URL}{endpoint}"
        return self._make_request(method, url, signed=False)

    def _private_request(
        self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """HMAC簽名私有端點"""
        if params is None:
            params = {}
        params["timestamp"] = self._get_timestamp()
        params["recvWindow"] = self.RECV_WINDOW

        # Fix #5: Do NOT sort params — Aster requires signature computed
        # on the EXACT same param order as the URL query string.
        query_string = "&".join(
            f"{k}={v}" for k, v in params.items()
        )
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature

        url = f"{self.BASE_URL}{endpoint}"
        if method == "GET":
            full_qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{full_qs}"
        return self._make_request(
            method, url, signed=True,
            body_params=params if method != "GET" else None,
        )

    def _make_request(
        self, method: str, url: str, signed: bool = False,
        body_params: Optional[Dict[str, Any]] = None, timeout: float = 10,
    ) -> Any:
        """底層HTTP請求 + 錯誤解析"""
        headers: Dict[str, str] = {
            "User-Agent": "OpenClaw-Trader/1.0",
        }
        if signed:
            headers["X-MBX-APIKEY"] = self.api_key

        data = None
        if body_params:
            data = urllib.parse.urlencode(body_params).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            return self._handle_http_error(e, url)
        except Exception as e:
            raise TemporaryError(f"Network error: {e}")

    def _handle_http_error(self, e: urllib.error.HTTPError, url: str) -> Any:
        """HTTP錯誤分類"""
        status = e.code
        try:
            error_data = json.loads(e.read().decode())
            code = error_data.get("code", 0)
            msg = error_data.get("msg", "")
        except Exception:
            code, msg = 0, str(e)

        if status == 429 or "Rate Limit" in msg:
            raise DDosProtection(f"Rate limit hit: {msg}")
        elif status >= 500:
            raise TemporaryError(f"Server error {status}: {msg}")
        elif status == 401:
            raise AuthenticationError(f"API key invalid: {msg}")
        elif code == -2010:
            raise InsufficientFundsError(f"Insufficient balance: {msg}")
        elif code in (-1013, -1111, -1116):
            raise InvalidOrderError(f"Invalid order: {code} {msg}")
        elif status >= 400:
            raise OrderError(f"Order rejected {status}: {msg}")
        else:
            raise ExchangeError(f"HTTP {status}: {msg} | {url}")

    # ─── Exchange Info & Precision ───

    def _get_exchange_info(self) -> Dict[str, Any]:
        """緩存exchangeInfo"""
        if not self._exchange_info:
            self._exchange_info = self._public_request("GET", "/fapi/v1/exchangeInfo")
        return self._exchange_info

    def validate_symbol_precision(self, symbol: str) -> Dict[str, float]:
        """驗證symbol精度，防止order reject"""
        info = self._get_exchange_info()
        symbol_info = next(
            (s for s in info.get("symbols", []) if s["symbol"] == symbol), None
        )
        if not symbol_info:
            raise InvalidOrderError(f"Symbol {symbol} not found on exchange")

        filters = {f["filterType"]: f for f in symbol_info.get("filters", [])}
        return {
            "price_precision": float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
            "qty_precision": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
            "min_qty": float(filters.get("LOT_SIZE", {}).get("minQty", 0.001)),
            "min_notional": float(
                filters.get("MIN_NOTIONAL", {}).get("minNotional", 5.0)
            ),
        }

    def _round_to_precision(self, value: float, tick_size: float) -> float:
        """精度四捨五入"""
        if tick_size <= 0:
            return value
        return round(round(value / tick_size) * tick_size, 8)

    # ─── Account ───

    @retry_quadratic()
    def set_margin_mode(
        self, symbol: str, margin_mode: str = "ISOLATED"
    ) -> Dict[str, Any]:
        """設置獨立保證金模式"""
        try:
            return self._private_request(
                "POST", "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_mode},
            )
        except OrderError as e:
            # Already in the desired margin mode → not an error
            if "No need to change" in str(e) or "-4046" in str(e):
                return {"msg": f"Already {margin_mode}"}
            raise

    @retry_quadratic()
    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """設置槓桿"""
        return self._private_request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
        )

    @retry_quadratic()
    def get_account_balance(self) -> List[Dict[str, Any]]:
        """獲取帳戶餘額"""
        return self._private_request("GET", "/fapi/v2/balance")

    def get_usdt_balance(self) -> float:
        """獲取 USDT available balance（convenience method）"""
        balances = self.get_account_balance()
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0))
        return 0.0

    @retry_quadratic()
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """獲取倉位"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = self._private_request("GET", "/fapi/v2/positionRisk", params)
        # Filter to non-zero positions
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]

    # ─── Orders ───

    @retry_quadratic()
    def create_market_order(
        self, symbol: str, side: str, qty: float,
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """市價單"""
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
        }
        if reduce_only:
            params["reduceOnly"] = "true"

        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def create_stop_market(
        self, symbol: str, side: str, qty: float,
        stop_price: float, reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """止損單（reduceOnly 安全）"""
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        stop_price = self._round_to_precision(stop_price, precision["price_precision"])

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "quantity": qty,
            "stopPrice": stop_price,
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": "MARK_PRICE",
        }
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def create_take_profit_market(
        self, symbol: str, side: str, qty: float,
        stop_price: float, reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """止盈單（reduceOnly 安全）"""
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        stop_price = self._round_to_precision(stop_price, precision["price_precision"])

        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": qty,
            "stopPrice": stop_price,
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": "MARK_PRICE",
        }
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """取消訂單"""
        return self._private_request(
            "DELETE", "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    @retry_quadratic()
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """未成交訂單"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._private_request("GET", "/fapi/v1/openOrders", params)

    @retry_quadratic()
    def get_income(
        self, income_type: Optional[str] = None,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """獲取收益記錄（已實現PnL、funding等）"""
        params: Dict[str, Any] = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return self._private_request("GET", "/fapi/v1/income", params)

    def close_position_market(self, symbol: str) -> Dict[str, Any]:
        """市價平倉（先讀倉位再平）"""
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

    # ─── Validation ───

    def _validate_connection(self):
        """初始化連接測試"""
        try:
            self.get_account_balance()
            logger.info("✅ AsterClient initialized successfully")
        except Exception as e:
            logger.error(f"❌ Client init failed: {e}")
            raise CriticalError(f"Cannot connect to AsterDEX: {e}")


# ─── CLI Test ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = AsterClient()
    print("Balance:", [b for b in client.get_account_balance() if b["asset"] == "USDT"])
    print("Positions:", client.get_positions())
