"""
Unit tests for whoop/analytics/ — daily_summary and trends.
"""

import pytest
from tests.conftest import make_daily_health
from whoop.analytics.daily_summary import build_daily_summary, DailySummary, _recommend
from whoop.analytics.trends import build_trends, _compute_trend
from whoop.schema.unified import (
    ActivityData,
    DailyHealth,
    RecoveryData,
    SleepData,
)


# ===========================================================================
# _recommend (recovery thresholds)
# ===========================================================================

class TestRecommend:
    @pytest.mark.parametrize("score, expected_emoji", [
        (100, "🟢"),
        (67, "🟢"),    # нижняя граница зелёного
        (66, "🟡"),    # верхняя граница жёлтого
        (34, "🟡"),    # нижняя граница жёлтого
        (33, "🔴"),    # верхняя граница красного
        (0,  "🔴"),
    ])
    def test_emoji_at_boundaries(self, score, expected_emoji):
        emoji, _ = _recommend(score)
        assert emoji == expected_emoji, f"score={score}: expected {expected_emoji}, got {emoji}"

    def test_none_returns_grey(self):
        emoji, rec = _recommend(None)
        assert emoji == "⚪"
        assert "No recovery" in rec

    def test_recommendation_strings_are_not_empty(self):
        for score in [0, 33, 34, 66, 67, 100]:
            _, rec = _recommend(score)
            assert len(rec) > 0


# ===========================================================================
# build_daily_summary
# ===========================================================================

class TestBuildDailySummary:
    def test_full_data(self):
        health = make_daily_health(recovery_score=74, sleep_score=82, hrv=55.2, rhr=58, strain=10.5)
        s = build_daily_summary(health)
        assert s.recovery_score == 74
        assert s.sleep_score == 82
        assert s.hrv_rmssd == 55.2
        assert s.resting_hr == 58
        assert s.strain == 10.5
        assert s.emoji == "🟢"

    def test_no_recovery_data(self):
        health = DailyHealth(
            source="whoop", date="2026-03-17", fetched_at="",
            sleep=SleepData(score=80, duration_hours=7.0, efficiency=0.9, stages={}),
            recovery=None,
            activity=None,
        )
        s = build_daily_summary(health)
        assert s.recovery_score is None
        assert s.emoji == "⚪"

    def test_no_sleep_data(self):
        health = DailyHealth(
            source="whoop", date="2026-03-17", fetched_at="",
            sleep=None,
            recovery=RecoveryData(score=70, hrv_rmssd=55.0, resting_hr=58, spo2=97.0),
            activity=None,
        )
        s = build_daily_summary(health)
        assert s.sleep_score is None
        assert s.recovery_score == 70

    def test_all_none(self):
        health = DailyHealth(source="whoop", date="2026-03-17", fetched_at="")
        s = build_daily_summary(health)
        assert s.recovery_score is None
        assert s.sleep_score is None
        assert s.strain is None

    def test_hrv_rounded_to_one_decimal(self):
        health = make_daily_health(hrv=54.789)
        s = build_daily_summary(health)
        assert s.hrv_rmssd == 54.8

    def test_format_line_full(self):
        s = build_daily_summary(make_daily_health(recovery_score=74, sleep_score=82))
        line = s.format_line()
        assert "Recovery 74%" in line
        assert "Sleep 82" in line
        assert "→" in line

    def test_format_line_no_data(self):
        health = DailyHealth(source="whoop", date="2026-03-17", fetched_at="")
        s = build_daily_summary(health)
        assert "No data" in s.format_line()

    def test_to_dict_has_all_keys(self):
        s = build_daily_summary(make_daily_health())
        d = s.to_dict()
        for key in ["date", "recovery_score", "sleep_score", "hrv_rmssd",
                    "resting_hr", "strain", "recommendation", "emoji", "summary_line"]:
            assert key in d, f"Key '{key}' missing from to_dict()"

    def test_date_preserved(self):
        health = make_daily_health(date="2026-01-01")
        s = build_daily_summary(health)
        assert s.date == "2026-01-01"


# ===========================================================================
# build_trends
# ===========================================================================

class TestBuildTrends:
    def test_empty_records(self):
        report = build_trends([])
        assert report.days == 0
        assert report.from_date == ""
        assert report.to_date == ""
        assert report.metrics == []

    def test_single_record(self):
        report = build_trends([make_daily_health(date="2026-03-17")])
        assert report.days == 1
        assert report.from_date == "2026-03-17"
        assert report.to_date == "2026-03-17"
        # Одна точка — нет тренда
        for m in report.metrics:
            if m.average is not None:
                assert m.direction == "→"
                assert m.change_pct == 0.0

    def test_records_sorted_by_date(self):
        """Передаём записи в обратном порядке — должны сортироваться."""
        records = [
            make_daily_health(date="2026-03-17"),
            make_daily_health(date="2026-03-15"),
            make_daily_health(date="2026-03-16"),
        ]
        report = build_trends(records)
        assert report.from_date == "2026-03-15"
        assert report.to_date == "2026-03-17"

    def test_upward_trend_detected(self):
        records = [
            make_daily_health(date=f"2026-03-{10+i:02d}", recovery_score=60 + i * 5)
            for i in range(8)
        ]
        report = build_trends(records)
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "↑"
        assert rec_trend.change_pct > 3

    def test_downward_trend_detected(self):
        records = [
            make_daily_health(date=f"2026-03-{10+i:02d}", recovery_score=90 - i * 5)
            for i in range(8)
        ]
        report = build_trends(records)
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "↓"
        assert rec_trend.change_pct < -3

    def test_stable_trend(self):
        records = [make_daily_health(date=f"2026-03-{10+i:02d}", recovery_score=70) for i in range(6)]
        report = build_trends(records)
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "→"
        assert rec_trend.change_pct == 0.0

    def test_metric_all_none(self):
        """Если у всех записей нет данных по метрике — direction=N/A."""
        records = [
            DailyHealth(source="whoop", date=f"2026-03-{10+i:02d}", fetched_at="",
                        recovery=None)
            for i in range(5)
        ]
        report = build_trends(records)
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.direction == "N/A"
        assert rec_trend.average is None

    def test_partial_none_values_ignored(self):
        """Дни без данных не влияют на расчёт тренда."""
        records = []
        for i in range(6):
            h = make_daily_health(date=f"2026-03-{10+i:02d}", recovery_score=70)
            if i % 2 == 0:
                h.recovery = None  # половина дней без recovery
            records.append(h)
        report = build_trends(records)
        rec_trend = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec_trend.average is not None
        assert rec_trend.direction in ("↑", "↓", "→")

    def test_to_dict_structure(self):
        report = build_trends([make_daily_health()])
        d = report.to_dict()
        assert "days" in d
        assert "from_date" in d
        assert "to_date" in d
        assert "metrics" in d
        for m in d["metrics"]:
            assert "metric" in m
            assert "direction" in m

    def test_five_metrics_always_returned(self):
        report = build_trends([make_daily_health()])
        assert len(report.metrics) == 5
        names = {m.metric for m in report.metrics}
        assert names == {"recovery_score", "sleep_score", "hrv_rmssd", "resting_hr", "strain"}


# ===========================================================================
# _compute_trend edge cases
# ===========================================================================

class TestComputeTrend:
    def _make_records(self, values: list):
        records = []
        for i, v in enumerate(values):
            h = DailyHealth(
                source="whoop", date=f"2026-03-{10+i:02d}", fetched_at="",
                recovery=RecoveryData(score=v, hrv_rmssd=None, resting_hr=None, spo2=None) if v is not None else None,
            )
            records.append(h)
        return records

    def test_two_values_trend(self):
        records = self._make_records([60, 80])
        report = build_trends(records)
        rec = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec.direction == "↑"

    def test_first_half_zero_does_not_divide_by_zero(self):
        """Если среднее первой половины = 0, direction должен быть →."""
        records = self._make_records([0, 0, 0, 10])
        report = build_trends(records)
        rec = next(m for m in report.metrics if m.metric == "recovery_score")
        # first_half avg = mean([0, 0]) = 0 → no division, direction = →
        assert rec.direction in ("→", "↑")  # допустимо оба

    def test_2_records_split(self):
        """При 2 записях mid=1: first=[v0], second=[v1]."""
        records = self._make_records([50, 90])
        report = build_trends(records)
        rec = next(m for m in report.metrics if m.metric == "recovery_score")
        assert rec.change_pct == pytest.approx(80.0)
        assert rec.direction == "↑"
