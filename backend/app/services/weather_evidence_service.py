from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherMeasurement,
    WeatherProviderDisagreement,
    WeatherProviderStatus,
    WeatherProvenance,
)
from app.providers.base import WeatherEvidenceProvider, WeatherProviderError
from app.providers.open_meteo import (
    DAILY_WEATHER_UNITS,
    DAILY_WEATHER_VARIABLES,
    HOURLY_WEATHER_UNITS,
    HOURLY_WEATHER_VARIABLES,
)
from app.models.weather_evidence import WeatherTimeSeries

logger = logging.getLogger(__name__)

MATERIAL_DISAGREEMENT_THRESHOLDS = {
    "temperature": (2.0, "°C"),
    "apparent_temperature": (2.0, "°C"),
    "humidity": (10.0, "%"),
    "precipitation_probability": (20.0, "%"),
    "wind_speed": (10.0, "km/h"),
}
MERGEABLE_INSTANT_SERIES = {
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "cloud_cover",
    "visibility",
}


class WeatherEvidenceService:
    def __init__(
        self,
        provider: WeatherEvidenceProvider,
        *,
        secondary_provider: WeatherEvidenceProvider | None = None,
        cache_ttl_seconds: int = 600,
    ) -> None:
        self.provider = provider
        self.secondary_provider = secondary_provider
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cache: dict[tuple[float, float], tuple[datetime, WeatherEvidence]] = {}
        self._lock = Lock()

    @staticmethod
    def _for_location(evidence: WeatherEvidence, location: dict[str, Any]) -> WeatherEvidence:
        updated_location = evidence.location.model_copy(update={
            "name": str(location.get("name") or "Selected map point"),
            "state": str(location.get("state") or ""),
            "country": str(location.get("country") or ""),
            "requested_latitude": float(location["lat"]),
            "requested_longitude": float(location["lng"]),
        })
        return evidence.model_copy(update={"location": updated_location})

    @staticmethod
    def _forecast_is_current(evidence: WeatherEvidence, now: datetime) -> bool:
        for key in ("temperature", "humidity"):
            measurement = evidence.forecast.get(key)
            if measurement is None or measurement.forecast_time is None:
                continue
            try:
                forecast_time = datetime.fromisoformat(
                    measurement.forecast_time.replace("Z", "+00:00")
                )
            except ValueError:
                return False
            if forecast_time.tzinfo is None or forecast_time <= now:
                return False
        return evidence.status != "UNAVAILABLE"

    @staticmethod
    def _measurement_available(measurement: WeatherMeasurement | None) -> bool:
        return bool(
            measurement
            and measurement.status == "AVAILABLE"
            and isinstance(measurement.value, (int, float))
            and not isinstance(measurement.value, bool)
            and math.isfinite(float(measurement.value))
            and measurement.forecast_time
        )

    @staticmethod
    def _available_fields(evidence: WeatherEvidence) -> list[str]:
        fields = [
            name for name, measurement in evidence.forecast.items()
            if WeatherEvidenceService._measurement_available(measurement)
        ]
        for series in (evidence.hourly, evidence.daily):
            fields.extend(
                name
                for name, values in series.values.items()
                if any(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                    for value in values
                )
            )
        return list(dict.fromkeys(fields))

    @staticmethod
    def _request_provider(
        provider: WeatherEvidenceProvider,
        location: dict[str, Any],
    ) -> tuple[WeatherEvidence | None, WeatherProviderError | None]:
        try:
            return provider.get_weather_evidence_for_location(location), None
        except WeatherProviderError as exc:
            return None, exc

    @staticmethod
    def _series_source(
        evidence: WeatherEvidence,
        *,
        unit: str,
        aggregation_period: str,
    ) -> dict[str, Any]:
        return {
            "provider": evidence.provenance.source,
            "source_url": evidence.provenance.source_url,
            "api": evidence.provenance.api,
            "data_type": evidence.provenance.data_type,
            "model_id": evidence.provenance.model_id,
            "retrieved_at": evidence.provenance.retrieved_at,
            "unit": unit,
            "requested_latitude": evidence.location.requested_latitude,
            "requested_longitude": evidence.location.requested_longitude,
            "returned_latitude": evidence.location.grid_latitude,
            "returned_longitude": evidence.location.grid_longitude,
            "timezone": evidence.location.timezone,
            "aggregation_period": aggregation_period,
        }

    @classmethod
    def _ensure_series_provenance(cls, evidence: WeatherEvidence) -> WeatherEvidence:
        hourly_provenance = dict(evidence.hourly.provenance)
        for variable in evidence.hourly.values:
            has_values = any(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in evidence.hourly.values[variable]
            )
            if not has_values:
                hourly_provenance.pop(variable, None)
                continue
            if not hourly_provenance.get(variable):
                hourly_provenance[variable] = [cls._series_source(
                    evidence,
                    unit=evidence.hourly.units.get(variable, ""),
                    aggregation_period=evidence.hourly.aggregation_period or "provider hourly series",
                )]
        daily_provenance = dict(evidence.daily.provenance)
        for variable in evidence.daily.values:
            has_values = any(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in evidence.daily.values[variable]
            )
            if not has_values:
                daily_provenance.pop(variable, None)
                continue
            if not daily_provenance.get(variable):
                daily_provenance[variable] = [cls._series_source(
                    evidence,
                    unit=evidence.daily.units.get(variable, ""),
                    aggregation_period=evidence.daily.aggregation_period or "provider daily series",
                )]
        return evidence.model_copy(update={
            "hourly": evidence.hourly.model_copy(update={"provenance": hourly_provenance}),
            "daily": evidence.daily.model_copy(update={"provenance": daily_provenance}),
        })

    @staticmethod
    def _unavailable_evidence(
        location: dict[str, Any],
        *,
        retrieved_at: str,
        provider_status: list[WeatherProviderStatus],
    ) -> WeatherEvidence:
        requested_latitude = float(location["lat"])
        requested_longitude = float(location["lng"])
        forecast = {}
        for public_name in HOURLY_WEATHER_VARIABLES.values():
            api_name = next(
                (key for key, value in HOURLY_WEATHER_VARIABLES.items() if value == public_name),
                "",
            )
            forecast[public_name] = WeatherMeasurement(
                value=None,
                unit=HOURLY_WEATHER_UNITS.get(api_name, ""),
                status="UNAVAILABLE",
                forecast_time=None,
                provenance={
                    "source": "No weather provider available",
                    "data_type": "FORECAST",
                    "availability_reason": "No configured provider returned a valid value.",
                    "retrieval_time": retrieved_at,
                },
            )
        return WeatherEvidence(
            status="UNAVAILABLE",
            location=WeatherLocationEvidence(
                name=str(location.get("name") or "Selected map point"),
                state=str(location.get("state") or ""),
                country=str(location.get("country") or ""),
                requested_latitude=requested_latitude,
                requested_longitude=requested_longitude,
            ),
            forecast=forecast,
            hourly=WeatherTimeSeries(
                times=[],
                values={key: [] for key in HOURLY_WEATHER_VARIABLES},
                units=dict(HOURLY_WEATHER_UNITS),
            ),
            daily=WeatherTimeSeries(
                times=[],
                values={key: [] for key in DAILY_WEATHER_VARIABLES},
                units=dict(DAILY_WEATHER_UNITS),
            ),
            provenance=WeatherProvenance(
                source="No weather provider available",
                source_url="",
                api="",
                retrieved_at=retrieved_at,
                quality="No usable forecast evidence was returned.",
                limitations=["No provider result is available; missing values are not replaced with zero or demo data."],
            ),
            provider_status=provider_status,
            missing_fields=list(forecast),
        )

    @staticmethod
    def _time_key(value: str) -> str | None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()

    @classmethod
    def _comparisons(
        cls,
        primary: WeatherEvidence,
        secondary: WeatherEvidence,
    ) -> list[WeatherProviderDisagreement]:
        disagreements = []
        for field, (threshold, expected_unit) in MATERIAL_DISAGREEMENT_THRESHOLDS.items():
            first = primary.forecast.get(field)
            second = secondary.forecast.get(field)
            if (
                not cls._measurement_available(first)
                or not cls._measurement_available(second)
                or first.unit != expected_unit
                or second.unit != expected_unit
                or cls._time_key(first.forecast_time or "") != cls._time_key(second.forecast_time or "")
            ):
                continue
            first_period = first.provenance.get("aggregation_period", "instantaneous forecast at valid time")
            second_period = second.provenance.get("aggregation_period", "instantaneous forecast at valid time")
            if first_period != second_period:
                continue
            difference = abs(float(first.value) - float(second.value))
            if difference >= threshold:
                disagreements.append(WeatherProviderDisagreement(
                    variable=field,
                    first_provider=str(first.provenance.get("source") or primary.provenance.source),
                    first_value=float(first.value),
                    second_provider=str(second.provenance.get("source") or secondary.provenance.source),
                    second_value=float(second.value),
                    difference=difference,
                    unit=expected_unit,
                    valid_time=first.forecast_time or "",
                    materiality_threshold=threshold,
                ))
        return disagreements

    @classmethod
    def _merge(
        cls,
        primary: WeatherEvidence,
        secondary: WeatherEvidence,
    ) -> tuple[WeatherEvidence, list[WeatherProviderDisagreement], list[str]]:
        primary = cls._ensure_series_provenance(primary)
        disagreements = cls._comparisons(primary, secondary)
        forecast = dict(primary.forecast)
        used_fields = []
        primary_valid_time = next(
            (
                measurement.forecast_time
                for measurement in primary.forecast.values()
                if measurement.forecast_time
            ),
            None,
        )
        if primary_valid_time is None:
            now_key = datetime.now(timezone.utc)
            primary_valid_time = next(
                (
                    value
                    for value in primary.hourly.times
                    if (time_key := cls._time_key(value)) is not None
                    and datetime.fromisoformat(time_key) >= now_key
                ),
                None,
            )
        for field, measurement in secondary.forecast.items():
            existing = forecast.get(field)
            if (
                cls._measurement_available(existing)
                or not cls._measurement_available(measurement)
                or primary_valid_time is None
                or cls._time_key(measurement.forecast_time or "")
                != cls._time_key(primary_valid_time)
                or existing is None
                or existing.unit != measurement.unit
                or existing.provenance.get("aggregation_period")
                != measurement.provenance.get("aggregation_period")
            ):
                continue
            forecast[field] = measurement
            used_fields.append(field)

        primary_time_index = {
            key: index for index, value in enumerate(primary.hourly.times)
            if (key := cls._time_key(value)) is not None
        }
        hourly_values = {name: list(values) for name, values in primary.hourly.values.items()}
        hourly_provenance = {
            name: [dict(item) for item in sources]
            for name, sources in primary.hourly.provenance.items()
        }
        for variable in MERGEABLE_INSTANT_SERIES:
            primary_values = hourly_values.get(variable)
            secondary_values = secondary.hourly.values.get(variable)
            if (
                primary_values is None
                or secondary_values is None
                or primary.hourly.units.get(variable) != secondary.hourly.units.get(variable)
            ):
                continue
            secondary_indexes = {
                key: index for index, value in enumerate(secondary.hourly.times)
                if (key := cls._time_key(value)) is not None
            }
            contributed_times = []
            for timestamp, target_index in primary_time_index.items():
                source_index = secondary_indexes.get(timestamp)
                if (
                    source_index is not None
                    and target_index < len(primary_values)
                    and source_index < len(secondary_values)
                    and primary_values[target_index] is None
                ):
                    value = secondary_values[source_index]
                    if (
                        isinstance(value, (int, float))
                        and not isinstance(value, bool)
                        and math.isfinite(float(value))
                    ):
                        primary_values[target_index] = value
                        contributed_times.append(primary.hourly.times[target_index])
            if contributed_times:
                used_fields.append(variable)
                unit = primary.hourly.units.get(variable, secondary.hourly.units.get(variable, ""))
                hourly_provenance.setdefault(variable, []).append({
                    **cls._series_source(
                        secondary,
                        unit=unit,
                        aggregation_period="instantaneous forecast at matching valid times",
                    ),
                    "contributed_times": contributed_times,
                })

        hourly = primary.hourly.model_copy(update={
            "values": hourly_values,
            "provenance": hourly_provenance,
        })
        limitations = list(primary.provenance.limitations)
        limitations.extend(secondary.provenance.limitations)
        limitations.append(
            "Fields supplied by different providers retain field-level source and timestamp information; values are not averaged."
        )
        merged = primary.model_copy(update={
            "status": "DEGRADED" if used_fields else primary.status,
            "forecast": forecast,
            "hourly": hourly,
            "provenance": primary.provenance.model_copy(update={
                "quality": (
                    "Combined forecast evidence; inspect field-level provider provenance."
                    if used_fields
                    else primary.provenance.quality
                ),
                "limitations": list(dict.fromkeys(limitations)),
            }),
        })
        if not used_fields:
            return primary, disagreements, []
        return merged, disagreements, list(dict.fromkeys(used_fields))

    @classmethod
    def _finalize(
        cls,
        evidence: WeatherEvidence,
        provider_status: list[WeatherProviderStatus],
        disagreements: list[WeatherProviderDisagreement],
        *,
        extra_limitation: str | None = None,
    ) -> WeatherEvidence:
        evidence = cls._ensure_series_provenance(evidence)
        limitations = list(evidence.provenance.limitations)
        if extra_limitation:
            limitations.append(extra_limitation)
        missing = [
            name for name, measurement in evidence.forecast.items()
            if not cls._measurement_available(measurement)
        ]
        return evidence.model_copy(update={
            "provider_status": provider_status,
            "provider_disagreements": disagreements,
            "missing_fields": missing,
            "provenance": evidence.provenance.model_copy(update={
                "limitations": list(dict.fromkeys(limitations)),
            }),
        })

    def _retrieve(self, location: dict[str, Any]) -> WeatherEvidence:
        primary_name = getattr(self.provider, "provider_name", "Primary weather provider")
        secondary_name = (
            getattr(self.secondary_provider, "provider_name", "Secondary weather provider")
            if self.secondary_provider is not None
            else "OpenWeather"
        )
        primary_error: WeatherProviderError | None = None
        primary: WeatherEvidence | None = None
        secondary: WeatherEvidence | None = None
        secondary_error: WeatherProviderError | None = None
        if self.secondary_provider is None:
            primary, primary_error = self._request_provider(self.provider, location)
        else:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="weather-provider") as executor:
                primary_future = executor.submit(self._request_provider, self.provider, location)
                secondary_future = executor.submit(
                    self._request_provider,
                    self.secondary_provider,
                    location,
                )
                primary, primary_error = primary_future.result()
                secondary, secondary_error = secondary_future.result()

        if primary_error:
            logger.warning(
                "Primary weather provider failed: provider=%s error=%s",
                primary_name,
                type(primary_error).__name__,
            )

        primary_status = WeatherProviderStatus(
            provider=primary_name,
            status=primary.status if primary else "UNAVAILABLE",
            retrieved_at=primary.provenance.retrieved_at if primary else None,
            fields=self._available_fields(primary) if primary else [],
            reason=str(primary_error) if primary_error else None,
        )
        if self.secondary_provider is None:
            secondary_status = WeatherProviderStatus(
                provider=secondary_name,
                status="NOT_CONFIGURED",
                reason="Set OPENWEATHER_API_KEY on the backend to enable secondary comparison and fallback.",
            )
        elif secondary_error:
            logger.warning(
                "Secondary weather provider failed: provider=%s error=%s",
                secondary_name,
                type(secondary_error).__name__,
            )
            secondary_status = WeatherProviderStatus(
                provider=secondary_name,
                status="UNAVAILABLE",
                reason=str(secondary_error),
            )
        else:
            assert secondary is not None
            secondary_fields = self._available_fields(secondary)
            secondary_status = WeatherProviderStatus(
                provider=secondary_name,
                status=(
                    "AVAILABLE" if secondary.status == "AVAILABLE" else
                    "DEGRADED" if secondary_fields else "UNAVAILABLE"
                ),
                retrieved_at=secondary.provenance.retrieved_at,
                fields=secondary_fields,
                reason=(
                    None if secondary_fields else
                    "Provider returned no supported usable fields for this location."
                ),
            )
            if (
                (primary is None or primary.status == "UNAVAILABLE")
                and secondary_fields
            ):
                limitations = list(secondary.provenance.limitations)
                limitations.append(
                    f"{primary_name} returned no usable forecast; this result uses {secondary_name} forecast evidence only."
                )
                evidence = secondary.model_copy(update={
                    "status": "DEGRADED",
                    "provenance": secondary.provenance.model_copy(update={
                        "quality": f"Fallback forecast from {secondary_name}; primary provider unavailable.",
                        "limitations": list(dict.fromkeys(limitations)),
                    }),
                })
                secondary_status = secondary_status.model_copy(update={
                    "used_fields": secondary_fields,
                })
                return self._finalize(
                    evidence,
                    [primary_status, secondary_status],
                    [],
                )
            if primary is not None and primary.status != "UNAVAILABLE" and secondary_fields:
                primary, disagreements, used_fields = self._merge(primary, secondary)
                secondary_status = secondary_status.model_copy(update={
                    "used_fields": used_fields,
                    "reason": (
                        None if used_fields
                        else "Secondary forecast retrieved for comparison; no compatible missing fields were added to the primary result."
                    ),
                })
                return self._finalize(
                    primary,
                    [primary_status, secondary_status],
                    disagreements,
                )

        if primary is None:
            statuses = [primary_status, secondary_status]
            return self._unavailable_evidence(
                location,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                provider_status=statuses,
            )
        return self._finalize(
            primary,
            [primary_status, secondary_status],
            [],
        )

    def get_for_location(
        self,
        location: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> WeatherEvidence:
        latitude = float(location["lat"])
        longitude = float(location["lng"])
        key = (latitude, longitude)
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(key)
            if (
                not force_refresh
                and cached
                and now - cached[0] < self.cache_ttl
                and self._forecast_is_current(cached[1], now)
            ):
                return self._for_location(cached[1], location)
        result = self._retrieve(location)
        if result.status == "UNAVAILABLE" and cached:
            cached_evidence = cached[1]
            failed_refresh_at = datetime.now(timezone.utc).isoformat()
            limitations = list(cached_evidence.provenance.limitations)
            limitations.append(
                f"Latest provider refresh failed at {failed_refresh_at}; showing cached forecast evidence retrieved at {cached_evidence.provenance.retrieved_at}."
            )
            stale_evidence = cached_evidence.model_copy(update={
                "status": "DEGRADED",
                "provider_status": result.provider_status,
                "provenance": cached_evidence.provenance.model_copy(update={
                    "quality": "Showing previously retrieved forecast evidence because all configured providers are currently unavailable.",
                    "limitations": list(dict.fromkeys(limitations)),
                }),
            })
            return self._for_location(stale_evidence, location)
        if result.status != "UNAVAILABLE":
            with self._lock:
                self._cache[key] = (datetime.now(timezone.utc), result)
        return self._for_location(result, location)

    def get_weather_evidence_for_location(self, location: dict[str, Any]) -> WeatherEvidence:
        return self.get_for_location(location)
