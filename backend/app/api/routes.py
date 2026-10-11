from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import settings
from app.providers.base import ProviderResult
from app.providers.open_meteo import OpenMeteoError, OpenMeteoRainfallProvider
from app.models.weather_evidence import HistoricalAnomalyEvidence, WeatherEvidence
from app.models.flood_evidence import FloodEvidence
from app.models.ensemble_evidence import EnsembleForecastEvidence
from app.providers.open_meteo_flood import OpenMeteoFloodError
from app.providers.open_meteo_ensemble import OpenMeteoEnsembleError
from app.providers.open_meteo_validation import OpenMeteoValidationError
from app.models.forecast_validation import ForecastValidationEvidence
from app.services.alert_service import AlertService
from heatshield_ai.agent import chat_about_risk, explain_risk
from heatshield_ai.global_evidence import build_global_evidence_tools, render_global_evidence_answer
from heatshield_ai.strands_agent import run_openrouter_global_evidence

router = APIRouter()
logger = logging.getLogger(__name__)
GLOBAL_EVIDENCE_TOOLS_TIMEOUT_SECONDS = 8.0
GLOBAL_EVIDENCE_AI_TIMEOUT_SECONDS = 18.0


class SimulationStartBody(BaseModel):
    locationId: str
    scenario: Literal["flood_glof"] = "flood_glof"
    preset: Literal["normal", "heavy_rain", "river_rising"] = "heavy_rain"


class SimulationHeatBody(BaseModel):
    locationId: str
    preset: Literal["normal", "extreme_heat"] = "extreme_heat"


class SimulationParametersBody(BaseModel):
    locationId: str
    rainfallIntensityMmHr: float | None = Field(default=None, ge=0, le=500)
    cumulativeRainfallMm24h: float | None = Field(default=None, ge=0, le=1000)
    riverLevelM: float | None = Field(default=None, ge=0, le=50)

    @model_validator(mode="after")
    def require_parameter(self) -> "SimulationParametersBody":
        if all(
            value is None
            for value in [self.rainfallIntensityMmHr, self.cumulativeRainfallMm24h, self.riverLevelM]
        ):
            raise ValueError("At least one simulation parameter is required")
        return self


class ExplainBody(BaseModel):
    locationId: str
    question: str | None = Field(default="Why is this risk level assigned?", max_length=2000)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Question must not be blank")
        return value


class ChatBody(BaseModel):
    locationId: str
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value.strip()


class GlobalEvidenceQuestionBody(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    name: str = Field(min_length=1, max_length=120)
    admin1: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=120)
    selection_type: Literal["named_place", "map_point"] = "named_place"
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("name", "question")
    @classmethod
    def non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank")
        return value.strip()


def _get_services(request: Request):
    return request.app.state.store, request.app.state.risk_service, request.app.state.alert_service


def _require_risk_assessments_available() -> None:
    if settings.data_mode == "real":
        raise HTTPException(
            status_code=503,
            detail=(
                "Risk assessment unavailable in real data mode; real environmental evidence "
                "has not been validated for deterministic scoring."
            ),
        )


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "heatshield-api",
        "dataMode": settings.data_mode,
        "demoData": settings.data_mode == "demo",
    }


@router.get("/config")
def public_config() -> dict[str, Any]:
    return {"dataMode": settings.data_mode}


@router.get("/weather/search")
def search_weather_locations(
    request: Request,
    query: str = Query(min_length=2, max_length=100),
) -> dict[str, Any]:
    provider = request.app.state.rainfall_provider
    if not isinstance(provider, OpenMeteoRainfallProvider):
        raise HTTPException(status_code=503, detail="Global live weather search requires real data mode.")
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=422, detail="Search query must contain at least two characters.")
    try:
        locations = provider.search_locations(query)
    except OpenMeteoError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"locations": locations, "source": "Open-Meteo Geocoding"}


@router.get("/weather/forecast", response_model=ProviderResult)
def global_weather_forecast(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    name: str = Query(min_length=1, max_length=120),
    admin1: str = Query(default="", max_length=120),
    country: str = Query(default="", max_length=120),
) -> ProviderResult:
    provider = request.app.state.rainfall_provider
    if not isinstance(provider, OpenMeteoRainfallProvider):
        raise HTTPException(status_code=503, detail="Global live forecasts require real data mode.")
    location = {
        "id": f"point-{latitude:.5f}-{longitude:.5f}",
        "name": name,
        "state": admin1 or country or "Selected map point",
        "lat": latitude,
        "lng": longitude,
    }
    result = provider.get_rainfall_for_location(location)
    if result["status"] == "unavailable":
        raise HTTPException(status_code=503, detail=result["dataQuality"])
    return result


@router.get("/weather/evidence", response_model=WeatherEvidence)
def global_weather_evidence(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    name: str = Query(min_length=1, max_length=120),
    admin1: str = Query(default="", max_length=120),
    country: str = Query(default="", max_length=120),
    refresh: bool = Query(default=False),
) -> WeatherEvidence:
    provider = request.app.state.rainfall_provider
    if not isinstance(provider, OpenMeteoRainfallProvider):
        raise HTTPException(status_code=503, detail="Global live weather requires real data mode.")
    location = {
        "id": f"point-{latitude:.5f}-{longitude:.5f}",
        "name": name,
        "state": admin1 or country or "Selected map point",
        "country": country,
        "lat": latitude,
        "lng": longitude,
    }
    try:
        evidence_service = request.app.state.weather_evidence_service
        evidence = (
            evidence_service.get_for_location(location, force_refresh=True)
            if refresh
            else evidence_service.get_for_location(location)
        )
    except OpenMeteoError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    history_service = request.app.state.historical_baseline_service
    try:
        if history_service is not None:
            historical = (
                history_service.calculate(evidence, force_refresh=True)
                if refresh
                else history_service.calculate(evidence)
            )
        else:
            historical = HistoricalAnomalyEvidence(
                status="UNAVAILABLE",
                anomalies=[],
                methodology="Historical baseline is not configured in demo mode.",
                retrieved_at=evidence.provenance.retrieved_at,
                quality="Historical reanalysis is unavailable in demo mode.",
                limitations=["No synthetic historical comparison is substituted."],
            )
    except Exception as exc:
        historical = HistoricalAnomalyEvidence(
            status="UNAVAILABLE",
            anomalies=[],
            methodology="Historical baseline uses the 1991-2020 calendar-month method.",
            retrieved_at=evidence.provenance.retrieved_at,
            quality=f"Historical baseline unavailable: {type(exc).__name__}.",
            limitations=["Historical provider failure does not replace or invalidate live weather evidence."],
        )
    return evidence.model_copy(update={"historical_anomalies": historical})


@router.get("/flood/evidence", response_model=FloodEvidence)
def flood_evidence(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    refresh: bool = Query(default=False),
) -> FloodEvidence:
    service = request.app.state.flood_evidence_service
    if service is None:
        raise HTTPException(status_code=503, detail="Live GloFAS evidence requires real data mode.")
    try:
        return (
            service.get_for_location(latitude, longitude, force_refresh=True)
            if refresh
            else service.get_for_location(latitude, longitude)
        )
    except OpenMeteoFloodError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/weather/ensemble", response_model=EnsembleForecastEvidence)
def weather_ensemble_evidence(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    refresh: bool = Query(default=False),
) -> EnsembleForecastEvidence:
    service = request.app.state.ensemble_evidence_service
    if service is None:
        raise HTTPException(status_code=503, detail="Ensemble forecasts require real data mode.")
    try:
        return (
            service.get_for_location(latitude, longitude, force_refresh=True)
            if refresh
            else service.get_for_location(latitude, longitude)
        )
    except OpenMeteoEnsembleError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/weather/validation", response_model=ForecastValidationEvidence)
def historical_forecast_performance(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    refresh: bool = Query(default=False),
) -> ForecastValidationEvidence:
    service = request.app.state.forecast_validation_service
    if service is None:
        raise HTTPException(status_code=503, detail="Historical forecast performance requires real data mode.")
    try:
        return (
            service.get_for_location(latitude, longitude, force_refresh=True)
            if refresh
            else service.get_for_location(latitude, longitude)
        )
    except OpenMeteoValidationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/locations")
def list_locations(request: Request) -> dict[str, Any]:
    if settings.data_mode == "real":
        raise HTTPException(
            status_code=503,
            detail="The demo location catalog is unavailable in real data mode; use live weather search.",
        )
    store, _, _ = _get_services(request)
    return {"locations": store.list_locations(), "dataProvenance": "DEMO"}


@router.get("/data/rainfall/{location_id}", response_model=ProviderResult)
def rainfall_data(location_id: str, request: Request) -> ProviderResult:
    store = request.app.state.store
    if not store.get_location(location_id):
        raise HTTPException(status_code=404, detail="Location not found")
    result = request.app.state.rainfall_provider.get_rainfall_observations(location_id)
    if result["status"] == "unavailable":
        raise HTTPException(status_code=503, detail=result["dataQuality"])
    return result


@router.get("/risk")
def all_risk(request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    _, risk_service, _ = _get_services(request)
    assessments = risk_service.assess_all()
    sim = request.app.state.store.simulation
    return {
        "assessments": assessments,
        "lastUpdated": assessments[0]["timestamp"] if assessments else None,
        "simulationMode": bool(sim),
        "dataProvenance": "DEMO",
    }


@router.get("/risk/heat-forecast")
def selected_location_heat_assessment(
    request: Request,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    name: str = Query(min_length=1, max_length=120),
    admin1: str = Query(default="", max_length=120),
    country: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    if settings.data_mode != "real":
        raise HTTPException(
            status_code=503,
            detail="Forecast heat assessments require real data mode.",
        )
    service = request.app.state.real_heat_assessment_service
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Forecast heat assessment is unavailable because its evidence service is not configured.",
        )
    location = {
        "id": f"selected-point-{latitude:.5f}-{longitude:.5f}",
        "name": name,
        "state": admin1 or country or "Selected map point",
        "country": country,
        "lat": latitude,
        "lng": longitude,
    }
    return service.assess(location)


@router.get("/risk/{location_id}")
def location_risk(location_id: str, request: Request) -> dict[str, Any]:
    if settings.data_mode == "real":
        store = request.app.state.store
        location = store.get_location(location_id)
        if not location:
            raise HTTPException(status_code=404, detail="Location not found")
        if location["primaryScenario"] == "extreme_heat":
            service = request.app.state.real_heat_assessment_service
            if service is None:
                raise HTTPException(
                    status_code=503,
                    detail="Forecast heat assessment is unavailable because its evidence service is not configured.",
                )
            return service.assess(location)
    _require_risk_assessments_available()
    _, risk_service, _ = _get_services(request)
    try:
        return risk_service.assess(location_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Location not found")


@router.get("/simulation")
def get_simulation(request: Request) -> dict[str, Any]:
    sim = request.app.state.store.simulation
    if not sim:
        return {"active": False, "locationId": None, "preset": None, "scenario": None}
    return {"active": True, **sim}


@router.post("/simulation/start")
def simulation_start(body: SimulationStartBody, request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    store = request.app.state.store
    location = store.get_location(body.locationId)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    if location["primaryScenario"] != "flood_glof":
        raise HTTPException(status_code=422, detail="Flood simulation requires a flood-risk location")
    store.simulation = {
        "locationId": body.locationId,
        "scenario": "flood_glof",
        "preset": body.preset,
    }
    assessment = request.app.state.risk_service.assess(body.locationId)
    return {"simulation": store.simulation, "assessment": assessment}


@router.post("/simulation/heat")
def simulation_heat(body: SimulationHeatBody, request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    store = request.app.state.store
    location = store.get_location(body.locationId)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    if location["primaryScenario"] != "extreme_heat":
        raise HTTPException(status_code=422, detail="Heat simulation requires an extreme-heat location")
    store.simulation = {
        "locationId": body.locationId,
        "scenario": "extreme_heat",
        "preset": body.preset,
    }
    assessment = request.app.state.risk_service.assess(body.locationId)
    return {"simulation": store.simulation, "assessment": assessment}


@router.post("/simulation/parameters")
def simulation_parameters(body: SimulationParametersBody, request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    store = request.app.state.store
    location = store.get_location(body.locationId)
    if not location:
        raise HTTPException(status_code=404, detail="Location not found")
    if location["primaryScenario"] != "flood_glof":
        raise HTTPException(status_code=422, detail="Flood parameters require a flood-risk location")
    current = store.simulation
    parameters = (
        dict(current.get("parameters", {}))
        if current
        and current.get("locationId") == body.locationId
        and current.get("scenario") == "flood_glof"
        else {}
    )
    for key in ("rainfallIntensityMmHr", "cumulativeRainfallMm24h", "riverLevelM"):
        value = getattr(body, key)
        if value is not None:
            parameters[key] = value
    store.simulation = {
        "locationId": body.locationId,
        "scenario": "flood_glof",
        "preset": "custom",
        "parameters": parameters,
    }
    assessment = request.app.state.risk_service.assess(body.locationId)
    return {"simulation": store.simulation, "assessment": assessment}


@router.post("/simulation/stop")
def simulation_stop(request: Request) -> dict[str, Any]:
    request.app.state.store.simulation = None
    return {"active": False}


@router.get("/alerts")
def alerts(request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    _, risk_service, alert_service = _get_services(request)
    assessments = risk_service.assess_all()
    return {"alerts": alert_service.build_alerts(assessments)}


@router.get("/data-sources")
def data_sources() -> dict[str, Any]:
    return {
        "dataMode": settings.data_mode,
        "dataProvenance": "DEMO" if settings.data_mode == "demo" else "MODEL_EVIDENCE",
        "sources": [
            {
                "name": "HeatShield Demo Dataset",
                "url": "",
                "dataset": "data/demo/*.json",
                "updateFrequency": "static for MVP",
                "limitations": "Synthetic values for hackathon demo — not live measurements.",
            },
            {
                "name": "Open-Meteo",
                "url": "https://open-meteo.com/",
                "dataset": "Global geocoding and hourly/daily weather-model forecast evidence",
                "updateFrequency": "Forecast API; model, variables, and update cadence vary by location",
                "limitations": (
                    "Forecast, not ground observation. Auto-selected model ID and exact resolution are not returned. "
                    "Values are not used by the risk engine. "
                    "Free API is non-commercial, limited to 10,000 calls/day, and requires CC BY 4.0 attribution."
                ),
            },
            {
                "name": "Open-Meteo Global Flood API / GloFAS",
                "url": "https://open-meteo.com/en/docs/flood-api",
                "dataset": "Daily modeled river discharge and forecast ensemble quantiles",
                "updateFrequency": "Daily; forecast horizon varies by GloFAS product",
                "limitations": (
                    "Approximately 5 km grid may associate the coordinate with a nearby/largest modeled river. "
                    "GloFAS v4 reanalysis ends in July 2022; later past dates are not labeled reanalysis. "
                    "No river name or local gauge verification is provided; evidence does not change risk scores."
                ),
            },
            {
                "name": "Open-Meteo Historical Weather API / ERA5",
                "url": "https://open-meteo.com/en/docs/historical-weather-api",
                "dataset": "1991-2020 calendar-month baseline for temperature maximum, precipitation, and rain",
                "updateFrequency": "Fixed baseline; cached locally for 24 hours",
                "limitations": "Reanalysis estimate, not station observation; approximately 25 km resolution; evidence-only anomaly comparison.",
            },
            {
                "name": "Open-Meteo Ensemble API / ECMWF IFS",
                "url": "https://open-meteo.com/en/docs/ensemble-api",
                "dataset": "72-hour temperature and precipitation ensemble member summaries",
                "updateFrequency": "Ensemble model update cadence",
                "limitations": "Approximately 25 km resolution; spread categories are display-only and are not disaster probabilities or risk rules.",
            },
            {
                "name": "Open-Meteo Previous Runs API",
                "url": "https://open-meteo.com/en/docs/previous-runs-api",
                "dataset": "24h/48h lead-time forecast performance compared with ERA5 reanalysis",
                "updateFrequency": "Rolling seven-day verification; cached locally for 24 hours",
                "limitations": "Model-to-reanalysis comparison, not station-verified accuracy or disaster-prediction validation.",
            },
            {
                "name": "India Meteorological Department (IMD)",
                "url": "https://mausam.imd.gov.in/",
                "dataset": "Planned: heat/rainfall observations",
                "updateFrequency": "TBD",
                "limitations": "Not yet integrated in MVP.",
            },
            {
                "name": "Central Water Commission (CWC)",
                "url": "https://cwc.gov.in/",
                "dataset": "Planned: river levels",
                "updateFrequency": "TBD",
                "limitations": "Not yet integrated in MVP.",
            },
            {
                "name": "Validated satellite lake inventory",
                "url": "",
                "dataset": "Planned: glacial lake extent and monitoring metadata",
                "updateFrequency": "TBD",
                "limitations": "No live lake monitoring feed is integrated; a lake watch status is not a GLOF forecast.",
            },
            {
                "name": "Population and infrastructure exposure datasets",
                "url": "",
                "dataset": "Planned: gridded population and infrastructure exposure layers",
                "updateFrequency": "Periodic, subject to source",
                "limitations": "MVP uses synthetic indices; no population counts or current affected-area estimates are supplied.",
            },
        ],
    }


@router.post("/ai/explain")
async def ai_explain(body: ExplainBody, request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    _, risk_service, _ = _get_services(request)
    try:
        assessment = risk_service.assess(body.locationId)
    except KeyError:
        raise HTTPException(status_code=404, detail="Location not found")
    return await explain_risk(
        assessment,
        question=body.question,
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        fallback_models=settings.openrouter_model_chain[1:],
        base_url=settings.openrouter_base_url,
        agent_enabled=settings.ai_agent_enabled and settings.ai_model_provider == "openrouter",
        risk_service=risk_service,
        rainfall_provider=request.app.state.rainfall_provider,
    )


@router.post("/ai/chat")
async def ai_chat(body: ChatBody, request: Request) -> dict[str, Any]:
    _require_risk_assessments_available()
    _, risk_service, _ = _get_services(request)
    try:
        assessment = risk_service.assess(body.locationId)
    except KeyError:
        raise HTTPException(status_code=404, detail="Location not found")
    return await chat_about_risk(
        assessment,
        body.message,
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        fallback_models=settings.openrouter_model_chain[1:],
        base_url=settings.openrouter_base_url,
        agent_enabled=settings.ai_agent_enabled and settings.ai_model_provider == "openrouter",
        risk_service=risk_service,
        rainfall_provider=request.app.state.rainfall_provider,
    )


@router.post("/ai/evidence")
async def ai_global_evidence(body: GlobalEvidenceQuestionBody, request: Request) -> dict[str, Any]:
    if settings.data_mode != "real":
        raise HTTPException(status_code=503, detail="Global evidence Copilot requires real data mode.")
    weather_provider = request.app.state.rainfall_provider
    flood_service = request.app.state.flood_evidence_service
    historical_service = request.app.state.historical_baseline_service
    ensemble_service = request.app.state.ensemble_evidence_service
    if any(service is None for service in (flood_service, historical_service, ensemble_service)):
        raise HTTPException(status_code=503, detail="One or more real evidence providers are not configured.")

    location = {
        "name": body.name,
        "state": body.admin1,
        "country": body.country,
        "lat": body.latitude,
        "lng": body.longitude,
        "selection_type": body.selection_type,
    }
    tools, tool_results = build_global_evidence_tools(
        location=location,
        weather_provider=request.app.state.weather_evidence_service,
        flood_service=flood_service,
        historical_service=historical_service,
        ensemble_service=ensemble_service,
    )
    tool_by_name = {tool.tool_name: tool for tool in tools}
    required_tools = (
        "get_weather_evidence",
        "get_flood_evidence",
        "get_forecast_uncertainty",
        "get_historical_baseline",
        "get_data_provenance",
    )
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(asyncio.to_thread(tool_by_name[name]) for name in required_tools),
                return_exceptions=True,
            ),
            timeout=GLOBAL_EVIDENCE_TOOLS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Global evidence retrieval timed out after %.1f seconds; returning available evidence and marking pending sources unavailable",
            GLOBAL_EVIDENCE_TOOLS_TIMEOUT_SECONDS,
        )
    else:
        for name, result in zip(required_tools, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Global evidence tool failed: tool=%s error=%s",
                    name,
                    type(result).__name__,
                )

    response_tool_results = dict(tool_results)
    for name in required_tools:
        if name not in response_tool_results:
            response_tool_results[name] = {
                "status": "UNAVAILABLE",
                "reason": "Evidence retrieval did not finish before the Copilot response deadline.",
            }
    intent = "other"
    ai_guidance: dict[str, Any] | None = None
    has_environmental_evidence = any(
        isinstance(response_tool_results.get(name), dict)
        and response_tool_results[name].get("status") not in {"UNAVAILABLE", "ERROR", "FAILED"}
        for name in required_tools
    )
    if has_environmental_evidence and settings.ai_agent_enabled and settings.openrouter_api_key:
        try:
            intent, ai_guidance = await asyncio.wait_for(
                run_openrouter_global_evidence(
                    question=body.question,
                    location=location,
                    tool_results=response_tool_results,
                    api_key=settings.openrouter_api_key,
                    model_ids=settings.openrouter_model_chain,
                    base_url=settings.openrouter_base_url,
                ),
                timeout=GLOBAL_EVIDENCE_AI_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Global evidence AI guidance timed out after %.1f seconds; using deterministic evidence fallback",
                GLOBAL_EVIDENCE_AI_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("Global evidence AI guidance unavailable: %s", type(exc).__name__)
            intent = "other"

    return render_global_evidence_answer(
        location=location,
        tool_results=response_tool_results,
        question=body.question,
        intent=intent,
        ai_guidance=ai_guidance,
    )
