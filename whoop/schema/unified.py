"""
Source-agnostic health data schema.
Designed to accommodate WHOOP, Oura, and Apple Health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SleepData:
    score: int | None              # 0-100
    duration_hours: float | None
    efficiency: float | None       # 0.0-1.0
    stages: dict[str, Any] = field(default_factory=dict)  # source-specific


@dataclass
class RecoveryData:
    score: int | None              # 0-100
    hrv_rmssd: float | None        # ms
    resting_hr: int | None         # bpm
    spo2: float | None             # %, optional
    skin_temp_deviation: float | None = None  # °C, optional


@dataclass
class WorkoutData:
    sport: str
    strain: float | None           # WHOOP-specific
    duration_minutes: float | None
    avg_hr: int | None
    max_hr: int | None
    calories: int | None
    started_at: str | None         # ISO datetime


@dataclass
class ActivityData:
    strain: float | None           # 0-21 (WHOOP)
    calories: int | None
    workouts: list[WorkoutData] = field(default_factory=list)


@dataclass
class DailyHealth:
    source: str                    # "whoop" | "oura" | "apple_health"
    date: str                      # ISO date "YYYY-MM-DD"
    fetched_at: str                # ISO datetime UTC
    sleep: SleepData | None = None
    recovery: RecoveryData | None = None
    activity: ActivityData | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for MCP tool responses."""
        return {
            "source": self.source,
            "date": self.date,
            "fetched_at": self.fetched_at,
            "sleep": _dataclass_to_dict(self.sleep),
            "recovery": _dataclass_to_dict(self.recovery),
            "activity": _activity_to_dict(self.activity),
        }


def _dataclass_to_dict(obj) -> dict | None:
    if obj is None:
        return None
    return {k: v for k, v in obj.__dict__.items()}


def _activity_to_dict(obj: ActivityData | None) -> dict | None:
    if obj is None:
        return None
    return {
        "strain": obj.strain,
        "calories": obj.calories,
        "workouts": [_dataclass_to_dict(w) for w in obj.workouts],
    }
