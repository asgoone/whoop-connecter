import pytest
from whoop.schema.unified import (
    ActivityData,
    DailyHealth,
    RecoveryData,
    SleepData,
    WorkoutData,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_recovery(score=70.0, hrv=55.0, rhr=58, spo2=97.0):
    return {
        "score": {
            "recovery_score": score,
            "hrv_rmssd_milli": hrv,
            "resting_heart_rate": rhr,
            "spo2_percentage": spo2,
            "skin_temp_celsius": None,
        }
    }


def make_sleep(score=82, duration_start="2026-03-17T00:00:00Z", duration_end="2026-03-17T07:30:00Z",
               efficiency=91.0, nap=False):
    return {
        "start": duration_start,
        "end": duration_end,
        "nap": nap,
        "score": {
            "sleep_performance_percentage": score,
            "sleep_efficiency_percentage": efficiency,
            "stage_summary": {"light": 100, "rem": 90, "slow_wave": 80},
        },
    }


def make_workout(sport_id=0, strain=12.5, start="2026-03-17T08:00:00Z",
                 end="2026-03-17T09:00:00Z", avg_hr=145, max_hr=178, kj=2000.0):
    return {
        "sport_id": sport_id,
        "start": start,
        "end": end,
        "score": {
            "strain": strain,
            "average_heart_rate": avg_hr,
            "max_heart_rate": max_hr,
            "kilojoule": kj,
        },
    }


def make_cycle(strain=10.5, kj=8000.0):
    return {"start": "2026-03-17T06:00:00Z", "score": {"strain": strain, "kilojoule": kj}}


def make_daily_health(date="2026-03-17", recovery_score=70, sleep_score=82,
                      hrv=55.0, rhr=58, strain=10.5):
    return DailyHealth(
        source="whoop",
        date=date,
        fetched_at="2026-03-17T08:00:00Z",
        sleep=SleepData(score=sleep_score, duration_hours=7.5, efficiency=0.91, stages={}),
        recovery=RecoveryData(score=recovery_score, hrv_rmssd=hrv, resting_hr=rhr, spo2=97.0),
        activity=ActivityData(strain=strain, calories=1900, workouts=[]),
    )
