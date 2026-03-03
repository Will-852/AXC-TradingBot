---
name: slash-commands
description: All trading slash commands — run via slash_cmd.py
user-invocable: true
---

# Slash Commands

All commands follow the same pattern. Run the exact bash command and forward stdout to the user. Do not add any commentary.

## Commands

### /bal — Show current USDT balance
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py bal --send
```

### /report — Full trading status report
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py report --send
```

### /pos — Current open positions
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py pos --send
```

### /run — Run live trading cycle now
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py run --send
```

### /dryrun — Dry-run trading cycle (no execution)
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py dryrun --send
```

### /log — Recent trading log entries
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py log --send
```

### /health — System health status
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py health --send
```

### /mode — Show or change trading mode
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py mode --send
```

### /pnl — Profit and loss summary
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py pnl --send
```

### /sl — Current stop-loss levels
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py sl --send
```

### /stop_2 — Pause auto trading
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py stop --send
```

### /resume — Resume auto trading
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py resume --send
```

### /reset_2 — Reset trading state to defaults
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py reset --send
```

### /new — Scan all pairs for new entry signals
```bash
cd /Users/wai/.openclaw/workspace/tools && python3 slash_cmd.py new --send
```
