"""
Unit tests for whoop/schema/mappers.py
Tests both flat (real API) and nested (docs-style) response formats.
"""

import pytest
from tests.conftest import (
    make_body_measurement,
    make_recovery, make_recovery_nested,
    make_sleep, make_sleep_nested,
    make_workout, make_cycle, make_cycle_nested,
)
from whoop.schema.mappers import (
    map_body_measurement,
    map_recovery,
    map_sleep,
    map_workout,
    map_cycle,
    _duration_ms,
    _sport_name,
)


# ===========================================================================
# map_recovery — flat format (real API)
# ===========================================================================

class TestMapRecoveryFlat:
    def test_typical_green_state(self):
        r = map_recovery(make_recovery(score=75))
        assert r.score == 75
        assert r.hrv_rmssd == 55.0
        assert r.resting_hr == 58
        assert r.spo2 == 97.0

    def test_score_zero_is_valid(self):
        """Score 0 is falsy — must NOT be treated as None."""
        r = map_recovery(make_recovery(score=0))
        assert r.score == 0

    def test_score_100_max(self):
        r = map_recovery(make_recovery(score=100))
        assert r.score == 100

    def test_pending_score_returns_all_none(self):
        r = map_recovery(make_recovery(score_state="PENDING_SCORE"))
        assert r.score is None
        assert r.hrv_rmssd is None

    def test_unscorable_returns_all_none(self):
        r = map_recovery(make_recovery(score_state="UNSCORABLE"))
        assert r.score is None

    def test_skin_temp_zero_preserved(self):
        """skin_temp_celsius=0.0 should be preserved as 0.0, not None."""
        r = map_recovery(make_recovery())
        assert r.skin_temp_deviation == 0.0


# ===========================================================================
# map_recovery — nested format (docs/legacy)
# ===========================================================================

class TestMapRecoveryNested:
    def test_typical_nested(self):
        r = map_recovery(make_recovery_nested(score=74.6))
        assert r.score == 75
        assert r.hrv_rmssd == 55.0
        assert r.resting_hr == 58

    def test_rounds_score_half_up(self):
        assert map_recovery(make_recovery_nested(score=55.5)).score == 56
        assert map_recovery(make_recovery_nested(score=55.4)).score == 55

    def test_no_score_object_returns_none(self):
        r = map_recovery({})
        assert r.score is None
        assert r.hrv_rmssd is None
        assert r.resting_hr is None

    def test_score_object_present_but_no_recovery_score(self):
        r = map_recovery({"score": {"hrv_rmssd_milli": 50.0}})
        assert r.score is None
        assert r.hrv_rmssd == 50.0

    def test_optional_fields_can_be_none(self):
        data = {"score": {"recovery_score": 70.0}}
        r = map_recovery(data)
        assert r.spo2 is None
        assert r.skin_temp_deviation is None


# ===========================================================================
# map_sleep — flat format (real API)
# ===========================================================================

class TestMapSleepFlat:
    def test_typical_sleep(self):
        s = map_sleep(make_sleep(score=82, efficiency=91.0))
        assert s.score == 82
        assert s.efficiency == pytest.approx(0.91)

    def test_duration_from_in_bed_time(self):
        # 28440000 ms = 7.9 hours
        s = map_sleep(make_sleep(in_bed_ms=28440000))
        assert s.duration_hours == pytest.approx(7.9, abs=0.01)

    def test_stages_from_flat_fields(self):
        s = map_sleep(make_sleep(light_ms=13320000, sws_ms=4860000, rem_ms=4320000))
        assert "total_light_sleep_time_milli" in s.stages
        assert s.stages["total_light_sleep_time_milli"] == 13320000

    def test_pending_score_returns_empty(self):
        s = map_sleep(make_sleep(score_state="PENDING_SCORE"))
        assert s.score is None
        assert s.duration_hours is None
        assert s.efficiency is None

    def test_efficiency_zero_is_zero(self):
        s = map_sleep(make_sleep(efficiency=0.0))
        assert s.efficiency == 0.0


# ===========================================================================
# map_sleep — nested format (docs/legacy)
# ===========================================================================

class TestMapSleepNested:
    def test_typical_nested(self):
        s = map_sleep(make_sleep_nested(score=85, efficiency=91.0))
        assert s.score == 85
        assert s.efficiency == pytest.approx(0.91)
        assert s.duration_hours == pytest.approx(7.5, abs=0.01)

    def test_duration_calculated_correctly(self):
        data = make_sleep_nested(
            duration_start="2026-03-17T00:00:00Z",
            duration_end="2026-03-17T08:00:00Z",
        )
        s = map_sleep(data)
        assert s.duration_hours == pytest.approx(8.0)

    def test_no_start_or_end_yields_none_duration(self):
        data = {"score": {"sleep_performance_percentage": 80, "sleep_efficiency_percentage": 90}}
        s = map_sleep(data)
        assert s.duration_hours is None

    def test_missing_score_block(self):
        s = map_sleep({"start": "2026-03-17T00:00:00Z", "end": "2026-03-17T07:00:00Z"})
        assert s.score is None
        assert s.efficiency is None
        assert s.duration_hours == pytest.approx(7.0)

    def test_stage_summary_preserved(self):
        s = map_sleep(make_sleep_nested())
        assert "total_light_sleep_time_milli" in s.stages

    def test_empty_dict_does_not_raise(self):
        s = map_sleep({})
        assert s.score is None
        assert s.duration_hours is None


# ===========================================================================
# map_workout
# ===========================================================================

class TestMapWorkout:
    def test_typical_workout(self):
        w = map_workout(make_workout(sport_id=0, strain=12.5, kj=2000.0))
        assert w.sport == "Running"
        assert w.strain == 12.5
        assert w.calories == int(2000.0 / 4.184)
        assert w.avg_hr == 145
        assert w.max_hr == 178

    def test_duration_in_minutes(self):
        w = map_workout(make_workout(
            start="2026-03-17T08:00:00Z",
            end="2026-03-17T09:30:00Z",
        ))
        assert w.duration_minutes == pytest.approx(90.0)

    def test_unknown_sport_id(self):
        w = map_workout(make_workout(sport_id=9999))
        assert w.sport == "Sport 9999"

    def test_sport_name_from_api_preferred(self):
        data = {**make_workout(sport_id=0), "sport_name": "Trail Running"}
        w = map_workout(data)
        assert w.sport == "Trail Running"

    def test_default_sport_id_minus_one(self):
        data = {**make_workout(), "sport_id": -1}
        w = map_workout(data)
        assert w.sport == "Activity"

    def test_zero_kilojoules_gives_zero_calories(self):
        data = make_workout(kj=0.0)
        w = map_workout(data)
        assert w.calories == 0

    def test_no_end_time_yields_none_duration(self):
        data = {
            "sport_id": 0,
            "start": "2026-03-17T08:00:00Z",
            "score": {"strain": 10.0},
        }
        w = map_workout(data)
        assert w.duration_minutes is None

    def test_no_score_block(self):
        data = {
            "sport_id": 45,
            "start": "2026-03-17T08:00:00Z",
            "end": "2026-03-17T09:00:00Z",
        }
        w = map_workout(data)
        assert w.sport == "Weightlifting"
        assert w.strain is None
        assert w.calories is None


# ===========================================================================
# map_cycle — flat format (real API)
# ===========================================================================

class TestMapCycleFlat:
    def test_typical_cycle(self):
        a = map_cycle(make_cycle(strain=10.5, kj=8000.0))
        assert a.strain == 10.5
        assert a.calories == int(8000.0 / 4.184)
        assert a.workouts == []

    def test_empty_cycle(self):
        a = map_cycle({})
        assert a.strain is None
        assert a.calories is None


# ===========================================================================
# map_cycle — nested format (legacy)
# ===========================================================================

class TestMapCycleNested:
    def test_typical_cycle(self):
        a = map_cycle(make_cycle_nested(strain=10.5, kj=8000.0))
        assert a.strain == 10.5
        assert a.calories == int(8000.0 / 4.184)

    def test_no_score_block(self):
        a = map_cycle({"start": "2026-03-17T06:00:00Z"})
        assert a.strain is None


# ===========================================================================
# _duration_ms
# ===========================================================================

class TestDurationMs:
    def test_exactly_one_hour(self):
        ms = _duration_ms("2026-03-17T08:00:00Z", "2026-03-17T09:00:00Z")
        assert ms == 3_600_000

    def test_7_5_hours(self):
        ms = _duration_ms("2026-03-17T00:00:00Z", "2026-03-17T07:30:00Z")
        assert ms == 7 * 3_600_000 + 30 * 60_000

    def test_end_before_start_returns_negative(self):
        ms = _duration_ms("2026-03-17T09:00:00Z", "2026-03-17T08:00:00Z")
        assert ms == -3_600_000

    def test_same_time_returns_zero(self):
        ms = _duration_ms("2026-03-17T08:00:00Z", "2026-03-17T08:00:00Z")
        assert ms == 0

    def test_invalid_iso_returns_none(self):
        assert _duration_ms("not-a-date", "2026-03-17T08:00:00Z") is None
        assert _duration_ms("2026-03-17T08:00:00Z", "garbage") is None

    def test_empty_string_returns_none(self):
        assert _duration_ms("", "2026-03-17T08:00:00Z") is None


# ===========================================================================
# _sport_name
# ===========================================================================

class TestSportName:
    def test_known_sports(self):
        assert _sport_name(0) == "Running"
        assert _sport_name(1) == "Cycling"
        assert _sport_name(44) == "Yoga"
        assert _sport_name(-1) == "Activity"
        assert _sport_name(73) == "HIIT"

    def test_unknown_sport_id(self):
        assert _sport_name(12345) == "Sport 12345"
        assert _sport_name(2) == "Sport 2"

    def test_boundary_sport_ids(self):
        assert _sport_name(101) == "Volleyball (Beach)"
        assert _sport_name(102) == "Sport 102"


# ===========================================================================
# map_body_measurement
# ===========================================================================

class TestMapBodyMeasurement:
    def test_typical_measurement(self):
        b = map_body_measurement(make_body_measurement())
        assert b.height_meter == 1.8
        assert b.weight_kilogram == 82.5
        assert b.max_heart_rate == 195

    def test_height_rounded_to_two_decimals(self):
        b = map_body_measurement(make_body_measurement(height=1.8567))
        assert b.height_meter == 1.86

    def test_weight_rounded_to_one_decimal(self):
        b = map_body_measurement(make_body_measurement(weight=82.456))
        assert b.weight_kilogram == 82.5

    def test_max_hr_is_int(self):
        b = map_body_measurement(make_body_measurement(max_hr=195.6))
        assert b.max_heart_rate == 195
        assert isinstance(b.max_heart_rate, int)

    def test_empty_dict_returns_all_none(self):
        b = map_body_measurement({})
        assert b.height_meter is None
        assert b.weight_kilogram is None
        assert b.max_heart_rate is None

    def test_partial_data(self):
        b = map_body_measurement({"weight_kilogram": 75.0})
        assert b.weight_kilogram == 75.0
        assert b.height_meter is None
        assert b.max_heart_rate is None

    def test_zero_values_not_treated_as_none(self):
        b = map_body_measurement({"height_meter": 0.0, "weight_kilogram": 0.0, "max_heart_rate": 0})
        assert b.height_meter == 0.0
        assert b.weight_kilogram == 0.0
        assert b.max_heart_rate == 0

    def test_to_dict(self):
        b = map_body_measurement(make_body_measurement())
        d = b.to_dict()
        assert "height_meter" in d
        assert "weight_kilogram" in d
        assert "max_heart_rate" in d
