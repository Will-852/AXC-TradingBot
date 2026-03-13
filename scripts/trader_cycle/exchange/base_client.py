"""
base_client.py — BaseExchangeClient ABC + HmacExchangeClient shared implementation.

BaseExchangeClient: interface contract for all exchange clients (~13 methods).
HmacExchangeClient: shared HTTP + HMAC-SHA256 logic for Binance-compatible APIs.
  - Aster and Binance inherit this (only override BASE_URL + env key names).
  - HyperLiquid implements BaseExchangeClient directly (SDK-based, no HTTP/HMAC).

Design decision: HmacExchangeClient contains ALL shared logic (HTTP, signing,
precision, error handling) so that Aster/Binance subclasses are config-only (~25 lines).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .exceptions import (
    ExchangeError, TemporaryError, DDosProtection,
    OrderError, InsufficientFundsError, InvalidOrderError,
    AuthenticationError, CriticalError,
)
from .retry import retry_quadratic

logger = logging.getLogger(__name__)


# ─── Abstract Base ───

class BaseExchangeClient(ABC):
    """Interface contract for all exchange clients.
    Every exchange client (HMAC-based or SDK-based) must implement these methods.
    """

    @abstractmethod
    def get_usdt_balance(self) -> float: ...

    @abstractmethod
    def get_account_balance(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def create_market_order(self, symbol: str, side: str, qty: float,
                            reduce_only: bool = False) -> Dict[str, Any]: ...

    @abstractmethod
    def create_limit_order(self, symbol: str, side: str, qty: float,
                           price: float, reduce_only: bool = False) -> Dict[str, Any]: ...

    @abstractmethod
    def create_stop_market(self, symbol: str, side: str, qty: float,
                           stop_price: float, reduce_only: bool = True) -> Dict[str, Any]: ...

    @abstractmethod
    def create_take_profit_market(self, symbol: str, side: str, qty: float,
                                  stop_price: float, reduce_only: bool = True) -> Dict[str, Any]: ...

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]: ...

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]: ...

    @abstractmethod
    def set_margin_mode(self, symbol: str, margin_mode: str = "ISOLATED") -> Dict[str, Any]: ...

    @abstractmethod
    def get_income(self, income_type: Optional[str] = None,
                   start_time: Optional[int] = None, end_time: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def close_position_market(self, symbol: str) -> Dict[str, Any]: ...

    @abstractmethod
    def validate_symbol_precision(self, symbol: str) -> Dict[str, float]: ...

    @abstractmethod
    def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]: ...


# ─── HMAC-based shared implementation ───

class HmacExchangeClient(BaseExchangeClient):
    """Shared HTTP + HMAC-SHA256 client for Binance-compatible APIs.

    Subclasses override:
      - BASE_URL: exchange API base URL
      - API_KEY_ENV: env var name for API key
      - SECRET_KEY_ENV: env var name for secret key
      - EXCHANGE_NAME: human-readable name for logging
    """

    BASE_URL: str = ""
    API_KEY_ENV: str = ""
    SECRET_KEY_ENV: str = ""
    EXCHANGE_NAME: str = "exchange"
    RECV_WINDOW: int = 10000

    def __init__(self):
        self.api_key: str = ""
        self.secret_key: str = ""
        self.time_offset: int = 0
        self._exchange_info: Optional[Dict] = None
        self._load_credentials()
        self._sync_time()
        self._validate_connection()

    def _load_credentials(self):
        """Load API credentials from env vars, fallback to secrets/.env."""
        self.api_key = os.getenv(self.API_KEY_ENV, "")
        self.secret_key = os.getenv(self.SECRET_KEY_ENV, "")

        if not self.api_key or not self.secret_key:
            secrets_path = Path(
                os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading"))
            ) / "secrets" / ".env"
            if secrets_path.exists():
                load_dotenv(secrets_path)
                self.api_key = os.getenv(self.API_KEY_ENV, "")
                self.secret_key = os.getenv(self.SECRET_KEY_ENV, "")

        if not self.api_key or not self.secret_key:
            raise CriticalError(f"{self.API_KEY_ENV}/{self.SECRET_KEY_ENV} missing")

    def _sync_time(self):
        """Server time sync to handle clock drift."""
        try:
            response = self._public_request("GET", "/fapi/v1/time")
            server_time = response["serverTime"]
            self.time_offset = server_time - int(time.time() * 1000)
            logger.info(f"{self.EXCHANGE_NAME} time synced, offset: {self.time_offset}ms")
        except Exception as e:
            logger.warning(f"{self.EXCHANGE_NAME} time sync failed: {e}, using 0 offset")

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000) + self.time_offset

    # ─── HTTP Layer ───

    def _public_request(self, method: str, endpoint: str) -> Dict[str, Any]:
        """Unsigned public endpoint."""
        url = f"{self.BASE_URL}{endpoint}"
        return self._make_request(method, url, signed=False)

    def _private_request(
        self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """HMAC-signed private endpoint."""
        if params is None:
            params = {}
        params["timestamp"] = self._get_timestamp()
        params["recvWindow"] = self.RECV_WINDOW

        # Preserve param order for signature (Aster requires this)
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
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
        """Low-level HTTP request + error parsing."""
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
        """Classify HTTP errors into our exception hierarchy."""
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
        """Cached exchangeInfo."""
        if not self._exchange_info:
            self._exchange_info = self._public_request("GET", "/fapi/v1/exchangeInfo")
        return self._exchange_info

    def validate_symbol_precision(self, symbol: str) -> Dict[str, float]:
        """Get symbol precision from exchangeInfo filters."""
        info = self._get_exchange_info()
        symbol_info = next(
            (s for s in info.get("symbols", []) if s["symbol"] == symbol), None
        )
        if not symbol_info:
            raise InvalidOrderError(f"Symbol {symbol} not found on {self.EXCHANGE_NAME}")

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
        """Round to tick size precision."""
        if tick_size <= 0:
            return value
        return round(round(value / tick_size) * tick_size, 8)

    # ─── Account ───

    @retry_quadratic()
    def set_margin_mode(self, symbol: str, margin_mode: str = "ISOLATED") -> Dict[str, Any]:
        try:
            return self._private_request(
                "POST", "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_mode},
            )
        except OrderError as e:
            if "No need to change" in str(e) or "-4046" in str(e):
                return {"msg": f"Already {margin_mode}"}
            raise

    @retry_quadratic()
    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        return self._private_request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
        )

    @retry_quadratic()
    def get_account_balance(self) -> List[Dict[str, Any]]:
        return self._private_request("GET", "/fapi/v2/balance")

    def get_usdt_balance(self) -> float:
        balances = self.get_account_balance()
        for b in balances:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0))
        return 0.0

    @retry_quadratic()
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        data = self._private_request("GET", "/fapi/v2/positionRisk", params)
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]

    # ─── Orders ───

    @retry_quadratic()
    def create_market_order(self, symbol: str, side: str, qty: float,
                            reduce_only: bool = False) -> Dict[str, Any]:
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": qty,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def create_limit_order(self, symbol: str, side: str, qty: float,
                           price: float, reduce_only: bool = False) -> Dict[str, Any]:
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        price = self._round_to_precision(price, precision["price_precision"])
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "LIMIT",
            "quantity": qty, "price": price, "timeInForce": "GTC",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def create_stop_market(self, symbol: str, side: str, qty: float,
                           stop_price: float, reduce_only: bool = True) -> Dict[str, Any]:
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        stop_price = self._round_to_precision(stop_price, precision["price_precision"])
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "STOP_MARKET",
            "quantity": qty, "stopPrice": stop_price,
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": "MARK_PRICE",
        }
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def create_take_profit_market(self, symbol: str, side: str, qty: float,
                                  stop_price: float, reduce_only: bool = True) -> Dict[str, Any]:
        precision = self.validate_symbol_precision(symbol)
        qty = self._round_to_precision(qty, precision["qty_precision"])
        stop_price = self._round_to_precision(stop_price, precision["price_precision"])
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
            "quantity": qty, "stopPrice": stop_price,
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": "MARK_PRICE",
        }
        return self._private_request("POST", "/fapi/v1/order", params)

    @retry_quadratic()
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return self._private_request(
            "DELETE", "/fapi/v1/order",
            {"symbol": symbol, "orderId": order_id},
        )

    @retry_quadratic()
    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._private_request("GET", "/fapi/v1/openOrders", params)

    @retry_quadratic()
    def get_income(self, income_type: Optional[str] = None,
                   start_time: Optional[int] = None, end_time: Optional[int] = None,
                   limit: int = 100) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if income_type:
            params["incomeType"] = income_type
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return self._private_request("GET", "/fapi/v1/income", params)

    def get_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Public order book depth. Detects walls (single order > 5% of total depth)."""
        data = self._public_request("GET", f"/fapi/v1/depth?symbol={symbol}&limit={limit}")
        bids = [[float(p), float(q)] for p, q in data.get("bids", [])]
        asks = [[float(p), float(q)] for p, q in data.get("asks", [])]
        total_qty = sum(q for _, q in bids) + sum(q for _, q in asks)
        wall_threshold = total_qty * 0.05 if total_qty > 0 else float("inf")
        walls = []
        for side, levels in [("bid", bids), ("ask", asks)]:
            for price, qty in levels:
                if qty >= wall_threshold:
                    walls.append({"side": side, "price": price, "qty": qty})
        return {"bids": bids, "asks": asks, "walls": walls}

    def close_position_market(self, symbol: str) -> Dict[str, Any]:
        """Market close: read position → close with reduce_only."""
        positions = self.get_positions(symbol)
        if not positions:
            return {"msg": "No position to close"}
        pos = positions[0]
        amt = float(pos.get("positionAmt", 0))
        if amt == 0:
            return {"msg": "No position to close"}
        side = "SELL" if amt > 0 else "BUY"
        return self.create_market_order(symbol, side, abs(amt), reduce_only=True)

    # ─── Connection Validation ───

    def _validate_connection(self):
        """Init connection test."""
        try:
            self.get_account_balance()
            logger.info(f"{self.EXCHANGE_NAME} initialized successfully")
        except Exception as e:
            logger.error(f"{self.EXCHANGE_NAME} init failed: {e}")
            raise CriticalError(f"Cannot connect to {self.EXCHANGE_NAME}: {e}")
