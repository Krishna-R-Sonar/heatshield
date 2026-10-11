from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.providers.base import (
    GlacierDataProvider,
    PopulationExposureProvider,
    ProviderResult,
    MeasurementProvenance,
    RainfallDataProvider,
    RiverDataProvider,
    WeatherDataProvider,
)


def _provider_result(
    store: "DemoDataStore",
    location_id: str,
    values: dict[str, Any],
    units: dict[str, str],
    provenance: str,
) -> ProviderResult:
    location = store.get_location(location_id)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    quality = (
        "Synthetic simulation values; not real-world observations."
        if provenance == "DEMO_SIMULATION"
        else "Synthetic demonstration values; not real-world observations."
    )
    data_type = "SIMULATION" if provenance == "DEMO_SIMULATION" else "DEMO"
    normalized = {
        key: {
            "value": value,
            "unit": units.get(key, ""),
            "status": "unavailable" if value is None else (
                "simulated" if provenance == "DEMO_SIMULATION" else "synthetic"
            ),
            "dataType": data_type,
        }
        for key, value in values.items()
    }
    normalized_values: list[MeasurementProvenance] = []
    for key, value in normalized.items():
        measurement: MeasurementProvenance = {
            "source": "HeatShield Demo Dataset",
            "source_url": "",
            "location_id": location_id,
            "latitude": location.get("lat") if location else None,
            "longitude": location.get("lng") if location else None,
            "data_type": data_type,
            "retrieval_time": retrieved_at,
            "value": value["value"],
            "unit": value["unit"],
            "quality": quality,
            "status": value["status"],
        }
        value["provenance"] = measurement
        normalized_values.append(measurement)
    available_count = sum(value["status"] != "unavailable" for value in normalized.values())
    status = "unavailable" if available_count == 0 else (
        "available" if available_count == len(normalized) else "partial"
    )
    return {
        "source": "HeatShield Demo Dataset",
        "timestamp": retrieved_at,
        "retrievedAt": retrieved_at,
        "dataType": "simulation" if provenance == "DEMO_SIMULATION" else "demo",
        "location": {
            "id": location_id,
            "name": location.get("name") if location else None,
            "state": location.get("state") if location else None,
            "lat": location.get("lat") if location else None,
            "lng": location.get("lng") if location else None,
        },
        "status": status,
        "dataQuality": quality,
        "dataProvenance": provenance,
        "values": normalized,
        "provenance": {
            "source": "HeatShield Demo Dataset",
            "source_url": "",
            "location_id": location_id,
            "latitude": location.get("lat") if location else None,
            "longitude": location.get("lng") if location else None,
            "data_type": data_type,
            "retrieval_time": retrieved_at,
            "quality": quality,
            "status": status,
        },
        "measurements": normalized_values,
    }


class DemoDataStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._locations = self._load("locations.json")
        self._baselines = self._load("environmental_baselines.json")
        self._presets = self._load("simulation_presets.json")
        self.simulation: dict[str, Any] | None = None

    def _load(self, name: str) -> dict[str, Any]:
        with (self.data_dir / name).open(encoding="utf-8") as f:
            return json.load(f)

    def list_locations(self) -> list[dict[str, Any]]:
        return self._locations["locations"]

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        for loc in self.list_locations():
            if loc["id"] == location_id:
                return loc
        return None

    def flood_baseline(self, location_id: str) -> dict[str, Any]:
        return dict(self._baselines.get("flood", {}).get(location_id, {}))

    def heat_baseline(self, location_id: str) -> dict[str, Any]:
        return dict(self._baselines.get("heat", {}).get(location_id, {}))

    def apply_simulation_flood(self, location_id: str, preset: str) -> dict[str, Any]:
        base = self.flood_baseline(location_id)
        preset_data = self._presets["flood_glof"].get(preset, {})
        simulation = self.simulation
        parameters = (
            simulation.get("parameters", {})
            if simulation
            and simulation.get("locationId") == location_id
            and simulation.get("scenario") == "flood_glof"
            else {}
        )
        merged = {**base, **preset_data, **parameters}
        return merged

    def apply_simulation_heat(self, location_id: str, preset: str) -> dict[str, Any]:
        base = self.heat_baseline(location_id)
        preset_data = self._presets["extreme_heat"].get(preset, {})
        return {**base, **preset_data}


class DemoWeatherProvider(WeatherDataProvider):
    def __init__(self, store: DemoDataStore) -> None:
        self.store = store

    def get_heat_observations(self, location_id: str) -> ProviderResult:
        if self.store.simulation and self.store.simulation.get("locationId") == location_id:
            if self.store.simulation.get("scenario") == "extreme_heat":
                preset = self.store.simulation.get("preset", "normal")
                data = self.store.apply_simulation_heat(location_id, preset)
                provenance = "DEMO_SIMULATION"
            else:
                data = self.store.heat_baseline(location_id)
                provenance = "DEMO"
        else:
            data = self.store.heat_baseline(location_id)
            provenance = "DEMO"
        return _provider_result(
            self.store,
            location_id,
            {
                "temperatureC": data.get("temperatureC"),
                "humidityPct": data.get("humidityPct"),
                "heatIndexC": data.get("heatIndexC"),
                "elevatedTempDurationHours": data.get("elevatedTempDurationHours"),
            },
            {"temperatureC": "°C", "humidityPct": "%", "heatIndexC": "°C", "elevatedTempDurationHours": "h"},
            provenance,
        )


class DemoRainfallProvider(RainfallDataProvider):
    def __init__(self, store: DemoDataStore) -> None:
        self.store = store

    def get_rainfall_observations(self, location_id: str) -> ProviderResult:
        provenance = "DEMO"
        if self.store.simulation and self.store.simulation.get("locationId") == location_id:
            if self.store.simulation.get("scenario") == "flood_glof":
                preset = self.store.simulation.get("preset", "normal")
                data = self.store.apply_simulation_flood(location_id, preset)
                provenance = "DEMO_SIMULATION"
            else:
                data = self.store.flood_baseline(location_id)
        else:
            data = self.store.flood_baseline(location_id)
        return _provider_result(
            self.store,
            location_id,
            {
                "rainfallIntensityMmHr": data.get("rainfallIntensityMmHr"),
                "cumulativeRainfallMm24h": data.get("cumulativeRainfallMm24h"),
            },
            {"rainfallIntensityMmHr": "mm/h", "cumulativeRainfallMm24h": "mm/24h"},
            provenance,
        )


class DemoRiverProvider(RiverDataProvider):
    def __init__(self, store: DemoDataStore) -> None:
        self.store = store

    def get_river_observations(self, location_id: str) -> ProviderResult:
        provenance = "DEMO"
        if self.store.simulation and self.store.simulation.get("locationId") == location_id:
            if self.store.simulation.get("scenario") == "flood_glof":
                preset = self.store.simulation.get("preset", "normal")
                data = self.store.apply_simulation_flood(location_id, preset)
                provenance = "DEMO_SIMULATION"
            else:
                data = self.store.flood_baseline(location_id)
        else:
            data = self.store.flood_baseline(location_id)
        return _provider_result(
            self.store,
            location_id,
            {"riverLevelM": data.get("riverLevelM"), "riverThresholdM": data.get("riverThresholdM")},
            {"riverLevelM": "m", "riverThresholdM": "m"},
            provenance,
        )


class DemoGlacierProvider(GlacierDataProvider):
    def __init__(self, store: DemoDataStore) -> None:
        self.store = store

    def get_glacial_lake_status(self, location_id: str) -> ProviderResult:
        provenance = "DEMO"
        if self.store.simulation and self.store.simulation.get("locationId") == location_id:
            if self.store.simulation.get("scenario") == "flood_glof":
                preset = self.store.simulation.get("preset", "normal")
                data = self.store.apply_simulation_flood(location_id, preset)
                provenance = "DEMO_SIMULATION"
            else:
                data = self.store.flood_baseline(location_id)
        else:
            data = self.store.flood_baseline(location_id)
        lake = data.get("glacialLake") or {"available": False}
        return _provider_result(
            self.store,
            location_id,
            {
                "available": lake.get("available", False),
                "lakeId": lake.get("lakeId"),
                "status": lake.get("status") if lake.get("available") else None,
                "notes": lake.get("notes"),
            },
            {"available": "boolean", "lakeId": "identifier", "status": "monitoring status", "notes": ""},
            provenance,
        )


class DemoPopulationExposureProvider(PopulationExposureProvider):
    def __init__(self, store: DemoDataStore) -> None:
        self.store = store

    def get_exposure_indices(self, location_id: str) -> ProviderResult:
        loc = self.store.get_location(location_id)
        return _provider_result(
            self.store,
            location_id,
            {
                "populationExposureIndex": float(loc["populationExposureIndex"]) if loc and loc.get("populationExposureIndex") is not None else None,
                "infrastructureExposureIndex": float(loc["infrastructureExposureIndex"]) if loc and loc.get("infrastructureExposureIndex") is not None else None,
            },
            {"populationExposureIndex": "index (0-1)", "infrastructureExposureIndex": "index (0-1)"},
            "DEMO",
        )
