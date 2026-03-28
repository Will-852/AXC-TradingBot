#!/usr/bin/env python3
"""
gen_architecture.py — 自動架構文件生成器

設計決定：
- 純 stdlib，零外部依賴（同項目其他 script 一致）
- 每個 section 獨立 try/except — 一個壞唔影響其他
- Regex 解析 import（唔用 ast.parse — 更快、容錯好）
- Git log per-directory（~17 次，<2s）
- atomic_write（tempfile + os.replace）

輸出：docs/ARCHITECTURE_AUTO.md
用法：python3 scripts/gen_architecture.py
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── 路徑設定 ─────────────────────────────────────
AXC_HOME = Path(os.environ.get("AXC_HOME", str(Path.home() / "projects" / "axc-trading")))
OUTPUT_PATH = AXC_HOME / "docs" / "ARCHITECTURE_AUTO.md"
SCRIPT_NAME = "gen_architecture.py"

# 兩大系統（邊界偵測用）
SYSTEM_TRADER = AXC_HOME / "scripts" / "trader_cycle"
SYSTEM_POLY = AXC_HOME / "polymarket"
SHARED_INFRA_PKG = "shared_infra"

# 掃描嘅 top-level 目錄（順序 = 輸出順序）
TOP_DIRS = [
    "agents", "ai", "analysis", "backtest", "canvas", "cli", "config",
    "data", "docs", "memory", "polymarket", "scripts", "shared", "tests",
]

# 閾值
LARGE_FILE_WARN = 300      # >300 行列出
LARGE_FILE_DANGER = 500    # >500 行標 ⚠️
STALE_DAYS = 30            # >30 日標 ⚠️

# LaunchAgent
LAUNCH_AGENT_DIR = Path.home() / "Library" / "LaunchAgents"
LAUNCH_AGENT_PREFIX = "ai.openclaw."

# Import 解析
_RE_IMPORT = re.compile(r"^(?:from|import)\s+([\w.]+)")

# Timezone
try:
    from zoneinfo import ZoneInfo
    HKT = ZoneInfo("Asia/Hong_Kong")
except ImportError:
    HKT = timezone(timedelta(hours=8))

# ── Logging ──────────────────────────────────────
log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════

def _git_last_commit_date(path: Path) -> str:
    """拎 path 最後一次 git commit 日期。失敗返回 'N/A'。"""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ci", "--", str(path)],
            capture_output=True, text=True, cwd=AXC_HOME, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:10]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return "N/A"


def _scan_py_files(directory: Path, large_threshold: int = LARGE_FILE_WARN
                    ) -> tuple[int, int, list[tuple[Path, int]]]:
    """Single-pass scan: count .py files, total lines, and find large files.

    Returns (file_count, line_count, large_files) where large_files is
    sorted by line count descending.
    """
    file_count = 0
    line_count = 0
    large: list[tuple[Path, int]] = []
    if not directory.exists():
        return file_count, line_count, large
    for py in directory.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        file_count += 1
        try:
            n = len(py.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        line_count += n
        if n >= large_threshold:
            large.append((py, n))
    large.sort(key=lambda x: x[1], reverse=True)
    return file_count, line_count, large


def _extract_imports(py_file: Path) -> list[str]:
    """從 .py 文件提取 top-level import 嘅 package 名。"""
    imports: list[str] = []
    try:
        for line in py_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_IMPORT.match(line.strip())
            if m:
                imports.append(m.group(1))
    except OSError:
        pass
    return imports


def _build_import_graph(root: Path) -> dict[str, set[str]]:
    """掃描 root 下所有 .py，建立 {relative_path: {imported_packages}} 映射。"""
    graph: dict[str, set[str]] = {}
    if not root.exists():
        return graph
    for py in root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = str(py.relative_to(AXC_HOME))
        imports = _extract_imports(py)
        if imports:
            graph[rel] = set(imports)
    return graph


def _detect_circular_imports(graph: dict[str, set[str]]) -> list[tuple[str, str]]:
    """偵測直接循環引用（A imports B 且 B imports A）。Returns pair list。"""
    # Build reverse index: dotted module name → file path
    # e.g. "polymarket.mm.constants" could be from polymarket/mm/constants.py
    stem_to_file: dict[str, str] = {}
    for filepath in graph:
        # polymarket/mm/constants.py → "constants"
        stem_to_file[Path(filepath).stem] = filepath

    cycles: list[tuple[str, str]] = []
    for file_a, imports_a in graph.items():
        mod_a = Path(file_a).stem
        for imp in imports_a:
            # Match import to a file: check if the last segment matches a known stem
            imp_stem = imp.rsplit(".", 1)[-1]
            file_b = stem_to_file.get(imp_stem)
            if file_b is None or file_b == file_a:
                continue
            # Check reverse: does file_b import file_a's module?
            imports_b = graph.get(file_b, set())
            if any(imp_b.rsplit(".", 1)[-1] == mod_a for imp_b in imports_b):
                pair = tuple(sorted((file_a, file_b)))
                if pair not in cycles:
                    cycles.append(pair)

    return cycles


def _generate_header() -> list[str]:
    """自動生成嘅文件頭。"""
    now = datetime.now(HKT)
    return [
        "# AXC Trading — 自動生成架構清單",
        f"> Auto-generated by `{SCRIPT_NAME}` on {now:%Y-%m-%d %H:%M} HKT",
        "> **手動編輯無用** — 改代碼後重跑 `python3 scripts/gen_architecture.py`",
        f"> 判斷/危險位/設計決定 → 見 `docs/ARCHITECTURE_NOTES.md`",
        "",
    ]


# ════════════════════════════════════════════════════
# Import graph cache (built once, shared across sections)
# ════════════════════════════════════════════════════

_graph_cache: dict[str, dict[str, set[str]]] = {}


def _get_import_graph(root: Path) -> dict[str, set[str]]:
    """Cached wrapper — builds graph once per root path."""
    key = str(root)
    if key not in _graph_cache:
        _graph_cache[key] = _build_import_graph(root)
    return _graph_cache[key]


def _get_subgraph(parent_graph: dict[str, set[str]], subdir: str) -> dict[str, set[str]]:
    """Extract subset of a cached graph matching a subdirectory prefix."""
    return {k: v for k, v in parent_graph.items() if k.startswith(subdir)}


# ════════════════════════════════════════════════════
# Sections
# ════════════════════════════════════════════════════

def section_folder_manifest() -> list[str]:
    """Section 1: 每個 top-level dir 嘅文件統計。"""
    lines = ["## 1. Folder Manifest", ""]
    lines.append("| Directory | .py Files | Lines | Last Commit |")
    lines.append("|-----------|-----------|-------|-------------|")

    all_large: list[tuple[Path, int]] = []
    for dirname in TOP_DIRS:
        d = AXC_HOME / dirname
        if not d.exists():
            continue
        fcount, lcount, large = _scan_py_files(d)
        commit_date = _git_last_commit_date(d)
        lines.append(f"| `{dirname}/` | {fcount} | {lcount:,} | {commit_date} |")
        all_large.extend(large)

    lines.extend(["", "### Large Files (>300 lines)", ""])
    all_large.sort(key=lambda x: x[1], reverse=True)
    if all_large:
        for fpath, n in all_large:
            rel = fpath.relative_to(AXC_HOME)
            marker = " ⚠️" if n >= LARGE_FILE_DANGER else ""
            lines.append(f"- `{rel}` ({n:,} lines){marker}")
    else:
        lines.append("_None found._")

    lines.append("")
    return lines


def section_boundary_map() -> list[str]:
    """Section 2: 系統邊界 — trader_cycle ↔ polymarket 交叉引用。"""
    lines = ["## 2. System Boundary Map", ""]

    # Cross-import detection
    violations: list[str] = []
    expected_cross: list[str] = []

    # polymarket/ → trader_cycle?
    poly_graph = _get_import_graph(SYSTEM_POLY)
    for filepath, imports in poly_graph.items():
        for imp in imports:
            if imp.startswith("trader_cycle") or imp.startswith("scripts.trader_cycle"):
                violations.append(f"🔴 `{filepath}` imports `{imp}`")

    # trader_cycle/ → polymarket?
    tc_graph = _get_import_graph(SYSTEM_TRADER)
    for filepath, imports in tc_graph.items():
        for imp in imports:
            if imp.startswith("polymarket"):
                violations.append(f"🔴 `{filepath}` imports `{imp}`")

    # dashboard → polymarket (expected)
    dash_dir = AXC_HOME / "scripts" / "dashboard_ng"
    if dash_dir.exists():
        dash_graph = _get_import_graph(dash_dir)
        for filepath, imports in dash_graph.items():
            for imp in imports:
                if imp.startswith("polymarket"):
                    expected_cross.append(f"- `{filepath}` → `{imp}` (dashboard, expected)")

    lines.append("### Cross-System Imports")
    lines.append("")
    if violations:
        for v in violations:
            lines.append(v)
    else:
        lines.append("✅ **Zero cross-imports between trader_cycle/ and polymarket/**")
    lines.append("")

    if expected_cross:
        lines.append("### Expected Cross-Boundary (Dashboard)")
        lines.append("")
        lines.extend(expected_cross)
        lines.append("")

    # shared_infra usage
    lines.append("### shared_infra Usage")
    lines.append("")
    for label, graph in [("trader_cycle", tc_graph), ("polymarket", poly_graph)]:
        users = set()
        for filepath, imports in graph.items():
            for imp in imports:
                if SHARED_INFRA_PKG in imp:
                    users.add(filepath)
        if users:
            lines.append(f"**{label}/** ({len(users)} files import shared_infra)")
            for u in sorted(users)[:10]:
                lines.append(f"  - `{u}`")
            if len(users) > 10:
                lines.append(f"  - _...and {len(users) - 10} more_")
            lines.append("")

    lines.append("")
    return lines


def section_dependency_graph() -> list[str]:
    """Section 3: polymarket/mm/ 同 trader_cycle/ 嘅 import 層級。"""
    lines = ["## 3. Dependency Graph", ""]

    for label, root, subdir_prefix in [
        ("polymarket/mm", SYSTEM_POLY, "polymarket/mm/"),
        ("scripts/trader_cycle", SYSTEM_TRADER, "scripts/trader_cycle/"),
    ]:
        full_graph = _get_import_graph(root)
        graph = _get_subgraph(full_graph, subdir_prefix) if label == "polymarket/mm" else full_graph
        if not graph:
            continue
        lines.append(f"### {label}/")
        lines.append("")

        # 只顯示內部 import（filter 出 stdlib 同 third-party）
        internal_prefixes = ("polymarket", "trader_cycle", "shared_infra", "config", "scripts")
        for filepath in sorted(graph):
            internal = sorted(
                imp for imp in graph[filepath]
                if any(imp.startswith(p) for p in internal_prefixes)
            )
            if internal:
                rel = Path(filepath).name
                lines.append(f"- `{rel}` → {', '.join(f'`{i}`' for i in internal)}")

        # Circular imports
        cycles = _detect_circular_imports(graph)
        if cycles:
            lines.append("")
            lines.append("**🔴 Circular Imports Detected:**")
            for a, b in cycles:
                lines.append(f"  - `{a}` ↔ `{b}`")
        else:
            lines.append("")
            lines.append("_No circular imports detected._ ✅")

        lines.append("")

    return lines


def section_launchagent_services() -> list[str]:
    """Section 4: LaunchAgent 服務清單 + 運行狀態。"""
    lines = ["## 4. LaunchAgent Services", ""]

    if platform.system() != "Darwin":
        lines.append("_Skipped: not macOS._")
        lines.append("")
        return lines

    if not LAUNCH_AGENT_DIR.exists():
        lines.append("_LaunchAgent directory not found._")
        lines.append("")
        return lines

    plists = sorted(
        p for p in LAUNCH_AGENT_DIR.iterdir()
        if p.name.startswith(LAUNCH_AGENT_PREFIX) and p.suffix == ".plist"
    )
    # 加埋 com.openclaw.* 嘅
    plists.extend(sorted(
        p for p in LAUNCH_AGENT_DIR.iterdir()
        if p.name.startswith("com.openclaw.") and p.suffix == ".plist"
    ))

    if not plists:
        lines.append("_No ai.openclaw.*.plist found._")
        lines.append("")
        return lines

    # 攞 launchctl list 輸出
    loaded_services: set[str] = set()
    try:
        result = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            for row in result.stdout.splitlines():
                parts = row.split("\t")
                if len(parts) >= 3:
                    loaded_services.add(parts[2])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    lines.append(f"| Service | Status |")
    lines.append(f"|---------|--------|")
    for p in plists:
        name = p.stem
        status = "🟢 loaded" if name in loaded_services else "⚪ not loaded"
        lines.append(f"| `{name}` | {status} |")

    lines.extend(["", f"**Total: {len(plists)} services**", ""])
    return lines


def section_test_coverage() -> list[str]:
    """Section 5: 測試文件統計。"""
    lines = ["## 5. Test Coverage", ""]

    test_dirs = [
        ("tests/", AXC_HOME / "tests"),
        ("polymarket/tests/", SYSTEM_POLY / "tests"),
    ]

    total_tests = 0
    for label, tdir in test_dirs:
        if not tdir.exists():
            continue
        test_files = sorted(tdir.glob("test_*.py"))
        total_tests += len(test_files)
        lines.append(f"### {label} ({len(test_files)} files)")
        lines.append("")
        for tf in test_files:
            lines.append(f"- `{tf.name}`")
        lines.append("")

    if total_tests == 0:
        lines.append("_No test files found._")
        lines.append("")

    lines.append(f"**Total test files: {total_tests}**")
    lines.append("")
    return lines


def section_staleness_check() -> list[str]:
    """Section 6: docs/ 入面嘅 .md 文件新鮮度。"""
    lines = ["## 6. Documentation Staleness", ""]

    docs_dir = AXC_HOME / "docs"
    if not docs_dir.exists():
        lines.append("_docs/ not found._")
        lines.append("")
        return lines

    now = datetime.now()
    stale_cutoff = now - timedelta(days=STALE_DAYS)
    stale_files: list[tuple[Path, datetime]] = []
    fresh_count = 0

    for md in sorted(docs_dir.rglob("*.md")):
        try:
            mtime = datetime.fromtimestamp(md.stat().st_mtime)
        except OSError:
            continue
        if mtime < stale_cutoff:
            stale_files.append((md, mtime))
        else:
            fresh_count += 1

    lines.append(f"- Fresh (<{STALE_DAYS} days): **{fresh_count}** files")
    lines.append(f"- Stale (>{STALE_DAYS} days): **{len(stale_files)}** files")
    lines.append("")

    if stale_files:
        lines.append("### ⚠️ Potentially Stale")
        lines.append("")
        lines.append("| File | Last Modified |")
        lines.append("|------|---------------|")
        for fpath, mtime in stale_files:
            rel = fpath.relative_to(AXC_HOME)
            days_ago = (now - mtime).days
            lines.append(f"| `{rel}` | {mtime:%Y-%m-%d} ({days_ago}d ago) |")
        lines.append("")

    return lines


# ════════════════════════════════════════════════════
# Atomic Write
# ════════════════════════════════════════════════════

def atomic_write(path: Path, content: str) -> None:
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
# Main
# ════════════════════════════════════════════════════

SECTIONS = [
    ("Folder Manifest", section_folder_manifest),
    ("System Boundary Map", section_boundary_map),
    ("Dependency Graph", section_dependency_graph),
    ("LaunchAgent Services", section_launchagent_services),
    ("Test Coverage", section_test_coverage),
    ("Documentation Staleness", section_staleness_check),
]


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    t0 = time.monotonic()
    log.info("Scanning %s ...", AXC_HOME)

    all_lines = _generate_header()

    for name, func in SECTIONS:
        try:
            log.info("  → %s", name)
            section_lines = func()
            all_lines.extend(section_lines)
            all_lines.append("---")
            all_lines.append("")
        except Exception as e:
            log.warning("  ⚠️ Section '%s' failed: %s", name, e)
            all_lines.append(f"## {name}")
            all_lines.append("")
            all_lines.append(f"> ⚠️ Section skipped due to error: `{e}`")
            all_lines.append("")
            all_lines.append("---")
            all_lines.append("")

    elapsed = time.monotonic() - t0
    all_lines.append(f"_Generated in {elapsed:.1f}s_")

    content = "\n".join(all_lines) + "\n"
    atomic_write(OUTPUT_PATH, content)
    log.info("✅ Written to %s (%d lines, %.1fs)", OUTPUT_PATH, len(all_lines), elapsed)


if __name__ == "__main__":
    main()
