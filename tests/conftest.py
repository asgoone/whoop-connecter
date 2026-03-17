import pytest
from whoop.schema.unified import (
    ActivityData,
    DailyHealth,
    RecoveryData,
    SleepData,
    WorkoutData,
)


# ---------------------------------------------------------------------------
# Fixture builders — match REAL WHOOP API flat format
# ---------------------------------------------------------------------------

def make_recovery(score=70, hrv=55.0, rhr=58, spo2=97.0, score_state="SCORED"):
    """Build a recovery record matching the real WHOOP API flat format."""
    return {
        "cycle_id": 1123456789,
        "sleep_id": 2234567890,
        "created_at": "2026-03-16T04:35:12.123Z",
        "updated_at": "2026-03-16T04:42:01.456Z",
        "score_state": score_state,
        "user_calibrating": False,
        "recovery_score": score,
        "resting_heart_rate": rhr,
        "hrv_rmssd_milli": hrv,
        "spo2_percentage": spo2,
        "skin_temp_celsius": 0.0,
    }


def make_recovery_nested(score=70.0, hrv=55.0, rhr=58, spo2=97.0):
    """Build a recovery record in the nested (docs-style) format for compatibility."""
    return {
        "score": {
            "recovery_score": score,
            "hrv_rmssd_milli": hrv,
            "resting_heart_rate": rhr,
            "spo2_percentage": spo2,
            "skin_temp_celsius": None,
        }
    }


def make_sleep(score=82, efficiency=91.0, in_bed_ms=28440000,
               light_ms=13320000, sws_ms=4860000, rem_ms=4320000,
               awake_ms=1800000, score_state="SCORED"):
    """Build a sleep record matching the real WHOOP API flat format."""
    return {
        "id": 2234567890,
        "created_at": "2026-03-16T04:35:11.000Z",
        "updated_at": "2026-03-16T04:41:58.000Z",
        "score_state": score_state,
        "sleep_performance_percentage": score,
        "respiratory_rate": 14.6,
        "sleep_consistency_percentage": 76,
        "sleep_efficiency_percentage": efficiency,
        "total_in_bed_time_milli": in_bed_ms,
        "total_awake_time_milli": awake_ms,
        "total_light_sleep_time_milli": light_ms,
        "total_slow_wave_sleep_time_milli": sws_ms,
        "total_rem_sleep_time_milli": rem_ms,
        "sleep_cycle_count": 5,
    }


def make_sleep_nested(score=82, duration_start="2026-03-17T00:00:00Z",
                      duration_end="2026-03-17T07:30:00Z", efficiency=91.0, nap=False):
    """Build a sleep record in the nested (docs-style) format for compatibility."""
    return {
        "start": duration_start,
        "end": duration_end,
        "nap": nap,
        "score": {
            "sleep_performance_percentage": score,
            "sleep_efficiency_percentage": efficiency,
            "stage_summary": {
                "total_light_sleep_time_milli": 100,
                "total_rem_sleep_time_milli": 90,
                "total_slow_wave_sleep_time_milli": 80,
            },
        },
    }


def make_workout(sport_id=0, strain=12.5, start="2026-03-17T08:00:00Z",
                 end="2026-03-17T09:00:00Z", avg_hr=145, max_hr=178, kj=2000.0):
    """Build a workout record — uses nested score (matches both flat and nested)."""
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


def make_cycle(strain=10.5, kj=8000.0, date="2026-03-17"):
    """Build a cycle record matching the real WHOOP API flat format."""
    return {
        "id": 1123456789,
        "start": f"{date}T19:20:00.000Z",
        "end": f"{date}T04:35:00.000Z",
        "score_state": "SCORED",
        "strain": strain,
        "kilojoule": kj,
        "average_heart_rate": 67,
        "max_heart_rate": 144,
    }


def make_cycle_nested(strain=10.5, kj=8000.0):
    """Build a cycle record in the nested format for compatibility."""
    return {"start": "2026-03-17T06:00:00Z", "score": {"strain": strain, "kilojoule": kj}}


def make_body_measurement(height=1.803, weight=82.5, max_hr=195):
    """Build a body measurement record matching the WHOOP API format."""
    return {
        "height_meter": height,
        "weight_kilogram": weight,
        "max_heart_rate": max_hr,
    }


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
