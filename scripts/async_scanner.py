#!/usr/bin/env python3
"""
async_scanner.py — 並行多幣種掃描引擎
版本：v4 | 2026-03-05 | 10年長期運行設計

v4 修正：
  [1] LOGS_DIR.mkdir() 必須在 FileHandler 之前
  [2] RotatingFileHandler 取代 FileHandler（防止無限增長）
  [3] _rotate_log 用原子寫入（防止 dashboard 讀到一半）
  [4] 空結果保護 prev_cache（防止價格消失）

設計原則：
  A. 任何單點錯誤唔殺死整個系統
  B. 所有狀態可從外部觀察（心跳文件）
  C. 所有覆寫操作用原子寫入（臨時文件 + rename）
  D. 日誌文件有 rotation（10年唔爆磁碟）
  E. 空結果唔覆寫上次成功的快取

已知限制（接受）：
  - params.py 修改需重啟掃描器（模組層級 import）
  - 用直接 HTTP 而非 AsterClient（同 light_scan 一致）
  - SCAN_LOG rotation 讀入記憶體（500行 ≈ 50KB，可接受）
"""

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── 路徑設定 ─────────────────────────────────────
BASE_DIR   = Path.home() / ".openclaw"
SHARED_DIR = BASE_DIR / "shared"
LOGS_DIR   = BASE_DIR / "logs"

# 修正1：mkdir 必須在任何文件操作之前
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "config"))

log = logging.getLogger("scanner")

# ── API Endpoints ────────────────────────────────
ASTER_FAPI = "https://fapi.asterdex.com/fapi/v1"

# ── 模組層級 import params（啟動一次，修改需重啟）───
try:
    import params as _params
    ASTER_SYMBOLS   = list(getattr(_params, "ASTER_SYMBOLS",
                           ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]))
    BINANCE_SYMBOLS = list(getattr(_params, "BINANCE_SYMBOLS", []))
    SCAN_INTERVAL   = int(getattr(_params,  "SCAN_INTERVAL_SEC", 180))
    SCAN_TIMEOUT    = int(getattr(_params,  "SCAN_TIMEOUT_SEC", 30))
    SCAN_WORKERS    = int(getattr(_params,  "SCAN_MAX_WORKERS", 8))
    LOG_MAX_LINES   = int(getattr(_params,  "SCAN_LOG_MAX_LINES", 500))
    LOG_MAX_BYTES   = int(getattr(_params,  "SCAN_LOG_MAX_BYTES", 10_485_760))
    LOG_BACKUPS     = int(getattr(_params,  "SCAN_LOG_BACKUPS", 5))
    TRIGGER_PCT     = float(getattr(_params, "TRIGGER_PCT", 0.05))
except ImportError as e:
    log.warning(f"params.py 載入失敗，使用預設值: {e}")
    ASTER_SYMBOLS   = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]
    BINANCE_SYMBOLS = []
    SCAN_INTERVAL   = 180
    SCAN_TIMEOUT    = 30
    SCAN_WORKERS    = 8
    LOG_MAX_LINES   = 500
    LOG_MAX_BYTES   = 10_485_760
    LOG_BACKUPS     = 5
    TRIGGER_PCT     = 0.05

_semaphore: Optional[asyncio.Semaphore] = None


# ════════════════════════════════════════════════════
# 原子文件寫入
# ════════════════════════════════════════════════════

def atomic_write(path: Path, content: str):
    """原子寫入：同目錄臨時文件 → os.replace()"""
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


# ════════════════════════════════════════════════════
# 心跳文件
# ════════════════════════════════════════════════════

HEARTBEAT_PATH = LOGS_DIR / "scanner_heartbeat.txt"


def write_heartbeat(status: str, extra: str = ""):
    """每輪更新心跳。/health 讀 mtime 判斷是否 hang。"""
    try:
        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{ts} {status} {extra}".strip() + "\n"
        atomic_write(HEARTBEAT_PATH, line)
    except Exception as e:
        log.warning(f"心跳寫入失敗: {e}")


# ════════════════════════════════════════════════════
# HTTP 抓取（同 light_scan / slash_cmd 一致）
# ════════════════════════════════════════════════════

def _fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """Fetch JSON from URL. Returns None on any error."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "OpenClaw-AsyncScanner/4.0"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ════════════════════════════════════════════════════
# 數據抓取
# ════════════════════════════════════════════════════

async def fetch_aster_symbol(symbol: str) -> Optional[dict]:
    """
    抓取單個 Aster 幣種。用 run_in_executor 包裝同步 HTTP。
    所有異常返回 None → 單幣種失敗唔影響其他。
    """
    try:
        loop = asyncio.get_running_loop()

        def _sync_fetch():
            data = _fetch_json(
                f"{ASTER_FAPI}/ticker/24hr?symbol={symbol}",
                timeout=SCAN_TIMEOUT,
            )
            if not data or "lastPrice" not in data:
                return None
            return {
                "symbol":   symbol,
                "platform": "aster",
                "price":    float(data.get("lastPrice", 0)),
                "change":   float(data.get("priceChangePercent", 0)),
                "high":     float(data.get("highPrice", 0)),
                "low":      float(data.get("lowPrice", 0)),
                "volume":   float(data.get("quoteVolume", 0)),
                "ts":       datetime.now(timezone.utc).isoformat(),
            }

        return await asyncio.wait_for(
            loop.run_in_executor(None, _sync_fetch),
            timeout=SCAN_TIMEOUT + 5,
        )

    except asyncio.TimeoutError:
        log.warning(f"⏱ {symbol}@aster 超時 ({SCAN_TIMEOUT}s)")
        return None
    except Exception as e:
        log.error(f"❌ {symbol}@aster {type(e).__name__}: {e}")
        return None


async def fetch_binance_symbol(symbol: str) -> Optional[dict]:
    """Binance 幣種（整合前靜默返回 None）。"""
    return None  # Binance 整合後替換


# ════════════════════════════════════════════════════
# 信號判斷（純數學，零 LLM cost）
# ════════════════════════════════════════════════════

def evaluate_signal(data: dict) -> dict:
    """24H 變化幅度 → 信號強度。"""
    change    = abs(float(data.get("change", 0)))
    threshold = TRIGGER_PCT * 100   # e.g. 0.05 → 5%

    if change >= threshold * 1.5:
        signal, reason = "STRONG", f"24H_{change:.1f}pct"
    elif change >= threshold:
        signal, reason = "LIGHT",  f"24H_{change:.1f}pct"
    else:
        signal, reason = "NO_SIGNAL", ""

    return {**data, "signal": signal, "reason": reason}


# ════════════════════════════════════════════════════
# 並行掃描引擎
# ════════════════════════════════════════════════════

async def scan_all_symbols() -> list[dict]:
    """並行掃描。雙重保護：內層 try/except + 外層 return_exceptions。"""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SCAN_WORKERS)

    tasks = (
        [fetch_aster_symbol(s)   for s in ASTER_SYMBOLS] +
        [fetch_binance_symbol(s) for s in BINANCE_SYMBOLS]
    )

    if not tasks:
        log.warning("無掃描任務，請檢查 params.py ASTER_SYMBOLS")
        return []

    async def limited(coro):
        async with _semaphore:
            return await coro

    raw = await asyncio.gather(
        *[limited(t) for t in tasks],
        return_exceptions=True,
    )

    results = []
    for r in raw:
        if isinstance(r, Exception):
            log.error(f"gather 捕捉到未預期異常: {type(r).__name__}: {r}")
        elif r is not None:
            results.append(evaluate_signal(r))

    return results


# ════════════════════════════════════════════════════
# 文件寫入
# ════════════════════════════════════════════════════

def _rotate_scan_log(log_path: Path):
    """SCAN_LOG.md rotation — 超過 LOG_MAX_LINES → 原子寫入保留最新一半。"""
    if not log_path.exists():
        return
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > LOG_MAX_LINES:
            keep    = lines[-(LOG_MAX_LINES // 2):]
            atomic_write(log_path, "".join(keep))
            log.info(f"🔄 SCAN_LOG rotation: {len(lines)} → {len(keep)} 行")
    except Exception as e:
        log.warning(f"SCAN_LOG rotation 失敗（唔影響掃描）: {e}")


def write_scan_results(results: list[dict], prev_cache: dict) -> dict:
    """
    寫入三個輸出文件。所有寫入獨立 try/except。
    results 為空 → 保留 prev_cache + stale 標記。
    """
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    now    = datetime.now()
    ts_str = now.strftime("%H:%M")

    # ── 1. SCAN_LOG（append + rotation）──────────
    log_path = SHARED_DIR / "SCAN_LOG.md"
    try:
        _rotate_scan_log(log_path)
        with open(log_path, "a", encoding="utf-8") as f:
            if not results:
                f.write(f"⚠️  {ts_str}  API暫時不可用，跳過本輪\n")
            else:
                for r in results:
                    tag  = "觸發" if r["signal"] != "NO_SIGNAL" else "深度"
                    line = (
                        f"{tag}  {ts_str}  "
                        f"{r['signal']} {r['symbol']}@{r['platform']} "
                        f"${float(r.get('price', 0)):.4f} "
                        f"CHG:{float(r.get('change', 0)):+.2f}%"
                        f"{' ' + r['reason'] if r.get('reason') else ''}\n"
                    )
                    f.write(line)
    except Exception as e:
        log.error(f"SCAN_LOG 寫入失敗: {e}")

    # ── 2. SIGNAL.md（原子覆寫）──────────────────
    try:
        if not results:
            content = (
                f"# SIGNAL {now.strftime('%Y-%m-%d %H:%M')}\n\n"
                f"⚠️ API 暫時不可用 — 顯示上次數據\n"
            )
        else:
            triggered = [r for r in results if r["signal"] != "NO_SIGNAL"]
            lines = [f"# SIGNAL {now.strftime('%Y-%m-%d %H:%M')}\n\n"]
            if triggered:
                lines.append(f"觸發：{len(triggered)} 個\n\n")
                for r in triggered:
                    lines.append(
                        f"- {r['signal']} {r['symbol']} "
                        f"${float(r.get('price', 0)):.4f} "
                        f"{r.get('reason', '')}\n"
                    )
            else:
                lines.append("NO_SIGNAL — 等待入場機會\n")
            content = "".join(lines)
        atomic_write(SHARED_DIR / "SIGNAL.md", content)
    except Exception as e:
        log.error(f"SIGNAL.md 寫入失敗: {e}")

    # ── 3. prices_cache.json（原子覆寫）──────────
    try:
        if results:
            new_cache = {
                r["symbol"]: {
                    "price":    r.get("price"),
                    "change":   r.get("change"),
                    "high":     r.get("high"),
                    "low":      r.get("low"),
                    "volume":   r.get("volume"),
                    "signal":   r.get("signal"),
                    "platform": r.get("platform"),
                    "ts":       r.get("ts"),
                    "stale":    False,
                }
                for r in results
            }
        else:
            new_cache = {
                sym: {**data, "stale": True}
                for sym, data in prev_cache.items()
            }
            log.warning("results 為空，prices_cache 保留上次數據（stale=True）")

        atomic_write(
            SHARED_DIR / "prices_cache.json",
            json.dumps(new_cache, ensure_ascii=False, indent=2),
        )
        return new_cache

    except Exception as e:
        log.error(f"prices_cache.json 寫入失敗: {e}")
        return prev_cache


# ════════════════════════════════════════════════════
# 主循環
# ════════════════════════════════════════════════════

async def scanner_loop():
    """持續掃描循環。頂層 try/except → log → sleep(30) → 重試。"""
    total = len(ASTER_SYMBOLS) + len(BINANCE_SYMBOLS)

    log.info("🔍 並行掃描器 v4 啟動")
    log.info(f"   幣種：{total}個 | 間隔：{SCAN_INTERVAL}s | 並發：{SCAN_WORKERS}")
    log.info(f"   Aster:   {ASTER_SYMBOLS}")
    log.info(f"   Binance: {BINANCE_SYMBOLS or ['(未整合)']}")

    write_heartbeat("starting")

    prev_cache: dict = {}

    while True:
        t0 = time.monotonic()

        try:
            results    = await scan_all_symbols()
            prev_cache = write_scan_results(results, prev_cache)

            elapsed   = time.monotonic() - t0
            ok_count  = len(results)
            triggered = sum(1 for r in results if r["signal"] != "NO_SIGNAL")
            stale     = " ⚠️stale" if not results else ""

            status = f"{ok_count}/{total}成功 觸發:{triggered} 耗時:{elapsed:.1f}s{stale}"
            log.info(f"✅ {status}")
            write_heartbeat("running", status)

        except Exception as e:
            elapsed = time.monotonic() - t0
            log.error(
                f"❌ 掃描輪次錯誤 ({elapsed:.1f}s): {type(e).__name__}: {e}",
                exc_info=True,
            )
            write_heartbeat("error", f"{type(e).__name__}: {str(e)[:80]}")
            await asyncio.sleep(30)
            continue

        wait = max(0.0, SCAN_INTERVAL - (time.monotonic() - t0))
        await asyncio.sleep(wait)


# ════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════

def main():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    rotating = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "scanner.log",
        maxBytes    = LOG_MAX_BYTES,
        backupCount = LOG_BACKUPS,
        encoding    = "utf-8",
    )

    logging.basicConfig(
        level    = logging.INFO,
        format   = "%(asctime)s [SCANNER] %(message)s",
        datefmt  = "%H:%M:%S",
        handlers = [
            logging.StreamHandler(),
            rotating,
        ],
    )

    log.info(f"日誌：{LOGS_DIR}/scanner.log（{LOG_MAX_BYTES // 1_048_576}MB rotate × {LOG_BACKUPS}）")

    try:
        asyncio.run(scanner_loop())
    except KeyboardInterrupt:
        log.info("掃描器已停止（Ctrl+C）")
        write_heartbeat("stopped", "KeyboardInterrupt")


if __name__ == "__main__":
    main()
