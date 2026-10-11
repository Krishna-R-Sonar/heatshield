from __future__ import annotations

import math
from typing import Any

import httpx

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_DAILY_VARIABLES = (
    "temperature_2m_max",
    "precipitation_sum",
    "rain_sum",
)
HISTORICAL_START_DATE = "1991-01-01"
HISTORICAL_END_DATE = "2020-12-31"


class OpenMeteoHistoricalError(RuntimeError):
    pass


class OpenMeteoHistoricalProvider:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def get_daily_baseline(
        self,
        latitude: float,
        longitude: float,
        *,
        timezone_name: str = "UTC",
    ) -> dict[str, Any]:
        if (
            not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            raise OpenMeteoHistoricalError("Location coordinates are missing or invalid.")
        if not isinstance(timezone_name, str) or not timezone_name.strip():
            raise OpenMeteoHistoricalError("Forecast timezone is missing or invalid.")
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": HISTORICAL_START_DATE,
            "end_date": HISTORICAL_END_DATE,
            "daily": ",".join(HISTORICAL_DAILY_VARIABLES),
            "models": "era5",
            "timezone": timezone_name,
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
        }
        try:
            if self.client is None:
                response = httpx.get(
                    OPEN_METEO_ARCHIVE_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.client.get(
                    OPEN_METEO_ARCHIVE_URL,
                    params=params,
                    timeout=self.timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise OpenMeteoHistoricalError("Open-Meteo historical baseline request timed out.") from exc
        except httpx.HTTPStatusError as exc:
            raise OpenMeteoHistoricalError(
                f"Open-Meteo historical API returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise OpenMeteoHistoricalError("Open-Meteo historical API could not be reached.") from exc
        except ValueError as exc:
            raise OpenMeteoHistoricalError("Open-Meteo returned malformed historical data.") from exc

        daily = payload.get("daily") if isinstance(payload, dict) else None
        units = payload.get("daily_units") if isinstance(payload, dict) else None
        times = daily.get("time") if isinstance(daily, dict) else None
        if not isinstance(daily, dict) or not isinstance(units, dict) or not isinstance(times, list):
            raise OpenMeteoHistoricalError("Historical response is missing daily values or units.")
        if any(not isinstance(value, str) for value in times):
            raise OpenMeteoHistoricalError("Historical response contains invalid dates.")
        if len(set(times)) != len(times):
            raise OpenMeteoHistoricalError("Historical response contains duplicate dates.")

        expected_units = {
            "temperature_2m_max": "°C",
            "precipitation_sum": "mm",
            "rain_sum": "mm",
        }
        series: dict[str, list[float | None]] = {}
        for variable in HISTORICAL_DAILY_VARIABLES:
            raw_values = daily.get(variable)
            if units.get(variable) != expected_units[variable]:
                raise OpenMeteoHistoricalError(
                    f"Historical response returned incompatible units for {variable}."
                )
            if not isinstance(raw_values, list) or len(raw_values) != len(times):
                raise OpenMeteoHistoricalError(
                    f"Historical response is missing compatible values for {variable}."
                )
            cleaned = []
            for value in raw_values:
                if value is None:
                    cleaned.append(None)
                elif (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(float(value))
                ):
                    cleaned.append(float(value))
                else:
                    cleaned.append(None)
            series[variable] = cleaned

        return {
            "times": times,
            "series": series,
            "units": expected_units,
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "elevation": payload.get("elevation"),
            "timezone": payload.get("timezone"),
        }