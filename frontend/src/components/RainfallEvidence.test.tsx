import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { MeasurementProvenance, ProviderObservation, RiskAssessment, WeatherLocation } from '../types'
import { LocationPanel } from './LocationPanel'
import { RainfallEvidence } from './RainfallEvidence'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function measurement(
  value: number | null,
  overrides: Partial<MeasurementProvenance> = {},
): MeasurementProvenance {
  return {
    source: 'Open-Meteo',
    source_url: 'https://open-meteo.com/',
    location_id: 'sikkim',
    latitude: 27.533,
    longitude: 88.512,
    data_type: 'FORECAST',
    forecast_time: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    retrieval_time: new Date().toISOString(),
    value,
    unit: 'mm',
    quality: 'Weather-model forecast; not a ground observation.',
    status: value === null ? 'unavailable' : 'forecast',
    ...overrides,
  }
}

function forecast(dataType: 'FORECAST' | 'DEMO' | 'SIMULATION' = 'FORECAST'): ProviderObservation {
  const hourly = measurement(12.4, { data_type: dataType, unit: 'mm/h' })
  const daily = measurement(24.8, { data_type: dataType, unit: 'mm/24h' })
  const quality = 'Weather-model forecast; not a ground observation.'
  return {
    source: dataType === 'DEMO' ? 'HeatShield Demo Dataset' : 'Open-Meteo (weather model forecast)',
    timestamp: new Date().toISOString(),
    retrievedAt: new Date().toISOString(),
    dataType: dataType.toLowerCase(),
    location: { id: 'sikkim', name: 'Sikkim', state: 'Sikkim', lat: 27.533, lng: 88.512 },
    status: 'available',
    dataQuality: quality,
    dataProvenance: dataType === 'DEMO' ? 'DEMO' : 'LIVE',
    provenance: {
      source: dataType === 'DEMO' ? 'HeatShield Demo Dataset' : 'Open-Meteo',
      source_url: dataType === 'DEMO' ? '' : 'https://open-meteo.com/',
      location_id: 'sikkim',
      latitude: 27.533,
      longitude: 88.512,
      data_type: dataType,
      retrieval_time: new Date().toISOString(),
      quality,
      status: 'available',
    },
    values: {
      rainfallIntensityMmHr: { value: 12.4, unit: 'mm/h', status: dataType === 'DEMO' ? 'synthetic' : 'forecast', provenance: hourly },
      cumulativeRainfallMm24h: { value: 24.8, unit: 'mm/24h', status: dataType === 'DEMO' ? 'synthetic' : 'forecast', provenance: daily },
    },
  }
}

function assessment(): RiskAssessment {
  return {
    location: {
      id: 'sikkim',
      name: 'Sikkim',
      state: 'Sikkim',
      lat: 27.533,
      lng: 88.512,
      primaryScenario: 'flood_glof',
    },
    scenario: 'flood_glof',
    riskScore: 31,
    riskLevel: 'moderate',
    riskCategory: 'Prototype assessment',
    confidence: 0.7,
    uncertainty: 'Demo evidence.',
    contributingFactors: [],
    observations: [],
    recommendedActions: [],
    dataSources: [],
    timestamp: new Date().toISOString(),
    simulationActive: false,
    dataProvenance: 'DEMO',
    componentScores: { rainfall_score: 0, river_score: 0, exposure_score: 0, historical_score: 0 },
  }
}

describe('RainfallEvidence', () => {
  it.each(['sikkim', 'delhi'])('requests rainfall evidence from the backend for %s', async (locationId) => {
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue(forecast())

    render(<RainfallEvidence locationId={locationId} />)

    expect(await screen.findByText('FORECAST DATA')).toBeInTheDocument()
    expect(screen.getByText('Open-Meteo')).toBeInTheDocument()
    expect(screen.getByText('12.4 mm/h')).toBeInTheDocument()
    expect(api.getRainfallEvidence).toHaveBeenCalledWith(locationId)
    expect(screen.getByText(/Forecast time:/)).toBeInTheDocument()
    expect(screen.getByText(/Retrieved:/)).toBeInTheDocument()
  })

  it('requests a live forecast by arbitrary global coordinates', async () => {
    const location: WeatherLocation = {
      id: 'auckland',
      name: 'Auckland',
      state: 'Auckland Region',
      country: 'New Zealand',
      lat: -36.85,
      lng: 174.76,
    }
    vi.spyOn(api, 'getWeatherForecast').mockResolvedValue(forecast())

    render(<RainfallEvidence location={location} />)

    expect(await screen.findByText('FORECAST DATA')).toBeInTheDocument()
    expect(api.getWeatherForecast).toHaveBeenCalledWith(location)
  })

  it('shows a loading state while fetching', () => {
    vi.spyOn(api, 'getRainfallEvidence').mockImplementation(() => new Promise(() => {}))

    render(<RainfallEvidence locationId="sikkim" />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading rainfall data')
  })

  it('keeps demo values identified as demo input', async () => {
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue(forecast('DEMO'))

    render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('DEMO INPUT')).toBeInTheDocument()
    expect(screen.getByText('HeatShield Demo Dataset')).toBeInTheDocument()
  })

  it('renders HTTP 503 and network failures without rainfall values', async () => {
    vi.spyOn(api, 'getRainfallEvidence').mockRejectedValueOnce(
      new api.ApiError('Open-Meteo unavailable', 503),
    )
    const { unmount } = render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('UNAVAILABLE')).toBeInTheDocument()
    expect(screen.getByText('No rainfall value is available. Risk assessment remains separate.')).toBeInTheDocument()
    unmount()

    vi.mocked(api.getRainfallEvidence).mockRejectedValueOnce(new api.ApiError('Network offline'))
    render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('ERROR')).toBeInTheDocument()
    expect(screen.getByText('Network offline')).toBeInTheDocument()
  })

  it('reports malformed responses clearly', async () => {
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue({} as ProviderObservation)

    render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('The rainfall provider returned a malformed response.')).toBeInTheDocument()
  })

  it('does not turn a missing rainfall value into zero', async () => {
    const data = forecast()
    data.values.rainfallIntensityMmHr.provenance = measurement(null)
    data.values.rainfallIntensityMmHr.value = null
    data.status = 'partial'
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue(data)

    render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('AVAILABLE')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.queryByText('0 mm/h')).not.toBeInTheDocument()
    expect(screen.getByText(/Some rainfall values are missing/)).toBeInTheDocument()
  })

  it('marks an expired forecast stale and does not show its value as current', async () => {
    const oldTime = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
    const data = forecast()
    data.values.rainfallIntensityMmHr.provenance = measurement(12.4, { forecast_time: oldTime })
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue(data)

    render(<RainfallEvidence locationId="sikkim" />)

    expect(await screen.findByText('STALE')).toBeInTheDocument()
    expect(screen.getByText(/forecast period has passed/)).toBeInTheDocument()
    expect(screen.queryByText('12.4 mm/h')).not.toBeInTheDocument()
  })

  it('keeps the displayed risk score separate from forecast evidence', async () => {
    vi.spyOn(api, 'getRainfallEvidence').mockResolvedValue(forecast())

    render(
      <LocationPanel
        assessment={assessment()}
        loading={false}
        explanation={null}
        explainLoading={false}
        question=""
        onQuestionChange={() => {}}
        onExplain={() => {}}
        onOpenSimulation={() => {}}
      />,
    )

    expect(await screen.findByText('FORECAST DATA')).toBeInTheDocument()
    expect(screen.getByText('31')).toBeInTheDocument()
    expect(screen.getByText('DEMO DATA')).toBeInTheDocument()
    expect(screen.getByText('Forecast evidence — not currently used in risk calculation.')).toBeInTheDocument()
  })
})
