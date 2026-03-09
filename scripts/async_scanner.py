#!/usr/bin/env python3
"""
async_scanner.py — 並行多幣種掃描引擎
版本：v6 | 2026-03-07 | 梅花間竹版

v6 梅花間竹：
  共享幣種（Aster+Binance 都有）單雙輪交替 exchange，
  每個 exchange 只承受約一半 request rate，防 429。
  獨佔幣種每輪都掃，唔受影響。

v5 保留：
  [R1] Bounded ThreadPoolExecutor — 防止 thread 洩漏（第8日爆滿問題）
  [R4] 磁碟空間監控 — 500MB 告警，100MB critical
  [+]  Thread 數量監控 — 每10輪檢查
  [+]  Round counter — 長期運行追蹤

v4 保留：
  [1] LOGS_DIR.mkdir() before FileHandler
  [2] RotatingFileHandler (10MB x 5)
  [3] atomic_write for SCAN_LOG rotation
  [4] Empty results preserves prev_cache (stale=True)

已知限制（接受）：
  - 用直接 HTTP 而非 AsterClient（同 light_scan 一致）
  - SCAN_LOG rotation 讀入記憶體（500行 ≈ 50KB，可接受）
"""

import asyncio
import concurrent.futures
import json
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── 路徑設定 ─────────────────────────────────────
BASE_DIR   = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
SHARED_DIR = BASE_DIR / "shared"
LOGS_DIR   = BASE_DIR / "logs"

# 修正1：mkdir 必須在任何文件操作之前
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SHARED_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR / "config"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from write_activity import write_activity

log = logging.getLogger("scanner")

# ── API Endpoints ────────────────────────────────
ASTER_FAPI = "https://fapi.asterdex.com/fapi/v1"
BINANCE_FAPI = "https://fapi.binance.com/fapi/v1"

# ── Params（hot-reload 每 10 輪）──────────────────
PARAMS_PATH = str(BASE_DIR / "config" / "params.py")

# Defaults (used if params.py missing)
ASTER_SYMBOLS   = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]
BINANCE_SYMBOLS = []
SCAN_INTERVAL   = 180
SCAN_TIMEOUT    = 30
SCAN_WORKERS    = 8
LOG_MAX_LINES   = 500
LOG_MAX_BYTES   = 10_485_760
LOG_BACKUPS     = 5
_FALLBACK_TRIGGER = 0.05
_PROFILES         = {}
_ACTIVE           = None


def reload_params():
    """Hot-reload params.py via importlib（同 dashboard 同一模式）。
    更新所有 module globals，包括梅花間竹用嘅 symbol sets。"""
    global ASTER_SYMBOLS, BINANCE_SYMBOLS, SCAN_INTERVAL, SCAN_TIMEOUT
    global SCAN_WORKERS, LOG_MAX_LINES, LOG_MAX_BYTES, LOG_BACKUPS
    global _FALLBACK_TRIGGER, _PROFILES, _ACTIVE
    global _aster_set, _binance_set, _ALL_SYMBOLS

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("params_scanner", PARAMS_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ASTER_SYMBOLS     = list(getattr(mod, "ASTER_SYMBOLS",
                                  ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT"]))
        BINANCE_SYMBOLS   = list(getattr(mod, "BINANCE_SYMBOLS", []))
        SCAN_INTERVAL     = int(getattr(mod,  "SCAN_INTERVAL_SEC", 180))
        SCAN_TIMEOUT      = int(getattr(mod,  "SCAN_TIMEOUT_SEC", 30))
        SCAN_WORKERS      = int(getattr(mod,  "SCAN_MAX_WORKERS", 8))
        LOG_MAX_LINES     = int(getattr(mod,  "SCAN_LOG_MAX_LINES", 500))
        LOG_MAX_BYTES     = int(getattr(mod,  "SCAN_LOG_MAX_BYTES", 10_485_760))
        LOG_BACKUPS       = int(getattr(mod,  "SCAN_LOG_BACKUPS", 5))
        _FALLBACK_TRIGGER = float(getattr(mod, "TRIGGER_PCT", 0.05))
        _PROFILES         = getattr(mod, "TRADING_PROFILES", {})
        _ACTIVE           = getattr(mod, "ACTIVE_PROFILE", None)

        # Rebuild alternation sets
        _aster_set   = set(ASTER_SYMBOLS)
        _binance_set = set(BINANCE_SYMBOLS)
        _ALL_SYMBOLS = list(dict.fromkeys(ASTER_SYMBOLS + BINANCE_SYMBOLS))

        return True
    except Exception as e:
        log.warning(f"params.py hot-reload 失敗，保留現有值: {e}")
        return False


# Initial load
reload_params()


def get_trigger_pct() -> float:
    """讀當前 profile 觸發門檻。由 reload_params() 更新。"""
    profile = _PROFILES.get(_ACTIVE, {})
    return profile.get("trigger_pct", _FALLBACK_TRIGGER)

# Fix R1: Bounded ThreadPoolExecutor — thread hang 最多佔 SCAN_WORKERS 個 slot
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=SCAN_WORKERS,
    thread_name_prefix="scanner",
)

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

        # Fix R1: 用 bounded _executor，唔用預設 executor
        fut = loop.run_in_executor(_executor, _sync_fetch)
        return await asyncio.wait_for(fut, timeout=SCAN_TIMEOUT)

    except asyncio.TimeoutError:
        log.warning(f"⏱ {symbol}@aster 超時 ({SCAN_TIMEOUT}s)")
        return None
    except Exception as e:
        log.error(f"❌ {symbol}@aster {type(e).__name__}: {e}")
        return None


async def fetch_binance_symbol(symbol: str) -> Optional[dict]:
    """抓取單個 Binance Futures 幣種。同 Aster 一致模式。"""
    try:
        loop = asyncio.get_running_loop()

        def _sync_fetch():
            data = _fetch_json(
                f"{BINANCE_FAPI}/ticker/24hr?symbol={symbol}",
                timeout=SCAN_TIMEOUT,
            )
            if not data or "lastPrice" not in data:
                return None
            return {
                "symbol":   symbol,
                "platform": "binance",
                "price":    float(data.get("lastPrice", 0)),
                "change":   float(data.get("priceChangePercent", 0)),
                "high":     float(data.get("highPrice", 0)),
                "low":      float(data.get("lowPrice", 0)),
                "volume":   float(data.get("quoteVolume", 0)),
                "ts":       datetime.now(timezone.utc).isoformat(),
            }

        fut = loop.run_in_executor(_executor, _sync_fetch)
        return await asyncio.wait_for(fut, timeout=SCAN_TIMEOUT)

    except asyncio.TimeoutError:
        log.warning(f"⏱ {symbol}@binance 超時 ({SCAN_TIMEOUT}s)")
        return None
    except Exception as e:
        log.error(f"❌ {symbol}@binance {type(e).__name__}: {e}")
        return None


# ════════════════════════════════════════════════════
# 信號判斷（純數學，零 LLM cost）
# ════════════════════════════════════════════════════

def evaluate_signal(data: dict) -> dict:
    """24H 變化幅度 → 信號強度。"""
    change    = abs(float(data.get("change", 0)))
    threshold = get_trigger_pct() * 100   # e.g. 0.025 → 2.5%

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

async def scan_all_symbols(round_count: int = 0) -> list[dict]:
    """
    並行掃描 — 梅花間竹策略。
    共享幣種（兩邊都有）：單數輪用 Aster，雙數輪用 Binance。
    獨佔幣種（只有一邊）：每輪都掃。
    效果：每個 exchange 只承受約一半 request rate，防 429。
    """
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SCAN_WORKERS)

    use_aster = (round_count % 2 == 1)  # 單數=Aster, 雙數=Binance

    tasks = []
    for sym in _ALL_SYMBOLS:
        on_both = sym in _aster_set and sym in _binance_set
        if on_both:
            if use_aster:
                tasks.append(fetch_aster_symbol(sym))
            else:
                tasks.append(fetch_binance_symbol(sym))
        elif sym in _aster_set:
            tasks.append(fetch_aster_symbol(sym))
        else:
            tasks.append(fetch_binance_symbol(sym))

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

def check_disk_space() -> bool:
    """Fix R4: 磁碟空間監控。<500MB 告警，<100MB critical。"""
    try:
        usage = shutil.disk_usage(BASE_DIR)
        free_mb = usage.free // (1024 * 1024)
        if free_mb < 100:
            log.critical(f"🚨 磁碟空間極危：{free_mb}MB 剩餘！")
            write_heartbeat("disk_critical", f"free={free_mb}MB")
            return False
        elif free_mb < 500:
            log.warning(f"⚠️ 磁碟空間低：{free_mb}MB 剩餘")
        return True
    except Exception as e:
        log.warning(f"磁碟檢查失敗: {e}")
        return True


async def scanner_loop():
    """持續掃描循環。頂層 try/except → log → sleep(30) → 重試。"""
    total = len(_ALL_SYMBOLS)

    log.info("並行掃描器 v6 啟動（梅花間竹 + hot-reload）")
    log.info(f"   幣種：{total}個 | 間隔：{SCAN_INTERVAL}s | 並發：{SCAN_WORKERS}")
    log.info(f"   Aster:   {ASTER_SYMBOLS}")
    log.info(f"   Binance: {BINANCE_SYMBOLS or ['(未整合)']}")
    log.info(f"   共享：{sorted(_aster_set & _binance_set) or '(無)'} — 單雙輪交替")
    log.info(f"   Hot-reload: 每 10 輪自動重讀 params.py")
    log.info(f"   Executor: ThreadPool(max={SCAN_WORKERS})")

    write_heartbeat("starting")

    prev_cache: dict = {}
    round_count = 0

    while True:
        round_count += 1
        t0 = time.monotonic()

        # 每30輪寫活動日誌心跳
        if round_count % 30 == 0:
            try:
                write_activity("heartbeat", f"Scanner 第 {round_count} 輪")
            except Exception:
                pass

        try:
            # 每10輪做維護 + hot-reload params
            if round_count % 10 == 0:
                if reload_params():
                    total = len(_ALL_SYMBOLS)
                    log.info(f"params hot-reload: {len(ASTER_SYMBOLS)} Aster + {len(BINANCE_SYMBOLS)} Binance, 共享 {len(_aster_set & _binance_set)}")
                check_disk_space()
                active = threading.active_count()
                log.info(f"R{round_count} threads:{active}")
                if active > SCAN_WORKERS * 3:
                    log.warning(f"threads high: {active} (limit {SCAN_WORKERS * 3})")
                    write_heartbeat("thread_warning", f"threads={active}")

            src = "Aster" if (round_count % 2 == 1) else "Binance"
            results    = await scan_all_symbols(round_count)
            prev_cache = write_scan_results(results, prev_cache)

            elapsed   = time.monotonic() - t0
            ok_count  = len(results)
            triggered = sum(1 for r in results if r["signal"] != "NO_SIGNAL")
            stale     = " stale" if not results else ""

            status = f"{ok_count}/{len(_ALL_SYMBOLS)}成功 觸發:{triggered} 耗時:{elapsed:.1f}s R{round_count} [{src}]{stale}"
            log.info(f"✅ {status}")
            write_heartbeat("running", status)

        except Exception as e:
            elapsed = time.monotonic() - t0
            log.error(
                f"❌ 掃描輪次錯誤 R{round_count} ({elapsed:.1f}s): {type(e).__name__}: {e}",
                exc_info=True,
            )
            write_heartbeat("error", f"R{round_count} {type(e).__name__}: {str(e)[:80]}")
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
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        _executor.shutdown(wait=False)
        log.info("掃描器已停止")
        write_heartbeat("stopped")


if __name__ == "__main__":
    main()
