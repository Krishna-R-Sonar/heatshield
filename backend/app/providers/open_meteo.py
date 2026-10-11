from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.providers.base import (
    MeasurementProvenance,
    ProviderResult,
    ProviderStatus,
    ProviderValue,
    RainfallDataProvider,
    WeatherProviderError,
)
from app.providers.demo import DemoDataStore
from app.models.weather_evidence import (
    WeatherEvidence,
    WeatherLocationEvidence,
    WeatherMeasurement,
    WeatherProvenance,
    WeatherTimeSeries,
)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
MAX_HOURLY_RAIN_MM = 500.0
MAX_DAILY_RAIN_MM = 1000.0
HOURLY_WEATHER_VARIABLES = {
    "temperature_2m": "temperature",
    "apparent_temperature": "apparent_temperature",
    "relative_humidity_2m": "humidity",
    "precipitation": "precipitation",
    "rain": "rain",
    "showers": "showers",
    "precipitation_probability": "precipitation_probability",
    "wind_speed_10m": "wind_speed",
    "wind_gusts_10m": "wind_gusts",
    "wind_direction_10m": "wind_direction",
    "cloud_cover": "cloud_cover",
    "visibility": "visibility",
    "weather_code": "weather_code",
    "wet_bulb_temperature_2m": "wet_bulb_temperature",
    "uv_index": "uv_index",
    "shortwave_radiation": "shortwave_radiation",
    "et0_fao_evapotranspiration": "reference_evapotranspiration",
    "soil_moisture_0_to_1cm": "soil_moisture_0_to_1cm",
}
DAILY_WEATHER_VARIABLES = (
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "weather_code",
    "uv_index_max",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
)
HOURLY_WEATHER_UNITS = {
    "temperature_2m": "°C",
    "apparent_temperature": "°C",
    "relative_humidity_2m": "%",
    "precipitation": "mm",
    "rain": "mm",
    "showers": "mm",
    "precipitation_probability": "%",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
    "wind_direction_10m": "°",
    "cloud_cover": "%",
    "visibility": "m",
    "weather_code": "wmo code",
    "wet_bulb_temperature_2m": "°C",
    "uv_index": "",
    "shortwave_radiation": "W/m²",
    "et0_fao_evapotranspiration": "mm",
    "soil_moisture_0_to_1cm": "m³/m³",
}
DAILY_WEATHER_UNITS = {
    "temperature_2m_max": "°C",
    "temperature_2m_min": "°C",
    "apparent_temperature_max": "°C",
    "apparent_temperature_min": "°C",
    "precipitation_sum": "mm",
    "rain_sum": "mm",
    "showers_sum": "mm",
    "precipitation_probability_max": "%",
    "wind_speed_10m_max": "km/h",
    "wind_gusts_10m_max": "km/h",
    "wind_direction_10m_dominant": "°",
    "weather_code": "wmo code",
    "uv_index_max": "",
    "shortwave_radiation_sum": "MJ/m²",
    "et0_fao_evapotranspiration": "mm",
}


class OpenMeteoError(WeatherProviderError):
    pass


class OpenMeteoRainfallProvider(RainfallDataProvider):
    provider_name = "Open-Meteo"

    def __init__(
        self,
        store: DemoDataStore,
        *,
        timeout_seconds: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.store = store
        self.timeout_seconds = timeout_seconds
        self.client = client

    def search_locations(self, query: str) -> list[dict[str, Any]]:
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_GEOCODING_URL,
                    params={"name": query, "count": 10, "language": "en", "format": "json"},
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_GEOCODING_URL,
                    params={"name": query, "count": 10, "language": "en", "format": "json"},
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoError("Open-Meteo location search timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoError(
                f"Open-Meteo location search returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoError("Open-Meteo location search could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoError("Open-Meteo returned malformed location-search data.") from exc

        records = payload.get("results", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise OpenMeteoError("Open-Meteo returned malformed location-search data.")

        locations = []
        for record in records:
            if not isinstance(record, dict):
                continue
            latitude = record.get("latitude")
            longitude = record.get("longitude")
            name = record.get("name")
            if (
                not isinstance(latitude, (int, float))
                or isinstance(latitude, bool)
                or not math.isfinite(float(latitude))
                or not -90 <= latitude <= 90
                or not isinstance(longitude, (int, float))
                or isinstance(longitude, bool)
                or not math.isfinite(float(longitude))
                or not -180 <= longitude <= 180
                or not isinstance(name, str)
            ):
                continue
            country = record.get("country")
            admin1 = record.get("admin1")
            locations.append({
                "id": f"open-meteo-{record.get('id', len(locations))}",
                "name": name,
                "state": admin1 or country or "",
                "country": country or "",
                "lat": float(latitude),
                "lng": float(longitude),
            })
        return locations

    @staticmethod
    def _valid_weather_value(variable: str, value: Any) -> float | int | None:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        if not math.isfinite(float(value)):
            return None
        if variable in {"relative_humidity_2m", "precipitation_probability", "cloud_cover"}:
            if not 0 <= float(value) <= 100:
                return None
        if variable in {
            "precipitation", "rain", "showers", "wind_speed_10m", "wind_gusts_10m",
            "visibility", "uv_index", "et0_fao_evapotranspiration", "shortwave_radiation",
        } and value < 0:
            return None
        if variable == "soil_moisture_0_to_1cm" and not 0 <= float(value) <= 1:
            return None
        if variable == "weather_code":
            return int(value) if float(value).is_integer() else None
        return float(value)

    def get_weather_evidence_for_location(
        self,
        location: dict[str, Any],
    ) -> WeatherEvidence:
        latitude = location.get("lat")
        longitude = location.get("lng")
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not math.isfinite(float(latitude))
            or not -90 <= latitude <= 90
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not math.isfinite(float(longitude))
            or not -180 <= longitude <= 180
        ):
            raise OpenMeteoError("Location coordinates are missing or invalid.")

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_WEATHER_VARIABLES),
            "daily": ",".join(DAILY_WEATHER_VARIABLES),
            "forecast_days": 7,
            "timezone": "auto",
            "wind_speed_unit": "kmh",
            "precipitation_unit": "mm",
        }
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoError("Open-Meteo weather forecast timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoError(
                f"Open-Meteo returned HTTP {exc.response.status_code} for weather evidence."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoError("Open-Meteo weather forecast could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoError("Open-Meteo returned malformed weather forecast data.") from exc

        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        hourly_units = payload.get("hourly_units") if isinstance(payload, dict) else None
        daily = payload.get("daily") if isinstance(payload, dict) else None
        daily_units = payload.get("daily_units") if isinstance(payload, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        daily_times = daily.get("time") if isinstance(daily, dict) else None
        timezone_name = payload.get("timezone") if isinstance(payload, dict) else None
        if (
            not isinstance(hourly, dict)
            or not isinstance(hourly_units, dict)
            or not isinstance(times, list)
            or not times
            or not isinstance(daily, dict)
            or not isinstance(daily_units, dict)
            or not isinstance(daily_times, list)
            or not isinstance(timezone_name, str)
        ):
            raise OpenMeteoError("Open-Meteo response is missing forecast times, units, or timezone.")

        utc_offset_seconds = payload.get("utc_offset_seconds")
        if not isinstance(utc_offset_seconds, int) or isinstance(utc_offset_seconds, bool):
            raise OpenMeteoError("Open-Meteo response is missing a valid UTC offset.")
        try:
            local_timezone = timezone(timedelta(seconds=utc_offset_seconds))
        except ValueError as exc:
            raise OpenMeteoError("Open-Meteo response contains an invalid UTC offset.") from exc
        try:
            parsed_times = []
            for value in times:
                if not isinstance(value, str):
                    raise ValueError("Invalid hourly timestamp")
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=local_timezone)
                parsed_times.append(parsed)
        except ValueError as exc:
            raise OpenMeteoError("Open-Meteo returned invalid forecast timestamps or timezone.") from exc

        now = datetime.now(local_timezone)
        first_index = next(
            (index for index, value in enumerate(parsed_times) if value >= now),
            None,
        )
        if first_index is None or parsed_times[first_index] - now > timedelta(hours=2):
            raise OpenMeteoError("Open-Meteo forecast has no current or next-hour data.")
        forecast_time = parsed_times[first_index].isoformat()
        retrieved_at = datetime.now(timezone.utc).isoformat()
        grid_latitude = payload.get("latitude")
        grid_longitude = payload.get("longitude")
        quality = (
            "Weather-model forecast; values are not ground observations. "
            "Open-Meteo auto-selects the applicable model and does not return its exact model ID."
        )
        limitations = [
            "Forecast values are model output and are not station observations.",
            "The API response does not expose the exact selected model ID or its spatial resolution.",
            "Forecast generation/initialization time is not included in the response; generationtime_ms is API processing time, not forecast issue time.",
        ]

        hourly_values: dict[str, list[float | int | None]] = {}
        hourly_units_result: dict[str, str] = {}
        current_forecast: dict[str, WeatherMeasurement] = {}
        available_count = 0
        for api_name, public_name in HOURLY_WEATHER_VARIABLES.items():
            raw_values = hourly.get(api_name)
            unit = hourly_units.get(api_name)
            if not isinstance(unit, str):
                unit = ""
            if (
                unit == HOURLY_WEATHER_UNITS[api_name]
                and isinstance(raw_values, list)
                and len(raw_values) == len(times)
            ):
                cleaned = [self._valid_weather_value(api_name, value) for value in raw_values]
            else:
                cleaned = [None] * len(times)
            hourly_values[api_name] = cleaned
            hourly_units_result[api_name] = unit
            value = cleaned[first_index]
            status = "AVAILABLE" if value is not None else "UNAVAILABLE"
            available_count += status == "AVAILABLE"
            current_forecast[public_name] = WeatherMeasurement(
                value=value,
                unit=unit,
                status=status,
                forecast_time=forecast_time if value is not None else None,
                provenance={
                    "source": "Open-Meteo",
                    "source_url": "https://open-meteo.com/",
                    "api": "Weather Forecast API",
                    "data_type": "FORECAST",
                    "forecast_time": forecast_time if value is not None else None,
                    "aggregation_period": "one-hour forecast period",
                    "retrieval_time": retrieved_at,
                    "requested_latitude": float(latitude),
                    "requested_longitude": float(longitude),
                    "grid_latitude": grid_latitude,
                    "grid_longitude": grid_longitude,
                    "timezone": timezone_name,
                    "quality": quality,
                },
            )

        daily_values: dict[str, list[float | int | None]] = {}
        daily_units_result: dict[str, str] = {}
        daily_available_count = 0
        daily_values_complete = bool(daily_times)
        for variable in DAILY_WEATHER_VARIABLES:
            raw_values = daily.get(variable)
            unit = daily_units.get(variable)
            if not isinstance(unit, str):
                unit = ""
            if (
                unit == DAILY_WEATHER_UNITS[variable]
                and isinstance(raw_values, list)
                and len(raw_values) == len(daily_times)
            ):
                cleaned = [self._valid_weather_value(variable, value) for value in raw_values]
            else:
                cleaned = [None] * len(daily_times)
            daily_available_count += any(value is not None for value in cleaned)
            daily_values_complete = daily_values_complete and (
                len(cleaned) == len(daily_times)
                and all(value is not None for value in cleaned)
            )
            daily_values[variable] = cleaned
            daily_units_result[variable] = unit

        location_evidence = WeatherLocationEvidence(
            name=str(location.get("name") or "Selected map point"),
            state=str(location.get("state") or ""),
            country=str(location.get("country") or ""),
            requested_latitude=float(latitude),
            requested_longitude=float(longitude),
            grid_latitude=grid_latitude if isinstance(grid_latitude, (int, float)) else None,
            grid_longitude=grid_longitude if isinstance(grid_longitude, (int, float)) else None,
            elevation_m=payload.get("elevation") if isinstance(payload.get("elevation"), (int, float)) else None,
            timezone=timezone_name,
        )
        provenance = WeatherProvenance(
            model_id=None,
            retrieved_at=retrieved_at,
            generationtime_ms=(
                float(payload["generationtime_ms"])
                if isinstance(payload.get("generationtime_ms"), (int, float))
                else None
            ),
            timezone=timezone_name,
            quality=quality,
            limitations=limitations,
        )
        status = "UNAVAILABLE" if available_count + daily_available_count == 0 else (
            "AVAILABLE"
            if available_count == len(HOURLY_WEATHER_VARIABLES) and daily_values_complete
            else "DEGRADED"
        )
        return WeatherEvidence(
            status=status,
            location=location_evidence,
            forecast=current_forecast,
            hourly=WeatherTimeSeries(
                times=[value.isoformat() for value in parsed_times],
                values=hourly_values,
                units=hourly_units_result,
                interval_minutes=60,
                aggregation_period="one-hour forecast periods",
            ),
            daily=WeatherTimeSeries(
                times=[str(value) for value in daily_times],
                values=daily_values,
                units=daily_units_result,
                aggregation_period="local calendar day",
            ),
            provenance=provenance,
        )

    def _result(
        self,
        location_id: str,
        values: dict[str, ProviderValue],
        *,
        status: ProviderStatus,
        quality: str,
        location_data: dict[str, Any] | None = None,
    ) -> ProviderResult:
        location = location_data or self.store.get_location(location_id) or {}
        retrieved_at = datetime.now(timezone.utc).isoformat()
        latitude = location.get("lat") if location else None
        longitude = location.get("lng") if location else None
        measurements: list[MeasurementProvenance] = []
        for value in values.values():
            measurement: MeasurementProvenance = {
                "source": "Open-Meteo",
                "source_url": "https://open-meteo.com/",
                "location_id": location_id,
                "latitude": latitude,
                "longitude": longitude,
                "data_type": "FORECAST",
                "forecast_time": value.get("validTime"),
                "retrieval_time": retrieved_at,
                "value": value["value"],
                "unit": value["unit"],
                "quality": quality,
                "status": value["status"],
            }
            value["provenance"] = measurement
            measurements.append(measurement)
        return {
            "source": "Open-Meteo (weather model forecast)",
            "sourceUrl": "https://open-meteo.com/",
            "timestamp": retrieved_at,
            "retrievedAt": retrieved_at,
            "dataType": "forecast",
            "location": {
                "id": location_id,
                "name": location.get("name") if location else None,
                "state": location.get("state") if location else None,
                "lat": location.get("lat") if location else None,
                "lng": location.get("lng") if location else None,
            },
            "status": status,
            "dataQuality": quality,
            "dataProvenance": "LIVE",
            "values": values,
            "provenance": {
                "source": "Open-Meteo",
                "source_url": "https://open-meteo.com/",
                "location_id": location_id,
                "latitude": latitude,
                "longitude": longitude,
                "data_type": "FORECAST",
                "retrieval_time": retrieved_at,
                "quality": quality,
                "status": status,
            },
            "measurements": measurements,
        }

    def _unavailable(
        self,
        location_id: str,
        reason: str,
        *,
        location_data: dict[str, Any] | None = None,
    ) -> ProviderResult:
        return self._result(
            location_id,
            {
                "rainfallIntensityMmHr": {
                    "value": None,
                    "unit": "mm/h",
                    "status": "unavailable",
                    "dataType": "forecast",
                    "validTime": None,
                },
                "cumulativeRainfallMm24h": {
                    "value": None,
                    "unit": "mm/24h",
                    "status": "unavailable",
                    "dataType": "forecast",
                    "validTime": None,
                },
            },
            status="unavailable",
            quality=reason,
            location_data=location_data,
        )

    def get_rainfall_observations(self, location_id: str) -> ProviderResult:
        location = self.store.get_location(location_id)
        if not location:
            return self._unavailable(location_id, "Unknown location; no forecast was requested.")

        return self.get_rainfall_for_location(location)

    def get_rainfall_for_location(self, location: dict[str, Any]) -> ProviderResult:
        location_id = str(location.get("id", "global-location"))

        def unavailable(reason: str) -> ProviderResult:
            return self._unavailable(location_id, reason, location_data=location)

        latitude = location.get("lat")
        longitude = location.get("lng")
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not math.isfinite(float(latitude))
            or not -90 <= latitude <= 90
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not math.isfinite(float(longitude))
            or not -180 <= longitude <= 180
        ):
            return unavailable("Location coordinates are missing or invalid.")

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation",
            "forecast_days": 2,
            "timezone": "UTC",
        }
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
        except httpx.TimeoutException:
            return unavailable("Open-Meteo request timed out.")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                reason = "Open-Meteo rate limit reached; forecast unavailable."
            else:
                reason = f"Open-Meteo returned HTTP {exc.response.status_code}; forecast unavailable."
            return unavailable(reason)
        except httpx.RequestError:
            return unavailable("Open-Meteo could not be reached.")

        try:
            payload = response.json()
        except ValueError:
            return unavailable("Open-Meteo returned malformed JSON.")

        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        units = payload.get("hourly_units") if isinstance(payload, dict) else None
        times = hourly.get("time") if isinstance(hourly, dict) else None
        precipitation = hourly.get("precipitation") if isinstance(hourly, dict) else None
        if (
            not isinstance(units, dict)
            or units.get("precipitation") != "mm"
            or not isinstance(times, list)
            or not isinstance(precipitation, list)
            or len(times) != len(precipitation)
        ):
            return unavailable("Open-Meteo response is missing compatible hourly precipitation.")

        try:
            valid_times = []
            for value in times:
                if not isinstance(value, str):
                    continue
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                valid_times.append(parsed)
        except ValueError:
            return unavailable("Open-Meteo returned invalid forecast timestamps.")
        if len(valid_times) != len(times):
            return unavailable("Open-Meteo returned invalid forecast timestamps.")

        now = datetime.now(timezone.utc)
        first_index = next(
            (index for index, value in enumerate(valid_times) if value >= now),
            None,
        )
        if first_index is None:
            return unavailable("Open-Meteo forecast is stale; no future hourly data is available.")
        if valid_times[first_index] - now > timedelta(hours=2):
            return unavailable("Open-Meteo forecast is stale; the next forecast hour is missing.")

        # The legacy risk-input contract expresses the one-hour accumulation as its
        # period-average rate; the numeric value is equal only because the interval is 1 hour.
        first_value = precipitation[first_index]
        first_time = valid_times[first_index]
        intensity = (
            float(first_value)
            if isinstance(first_value, (int, float))
            and not isinstance(first_value, bool)
            and math.isfinite(float(first_value))
            and 0 <= float(first_value) <= MAX_HOURLY_RAIN_MM
            else None
        )

        window_values = precipitation[first_index : first_index + 24]
        window_times = valid_times[first_index : first_index + 24]
        window_is_complete = (
            len(window_values) == 24
            and len(window_times) == 24
            and all(
                right - left == timedelta(hours=1)
                for left, right in zip(window_times, window_times[1:])
            )
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and 0 <= float(value) <= MAX_HOURLY_RAIN_MM
                for value in window_values
            )
        )
        cumulative = sum(float(value) for value in window_values) if window_is_complete else None
        if cumulative is not None and cumulative > MAX_DAILY_RAIN_MM:
            cumulative = None

        values = {
            "rainfallIntensityMmHr": {
                "value": intensity,
                "unit": "mm/h",
                "status": "forecast" if intensity is not None else "unavailable",
                "dataType": "forecast",
                "validTime": first_time.isoformat() if intensity is not None else None,
            },
            "cumulativeRainfallMm24h": {
                "value": cumulative,
                "unit": "mm/24h",
                "status": "forecast" if cumulative is not None else "unavailable",
                "dataType": "forecast",
                "validTime": first_time.isoformat() if cumulative is not None else None,
            },
        }
        available_count = sum(value["status"] == "forecast" for value in values.values())
        status: ProviderStatus = (
            "unavailable"
            if available_count == 0
            else "available"
            if available_count == len(values)
            else "partial"
        )
        return self._result(
            location_id,
            values,
            status=status,
            quality=(
                "Weather-model forecast, not a ground-based observation. "
                "Forecast values are not used as observed rainfall in risk scoring."
            ),
            location_data=location,
        )
