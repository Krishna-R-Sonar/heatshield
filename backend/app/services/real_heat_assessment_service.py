from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.models.weather_evidence import WeatherEvidence, WeatherMeasurement
from app.providers.base import WeatherProviderError
from app.services.weather_evidence_service import WeatherEvidenceService

MIN_VALIDATED_TEMPERATURE_C = -90.0
MAX_VALIDATED_TEMPERATURE_C = 60.0
MAX_FORECAST_LEAD = timedelta(hours=2)
MAX_EVIDENCE_AGE = timedelta(minutes=10)

MISSING_REAL_INPUTS = (
    (
        "heat_index_c",
        "No provider heat index is available; the demo-only approximation is not used in Real mode.",
    ),
    (
        "elevated_temp_duration_hours",
        "The forecast evidence does not provide a validated elevated-temperature duration.",
    ),
    (
        "population_exposure_index",
        "No real population exposure input is configured for this assessment.",
    ),
    (
        "infrastructure_exposure_index",
        "No real infrastructure exposure input is configured for this assessment.",
    ),
)


class RealHeatAssessmentService:
    def __init__(self, weather_evidence_service: WeatherEvidenceService | None) -> None:
        self.weather_evidence_service = weather_evidence_service

    def assess(self, location: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        requested_latitude = location.get("lat")
        requested_longitude = location.get("lng")
        result = self._empty_result(location)

        if not self._valid_coordinate(requested_latitude, -90, 90) or not self._valid_coordinate(
            requested_longitude, -180, 180
        ):
            return self._finish(
                result,
                "UNAVAILABLE",
                [
                    self._reason(
                        "INVALID_SELECTED_LOCATION",
                        "location",
                        "The selected location has invalid coordinates.",
                    )
                ],
                [],
            )

        if self.weather_evidence_service is None:
            return self._finish(
                result,
                "UNAVAILABLE",
                [
                    self._reason(
                        "WEATHER_EVIDENCE_SERVICE_UNAVAILABLE",
                        "weather",
                        "The real weather evidence service is not configured.",
                    )
                ],
                [],
            )

        try:
            evidence = self.weather_evidence_service.get_for_location(location)
        except WeatherProviderError as exc:
            return self._finish(
                result,
                "UNAVAILABLE",
                [
                    self._reason(
                        "WEATHER_PROVIDER_UNAVAILABLE",
                        "weather",
                        str(exc),
                    )
                ],
                [],
            )

        result["provenance"] = self._provenance(evidence)
        result["assessmentWindow"]["timezone"] = evidence.location.timezone

        validation_reasons: list[dict[str, str]] = []
        if evidence.status == "UNAVAILABLE":
            validation_reasons.append(
                self._reason(
                    "WEATHER_EVIDENCE_UNAVAILABLE",
                    "weather",
                    "Configured weather providers returned no usable weather evidence.",
                )
            )

        if (
            evidence.provenance.source not in {"Open-Meteo", "OpenWeather"}
            or evidence.provenance.data_type != "FORECAST"
        ):
            validation_reasons.append(
                self._reason(
                    "UNSUPPORTED_EVIDENCE_SOURCE",
                    "weather",
                    "Only forecast evidence from an implemented weather provider is accepted for this assessment.",
                )
            )

        if not self._coordinates_match(
            evidence.location.requested_latitude,
            evidence.location.requested_longitude,
            float(requested_latitude),
            float(requested_longitude),
        ):
            validation_reasons.append(
                self._reason(
                    "LOCATION_ASSOCIATION_MISMATCH",
                    "location",
                    "Weather evidence was requested for coordinates that do not match the selected location.",
                )
            )

        if not self._valid_coordinate(evidence.location.grid_latitude, -90, 90) or not self._valid_coordinate(
            evidence.location.grid_longitude, -180, 180
        ):
            validation_reasons.append(
                self._reason(
                    "INVALID_PROVIDER_GRID_COORDINATES",
                    "location",
                    "Open-Meteo grid coordinates are missing or invalid.",
                )
            )

        retrieved_at = self._parse_timestamp(evidence.provenance.retrieved_at)
        if retrieved_at is None or retrieved_at > now or now - retrieved_at > MAX_EVIDENCE_AGE:
            validation_reasons.append(
                self._reason(
                    "STALE_OR_INVALID_EVIDENCE",
                    "retrieved_at",
                    "Weather evidence retrieval time is invalid, in the future, or older than 10 minutes.",
                )
            )

        validated_measurements: dict[str, dict[str, Any]] = {}
        parsed_forecast_times: dict[str, datetime] = {}
        for field, key, unit, minimum, maximum in (
            (
                "temperature",
                "temperature",
                "°C",
                MIN_VALIDATED_TEMPERATURE_C,
                MAX_VALIDATED_TEMPERATURE_C,
            ),
            ("relativeHumidity", "humidity", "%", 0.0, 100.0),
        ):
            measurement = evidence.forecast.get(key)
            validated, forecast_time, measurement_reasons = self._validate_measurement(
                measurement=measurement,
                field=field,
                expected_unit=unit,
                minimum=minimum,
                maximum=maximum,
                requested_latitude=float(requested_latitude),
                requested_longitude=float(requested_longitude),
                now=now,
            )
            validation_reasons.extend(measurement_reasons)
            validated_measurements[field] = self._measurement_output(
                measurement,
                validated=validated,
            )
            if forecast_time is not None:
                parsed_forecast_times[field] = forecast_time

        if (
            len(parsed_forecast_times) == 2
            and parsed_forecast_times["temperature"] == parsed_forecast_times["relativeHumidity"]
        ):
            result["assessmentWindow"]["forecastTime"] = evidence.forecast[
                "temperature"
            ].forecast_time
        elif len(parsed_forecast_times) == 2:
            validation_reasons.append(
                self._reason(
                    "FORECAST_TIMESTAMP_MISMATCH",
                    "forecastTime",
                    "Temperature and humidity must refer to the same forecast hour.",
                )
            )

        result["forecastInputs"] = validated_measurements
        result["provenance"]["locationAssociation"] = (
            "REQUESTED_COORDINATES_MATCH"
            if not any(
                reason["code"] == "LOCATION_ASSOCIATION_MISMATCH"
                for reason in validation_reasons
            )
            else "MISMATCH"
        )

        return self._finish(
            result,
            "UNAVAILABLE" if validation_reasons else "INCOMPLETE",
            validation_reasons,
            [
                self._reason("REQUIRED_REAL_INPUT_UNAVAILABLE", field, message)
                for field, message in MISSING_REAL_INPUTS
            ],
        )

    @staticmethod
    def _empty_result(location: dict[str, Any]) -> dict[str, Any]:
        return {
            "assessmentType": "FORECAST_HEAT",
            "status": "UNAVAILABLE",
            "scoreStatus": "NOT_CALCULATED",
            "riskScore": None,
            "riskLevel": None,
            "dataProvenance": "FORECAST",
            "location": {
                "id": location.get("id"),
                "name": location.get("name"),
                "state": location.get("state"),
                "latitude": location.get("lat"),
                "longitude": location.get("lng"),
            },
            "assessmentWindow": {
                "basis": "NEXT_AVAILABLE_HOURLY_FORECAST",
                "forecastTime": None,
                "timezone": None,
            },
            "forecastInputs": {
                "temperature": {
                    "value": None,
                    "unit": "°C",
                    "status": "UNAVAILABLE",
                    "dataType": "FORECAST",
                    "forecastTime": None,
                },
                "relativeHumidity": {
                    "value": None,
                    "unit": "%",
                    "status": "UNAVAILABLE",
                    "dataType": "FORECAST",
                    "forecastTime": None,
                },
            },
            "provenance": {
                "source": "Open-Meteo",
                "sourceUrl": "https://open-meteo.com/",
                "dataType": "FORECAST",
                "retrievedAt": None,
                "requestedCoordinates": {
                    "latitude": location.get("lat"),
                    "longitude": location.get("lng"),
                },
                "gridCoordinates": None,
                "locationAssociation": "UNVERIFIED",
                "spatialResolution": "Model-dependent; exact resolution is not returned for auto selection.",
                "limitations": [
                    "Forecast values are model output, not ground observations.",
                    "No heat index, elevated-temperature duration, or real exposure inputs are supplied.",
                    "A complete deterministic heat assessment is therefore not calculated.",
                ],
            },
            "missingInputs": [field for field, _ in MISSING_REAL_INPUTS],
            "reasons": [],
        }

    @staticmethod
    def _provenance(evidence: WeatherEvidence) -> dict[str, Any]:
        return {
            "source": evidence.provenance.source,
            "sourceUrl": evidence.provenance.source_url,
            "dataType": evidence.provenance.data_type,
            "retrievedAt": evidence.provenance.retrieved_at,
            "requestedCoordinates": {
                "latitude": evidence.location.requested_latitude,
                "longitude": evidence.location.requested_longitude,
            },
            "gridCoordinates": (
                {
                    "latitude": evidence.location.grid_latitude,
                    "longitude": evidence.location.grid_longitude,
                }
                if evidence.location.grid_latitude is not None
                and evidence.location.grid_longitude is not None
                else None
            ),
            "locationAssociation": "UNVERIFIED",
            "spatialResolution": evidence.provenance.spatial_resolution,
            "modelId": evidence.provenance.model_id,
            "quality": evidence.provenance.quality,
            "limitations": evidence.provenance.limitations,
        }

    @classmethod
    def _validate_measurement(
        cls,
        *,
        measurement: WeatherMeasurement | None,
        field: str,
        expected_unit: str,
        minimum: float,
        maximum: float,
        requested_latitude: float,
        requested_longitude: float,
        now: datetime,
    ) -> tuple[bool, datetime | None, list[dict[str, str]]]:
        reasons: list[dict[str, str]] = []
        if measurement is None or measurement.status != "AVAILABLE":
            return False, None, [
                cls._reason(
                    "FORECAST_MEASUREMENT_UNAVAILABLE",
                    field,
                    f"No available Open-Meteo {field} forecast value was returned.",
                )
            ]

        if measurement.data_type != "FORECAST" or measurement.provenance.get("data_type") != "FORECAST":
            reasons.append(
                cls._reason(
                    "NON_FORECAST_MEASUREMENT",
                    field,
                    f"The {field} value is not explicitly identified as forecast data.",
                )
            )
        if measurement.provenance.get("source") not in {"Open-Meteo", "OpenWeather"}:
            reasons.append(
                cls._reason(
                    "UNSUPPORTED_MEASUREMENT_SOURCE",
                    field,
                    f"The {field} value is not attributed to an implemented weather provider.",
                )
            )
        if measurement.unit != expected_unit:
            reasons.append(
                cls._reason(
                    "UNIT_MISMATCH",
                    field,
                    f"Expected {expected_unit} for {field}; received {measurement.unit or 'no unit'}.",
                )
            )

        value = measurement.value
        if (
            not cls._finite_number(value)
            or not minimum <= float(value) <= maximum
        ):
            range_text = (
                f"{minimum:g} to {maximum:g} {expected_unit}"
                if field == "temperature"
                else f"{minimum:g} to {maximum:g}%"
            )
            reasons.append(
                cls._reason(
                    "INVALID_MEASUREMENT_VALUE",
                    field,
                    f"The {field} value must be finite and within the broad input-validation range {range_text}.",
                )
            )

        forecast_time = cls._parse_timestamp(measurement.forecast_time)
        if (
            forecast_time is None
            or forecast_time < now
            or forecast_time > now + MAX_FORECAST_LEAD
            or measurement.provenance.get("forecast_time") != measurement.forecast_time
        ):
            reasons.append(
                cls._reason(
                    "STALE_OR_INVALID_FORECAST_TIME",
                    field,
                    f"The {field} forecast time must be timezone-aware, current or future, and no more than two hours ahead.",
                )
            )

        provenance_latitude = measurement.provenance.get("requested_latitude")
        provenance_longitude = measurement.provenance.get("requested_longitude")
        if not cls._coordinates_match(
            provenance_latitude,
            provenance_longitude,
            requested_latitude,
            requested_longitude,
        ):
            reasons.append(
                cls._reason(
                    "MEASUREMENT_LOCATION_MISMATCH",
                    field,
                    f"The {field} measurement provenance does not match the selected location.",
                )
            )

        return not reasons, forecast_time, reasons

    @staticmethod
    def _measurement_output(
        measurement: WeatherMeasurement | None,
        *,
        validated: bool,
    ) -> dict[str, Any]:
        return {
            "value": measurement.value if measurement is not None and validated else None,
            "unit": measurement.unit if measurement is not None else "",
            "status": (
                "VALIDATED"
                if validated
                else "REJECTED"
                if measurement is not None and measurement.value is not None
                else "UNAVAILABLE"
            ),
            "dataType": measurement.data_type if measurement is not None else "FORECAST",
            "forecastTime": measurement.forecast_time if measurement is not None else None,
        }

    @staticmethod
    def _finish(
        result: dict[str, Any],
        status: str,
        validation_reasons: list[dict[str, str]],
        missing_reasons: list[dict[str, str]],
    ) -> dict[str, Any]:
        result["status"] = status
        result["reasons"] = validation_reasons + missing_reasons
        return result

    @staticmethod
    def _reason(code: str, field: str, message: str) -> dict[str, str]:
        return {"code": code, "input": field, "message": message}

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _valid_coordinate(value: Any, minimum: float, maximum: float) -> bool:
        return (
            RealHeatAssessmentService._finite_number(value)
            and minimum <= float(value) <= maximum
        )

    @staticmethod
    def _finite_number(value: Any) -> bool:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except (OverflowError, ValueError):
            return False

    @classmethod
    def _coordinates_match(
        cls,
        latitude: Any,
        longitude: Any,
        expected_latitude: float,
        expected_longitude: float,
    ) -> bool:
        return (
            cls._valid_coordinate(latitude, -90, 90)
            and cls._valid_coordinate(longitude, -180, 180)
            and math.isclose(float(latitude), expected_latitude, rel_tol=0, abs_tol=1e-6)
            and math.isclose(float(longitude), expected_longitude, rel_tol=0, abs_tol=1e-6)
        )
