"""
_log_trim.py — 時間制 log 截斷（保留最近 N 小時）

設計決定：
- 支援多種 timestamp 格式（YYYY-MM-DD HH:MM:SS / HH:MM:SS / [HH:MM:SS]）
- 冇 timestamp 嘅行繼承上一行嘅時間（stack trace / multi-line output）
- 完全搵唔到 timestamp → fallback 保留尾 2000 行
- scanner.log 有自己嘅 RotatingFileHandler，唔處理
- 原子寫入：寫 .tmp → rename

Usage: python3 _log_trim.py <log_dir> <keep_hours>
"""

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# scanner.log 已有 RotatingFileHandler
SKIP_FILES = {"scanner.log"}

# 狀態文件（唔係 log，唔好清）
STATE_FILES = {"scanner_heartbeat.txt", "paper_gate_start.txt"}

# Fallback：完全冇 timestamp 時保留嘅行數
FALLBACK_LINES = 2000

# ── Timestamp 解析 ──

# 2026-03-14 01:58:56,144 or 2026-03-14 01:58:56
_RE_FULL = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")

# 00:05:42 [SCANNER] or 01:48:27 [NEWS_BOT] or [06:56:52]
_RE_TIME = re.compile(r"\[?(\d{2}:\d{2}:\d{2})\]?")

# "timestamp": "2026-03-03 03:40"
_RE_JSON_TS = re.compile(r'"timestamp":\s*"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})"')


def parse_timestamp(line: str, ref_date: datetime) -> datetime | None:
    """嘗試從 log 行提取 timestamp。ref_date 用於 HH:MM:SS-only 格式。"""

    # Full: YYYY-MM-DD HH:MM:SS
    m = _RE_FULL.search(line)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # JSON: "timestamp": "YYYY-MM-DD HH:MM"
    m = _RE_JSON_TS.search(line)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    # Time-only: HH:MM:SS → 用 file 嘅修改日期推斷日期
    m = _RE_TIME.search(line)
    if m:
        try:
            t = datetime.strptime(m.group(1), "%H:%M:%S").time()
            return datetime.combine(ref_date.date(), t)
        except ValueError:
            pass

    return None


def trim_file(filepath: Path, cutoff: datetime, now: datetime) -> tuple[int, int]:
    """截斷單個 log 文件。Returns (original_lines, kept_lines)。"""

    try:
        lines = filepath.read_text(errors="replace").splitlines(keepends=True)
    except OSError:
        return 0, 0

    if not lines:
        return 0, 0

    original = len(lines)

    # 用文件修改時間做 HH:MM:SS 格式嘅參考日期
    try:
        mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
    except OSError:
        mtime = now

    # 第一輪：搵每行嘅 timestamp
    timestamps: list[datetime | None] = []
    found_any = False
    for line in lines:
        ts = parse_timestamp(line, mtime)
        if ts is not None:
            found_any = True
        timestamps.append(ts)

    # 完全冇 timestamp → fallback 保留尾 N 行
    if not found_any:
        if original <= FALLBACK_LINES:
            return original, original
        keep = lines[-FALLBACK_LINES:]
        _atomic_write(filepath, keep)
        return original, len(keep)

    # 第二輪：冇 timestamp 嘅行繼承上一行（stack trace / continuation）
    last_ts = None
    for i, ts in enumerate(timestamps):
        if ts is not None:
            last_ts = ts
        else:
            timestamps[i] = last_ts

    # 搵第一行 >= cutoff 嘅位置
    start_idx = None
    for i, ts in enumerate(timestamps):
        if ts is not None and ts >= cutoff:
            start_idx = i
            break

    if start_idx is None:
        # 全部都舊過 cutoff → 只保留最後 200 行（至少有少少 context）
        keep = lines[-200:] if original > 200 else lines
        if len(keep) < original:
            _atomic_write(filepath, keep)
        return original, len(keep)

    if start_idx == 0:
        # 全部都喺 cutoff 內，唔使改
        return original, original

    keep = lines[start_idx:]
    _atomic_write(filepath, keep)
    return original, len(keep)


def _atomic_write(filepath: Path, lines: list[str]):
    """原子寫入：寫 .tmp → rename。"""
    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    try:
        tmp.write_text("".join(lines))
        tmp.rename(filepath)
    except OSError as e:
        print(f"  [error] {filepath.name}: {e}")
        tmp.unlink(missing_ok=True)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <log_dir> <keep_hours>")
        sys.exit(1)

    log_dir = Path(sys.argv[1])
    keep_hours = float(sys.argv[2])
    now = datetime.now()
    cutoff = now - timedelta(hours=keep_hours)

    print(f"[trim] cutoff: {cutoff:%Y-%m-%d %H:%M} ({keep_hours}h ago)")

    total_before = 0
    total_after = 0
    trimmed_count = 0

    for f in sorted(log_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name in SKIP_FILES or f.name in STATE_FILES:
            continue
        if not (f.suffix in (".log", ".jsonl")):
            continue

        before, after = trim_file(f, cutoff, now)
        total_before += before
        total_after += after

        if after < before:
            trimmed_count += 1
            pct = (1 - after / before) * 100 if before else 0
            print(f"  {f.name}: {before:,} → {after:,} lines (-{pct:.0f}%)")

    print(f"[trim] {trimmed_count} files trimmed, total {total_before:,} → {total_after:,} lines")


if __name__ == "__main__":
    main()
