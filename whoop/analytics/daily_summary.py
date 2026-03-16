"""
Aggregates sleep + recovery + activity into a single day summary
with a human-readable recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema.unified import DailyHealth


@dataclass
class DailySummary:
    date: str
    recovery_score: int | None
    sleep_score: int | None
    hrv_rmssd: float | None
    resting_hr: int | None
    strain: float | None
    recommendation: str
    emoji: str

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "recovery_score": self.recovery_score,
            "sleep_score": self.sleep_score,
            "hrv_rmssd": self.hrv_rmssd,
            "resting_hr": self.resting_hr,
            "strain": self.strain,
            "recommendation": self.recommendation,
            "emoji": self.emoji,
            "summary_line": self.format_line(),
        }

    def format_line(self) -> str:
        parts = []
        if self.recovery_score is not None:
            parts.append(f"Recovery {self.recovery_score}%")
        if self.sleep_score is not None:
            parts.append(f"Sleep {self.sleep_score}")
        if self.hrv_rmssd is not None:
            parts.append(f"HRV {self.hrv_rmssd:.0f}")
        if self.resting_hr is not None:
            parts.append(f"RHR {self.resting_hr}")
        if self.strain is not None:
            parts.append(f"Strain {self.strain:.1f}")
        metrics = " | ".join(parts) if parts else "No data"
        return f"{self.emoji} {metrics}\n→ {self.recommendation}"


def build_daily_summary(health: DailyHealth) -> DailySummary:
    recovery_score = health.recovery.score if health.recovery else None
    sleep_score = health.sleep.score if health.sleep else None
    hrv = health.recovery.hrv_rmssd if health.recovery else None
    rhr = health.recovery.resting_hr if health.recovery else None
    strain = health.activity.strain if health.activity else None

    emoji, recommendation = _recommend(recovery_score)

    return DailySummary(
        date=health.date,
        recovery_score=recovery_score,
        sleep_score=sleep_score,
        hrv_rmssd=round(hrv, 1) if hrv is not None else None,
        resting_hr=rhr,
        strain=strain,
        recommendation=recommendation,
        emoji=emoji,
    )


def _recommend(recovery_score: int | None) -> tuple[str, str]:
    if recovery_score is None:
        return "⚪", "No recovery data available."
    if recovery_score >= 67:
        return "🟢", "Good recovery. You can train at full intensity."
    if recovery_score >= 34:
        return "🟡", "Moderate recovery. Keep intensity moderate, don't overdo it."
    return "🔴", "Low recovery. Prioritize rest or light activity only."
