import type {
  AiExplanation,
  Alert,
  EnsembleForecastEvidence,
  ForecastValidationEvidence,
  FloodEvidence,
  GlobalEvidenceAnswer,
  Location,
  ProviderObservation,
  RealHeatAssessment,
  RiskAssessment,
  WeatherEvidence,
  WeatherLocation,
} from '../types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1'
const GLOBAL_EVIDENCE_TIMEOUT_MS = 30_000

export class ApiError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch (error) {
    throw new ApiError(error instanceof Error ? error.message : 'Network request failed')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new ApiError(err.detail ?? res.statusText, res.status)
  }
  return res.json() as Promise<T>
}

export async function getAppConfig(): Promise<{ dataMode: 'demo' | 'real' }> {
  return fetchJson('/config')
}

export async function searchWeatherLocations(query: string): Promise<WeatherLocation[]> {
  const data = await fetchJson<{ locations: WeatherLocation[] }>(
    `/weather/search?query=${encodeURIComponent(query)}`,
  )
  return data.locations
}

export async function getWeatherForecast(location: WeatherLocation): Promise<ProviderObservation> {
  const params = new URLSearchParams({
    latitude: String(location.lat),
    longitude: String(location.lng),
    name: location.name,
    admin1: location.state,
    country: location.country,
  })
  return fetchJson(`/weather/forecast?${params}`)
}

function coordinateParams(location: WeatherLocation): string {
  return new URLSearchParams({
    latitude: String(location.lat),
    longitude: String(location.lng),
  }).toString()
}

export async function getWeatherEvidence(
  location: WeatherLocation,
  refresh = false,
): Promise<WeatherEvidence> {
  const params = new URLSearchParams({
    ...Object.fromEntries(new URLSearchParams(coordinateParams(location))),
    name: location.name,
    admin1: location.state,
    country: location.country,
  })
  if (refresh) params.set('refresh', 'true')
  return fetchJson(`/weather/evidence?${params}`)
}

export async function getFloodEvidence(location: WeatherLocation, refresh = false): Promise<FloodEvidence> {
  const params = new URLSearchParams(coordinateParams(location))
  if (refresh) params.set('refresh', 'true')
  return fetchJson(`/flood/evidence?${params}`)
}

export async function getWeatherEnsemble(
  location: WeatherLocation,
  refresh = false,
): Promise<EnsembleForecastEvidence> {
  const params = new URLSearchParams(coordinateParams(location))
  if (refresh) params.set('refresh', 'true')
  return fetchJson(`/weather/ensemble?${params}`)
}

export async function getForecastValidation(
  location: WeatherLocation,
  refresh = false,
): Promise<ForecastValidationEvidence> {
  const params = new URLSearchParams(coordinateParams(location))
  if (refresh) params.set('refresh', 'true')
  return fetchJson(`/weather/validation?${params}`)
}

export async function getForecastHeatAssessment(location: WeatherLocation): Promise<RealHeatAssessment> {
  const params = new URLSearchParams({
    latitude: String(location.lat),
    longitude: String(location.lng),
    name: location.name,
    admin1: location.state,
    country: location.country,
  })
  return fetchJson(`/risk/heat-forecast?${params}`)
}

export async function askGlobalEvidence(
  location: WeatherLocation,
  question: string,
  signal?: AbortSignal,
): Promise<GlobalEvidenceAnswer> {
  const controller = new AbortController()
  const timeoutId = globalThis.setTimeout(
    () => controller.abort(new Error('Evidence interpretation request timed out after 20 seconds. Please retry.')),
    GLOBAL_EVIDENCE_TIMEOUT_MS,
  )
  const abortRequest = () => controller.abort(signal?.reason)
  if (signal?.aborted) {
    abortRequest()
  } else {
    signal?.addEventListener('abort', abortRequest, { once: true })
  }

  try {
    return await fetchJson('/ai/evidence', {
      method: 'POST',
      body: JSON.stringify({
        latitude: location.lat,
        longitude: location.lng,
        name: location.name,
        admin1: location.state,
        country: location.country,
        selection_type: location.selectionType ?? 'named_place',
        question,
      }),
      signal: controller.signal,
    })
  } finally {
    globalThis.clearTimeout(timeoutId)
    signal?.removeEventListener('abort', abortRequest)
  }
}

export async function getLocations(): Promise<Location[]> {
  const data = await fetchJson<{ locations: Location[] }>('/locations')
  return data.locations
}

export async function getAllRisk(): Promise<{
  assessments: RiskAssessment[]
  simulationMode: boolean
}> {
  return fetchJson('/risk')
}

export async function getRisk(locationId: string): Promise<RiskAssessment> {
  return fetchJson(`/risk/${locationId}`)
}

export async function getRainfallEvidence(locationId: string): Promise<ProviderObservation> {
  return fetchJson(`/data/rainfall/${encodeURIComponent(locationId)}`)
}

export async function getAlerts(): Promise<Alert[]> {
  const data = await fetchJson<{ alerts: Alert[] }>('/alerts')
  return data.alerts
}

export async function startFloodSimulation(
  locationId: string,
  preset: 'normal' | 'heavy_rain' | 'river_rising',
): Promise<RiskAssessment> {
  const data = await fetchJson<{ assessment: RiskAssessment }>('/simulation/start', {
    method: 'POST',
    body: JSON.stringify({ locationId, scenario: 'flood_glof', preset }),
  })
  return data.assessment
}

export async function startHeatSimulation(
  locationId: string,
  preset: 'normal' | 'extreme_heat',
): Promise<RiskAssessment> {
  const data = await fetchJson<{ assessment: RiskAssessment }>('/simulation/heat', {
    method: 'POST',
    body: JSON.stringify({ locationId, preset }),
  })
  return data.assessment
}

export async function updateFloodSimulationParameters(
  locationId: string,
  rainfallIntensityMmHr: number,
  cumulativeRainfallMm24h: number,
  riverLevelM: number,
): Promise<RiskAssessment> {
  const data = await fetchJson<{ assessment: RiskAssessment }>('/simulation/parameters', {
    method: 'POST',
    body: JSON.stringify({
      locationId,
      rainfallIntensityMmHr,
      cumulativeRainfallMm24h,
      riverLevelM,
    }),
  })
  return data.assessment
}

export async function stopSimulation(): Promise<void> {
  await fetchJson('/simulation/stop', { method: 'POST' })
}

export async function explainRisk(
  locationId: string,
  question?: string,
): Promise<AiExplanation> {
  return fetchJson('/ai/explain', {
    method: 'POST',
    body: JSON.stringify({ locationId, question }),
  })
}

export async function chatAboutRisk(locationId: string, message: string): Promise<AiExplanation> {
  return fetchJson('/ai/chat', {
    method: 'POST',
    body: JSON.stringify({ locationId, message }),
  })
}
