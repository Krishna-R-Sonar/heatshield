# HeatShield Architecture Overview

## Purpose

HeatShield is a climate-evidence and decision-support prototype with two isolated modes: an India-focused demo risk dashboard and a worldwide real-mode evidence dashboard. The deterministic risk engine scores only its configured demo-backed inputs. The real dashboard presents weather forecasts, reanalysis comparisons, ensemble spread, and modeled GloFAS discharge as evidence; none is automatically fed into a risk score.

## High-Level Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│ Data        │────▶│ Risk Engine  │────▶│ FastAPI Backend │────▶│ React UI     │
│ Providers   │     │ (Python)     │     │ (REST)          │     │ Map + Panel  │
│ (Demo/Real) │     │ deterministic│     │                 │     │              │
└─────────────┘     └──────────────┘     └────────┬────────┘     └──────────────┘
                                                  │
                                                  ▼
                                         ┌─────────────────┐
                                         │ OpenRouter API  │
                                         │ (async, bounded)│
                                         │ evidence text   │
                                         └─────────────────┘
```

## Layer Responsibilities

| Layer | Responsibility |
|-------|----------------|
| **Frontend** | Global selectable map, live evidence sections/charts, provenance, and evidence Copilot in real mode; India demo map, risk panel, alerts, and simulations in demo mode |
| **Backend** | HTTP API, orchestration, simulation overrides, AI proxy (keys server-side) |
| **Risk engine** | Configurable rules, scoring, contributing factors, uncertainty |
| **Data providers** | Demo providers remain isolated; real-mode weather uses Open-Meteo as primary and optional OpenWeather as one bounded secondary forecast source. Historical Weather, Previous Runs, Ensemble, Geocoding, and Global Flood (GloFAS) remain separate Open-Meteo services. |
| **AI** | The demo Copilot uses Strands tool selection over read-only assessment tools. Real-mode evidence is retrieved by read-only HeatShield tools, then interpreted directly by OpenRouter with a cancellable timeout; timeout or invalid output uses the deterministic fallback. |

## Scientific & Product Constraints

- Floods have **multiple causes** (rain, rivers, drainage, landslides, GLOFs). The UI and copy must not attribute all floods to climate change or glacier melt alone.
- **Missing glacier data** must not trigger a GLOF warning.
- Simulated inputs are clearly marked **SIMULATION MODE**.
- HeatShield assessments are **not** official government emergency warnings.

## Real-mode Evidence Sources

| Evidence | Source/type | Resolution and period | Current use and limitations |
|----------|-------------|-----------------------|-----------------------------|
| Weather outlook | Open-Meteo Forecast API; `FORECAST` | Model auto-selection; resolution and exact model ID are not returned; hourly and daily horizon | Display only. Grid may differ from requested coordinates; not a station observation. |
| Optional secondary weather source | OpenWeather 5 Day / 3 Hour Forecast API; `FORECAST` | Forecast steps every 3 hours; UTC times; provider-returned coordinate is checked against the selected location | Queried concurrently with Open-Meteo once per Real-mode cache refresh when `OPENWEATHER_API_KEY` is configured. Open-Meteo remains primary; OpenWeather can fill compatible missing fields and expose comparable forecast disagreements. No station observations or official warnings; incompatible periods are not merged; values are not averaged. |
| River discharge | Open-Meteo Global Flood API / GloFAS; forecast or documented reanalysis period | Approx. 5 km grid; returned cell coordinates included; daily discharge and forecast ensemble statistics | Display only. River association is unverified; may be a nearby/largest modeled river, not actual local river level. No warning conversion. |
| Historical baseline | Open-Meteo Historical Weather API / ERA5; `REANALYSIS` | 0.25 degrees (~25 km); fixed 1991-2020 calendar-month comparison | Daily max temperature, precipitation, and rain anomaly/percentile; reanalysis estimate, not station observation. Not mixed with weather forecast values. |
| Weather ensemble | Open-Meteo Ensemble API / ECMWF IFS 0.25 degrees; `ENSEMBLE_FORECAST` | Approx. 25 km; 72-hour request, member-level summaries | Median, P25/P75, min/max, IQR and display-only uncertainty category. Spread is not disaster probability. |
| Historical forecast performance | Previous Runs API vs ERA5 archive; forecast compared with `REANALYSIS` | 24h/48h lead times; rolling 7-day verification window | Reports MAE and sample counts. This is not station-verified accuracy or disaster-prediction validation. |

### Real-mode weather fallback

`WeatherEvidenceService` requests Open-Meteo and, when configured, OpenWeather concurrently, at most once each per cache refresh. Open-Meteo remains primary; OpenWeather can fill compatible missing fields and report comparable forecast disagreements. Provider failures are independent and do not invalidate the other provider's usable result. Provider status distinguishes fields returned by OpenWeather from fields actually used to fill gaps. If both providers fail but a prior forecast is cached, that forecast is returned as degraded with its original retrieval time and an explicit stale-evidence limitation. Otherwise missing fields remain unavailable; Real Mode never substitutes demo values.

The OpenWeather adapter is forecast-only, checks the provider-returned coordinate against the selected point, and retains UTC valid times because its response does not provide an IANA timezone. It supports selected temperature, humidity, precipitation-period, and wind-related fields; the provider's precipitation probability and rain amount refer to a three-hour period. Secondary scalar fields fill only missing primary fields. Compatible instantaneous hourly series may be filled at matching UTC valid times and matching units; three-hour precipitation accumulations are not treated as hourly totals. Values are never averaged. Every returned field retains source and time metadata, and comparable values from the two forecasts are exposed as disagreements when they exceed documented display-only thresholds. A disagreement does not identify the more accurate source or establish independent confirmation.

Weather-field status lists the fields returned by each provider and, separately, fields actually used from the secondary provider; the response also records missing scalar fields, retrieval times, and material disagreements. Time-series provenance is attached only when a series contains values, with the secondary provider recording the valid times it contributed. The live evidence path does not integrate official warning feeds, verified local gauges, glacial-lake observations, municipal waterlogging records, or a long-window drought assessment. WeatherAPI.com and official national meteorological services are future adapter opportunities only.

## Risk Input Register

| Variable | Source/units/resolution | Rationale and current rule | Enabled for score? | Limitations |
|----------|------------------------|----------------------------|--------------------|-------------|
| Demo rainfall intensity and 24h total | Bundled synthetic dataset; mm/h and mm/24h | Prototype flood inputs; thresholds in `risk-engine/heatshield_risk/thresholds.yaml` | Demo mode only | Thresholds are prototype configuration, not a globally validated scientific standard. |
| Demo temperature, humidity, heat index/duration | Bundled synthetic dataset; °C, %, hours | Prototype heat inputs; thresholds in `thresholds.yaml` | Demo mode only | Prototype thresholds; not an official heat-health warning rule. |
| Live weather forecast variables | Open-Meteo; variable units in provider response; model-dependent grid | Potential future heat/water context | No | Forecast, auto model ID/resolution limitations; forecast values do not enter scoring. |
| GloFAS discharge/quantiles | Open-Meteo Global Flood API; m³/s; approx. 5 km | Potential river context; no local threshold defined | No | Unverified river association; not a river gauge or official flood threshold. |
| ERA5 anomalies and ensemble spread | Open-Meteo; °C/mm; approx. 25 km | Contextual comparison and forecast spread | No | Reanalysis/model spread, not measured observations or impact probabilities. |
| Population, infrastructure, river threshold, lake status/history | Demo JSON / demo providers | Current demo engine inputs | Demo mode only | Synthetic/demo-only; no global real exposure or river/lake monitoring feed is enabled. |

No new real-data threshold has been introduced. The future real-scoring gate remains disabled until sources, units, spatial/temporal resolution, limitations, and scientifically justified rules are validated.

## Local Development Stack

- **Frontend:** React, TypeScript, Vite, Leaflet
- **Backend:** Python 3.11+, FastAPI, Uvicorn
- **Risk engine:** Standalone Python package (`heatshield_risk`)
- **Persistence (local):** JSON files + in-memory simulation state; repository pattern for later DynamoDB
- **AI:** OpenRouter (server-side `OPENROUTER_API_KEY`)
- **Optional secondary weather forecast:** OpenWeather (`OPENWEATHER_API_KEY`, backend only); no credential is required for Open-Meteo.

## Local Copilot (Strands Agents SDK)

The backend uses the open-source `strands-agents` Python SDK to let the demo Copilot select from four application tools: `get_risk_assessment`, `get_environmental_evidence`, `get_simulation_result`, and `get_data_quality`. The real evidence Copilot has six read-only tools: `get_weather_evidence`, `get_flood_evidence`, `get_historical_baseline`, `get_forecast_uncertainty`, `get_data_provenance`, and `get_climate_projection`. Climate projection returns explicit unavailable status until that separate module is implemented. Global tools accept the selected coordinates and do not call the risk engine.

`AI_AGENT_ENABLED=true` and `AI_MODEL_PROVIDER=openrouter` enable optional model interpretation with the server-side OpenRouter settings. The demo Copilot uses Strands Agents SDK to select among read-only assessment tools. For the real global-evidence Copilot, HeatShield retrieves the evidence first and sends a compact structured snapshot directly to up to three models from the configured OpenRouter model chain, in order. Each request has a one-second timeout within a four-second overall model deadline; account-wide free-model quota errors stop retries immediately. On failure, HeatShield uses the deterministic evidence fallback. Evidence retrieval is capped at eight seconds, and the browser request at 20 seconds. The request does not start a Strands worker. Coordinate-only map pins receive weather evidence and limitations without local-action suggestions. The response is schema-checked, numeric claims are constrained to cited evidence, and findings plus evidence-based suggestions must cite references from the supplied snapshot; general suggestions are labeled as not locally established. Free-text claims are not semantically verified for entailment, so provider facts and provenance remain canonical. Missing inputs and fixed assessment limitations are included even when AI interpretation succeeds. The deterministic risk engine remains solely responsible for scores and levels. If the key is absent, the model response is invalid, or the provider fails or times out, HeatShield retains its labeled deterministic fallback.

The model must not independently score heat or flood risk. In particular, modeled GloFAS discharge with an unverified river association cannot establish local flood likelihood or flood safety; high humidity alone cannot establish heat stress. The map summary remains a factual, non-AI forecast overview; the Copilot interpretation is a separate model or deterministic response.

Strands Agents is open source under Apache-2.0. This integration runs in the local Python process; it does not use AWS credentials, deploy to AWS, or create AWS resources. No claim is made here about eligibility under any hackathon rules.

## Recommended AWS Architecture (Not Deployed)

```
Users → CloudFront + private S3 (static frontend)
       → API Gateway HTTP API → Lambda (FastAPI + Mangum)

EventBridge Scheduler → Step Functions → provider adapters
                         → SQS + dead-letter queue for retries
                         → S3 raw source archive
                         → normalized observations in DynamoDB

Normalized observations → deterministic risk package → DynamoDB assessments
API → structured assessment → Bedrock or secured OpenRouter proxy for explanation
CloudWatch logs/metrics/alarms; SNS only for explicitly configured notifications
```

Keep the risk engine as a versioned shared Python package and persist its version and input provenance with each assessment. Separate current snapshots from time-indexed observations; retain raw provider payloads in S3 for audit and replay. Validate source freshness, units, location mapping, and quality before updating current data. Use a queue and dead-letter queue so a failing upstream feed cannot block other sources.

For short API requests and modest hackathon traffic, API Gateway plus Lambda is the simplest first deployment. Prefer ECS Fargate behind an Application Load Balancer if provider calls, sustained traffic, or execution duration no longer fit Lambda well. Keep model credentials in Secrets Manager, use least-privilege IAM and KMS encryption, and monitor provider freshness as well as API health. Any official alerting requires a separately approved source, policy, and delivery workflow; HeatShield assessments alone are not government warnings.

This is a target design only. AWS resources have not been created and no deployment has been performed.

## Monorepo Layout

See [milestones.md](./milestones.md) and repository root `README.md`.

## Extension Points

- `WeatherDataProvider`, `RainfallDataProvider`, `RiverDataProvider`, `GlacierDataProvider`, `PopulationExposureProvider`
- `RiskEngineConfig` (YAML thresholds)
- `LLMProvider` (OpenRouter default; swappable)
- `RiskAssessmentRepository` (JSON local → DynamoDB)
