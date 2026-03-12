"""
Binance Futures API Client — config-only subclass of HmacExchangeClient.
All HTTP, HMAC, retry, precision, and order logic lives in base_client.py.
"""

from .base_client import HmacExchangeClient


class BinanceClient(HmacExchangeClient):
    """Binance Futures API client.
    Inherits everything from HmacExchangeClient — only config differs.
    """

    BASE_URL = "https://fapi.binance.com"
    API_KEY_ENV = "BINANCE_API_KEY"
    SECRET_KEY_ENV = "BINANCE_API_SECRET"
    EXCHANGE_NAME = "Binance"


# ─── CLI Test ───
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    client = BinanceClient()
    print("Balance:", [b for b in client.get_account_balance() if b["asset"] == "USDT"])
    print("Positions:", client.get_positions())
