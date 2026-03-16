"""
Computes trends over a list of DailyHealth records.
Returns direction indicators and averages for key metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Callable

from ..schema.unified import DailyHealth


@dataclass
class MetricTrend:
    metric: str
    average: float | None
    first_half_avg: float | None
    second_half_avg: float | None
    direction: str  # "↑" | "↓" | "→" | "N/A"
    change_pct: float | None


@dataclass
class TrendReport:
    days: int
    from_date: str
    to_date: str
    metrics: list[MetricTrend]

    def to_dict(self) -> dict:
        return {
            "days": self.days,
            "from_date": self.from_date,
            "to_date": self.to_date,
            "metrics": [
                {
                    "metric": m.metric,
                    "average": m.average,
                    "direction": m.direction,
                    "change_pct": m.change_pct,
                }
                for m in self.metrics
            ],
        }


def build_trends(records: list[DailyHealth]) -> TrendReport:
    if not records:
        return TrendReport(days=0, from_date="", to_date="", metrics=[])

    sorted_records = sorted(records, key=lambda r: r.date)
    from_date = sorted_records[0].date
    to_date = sorted_records[-1].date

    extractors: list[tuple[str, Callable[[DailyHealth], float | None]]] = [
        ("recovery_score", lambda r: r.recovery.score if r.recovery else None),
        ("sleep_score", lambda r: r.sleep.score if r.sleep else None),
        ("hrv_rmssd", lambda r: r.recovery.hrv_rmssd if r.recovery else None),
        ("resting_hr", lambda r: r.recovery.resting_hr if r.recovery else None),
        ("strain", lambda r: r.activity.strain if r.activity else None),
    ]

    metrics = [
        _compute_trend(name, sorted_records, fn)
        for name, fn in extractors
    ]

    return TrendReport(
        days=len(sorted_records),
        from_date=from_date,
        to_date=to_date,
        metrics=metrics,
    )


def _compute_trend(
    name: str,
    records: list[DailyHealth],
    extractor: Callable[[DailyHealth], float | None],
) -> MetricTrend:
    values = [v for r in records if (v := extractor(r)) is not None]

    if not values:
        return MetricTrend(
            metric=name,
            average=None,
            first_half_avg=None,
            second_half_avg=None,
            direction="N/A",
            change_pct=None,
        )

    avg = round(mean(values), 2)

    if len(values) < 2:
        return MetricTrend(
            metric=name,
            average=avg,
            first_half_avg=avg,
            second_half_avg=avg,
            direction="→",
            change_pct=0.0,
        )

    mid = len(values) // 2
    first_half = mean(values[:mid])
    second_half = mean(values[mid:])

    if first_half == 0:
        direction, change_pct = "→", 0.0
    else:
        change_pct = round(((second_half - first_half) / abs(first_half)) * 100, 1)
        if change_pct > 3:
            direction = "↑"
        elif change_pct < -3:
            direction = "↓"
        else:
            direction = "→"

    return MetricTrend(
        metric=name,
        average=avg,
        first_half_avg=round(first_half, 2),
        second_half_avg=round(second_half, 2),
        direction=direction,
        change_pct=change_pct,
    )
