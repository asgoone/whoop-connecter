"""
Pure functions: WHOOP API JSON → unified schema dataclasses.
No side effects, no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .unified import ActivityData, RecoveryData, SleepData, WorkoutData


def map_recovery(data: dict) -> RecoveryData:
    score_obj = data.get("score") or {}
    raw_score = score_obj.get("recovery_score")
    return RecoveryData(
        score=round(raw_score) if raw_score is not None else None,
        hrv_rmssd=score_obj.get("hrv_rmssd_milli"),
        resting_hr=score_obj.get("resting_heart_rate"),
        spo2=score_obj.get("spo2_percentage"),
        skin_temp_deviation=score_obj.get("skin_temp_celsius"),
    )


def map_sleep(data: dict) -> SleepData:
    score_obj = data.get("score") or {}
    stage_summary = score_obj.get("stage_summary") or {}

    duration_ms = data.get("end") and data.get("start") and _duration_ms(
        data["start"], data["end"]
    )
    duration_hours = (duration_ms / 3_600_000) if duration_ms else None

    return SleepData(
        score=score_obj.get("sleep_performance_percentage"),
        duration_hours=duration_hours,
        efficiency=score_obj.get("sleep_efficiency_percentage") and
                   score_obj["sleep_efficiency_percentage"] / 100,
        stages=stage_summary,
    )


def map_workout(data: dict) -> WorkoutData:
    score_obj = data.get("score") or {}
    return WorkoutData(
        sport=_sport_name(data.get("sport_id", -1)),
        strain=score_obj.get("strain"),
        duration_minutes=_duration_minutes(data.get("start"), data.get("end")),
        avg_hr=score_obj.get("average_heart_rate"),
        max_hr=score_obj.get("max_heart_rate"),
        calories=score_obj.get("kilojoule") and int(score_obj["kilojoule"] / 4.184),
        started_at=data.get("start"),
    )


def map_cycle(data: dict) -> ActivityData:
    score_obj = data.get("score") or {}
    return ActivityData(
        strain=score_obj.get("strain"),
        calories=score_obj.get("kilojoule") and int(score_obj["kilojoule"] / 4.184),
        workouts=[],  # workouts fetched separately if needed
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
    return (ms / 60_000) if ms else None


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
