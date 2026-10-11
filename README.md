# HeatShield

Climate-risk dashboard with an India-focused demo mode and a global weather
evidence mode. The app combines provider forecasts with available historical,
ensemble, and modeled river data. Its Copilot summarizes retrieved evidence and
its limitations.

## Project structure

- `frontend/` — React and TypeScript dashboard
- `backend/` — FastAPI API and provider orchestration
- `risk-engine/` — deterministic demo risk calculations
- `ai/` — Copilot workflows and OpenRouter integration
- `data/demo/` — bundled demo data
- `docs/` — API contract and project documentation
- `compose.yaml` — local Finch services

## Run with Finch

Install [Finch](https://github.com/runfinch/finch/releases). On Windows, enable WSL 2.
Initialize the Finch VM once:

```powershell
finch vm init
```

From the repository root, build and start the backend and frontend:

```powershell
$env:DATA_MODE = "real"
finch compose up --build -d
```

Open http://localhost:5173. The API health endpoint is
http://localhost:8000/api/v1/health. Finch Compose starts both services but does not
wait for backend readiness; allow startup time and reload the page if the API is not
ready yet. `finch compose ps` shows service status.

Stop the HeatShield containers and release ports 5173 and 8000:

```powershell
finch compose down
```

Use `$env:DATA_MODE = "demo"` to run with bundled synthetic data. Open-Meteo requires
no API key. An OpenRouter key in the root `.env` enables AI-generated Copilot
interpretation; without one, the Copilot returns a deterministic evidence summary.
Keep `.env` private.

## Tests

Install Python test dependencies if needed, then run backend tests:

```powershell
pip install -r backend\requirements.txt
pip install -e "risk-engine[dev]"
.\.venv\Scripts\python.exe -m pytest
```

## API

Base URL: `http://localhost:8000/api/v1`. See
[docs/api-contract.md](docs/api-contract.md).

## Scope and limitations

- Demo data is synthetic. Live forecasts and modeled discharge are not verified local
  measurements or official warnings.
- The app does not calculate complete real-world heat or flood risk scores. Local
  exposure, waterlogging, drought, groundwater, tankers, leaks, and official
  advisories are not integrated into the live evidence path.
- Provider outages, rate limits, and incomplete coverage can make evidence
  unavailable. Open-Meteo attribution and usage terms apply:
  [open-meteo.com](https://open-meteo.com/).
- HeatShield is local software; Finch does not deploy it to AWS or provision cloud
  resources.
