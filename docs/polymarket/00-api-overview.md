# Polymarket API Overview

> Reference for AXC integration. Last updated: 2026-03-17

## Architecture

Polymarket = hybrid-decentralized prediction market on **Polygon** (Chain ID 137).
Off-chain order matching + on-chain settlement. Non-custodial.

## 5 API Services

| Service | Base URL | Auth | Purpose |
|---------|----------|------|---------|
| **Gamma** | `https://gamma-api.polymarket.com` | None | Market discovery, metadata, search |
| **CLOB** | `https://clob.polymarket.com` | Read=None / Write=L2 | Orderbook, pricing, trading |
| **Data** | `https://data-api.polymarket.com` | None | Positions, trades, leaderboards |
| **Relayer** | `https://relayer-v2.polymarket.com` | Builder Key | Gasless tx submission |
| **Bridge** | `https://bridge.polymarket.com` | None | Deposits / withdrawals |

Staging CLOB: `https://clob-staging.polymarket.com`

## 3 WebSocket Channels

| Channel | URL | Auth |
|---------|-----|------|
| Market | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | None |
| User | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | API creds |
| Sports | `wss://sports-api.polymarket.com/ws` | None |

---

## Authentication

### L1 (Private Key / EIP-712) — Bootstrap only
Used to create/derive API credentials. Signs EIP-712 message with wallet private key.

**Domain**: `ClobAuthDomain`, version `1`, chainId `137`

**Headers**:
- `POLY_ADDRESS` — Polygon signer address
- `POLY_SIGNATURE` — EIP-712 signature
- `POLY_TIMESTAMP` — Unix timestamp (from CLOB `/time`)
- `POLY_NONCE` — Credential identifier (default: 0)

### L2 (HMAC-SHA256) — All trading endpoints
**Headers** (all 5 required):
- `POLY_API_KEY` — UUID
- `POLY_ADDRESS` — Polygon signer address
- `POLY_SIGNATURE` — HMAC-SHA256 of request using `secret`
- `POLY_PASSPHRASE` — hex-encoded string
- `POLY_TIMESTAMP` — Unix timestamp

### Builder Auth (Relayer)
- `POLY_BUILDER_API_KEY`, `POLY_BUILDER_PASSPHRASE`, `POLY_BUILDER_SIGNATURE`, `POLY_BUILDER_TIMESTAMP`

### Signature Types
| Type | Value | Description |
|------|-------|-------------|
| EOA | 0 | Standard MetaMask wallet |
| POLY_PROXY | 1 | Magic Link / email |
| GNOSIS_SAFE | 2 | Browser / embedded (most common) |

---

## ID Hierarchy (Critical)

```
Event (Gamma integer ID)
  → Market (condition_id: 0x... hash)     ← Data API + User WS
      → Token (token_id: long integer)     ← CLOB API + Market WS
          → "Yes" outcome token
          → "No" outcome token
```

Discover `token_id` via Gamma API → use in CLOB API calls.
**Do NOT mix** `token_id` (CLOB) with `condition_id` (Data) — common source of 400 errors.

---

## Market Types

### Binary (Standard)
- Yes/No question, 2 ERC1155 outcome tokens
- Invariant: `P_YES + P_NO = $1.00`
- Traded on **CTF Exchange**

### Multi-Outcome (Negative Risk)
- Multiple mutually exclusive outcomes under one Event
- Each outcome is its own binary market linked via **NegRiskAdapter**
- 1 No token → 1 Yes token in every other outcome (atomic conversion)
- Gamma flag: `negRisk: true`
- Traded on **NegRiskCTFExchange**

**No scalar/continuous markets** — everything is binary.

---

## Pricing

- Prices = decimal dollars: `$0.00` to `$1.00` (= implied probability)
- Tick sizes: 0.1 / 0.01 / 0.001 / 0.0001 (per-market, dynamic)
- Tick size changes → old orders rejected

---

## Order Types

| Type | Behavior |
|------|----------|
| GTC | Good Till Cancelled — rests on book indefinitely |
| GTD | Good Till Date — auto-cancels at expiration |
| FOK | Fill Or Kill — 100% fill or reject entirely |
| FAK | Fill And Kill — partial fill OK, remainder killed |
| Post-only | Must add liquidity; rejects if would immediately match |

---

## Fees

Most markets are **fee-free**. Only select categories:

| Category | Fee Rate | Exponent | Max Rate (at p=0.50) | Maker Rebate |
|----------|----------|----------|---------------------|--------------|
| Crypto | 0.25 | 2 | ~1.56% | 20% |
| NCAAB | 0.0175 | 1 | ~0.44% | 25% |
| Serie A | 0.0175 | 1 | ~0.44% | 25% |

Formula: `fee = C * p * feeRate * (p * (1-p))^exponent`
- Buy: fees in shares. Sell: fees in USDC. Min: 0.0001 USDC.

---

## Rate Limits (sliding 10s windows)

### Gamma (4,000/10s general)
| Endpoint | Limit |
|----------|-------|
| `/events` | 500/10s |
| `/markets` | 300/10s |
| `/public-search` | 350/10s |
| `/comments`, `/tags` | 200/10s |

### CLOB (9,000/10s general)
| Endpoint | Burst (10s) | Sustained (10min) |
|----------|-------------|-------------------|
| `POST /order` | 3,500 | 36,000 |
| `DELETE /order` | 3,000 | 30,000 |
| `POST /orders` | 1,000 | 15,000 |
| `/book`, `/price`, `/midpoint` | 1,500 | — |
| `/books`, `/prices`, `/midpoints` | 500 | — |

### Data (1,000/10s general)
| Endpoint | Limit |
|----------|-------|
| `/trades` | 200/10s |
| `/positions` | 150/10s |

Behavior: **throttled** (queued/delayed), not immediately rejected. HTTP 429 when exceeded.

---

## Resolution: UMA Optimistic Oracle

1. Proposal → bond ~$750 USDC.e
2. Challenge window: **2 hours**
3. Undisputed → auto-resolve (~98.5% of markets)
4. Single dispute → re-proposal (new 2h window)
5. Double dispute → UMA DVM vote (4-6 days)

---

## Contract Addresses (Polygon Mainnet)

| Contract | Address |
|----------|---------|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` |
| Conditional Tokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` |
| Gnosis Safe Factory | `0xaacfeea03eb1561c4e67d661e40682bd20e3541b` |
| Proxy Factory | `0xaB45c5A4B0c941a2F231C04C3f49182e1A254052` |
| UMA Adapter | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` |
| UMA Optimistic Oracle | `0xCB1822859cEF82Cd2Eb4E6276C7916e692995130` |

---

## SDKs

| Language | Package |
|----------|---------|
| Python | `pip install py-clob-client` (v0.34.6) |
| TypeScript | `@polymarket/clob-client` |
| Rust | `polymarket-client-sdk` |
| Community (Python) | `pip install polymarket-apis` (unified, Pydantic models) |

---

## Geographic Restrictions

**Fully blocked (33 countries)**: US, GB, AU, DE, FR, IT, NL, BE, RU, CN, ...
**Close-only**: PL, SG, TH, TW

Check: `GET https://polymarket.com/api/geoblock` → `{blocked, ip, country}`

---

## File Index

| File | Content |
|------|---------|
| `00-api-overview.md` | This file — architecture, auth, concepts |
| `01-gamma-api.md` | Gamma API full endpoint reference |
| `02-clob-market-data.md` | CLOB read-only endpoints |
| `03-clob-trading.md` | CLOB trading + auth + account endpoints |
| `04-data-api.md` | Data API — positions, trades, leaderboards |
| `05-relayer-bridge.md` | Relayer + Bridge APIs |
| `06-websocket.md` | WebSocket channels + events |
| `07-reference.md` | Error codes, token model, gotchas |
