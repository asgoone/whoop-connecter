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
#
# *_v2      — the real WHOOP API v2 shape (verified against live responses
#             and the v2 OpenAPI spec): metrics nested under "score",
#             records carry cycle_id / timezone_offset. Prefer these.
# *_flat    — legacy flat shape with metrics at the top level. WHOOP v2 does
#             NOT return it; kept only to test the mapper's flat fallback.
# *_nested  — minimal nested records for focused mapper edge cases.
# ---------------------------------------------------------------------------

def make_cycle_v2(cycle_id=1, start="2026-03-17T00:20:00.000Z", end="2026-03-17T23:40:00.000Z",
                  tz="+00:00", strain=10.5, kj=8000.0, avg_hr=67, max_hr=144,
                  score_state="SCORED"):
    return {
        "id": cycle_id, "user_id": 1,
        "created_at": start, "updated_at": start,
        "start": start, "end": end, "timezone_offset": tz,
        "score_state": score_state, "step_count": 8000,
        "score": {"strain": strain, "kilojoule": kj,
                  "average_heart_rate": avg_hr, "max_heart_rate": max_hr},
    }


def make_sleep_v2(sleep_id=456, cycle_id=1, start="2026-03-17T00:00:00.000Z",
                  end="2026-03-17T07:30:00.000Z", tz="+00:00", score=82, efficiency=91.0,
                  in_bed_ms=27_000_000, light_ms=13_000_000, sws_ms=5_000_000,
                  rem_ms=4_000_000, awake_ms=1_800_000, respiratory_rate=14.6,
                  sleep_consistency=76, sleep_needed=None, nap=False,
                  score_state="SCORED", created_at=None):
    if sleep_needed is None:
        sleep_needed = {"baseline_milli": 27_000_000, "need_from_sleep_debt_milli": 1_200_000,
                        "need_from_recent_strain_milli": 300_000, "need_from_recent_nap_milli": 0}
    return {
        "id": sleep_id, "v1_id": None, "cycle_id": cycle_id, "user_id": 1,
        "created_at": created_at or end, "updated_at": created_at or end,
        "start": start, "end": end, "timezone_offset": tz, "nap": nap,
        "score_state": score_state,
        "score": {
            "stage_summary": {
                "total_in_bed_time_milli": in_bed_ms,
                "total_awake_time_milli": awake_ms,
                "total_no_data_time_milli": 0,
                "total_light_sleep_time_milli": light_ms,
                "total_slow_wave_sleep_time_milli": sws_ms,
                "total_rem_sleep_time_milli": rem_ms,
                "sleep_cycle_count": 5,
                "disturbance_count": 3,
            },
            "sleep_needed": sleep_needed,
            "respiratory_rate": respiratory_rate,
            "sleep_performance_percentage": score,
            "sleep_consistency_percentage": sleep_consistency,
            "sleep_efficiency_percentage": efficiency,
        },
    }


def make_recovery_v2(cycle_id=1, sleep_id=456, created_at="2026-03-17T06:00:00.000Z",
                     score=70, hrv=55.0, rhr=58, spo2=97.0, skin_temp=33.5,
                     score_state="SCORED"):
    return {
        "cycle_id": cycle_id, "sleep_id": sleep_id, "user_id": 1,
        "created_at": created_at, "updated_at": created_at,
        "score_state": score_state,
        "score": {"user_calibrating": False, "recovery_score": score,
                  "resting_heart_rate": rhr, "hrv_rmssd_milli": hrv,
                  "spo2_percentage": spo2, "skin_temp_celsius": skin_temp},
    }


def make_workout_v2(workout_id="7f3c1a52-0000-4000-8000-000000000001", sport_id=0,
                    sport_name="running", start="2026-03-17T08:00:00.000Z",
                    end="2026-03-17T09:00:00.000Z", tz="+00:00", strain=12.0, avg_hr=145,
                    max_hr=178, kj=2000.0, distance=5000.0, altitude_gain=120.0,
                    percent_recorded=98.5, zone_durations=None):
    if zone_durations is None:
        zone_durations = {"zone_zero_milli": 60_000, "zone_one_milli": 600_000,
                          "zone_two_milli": 1_200_000, "zone_three_milli": 1_200_000,
                          "zone_four_milli": 540_000, "zone_five_milli": 0}
    return {
        "id": workout_id, "v1_id": None, "user_id": 1,
        "created_at": end, "updated_at": end,
        "start": start, "end": end, "timezone_offset": tz,
        "sport_name": sport_name, "sport_id": sport_id, "score_state": "SCORED",
        "score": {"strain": strain, "average_heart_rate": avg_hr, "max_heart_rate": max_hr,
                  "kilojoule": kj, "percent_recorded": percent_recorded,
                  "distance_meter": distance, "altitude_gain_meter": altitude_gain,
                  "altitude_change_meter": 10.0, "zone_durations": zone_durations},
    }


def make_recovery_flat(score=70, hrv=55.0, rhr=58, spo2=97.0, score_state="SCORED"):
    """Legacy flat recovery (not returned by WHOOP v2)."""
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
    """Minimal nested recovery (v2 shape, score fields only)."""
    return {
        "score": {
            "recovery_score": score,
            "hrv_rmssd_milli": hrv,
            "resting_heart_rate": rhr,
            "spo2_percentage": spo2,
            "skin_temp_celsius": None,
        }
    }


def make_sleep_flat(score=82, efficiency=91.0, in_bed_ms=28440000,
               light_ms=13320000, sws_ms=4860000, rem_ms=4320000,
               awake_ms=1800000, score_state="SCORED",
               respiratory_rate=14.6, sleep_consistency=76,
               sleep_needed=None):
    """Legacy flat sleep (not returned by WHOOP v2)."""
    data = {
        "id": 2234567890,
        "created_at": "2026-03-16T04:35:11.000Z",
        "updated_at": "2026-03-16T04:41:58.000Z",
        "score_state": score_state,
        "sleep_performance_percentage": score,
        "respiratory_rate": respiratory_rate,
        "sleep_consistency_percentage": sleep_consistency,
        "sleep_efficiency_percentage": efficiency,
        "total_in_bed_time_milli": in_bed_ms,
        "total_awake_time_milli": awake_ms,
        "total_light_sleep_time_milli": light_ms,
        "total_slow_wave_sleep_time_milli": sws_ms,
        "total_rem_sleep_time_milli": rem_ms,
        "sleep_cycle_count": 5,
    }
    if sleep_needed is not None:
        data["sleep_needed"] = sleep_needed
    return data


def make_sleep_nested(score=82, duration_start="2026-03-17T00:00:00Z",
                      duration_end="2026-03-17T07:30:00Z", efficiency=91.0, nap=False):
    """Minimal nested sleep (v2 shape, score fields only)."""
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
                 end="2026-03-17T09:00:00Z", avg_hr=145, max_hr=178, kj=2000.0,
                 distance=None, altitude_gain=None, percent_recorded=None,
                 zone_durations=None):
    """Minimal nested workout (v2 score shape without ids)."""
    score = {
        "strain": strain,
        "average_heart_rate": avg_hr,
        "max_heart_rate": max_hr,
        "kilojoule": kj,
    }
    if distance is not None:
        score["distance_meter"] = distance
    if altitude_gain is not None:
        score["altitude_gain_meter"] = altitude_gain
    if percent_recorded is not None:
        score["percent_recorded"] = percent_recorded
    if zone_durations is not None:
        score["zone_durations"] = zone_durations

    return {
        "sport_id": sport_id,
        "start": start,
        "end": end,
        "score": score,
    }


def make_cycle_flat(strain=10.5, kj=8000.0, date="2026-03-17"):
    """Legacy flat cycle (not returned by WHOOP v2)."""
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
    """Minimal nested cycle (v2 score shape)."""
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
