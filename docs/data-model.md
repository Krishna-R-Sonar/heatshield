# HeatShield Data Model

All bundled values are synthetic and include `"dataProvenance": "DEMO"`; they are not real-world observations. Simulation values use `"DEMO_SIMULATION"`.

## Provider Result

Weather, rainfall, river, glacial lake, and population/exposure providers return the same envelope. Each provider value has its own unit and status.

```typescript
{
  source: string;
  timestamp: string; // provider retrieval time, ISO 8601
  retrievedAt?: string; // explicit retrieval time when provided
  dataType?: "observation" | "forecast" | "demo" | "simulation";
  location: { id: string; name: string | null; state: string | null; lat: number | null; lng: number | null };
  status: "available" | "partial" | "unavailable";
  dataQuality: string;
  dataProvenance: "DEMO" | "DEMO_SIMULATION" | "LIVE";
  values: Record<string, {
    value: number | string | boolean | null;
    unit: string;
    status: "synthetic" | "simulated" | "observed" | "forecast" | "unavailable";
    dataType?: "observation" | "forecast" | "demo" | "simulation";
    validTime?: string | null; // observation or forecast validity time, ISO 8601
  }>;
}
```

The worldwide Open-Meteo evidence adapter reports `hourly.precipitation` in mm as the amount accumulated during the selected forecast hour; it is not a rate in mm/h. The hourly precipitation probability applies to that forecast hour. Daily `precipitation_sum` is the calendar-day total precipitation, and daily `rain_sum` is the calendar-day rain-only total; these are distinct provider variables and may differ. Daily periods follow the location timezone. Missing values remain unavailable rather than being replaced with zero. Forecast values are model output, not ground observations, and are excluded from risk-engine inputs.

Historical comparisons use the matching daily statistic and variable (daily maximum temperature, `precipitation_sum`, or `rain_sum`) for the same location-calendar date and timezone, compared with ERA5 daily values from the same calendar month in 1991–2020. The displayed percentile is a historical empirical comparison, not a disaster probability or validated hazard score.

Ensemble P25/P75, median, and IQR are computed from available model-member values before display rounding. The spread category is a display descriptor, not a hazard threshold. If a nonzero spread rounds to zero at the displayed precision, the evidence identifies that rounding; zero or low spread does not establish zero risk, impact, or safety.

For compatibility with the legacy risk-input envelope, `/data/rainfall/{location_id}` exposes the forecast amount accumulated in the next available one-hour interval as `rainfallIntensityMmHr` in mm/h. This is the period-average rate over that hour (numerically the same as the hourly amount only because the interval is one hour), not an instantaneous rate. Its `cumulativeRainfallMm24h` value sums 24 complete consecutive hourly forecast amounts beginning at that hour; it is a rolling window, not the location-timezone calendar-day `precipitation_sum`. This legacy evidence is not used to calculate a validated Real-mode risk score.

## Normalized Measurement Provenance

`GET /data/rainfall/{location_id}` retains the provider envelope and legacy `values` map, and adds `provenance` plus a normalized `measurements` list. Each measurement also appears under its legacy value's `provenance` key for callers that consume named values.

```typescript
{
  source: string;
  source_url: string;
  location_id: string;
  latitude: number | null;
  longitude: number | null;
  data_type: "OBSERVATION" | "FORECAST" | "DEMO" | "SIMULATION" | "CALCULATED";
  observation_time?: string | null;
  forecast_time?: string | null;
  retrieval_time: string;
  value: number | string | boolean | null;
  unit: string;
  quality: string;
  status: string;
}
```

Open-Meteo data use `data_type: "FORECAST"` and `forecast_time`; demo baselines use `"DEMO"` and simulation overrides use `"SIMULATION"`. Missing values remain `null`. The rainfall evidence is independent of the risk assessment: forecast values do not currently influence the risk score.

## Location

```typescript
{
  id: string;              // e.g. "sikkim"
  name: string;            // e.g. "Sikkim"
  state: string;
  lat: number;
  lng: number;
  primaryScenario: "flood_glof" | "extreme_heat";
  populationExposureIndex: number;  // 0-1 demo index, not census count
  infrastructureExposureIndex: number; // 0-1
}
```

## Environmental Snapshot (observations input to engine)

### Flood-risk inputs and separate lake monitoring

```typescript
{
  locationId: string;
  rainfallIntensityMmHr: number | null;
  cumulativeRainfallMm24h: number | null;
  riverLevelM: number | null;
  riverThresholdM: number | null;
  glacialLake: {
    available: boolean;
    lakeId?: string;
    status?: "normal" | "watch" | "elevated";  // only if available
    notes?: string;
  };
  historicalFloodEvents5y: number | null;  // count, demo
  timestamp: string;  // ISO8601
  dataProvenance: "DEMO" | "DEMO_SIMULATION" | "LIVE";
}
```

### Heat context

```typescript
{
  locationId: string;
  temperatureC: number | null;
  humidityPct: number | null;
  heatIndexC: number | null;
  elevatedTempDurationHours: number | null;
  timestamp: string;
  dataProvenance: "DEMO" | "LIVE";
}
```

## Risk Assessment (engine output)

```typescript
{
  location: { id, name, state, lat, lng };
  scenario: "flood_glof" | "extreme_heat";
  riskScore: number;       // 0-100
  riskLevel: "low" | "moderate" | "high" | "critical";
  riskCategory: string;    // human-readable
  confidence: number;      // 0-1
  uncertainty: string;     // narrative
  contributingFactors: ContributingFactor[];
  observations: ObservationEntry[];
  recommendedActions: string[];
  dataSources: DataSourceRef[];
  timestamp: string;
  simulationActive: boolean;
  componentScores: Record<string, number>; // actual additive engine components
  providerObservations: Record<string, ProviderResult>;
  riskDimensions: {
    overallFloodRisk: { level: string; score: number };
    rainfallRisk: { scoreContribution: number };
    riverRisk: { scoreContribution: number };
    downstreamExposure: { scoreContribution: number };
    glacialLakeMonitoring: { status: string; available: boolean; notes: string | null };
  };
  simulationComparison?: {
    baselineLabel: string;
    currentLabel: string;
    changes: { label: string; baseline: number; current: number; delta: number; unit: string }[];
  };
}
```

## ContributingFactor

```typescript
{
  id: string;
  label: string;
  severity: "low" | "moderate" | "high";
  kind: "observed" | "calculated" | "historical";
  detail: string;
  scoreContribution?: number;
}
```

## Alert

```typescript
{
  id: string;
  locationId: string;
  locationName: string;
  severity: "moderate" | "high" | "critical";
  title: string;           // e.g. "Elevated flood-related risk (HeatShield assessment)"
  reason: string;
  potentialExposure: string;
  recommended: string;
  disclaimer: string;      // not an official warning
  createdAt: string;
  simulationActive: boolean;
}
```

## Simulation Override

```typescript
{
  locationId: string;
  scenario: "flood_glof" | "extreme_heat";
  preset?: "normal" | "heavy_rain" | "river_rising" | "extreme_heat";
  overrides?: Partial<EnvironmentalSnapshot>;
}
```

## AI Explanation Request / Response

Request carries full `RiskAssessment` JSON. Response:

```typescript
{
  summary: string;
  sections: {
    observed: string[];
    calculated: string[];
    interpreted: string[];
    recommended: string[];
  };
  answer?: string;  // for Q&A
  disclaimer: string;
}
```
