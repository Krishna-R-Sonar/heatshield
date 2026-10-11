# 3-Minute Hackathon Demo Flow

## Real-Mode Evidence Walkthrough

1. Start the API with `DATA_MODE=real` and open `http://localhost:5173`; the dashboard identifies itself as **LIVE WEATHER** and does not load demo scores or alerts.
2. Search for a place outside India or click any coordinate. Confirm the location metadata and Open-Meteo forecast source, times, and grid coordinates.
3. Review the weather outlook and 24-hour/seven-day charts. Keep every value labeled as a **forecast**, not a station observation.
4. Review heat evidence and the ERA5 same-calendar-month anomaly. Point out the fixed 1991-2020 baseline, reanalysis type, resolution, and sample count.
5. Review **Modeled river-discharge evidence** and its GloFAS grid-cell coordinates. Do not call it an actual local river level or flood warning; the nearest river association is unverified.
6. Review ECMWF IFS median/P25/P75/IQR and its spread category. Explain that ensemble spread is not a probability of disaster.
7. Review **Historical forecast performance**: 24h/48h MAE against ERA5 reanalysis, with sample counts. This is not station-verified or disaster-prediction accuracy.
8. Ask Climate Copilot a question about missing or elevated evidence. In Real Mode, evidence is retrieved first and sent directly to OpenRouter for interpretation; if OpenRouter is unavailable or rate-limited, verify that the deterministic evidence fallback still answers from the retrieved sources.
9. State that these real-mode values do not change existing risk scores. No AWS resources are required or deployed.

## Demo-Mode Risk Walkthrough

1. Open `http://localhost:5173`; the India map, legend, and **DEMO DATA** label are immediately visible.
2. Select Sikkim; show rainfall/river inputs and the separate **Glacial lake: WATCH** monitoring status.
3. Point out **Flood risk: HIGH** and the engine-generated score contributions. Emphasize observed inputs versus calculated risk.
4. Ask Climate Copilot: **Why is this high?** Review its observed, calculated, interpreted, and preparedness sections.
5. Open simulation controls and choose **Heavy rainfall**. The map, score, component contributions, alert, and explanation refresh; **SIMULATION MODE** is visible.
6. Choose **River level rising** and compare the structured baseline-to-simulation deltas.
7. Show the generated HeatShield alert and the decision-support disclaimer; these are not official warnings or instructions.
8. Select Delhi and run **Extreme heat** to demonstrate the other supported scenario.
9. Close with [architecture.md](architecture.md) and the proposed AWS path in [aws-deployment.md](aws-deployment.md). Do not deploy during the local MVP demo.

## Strands demo-risk Copilot proof (45 seconds)

1. Start HeatShield with `AI_AGENT_ENABLED=true`, `AI_MODEL_PROVIDER=openrouter`, and a server-side `OPENROUTER_API_KEY`; no AWS credentials are configured.
2. Select Sikkim and ask, **“Is the rainfall evidence observed or forecast?”**
3. Show the demo-mode `/api/v1/ai/chat` response: when Strands returns an answer, its source is `strands_tools_deterministic_response`, and the explanation identifies the returned evidence as `DEMO` or `FORECAST` with its source/time when available. The Strands agent selects assessment/evidence/data-quality tools; HeatShield renders the answer from those structured results.
4. Start a rainfall simulation and ask, **“What changed after increasing rainfall?”** Show the deterministic baseline-to-simulation score and input deltas; the agent does not calculate or alter the scores.
5. Explain the separation: Strands is used for the demo-risk Copilot, while the real-evidence Copilot calls OpenRouter directly and never waits for Strands. Both run locally; no AWS cloud resource or deployment is involved.
