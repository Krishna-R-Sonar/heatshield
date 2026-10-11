import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type {
  EnsembleForecastEvidence,
  FloodEvidence,
  ForecastValidationEvidence,
  GlobalEvidenceAnswer,
  RealHeatAssessment,
  WeatherEvidence,
  WeatherLocation,
} from '../types'
import { GlobalEvidencePanel } from './GlobalEvidencePanel'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const location: WeatherLocation = {
  id: 'auckland',
  name: 'Auckland',
  state: 'Auckland Region',
  country: 'New Zealand',
  lat: -36.85,
  lng: 174.76,
}

const mapPoint: WeatherLocation = {
  id: 'map-point-28.1177-85.4736',
  name: 'Map point 28.1177, 85.4736',
  state: 'Selected map point',
  country: '',
  lat: 28.1177,
  lng: 85.4736,
  selectionType: 'map_point',
}

function weatherEvidence(): WeatherEvidence {
  const forecast = (value: number, unit: string) => ({
    value,
    unit,
    status: 'AVAILABLE' as const,
    data_type: 'FORECAST' as const,
    forecast_time: '2026-10-09T14:00:00+13:00',
    provenance: { source: 'Open-Meteo', data_type: 'FORECAST' },
  })
  const times = Array.from({ length: 48 }, (_, index) => {
    const date = new Date(Date.parse('2026-10-09T00:00:00+13:00') + index * 60 * 60 * 1000)
    return `${date.toISOString().slice(0, 19)}+00:00`
  })
  return {
    status: 'AVAILABLE',
    location: {
      name: 'Auckland',
      state: 'Auckland Region',
      country: 'New Zealand',
      requested_latitude: -36.85,
      requested_longitude: 174.76,
      grid_latitude: -36.875,
      grid_longitude: 174.75,
      elevation_m: 14,
      timezone: 'Pacific/Auckland',
    },
    forecast: {
      temperature: forecast(24, '°C'),
      apparent_temperature: forecast(25, '°C'),
      humidity: forecast(65, '%'),
      wet_bulb_temperature: forecast(20, '°C'),
      precipitation: forecast(1.5, 'mm'),
      precipitation_probability: forecast(80, '%'),
      wind_speed: forecast(12, 'km/h'),
      wind_gusts: forecast(20, 'km/h'),
      wind_direction: forecast(180, '°'),
      visibility: forecast(10000, 'm'),
      cloud_cover: forecast(55, '%'),
      uv_index: forecast(4, ''),
      weather_code: forecast(61, 'wmo code'),
    },
    hourly: {
      times,
      values: { temperature_2m: Array.from({ length: 48 }, (_, index) => 20 + index / 2) },
      units: { temperature_2m: '°C' },
    },
    daily: {
      times: ['2026-10-09', '2026-10-10'],
      values: {
        temperature_2m_max: [30, 27],
        apparent_temperature_max: [31, 28],
        precipitation_sum: [12, 4],
        rain_sum: [11, 3],
      },
      units: { temperature_2m_max: '°C', precipitation_sum: 'mm', rain_sum: 'mm' },
    },
    historical_anomalies: {
      status: 'AVAILABLE',
      anomalies: [{
        date: '2026-10-09',
        indicator: 'temperature_max',
        current_value: 30,
        unit: '°C',
        anomaly: 5.8,
        percentile: 90,
        historical_baseline: { mean: 24.2, median: 24, p25: 22, p75: 26, minimum: 12, maximum: 34, sample_count: 930 },
        status: 'AVAILABLE',
        forecast_data_type: 'FORECAST',
        baseline_data_type: 'REANALYSIS',
      }, {
        date: '2026-10-09',
        indicator: 'precipitation',
        current_value: 12,
        unit: 'mm',
        anomaly: -3.1,
        percentile: 70,
        historical_baseline: { mean: 15.1, median: 8, p25: 5, p75: 12, minimum: 0, maximum: 50, sample_count: 930 },
        status: 'AVAILABLE',
        forecast_data_type: 'FORECAST',
        baseline_data_type: 'REANALYSIS',
      }, {
        date: '2026-10-09',
        indicator: 'rainfall',
        current_value: 11,
        unit: 'mm',
        anomaly: 1.3,
        percentile: 60,
        historical_baseline: { mean: 9.7, median: 9, p25: 6, p75: 13, minimum: 0, maximum: 26, sample_count: 930 },
        status: 'AVAILABLE',
        forecast_data_type: 'FORECAST',
        baseline_data_type: 'REANALYSIS',
      }],
      baseline_period: '1991-2020',
      methodology: 'Same calendar month; historical daily mean and empirical percentile.',
      source: 'Open-Meteo Historical Weather API',
      model: 'ERA5',
      spatial_resolution: '0.25 degrees (~25 km)',
      retrieved_at: '2026-10-08T01:00:00Z',
      grid_latitude: -36.875,
      grid_longitude: 174.75,
      quality: 'ERA5 monthly baseline.',
      limitations: ['Reanalysis estimate, not a station observation.'],
    },
    provenance: {
      source: 'Open-Meteo',
      source_url: 'https://open-meteo.com/',
      api: 'Weather Forecast API',
      data_type: 'FORECAST',
      model_selection: 'auto',
      model_id: null,
      forecast_generated_at: null,
      retrieved_at: '2026-10-08T01:00:00Z',
      generationtime_ms: 1,
      timezone: 'Pacific/Auckland',
      spatial_resolution: 'Exact resolution not returned for auto selection.',
      quality: 'Weather-model forecast; not a station observation.',
      limitations: ['The exact selected model ID is not returned.'],
    },
  }
}

function floodEvidence(): FloodEvidence {
  const measurement = (value: number | null, dataType: 'FORECAST' | 'REANALYSIS' = 'FORECAST') => ({
    value,
    unit: 'm³/s',
    status: value === null ? 'UNAVAILABLE' as const : 'AVAILABLE' as const,
    data_type: dataType,
    reference_time: '2026-10-09',
    provenance: { source: 'GloFAS' },
  })
  const day = {
    time: '2026-10-09',
    data_type: 'FORECAST' as const,
    discharge: measurement(12),
    mean: measurement(12),
    median: measurement(11),
    minimum: measurement(6),
    maximum: measurement(20),
    p25: measurement(8),
    p75: measurement(16),
    discharge_spread_iqr: 8,
    uncertainty: 'MODERATE' as const,
    uncertainty_reason: 'Modeled discharge spread is moderate.',
  }
  return {
    status: 'AVAILABLE',
    location: {
      requested_latitude: -36.85,
      requested_longitude: 174.76,
      grid_latitude: -36.875,
      grid_longitude: 174.75,
      elevation_m: 14,
      timezone: 'GMT',
      river_association: 'UNVERIFIED',
    },
    daily: [day],
    provenance: {
      source: 'Open-Meteo Global Flood API',
      source_url: 'https://open-meteo.com/en/docs/flood-api',
      dataset: 'Global Flood Awareness System (GloFAS)',
      model: 'GloFAS v4',
      model_version_in_payload: false,
      data_type: 'MODEL_EVIDENCE',
      retrieved_at: '2026-10-08T01:00:00Z',
      forecast_generated_at: null,
      generationtime_ms: 1,
      timezone: 'GMT',
      spatial_resolution_km: 5,
      requested_latitude: -36.85,
      requested_longitude: 174.76,
      grid_latitude: -36.875,
      grid_longitude: 174.75,
      historical_period_available: '1984 through July 2022',
      returned_historical_period: null,
      returned_forecast_period: '2026-10-09 to 2026-10-09',
      forecast_horizon_days: 1,
      quality: 'Modeled discharge evidence.',
      limitations: ['Not an actual river level at this location.'],
    },
  }
}

function ensembleEvidence(): EnsembleForecastEvidence {
  const statistics = {
    status: 'AVAILABLE' as const,
    unit: 'mm',
    member_count: 51,
    mean: 50,
    median: 45,
    p25: 20,
    p75: 80,
    minimum: 5,
    maximum: 110,
    spread_iqr: 60,
    uncertainty: 'HIGH' as const,
    uncertainty_reason: 'Ensemble IQR is wide relative to median.',
  }
  return {
    status: 'AVAILABLE',
    member_count: 51,
    daily: [{
      date: '2026-10-09',
      temperature_max: {
        ...statistics,
        unit: '°C',
        mean: 24.5,
        median: 24.5,
        p25: 22.25,
        p75: 26.75,
        minimum: 20,
        maximum: 29,
        spread_iqr: 4.5,
      },
      precipitation_sum: {
        ...statistics,
        mean: 10.8,
        median: 10.8,
        p25: 5.4,
        p75: 16.2,
        minimum: 0,
        maximum: 30,
        spread_iqr: 10.8,
      },
    }],
    provenance: {
      source: 'Open-Meteo Ensemble API',
      source_url: 'https://open-meteo.com/en/docs/ensemble-api',
      model: 'ECMWF IFS 0.25° ensemble',
      model_id: 'ecmwf_ifs025',
      data_type: 'ENSEMBLE_FORECAST',
      retrieved_at: '2026-10-08T01:00:00Z',
      forecast_generated_at: null,
      timezone: 'GMT',
      requested_latitude: -36.85,
      requested_longitude: 174.76,
      grid_latitude: -36.875,
      grid_longitude: 174.75,
      spatial_resolution: '0.25° (~25 km)',
      forecast_period: '2026-10-09 to 2026-10-09',
      classification_method: 'IQR display bands; not disaster probabilities.',
      limitations: ['Spread is not probability of disaster.'],
    },
  }
}

function validationEvidence(): ForecastValidationEvidence {
  return {
    status: 'AVAILABLE',
    title: 'Historical forecast performance',
    metrics: [{
      status: 'AVAILABLE',
      variable: 'temperature',
      forecast_horizon_hours: 24,
      mean_absolute_error: 1.8,
      unit: '°C',
      sample_count: 100,
    }],
    verification_period: '2026-10-01 to 2026-10-07',
    forecast_source: 'Open-Meteo Previous Runs API',
    forecast_model_selection: 'auto',
    reference_source: 'Open-Meteo Historical Weather API',
    reference_model: 'ERA5 reanalysis',
    reference_data_type: 'REANALYSIS',
    retrieved_at: '2026-10-08T01:00:00Z',
    quality: 'Compared 100 forecast/reference pairs.',
    limitations: ['ERA5 is not a station observation.'],
  }
}

function heatAssessment(): RealHeatAssessment {
  return {
    assessmentType: 'FORECAST_HEAT',
    status: 'INCOMPLETE',
    scoreStatus: 'NOT_CALCULATED',
    riskScore: null,
    riskLevel: null,
    dataProvenance: 'FORECAST',
    location: {
      id: 'selected-point',
      name: 'Auckland',
      state: 'Auckland Region',
      latitude: -36.85,
      longitude: 174.76,
    },
    assessmentWindow: {
      basis: 'NEXT_AVAILABLE_HOURLY_FORECAST',
      forecastTime: '2026-10-09T14:00:00+13:00',
      timezone: 'Pacific/Auckland',
    },
    provenance: {
      source: 'Open-Meteo',
      sourceUrl: 'https://open-meteo.com/',
      dataType: 'FORECAST',
      retrievedAt: new Date().toISOString(),
      requestedCoordinates: { latitude: -36.85, longitude: 174.76 },
      gridCoordinates: { latitude: -36.875, longitude: 174.75 },
      locationAssociation: 'REQUESTED_COORDINATES_MATCH',
      spatialResolution: 'Model-dependent',
      limitations: ['Forecast values are model output, not ground observations.'],
    },
    missingInputs: ['heat_index_c', 'elevated_temp_duration_hours', 'population_exposure_index'],
    reasons: [{
      code: 'REQUIRED_REAL_INPUT_UNAVAILABLE',
      field: 'population_exposure_index',
      message: 'A real population exposure value is not available.',
    }],
  }
}

describe('GlobalEvidencePanel', () => {
  it('loads weather, flood, and ensemble evidence concurrently and renders provenance', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())
    const copilotAnswer: GlobalEvidenceAnswer = {
      summary: 'Forecast evidence was retrieved for Auckland.',
      ai_response: 'The selected-hour forecast is 24 °C with 1.5 mm precipitation.',
      sections: {
        evidence: ['Open-Meteo temperature forecast: 24 °C.'],
        interpreted: [],
        recommended: [],
        recommendations: {
          residents_and_workers: [{
            topic: 'rainfall',
            recommendation: 'For plans on this date, check local forecast updates before relying on dry conditions.',
            evidence_type: 'FORECAST',
            evidence_basis: 'Open-Meteo precipitation forecast: 1.5 mm at 2026-10-09T14:00:00+13:00.',
            period: '2026-10-09T14:00:00+13:00',
            scope: 'forecast-preparedness',
            guidance_source: 'ai_generated',
          }],
          municipal_planners: [{
            topic: 'rainfall',
            recommendation: 'Compare the forecast with verified local gauges before making drainage decisions.',
            evidence_type: 'FORECAST',
            evidence_basis: 'Open-Meteo precipitation forecast: 1.5 mm at 2026-10-09T14:00:00+13:00.',
            period: '2026-10-09T14:00:00+13:00',
            scope: 'forecast-preparedness',
            guidance_source: 'ai_generated',
          }],
        },
      },
      disclaimer: 'Not an official warning.',
      source: 'openrouter_ai_evidence_guidance',
    }
    vi.spyOn(api, 'askGlobalEvidence').mockResolvedValue(copilotAnswer)

    render(<GlobalEvidencePanel location={location} />)

    expect(await screen.findByRole('heading', { name: 'Next forecast' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Next forecast' }).parentElement)
      .toHaveTextContent(/24 °C.*feels like 25 °C/i)
    expect(screen.getByRole('heading', { name: 'Warmest forecast day' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Wettest forecast day' }).parentElement)
      .toHaveTextContent(/12 mm total precipitation.*11 mm/)
    expect(await screen.findAllByRole('heading', { name: 'What to check next' })).toHaveLength(2)
    expect(screen.queryByText(/AI suggestion · FORECAST/)).not.toBeInTheDocument()
    expect(screen.getByText(/check local forecast updates before relying on dry conditions/)).toBeInTheDocument()
    expect(screen.queryByText(/Compare the forecast with verified local gauges/)).not.toBeInTheDocument()
    expect(screen.getByText(/does not calculate a real-world heat or flood risk score/)).toBeInTheDocument()
    expect(screen.getByText(/computer-model estimate and may not represent a river at this location/)).toBeInTheDocument()
    expect(screen.queryByText(/Review local heat-health guidance when planning outdoor activity/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('Explore detailed weather, flood, and historical evidence'))
    expect((await screen.findAllByText(/5.8 °C above the historical October average/)).length).toBe(1)
    expect(screen.getByText(/total precipitation forecast for Friday, 9 Oct is 12 mm/))
      .toHaveTextContent('near the middle of comparable October days')
    expect(screen.getByText(/The average and historical ranking can differ/)).toBeInTheDocument()
    expect(screen.getByText(/rainfall forecast for Friday, 9 Oct is 11 mm/))
      .toHaveTextContent('near the middle of comparable October days')
    expect(screen.getByText('Precipitation in this forecast hour')).toBeInTheDocument()
    expect(screen.getByText(/hourly precipitation amount applies to that forecast hour only/)).toBeInTheDocument()
    expect(screen.getByText('Weather conditions: Light rain.')).toBeInTheDocument()
    expect(screen.getByText('Estimated river flow (computer model)')).toBeInTheDocument()
    expect(screen.getByText('Historical forecast performance')).toBeInTheDocument()
    expect(screen.getByText(/Forecast highs:/)).toHaveTextContent('Forecasts vary more')
    expect(screen.getByText('Middle half of forecast highs (P25–P75)').parentElement)
      .toHaveTextContent('22.25 °C–26.75 °C')
    expect(screen.getByText('Middle half of precipitation estimates (P25–P75)').parentElement)
      .toHaveTextContent('5.4 mm–16.2 mm')
    expect(screen.getByText(/Model agreement is not a guarantee/)).toBeInTheDocument()
    expect(screen.getByRole('img', {
      name: /Temperature forecast, .*up to 24 hours, from Fri, 9 Oct, 2:00 pm.*to Sat, 10 Oct, 1:00 pm/i,
    })).toBeInTheDocument()
    expect(screen.getByText(/Highest hourly temperature · .*up to 24 hours: 38\.5 °C at Sat, 10 Oct, 1:00 pm/i))
      .toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Daily total precipitation by local date' })).toBeInTheDocument()
    expect(api.getWeatherEvidence).toHaveBeenCalledWith(location, false)
    expect(api.getFloodEvidence).toHaveBeenCalledWith(location, false)
    expect(api.getWeatherEnsemble).toHaveBeenCalledWith(location, false)
    expect(api.getForecastHeatAssessment).toHaveBeenCalledWith(location)
    expect(await screen.findByText('Can we assess local heat risk?')).toBeInTheDocument()
    expect(screen.getByText(/No numeric risk score is calculated/)).toBeInTheDocument()
    expect(screen.getByText('local population exposure')).toBeInTheDocument()
    expect(screen.queryByText(/^Risk score:/i)).not.toBeInTheDocument()
    expect((await screen.findByRole('heading', { name: 'Wettest forecast day' })).parentElement)
      .toHaveTextContent(/12 mm total precipitation.*11 mm/)

    fireEvent.click(screen.getByText('Weather provenance'))
    expect(screen.getByText(/model ID: Not provided by auto selection/)).toBeInTheDocument()
  })

  it('refreshes the automatic Copilot interpretation with provider evidence', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())
    const copilotRequest = vi.spyOn(api, 'askGlobalEvidence')
      .mockRejectedValue(new Error('Copilot unavailable'))

    render(<GlobalEvidencePanel location={location} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Copilot unavailable')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect((await screen.findByRole('heading', { name: 'Wettest forecast day' })).parentElement)
      .toHaveTextContent(/12 mm total precipitation.*11 mm/)
    expect(screen.queryByText(/Suggestions unavailable:/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental conditions and personal preparedness' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental and infrastructure considerations' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update data' }))
    await waitFor(() => expect(copilotRequest).toHaveBeenCalledTimes(2))
  })

  it('labels partial rainfall coverage and does not treat missing days as zero', async () => {
    const weather = weatherEvidence()
    weather.daily.values.precipitation_sum = [null, 4]
    weather.daily.values.rain_sum = [null, 3]
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weather)
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())
    vi.spyOn(api, 'askGlobalEvidence').mockRejectedValue(new Error('Copilot unavailable'))

    render(<GlobalEvidencePanel location={location} />)

    expect((await screen.findByRole('heading', { name: 'Wettest forecast day' })).parentElement)
      .toHaveTextContent(/4 mm total precipitation.*10 Oct/)
    expect(screen.getByText(/Daily precipitation is available for 1 of 2 forecast days\. Missing days are unknown, not zero/))
      .toBeInTheDocument()
  })

  it('does not present rounded-equal quartiles as meaningful zero spread', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    const ensemble = ensembleEvidence()
    ensemble.daily[0].temperature_max.p25 = 0.001
    ensemble.daily[0].temperature_max.p75 = 0.004
    ensemble.daily[0].temperature_max.spread_iqr = 0.003
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensemble)
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())

    render(<GlobalEvidencePanel location={location} />)

    expect(await screen.findByRole('heading', { name: 'What to know first' })).toBeInTheDocument()
    fireEvent.click(screen.getByText('Explore detailed weather, flood, and historical evidence'))

    expect(screen.getByText('Middle half of forecast highs (P25–P75)').parentElement)
      .toHaveTextContent(/No difference at displayed precision \(less than 0\.01 °C–less than 0\.01 °C\)/)
    expect(screen.getByText(/Matching estimates do not guarantee/)).toBeInTheDocument()
  })

  it('displays a small nonzero forecast-error MAE without rounding it to zero', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    const validation = validationEvidence()
    validation.metrics[0].mean_absolute_error = 0.00001
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validation)
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())

    render(<GlobalEvidencePanel location={location} />)

    expect(await screen.findByRole('heading', { name: 'What to know first' })).toBeInTheDocument()
    fireEvent.click(screen.getByText('Explore detailed weather, flood, and historical evidence'))

    expect(await screen.findByText('less than 0.1 °C')).toBeInTheDocument()
    expect(screen.getByText(/Values under the displayed precision are marked as smaller than that amount/)).toBeInTheDocument()
  })

  it('keeps weather visible when flood fails and reports each provider independently', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockRejectedValue(new Error('GloFAS offline'))
    vi.spyOn(api, 'getWeatherEnsemble').mockRejectedValue(new Error('Ensemble offline'))
    vi.spyOn(api, 'getForecastValidation').mockRejectedValue(new Error('Validation offline'))
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())

    render(<GlobalEvidencePanel location={location} />)

    expect(await screen.findByText('Estimated river flow (computer model)')).toBeInTheDocument()
    expect(await screen.findByText(/River-flow data source unavailable · GloFAS offline/)).toBeInTheDocument()
    expect(await screen.findByText(/Forecast comparison source unavailable · Ensemble offline/)).toBeInTheDocument()
    expect(await screen.findByText(/Historical forecast-check source unavailable · Validation offline/)).toBeInTheDocument()
    expect(screen.getAllByText('24 °C').length).toBeGreaterThan(0)
  })

  it('shows point weather context without implying resident or planner context', async () => {
    vi.spyOn(api, 'getWeatherEvidence').mockResolvedValue(weatherEvidence())
    vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())

    render(<GlobalEvidencePanel location={mapPoint} />)

    expect(await screen.findByRole('heading', { name: 'What to know first' })).toBeInTheDocument()
    expect(screen.getByText(/selected coordinates and nearby forecast area/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental conditions and personal preparedness' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental and infrastructure considerations' })).not.toBeInTheDocument()
  })

  it('refreshes all evidence sources and retains prior weather visibly after a refresh failure', async () => {
    const weatherRequest = vi.spyOn(api, 'getWeatherEvidence')
      .mockResolvedValueOnce(weatherEvidence())
      .mockRejectedValueOnce(new Error('Open-Meteo timed out'))
    const floodRequest = vi.spyOn(api, 'getFloodEvidence').mockResolvedValue(floodEvidence())
    const ensembleRequest = vi.spyOn(api, 'getWeatherEnsemble').mockResolvedValue(ensembleEvidence())
    const validationRequest = vi.spyOn(api, 'getForecastValidation').mockResolvedValue(validationEvidence())
    vi.spyOn(api, 'getForecastHeatAssessment').mockResolvedValue(heatAssessment())

    render(<GlobalEvidencePanel location={location} />)
    expect(await screen.findByText('Estimated river flow (computer model)')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Update data' }))

    expect(await screen.findByText(/Refresh failed\. Showing the last successful response/)).toBeInTheDocument()
    expect(screen.getAllByText('24 °C').length).toBeGreaterThan(0)
    expect(weatherRequest).toHaveBeenNthCalledWith(1, location, false)
    expect(weatherRequest).toHaveBeenNthCalledWith(2, location, true)
    expect(floodRequest).toHaveBeenLastCalledWith(location, true)
    expect(ensembleRequest).toHaveBeenLastCalledWith(location, true)
    expect(validationRequest).toHaveBeenLastCalledWith(location, true)
  })
})