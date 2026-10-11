# HeatShield API Contract

Base URL (local): `http://127.0.0.1:8000/api/v1`

All responses JSON. Errors: `{ "detail": string }`.

## Health

### `GET /health`

```json
{ "status": "ok", "service": "heatshield-api", "dataMode": "real", "demoData": false }
```

### `GET /config`

Returns the configured UI data mode (`demo` or `real`). The frontend uses this to avoid loading the demo risk dashboard in real mode.

## Locations

### `GET /locations`

In `DATA_MODE=demo`, returns demo locations with coordinates and primary scenario.

```json
{
  "locations": [ { "id", "name", "state", "lat", "lng", "primaryScenario" } ],
  "dataProvenance": "DEMO"
}
```

In `DATA_MODE=real`, returns HTTP 503 rather than exposing the synthetic demo catalog. Use `GET /weather/search` to find live weather locations.

## Risk

### `GET /risk`

Query all locations’ current assessments (respects active simulation).

```json
{
  "assessments": [ /* RiskAssessment */ ],
  "lastUpdated": "ISO8601",
  "simulationMode": false,
  "dataProvenance": "DEMO"
}
```

### `GET /risk/{location_id}`

Single `RiskAssessment`, including `providerObservations` (one standardized envelope per provider), `dataProvenance`, `componentScores`, and `riskDimensions`. For flood assessments, lake monitoring is separate from flood-risk scoring. Provider values carry a unit and are explicitly marked `synthetic`, `simulated`, `observed`, or `unavailable`.

### `GET /risk/heat-forecast?latitude={lat}&longitude={lon}&name={place}&admin1={region}&country={country}`

Real-mode-only, selected-coordinate forecast heat assessment contract. Returns `INCOMPLETE` or `UNAVAILABLE`, with `riskScore` and `riskLevel` null, forecast time/provenance, and structured missing-input reasons. Validated forecast inputs may come from Open-Meteo or the configured OpenWeather fallback. No numeric risk score is calculated.

### `POST /risk/recalculate`

Optional body: `{ "locationIds": ["sikkim"] }`. Forces refresh from providers + engine.

## Simulation

### `GET /simulation`

```json
{
  "active": false,
  "locationId": null,
  "preset": null
}
```

### `POST /simulation/start`

```json
{
  "locationId": "sikkim",
  "scenario": "flood_glof",
  "preset": "heavy_rain" | "river_rising" | "normal"
}
```

Flood presets are accepted only for flood-risk locations. Active simulations are marked `DEMO_SIMULATION` and include baseline-to-simulation values and deltas for available numeric inputs.

### `POST /simulation/heat`

```json
{
  "locationId": "delhi",
  "preset": "normal" | "extreme_heat"
}
```

Heat presets are accepted only for extreme-heat locations.

### `POST /simulation/parameters`

Updates any subset of `rainfallIntensityMmHr` (0–500), `cumulativeRainfallMm24h` (0–1000), and `riverLevelM` (0–50) for a flood-risk location. At least one value is required. Values persist for the active simulation until another preset is selected or simulation is stopped; each update returns a recalculated assessment and baseline deltas.

### `POST /simulation/stop`

Clears simulation; returns to demo baseline observations.

## Alerts

### `GET /alerts`

Lists alerts for locations at `high` or `critical` (HeatShield assessment, not official).

## AI

### `POST /ai/explain`

Body:

```json
{
  "locationId": "sikkim",
  "question": "Why is this HIGH?"
}
```

Server loads the current structured assessment. When `AI_AGENT_ENABLED=true` and `AI_MODEL_PROVIDER=openrouter`, a Strands Agent uses `OPENROUTER_MODEL` followed by `OPENROUTER_FALLBACK_MODELS` in their configured order to select from the backend-only assessment, environmental-evidence, simulation-result, and data-quality tools. On a model request error, malformed intent, or missing tool call, it tries the next model. The model's prose is discarded; HeatShield renders the answer from structured assessment/tool results. If Strands is unavailable, the key is missing, or every model/tool attempt fails, deterministic local question handling is used. With `AI_AGENT_ENABLED=false`, the retained direct OpenRouter intent-classification path uses the same model order, with deterministic fallback otherwise. The endpoint request and response shape remain unchanged.

### `POST /ai/chat`

Body: `{ "locationId", "message": string }`. Messages must contain non-whitespace text and be at most 2000 characters. The tool workflow can retrieve assessment, environmental provenance, data quality, and the current/requested isolated simulation comparison. With no model key or when Strands fails, the deterministic fallback reports supplied evidence and engine-generated scores. Model-generated prose is not returned as an application fact.

### `POST /ai/evidence`

Body: `{ "latitude", "longitude", "name", "admin1", "country", "selection_type", "question" }`. `selection_type` is optional and defaults to `named_place`; map clicks send `map_point`. Real-mode-only, coordinate-based evidence Copilot. Read-only tools retrieve weather evidence, GloFAS evidence, ERA5 baseline, ensemble spread, provenance, or explicit climate-projection unavailability. Its compact AI evidence package preserves weather provider status, field-level sources, forecast/reanalysis/modeled evidence types, retrieval and valid times, missing scalar fields, and compatible-provider disagreements; unavailable and stale inputs are separately described. The response includes a structured `environmental_assessment` with a location summary and evidence references, findings and concerns with references, separate evidence-based/general/community suggestions, missing information, next steps, limitations, generation time, and model identifier when available. Unknown reference IDs, invalid structure, and unsupported numerical claims are rejected; evidence-based suggestions require cited references. Missing evidence is stated explicitly and known data gaps are merged into the assessment. For `map_point`, the Copilot describes weather at the selected coordinates and provider grid cells; it does not infer a nearby settlement or return local-action suggestions. If AI is unavailable or its map-point interpretation assumes an unverified place or audience, the deterministic evidence response is used. Model prose is not semantically verified for factual entailment; treat structured provider evidence and provenance as canonical. The model is not the source of risk scores or levels. In particular, modeled river discharge with unverified local association cannot establish flood likelihood or safety. Demo mode returns HTTP 503.

## Data sources

### `GET /weather/search?query={place}`

Searches Open-Meteo's worldwide geocoding catalog. Results include a place name, administrative area, country, and coordinates. This endpoint requires `DATA_MODE=real`; provider failures return HTTP 503.

### `GET /weather/forecast?latitude={lat}&longitude={lon}&name={place}&admin1={region}&country={country}`

Returns Open-Meteo hourly precipitation forecasts for any valid world coordinate, with live provenance and forecast/retrieval timestamps. This endpoint requires `DATA_MODE=real`; invalid coordinates return HTTP 422 and provider failures or unavailable values return HTTP 503. Map selections do not need to exist in the bundled demo location catalog.

### `GET /weather/evidence?latitude={lat}&longitude={lon}&name={place}&admin1={region}&country={country}&refresh={true|false}`

Returns a structured seven-day `FORECAST` envelope with current forecast measurements and hourly/daily series. Open-Meteo is always requested. If `OPENWEATHER_API_KEY` is set on the backend, OpenWeather is requested concurrently with Open-Meteo, at most once per cache refresh. OpenWeather can fill compatible missing primary fields and provide a second forecast for comparison; Open-Meteo remains the primary displayed forecast. An unconfigured secondary provider is not called and does not prevent normal Open-Meteo use. OpenWeather is implemented for the 5 Day / 3 Hour Forecast API only; it does not provide station observations or official warnings, and its response does not identify an underlying numerical model.

Open-Meteo supports the requested variables where returned: 2 m temperature, apparent temperature, relative humidity, precipitation, rain, showers, precipitation probability, wind speed/gust/direction, cloud cover, visibility, WMO weather code, wet-bulb temperature, UV, shortwave radiation, reference evapotranspiration, and top-layer soil moisture. The OpenWeather adapter supports selected temperature/apparent-temperature, humidity, wind, cloud-cover, and visibility fields for compatible scalar fallback. Secondary scalar values fill only missing supported primary fields with matching valid time, unit, and aggregation period. Compatible instantaneous hourly values may be merged only at matching valid timestamps and units; the OpenWeather hourly series uses UTC timestamps sampled at three-hour intervals. OpenWeather's rain amount is mm/3h and its precipitation probability applies to a three-hour period; neither is treated as an Open-Meteo hourly amount or probability, and no unlike periods are merged or averaged. When OpenWeather is the only usable forecast, its standalone evidence may include UTC daily temperature extrema sampled at three-hour intervals and rain-only totals calculated only when all eight consecutive preceding three-hour rain periods are available. It does not infer total precipitation (including snow) or daily precipitation probability, and these daily values are not merged into a primary Open-Meteo daily series.

Hourly Open-Meteo `precipitation` is an amount in mm accumulated during that forecast hour (not mm/h); its probability is for that hour. Daily `precipitation_sum` is total precipitation and `rain_sum` is rain-only, each for a location-timezone calendar day; these are distinct variables. Missing values remain unavailable, not zero. Per-variable values may be `null` with `UNAVAILABLE` status. The response's `provider_status` reports each configured provider's status, available fields, fields used to fill missing primary values, retrieval time, and safe limitations; `missing_fields` lists unavailable scalar forecast fields. Hourly/daily `provenance` preserves provider, source URL, requested/returned coordinates, retrieval time, units, and aggregation period for series containing values; secondary series provenance records contributed valid times. `provider_disagreements` reports only same-variable scalar forecasts with matching valid times, units, and aggregation periods whose absolute difference meets the display threshold: temperature 2 °C, apparent temperature 2 °C, humidity 10 percentage points, precipitation probability 20 percentage points, or wind speed 10 km/h. These are display thresholds, not risk thresholds. Values are not averaged, and disagreement does not establish which forecast is more accurate or that sources are independent.

Response provenance includes requested and grid coordinates, elevation, timezone where supplied, retrieval time, API/model limitations, and field-level source details. Open-Meteo auto-selects the highest-resolution applicable model but does not return its exact model ID, forecast initialization time, or exact resolution. OpenWeather reports forecast valid times in UTC and provides an offset but not an IANA timezone. The endpoint also attaches historical anomaly status independently; unavailable history does not fail available weather evidence. Optional `refresh=true` bypasses the weather and ERA5 baseline caches for a user-requested refresh. Provider failures remain separate: if both weather providers fail, the service returns a cached forecast as `DEGRADED` when available, retaining its original retrieval time and adding a stale-data limitation; without cached evidence it reports unavailable data and never substitutes demo evidence. The weather cache is coordinate-keyed and lasts ten minutes, refreshing when its selected forecast time has passed. `OPENWEATHER_API_KEY` is backend-only; an empty value leaves the secondary provider disabled. Configure only credentials with access to this endpoint and comply with the provider's current quota, attribution, and terms. WeatherAPI.com and official national meteorological services are not implemented or queried.

The attached ERA5 anomaly report compares each daily forecast statistic and variable with its corresponding ERA5 1991–2020 daily values from the same calendar month, using the forecast location's calendar timezone. Temperature uses daily maximum; precipitation uses `precipitation_sum`; rainfall uses `rain_sum`. It reports the historical mean, difference, and empirical percentile. These are historical comparisons, not disaster probabilities or validated hazard scores. No daily comparison is made when the forecast timezone is unavailable. OpenWeather's daily maximum uses three-hour temperature samples rather than full hourly coverage, so its ERA5 daily maximum comparison is marked unavailable instead of implying equivalent aggregation.

### `GET /flood/evidence?latitude={lat}&longitude={lon}&refresh={true|false}`

Returns daily GloFAS `MODEL_EVIDENCE`: discharge, ensemble mean/median/min/max/P25/P75 where available, data type, requested/grid coordinates, period/horizon, units, retrieval time, and limitations. Only dates through July 2022 are classified as GloFAS v4 reanalysis; current/future dates are forecast; dates between that cutoff and the forecast are explicitly unavailable rather than mislabeled. The API documents a default GloFAS v4 model and approximately 5 km resolution; the response does not itself return model version or a river name. The nearest/largest modeled river association is **unverified**. UI wording must say “Modeled river-discharge evidence,” not actual local river level. Discharge is not converted to an official warning or risk score. Missing values remain null; no usable values or upstream failure returns HTTP 503. Real mode only.

`refresh=true` bypasses the in-memory provider cache and requests fresh evidence; provider failures return HTTP 503.

### `GET /weather/ensemble?latitude={lat}&longitude={lon}&refresh={true|false}`

Opt-in 72-hour ECMWF IFS 0.25-degree ensemble endpoint. Returns daily temperature-max and precipitation-sum median, P25/P75, min/max, mean, IQR, member count, and deterministic display-only spread category. Statistics and categories use unrounded member values; returned statistics are rounded for display. When a nonzero IQR rounds to zero, its reason identifies the precision loss, and equal displayed quartiles are labeled as no spread at displayed precision. Temperature categories use IQR bands (LOW <=2°C, MODERATE >2°C to 5°C, HIGH >5°C). Precipitation categories use IQR/median bands (LOW <=0.25, MODERATE >0.25 to 0.75, HIGH >0.75; zero median with nonzero spread is HIGH). These describe model-member disagreement only, not hazard thresholds, forecast accuracy, disaster probability, impact, or safety; zero or low spread does not imply zero risk. Missing spread is `UNAVAILABLE`. Real mode only.

### `GET /weather/validation?latitude={lat}&longitude={lon}&refresh={true|false}`

Returns **Historical forecast performance**: temperature and precipitation MAE for the 24h and 48h Previous Runs lead-time fields against ERA5 hourly reanalysis over a rolling seven-day verification period, with sample counts and limitations. This is a model-to-reanalysis comparison, not station-verified accuracy or disaster-prediction validation. Missing pairs are excluded and reported through sample counts; unavailable variables remain null. Results are cached in memory for 24 hours. Real mode only.

Both endpoints accept `refresh=true` to bypass their in-memory cache.

### `GET /data/rainfall/{location_id}`

Returns the configured rainfall provider envelope and normalized provenance (`source`, `source_url`, `location_id`, `latitude`, `longitude`, `data_type`, `forecast_time` or `observation_time`, `retrieval_time`, `value`, `unit`, `quality`, and `status`). Existing envelope and named `values` fields are retained for compatibility; normalized records are available in `measurements` and each value's `provenance`. `DATA_MODE=demo` (default) returns synthetic values marked `DEMO`; `DATA_MODE=real` queries Open-Meteo and returns hourly precipitation forecasts marked `FORECAST`. The legacy `rainfallIntensityMmHr` field represents the amount for the next available one-hour interval as a period-average rate in mm/h, not an instantaneous rate. `cumulativeRainfallMm24h` sums 24 complete consecutive hourly amounts starting at that forecast hour; it is a rolling 24-hour window, not a location-timezone calendar-day total. Incomplete windows remain unavailable. Forecast validity time is distinct from retrieval time. A source failure or response with no usable data returns HTTP 503; unknown locations return 404. Missing measurements remain `null`. Forecasts are evidence only and are not passed to the risk engine.

### `GET /data-sources`

Catalog of active Open-Meteo Forecast, Geocoding, Historical Weather/ERA5, Previous Runs, Ensemble, and Global Flood/GloFAS providers plus remaining planned IMD, CWC gauge, validated satellite lake, and real exposure integrations. Open-Meteo data require CC BY 4.0 attribution and its free API is for non-commercial use with a 10,000-call/day limit.

## Data Mode and Risk Scoring

`GET /config` returns `demo` or `real`. Demo mode retains the bundled synthetic locations and risk inputs. Real mode displays provider evidence only; no real weather, ERA5, GloFAS, or ensemble value currently affects the deterministic risk score. Existing risk thresholds are prototype demo configuration, not newly validated global scientific thresholds. Simulations and alerts remain tied to the demo assessment APIs. No provider failure silently falls back to synthetic data.
