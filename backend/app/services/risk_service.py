from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from heatshield_risk.engine import assess_location
from heatshield_risk.models import FloodInputs, GlacialLakeInfo, HeatInputs, LocationInfo

from app.providers.base import (
    GlacierDataProvider,
    PopulationExposureProvider,
    RainfallDataProvider,
    RiverDataProvider,
    WeatherDataProvider,
)
from app.providers.demo import (
    DemoDataStore,
    DemoGlacierProvider,
    DemoPopulationExposureProvider,
    DemoRainfallProvider,
    DemoRiverProvider,
    DemoWeatherProvider,
)


class RiskService:
    def __init__(
        self,
        store: DemoDataStore,
        *,
        weather: WeatherDataProvider | None = None,
        rainfall: RainfallDataProvider | None = None,
        river: RiverDataProvider | None = None,
        glacier: GlacierDataProvider | None = None,
        exposure: PopulationExposureProvider | None = None,
    ) -> None:
        self.store = store
        self.weather = weather or DemoWeatherProvider(store)
        self.rainfall = rainfall or DemoRainfallProvider(store)
        self.river = river or DemoRiverProvider(store)
        self.glacier = glacier or DemoGlacierProvider(store)
        self.exposure = exposure or DemoPopulationExposureProvider(store)

    @staticmethod
    def _value(provider_result: dict[str, Any], key: str) -> Any:
        return provider_result.get("values", {}).get(key, {}).get("value")

    def _simulation_active_for(self, location_id: str) -> bool:
        sim = self.store.simulation
        return bool(sim and sim.get("locationId") == location_id)

    def assess(self, location_id: str) -> dict[str, Any]:
        loc_raw = self.store.get_location(location_id)
        if not loc_raw:
            raise KeyError(location_id)

        location = LocationInfo(
            id=loc_raw["id"],
            name=loc_raw["name"],
            state=loc_raw["state"],
            lat=float(loc_raw["lat"]),
            lng=float(loc_raw["lng"]),
        )
        scenario = loc_raw["primaryScenario"]
        exposure = self.exposure.get_exposure_indices(location_id)
        population_exposure = self._value(exposure, "populationExposureIndex")
        infrastructure_exposure = self._value(exposure, "infrastructureExposureIndex")
        ts = datetime.now(timezone.utc).isoformat()
        sim_active = self._simulation_active_for(location_id)

        if scenario == "flood_glof":
            rain = self.rainfall.get_rainfall_observations(location_id)
            river = self.river.get_river_observations(location_id)
            lake_raw = self.glacier.get_glacial_lake_status(location_id)
            base = self.store.flood_baseline(location_id)

            lake = GlacialLakeInfo(
                available=bool(self._value(lake_raw, "available")),
                lake_id=self._value(lake_raw, "lakeId"),
                status=self._value(lake_raw, "status"),
                notes=self._value(lake_raw, "notes"),
            )
            flood = FloodInputs(
                location_id=location_id,
                rainfall_intensity_mm_hr=self._value(rain, "rainfallIntensityMmHr"),
                cumulative_rainfall_mm_24h=self._value(rain, "cumulativeRainfallMm24h"),
                river_level_m=self._value(river, "riverLevelM"),
                river_threshold_m=self._value(river, "riverThresholdM"),
                glacial_lake=lake,
                historical_flood_events_5y=base.get("historicalFloodEvents5y"),
                population_exposure_index=population_exposure,
                infrastructure_exposure_index=infrastructure_exposure,
                data_provenance=rain.get("dataProvenance", "DEMO"),
                timestamp=ts,
            )
            result = assess_location(
                location, scenario, flood, None, simulation_active=sim_active, timestamp=ts
            )
        else:
            heat_raw = self.weather.get_heat_observations(location_id)
            heat = HeatInputs(
                location_id=location_id,
                temperature_c=self._value(heat_raw, "temperatureC"),
                humidity_pct=self._value(heat_raw, "humidityPct"),
                heat_index_c=self._value(heat_raw, "heatIndexC"),
                elevated_temp_duration_hours=self._value(heat_raw, "elevatedTempDurationHours"),
                population_exposure_index=population_exposure,
                infrastructure_exposure_index=infrastructure_exposure,
                data_provenance=heat_raw.get("dataProvenance", "DEMO"),
                timestamp=ts,
            )
            result = assess_location(
                location, scenario, None, heat, simulation_active=sim_active, timestamp=ts
            )

        assessment = result.to_dict()
        assessment["dataProvenance"] = "DEMO_SIMULATION" if sim_active else "DEMO"
        assessment["providerObservations"] = {
            "exposure": exposure,
            **(
                {"rainfall": rain, "river": river, "glacialLake": lake_raw}
                if scenario == "flood_glof"
                else {"weather": heat_raw}
            ),
        }
        factor_levels = {factor.id: factor.severity for factor in result.contributing_factors}
        if sim_active:
            comparison_fields = (
                [
                    ("Rainfall intensity", "rainfallIntensityMmHr", "mm/h", "rainfall"),
                    ("24h cumulative rainfall", "cumulativeRainfallMm24h", "mm/24h", "rainfall"),
                    ("River level", "riverLevelM", "m", "river"),
                ]
                if scenario == "flood_glof"
                else [
                    ("Temperature", "temperatureC", "°C", "weather"),
                    ("Humidity", "humidityPct", "%", "weather"),
                    ("Heat index", "heatIndexC", "°C", "weather"),
                ]
            )
            baseline = (
                self.store.flood_baseline(location_id)
                if scenario == "flood_glof"
                else self.store.heat_baseline(location_id)
            )
            provider_results = (
                {"rainfall": rain, "river": river}
                if scenario == "flood_glof"
                else {"weather": heat_raw}
            )
            changes = []
            for label, key, unit, provider_name in comparison_fields:
                baseline_value = baseline.get(key)
                current_value = self._value(provider_results[provider_name], key)
                if isinstance(baseline_value, (int, float)) and isinstance(current_value, (int, float)):
                    changes.append(
                        {
                            "label": label,
                            "baseline": baseline_value,
                            "current": current_value,
                            "delta": round(current_value - baseline_value, 2),
                            "unit": unit,
                        }
                    )
            assessment["simulationComparison"] = {
                "baselineLabel": "DEMO baseline",
                "currentLabel": "DEMO simulation",
                "changes": changes,
            }
        if scenario == "flood_glof":
            assessment["riskDimensions"] = {
                "overallFloodRisk": {"level": assessment["riskLevel"], "score": assessment["riskScore"]},
                "rainfallRisk": {
                    "level": factor_levels.get("rainfall", "low"),
                    "scoreContribution": result.component_scores.get("rainfall_score", 0),
                },
                "riverRisk": {
                    "level": factor_levels.get("river", "low"),
                    "scoreContribution": result.component_scores.get("river_score", 0),
                },
                "downstreamExposure": {
                    "level": factor_levels.get("exposure", "low"),
                    "scoreContribution": result.component_scores.get("exposure_score", 0),
                },
                "glacialLakeMonitoring": {
                    "status": lake.status if lake.available else "unavailable",
                    "available": lake.available,
                    "notes": lake.notes,
                },
            }
        else:
            assessment["riskDimensions"] = {
                "overallHeatRisk": {"level": assessment["riskLevel"], "score": assessment["riskScore"]},
                "heatExposure": {"scoreContribution": result.component_scores.get("exposure_score", 0)},
            }
        return assessment

    def assess_all(self) -> list[dict[str, Any]]:
        return [self.assess(loc["id"]) for loc in self.store.list_locations()]

    def assess_simulation(
        self,
        location_id: str,
        parameters: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        location = self.store.get_location(location_id)
        if not location:
            raise KeyError(location_id)
        scenario = location["primaryScenario"]
        if parameters is not None and not parameters:
            raise ValueError("At least one simulation parameter is required")
        if parameters is not None and scenario != "flood_glof":
            raise ValueError("Custom simulation parameters are currently supported for flood-risk locations only")
        limits = {
            "rainfallIntensityMmHr": (0.0, 500.0),
            "cumulativeRainfallMm24h": (0.0, 1000.0),
            "riverLevelM": (0.0, 50.0),
        }
        if parameters is not None:
            if not set(parameters).issubset(limits):
                raise ValueError("Unsupported simulation parameter")
            for name, value in parameters.items():
                minimum, maximum = limits[name]
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not minimum <= value <= maximum:
                    raise ValueError(f"Invalid value for simulation parameter {name}")

        simulation = self.store.simulation
        if parameters is not None:
            simulation = {
                "locationId": location_id,
                "scenario": "flood_glof",
                "preset": "custom",
                "parameters": parameters,
            }
        if not simulation or simulation.get("locationId") != location_id:
            raise ValueError("No active simulation is available for this location")

        baseline = self._assess_with_simulation(location_id, None)
        simulated = self._assess_with_simulation(location_id, simulation)
        return baseline, simulated

    def _assess_with_simulation(
        self,
        location_id: str,
        simulation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        store = copy.copy(self.store)
        store.simulation = simulation
        service = copy.copy(self)
        service.store = store
        for name in ("weather", "rainfall", "river", "glacier", "exposure"):
            provider = copy.copy(getattr(self, name))
            if getattr(provider, "store", None) is self.store:
                provider.store = store
            setattr(service, name, provider)
        return service.assess(location_id)
