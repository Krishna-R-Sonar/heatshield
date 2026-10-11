from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from app.models.ensemble_evidence import (
    EnsembleDailyEvidence,
    EnsembleForecastEvidence,
    EnsembleProvenance,
    EnsembleStatistics,
)

OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
ENSEMBLE_CLASSIFICATION_METHOD = (
    "Spread is the interquartile range (P75-P25) across available ensemble members. "
    "Temperature bands: LOW <=2°C, MODERATE >2°C to 5°C, HIGH >5°C. "
    "Precipitation bands use IQR/median: LOW <=0.25, MODERATE >0.25 to 0.75, HIGH >0.75; "
    "zero median with nonzero spread is HIGH. These are display-only spread descriptors, "
    "not hazard thresholds or probabilities of disaster. No or low spread does not establish "
    "zero risk, impact, or safety."
)
ENSEMBLE_LIMITATIONS = [
    "Ensemble spread describes disagreement among model members, not the probability of disaster.",
    "The ECMWF IFS 0.25° ensemble grid is approximately 25 km and differs from the deterministic forecast grid.",
    "Daily summaries aggregate the hourly member values returned for each UTC date; member data may have model-specific time steps.",
]
MEMBER_PATTERN = re.compile(r"^(temperature_2m|precipitation)_member(\d+)$")


class OpenMeteoEnsembleError(RuntimeError):
    pass


class OpenMeteoEnsembleProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    @staticmethod
    def _number(value: Any) -> float | None:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return None
        return float(value)

    @staticmethod
    def _quantile(sorted_values: list[float], fraction: float) -> float:
        if len(sorted_values) == 1:
            return sorted_values[0]
        position = (len(sorted_values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return sorted_values[lower]
        weight = position - lower
        return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight

    @staticmethod
    def _uncertainty(
        indicator: Literal["temperature", "precipitation"],
        median: float,
        spread: float,
    ) -> tuple[Literal["LOW", "MODERATE", "HIGH"], str]:
        precision_note = (
            " No ensemble spread at two-decimal display precision; this does not imply zero risk."
            if spread == 0
            else ""
        )
        if indicator == "temperature":
            spread_display = f"{spread:g}"
            if spread <= 2 or math.isclose(spread, 2, rel_tol=1e-12, abs_tol=1e-12):
                return "LOW", f"Ensemble temperature IQR is {spread_display}°C (<=2°C display band).{precision_note}"
            if spread <= 5 or math.isclose(spread, 5, rel_tol=1e-12, abs_tol=1e-12):
                return "MODERATE", f"Ensemble temperature IQR is {spread_display}°C (>2°C to 5°C display band).{precision_note}"
            return "HIGH", f"Ensemble temperature IQR is {spread_display}°C (>5°C display band).{precision_note}"
        if median == 0 and spread > 0:
            return "HIGH", f"Precipitation ensemble median is zero while member spread is nonzero.{precision_note}"
        ratio = spread / abs(median) if median else 0.0
        if ratio <= 0.25 or math.isclose(ratio, 0.25, rel_tol=1e-12, abs_tol=1e-12):
            return "LOW", f"Precipitation IQR/median is {ratio:.2f} (<=0.25 display band).{precision_note}"
        if ratio <= 0.75 or math.isclose(ratio, 0.75, rel_tol=1e-12, abs_tol=1e-12):
            return "MODERATE", f"Precipitation IQR/median is {ratio:.2f} (>0.25 to 0.75 display band).{precision_note}"
        return "HIGH", f"Precipitation IQR/median is {ratio:.2f} (>0.75 display band).{precision_note}"

    def _statistics(
        self,
        values: list[float],
        *,
        unit: str,
        indicator: Literal["temperature", "precipitation"],
    ) -> EnsembleStatistics:
        if len(values) < 2:
            return EnsembleStatistics(
                status="UNAVAILABLE",
                unit=unit,
                member_count=len(values),
                uncertainty="UNAVAILABLE",
                uncertainty_reason="At least two valid ensemble members are required to describe spread.",
            )
        ordered = sorted(values)
        p25 = self._quantile(ordered, 0.25)
        median = self._quantile(ordered, 0.5)
        p75 = self._quantile(ordered, 0.75)
        spread = p75 - p25
        category, reason = self._uncertainty(indicator, median, spread)
        return EnsembleStatistics(
            status="AVAILABLE",
            unit=unit,
            member_count=len(values),
            mean=sum(values) / len(values),
            median=median,
            p25=p25,
            p75=p75,
            minimum=ordered[0],
            maximum=ordered[-1],
            spread_iqr=spread,
            uncertainty=category,
            uncertainty_reason=reason,
        )

    def get_evidence(self, latitude: float, longitude: float) -> EnsembleForecastEvidence:
        if (
            not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            raise OpenMeteoEnsembleError("Location coordinates are missing or invalid.")
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,precipitation",
            "models": "ecmwf_ifs025",
            "forecast_hours": 72,
            "timezone": "UTC",
            "precipitation_unit": "mm",
            "temperature_unit": "celsius",
        }
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_ENSEMBLE_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_ENSEMBLE_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoEnsembleError("Open-Meteo ensemble request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoEnsembleError(
                f"Open-Meteo ensemble API returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoEnsembleError("Open-Meteo ensemble API could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoEnsembleError("Open-Meteo returned malformed ensemble data.") from exc

        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        units = payload.get("hourly_units") if isinstance(payload, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        if not isinstance(hourly, dict) or not isinstance(units, dict) or not isinstance(times, list):
            raise OpenMeteoEnsembleError("Ensemble response is missing hourly values or units.")
        if units.get("temperature_2m") != "°C" or units.get("precipitation") != "mm":
            raise OpenMeteoEnsembleError("Ensemble response returned incompatible temperature or precipitation units.")

        try:
            parsed_times = []
            for value in times:
                if not isinstance(value, str):
                    raise ValueError("Invalid ensemble timestamp")
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed_times.append(parsed)
        except ValueError as exc:
            raise OpenMeteoEnsembleError("Ensemble response returned invalid timestamps.") from exc
        if any(value is None for value in parsed_times) or any(
            right <= left for left, right in zip(parsed_times, parsed_times[1:])
        ):
            raise OpenMeteoEnsembleError("Ensemble response returned invalid timestamps.")
        first_future = next(
            (index for index, value in enumerate(parsed_times) if value >= datetime.now(timezone.utc)),
            None,
        )
        if first_future is None:
            raise OpenMeteoEnsembleError("Ensemble response has no current or future data.")

        members: dict[str, dict[str, list[float | None]]] = defaultdict(dict)
        for key, raw_values in hourly.items():
            match = MEMBER_PATTERN.fullmatch(key)
            if not match or not isinstance(raw_values, list) or len(raw_values) != len(times):
                continue
            variable, member_id = match.groups()
            members[member_id][variable] = [self._number(value) for value in raw_values]
        temperature_member_ids = {
            member_id for member_id, values in members.items() if "temperature_2m" in values
        }
        precipitation_member_ids = {
            member_id for member_id, values in members.items() if "precipitation" in values
        }
        if not temperature_member_ids and not precipitation_member_ids:
            raise OpenMeteoEnsembleError("Ensemble response contains no recognized forecast members.")

        by_date: dict[str, list[int]] = defaultdict(list)
        for index in range(first_future, len(parsed_times)):
            by_date[parsed_times[index].date().isoformat()].append(index)

        daily = []
        for date, indices in by_date.items():
            temperature_values = []
            precipitation_values = []
            for member_id in sorted(temperature_member_ids):
                series = members[member_id]["temperature_2m"]
                sample = [series[index] for index in indices]
                if sample and all(value is not None for value in sample):
                    temperature_values.append(max(value for value in sample if value is not None))
            for member_id in sorted(precipitation_member_ids):
                series = members[member_id]["precipitation"]
                sample = [series[index] for index in indices]
                if sample and all(value is not None and value >= 0 for value in sample):
                    precipitation_values.append(sum(value for value in sample if value is not None))
            daily.append(EnsembleDailyEvidence(
                date=date,
                temperature_max=self._statistics(
                    temperature_values,
                    unit="°C",
                    indicator="temperature",
                ),
                precipitation_sum=self._statistics(
                    precipitation_values,
                    unit="mm",
                    indicator="precipitation",
                ),
            ))

        valid_period_count = sum(
            item.temperature_max.status == "AVAILABLE" or item.precipitation_sum.status == "AVAILABLE"
            for item in daily
        )
        status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE"] = (
            "UNAVAILABLE" if valid_period_count == 0
            else "AVAILABLE" if all(
                item.temperature_max.status == "AVAILABLE"
                and item.precipitation_sum.status == "AVAILABLE"
                for item in daily
            )
            else "DEGRADED"
        )
        model_member_count = max(len(temperature_member_ids), len(precipitation_member_ids))
        retrieved_at = datetime.now(timezone.utc).isoformat()
        return EnsembleForecastEvidence(
            status=status,
            member_count=model_member_count,
            daily=daily,
            provenance=EnsembleProvenance(
                retrieved_at=retrieved_at,
                timezone=payload.get("timezone") if isinstance(payload.get("timezone"), str) else None,
                requested_latitude=latitude,
                requested_longitude=longitude,
                grid_latitude=(
                    float(payload["latitude"])
                    if isinstance(payload.get("latitude"), (int, float))
                    else None
                ),
                grid_longitude=(
                    float(payload["longitude"])
                    if isinstance(payload.get("longitude"), (int, float))
                    else None
                ),
                forecast_period=(f"{daily[0].date} to {daily[-1].date}" if daily else None),
                classification_method=ENSEMBLE_CLASSIFICATION_METHOD,
                limitations=ENSEMBLE_LIMITATIONS,
            ),
        )