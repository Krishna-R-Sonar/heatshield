# HEATSHIELD

**Climate Risk Intelligence & Global Weather**

HeatShield includes an India-focused deterministic demo risk dashboard and a worldwide climate-evidence dashboard. `DATA_MODE=demo` retains the demo locations, synthetic environmental inputs, alerts, and isolated simulations. `DATA_MODE=real` opens a global map where users can search for places or select coordinates and inspect weather forecasts, GloFAS modeled discharge, ERA5 historical anomalies, ECMWF IFS ensemble spread, and historical forecast performance. Open-Meteo is the primary weather provider; when the backend-only `OPENWEATHER_API_KEY` is configured, the OpenWeather forecast adapter is requested concurrently for compatible missing fields and comparison. Open-Meteo and OpenWeather forecasts are not averaged.

Real evidence is not automatically scored. Weather forecasts, GloFAS modeled river discharge, ERA5 reanalysis, and ensemble spread have separate data types and provenance; provider failures remain visible and are never replaced with synthetic values. A failed refresh may retain clearly identified older evidence with its original retrieval time. Real mode presents a score-free, incomplete forecast heat assessment; no real evidence currently changes the deterministic risk score. Waterlogging, long-term drought, real-time glacial-lake monitoring, verified local flood gauges, and official heat/flood advisories are not integrated in the live evidence path; evidence completeness labels identify those limitations and are not hazard levels. The evidence dashboard's **Refresh evidence** action bypasses the local evidence caches. Open-Meteo data require CC BY 4.0 attribution and its free API is limited to non-commercial use (10,000 calls/day): [Weather data by Open-Meteo.com](https://open-meteo.com/).

## Monorepo layout

| Path | Purpose |
|------|---------|
| `frontend/` | React + TypeScript + Leaflet dashboard |
| `backend/` | FastAPI orchestration |
| `risk-engine/` | Configurable deterministic risk rules |
| `ai/` | Strands tool workflow, OpenRouter adapter, and deterministic response fallback |
| `data/demo/` | Demo locations and environmental baselines |
| `docs/` | Architecture, API contract, milestones |
| `infrastructure/` | Proposed AWS deployment documentation; nothing is deployed |

## Quick start (local)

### 1. Backend

```powershell
cd c:\Users\krish\OneDrive\Desktop\heatshield
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
pip install -e risk-engine
copy .env.example .env
# Optional: set OPENROUTER_API_KEY in .env to enable OpenRouter interpretations.
# OPENROUTER_MODEL sets the real-evidence Copilot's primary model.
# OPENROUTER_FALLBACK_MODELS sets ordered retries for the demo-risk Copilot.
# AI_AGENT_ENABLED=true and AI_MODEL_PROVIDER=openrouter are the local defaults.
# Keep DATA_MODE=demo for the India demo risk dashboard. Set DATA_MODE=real for the
# worldwide evidence dashboard. Open-Meteo APIs require no weather API key.
# Optional: set OPENWEATHER_API_KEY in the backend .env to query OpenWeather once
# per Real-mode evidence refresh for compatible missing fields and forecast comparison.
# Open-Meteo is keyless and remains the primary displayed forecast.

cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### Run locally with Finch containers

Finch runs the existing frontend and FastAPI backend as local containers. This is a
local development/demo setup, not an AWS deployment or production configuration.

On Windows, install Finch from its [official releases](https://github.com/runfinch/finch/releases)
and enable WSL 2 first. Initialize the Finch VM once:

```powershell
finch vm init
```

From the project root, build and start both services:

```powershell
finch compose up --build
```

Open http://localhost:5173. The backend health endpoint is
http://localhost:8000/api/v1/health. Demo mode is the default unless `DATA_MODE` is
set in the shell or the root `.env` file. To explicitly run the Real-mode evidence
dashboard in PowerShell:

```powershell
$env:DATA_MODE = "real"
finch compose up --build
```

Set `$env:DATA_MODE = "demo"` to run the synthetic Demo-mode dashboard instead.
Compose passes `OPENROUTER_API_KEY` to the backend only when supplied by the shell or
root `.env`; without a key, the Copilot uses its deterministic fallback. Keep `.env`
out of source control. To stop the services, press Ctrl+C and run
`finch compose down` from the project root. The `docker compose` command can be used
with the same `compose.yaml` when Docker Compose is installed.

### 3. Tests

```powershell
cd c:\Users\krish\OneDrive\Desktop\heatshield
pip install -e "risk-engine[dev]"
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run lint
npm run build
```

## API

Base: `http://127.0.0.1:8000/api/v1` — see [docs/api-contract.md](docs/api-contract.md)

## Important disclaimers

- Bundled demo data is synthetic and **not** live environmental measurement.
- In `DATA_MODE=real`, the dashboard searches worldwide places and can select any map coordinate. **Refresh evidence** re-requests weather, historical baseline, GloFAS, ensemble, and validation evidence without using the server-side cache. The UI shows source and retrieval/forecast times, requested and grid coordinates, and freshness based on retrieval age; after a refresh failure, a prior response remains visible with an explicit failure notice. It shows temperature, apparent temperature, humidity, wet-bulb temperature, precipitation, wind, visibility, UV, radiation, evapotranspiration, soil moisture, hourly/seven-day charts, ERA5 monthly anomalies, GloFAS discharge evidence, and ECMWF IFS ensemble spread where the providers return values. The at-a-glance rainfall snapshot uses the selected response's dated daily totals and, when available, rain-only totals; it reports incomplete coverage without treating missing days as zero, and daily amounts do not establish rainfall timing or local flood safety. This snapshot remains available independently of Copilot responses. Open-Meteo remains the primary forecast. When `OPENWEATHER_API_KEY` is configured, one OpenWeather request is made per Real-mode evidence refresh to fill compatible missing fields and compare available forecasts; without the key, Open-Meteo works normally and no secondary request is made. Returned and used fields retain provider-level provenance, and incompatible precipitation periods are not merged or averaged. Enable OpenWeather only with credentials that have access to its forecast endpoint and in accordance with its current quota, attribution, and terms. WeatherAPI.com and national meteorological-service adapters are not implemented.
- Weather forecast values are model output, not ground-based observations. GloFAS discharge is modeled evidence and may represent an unverified nearby river; it is not a local river level or warning. ERA5 is reanalysis, not a station measurement. Ensemble spread describes model-member disagreement, not disaster probability. Historical forecast performance is compared with ERA5 reanalysis, not station-verified accuracy.
- No complete numeric Real-mode risk score is currently enabled. For a catalog heat location, `GET /api/v1/risk/{location_id}` and for a selected map point, `GET /api/v1/risk/heat-forecast` validate the next available Open-Meteo temperature and humidity forecast and return an explicitly `INCOMPLETE` assessment with no score; missing heat index, elevated-temperature duration, and real exposure inputs are reported as blockers. The dashboard displays this score-free assessment. It does not use demo inputs or the demo heat-index approximation. Real-mode flood scoring, risk alerts, and simulations remain unavailable. The Real-mode map Copilot provides evidence-scoped decision-support suggestions, but these are not risk alerts or substitute scores. Real-mode source failures do not fall back to demo values. `OPEN_METEO_TIMEOUT_SECONDS` controls provider timeouts; exact-coordinate weather is cached for 10 minutes but refreshed if its selected forecast hour has passed; GloFAS/ensemble evidence are cached for 15 minutes, and ERA5 baselines/validation for 24 hours unless refreshed manually.
- The demo Copilot uses Strands Agents SDK (`strands-agents`) to select from four read-only assessment tools. The real evidence Copilot retrieves weather, flood, historical baseline, ensemble uncertainty, data quality, and provenance through read-only evidence tools, then sends a compact structured snapshot directly to OpenRouter. Its structured response separates evidence-based suggestions, general preventive ideas, and community opportunities, with findings and local suggestions linked to supplied evidence references. Unknown references, unsupported numerical claims, and malformed output are rejected; evidence-based suggestions require citations, while general ideas are labeled as not locally established. The deterministic risk engine alone calculates risk scores and levels; the AI does not create or override them. Provider measurements and provenance are rendered from structured source data, not model prose.
- Real-mode Copilot automatically presents an environmental assessment when evidence is available; users can ask follow-up questions. Evidence retrieval and optional model interpretation have independent server-side deadlines: unfinished sources are marked unavailable, while model errors or timeouts return a clearly labeled deterministic evidence summary. Real-mode interpretation tries up to three configured OpenRouter models in order, with a one-second limit per model and a four-second total deadline. Account-wide free-model quota errors stop retries immediately. It does not start a Strands worker or wait through slow model retries. The response is structurally checked and its factual findings/suggestions cite supplied evidence, but free-text claims are not semantically verified for entailment; provider evidence remains canonical. Known missing inputs and assessment limitations are shown alongside AI output. Guidance does not establish a warning, neighborhood hotspot, personal health risk, or confirmed local river condition; GloFAS discharge cannot establish local flood likelihood when river association is unverified.
- Selected map points receive a coordinate-level weather summary without assumptions about nearby populations. Provider-specific values, methods, and limitations are available under the detailed-evidence disclosure. The application uses multiple Open-Meteo services: Forecast, Historical Weather/ERA5, Global Flood/GloFAS, Ensemble, and Previous Runs. These are different evidence feeds from one provider ecosystem, not independent provider cross-validation.
- The demo Strands workflow uses the configured OpenRouter URL, primary model, ordered fallback list, and server-side API key. The real-evidence Copilot tries up to three models from that same ordered chain, then uses the deterministic evidence fallback if the requests fail, time out, or return invalid content. The default chain includes the supplied text-capable free models; media-generation and content-safety-only entries are excluded because they cannot provide the Copilot's text interpretation. No AWS credentials or cloud deployment are needed. Set `AI_AGENT_ENABLED=false` to disable model interpretation. Set `AI_MODEL_PROVIDER=openrouter`; this is currently the supported provider.
- Local setup installs the Strands SDK and its OpenAI-compatible client from `backend\requirements.txt`. Strands uses an OpenAI-compatible model adapter against OpenRouter; no optional Bedrock extra or Strands tools package is required.
- Glacial lake monitoring status is not evidence of an imminent GLOF.
- Simulation mode is labeled **SIMULATION MODE** in the UI.
- HeatShield does **not** issue official government emergency warnings.
- HeatShield uses the open-source Strands Agents SDK in the local application; it is not hosted on or deployed to AWS. No AWS resources are required or created by this integration.

## Hackathon track

Primary: **Heat and Water**. HeatShield keeps the worldwide map at the center: residents can select or search for a place, see a readable short-range weather outlook, and get practical preparedness suggestions; planners can see where local verification is needed and consider general heat/stormwater measures. The application combines forecasts, historical reanalysis, ensemble spread, and modeled river discharge, while explicitly showing that these feeds do not establish neighborhood exposure or a validated local heat/flood risk score. It is decision support, not an official warning system.
