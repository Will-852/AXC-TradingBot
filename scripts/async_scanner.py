#!/usr/bin/env python3
"""
async_scanner.py — 並行多幣種掃描引擎
版本：v7 | 2026-03-10 | 9路輪轉版

v7 9路輪轉：
  9 個 exchange round-robin（Aster, Binance, HyperLiquid,
  Bybit, OKX, KuCoin, Gate.io, MEXC, Bitget）。
  每 20 秒掃一個 exchange，每個 exchange 每 180 秒才被 hit 一次。
  所有 public data — 無需認證。

保留：
  [R1] Bounded ThreadPoolExecutor — 防止 thread 洩漏
  [R4] 磁碟空間監控 — 500MB 告警，100MB critical
  [+]  Thread 數量監控 — 每10輪檢查
  [+]  Round counter — 長期運行追蹤
  [+]  atomic_write for SCAN_LOG rotation
  [+]  Empty results preserves prev_cache (stale=True)

已知限制（接受）：
  - KuCoin 用 spot ticker（futures 無 bulk endpoint）
  - HyperLiquid 無 24h high/low
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

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "config"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from write_activity import write_activity
from public_feeds import fetch_exchange_tickers
from public_feeds import shutdown as feeds_shutdown

log = logging.getLogger("scanner")

# ── Params（hot-reload 每 10 輪）──────────────────
PARAMS_PATH = str(BASE_DIR / "config" / "params.py")

# Defaults (used if params.py missing)
SCAN_INTERVAL   = 20
SCAN_TIMEOUT    = 30
SCAN_WORKERS    = 8
LOG_MAX_LINES   = 500
LOG_MAX_BYTES   = 10_485_760
LOG_BACKUPS     = 5
_FALLBACK_TRIGGER = 0.05
_CACHED_TRIGGER   = None  # set by reload_params() via profile loader
_EXCHANGE_ROTATION = [
    "aster", "binance", "hyperliquid",
    "bybit", "okx", "kucoin", "gate", "mexc", "bitget",
]
_ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "XAGUSDT", "SOLUSDT"]


def reload_params():
    """Hot-reload params.py via importlib（同 dashboard 同一模式）。
    更新所有 module globals，包括 9 路輪轉設定。"""
    global SCAN_INTERVAL, SCAN_TIMEOUT
    global SCAN_WORKERS, LOG_MAX_LINES, LOG_MAX_BYTES, LOG_BACKUPS
    global _FALLBACK_TRIGGER, _CACHED_TRIGGER
    global _EXCHANGE_ROTATION, _ALL_SYMBOLS

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("params_scanner", PARAMS_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        SCAN_INTERVAL     = int(getattr(mod,  "SCAN_INTERVAL_SEC", 20))
        SCAN_TIMEOUT      = int(getattr(mod,  "SCAN_TIMEOUT_SEC", 30))
        SCAN_WORKERS      = int(getattr(mod,  "SCAN_MAX_WORKERS", 8))
        LOG_MAX_LINES     = int(getattr(mod,  "SCAN_LOG_MAX_LINES", 500))
        LOG_MAX_BYTES     = int(getattr(mod,  "SCAN_LOG_MAX_BYTES", 10_485_760))
        LOG_BACKUPS       = int(getattr(mod,  "SCAN_LOG_BACKUPS", 5))
        _FALLBACK_TRIGGER = float(getattr(mod, "TRIGGER_PCT", 0.05))

        # Profile loader → get trigger_pct
        try:
            from config.profiles.loader import load_profile
            _p = load_profile()
            _CACHED_TRIGGER = _p.get("trigger_pct", _FALLBACK_TRIGGER)
        except Exception:
            _CACHED_TRIGGER = _FALLBACK_TRIGGER

        # 9 路輪轉
        _EXCHANGE_ROTATION = list(getattr(mod, "EXCHANGE_ROTATION", _EXCHANGE_ROTATION))

        # 所有幣種 = union of all exchange symbol lists
        aster_syms   = list(getattr(mod, "ASTER_SYMBOLS", []))
        binance_syms = list(getattr(mod, "BINANCE_SYMBOLS", []))
        hl_syms      = list(getattr(mod, "HL_SYMBOLS", []))
        _ALL_SYMBOLS = list(dict.fromkeys(aster_syms + binance_syms + hl_syms))

        return True
    except Exception as e:
        log.warning(f"params.py hot-reload 失敗，保留現有值: {e}")
        return False


# Initial load
reload_params()


def get_trigger_pct() -> float:
    """讀當前 profile 觸發門檻。由 reload_params() 更新。"""
    if _CACHED_TRIGGER is not None:
        return _CACHED_TRIGGER
    return _FALLBACK_TRIGGER

# Fix R1: Bounded ThreadPoolExecutor — thread hang 最多佔 SCAN_WORKERS 個 slot
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=SCAN_WORKERS,
    thread_name_prefix="scanner",
)


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
    9 路輪轉掃描。每輪選一個 exchange，用 public_feeds 取得所有 ticker，
    然後只保留 _ALL_SYMBOLS 內嘅幣種。
    每個 exchange 每 N 輪才被 hit 一次（N = len(EXCHANGE_ROTATION)）。
    """
    if not _EXCHANGE_ROTATION:
        log.warning("EXCHANGE_ROTATION 為空")
        return []

    exchange = _EXCHANGE_ROTATION[round_count % len(_EXCHANGE_ROTATION)]
    tickers = await fetch_exchange_tickers(exchange)

    if not tickers:
        log.warning(f"{exchange} 返回空結果，本輪跳過")
        return []

    now_ts = datetime.now(timezone.utc).isoformat()
    results = []
    for sym in _ALL_SYMBOLS:
        if sym in tickers:
            t = tickers[sym]
            results.append(evaluate_signal({
                "symbol":   sym,
                "platform": exchange,
                "price":    t["price"],
                "change":   t["change"],
                "high":     t["high"],
                "low":      t["low"],
                "volume":   t["volume"],
                "ts":       now_ts,
            }))

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

    # ── 3. prices_cache.json（merge 模式 — 9路輪轉每輪只掃部分幣種）──
    try:
        new_cache = dict(prev_cache)  # 保留所有舊數據

        if results:
            # Merge：本輪掃到嘅幣種更新，其他保持不變
            for r in results:
                new_cache[r["symbol"]] = {
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
        else:
            # 全部標 stale
            for sym in new_cache:
                new_cache[sym] = {**new_cache[sym], "stale": True}
            log.warning("results 為空，prices_cache 全部標 stale")

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

    log.info("並行掃描器 v7 啟動（9路輪轉 + hot-reload）")
    log.info(f"   幣種：{total}個 | 間隔：{SCAN_INTERVAL}s | 交易所：{len(_EXCHANGE_ROTATION)}個")
    log.info(f"   輪轉：{_EXCHANGE_ROTATION}")
    log.info(f"   每個交易所 hit 頻率：每 {SCAN_INTERVAL * len(_EXCHANGE_ROTATION)}s")
    log.info(f"   Hot-reload: 每 10 輪自動重讀 params.py")

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
                    log.info(f"params hot-reload: {total} symbols, {len(_EXCHANGE_ROTATION)} exchanges")
                check_disk_space()
                active = threading.active_count()
                log.info(f"R{round_count} threads:{active}")
                if active > SCAN_WORKERS * 3:
                    log.warning(f"threads high: {active} (limit {SCAN_WORKERS * 3})")
                    write_heartbeat("thread_warning", f"threads={active}")

            src = _EXCHANGE_ROTATION[round_count % len(_EXCHANGE_ROTATION)]
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
        feeds_shutdown()
        log.info("掃描器已停止")
        write_heartbeat("stopped")


if __name__ == "__main__":
    main()
