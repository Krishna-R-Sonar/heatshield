# Risk Engine Design

Package: `heatshield_risk` (under `risk-engine/`)

## Principles

1. **Deterministic** — same inputs + config → same output.
2. **Transparent** — component scores exposed in `componentScores`.
3. **Configurable** — thresholds in `heatshield_risk/thresholds.yaml`.
4. **Conservative with missing data** — unavailable inputs are explicit, do not receive substitute exposure values, and lower confidence; never infer GLOF.

## Flood Risk and Lake Monitoring

Component scores (each 0–25, weighted sum → 0–100):

| Component | Input | Rule (summary) |
|-----------|--------|----------------|
| `rainfall_score` | intensity, 24h cumulative | Tiered thresholds |
| `river_score` | level vs threshold ratio | Ratio bands |
| `exposure_score` | population + infrastructure indices | Weighted blend |
| `historical_score` | flood event count (optional) | Small capped contribution |

```
overall = min(100, rainfall + river + exposure + historical)
```

Glacial lake monitoring status is returned separately from flood-risk scoring. A lake's existence or a `WATCH` status is not evidence that a GLOF is imminent. The assessment keeps rainfall risk, river risk, downstream exposure, lake-monitoring status, and overall flood risk distinct.

**Risk levels** (configurable):

- 0–24 → low  
- 25–49 → moderate  
- 50–74 → high  
- 75–100 → critical  

**Uncertainty:** Reduced confidence when key fields are null (e.g. no river data).

## Extreme Heat Scenario

Components (0–25 each):

| Component | Input |
|-----------|--------|
| `temperature_score` | °C vs thresholds |
| `heat_index_score` | computed or provided heat index |
| `duration_score` | hours above threshold |
| `exposure_score` | population/infrastructure indices |

Heat index (when not provided): simplified Rothfusz-style approximation for demo only, labeled calculated.

## Output Contract

Every run returns `RiskAssessmentResult` matching `docs/data-model.md`.

Every non-zero additive component is exposed with its score contribution. Component values are calculated by the configured risk engine; the overall score is capped at 100. Lake-monitoring status is not an additive component.

Each provider returns `source`, `timestamp`, `location`, `status`, `dataQuality`, `dataProvenance`, and named `values` with `value`, `unit`, and per-value status (`synthetic`, `simulated`, `observed`, or `unavailable`). Demo providers never label synthetic values as observations.

## Simulation

Backend applies preset overrides before calling the engine. Provider values are marked `simulated`, the assessment is marked `DEMO_SIMULATION`, and a structured baseline-to-simulation comparison is returned. Simulation values never overwrite the demo baseline.

## Tests (required)

- NORMAL → low  
- HEAVY RAIN + HIGH RIVER → high  
- EXTREME HEAT + HIGH HUMIDITY → high  
- Missing glacier → no GLOF factor claiming lake breach  

See `risk-engine/tests/`.
