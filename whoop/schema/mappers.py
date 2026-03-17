"""
Pure functions: WHOOP API JSON → unified schema dataclasses.
No side effects, no I/O.

Supports both flat and nested response formats from WHOOP API.
The real API (as of 2026-03) returns flat records where fields like
recovery_score, hrv_rmssd_milli sit at the top level — NOT inside
a nested "score" object. We check flat fields first, then fall back
to the nested "score.*" path for forward-compatibility.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .unified import ActivityData, BodyMeasurement, RecoveryData, SleepData, WorkoutData

logger = logging.getLogger(__name__)


def _get(data: dict, key: str, fallback_obj: dict | None = None):
    """Get a field from data (flat), falling back to fallback_obj (nested score)."""
    val = data.get(key)
    if val is not None:
        return val
    if fallback_obj is not None:
        return fallback_obj.get(key)
    return None


def _safe_divide(value, divisor):
    """Safely divide, handling None and zero values correctly."""
    if value is None:
        return None
    return value / divisor


def _safe_int(value):
    """Safely convert to int, handling None."""
    if value is None:
        return None
    return int(value)


# ---------------------------------------------------------------------------
# Body Measurement
# ---------------------------------------------------------------------------

def map_body_measurement(data: dict) -> BodyMeasurement:
    """Map WHOOP body measurement response to BodyMeasurement.

    API returns flat object: {height_meter, weight_kilogram, max_heart_rate}.
    """
    height = data.get("height_meter")
    weight = data.get("weight_kilogram")
    max_hr = data.get("max_heart_rate")

    return BodyMeasurement(
        height_meter=round(float(height), 2) if height is not None else None,
        weight_kilogram=round(float(weight), 1) if weight is not None else None,
        max_heart_rate=int(max_hr) if max_hr is not None else None,
    )


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def map_recovery(data: dict) -> RecoveryData:
    """Map a single WHOOP recovery record to RecoveryData.

    Handles both flat format (real API) and nested score format.
    Respects score_state: returns all-None if not SCORED.
    """
    score_state = data.get("score_state")
    if score_state and score_state != "SCORED":
        logger.debug("Recovery score_state=%s, returning empty", score_state)
        return RecoveryData(
            score=None, hrv_rmssd=None, resting_hr=None,
            spo2=None, skin_temp_deviation=None,
        )

    score_obj = data.get("score") or {}

    raw_score = _get(data, "recovery_score", score_obj)
    hrv = _get(data, "hrv_rmssd_milli", score_obj)
    rhr = _get(data, "resting_heart_rate", score_obj)
    spo2 = _get(data, "spo2_percentage", score_obj)
    skin_temp = _get(data, "skin_temp_celsius", score_obj)

    return RecoveryData(
        score=round(raw_score) if raw_score is not None else None,
        hrv_rmssd=float(hrv) if hrv is not None else None,
        resting_hr=int(rhr) if rhr is not None else None,
        spo2=float(spo2) if spo2 is not None else None,
        skin_temp_deviation=float(skin_temp) if skin_temp is not None else None,
    )


# ---------------------------------------------------------------------------
# Sleep
# ---------------------------------------------------------------------------

def map_sleep(data: dict) -> SleepData:
    """Map a single WHOOP sleep record to SleepData.

    Handles both flat format (real API) and nested score format.
    Duration is computed from total_in_bed_time_milli if available,
    otherwise from start/end timestamps.
    """
    score_state = data.get("score_state")
    if score_state and score_state != "SCORED":
        return SleepData(score=None, duration_hours=None, efficiency=None, stages={})

    score_obj = data.get("score") or {}
    stage_summary = score_obj.get("stage_summary") or {}

    # Score
    perf = _get(data, "sleep_performance_percentage", score_obj)

    # Efficiency — percentage (0-100) → fraction (0-1)
    eff_raw = _get(data, "sleep_efficiency_percentage", score_obj)
    efficiency = _safe_divide(eff_raw, 100)

    # Duration: prefer total_in_bed_time_milli (flat), then start/end
    in_bed_ms = _get(data, "total_in_bed_time_milli", score_obj)
    if in_bed_ms is None and stage_summary:
        in_bed_ms = stage_summary.get("total_in_bed_time_milli")

    if in_bed_ms is not None:
        duration_hours = round(in_bed_ms / 3_600_000, 2)
    elif data.get("start") and data.get("end"):
        ms = _duration_ms(data["start"], data["end"])
        duration_hours = round(ms / 3_600_000, 2) if ms and ms > 0 else None
    else:
        duration_hours = None

    # Stage summary — build from flat fields if nested is empty
    stages = _build_stages(data, score_obj, stage_summary)

    return SleepData(
        score=int(perf) if perf is not None else None,
        duration_hours=duration_hours,
        efficiency=round(efficiency, 4) if efficiency is not None else None,
        stages=stages,
    )


def _build_stages(data: dict, score_obj: dict, stage_summary: dict) -> dict:
    """Build sleep stages dict from flat or nested fields."""
    if stage_summary:
        return stage_summary

    # Try flat fields (real API format)
    stage_keys = [
        "total_light_sleep_time_milli",
        "total_slow_wave_sleep_time_milli",
        "total_rem_sleep_time_milli",
        "total_awake_time_milli",
        "total_no_data_time_milli",
        "sleep_cycle_count",
        "disturbance_count",
    ]
    stages = {}
    for key in stage_keys:
        val = _get(data, key, score_obj)
        if val is not None:
            stages[key] = val
    return stages


# ---------------------------------------------------------------------------
# Workout
# ---------------------------------------------------------------------------

def map_workout(data: dict) -> WorkoutData:
    """Map a single WHOOP workout record to WorkoutData.

    Handles both flat and nested score format.
    Uses sport_name from API if available, otherwise looks up sport_id.
    """
    score_obj = data.get("score") or {}

    strain = _get(data, "strain", score_obj)
    avg_hr = _get(data, "average_heart_rate", score_obj)
    max_hr = _get(data, "max_heart_rate", score_obj)
    kj = _get(data, "kilojoule", score_obj)

    # Use sport_name from API if available, otherwise look up sport_id
    sport = data.get("sport_name") or _sport_name(data.get("sport_id", -1))

    return WorkoutData(
        sport=sport,
        strain=float(strain) if strain is not None else None,
        duration_minutes=_duration_minutes(data.get("start"), data.get("end")),
        avg_hr=_safe_int(avg_hr),
        max_hr=_safe_int(max_hr),
        calories=_safe_int(_safe_divide(kj, 4.184)) if kj is not None else None,
        started_at=data.get("start"),
    )


# ---------------------------------------------------------------------------
# Cycle
# ---------------------------------------------------------------------------

def map_cycle(data: dict) -> ActivityData:
    """Map a single WHOOP cycle record to ActivityData.

    Handles both flat and nested score format.
    """
    score_obj = data.get("score") or {}

    strain = _get(data, "strain", score_obj)
    kj = _get(data, "kilojoule", score_obj)

    return ActivityData(
        strain=float(strain) if strain is not None else None,
        calories=_safe_int(_safe_divide(kj, 4.184)) if kj is not None else None,
        workouts=[],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _duration_ms(start: str, end: str) -> int | None:
    try:
        t_start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        t_end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return int((t_end - t_start).total_seconds() * 1000)
    except Exception:
        return None


def _duration_minutes(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    ms = _duration_ms(start, end)
    if ms is None or ms <= 0:
        return None
    return round(ms / 60_000, 1)


_SPORT_NAMES: dict[int, str] = {
    -1: "Activity",
    0: "Running",
    1: "Cycling",
    16: "Baseball",
    17: "Basketball",
    18: "Rowing",
    19: "Fencing",
    20: "Field Hockey",
    21: "Football",
    22: "Golf",
    24: "Ice Hockey",
    25: "Lacrosse",
    27: "Rugby",
    28: "Sailing",
    29: "Skiing",
    30: "Soccer",
    31: "Softball",
    32: "Squash",
    33: "Swimming",
    34: "Tennis",
    35: "Track & Field",
    36: "Volleyball",
    37: "Water Polo",
    38: "Wrestling",
    39: "Boxing",
    42: "Dance",
    43: "Pilates",
    44: "Yoga",
    45: "Weightlifting",
    47: "Cross Country Skiing",
    48: "Functional Fitness",
    49: "Duathlon",
    51: "Gymnastics",
    52: "Hiking/Rucking",
    53: "Horseback Riding",
    55: "Kayaking",
    56: "Martial Arts",
    57: "Mountain Biking",
    59: "Powerlifting",
    60: "Rock Climbing",
    61: "Paddleboarding",
    62: "Triathlon",
    63: "Walking",
    64: "Surfing",
    65: "Elliptical",
    66: "Stairmaster",
    70: "Meditation",
    71: "Other",
    73: "HIIT",
    74: "Jumping Rope",
    75: "Australian Football",
    76: "Skateboarding",
    77: "Coaching",
    78: "Ice Bath",
    82: "Obstacle Course Racing",
    83: "Motor Racing",
    84: "SCUBA Diving",
    85: "Snowboarding",
    86: "Motocross",
    87: "Canoeing",
    88: "Racquetball",
    89: "Virtual Cycling",
    90: "Polo",
    91: "Wheelchair Pushing",
    92: "Cricket",
    93: "Archery",
    94: "Snowshoeing",
    95: "Handball",
    96: "Badminton",
    97: "Pickleball",
    98: "Luge",
    99: "Winter Sports",
    100: "Bobsled",
    101: "Volleyball (Beach)",
}


def _sport_name(sport_id: int) -> str:
    return _SPORT_NAMES.get(sport_id, f"Sport {sport_id}")
