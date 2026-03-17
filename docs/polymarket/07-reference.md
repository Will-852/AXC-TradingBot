# Reference — Error Codes, Token Model, Gotchas

---

## Error Codes

### Global (All Authenticated Endpoints)
| Status | Error | Cause |
|--------|-------|-------|
| 401 | Invalid api key | Missing/expired/invalid API key |
| 401 | Invalid L1 Request headers | Malformed HMAC or signature |
| 429 | Too Many Requests | Rate limit exceeded |
| 503 | Trading is currently disabled | Exchange paused |
| 503 | Trading is currently cancel-only | Only cancels allowed |

### Order Placement (POST /order, /orders)
| Status | Error | Cause |
|--------|-------|-------|
| 400 | Invalid order payload | Malformed/incomplete data |
| 400 | order owner has to be the owner of the API KEY | Maker ≠ key owner |
| 400 | order signer address has to be the address of the API KEY | Signer mismatch |
| 400 | address banned | Trading restrictions |
| 400 | address in closed only mode | Can only close positions |
| 400 | Too many orders in payload | >15 for POST, >3000 for DELETE |
| 400 | invalid post-only order: order crosses book | Would immediately match |
| 400 | Price breaks minimum tick size rule | Price not aligned to tick |
| 400 | Size lower than the minimum | Below min_order_size |
| 400 | Duplicated | Duplicate order |
| 400 | not enough balance / allowance | Insufficient funds |
| 400 | invalid nonce | Nonce already used |
| 400 | invalid expiration | Past timestamp |
| 400 | order canceled in CTF exchange contract | Already canceled on-chain |
| 400 | FOK orders couldn't be fully filled | Fill-or-Kill incomplete |
| 400 | no orders found to match with FAK order | No counterparties |
| 400 | market is not yet ready | Not accepting orders |

### Matching Engine
| Status | Error | Cause |
|--------|-------|-------|
| 425 | (varies) | Engine restarting — retry with backoff |
| 500 | no matching orders | No counterparties |
| 500 | FOK orders are filled or killed | Incomplete fill |
| 500 | trade contains rounding issues | Decimal precision |
| 500 | price discrepancy | Price variance exceeded |

---

## Token Model: Conditional Token Framework (CTF)

### Token Standard
- **ERC1155** on **Polygon**
- Based on **Gnosis Conditional Tokens Framework**

### Position ID Derivation
```
Step 1: conditionId = keccak256(oracleAddress, questionId, outcomeSlotCount)
        outcomeSlotCount = 2 for binary

Step 2: collectionId = keccak256(parentCollectionId, conditionId, indexSet)
        indexSet = 0b01 (Yes) or 0b10 (No)
        parentCollectionId = bytes32(0) for top-level

Step 3: positionId = keccak256(collateralToken, collectionId)
        collateralToken = USDC.e address
```

### Core Token Operations
| Operation | Description |
|-----------|-------------|
| **Split** | Deposit 1 USDC.e → receive 1 Yes + 1 No token |
| **Merge** | Return 1 Yes + 1 No → receive 1 USDC.e |
| **Redeem** | After resolution, winning tokens → 1 USDC.e each |
| **Convert** | (Neg Risk only) 1 No → 1 Yes in every other outcome |

### Exchange Matching with Mint/Merge
- **Mint + Match**: Yes buyer + No buyer → combined $1 splits into tokens via CTF
- **Merge + Match**: Yes seller + No seller → tokens merge back into USDC.e

---

## Decimal Precision Rules

| Context | Precision | Example |
|---------|-----------|---------|
| Price | Must align to tick_size | 0.01, 0.001, etc. |
| Sell maker amount | Max 2 decimal places | 100.50 |
| Sell taker amount | Max 4 decimal places | 55.2750 |
| Product (size × price) | Max 2 decimal places | 50.00 |
| Fee minimum | 0.0001 USDC | Below rounds to 0 |

---

## Gotchas & Common Pitfalls

### 1. token_id vs condition_id
- **CLOB API + Market WS** → use `token_id` (per-outcome, very long integer string)
- **Data API + User WS** → use `condition_id` (per-market, 0x hex hash)
- Mixing them = 400 errors. This is the #1 integration mistake.

### 2. Tick Size is Dynamic
- Markets can change tick size when they become one-sided
- Old tick size orders get rejected
- Always check `GET /tick-size/{token_id}` before placing orders
- Subscribe to `tick_size_change` WebSocket events

### 3. Post-Only Rejection
- Post-only orders crossing the spread are **rejected**, not executed
- This is by design — guarantees maker status

### 4. FOK Must Fill 100%
- Fill-or-Kill requires complete fill by existing liquidity
- If not 100% fillable, entire order is cancelled
- Use FAK for partial fills

### 5. Neg Risk Markets
- `neg_risk: true` markets use different exchange contract (`NegRiskCTFExchange`)
- Multi-outcome events where outcomes are mutually exclusive
- Requires `neg-risk-ctf-adapter` for token operations

### 6. Token Allowances (EOA Only)
Before trading with EOA wallet, approve 3 contracts:
- CTF Exchange for USDC.e
- CTF Exchange for Conditional Tokens
- Neg Risk Exchange (if trading neg risk markets)
Email/Magic wallets handle this automatically.

### 7. Request Staleness
- `POLY_TIMESTAMP` must be within ~30 seconds of server time
- Use `GET /time` to sync clocks

### 8. Error 425 (Too Early)
- Matching engine restarting
- Retry with exponential backoff (1s, 2s, 4s...)

### 9. Heartbeat Required for Market Makers
- Without periodic `POST /heartbeats`, open orders may be auto-cancelled
- Send every 30-60 seconds for safety

### 10. Geographic Blocking
- API endpoints are accessible globally (unlike web UI)
- But trades from blocked regions may be restricted
- Check `GET https://polymarket.com/api/geoblock`

### 11. Nonce Uniqueness
- Each order nonce must be unique per signer
- Reusing a nonce = order rejected
- Use incrementing counter or random generation

### 12. Minimum Order Size
- Enforced per-market (varies)
- Check `min_order_size` from `/book` response
- Orders below minimum are rejected

---

## Python Quick Start (py-clob-client)

### Install
```bash
pip install py-clob-client python-dotenv
```

### Read-Only (no auth)
```python
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com")

# Health check
client.get_ok()

# Server time
client.get_server_time()

# Order book
book = client.get_order_book("71321045...")

# Price
price = client.get_price("71321045...", "BUY")

# Midpoint
mid = client.get_midpoint("71321045...")

# Markets (paginated)
markets = client.get_markets()
```

### Authenticated Trading
```python
import os
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

client = ClobClient(
    host="https://clob.polymarket.com",
    key=os.getenv("PK"),
    chain_id=137,
    creds=ApiCreds(
        api_key=os.getenv("CLOB_API_KEY"),
        api_secret=os.getenv("CLOB_SECRET"),
        api_passphrase=os.getenv("CLOB_PASS_PHRASE"),
    ),
    signature_type=1,  # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE
    funder=os.getenv("FUNDER_ADDRESS"),
)

# Create/derive API credentials (first time only)
creds = client.create_or_derive_api_creds()
# Returns: {apiKey, secret, passphrase}

# Limit order (GTC)
signed = client.create_order(OrderArgs(
    price=0.50,
    size=20,
    side=BUY,
    token_id="71321045...",
))
resp = client.post_order(signed)

# Market order (FOK)
signed = client.create_market_order(MarketOrderArgs(
    token_id="71321045...",
    amount=100,  # $100 USDC for buys
    side=BUY,
))
resp = client.post_order(signed, orderType=OrderType.FOK)

# Cancel
client.cancel("order_id")
client.cancel_all()

# Balance
balance = client.get_balance_allowance()
```

---

## Useful Links

- [Official Docs](https://docs.polymarket.com)
- [CLOB Introduction](https://docs.polymarket.com/developers/CLOB/introduction)
- [Authentication](https://docs.polymarket.com/api-reference/authentication)
- [Rate Limits](https://docs.polymarket.com/api-reference/rate-limits)
- [Error Codes](https://docs.polymarket.com/resources/error-codes)
- [Orders](https://docs.polymarket.com/developers/CLOB/orders/orders)
- [WebSocket](https://docs.polymarket.com/developers/CLOB/websocket/wss-overview)
- [Gamma API](https://docs.polymarket.com/developers/gamma-markets-api/overview)
- [py-clob-client](https://github.com/Polymarket/py-clob-client) (Python SDK)
- [clob-client](https://github.com/Polymarket/clob-client) (TypeScript SDK)
- [ctf-exchange](https://github.com/Polymarket/ctf-exchange) (Smart contracts)
