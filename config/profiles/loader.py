"""
loader.py — Profile 載入、列表、驗證。

設計決定：用 importlib 動態 import profile 文件，唔用 JSON/YAML，
因為 profile 文件可以寫 comment + 用 Python 表達式。
"""

import importlib.util
import logging
import os

from config.profiles._base import DEFAULT_PROFILE

log = logging.getLogger(__name__)

_PROFILE_DIR = os.path.dirname(os.path.abspath(__file__))
_SKIP_FILES = {"__init__", "_base", "loader"}

# 最近一次 load_profile 嘅結果狀態。
# Dashboard / 其他模組可讀呢個 dict 嚟知道有冇 fallback。
_LAST_LOAD_STATUS: dict = {
    "profile": None,       # str: 實際載入嘅 profile 名
    "fallback": False,     # bool: 是否用咗 fallback
    "reason": "",          # str: fallback 原因（空 = 成功）
    "warnings": [],        # list[str]: 驗證 warnings
}


def _set_status(profile: str, *, fallback: bool = False,
                reason: str = "", warnings: list[str] | None = None):
    """更新 _LAST_LOAD_STATUS。"""
    _LAST_LOAD_STATUS["profile"] = profile
    _LAST_LOAD_STATUS["fallback"] = fallback
    _LAST_LOAD_STATUS["reason"] = reason
    _LAST_LOAD_STATUS["warnings"] = warnings or []


def load_profile(name: str | None = None) -> dict:
    """載入並合併 profile。name=None → 讀 params.py ACTIVE_PROFILE。

    失敗時 log warning 並返回 DEFAULT_PROFILE（唔 crash）。
    結果記錄到 _LAST_LOAD_STATUS，caller 可檢查 fallback 狀態。
    """
    if name is None:
        try:
            params_path = os.path.join(
                os.path.dirname(_PROFILE_DIR), "params.py"
            )
            spec = importlib.util.spec_from_file_location("_params_loader", params_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            name = getattr(mod, "ACTIVE_PROFILE", "BALANCED")
        except Exception as e:
            reason = f"Failed to read ACTIVE_PROFILE from params.py: {e}"
            log.warning(reason)
            _set_status("BALANCED", fallback=True, reason=reason)
            return dict(DEFAULT_PROFILE)

    name_lower = name.lower()
    profile_path = os.path.join(_PROFILE_DIR, f"{name_lower}.py")
    if not os.path.isfile(profile_path):
        reason = f"Profile '{name}' not found at {profile_path}. Using defaults."
        log.warning(reason)
        _set_status(name, fallback=True, reason=reason)
        return dict(DEFAULT_PROFILE)
    try:
        # file-based import — 每次重新讀文件，唔受 importlib cache 影響
        spec = importlib.util.spec_from_file_location(
            f"_profile_{name_lower}", profile_path
        )
        profile_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profile_mod)
        overrides = getattr(profile_mod, "PROFILE", {})
    except Exception as e:
        reason = f"Failed to load profile '{name}': {e}. Using defaults."
        log.warning(reason)
        _set_status(name, fallback=True, reason=reason)
        return dict(DEFAULT_PROFILE)

    # 過濾 unknown keys — 唔 merge 入 dict，只 log warning
    warnings = []
    unknown_keys = set(overrides) - set(DEFAULT_PROFILE)
    for k in sorted(unknown_keys):
        msg = f"unknown key '{k}' ignored (typo?)"
        log.warning("Profile '%s': %s", name, msg)
        warnings.append(msg)
    clean_overrides = {k: v for k, v in overrides.items() if k in DEFAULT_PROFILE}

    merged = {**DEFAULT_PROFILE, **clean_overrides}

    # 自動驗證：type mismatch 等
    for issue in validate_profile(merged):
        log.warning("Profile '%s': %s", name, issue)
        warnings.append(issue)

    _set_status(name, warnings=warnings)
    return merged


def list_profiles() -> list[str]:
    """掃描 config/profiles/ 目錄，返回可用 profile 名（大寫）。"""
    profiles = []
    for f in sorted(os.listdir(_PROFILE_DIR)):
        if not f.endswith(".py"):
            continue
        stem = f[:-3]
        if stem in _SKIP_FILES or stem.startswith("_"):
            continue
        profiles.append(stem.upper())
    return profiles


def validate_profile(p: dict) -> list[str]:
    """驗證 profile dict。返回問題列表（空 = 通過）。"""
    issues = []

    # 檢查缺少嘅 key
    missing = set(DEFAULT_PROFILE) - set(p)
    for k in sorted(missing):
        issues.append(f"Missing key: '{k}'")

    # 檢查未知嘅 key
    unknown = set(p) - set(DEFAULT_PROFILE)
    for k in sorted(unknown):
        issues.append(f"Unknown key: '{k}' (typo?)")

    # Type check（同 base 比較，int/float 視為相容）
    for k, base_val in DEFAULT_PROFILE.items():
        if k not in p:
            continue
        val = p[k]
        if base_val is None or val is None:
            continue  # None = 任何 type 都得 / profile 可以 disable
        base_type = type(base_val)
        val_type = type(val)
        # int 同 float 互換合理（例如 70 vs 70.0）
        if isinstance(base_val, (int, float)) and isinstance(val, (int, float)):
            continue
        if not isinstance(val, base_type):
            issues.append(
                f"Type mismatch: '{k}' expected {base_type.__name__}, "
                f"got {val_type.__name__}"
            )

    return issues


def get_all_profiles() -> dict[str, dict]:
    """返回所有 profile 嘅 merged dict，結構同舊 TRADING_PROFILES 一樣。

    用途：dashboard 需要列出所有 profile 嘅完整參數。
    """
    result = {}
    for name in list_profiles():
        result[name] = load_profile(name)
    return result
