"""files.py — Docs serving + share package generation."""

import io
import logging
import os
import subprocess
import zipfile

from scripts.dashboard.constants import HOME, DOCS_ROOT


def _safe_docs_path(rel_path):
    """Resolve path and ensure it stays within HOME/docs/. Returns None if traversal detected."""
    docs_root = os.path.abspath(os.path.join(HOME, "docs"))
    resolved = os.path.abspath(os.path.join(HOME, rel_path))
    if not resolved.startswith(docs_root + os.sep) and resolved != docs_root:
        return None
    return resolved


def handle_file_read(rel_path):
    """GET /api/file?path=docs/..."""
    fp = _safe_docs_path(rel_path)
    if fp is None:
        return 403, "Forbidden"
    if not os.path.exists(fp):
        return 404, "Not found"
    with open(fp) as f:
        return 200, f.read()


def handle_open_folder(rel_path):
    """GET /api/open_folder?path=docs/..."""
    fp = _safe_docs_path(rel_path)
    if fp is None:
        return 403, {"error": "Forbidden"}
    if os.path.exists(fp):
        subprocess.Popen(["open", fp])
    return 200, {"ok": True}


def get_docs_list() -> list:
    """返回 docs/ 下所有 .md 文件嘅相對路徑。"""
    result = []
    if not os.path.isdir(DOCS_ROOT):
        return result
    for root, dirs, files in os.walk(DOCS_ROOT):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for f in sorted(files):
            if f.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, f), DOCS_ROOT)
                result.append(rel.replace("\\", "/"))
    return result


def serve_doc(filename: str):
    """
    提供 docs/ 文件內容。
    雙重安全：只允許 .md + abspath 確認在 docs/ 範圍內。
    Returns: (status_code, content, content_type)
    """
    if not filename.endswith(".md"):
        return 403, "Not allowed", "text/plain; charset=utf-8"

    safe_path = os.path.abspath(os.path.join(DOCS_ROOT, filename))
    if not safe_path.startswith(os.path.abspath(DOCS_ROOT)):
        return 403, "Forbidden", "text/plain; charset=utf-8"

    if not os.path.exists(safe_path):
        return 404, "Not found", "text/plain; charset=utf-8"

    with open(safe_path, encoding="utf-8") as f:
        content = f.read()
    return 200, content, "text/plain; charset=utf-8"


def generate_share_package() -> bytes:
    """
    生成 AXC setup zip（io.BytesIO，記憶體操作）。
    包含：scripts/, config/, canvas/, docs/, backtest/（源碼）,
          agents/*/SOUL.md, CLAUDE.md, requirements.txt, openclaw.json
    排除：secrets/, logs/, memory/, shared/, backups/,
          mlx_model/, __pycache__/, .git/,
          agents/*/workspace/, agents/*/agent/,
          agents/main/sessions/, backtest/data/
    自動生成 secrets/.env.example（變數名，值清空）
    """
    ROOT = HOME
    EXCLUDE_TOP = {
        "secrets", "logs", "memory", "shared",
        "backups", "mlx_model", ".git", ".github",
    }

    def should_exclude(rel: str) -> bool:
        parts = rel.replace("\\", "/").split("/")
        if parts[0] in EXCLUDE_TOP:
            return True
        if (len(parts) >= 3 and parts[0] == "agents"
                and parts[2] in ("workspace", "agent")):
            return True
        if "__pycache__" in parts:
            return True
        if rel.startswith(os.path.join("agents", "main", "sessions")):
            return True
        # backtest/data/ 排除（CSV cache + 生成嘅 PNG/JSONL）
        if rel.startswith(os.path.join("backtest", "data")):
            return True
        return False

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 包含主要目錄
        for inc in ["scripts", "config", "canvas", "docs", "backtest"]:
            inc_path = os.path.join(ROOT, inc)
            if not os.path.exists(inc_path):
                continue
            for dirpath, dirs, files in os.walk(inc_path):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for fname in files:
                    fpath = os.path.join(dirpath, fname)
                    rel = os.path.relpath(fpath, ROOT)
                    if not should_exclude(rel):
                        zf.write(fpath, rel)

        # agents/ 只包含 SOUL.md
        agents_path = os.path.join(ROOT, "agents")
        if os.path.exists(agents_path):
            for dirpath, dirs, files in os.walk(agents_path):
                dirs[:] = [d for d in dirs
                           if d not in ("workspace", "agent", "__pycache__")]
                for fname in files:
                    if fname == "SOUL.md":
                        fpath = os.path.join(dirpath, fname)
                        rel = os.path.relpath(fpath, ROOT)
                        zf.write(fpath, rel)

        # 根目錄文件（存在先加）
        for rf in ["CLAUDE.md", "README.md", "requirements.txt"]:
            rpath = os.path.join(ROOT, rf)
            if os.path.exists(rpath):
                zf.write(rpath, rf)

        # 動態生成 .env.example（從現有 .env 取 key 名，清空值）
        env_example = [
            "# AXC Trading System .env.example",
            "# 複製為 secrets/.env 並填入你的 API Key",
            "# cp secrets/.env.example secrets/.env",
            "",
            "# ── AI 推理（選填，核心交易唔需要）────────────────",
            "PROXY_API_KEY=",
            "PROXY_BASE_URL=https://tao.plus7.plus/v1",
            "",
        ]
        env_path = os.path.join(ROOT, "secrets", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        key = line.split("=")[0]
                        if key not in ("PROXY_API_KEY", "PROXY_BASE_URL"):
                            env_example.append(f"{key}=")
        else:
            env_example += [
                "ASTER_API_KEY=", "ASTER_API_SECRET=",
                "BINANCE_API_KEY=", "BINANCE_API_SECRET=",
                "TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=",
                "VOYAGE_API_KEY=",
            ]
        zf.writestr("secrets/.env.example", "\n".join(env_example))

        # INSTALL.md（從 guides 搬）
        install_path = os.path.join(ROOT, "docs", "guides", "00-install.md")
        if os.path.exists(install_path):
            zf.write(install_path, "INSTALL.md")

    return buf.getvalue()
