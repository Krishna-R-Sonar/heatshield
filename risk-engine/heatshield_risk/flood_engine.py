from __future__ import annotations

from heatshield_risk.config import RiskThresholds
from heatshield_risk.models import (
    ContributingFactor,
    FloodInputs,
    GlacialLakeInfo,
    ObservationEntry,
    RiskLevel,
)


def _tier_score(value: float, moderate: float, high: float, critical: float, cap: float) -> float:
    if value >= critical:
        return cap
    if value >= high:
        return cap * 0.72
    if value >= moderate:
        return cap * 0.44
    return cap * 0.12


def _rainfall_score(inputs: FloodInputs, cfg: dict, cap: float) -> float:
    intensity = inputs.rainfall_intensity_mm_hr
    cumulative = inputs.cumulative_rainfall_mm_24h
    scores: list[float] = []
    ti = cfg["rainfall_intensity_mm_hr"]
    tc = cfg["cumulative_rainfall_mm_24h"]
    if intensity is not None:
        scores.append(_tier_score(intensity, ti["moderate"], ti["high"], ti["critical"], cap))
    if cumulative is not None:
        scores.append(_tier_score(cumulative, tc["moderate"], tc["high"], tc["critical"], cap))
    if not scores:
        return 0.0
    return min(cap, max(scores))


def _river_score(inputs: FloodInputs, cfg: dict, cap: float) -> float:
    if inputs.river_level_m is None or inputs.river_threshold_m is None:
        return 0.0
    if inputs.river_threshold_m <= 0:
        return 0.0
    ratio = inputs.river_level_m / inputs.river_threshold_m
    tr = cfg["river_ratio"]
    return _tier_score(ratio, tr["moderate"], tr["high"], tr["critical"], cap)


def _exposure_score(inputs: FloodInputs, cap: float) -> float:
    if inputs.population_exposure_index is None or inputs.infrastructure_exposure_index is None:
        return 0.0
    blend = 0.6 * inputs.population_exposure_index + 0.4 * inputs.infrastructure_exposure_index
    return min(cap, blend * cap)


def _historical_score(inputs: FloodInputs, cfg: dict, cap: float) -> float:
    if inputs.historical_flood_events_5y is None:
        return 0.0
    cap_events = int(cfg.get("historical_events_cap", 5))
    ratio = min(1.0, inputs.historical_flood_events_5y / max(1, cap_events))
    return ratio * (cap * 0.5)


def _severity_from_score(score: float, cap: float) -> str:
    if score >= cap * 0.65:
        return "high"
    if score >= cap * 0.35:
        return "moderate"
    return "low"


def assess_flood(
    location_name: str,
    state: str,
    lat: float,
    lng: float,
    inputs: FloodInputs,
    thresholds: RiskThresholds | None = None,
) -> tuple[float, dict[str, float], list[ContributingFactor], list[ObservationEntry], float, str]:
    th = thresholds or RiskThresholds.load()
    cfg = th.flood
    cap = float(cfg["component_max"])

    rainfall = _rainfall_score(inputs, cfg, cap)
    river = _river_score(inputs, cfg, cap)
    exposure = _exposure_score(inputs, cap)
    historical = _historical_score(inputs, cfg, cap)

    total = min(100.0, rainfall + river + exposure + historical)
    components = {
        "rainfall_score": round(rainfall, 2),
        "river_score": round(river, 2),
        "exposure_score": round(exposure, 2),
        "historical_score": round(historical, 2),
    }

    factors: list[ContributingFactor] = []
    if rainfall > 0:
        factors.append(
            ContributingFactor(
                id="rainfall",
                label="Heavy rainfall",
                severity=_severity_from_score(rainfall, cap),  # type: ignore[arg-type]
                kind="observed",
                detail="Rainfall intensity or 24h cumulative exceeds configured moderate thresholds.",
                score_contribution=rainfall,
            )
        )
    if river > 0:
        factors.append(
            ContributingFactor(
                id="river",
                label="Elevated river level",
                severity=_severity_from_score(river, cap),  # type: ignore[arg-type]
                kind="observed",
                detail="River level is near or above the configured alert threshold.",
                score_contribution=river,
            )
        )
    if exposure > 0:
        factors.append(
            ContributingFactor(
                id="exposure",
                label="Downstream population exposure",
                severity=_severity_from_score(exposure, cap),  # type: ignore[arg-type]
                kind="calculated",
                detail="Demo exposure indices indicate vulnerable populations or infrastructure in the area.",
                score_contribution=exposure,
            )
        )
    if historical > 0:
        factors.append(
            ContributingFactor(
                id="historical",
                label="Historical flood occurrence",
                severity="moderate",
                kind="historical",
                detail="Past flood events in demo historical record increase contextual sensitivity.",
                score_contribution=historical,
            )
        )

    observations: list[ObservationEntry] = []
    if inputs.rainfall_intensity_mm_hr is not None:
        observations.append(
            ObservationEntry(
                "Rainfall intensity",
                f"{inputs.rainfall_intensity_mm_hr} mm/hr ({inputs.data_provenance})",
                "observed",
            )
        )
    else:
        observations.append(ObservationEntry("Rainfall intensity", "Data unavailable.", "unavailable"))
    if inputs.cumulative_rainfall_mm_24h is not None:
        observations.append(
            ObservationEntry(
                "24h cumulative rainfall",
                f"{inputs.cumulative_rainfall_mm_24h} mm ({inputs.data_provenance})",
                "observed",
            )
        )
    else:
        observations.append(ObservationEntry("24h cumulative rainfall", "Data unavailable.", "unavailable"))
    if inputs.river_level_m is not None and inputs.river_threshold_m is not None:
        observations.append(
            ObservationEntry(
                "River level vs threshold",
                f"{inputs.river_level_m} m / {inputs.river_threshold_m} m ({inputs.data_provenance})",
                "observed",
            )
        )
    else:
        observations.append(ObservationEntry("River level and threshold", "Data unavailable.", "unavailable"))
    if inputs.glacial_lake.available and inputs.glacial_lake.status:
        observations.append(
            ObservationEntry(
                "Glacial lake status",
                f"{inputs.glacial_lake.status} — {inputs.glacial_lake.notes or 'demo watch list'}",
                "observed",
            )
        )
    else:
        observations.append(
            ObservationEntry(
                "Glacial lake status",
                "Data unavailable — GLOF-specific factor not applied.",
                "unavailable",
            )
        )
    for label, value in [
        ("Population exposure index", inputs.population_exposure_index),
        ("Infrastructure exposure index", inputs.infrastructure_exposure_index),
    ]:
        observations.append(
            ObservationEntry(
                label,
                f"{value} (DEMO index)" if value is not None else "Data unavailable.",
                "calculated" if value is not None else "unavailable",
            )
        )
    observations.append(
        ObservationEntry(
            "Historical flood events (5 years)",
            (
                f"{inputs.historical_flood_events_5y} events (DEMO historical context)"
                if inputs.historical_flood_events_5y is not None
                else "Data unavailable."
            ),
            "historical" if inputs.historical_flood_events_5y is not None else "unavailable",
        )
    )

    missing = sum(
        1
        for v in [
            inputs.rainfall_intensity_mm_hr,
            inputs.cumulative_rainfall_mm_24h,
            inputs.river_level_m,
            inputs.river_threshold_m,
            inputs.population_exposure_index,
            inputs.infrastructure_exposure_index,
        ]
        if v is None
    )
    confidence = max(0.35, 0.9 - missing * 0.2)
    uncertainty = (
        "Moderate uncertainty: one or more environmental or exposure fields are unavailable."
        if missing
        else "Lower uncertainty: core flood inputs present (demo dataset)."
    )

    return total, components, factors, observations, confidence, uncertainty
