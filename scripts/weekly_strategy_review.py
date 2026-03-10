#!/usr/bin/env python3
"""
weekly_strategy_review.py — 每週策略回顧
讀 trades.jsonl + analysiss.jsonl + params.py → Claude Sonnet 分析 → 原子寫入 STRATEGY.md

排程：每週一 10:00 HKT via LaunchAgent ai.openclaw.strategyreview
手動：python3 ~/projects/axc-trading/scripts/weekly_strategy_review.py [--dry-run]
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ──
BASE_DIR = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
TRADES_FILE = BASE_DIR / "memory" / "store" / "trades.jsonl"
ANALYSIS_FILE = BASE_DIR / "memory" / "store" / "analysiss.jsonl"
STRATEGY_FILE = BASE_DIR / "ai" / "STRATEGY.md"
BACKUP_DIR = BASE_DIR / "backups"

# ── Load .env ──
ENV_PATH = BASE_DIR / "secrets" / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

PROXY_API_KEY = os.environ.get("PROXY_API_KEY", "")
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "https://tao.plus7.plus/v1")
CLAUDE_MODEL = "claude-sonnet-4-6"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HKT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [STRATEGY] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("strategy_review")

# ── Config/params import ──
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "config"))
try:
    import params as _params
except ImportError:
    _params = None

try:
    from config.profiles.loader import load_profile as _load_profile
    _active_profile = _load_profile()
except Exception:
    _active_profile = None


def read_jsonl(path: Path, limit: int = 0) -> list[dict]:
    """Read JSONL file, optionally limit to last N records."""
    if not path.exists():
        return []
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit > 0:
        return records[-limit:]
    return records


def get_params_snapshot() -> dict:
    """Extract current trading params for context."""
    if _params is None:
        return {"error": "params.py not found"}
    # Profile-aware values (from config/profiles/)
    _ap = _active_profile or {}
    return {
        "active_profile": getattr(_params, "ACTIVE_PROFILE", "?"),
        "risk_per_trade": _ap.get("risk_per_trade_pct", 0.02),
        "max_position_usdt": getattr(_params, "MAX_POSITION_SIZE_USDT", 0),
        "max_open_positions": _ap.get("max_open_positions", 2),
        "aster_symbols": getattr(_params, "ASTER_SYMBOLS", []),
        "binance_symbols": getattr(_params, "BINANCE_SYMBOLS", []),
        "trigger_pct": _ap.get("trigger_pct", getattr(_params, "TRIGGER_PCT", 0)),
        "bb_touch_tol": getattr(_params, "BB_TOUCH_TOL_DEFAULT", 0),
        "bb_width_min": getattr(_params, "BB_WIDTH_MIN", 0),
    }


def build_prompt(trades: list[dict], analyses: list[dict], params: dict) -> tuple[str, str]:
    """Build system + user prompt for Claude.

    Returns (system_prompt, user_prompt).
    """
    # Summarise trades
    total = len(trades)
    with_exit = [t for t in trades if t.get("exit") is not None]
    wins = [t for t in with_exit if (t.get("pnl") or 0) > 0]
    losses = [t for t in with_exit if (t.get("pnl") or 0) < 0]
    total_pnl = sum(t.get("pnl", 0) or 0 for t in with_exit)

    trades_summary = json.dumps(trades[-30:], ensure_ascii=False, indent=1, default=str)
    analyses_summary = json.dumps(analyses[-10:], ensure_ascii=False, indent=1, default=str)

    system_prompt = (
        "You are a trading strategy analyst for OpenClaw, a crypto futures trading system. "
        "Analyze the provided trade records, analysis logs, and current parameters. "
        "Output in Traditional Chinese (繁體中文). "
        "Be data-driven. If data is sparse, clearly state observations are preliminary."
    )

    user_prompt = f"""請根據以下數據生成每週策略回顧報告。

## 交易數據摘要
- 總記錄: {total}
- 已平倉: {len(with_exit)}（勝: {len(wins)}, 負: {len(losses)}）
- 總 PnL: ${total_pnl:.2f}

## 最近交易記錄（最多30條）
```json
{trades_summary}
```

## 最近分析記錄（最多10條）
```json
{analyses_summary}
```

## 當前交易參數
```json
{json.dumps(params, indent=2)}
```

## 輸出格式

請嚴格按以下6個 section 輸出（Markdown 格式）：

### 1. 交易風格摘要
概括目前嘅交易風格同偏好。

### 2. 有效策略
邊啲策略/模式表現最好？列出具體例子。

### 3. 需改善
邊啲方面需要改進？提出具體建議。

### 4. 個人交易規則
根據歷史表現，建議嘅個人交易規則。

### 5. 統計概覽
| 指標 | 數值 |
|------|------|
| Win Rate | X% |
| Avg Win | $X |
| Avg Loss | $X |
| Profit Factor | X |
| Max Drawdown | $X |

### 6. 數據覆蓋
說明目前數據嘅質量同完整度，指出邊啲結論係 preliminary。"""

    return system_prompt, user_prompt


def call_claude(system_prompt: str, user_prompt: str) -> str:
    """Call Claude Sonnet via proxy. Returns response text."""
    if not PROXY_API_KEY:
        raise ValueError("PROXY_API_KEY not set")

    url = f"{PROXY_BASE_URL}/messages"
    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PROXY_API_KEY}",
        "anthropic-version": "2023-06-01",
    })

    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())

    # Extract text from response
    content = data.get("content", [])
    parts = [block.get("text", "") for block in content if block.get("type") == "text"]
    return "\n".join(parts)


def atomic_write(path: Path, content: str):
    """Atomic write: tempfile in same dir → os.replace()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_strategy():
    """Backup current STRATEGY.md before overwriting."""
    if not STRATEGY_FILE.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(HKT).strftime("%Y%m%d_%H%M")
    dest = BACKUP_DIR / f"STRATEGY.md.{date_str}"
    shutil.copy2(STRATEGY_FILE, dest)
    log.info(f"Backed up STRATEGY.md → {dest}")


def send_telegram(message: str):
    """Send Telegram notification (optional, best-effort)."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TG_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=payload, method="POST", headers={
            "Content-Type": "application/json",
        })
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Weekly Strategy Review")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt only, don't call API")
    args = parser.parse_args()

    now = datetime.now(HKT)
    log.info(f"Weekly strategy review starting at {now.strftime('%Y-%m-%d %H:%M')} HKT")

    # 1. Read data
    trades = read_jsonl(TRADES_FILE)
    analyses = read_jsonl(ANALYSIS_FILE, limit=20)
    params = get_params_snapshot()

    log.info(f"Trades: {len(trades)} | Analyses: {len(analyses)}")

    # 2. Build prompt
    system_prompt, user_prompt = build_prompt(trades, analyses, params)

    if args.dry_run:
        print("=== SYSTEM ===")
        print(system_prompt)
        print("\n=== USER ===")
        print(user_prompt)
        return

    # 3. Call Claude
    log.info(f"Calling {CLAUDE_MODEL} via proxy...")
    try:
        review = call_claude(system_prompt, user_prompt)
    except Exception as e:
        log.error(f"Claude API call failed: {e}")
        send_telegram(f"⚠️ 策略回顧失敗: {e}")
        sys.exit(1)

    # 4. Write output
    header = (
        f"# OpenClaw — 交易策略規則\n"
        f"> 自動更新：weekly_strategy_review.py\n"
        f"> 最後更新：{now.strftime('%Y-%m-%d %H:%M')} HKT\n\n"
    )
    full_content = header + review + "\n"

    backup_strategy()
    atomic_write(STRATEGY_FILE, full_content)
    log.info(f"STRATEGY.md updated ({len(full_content)} chars)")

    # 5. Telegram notification
    send_telegram(
        f"📊 <b>每週策略回顧已更新</b>\n"
        f"交易記錄: {len(trades)} | 分析: {len(analyses)}\n"
        f"詳見 ai/STRATEGY.md"
    )

    log.info("Done.")


if __name__ == "__main__":
    main()
