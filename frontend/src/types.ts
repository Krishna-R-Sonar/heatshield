export type RiskLevel = 'low' | 'moderate' | 'high' | 'critical'
export type Scenario = 'flood_glof' | 'extreme_heat'

export interface Location {
  id: string
  name: string
  state: string
  lat: number
  lng: number
  primaryScenario: Scenario
}

export interface WeatherLocation {
  id: string
  name: string
  state: string
  country: string
  lat: number
  lng: number
  selectionType?: 'named_place' | 'map_point'
}

export interface WeatherMeasurement {
  value: number | null
  unit: string
  status: 'AVAILABLE' | 'UNAVAILABLE'
  data_type: 'FORECAST'
  forecast_time: string | null
  provenance: Record<string, unknown>
}

export interface HistoricalAnomalyPoint {
  date: string
  indicator: 'temperature_max' | 'precipitation' | 'rainfall'
  current_value: number | null
  unit: string
  anomaly: number | null
  percentile: number | null
  historical_baseline: {
    mean: number
    median: number
    p25: number
    p75: number
    minimum: number
    maximum: number
    sample_count: number
  } | null
  status: 'AVAILABLE' | 'UNAVAILABLE'
  forecast_data_type: 'FORECAST'
  baseline_data_type: 'REANALYSIS'
  forecast_source?: string | null
}

export interface WeatherEvidence {
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE'
  location: {
    name: string
    state: string
    country: string
    requested_latitude: number
    requested_longitude: number
    grid_latitude: number | null
    grid_longitude: number | null
    elevation_m: number | null
    timezone: string | null
  }
  forecast: Record<string, WeatherMeasurement>
  hourly: {
    times: string[]
    values: Record<string, Array<number | null>>
    units: Record<string, string>
    interval_minutes?: number | null
    aggregation_period?: string | null
    provenance?: Record<string, Array<Record<string, unknown>>>
  }
  daily: {
    times: string[]
    values: Record<string, Array<number | null>>
    units: Record<string, string>
    interval_minutes?: number | null
    aggregation_period?: string | null
    provenance?: Record<string, Array<Record<string, unknown>>>
  }
  historical_anomalies: {
    status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE'
    anomalies: HistoricalAnomalyPoint[]
    baseline_period: string
    methodology: string
    source: string
    model: string
    spatial_resolution: string
    retrieved_at: string
    grid_latitude: number | null
    grid_longitude: number | null
    quality: string
    limitations: string[]
  } | null
  provenance: {
    source: string
    source_url: string
    api: string
    data_type: 'FORECAST'
    model_selection: string
    model_id: string | null
    forecast_generated_at: string | null
    retrieved_at: string
    generationtime_ms: number | null
    timezone: string | null
    spatial_resolution: string
    quality: string
    limitations: string[]
  }
  provider_status?: WeatherProviderStatus[]
  provider_disagreements?: WeatherProviderDisagreement[]
  missing_fields?: string[]
}

export interface WeatherProviderStatus {
  provider: string
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE' | 'NOT_CONFIGURED' | 'NOT_NEEDED'
  retrieved_at: string | null
  fields: string[]
  used_fields?: string[]
  reason: string | null
}

export interface WeatherProviderDisagreement {
  variable: string
  first_provider: string
  first_value: number
  second_provider: string
  second_value: number
  difference: number
  unit: string
  valid_time: string
  materiality_threshold: number
}

export interface FloodMeasurement {
  value: number | null
  unit: string
  status: 'AVAILABLE' | 'UNAVAILABLE'
  data_type: 'FORECAST' | 'REANALYSIS'
  reference_time: string
  provenance: Record<string, unknown>
}

export interface FloodEvidence {
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE'
  location: {
    requested_latitude: number
    requested_longitude: number
    grid_latitude: number | null
    grid_longitude: number | null
    elevation_m: number | null
    timezone: string | null
    river_association: 'UNVERIFIED'
  }
  daily: Array<{
    time: string
    data_type: 'FORECAST' | 'REANALYSIS'
    discharge: FloodMeasurement
    mean: FloodMeasurement
    median: FloodMeasurement
    minimum: FloodMeasurement
    maximum: FloodMeasurement
    p25: FloodMeasurement
    p75: FloodMeasurement
    discharge_spread_iqr: number | null
    uncertainty: 'LOW' | 'MODERATE' | 'HIGH' | 'UNAVAILABLE'
    uncertainty_reason: string
  }>
  provenance: {
    source: string
    source_url: string
    dataset: string
    model: string
    model_version_in_payload: boolean
    data_type: 'MODEL_EVIDENCE'
    retrieved_at: string
    forecast_generated_at: string | null
    generationtime_ms: number | null
    timezone: string | null
    spatial_resolution_km: number
    requested_latitude: number
    requested_longitude: number
    grid_latitude: number | null
    grid_longitude: number | null
    historical_period_available: string
    returned_historical_period: string | null
    returned_forecast_period: string | null
    forecast_horizon_days: number
    quality: string
    limitations: string[]
  }
}

export interface EnsembleStatistics {
  status: 'AVAILABLE' | 'UNAVAILABLE'
  unit: string
  member_count: number
  mean: number | null
  median: number | null
  p25: number | null
  p75: number | null
  minimum: number | null
  maximum: number | null
  spread_iqr: number | null
  uncertainty: 'LOW' | 'MODERATE' | 'HIGH' | 'UNAVAILABLE'
  uncertainty_reason: string
}

export interface EnsembleForecastEvidence {
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE'
  member_count: number
  daily: Array<{
    date: string
    temperature_max: EnsembleStatistics
    precipitation_sum: EnsembleStatistics
  }>
  provenance: {
    source: string
    source_url: string
    model: string
    model_id: string
    data_type: 'ENSEMBLE_FORECAST'
    retrieved_at: string
    forecast_generated_at: string | null
    timezone: string | null
    requested_latitude: number
    requested_longitude: number
    grid_latitude: number | null
    grid_longitude: number | null
    spatial_resolution: string
    forecast_period: string | null
    classification_method: string
    limitations: string[]
  }
}

export interface GlobalEvidenceAnswer {
  summary: string
  ai_response: string | null
  environmental_assessment?: GlobalEnvironmentalAssessment
  sections: {
    evidence: string[]
    interpreted: string[]
    recommended: string[]
    recommendations: {
      residents_and_workers: GlobalEvidenceRecommendation[]
      municipal_planners: GlobalEvidenceRecommendation[]
    }
  }
  disclaimer: string
  source: string
}

export interface GlobalEnvironmentalAssessment {
  status: 'AI_GENERATED' | 'DETERMINISTIC_FALLBACK'
  evidence_completeness?: Record<string, {
    status: 'sufficient_evidence' | 'partial_evidence' | 'insufficient_evidence'
    rationale: string
  }>
  location_summary: string
  location_summary_evidence_refs: string[]
  key_findings: { finding: string; evidence_refs: string[] }[]
  environmental_concerns: { concern: string; evidence_refs: string[] }[]
  evidence_based_suggestions: GlobalEnvironmentalSuggestion[]
  general_preventive_ideas: GlobalEnvironmentalSuggestion[]
  community_opportunities: GlobalEnvironmentalSuggestion[]
  missing_information: string[]
  recommended_next_steps: string[]
  limitations: string[]
  generated_at: string
  model_used: string | null
  evidence_references: GlobalEvidenceReference[]
}

export interface GlobalEnvironmentalSuggestion {
  suggestion: string
  why_it_may_help: string
  evidence_refs: string[]
  who_can_act: string
  effort: 'low' | 'medium' | 'high' | 'unknown'
  expected_benefit: string
  confidence: 'strong' | 'partial' | 'general'
  limitations: string
  category: string
}

export interface GlobalEvidenceReference {
  id: string
  label: string
  value: string
  unit: string
  period: string
  source: string
  evidence_type: string
  limitation: string
}

export interface GlobalEvidenceRecommendation {
  topic: string
  recommendation: string
  evidence_type: 'FORECAST' | 'MODEL_EVIDENCE' | 'GENERAL'
  evidence_basis: string
  period: string | null
  scope: 'forecast-preparedness' | 'general-mitigation' | 'evidence-limitation' | 'evidence-validation'
  guidance_source?: 'ai_generated' | 'evidence_based_fallback'
}

export type GlobalEvidenceReviewState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'complete'; answer: GlobalEvidenceAnswer }

export interface ForecastValidationEvidence {
  status: 'AVAILABLE' | 'DEGRADED' | 'UNAVAILABLE'
  title: string
  metrics: Array<{
    status: 'AVAILABLE' | 'UNAVAILABLE'
    variable: 'temperature' | 'precipitation'
    forecast_horizon_hours: number
    mean_absolute_error: number | null
    unit: string
    sample_count: number
  }>
  verification_period: string
  forecast_source: string
  forecast_model_selection: string
  reference_source: string
  reference_model: string
  reference_data_type: 'REANALYSIS'
  retrieved_at: string
  quality: string
  limitations: string[]
}

export interface RealHeatAssessment {
  assessmentType: 'FORECAST_HEAT'
  status: 'INCOMPLETE' | 'UNAVAILABLE'
  scoreStatus: 'NOT_CALCULATED'
  riskScore: null
  riskLevel: null
  dataProvenance: 'FORECAST'
  location: {
    id: string
    name: string
    state: string
    latitude: number
    longitude: number
  }
  assessmentWindow: {
    basis: string
    forecastTime: string | null
    timezone: string | null
  }
  provenance: {
    source: string
    sourceUrl: string
    dataType: string
    retrievedAt: string | null
    requestedCoordinates: { latitude: number; longitude: number }
    gridCoordinates: { latitude: number | null; longitude: number | null } | null
    locationAssociation: string
    spatialResolution: string
    limitations: string[]
  }
  missingInputs: string[]
  reasons: Array<{ code: string; field: string; message: string }>
}

export interface ContributingFactor {
  id: string
  label: string
  severity: string
  kind: string
  detail: string
  scoreContribution?: number
}

export interface ObservationEntry {
  label: string
  value: string
  kind: string
}

export interface ProviderValue {
  value: number | string | boolean | null
  unit: string
  status: 'synthetic' | 'simulated' | 'observed' | 'forecast' | 'unavailable'
  dataType?: 'OBSERVATION' | 'FORECAST' | 'DEMO' | 'SIMULATION' | 'CALCULATED' |
    'observation' | 'forecast' | 'demo' | 'simulation' | 'calculated'
  validTime?: string | null
  provenance?: MeasurementProvenance
}

export interface MeasurementProvenance {
  source: string
  source_url: string
  location_id: string
  latitude: number | null
  longitude: number | null
  data_type: 'OBSERVATION' | 'FORECAST' | 'DEMO' | 'SIMULATION' | 'CALCULATED'
  observation_time?: string | null
  forecast_time?: string | null
  retrieval_time: string
  value: number | string | boolean | null
  unit: string
  quality: string
  status: string
}

export interface ProviderObservation {
  source: string
  timestamp: string
  retrievedAt?: string
  dataType?: string
  location: Pick<Location, 'id' | 'name' | 'state' | 'lat' | 'lng'>
  status: 'available' | 'partial' | 'unavailable'
  dataQuality: string
  dataProvenance: 'DEMO' | 'DEMO_SIMULATION' | 'LIVE'
  values: Record<string, ProviderValue>
  provenance?: Omit<MeasurementProvenance, 'value' | 'unit' | 'status'> & {
    quality: string
    status: string
  }
  measurements?: MeasurementProvenance[]
}

export interface RiskDimension {
  level?: RiskLevel | 'unavailable' | 'watch' | 'normal' | 'elevated'
  score?: number
  scoreContribution?: number
  available?: boolean
  status?: string
  notes?: string | null
}

export interface RiskAssessment {
  location: Location
  scenario: Scenario
  riskScore: number
  riskLevel: RiskLevel
  riskCategory: string
  confidence: number
  uncertainty: string
  contributingFactors: ContributingFactor[]
  observations: ObservationEntry[]
  recommendedActions: string[]
  dataSources: { name: string; note: string }[]
  timestamp: string
  simulationActive: boolean
  dataProvenance?: 'DEMO' | 'DEMO_SIMULATION' | 'LIVE'
  providerObservations?: Record<string, ProviderObservation>
  riskDimensions?: Record<string, RiskDimension>
  componentScores: Record<string, number>
}

export interface Alert {
  id: string
  locationId: string
  locationName: string
  severity: string
  title: string
  reason: string
  potentialExposure: string
  recommended: string
  disclaimer: string
  createdAt: string
  simulationActive: boolean
}

export interface AiExplanation {
  summary: string
  sections: {
    observed: string[]
    calculated: string[]
    interpreted: string[]
    recommended: string[]
  }
  disclaimer: string
  source?: string
}
