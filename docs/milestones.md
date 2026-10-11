# Development Milestones

| Phase | Scope | Status |
|-------|--------|--------|
| **0** | Architecture, data model, API contract, risk design (this docs set) | Done |
| **1** | Monorepo structure, risk-engine package, backend skeleton, demo data | Done |
| **2** | React dashboard + Leaflet India map | Done |
| **3** | Demo environmental datasets wired to providers | Done |
| **4** | Deterministic risk engine + tests | Done |
| **5** | Frontend ↔ backend integration | Done |
| **6** | Simulation mode (UI + API) | Done |
| **7** | OpenRouter AI explain/chat + fallback | Done |
| **8** | Alerts from high/critical assessments | Done |
| **9** | Expanded API, missing-data, simulation, AI-input, and risk-transition tests | Done (local suites pass) |
| **10** | AWS infrastructure (SAM/CDK/Terraform stub) | Planned |
| **11** | Deploy to AWS | Planned |
| **12** | Architecture diagram + 3-min demo script | Planned |

## Demo Script (3 minutes)

1. Open HeatShield — India map, legend, and **DEMO DATA** label.
2. Select **Sikkim** — show DEMO rainfall/river inputs and separate glacial-lake monitoring status.
3. Show the calculated flood risk and engine-generated score contributions.
4. Ask Climate Copilot **Why is this high?**; review sourced observations and calculations.
5. Enter **SIMULATION MODE**, increase rainfall, then river level independently; show risk, map, alert, data provenance, baseline deltas, and refreshed explanation.
6. Show general preparedness information and the not-official-warning disclaimer.
7. Select **Delhi**, demonstrate extreme heat, then show the AWS reference architecture. Do not deploy.

## Definition of Done (MVP)

- End-to-end local run: `backend` + `frontend`  
- No fabricated “live” measurements — **DEMO DATA** labels visible  
- Risk never hardcoded in UI — always from API/engine  
- AI does not invent numbers  
