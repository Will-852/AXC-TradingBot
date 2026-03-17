"""
weather_tracker.py — Multi-model ensemble paper tracker for weather markets

設計決定：
- Standalone paper tracker，唔動 pipeline（edge_finder.py 保持不變）
- 3 model ensemble (GFS 31 + ECMWF 51 + ICON 40 = 122 members)
- 跑 2 週收集 data → 計算 model bias → 驗證 edge
- Resolution 先用 Open-Meteo archive 做 proxy（Wunderground JS rendering 問題）
"""

import json
import logging
import os
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..config.categories import WEATHER_CITIES
from ..config.settings import AXC_HOME, LOG_DIR

logger = logging.getLogger(__name__)

_HKT = ZoneInfo("Asia/Hong_Kong")

# ─── Constants ───

_TRACKER_LOG = os.path.join(LOG_DIR, "weather_predictions.jsonl")
_RESOLUTION_LOG = os.path.join(LOG_DIR, "weather_resolutions.jsonl")

# Ensemble models available via Open-Meteo (free)
# Total: GFS(31) + ECMWF(51) + ICON(40) = 122 members
ENSEMBLE_MODELS = ["gfs_seamless", "ecmwf_ifs025", "icon_global"]

# Resolution sources per city
RESOLUTION_SOURCES = {
    "seoul": {"type": "wunderground", "station": "RKSI", "precision": "whole"},
    "shanghai": {"type": "wunderground", "station": "ZSPD", "precision": "whole"},
    "hong kong": {"type": "hko", "station": "HKO_HQ", "precision": "decimal"},
}

# Ensure Seoul is available for market parsing (not in WEATHER_CITIES by default)
if "seoul" not in WEATHER_CITIES:
    WEATHER_CITIES["seoul"] = (37.566, 126.978, "C")

_ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"
_ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
_HKO_API = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"
_UA = "AXC-Trading/1.0"
_TIMEOUT = 15


# ─── Function 1: Fetch Ensemble Forecast ───

def fetch_ensemble_forecast(
    lat: float,
    lon: float,
    target_date: str,
    models: list[str] | None = None,
    fahrenheit: bool = False,
) -> dict[str, list[float]]:
    """Fetch all ensemble member max temps from Open-Meteo ensemble API.

    Returns dict keyed by model suffix → list of member max temp values.
    Open-Meteo returns flat daily dict with model suffix in each key:
      temperature_2m_max_member01_ncep_gefs_seamless, etc.
    Pass fahrenheit=True for US cities (°F buckets).
    """
    models = models or ENSEMBLE_MODELS
    models_str = ",".join(models)
    temp_unit = "&temperature_unit=fahrenheit" if fahrenheit else ""
    url = (
        f"{_ENSEMBLE_API}?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&models={models_str}"
        f"&timezone=auto{temp_unit}"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Ensemble API fetch error: %s", e)
        return {}

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        logger.warning("No time axis in ensemble response")
        return {}

    date_idx = None
    for i, d in enumerate(dates):
        if d == target_date:
            date_idx = i
            break
    if date_idx is None:
        logger.info("Target date %s not in response (range: %s to %s)",
                     target_date, dates[0], dates[-1])
        return {}

    # Group member values by model suffix extracted from key names.
    # Key format: temperature_2m_max[_memberNN]_<model_suffix>
    # e.g. temperature_2m_max_ncep_gefs_seamless (control)
    #      temperature_2m_max_member01_ncep_gefs_seamless
    result: dict[str, list[float]] = {}
    for key, values in daily.items():
        if not key.startswith("temperature_2m_max") or key == "time":
            continue
        if date_idx >= len(values) or values[date_idx] is None:
            continue

        # Extract model suffix: strip prefix + optional memberNN_
        stripped = key.replace("temperature_2m_max_", "")
        if stripped.startswith("member"):
            # member01_ncep_gefs_seamless → ncep_gefs_seamless
            parts = stripped.split("_", 1)
            model_suffix = parts[1] if len(parts) > 1 else "unknown"
        else:
            model_suffix = stripped

        result.setdefault(model_suffix, []).append(float(values[date_idx]))

    for model_suffix, members in result.items():
        logger.info(
            "Model %s: %d members, mean=%.1f°C",
            model_suffix, len(members), statistics.mean(members),
        )

    return result


# ─── Function 2: Compute Bucket Probabilities ───

def compute_bucket_probabilities(
    members: list[float],
    bucket_boundaries: list[tuple[str, float | None, float | None]],
) -> dict[str, float]:
    """Count ensemble members in each bucket → probability.

    Args:
        members: flat list of all ensemble member max temps
        bucket_boundaries: list of (label, low_inclusive, high_exclusive) tuples.
            low=None means floor bucket (everything below high).
            high=None means ceiling bucket (everything >= low).
    """
    if not members:
        return {}

    total = len(members)
    counts = {label: 0 for label, _, _ in bucket_boundaries}

    for temp in members:
        for label, low, high in bucket_boundaries:
            if low is None:
                # Floor bucket: temp < high
                if temp < high:
                    counts[label] += 1
                    break
            elif high is None:
                # Ceiling bucket: temp >= low
                if temp >= low:
                    counts[label] += 1
                    break
            else:
                # Normal bucket: low <= temp < high
                if low <= temp < high:
                    counts[label] += 1
                    break

    return {label: round(count / total, 4) for label, count in counts.items()}


# ─── Function 3: Log Weather Prediction ───

def log_weather_prediction(
    *,
    city: str,
    target_date: str,
    resolution_source: str,
    resolution_station: str,
    precision: str,
    lead_days: int,
    ensemble_count: int,
    models_used: list[str],
    ensemble_mean: float,
    ensemble_std: float,
    ensemble_min: float,
    ensemble_max: float,
    buckets: dict,
    best_edge_bucket: str,
    best_edge_pct: float,
    acted: bool = False,
    notes: str = "paper_track",
) -> None:
    """Append weather prediction record to JSONL for calibration tracking."""
    now = datetime.now(tz=_HKT)

    res = RESOLUTION_SOURCES.get(city, {})
    bucket_rule = "floor_unconfirmed" if res.get("precision") == "decimal" else "whole_degree"

    record = {
        "ts": now.isoformat(),
        "city": city,
        "target_date": target_date,
        "resolution_source": resolution_source,
        "resolution_station": resolution_station,
        "precision": precision,
        "bucket_rule": bucket_rule,
        "lead_days": lead_days,
        "ensemble_count": ensemble_count,
        "models_used": models_used,
        "ensemble_mean": round(ensemble_mean, 2),
        "ensemble_std": round(ensemble_std, 2),
        "ensemble_min": round(ensemble_min, 2),
        "ensemble_max": round(ensemble_max, 2),
        "buckets": buckets,
        "best_edge_bucket": best_edge_bucket,
        "best_edge_pct": round(best_edge_pct, 4),
        "acted": acted,
        "notes": notes,
    }

    try:
        os.makedirs(os.path.dirname(_TRACKER_LOG), exist_ok=True)
        with open(_TRACKER_LOG, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            "Logged weather prediction: %s %s (best edge: %s %.1f%%)",
            city, target_date, best_edge_bucket, best_edge_pct * 100,
        )
    except IOError as e:
        logger.warning("Weather prediction log write failed: %s", e)


# ─── Function 4: Fetch Resolution ───

def fetch_resolution(city: str, target_date: str) -> float | None:
    """Fetch actual max temperature for a resolved market date.

    Uses Open-Meteo archive API as proxy for Wunderground (JS rendering issue).
    For HKO: uses HK Observatory open data API, falls back to archive.
    Note: Archive API uses ERA5 reanalysis, may differ 1-2°C from Wunderground.
    Phase 2 will find better resolution sources.
    """
    res = RESOLUTION_SOURCES.get(city)
    if not res:
        logger.warning("No resolution source for city: %s", city)
        return None

    if city not in WEATHER_CITIES:
        logger.warning("City %s not in WEATHER_CITIES", city)
        return None

    lat, lon, _ = WEATHER_CITIES[city]

    if res["type"] == "hko":
        return _fetch_hko_max(lat, lon, target_date)

    # Wunderground cities: use Open-Meteo archive as proxy
    return _fetch_archive_max(lat, lon, target_date)


def _fetch_archive_max(lat: float, lon: float, target_date: str) -> float | None:
    """Fetch historical max temp from Open-Meteo archive API (ERA5 reanalysis)."""
    url = (
        f"{_ARCHIVE_API}?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max"
        f"&start_date={target_date}&end_date={target_date}"
        f"&timezone=auto"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())

        maxes = data.get("daily", {}).get("temperature_2m_max", [])
        if maxes and maxes[0] is not None:
            return float(maxes[0])
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Archive API fetch error for %s: %s", target_date, e)
        return None


def _fetch_hko_max(lat: float, lon: float, target_date: str) -> float | None:
    """Fetch HKO max temperature. Uses rhrread for today, archive for past."""
    today = date.today().isoformat()

    if target_date == today:
        url = f"{_HKO_API}?dataType=rhrread&lang=en"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())

            temp_data = data.get("temperature", {}).get("data", [])
            if temp_data:
                temps = [float(t["value"]) for t in temp_data if "value" in t]
                if temps:
                    return max(temps)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            logger.warning("HKO API fetch error: %s", e)

    # Past date or HKO fail: use Open-Meteo archive
    return _fetch_archive_max(lat, lon, target_date)


# ─── Function 5: Compute Brier Score ───

def compute_brier_score(
    predictions_path: str | None = None,
    resolutions_path: str | None = None,
) -> dict:
    """Join predictions + resolutions → Brier score + bias analysis.

    Brier = (1/N) × Σ (forecast_prob - actual_outcome)² per bucket.
    Also computes systematic bias = mean(ensemble_mean - actual_max).
    """
    pred_path = predictions_path or _TRACKER_LOG
    res_path = resolutions_path or _RESOLUTION_LOG

    # Load predictions: keyed by (city, date)
    predictions = {}
    try:
        with open(pred_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = (rec["city"], rec["target_date"])
                predictions[key] = rec
    except FileNotFoundError:
        logger.warning("No predictions file: %s", pred_path)
        return {"error": "no_predictions"}

    # Load resolutions: keyed by (city, date) → actual_max
    resolutions = {}
    try:
        with open(res_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = (rec["city"], rec["target_date"])
                resolutions[key] = rec["actual_max"]
    except FileNotFoundError:
        logger.warning("No resolutions file: %s", res_path)
        return {"error": "no_resolutions", "predictions_count": len(predictions)}

    # Match predictions with resolutions
    brier_scores = []
    biases = []
    by_city: dict[str, list[float]] = {}
    by_lead: dict[int, list[float]] = {}
    bias_by_city: dict[str, list[float]] = {}

    for key, pred in predictions.items():
        if key not in resolutions:
            continue

        actual_max = resolutions[key]
        city = pred["city"]
        lead = pred["lead_days"]

        # Bias: model mean - actual
        bias = pred["ensemble_mean"] - actual_max
        biases.append(bias)
        bias_by_city.setdefault(city, []).append(bias)

        # Brier score per prediction: Σ (forecast_prob - outcome)² across buckets
        buckets = pred["buckets"]
        brier = 0.0
        for bucket_label, bucket_data in buckets.items():
            prob = bucket_data["ensemble_prob"]
            bucket_val = float(bucket_label)
            precision = pred.get("precision", "whole")
            if precision == "decimal":
                # HKO floor-based: bucket "X" = [X, X+1)
                outcome = 1.0 if bucket_val <= actual_max < bucket_val + 1.0 else 0.0
            else:
                # Wunderground whole degree: bucket "X" = rounded to X
                outcome = 1.0 if round(actual_max) == bucket_val else 0.0
            brier += (prob - outcome) ** 2

        brier_scores.append(brier)
        by_city.setdefault(city, []).append(brier)
        by_lead.setdefault(lead, []).append(brier)

    matched = len(brier_scores)
    if not matched:
        return {
            "matched": 0,
            "predictions_count": len(predictions),
            "resolutions_count": len(resolutions),
        }

    return {
        "matched": matched,
        "predictions_count": len(predictions),
        "resolutions_count": len(resolutions),
        "brier_overall": round(statistics.mean(brier_scores), 4),
        "bias_overall": round(statistics.mean(biases), 2),
        "bias_std": round(statistics.stdev(biases), 2) if len(biases) > 1 else 0.0,
        "by_city": {
            city: {
                "brier": round(statistics.mean(scores), 4),
                "n": len(scores),
                "bias": round(statistics.mean(bias_by_city.get(city, [0])), 2),
            }
            for city, scores in by_city.items()
        },
        "by_lead_days": {
            str(lead): {
                "brier": round(statistics.mean(scores), 4),
                "n": len(scores),
            }
            for lead, scores in sorted(by_lead.items())
        },
    }
