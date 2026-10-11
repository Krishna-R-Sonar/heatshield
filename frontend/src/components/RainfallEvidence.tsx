import { useEffect, useState } from 'react'
import { ApiError, getRainfallEvidence, getWeatherForecast } from '../api/client'
import type { MeasurementProvenance, ProviderObservation, WeatherLocation } from '../types'

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; status: 'UNAVAILABLE' | 'ERROR'; message: string }
  | { kind: 'loaded'; result: ProviderObservation }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isMeasurement(value: unknown): value is MeasurementProvenance {
  if (!isRecord(value)) return false
  return (
    typeof value.source === 'string' &&
    typeof value.source_url === 'string' &&
    typeof value.location_id === 'string' &&
    (typeof value.latitude === 'number' || value.latitude === null) &&
    (typeof value.longitude === 'number' || value.longitude === null) &&
    ['OBSERVATION', 'FORECAST', 'DEMO', 'SIMULATION', 'CALCULATED'].includes(String(value.data_type)) &&
    typeof value.retrieval_time === 'string' &&
    typeof value.unit === 'string' &&
    typeof value.quality === 'string' &&
    typeof value.status === 'string' &&
    (typeof value.value === 'number' || value.value === null)
  )
}

function isRainfallEvidence(value: unknown): value is ProviderObservation {
  if (!isRecord(value) || !isRecord(value.provenance) || !isRecord(value.values)) return false
  const provenance = value.provenance
  const intensity = value.values.rainfallIntensityMmHr
  const cumulative = value.values.cumulativeRainfallMm24h
  return (
    isMeasurement({ ...provenance, value: 0, unit: 'mm', status: provenance.status }) &&
    isRecord(intensity) &&
    isMeasurement(intensity.provenance) &&
    isRecord(cumulative) &&
    isMeasurement(cumulative.provenance) &&
    ['available', 'partial', 'unavailable'].includes(String(value.status))
  )
}

function formatTime(timestamp: string | undefined): string {
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return 'Time unavailable'
  return new Date(timestamp).toLocaleString()
}

function displayValue(measurement: MeasurementProvenance): string {
  if (typeof measurement.value !== 'number' || !Number.isFinite(measurement.value)) {
    return 'Unavailable'
  }
  return `${measurement.value} ${measurement.unit}`
}

type Props =
  | { locationId: string; location?: never; showRiskNote?: boolean }
  | { location: WeatherLocation; locationId?: never; showRiskNote?: boolean }

export function RainfallEvidence(props: Props) {
  const key = props.location?.id ?? props.locationId
  return <RainfallEvidenceInner key={key} {...props} />
}

function RainfallEvidenceInner(props: Props) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' })
  const now = useState(() => new Date())[0]
  const locationId = props.locationId
  const location = props.location

  useEffect(() => {
    let active = true
    const request = location
      ? getWeatherForecast(location)
      : locationId
        ? getRainfallEvidence(locationId)
        : Promise.reject(new Error('No forecast location was supplied.'))
    request
      .then((result) => {
        if (!active) return
        if (!isRainfallEvidence(result)) {
          setState({
            kind: 'error',
            status: 'ERROR',
            message: 'The rainfall provider returned a malformed response.',
          })
          return
        }
        setState({ kind: 'loaded', result })
      })
      .catch((error: unknown) => {
        if (!active) return
        const unavailable = error instanceof ApiError && error.status === 503
        setState({
          kind: 'error',
          status: unavailable ? 'UNAVAILABLE' : 'ERROR',
          message: error instanceof Error
            ? error.message
            : 'Rainfall evidence could not be loaded.',
        })
      })
    return () => {
      active = false
    }
  }, [location, locationId])

  if (state.kind === 'loading') {
    return (
      <section className="rainfall-evidence" aria-label="Rainfall forecast evidence" aria-busy="true">
        <h3>Rainfall evidence</h3>
        <p className="muted small" role="status">Loading rainfall data…</p>
      </section>
    )
  }

  if (state.kind === 'error') {
    return (
      <section className="rainfall-evidence" aria-label="Rainfall forecast evidence">
        <div className="evidence-heading">
          <h3>Rainfall evidence</h3>
          <span className="evidence-status">{state.status}</span>
        </div>
        <p className="muted small">{state.message}</p>
        <p className="muted small">No rainfall value is available. Risk assessment remains separate.</p>
      </section>
    )
  }

  const { result } = state
  const intensity = result.values.rainfallIntensityMmHr.provenance!
  const cumulative = result.values.cumulativeRainfallMm24h.provenance!
  const isForecast = result.provenance?.data_type === 'FORECAST'
  const isDemo = result.provenance?.data_type === 'DEMO'
  const isSimulation = result.provenance?.data_type === 'SIMULATION'
  const periodEnd = intensity.forecast_time ? Date.parse(intensity.forecast_time) + 60 * 60 * 1000 : NaN
  const stale = isForecast && Number.isFinite(periodEnd) && periodEnd <= now.getTime()
  const status = stale ? 'STALE' : result.status === 'unavailable' ? 'UNAVAILABLE' : 'AVAILABLE'
  const label = isForecast
    ? 'FORECAST DATA'
    : isSimulation
      ? 'SIMULATION'
      : isDemo
        ? 'DEMO INPUT'
        : result.provenance?.data_type ?? 'ENVIRONMENTAL DATA'

  return (
    <section className="rainfall-evidence" aria-label="Rainfall forecast evidence">
      <div className="evidence-heading">
        <h3>Rainfall evidence</h3>
        <span className="evidence-status">{status}</span>
      </div>
      <p className="evidence-type">{label}</p>
      {stale ? (
        <p className="muted small">This forecast period has passed. Refresh the location for current forecast evidence.</p>
      ) : (
        <dl className="evidence-values">
          <div>
            <dt>Next hour</dt>
            <dd>{displayValue(intensity)}</dd>
          </div>
          <div>
            <dt>Next 24 hours</dt>
            <dd>{displayValue(cumulative)}</dd>
          </div>
        </dl>
      )}
      <p className="evidence-meta">
        Source: <a href={result.provenance?.source_url} target="_blank" rel="noreferrer">
          {result.provenance?.source}
        </a>
      </p>
      <p className="evidence-meta">Forecast time: {formatTime(intensity.forecast_time ?? undefined)}</p>
      <p className="evidence-meta">Retrieved: {formatTime(result.provenance?.retrieval_time)}</p>
      <p className="evidence-meta">Quality: {result.provenance?.quality}</p>
      {result.status === 'partial' && !stale && (
        <p className="muted small">Some rainfall values are missing; unavailable values are not treated as zero.</p>
      )}
      {props.showRiskNote !== false && (
        <p className="muted small">Forecast evidence — not currently used in risk calculation.</p>
      )}
    </section>
  )
}
