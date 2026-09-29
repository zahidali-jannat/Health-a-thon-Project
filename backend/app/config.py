"""Detection and consent thresholds. Tune these numbers here, never inside the logic."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectionConfig:
    # --- Soft signals (need `soft_signals_needed` of them together) ---
    refill_late_days: int = 10               # flag if a refill is this many days late OR MORE
    default_days_supply: int = 30            # used when a refill row has no days-supply value

    engagement_window_days: int = 60         # compare last 60 days vs the 60 days before
    engagement_drop_pct: float = 40.0        # flag if logs fell by AT LEAST this percent
    engagement_min_baseline: int = 4         # need this many earlier logs to compare fairly

    hba1c_readings_to_compare: int = 3       # last 3 results (uses 2 if only 2 exist)
    hba1c_min_improvement: float = 0.3       # a drop smaller than this (% points) = "not improving"
    hba1c_target: float | None = 7.0         # below target a flat HbA1c is fine; None disables

    missed_visit_grace_days: int = 3         # a scheduled visit counts as missed this many days after its date

    screening_interval_days: dict[str, int] = field(
        default_factory=lambda: {"Eye": 365, "Foot": 365, "Kidney": 365})

    soft_signals_needed: int = 2

    # --- Hard signals (any one alone raises a HIGH PRIORITY flag) ---
    hypo_lookback_days: int = 90
    emergency_lookback_days: int = 90        # emergency visits since the last clinic visit, capped at this


@dataclass(frozen=True)
class ConsentConfig:
    request_expiry_minutes: int = 60 * 24    # patient must answer within this time, else EXPIRED
    access_days: int = 30                    # how long the care team may view the data once approved
    history_months: int = 12                 # records period requested from the other hospital


@dataclass(frozen=True)
class HbA1cStage:
    """One diabetes stage by HbA1c. Lower limit inclusive, upper limit exclusive; None = open-ended."""
    key: str
    label: str
    tone: str                       # ok | warn | alert - colour meaning in the UI (always shown with text)
    low_pct: float | None
    high_pct: float | None
    low_mmol: float | None
    high_mmol: float | None


# ADA diagnostic categories. These are clinical cut-offs for classifying a result, NOT a lab's reference range:
# the range printed on each report is still kept and shown separately.
HBA1C_STAGES_SOURCE = "ADA diagnostic categories"
HBA1C_STAGES = (
    HbA1cStage("normal", "Normal", "ok", None, 5.7, None, 39),               # < 5.7 %   (< 39 mmol/mol)
    HbA1cStage("prediabetes", "Prediabetes", "warn", 5.7, 6.5, 39, 48),     # 5.7-6.4 % (39-47 mmol/mol)
    HbA1cStage("diabetes", "Diabetes", "alert", 6.5, None, 48, None),        # >= 6.5 %  (>= 48 mmol/mol)
)


# Average eGFR by age, mL/min/1.73 m² (National Kidney Foundation). A population AVERAGE to compare with - not a
# normal/abnormal cut-off (a healthy person can be below the average for their age), so it is drawn as a line.
EGFR_AGE_AVERAGES_SOURCE = "average eGFR for age (National Kidney Foundation)"
EGFR_AGE_AVERAGES = (       # (youngest age, oldest age or None, average)
    (20, 29, 116), (30, 39, 107), (40, 49, 99), (50, 59, 93), (60, 69, 85), (70, None, 75),
)


DEFAULT_CONFIG = DetectionConfig()
CONSENT_CONFIG = ConsentConfig()
