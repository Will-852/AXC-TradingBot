---
name: new_2
description: Analyze breaking news impact on trading pairs and current positions
user-invocable: true
---

# News Analysis Skill

When the user sends `/new_2 [news text]`, perform a full news impact analysis.

## Steps

1. **Read current state** (bash, parallel):
   ```bash
   cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py pos
   ```
   ```bash
   cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py mode
   ```

2. **Read shared state files**:
   - `~/.openclaw/shared/TRADE_STATE.md` — current position, balance, market mode
   - `~/.openclaw/shared/SIGNAL.md` — last scan result
   - `~/.openclaw/workspace/agents/aster_trader/config/SCAN_CONFIG.md` — latest prices, triggers

3. **Read trading rules** for context on entry/exit criteria:
   - `~/.openclaw/agents/aster_trader/workspace/skills/trading-rules/SKILL.md`

4. **Analyze the news text**:
   a. Identify affected assets (BTC, ETH, XRP, XAG, USD, macro)
   b. Score market impact per pair: BULLISH / BEARISH / NEUTRAL
   c. Cross-reference with current technical state (market mode, RSI, MACD from SCAN_CONFIG)
   d. Assess risk to any open position

5. **Send Telegram report** using this exact format (wrap in `<pre>` tags):

```
📰 NEWS ANALYSIS · [timestamp UTC+8]
─────────────────────────────
News: [1-2 line summary of input]

Impact Assessment:
BTC  [BULLISH/BEARISH/NEUTRAL] [reason]
ETH  [BULLISH/BEARISH/NEUTRAL] [reason]
XRP  [BULLISH/BEARISH/NEUTRAL] [reason]
XAG  [BULLISH/BEARISH/NEUTRAL] [reason]

Current Position Risk:
[if open position: how does this news affect it?]
[if no position: NO OPEN POSITIONS]

Recommendation:
[HOLD / REDUCE RISK / PAUSE TRADING / OPPORTUNITY]
[one line reason]
─────────────────────────────
```

## Rules

- Use tier1 (claude-sonnet-4-6) for this analysis — requires reasoning
- Keep output under 25 lines
- Be specific about WHY each pair is affected
- If news is unclear or irrelevant to crypto/commodities, say so
- Do not recommend specific entry/exit prices — that's the trader agent's job
- Send via Telegram using:
  ```bash
  cd /Users/wai/.openclaw/workspace/tools && python3 -c "
  import sys; sys.path.insert(0, '.')
  from slash_cmd import send_telegram
  send_telegram('<pre>YOUR_FORMATTED_OUTPUT_HERE</pre>')
  "
  ```
