from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.models.forecast_validation import ForecastPerformanceMetric, ForecastValidationEvidence

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ERA5_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
VALIDATION_LIMITATIONS = [
    "The reference is ERA5 reanalysis, a gridded estimate, not a ground station observation.",
    "Forecast and reanalysis use different model systems and grid resolutions; this comparison is not a station-verified score.",
    "Available ERA5 reference data are delayed; recent dates may have null values and reduce the sample count.",
    "Results describe historical weather-variable error only and do not validate disaster prediction or risk scores.",
    "Previous Runs model selection defaults to the provider's best match; the exact selected model ID is not returned.",
]


class OpenMeteoValidationError(RuntimeError):
    pass


class OpenMeteoForecastValidationProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
        lookback_days: int = 7,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client
        self.lookback_days = lookback_days

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if self.client is None:
                response = httpx.get(url, params=params, timeout=self.timeout_seconds)
            else:
                response = self.client.get(url, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoValidationError("Historical forecast validation request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoValidationError(
                f"Open-Meteo validation API returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoValidationError("Open-Meteo validation API could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoValidationError("Open-Meteo validation API returned malformed JSON.") from exc
        if not isinstance(payload, dict):
            raise OpenMeteoValidationError("Open-Meteo validation API returned an invalid response object.")
        return payload

    @staticmethod
    def _valid_number(value: Any) -> float | None:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return None
        return float(value)

    @staticmethod
    def _time_key(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")

    def get_historical_performance(self, latitude: float, longitude: float) -> ForecastValidationEvidence:
        if (
            not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            raise OpenMeteoValidationError("Location coordinates are missing or invalid.")

        end = datetime.now(timezone.utc).date() - timedelta(days=1)
        start = end - timedelta(days=self.lookback_days - 1)
        previous_params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(
                f"{variable}_previous_day{lead}"
                for lead in (1, 2)
                for variable in ("temperature_2m", "precipitation")
            ),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "UTC",
        }
        reference_params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "hourly": "temperature_2m,precipitation",
            "models": "era5",
            "timezone": "UTC",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            previous_future = executor.submit(self._get_json, PREVIOUS_RUNS_URL, previous_params)
            reference_future = executor.submit(self._get_json, ERA5_ARCHIVE_URL, reference_params)
            previous_payload = previous_future.result()
            reference_payload = reference_future.result()

        previous = previous_payload.get("hourly")
        previous_units = previous_payload.get("hourly_units")
        reference = reference_payload.get("hourly")
        reference_units = reference_payload.get("hourly_units")
        times = previous.get("time") if isinstance(previous, dict) else None
        reference_times = reference.get("time") if isinstance(reference, dict) else None
        if (
            not isinstance(previous, dict)
            or not isinstance(previous_units, dict)
            or not isinstance(reference, dict)
            or not isinstance(reference_units, dict)
            or not isinstance(times, list)
            or not isinstance(reference_times, list)
        ):
            raise OpenMeteoValidationError("Validation response is missing hourly values, times, or units.")
        expected_units = {
            "temperature": ("temperature_2m", "°C"),
            "precipitation": ("precipitation", "mm"),
        }
        reference_index = {
            key: index
            for index, value in enumerate(reference_times)
            if (key := self._time_key(value)) is not None
        }
        metrics: list[ForecastPerformanceMetric] = []
        for lead in (1, 2):
            for metric_name, (variable, unit) in expected_units.items():
                forecast_name = f"{variable}_previous_day{lead}"
                values = previous.get(forecast_name)
                if previous_units.get(forecast_name) != unit or reference_units.get(variable) != unit:
                    raise OpenMeteoValidationError(f"Validation response has incompatible units for {forecast_name}.")
                if not isinstance(values, list) or len(values) != len(times):
                    raise OpenMeteoValidationError(f"Validation response is missing {forecast_name}.")
                errors = []
                for index, value in enumerate(times):
                    time_key = self._time_key(value)
                    if time_key is None or time_key not in reference_index:
                        continue
                    reference_values = reference.get(variable)
                    if not isinstance(reference_values, list) or len(reference_values) != len(reference_times):
                        raise OpenMeteoValidationError(f"ERA5 response is missing {variable} values.")
                    forecast_value = self._valid_number(values[index])
                    reference_value = self._valid_number(reference_values[reference_index[time_key]])
                    if forecast_value is not None and reference_value is not None:
                        errors.append(abs(forecast_value - reference_value))
                metrics.append(ForecastPerformanceMetric(
                    status="AVAILABLE" if errors else "UNAVAILABLE",
                    variable=metric_name,
                    forecast_horizon_hours=lead * 24,
                    mean_absolute_error=sum(errors) / len(errors) if errors else None,
                    unit=unit,
                    sample_count=len(errors),
                ))

        available = sum(metric.status == "AVAILABLE" for metric in metrics)
        status = "UNAVAILABLE" if not available else (
            "AVAILABLE" if available == len(metrics) else "DEGRADED"
        )
        total_samples = sum(metric.sample_count for metric in metrics)
        return ForecastValidationEvidence(
            status=status,
            metrics=metrics,
            verification_period=f"{start.isoformat()} to {end.isoformat()}",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            quality=f"Compared {total_samples} forecast/reference pairs where both values were available.",
            limitations=VALIDATION_LIMITATIONS,
        )