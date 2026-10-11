import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import {
  getForecastHeatAssessment,
  getFloodEvidence,
  getForecastValidation,
  getWeatherEnsemble,
  getWeatherEvidence,
} from '../api/client'
import type {
  EnsembleForecastEvidence,
  ForecastValidationEvidence,
  FloodEvidence,
  HistoricalAnomalyPoint,
  RealHeatAssessment,
  WeatherEvidence,
  WeatherLocation,
} from '../types'
import {
  displayNumber,
  formatLocalDate,
  formatLocalDateTime,
  historicalComparisonText,
  plainStatus,
  uncertaintyText,
} from '../utils/environmentPresentation'
import { GlobalEvidenceCopilot } from './GlobalEvidenceCopilot'

type ProviderState<T> =
  | { kind: 'loading'; data?: T }
  | { kind: 'error'; message: string; data?: T }
  | { kind: 'loaded'; data: T }

function loadProvider<T>(
  setState: Dispatch<SetStateAction<ProviderState<T>>>,
  request: () => Promise<T>,
  fallbackMessage: string,
  isActive: () => boolean,
): Promise<void> {
  setState((previous) => ({ kind: 'loading', data: previous.data }))
  return request().then(
    (data) => {
      if (isActive()) setState({ kind: 'loaded', data })
    },
    (error: unknown) => {
      if (isActive()) {
        setState((previous) => ({
          kind: 'error',
          message: error instanceof Error ? error.message : fallbackMessage,
          ...(previous.data ? { data: previous.data } : {}),
        }))
      }
    },
  )
}

function evidenceData<T>(state: ProviderState<T>): T | null {
  return state.data ?? null
}

function freshnessLabel(retrievedAt: string | null | undefined, maxAgeMs: number): string {
  if (!retrievedAt || !Number.isFinite(Date.parse(retrievedAt))) return 'Update time unavailable'
  return plainStatus(Date.now() - Date.parse(retrievedAt) > maxAgeMs ? 'STALE' : 'FRESH')
}

function weatherStatus(
  state: ProviderState<WeatherEvidence>,
  data: WeatherEvidence | null,
): string {
  if (!data) return state.kind === 'loading' ? 'LOADING' : 'UNAVAILABLE'
  const retrievedAt = Date.parse(data.provenance.retrieved_at)
  const stale = state.kind === 'error'
    || (Number.isFinite(retrievedAt) && Date.now() - retrievedAt > 10 * 60 * 1000)
  if (stale && data.status === 'DEGRADED') return 'STALE_DEGRADED'
  if (stale) return 'STALE'
  return data.status
}

function weatherStatusLabel(state: ProviderState<WeatherEvidence>, data: WeatherEvidence | null): string {
  const status = weatherStatus(state, data)
  if (status === 'AVAILABLE') return 'Current'
  if (status === 'LOADING') return 'Loading'
  if (status === 'STALE_DEGRADED') return 'Out of date · some details missing'
  return plainStatus(status)
}

function inputLabel(input: string): string {
  const labels: Record<string, string> = {
    heat_index_c: 'a heat-index estimate',
    elevated_temp_duration_hours: 'how long elevated temperatures last',
    population_exposure_index: 'local population exposure',
    infrastructure_exposure_index: 'local infrastructure exposure',
  }
  return labels[input] ?? input.replaceAll('_', ' ')
}

function displayValue(value: number | null | undefined, unit = '', precision = 1): string {
  return displayNumber(value, unit, precision)
}

function formatTime(value: string | null | undefined, timezone?: string | null): string {
  return value ? formatLocalDateTime(value, timezone) : 'Not provided'
}

function isAvailableForecast(
  measurement: WeatherEvidence['forecast'][string] | undefined,
  unit: string,
): measurement is NonNullable<typeof measurement> & { value: number } {
  return Boolean(
    measurement
    && measurement.status === 'AVAILABLE'
    && measurement.data_type === 'FORECAST'
    && measurement.unit === unit
    && typeof measurement.value === 'number'
    && Number.isFinite(measurement.value),
  )
}

function formatForecastDate(value: string): string {
  return formatLocalDate(value)
}

function wmoDescription(value: number | null | undefined): string {
  const descriptions: Record<number, string> = {
    0: 'Clear', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Rime fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
    61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 80: 'Rain showers', 81: 'Showers',
    82: 'Heavy showers', 95: 'Thunderstorm', 96: 'Thunderstorm with hail', 99: 'Severe thunderstorm',
  }
  return typeof value === 'number' ? descriptions[value] ?? 'Condition description unavailable' : 'Unavailable'
}

function Metric({
  label,
  value,
  unit = '',
  precision = 1,
}: {
  label: string
  value: number | null | undefined
  unit?: string
  precision?: number
}) {
  return (
    <div className="evidence-metric">
      <dt>{label}</dt>
      <dd>{displayValue(value, unit, precision)}</dd>
    </div>
  )
}

function spreadRange(lower: number | null, upper: number | null, unit: string): string {
  if (lower === null || upper === null || !Number.isFinite(lower) || !Number.isFinite(upper)) {
    return 'Unavailable'
  }
  const lowerRounded = Number(lower.toFixed(2))
  const upperRounded = Number(upper.toFixed(2))
  const range = `${displayValue(lower, unit, 2)}–${displayValue(upper, unit, 2)}`
  return lowerRounded === upperRounded
    ? `No difference at displayed precision (${range})`
    : range
}

function SpreadRangeMetric({
  label,
  lower,
  upper,
  unit,
}: {
  label: string
  lower: number | null
  upper: number | null
  unit: string
}) {
  return (
    <div className="evidence-metric">
      <dt>{label}</dt>
      <dd>{spreadRange(lower, upper, unit)}</dd>
    </div>
  )
}

type HourlyForecastPoint = { value: number | null; time: string }

function hourlyForecastWindow(
  times: string[],
  values: Array<number | null> | undefined,
  forecastTime: string | null | undefined,
  intervalMinutes: number | null | undefined,
): { points: HourlyForecastPoint[]; period: string } {
  const forecastTimestamp = Date.parse(forecastTime ?? '')
  const anchored = Number.isFinite(forecastTimestamp)
  const startIndex = anchored
    ? times.findIndex((time) => {
      const timestamp = Date.parse(time)
      return Number.isFinite(timestamp) && timestamp >= forecastTimestamp
    })
    : 0
  if (startIndex < 0) return { points: [], period: anchored ? 'next 24 hours' : 'available hourly forecast' }

  const stepMinutes = intervalMinutes && intervalMinutes > 0 ? intervalMinutes : 60
  const stepCount = Math.max(1, Math.floor((24 * 60) / stepMinutes))
  const points = times.slice(startIndex, startIndex + stepCount).map((time, offset) => {
    const index = startIndex + offset
    const value = values?.[index]
    return {
      value: typeof value === 'number' && Number.isFinite(value) ? value : null,
      time,
    }
  })
  const cadence = stepMinutes % 60 === 0
    ? `${stepMinutes / 60}-hour steps`
    : `${stepMinutes}-minute steps`
  const period = anchored
    ? `${points.length} available points · ${cadence} · up to 24 hours`
    : `available forecast points · ${cadence}`
  return { points, period }
}

function ForecastLineChart({
  points,
  period,
  label,
  unit,
  color,
  timezone,
}: {
  points: HourlyForecastPoint[]
  period: string
  label: string
  unit: string
  color: string
  timezone?: string | null
}) {
  const available = points.filter((point) => point.value !== null)
  if (!available.length) {
    return <p className="muted small">{label} chart unavailable.</p>
  }
  const minimum = Math.min(...available.map((point) => point.value!))
  const maximum = Math.max(...available.map((point) => point.value!))
  const range = maximum - minimum || 1
  const segments: string[][] = []
  points.forEach((point, index) => {
    if (point.value === null) {
      if (segments.at(-1)?.length) segments.push([])
      return
    }
    if (!segments.length) segments.push([])
    const segment = segments.at(-1)!
    segment.push(`${(index / Math.max(1, points.length - 1)) * 600},${112 - ((point.value - minimum) / range) * 96}`)
  })
  const plotted = segments.filter((segment) => segment.length > 0)
  const firstTime = formatTime(points[0]?.time, timezone)
  const lastTime = formatTime(points.at(-1)?.time, timezone)
  return (
    <figure className="forecast-line-chart">
      <figcaption>{label} forecast · {period} ({unit})</figcaption>
      <div className="forecast-chart-scale" aria-hidden="true">
        <span>{displayValue(maximum, unit)}</span>
        <span>{displayValue(minimum, unit)}</span>
      </div>
      <svg viewBox="0 0 600 130" role="img" aria-label={`${label} forecast, ${period}, from ${firstTime} to ${lastTime}, ranging from ${displayValue(minimum, unit)} to ${displayValue(maximum, unit)}`} preserveAspectRatio="none">
        <line x1="0" y1="20" x2="600" y2="20" stroke="#cedbdc" strokeWidth="1" />
        <line x1="0" y1="66" x2="600" y2="66" stroke="#e2e9e5" strokeWidth="1" />
        <line x1="0" y1="112" x2="600" y2="112" stroke="#cedbdc" strokeWidth="1" />
        {plotted.map((segment, index) => {
          if (segment.length > 1) {
            return <polyline key={index} points={segment.join(' ')} fill="none" stroke={color} strokeWidth="3" vectorEffect="non-scaling-stroke" />
          }
          const [cx, cy] = segment[0].split(',')
          return <circle key={index} cx={cx} cy={cy} r="4" fill={color} />
        })}
      </svg>
      <div className="forecast-chart-labels">
        <span>{firstTime}</span>
        <span>{lastTime}</span>
      </div>
      <p className="chart-description">{label} changes by hour; this forecast is not a ground measurement.</p>
    </figure>
  )
}

function DailyPrecipitationChart({ evidence }: { evidence: WeatherEvidence }) {
  const values = evidence.daily.values.precipitation_sum ?? []
  const dates = evidence.daily.times.slice(0, 7)
  const available = values.slice(0, 7).filter((value): value is number => typeof value === 'number')
  if (!available.length) return <p className="muted small">Seven-day precipitation chart unavailable.</p>
  const maximum = Math.max(...available, 1)
  return (
    <figure className="daily-bar-chart">
      <figcaption>Total precipitation forecast · next {dates.length} local days</figcaption>
      <p className="chart-description">Daily amounts may include snow or other precipitation, not just rain.</p>
      <ol className="daily-bars" aria-label="Daily total precipitation by local date">
        {dates.map((date, index) => {
          const value = values[index]
          const height = typeof value === 'number' ? Math.max(3, (value / maximum) * 72) : 0
          return (
            <li className="daily-bar-column" key={date} aria-label={`${formatForecastDate(date)}: ${displayValue(value, 'mm')}`}>
              <strong>{displayValue(value, 'mm')}</strong>
              <div className="daily-bar-track"><span style={{ height: `${height}px` }} /></div>
              <time dateTime={date}>{new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })}</time>
            </li>
          )
        })}
      </ol>
    </figure>
  )
}

function Provenance({ title, children }: { title: string; children: React.ReactNode }) {
  return <details className="evidence-provenance"><summary>{title}</summary>{children}</details>
}

function HistoricalSentence({ point, baselinePeriod }: {
  point: HistoricalAnomalyPoint
  baselinePeriod: string
}) {
  const text = historicalComparisonText(point, baselinePeriod)
  if (!text) return <p className="muted small">A comparable historical value is not available for this forecast date.</p>
  const directionsDiffer = (
    point.anomaly !== null
    && point.percentile !== null
    && ((point.anomaly < 0 && point.percentile > 50) || (point.anomaly > 0 && point.percentile < 50))
  )
  return (
    <>
      <p className="anomaly-sentence">{text}</p>
      {point.forecast_source && (
        <p className="muted small">Forecast compared: {point.forecast_source} · historical baseline: ERA5 reanalysis.</p>
      )}
      {directionsDiffer && (
        <p className="muted small">
          The average and historical ranking can differ because they describe different comparisons:
          one is the monthly average; the other compares this value with each day in the historical record.
        </p>
      )}
    </>
  )
}

function ProviderLoading({ label }: { label: string }) {
  return <p className="muted small" role="status">Loading {label}…</p>
}

export function GlobalEvidencePanel({ location }: { location: WeatherLocation }) {
  const [weather, setWeather] = useState<ProviderState<WeatherEvidence>>({ kind: 'loading' })
  const [flood, setFlood] = useState<ProviderState<FloodEvidence>>({ kind: 'loading' })
  const [ensemble, setEnsemble] = useState<ProviderState<EnsembleForecastEvidence>>({ kind: 'loading' })
  const [validation, setValidation] = useState<ProviderState<ForecastValidationEvidence>>({ kind: 'loading' })
  const [heatAssessment, setHeatAssessment] = useState<ProviderState<RealHeatAssessment>>({ kind: 'loading' })
  const [refreshing, setRefreshing] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const isMapPoint = location.selectionType === 'map_point'

  useEffect(() => {
    let active = true
    const isActive = () => active
    const forceRefresh = refreshVersion > 0
    const weatherRequest = loadProvider(
      setWeather,
      () => getWeatherEvidence(location, forceRefresh),
      'Weather evidence unavailable.',
      isActive,
    )
    const requests = [
      weatherRequest.then(() => loadProvider(
        setHeatAssessment,
        () => getForecastHeatAssessment(location),
        'Forecast heat assessment unavailable.',
        isActive,
      )),
      loadProvider(setFlood, () => getFloodEvidence(location, forceRefresh), 'GloFAS evidence unavailable.', isActive),
      loadProvider(setEnsemble, () => getWeatherEnsemble(location, forceRefresh), 'Ensemble evidence unavailable.', isActive),
      loadProvider(
        setValidation,
        () => getForecastValidation(location, forceRefresh),
        'Historical forecast performance unavailable.',
        isActive,
      ),
    ]
    void Promise.all(requests).then(() => {
      if (active) setRefreshing(false)
    })
    return () => { active = false }
  }, [location, refreshVersion])

  const weatherData = evidenceData(weather)
  const floodData = evidenceData(flood)
  const ensembleData = evidenceData(ensemble)
  const validationData = evidenceData(validation)
  const heatAssessmentData = evidenceData(heatAssessment)
  const currentTemperature = weatherData?.forecast.temperature
  const wetBulb = weatherData?.forecast.wet_bulb_temperature
  const nextFloodDay = floodData
    ? floodData.daily.find((point) => point.data_type === 'FORECAST' && point.discharge.status === 'AVAILABLE')
    : undefined
  const nextEnsembleDay = ensembleData?.daily[0]
  const hourlyWindow = weatherData
    ? hourlyForecastWindow(
      weatherData.hourly.times,
      weatherData.hourly.values.temperature_2m,
      currentTemperature?.forecast_time,
      weatherData.hourly.interval_minutes,
    )
    : null
  const hourlyPeak = hourlyWindow
    ? hourlyWindow.points.reduce<{ value: number; time: string } | null>(
      (peak, point) => typeof point.value === 'number' && (!peak || point.value > peak.value)
        ? { value: point.value, time: point.time }
        : peak,
      null,
    )
    : null
  const dailyTemperatures = weatherData?.daily.values.temperature_2m_max ?? []
  const dailyPeak = dailyTemperatures.reduce<{ value: number; date: string } | null>(
    (peak, value, index) => typeof value === 'number' && (!peak || value > peak.value)
      ? { value, date: weatherData?.daily.times[index] ?? '' }
      : peak,
    null,
  )
  const rainForecast = (weatherData?.daily.times ?? []).slice(0, 7).map((date, index) => {
    const precipitation = weatherData?.daily.values.precipitation_sum?.[index]
    const rain = weatherData?.daily.values.rain_sum?.[index]
    return {
      date,
      precipitation: weatherData?.daily.units.precipitation_sum === 'mm'
        && typeof precipitation === 'number'
        && Number.isFinite(precipitation)
        && precipitation >= 0
        ? precipitation
        : null,
      rain: weatherData?.daily.units.rain_sum === 'mm'
        && typeof rain === 'number'
        && Number.isFinite(rain)
        && rain >= 0
        ? rain
        : null,
    }
  })
  const availableRainForecast = rainForecast.filter(
    (day): day is typeof day & { precipitation: number } => day.precipitation !== null,
  )
  const hasCompleteRainForecast = rainForecast.length > 0
    && availableRainForecast.length === rainForecast.length
  const peakRainDay = availableRainForecast.reduce<{ date: string; precipitation: number; rain: number | null } | null>(
    (peak, day) => !peak || day.precipitation > peak.precipitation
      ? day
      : peak,
    null,
  )
  const climateDate = weatherData?.daily.times[0]
  const climateAnomalies = weatherData?.historical_anomalies?.anomalies.filter(
    (point) => point.date === climateDate,
  ) ?? []
  const standoutHistorical = climateAnomalies
    .filter((point) =>
      point.status === 'AVAILABLE'
      && point.percentile !== null
      && Math.abs(point.percentile - 50) >= 25,
    )
    .sort((first, second) =>
      Math.abs((second.percentile ?? 50) - 50) - Math.abs((first.percentile ?? 50) - 50),
    )[0]
  const standoutHistoricalText = standoutHistorical
    ? historicalComparisonText(
      standoutHistorical,
      weatherData?.historical_anomalies?.baseline_period ?? '1991–2020',
    )
    : null
  const additionalClimateComparisons = climateAnomalies.filter((point) => point !== standoutHistorical)
  const locationTimezone = weatherData?.location.timezone
  const weatherProviders = weatherData?.provider_status ?? []
  const primaryProviderStatus = weatherProviders[0]
  const secondaryProviderStatus = weatherProviders[1]
  const secondaryForecastFields = Object.entries(weatherData?.forecast ?? {})
    .filter(([, measurement]) =>
      measurement.status === 'AVAILABLE'
      && measurement.provenance.source
      && measurement.provenance.source !== weatherData?.provenance.source,
    )
    .map(([field, measurement]) => ({
      field,
      provider: String(measurement.provenance.source),
      period: 'forecast',
    }))
  const secondarySeriesFields = (['hourly', 'daily'] as const).flatMap((seriesName) =>
    Object.entries(weatherData?.[seriesName].provenance ?? {}).flatMap(([field, sources]) =>
      sources
        .filter((source) =>
          typeof source.provider === 'string'
          && source.provider !== weatherData?.provenance.source
          && Array.isArray(source.contributed_times)
          && source.contributed_times.length > 0,
        )
        .map((source) => ({
          field,
          provider: String(source.provider),
          period: seriesName,
        })),
    ),
  )
  const secondarySourceFields = [...secondaryForecastFields, ...secondarySeriesFields]
  const fallbackUsed = Boolean(
    secondarySourceFields.length > 0
    || (
      primaryProviderStatus?.status === 'UNAVAILABLE'
      && weatherData?.provenance.source === weatherProviders[1]?.provider
      && (weatherProviders[1]?.fields.length ?? 0) > 0
    ),
  )

  return (
    <div className="global-evidence-panel">
      <section className="evidence-section location-status-section" aria-label="Forecast update status">
        <div className="evidence-section-heading">
          <h3>Location and update</h3>
          <div className="evidence-controls">
            <span className={`provider-pill provider-${weatherStatus(weather, weatherData).toLowerCase()}`}>
              {weatherStatusLabel(weather, weatherData)}
            </span>
            <button
              className="evidence-refresh-button"
              type="button"
              onClick={() => {
                setRefreshing(true)
                setRefreshVersion((version) => version + 1)
              }}
              disabled={refreshing}
            >
              {refreshing ? 'Updating…' : 'Update data'}
            </button>
          </div>
        </div>
        <p className="location-update-line">
          {currentTemperature?.forecast_time
            ? <>Forecast applies {formatTime(currentTemperature.forecast_time, locationTimezone)}.</>
            : 'Forecast time is not available.'}
          {weatherData?.provenance.retrieved_at && (
            <> Last retrieved {formatTime(weatherData.provenance.retrieved_at, locationTimezone)}.</>
          )}
        </p>
        {['DEGRADED', 'STALE_DEGRADED'].includes(weatherStatus(weather, weatherData)) && (
          <p className="status-explanation">Some forecast details are missing. HeatShield does not fill gaps with estimates.</p>
        )}
        {['STALE', 'STALE_DEGRADED'].includes(weatherStatus(weather, weatherData)) && (
          <p className="status-explanation status-caution">This information may be out of date. Update it before relying on the forecast.</p>
        )}
        {weatherStatus(weather, weatherData) === 'UNAVAILABLE' && (
          <p className="status-explanation status-caution">The weather source did not provide a usable forecast for this location.</p>
        )}
        {weatherData && (
          <Provenance title="Coordinates and forecast area">
            <p>Requested coordinates: {weatherData.location.requested_latitude}, {weatherData.location.requested_longitude}</p>
            <p>Approximate forecast area: {weatherData.location.grid_latitude ?? 'Not available'}, {weatherData.location.grid_longitude ?? 'Not available'}</p>
            <p>Elevation: {displayValue(weatherData.location.elevation_m, 'm')} · Time zone: {weatherData.location.timezone ?? 'Not available'}</p>
          </Provenance>
        )}
      </section>

      <section className="plain-language-summary" aria-label="What to know first">
        <p className="summary-kicker">FORECAST · NOT AN OFFICIAL WARNING</p>
        <h3>What to know first</h3>
        {weather.kind === 'loading' && <ProviderLoading label="plain-language weather summary" />}
        {weather.kind === 'error' && !weatherData && (
          <p className="provider-error" role="status">A forecast is not available, so HeatShield cannot summarize conditions here.</p>
        )}
        {weather.kind === 'error' && weatherData && (
          <p className="stale-evidence" role="status">The update failed. This page is still showing the last successful forecast, which may be out of date.</p>
        )}
        {weatherData && (
          <>
            {weatherData.status === 'UNAVAILABLE' ? (
              <p className="provider-error" role="status">
                No configured weather provider returned usable forecast evidence for this location.
                Missing information has not been replaced with demo data.
              </p>
            ) : (
              <p className="summary-period">
                Main forecast source: {String(currentTemperature?.provenance.source ?? weatherData.provenance.source)}
                {fallbackUsed ? ' · Secondary-provider fallback used' : ''}
                {!fallbackUsed && secondaryProviderStatus
                  && ['AVAILABLE', 'DEGRADED'].includes(secondaryProviderStatus.status)
                  ? ` · ${secondaryProviderStatus.provider} also checked`
                  : ''}
                {secondarySourceFields.length > 0
                  ? ` · ${secondarySourceFields.length} field(s) use a secondary source`
                  : ''}
              </p>
            )}
            {isAvailableForecast(currentTemperature, '°C') ? (
              <div className="summary-finding summary-lead">
                <h4>Next forecast</h4>
                <p>
                  <strong>{displayValue(currentTemperature.value, '°C')}</strong>
                  {isAvailableForecast(weatherData.forecast.apparent_temperature, '°C')
                    ? <> · feels like {displayValue(weatherData.forecast.apparent_temperature.value, '°C')}</>
                    : null}
                </p>
                <p className="summary-period">{formatTime(currentTemperature.forecast_time, locationTimezone)}</p>
              </div>
            ) : (
              <p className="summary-lead">A temperature forecast is not available for this location.</p>
            )}
            <div className="summary-finding-grid">
              {dailyPeak && weatherData.daily.units.temperature_2m_max === '°C' && (
                <div className="summary-finding">
                  <h4>Warmest forecast day</h4>
                  <p><strong>{displayValue(dailyPeak.value, '°C')}</strong>{dailyPeak.date ? ` · ${formatForecastDate(dailyPeak.date)}` : ''}</p>
                </div>
              )}
              {peakRainDay && (
                <div className="summary-finding">
                  <h4>Wettest forecast day</h4>
                  <p>
                    <strong>{displayValue(peakRainDay.precipitation, 'mm')}</strong>
                    {' total precipitation · '}{formatForecastDate(peakRainDay.date)}
                  </p>
                  {peakRainDay.rain !== null && peakRainDay.rain !== peakRainDay.precipitation && (
                    <p className="summary-period">Rain alone: {displayValue(peakRainDay.rain, 'mm')} that day.</p>
                  )}
                </div>
              )}
            </div>
            {standoutHistoricalText && (
              <p className="summary-historical">
                <strong>Compared with history:</strong> {standoutHistoricalText}
              </p>
            )}
            {!hasCompleteRainForecast && (
              <p className="summary-data-gap">
                {availableRainForecast.length > 0
                  ? `Daily precipitation is available for ${availableRainForecast.length} of ${rainForecast.length} forecast days. Missing days are unknown, not zero.`
                  : 'Daily precipitation totals are not available in this forecast.'}
              </p>
            )}
          </>
        )}
        {heatAssessmentData?.status === 'INCOMPLETE' && (
          <p className="summary-data-gap">
            <strong>Heat assessment:</strong> We can show the weather forecast, but we do not yet have enough information to assess local heat risk.
          </p>
        )}
        {heatAssessmentData?.status === 'UNAVAILABLE' && (
          <p className="summary-data-gap">
            <strong>Heat assessment:</strong> Not enough information is available to assess local heat risk.
          </p>
        )}
        {isMapPoint && (
          <p className="summary-data-gap">
            This forecast describes the selected coordinates and nearby forecast area, not a verified settlement or its population.
          </p>
        )}
        <div className="summary-next-step">
          <h4>What to check next</h4>
          {peakRainDay && peakRainDay.precipitation > 0 ? (
            <p>For plans on {formatForecastDate(peakRainDay.date)}, check the hour-by-hour forecast closer to the time. Daily totals do not show when rain may fall.</p>
          ) : (
            <p>No specific action is supported by this forecast alone.</p>
          )}
          <p>For local flood conditions, use verified river gauges and official local advisories. HeatShield does not provide a flood warning.</p>
        </div>
        <p className="summary-flood-note">
          <strong>Flood information gap:</strong>{' '}
          {floodData
            ? 'The river-flow figure, when available, is a computer-model estimate and may not represent a river at this location. No local river level, drainage condition, or flood threshold is verified here.'
            : 'Local river levels, drainage conditions, and flood thresholds are not available here.'}
        </p>
        <p className="summary-disclaimer">
          HeatShield shows environmental evidence for decision support. It does not calculate a real-world heat or flood risk score or issue official warnings.
        </p>
      </section>

      <GlobalEvidenceCopilot
        key={location.id}
        location={location}
        refreshVersion={refreshVersion}
      />

      <details className="evidence-source-details">
        <summary>Explore detailed weather, flood, and historical evidence</summary>
        <div className="evidence-source-details-content">
      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Weather outlook</h3>
          {weatherData && <span className={`provider-pill provider-${weatherData.status.toLowerCase()}`}>{plainStatus(weatherData.provenance.data_type)}</span>}
        </div>
        {weatherData?.status === 'DEGRADED' && (
          <p className="muted small">Some requested forecast details are unavailable. Missing values are not replaced with estimates.</p>
        )}
        {weather.kind === 'loading' && <ProviderLoading label="weather forecast" />}
        {weather.kind === 'error' && <p className="provider-error" role="status">Weather source unavailable · {weather.message}</p>}
        {weather.kind === 'error' && weather.data && (
          <p className="stale-evidence" role="status">Refresh failed. Showing the last successful response; its freshness is based on the original retrieval time.</p>
        )}
        {weatherData && (
          <>
            <p className="evidence-meta">
              <a href={weatherData.provenance.source_url} target="_blank" rel="noreferrer">{weatherData.provenance.source}</a>
              {' · Forecast time: '}{formatTime(currentTemperature?.forecast_time, locationTimezone)}
              {' · Retrieved: '}{formatTime(weatherData.provenance.retrieved_at, locationTimezone)}
              {' · Update: '}{freshnessLabel(weatherData.provenance.retrieved_at, 10 * 60 * 1000)}
            </p>
            <p className="evidence-meta">
              Requested coordinates: {weatherData.location.requested_latitude}, {weatherData.location.requested_longitude}
              {' · Approximate forecast area: '}{weatherData.location.grid_latitude ?? 'Not available'}, {weatherData.location.grid_longitude ?? 'Not available'}
            </p>
            <p className="evidence-meta">
              Main forecast field source: {String(currentTemperature?.provenance.source ?? weatherData.provenance.source)}
              {' · Retrieved: '}{formatTime(
                typeof currentTemperature?.provenance.retrieval_time === 'string'
                  ? currentTemperature.provenance.retrieval_time
                  : weatherData.provenance.retrieved_at,
                locationTimezone,
              )}
              {fallbackUsed ? ' · Primary unavailable; fallback evidence is shown.' : ''}
            </p>
            <dl className="evidence-metrics">
              <Metric label="Temperature" value={currentTemperature?.value} unit={currentTemperature?.unit} />
              <Metric label="Feels like" value={weatherData.forecast.apparent_temperature?.value} unit={weatherData.forecast.apparent_temperature?.unit} />
              <Metric label="Humidity" value={weatherData.forecast.humidity?.value} unit={weatherData.forecast.humidity?.unit} />
              <Metric label="Wet-bulb temperature (heat + moisture measure)" value={wetBulb?.value} unit={wetBulb?.unit} />
              <Metric label="Precipitation in this forecast hour" value={weatherData.forecast.precipitation?.value} unit={weatherData.forecast.precipitation?.unit ?? ''} />
              <Metric label="Forecast chance of precipitation" value={weatherData.forecast.precipitation_probability?.value} unit="%" />
              <Metric label="Wind" value={weatherData.forecast.wind_speed?.value} unit="km/h" />
              <Metric label="Wind gusts" value={weatherData.forecast.wind_gusts?.value} unit="km/h" />
              <Metric label="Visibility" value={weatherData.forecast.visibility?.value} unit="m" />
              <Metric label="Cloud cover" value={weatherData.forecast.cloud_cover?.value} unit="%" />
              <Metric label="UV index (sun exposure)" value={weatherData.forecast.uv_index?.value} />
            </dl>
            <p className="muted small">The hourly precipitation amount applies to that forecast hour only; it is not a daily total or a rain-intensity rate. The chance shown is for precipitation, not flooding.</p>
            <p className="weather-code-label">Weather conditions: {wmoDescription(weatherData.forecast.weather_code?.value)}.</p>
            <ForecastLineChart
              points={hourlyWindow?.points ?? []}
              period={hourlyWindow?.period ?? 'available hourly forecast'}
              label="Temperature"
              unit="°C"
              color="#b84e33"
              timezone={locationTimezone}
            />
            <DailyPrecipitationChart evidence={weatherData} />
            <p className="evidence-meta">Daily total precipitation can include snow or other precipitation; the rain-only total counts rain.</p>
            <Provenance title="Weather provenance">
              <p>Source / API: {weatherData.provenance.source} · {weatherData.provenance.api}</p>
              <p>Model selection: {weatherData.provenance.model_selection}; model ID: {weatherData.provenance.model_id ?? 'Not provided by auto selection'}</p>
              <p>Resolution: {weatherData.provenance.spatial_resolution}</p>
              <p>{weatherData.provenance.quality}</p>
              <ul>{weatherData.provenance.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </Provenance>
            <Provenance title="Provider status and field-level sources">
              {weatherProviders.length > 0 ? (
                <ul>
                  {weatherProviders.map((provider) => (
                    <li key={provider.provider}>
                      <strong>{provider.provider}: {plainStatus(provider.status)}</strong>
                      {provider.retrieved_at
                        ? ` · Retrieved ${formatTime(provider.retrieved_at, locationTimezone)}`
                        : ''}
                      {provider.fields.length > 0
                        ? ` · Available fields: ${provider.fields.map((field) => field.replaceAll('_', ' ')).join(', ')}`
                        : ''}
                      {provider.used_fields?.length
                        ? ` · Used to fill missing fields: ${provider.used_fields.map((field) => field.replaceAll('_', ' ')).join(', ')}`
                        : ''}
                      {provider.reason ? ` · ${provider.reason}` : ''}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>Provider-by-provider status is not available in this response.</p>
              )}
              {secondarySourceFields.length > 0 && (
                <>
                  <p>Fields supplied from a secondary provider:</p>
                  <ul>{secondarySourceFields.map(({ field, provider, period }) => (
                    <li key={`${period}-${field}`}>{field.replaceAll('_', ' ')} ({period}) · {provider}</li>
                  ))}</ul>
                </>
              )}
              {(weatherData.missing_fields ?? []).length > 0 && (
                <p>Unavailable fields: {weatherData.missing_fields?.map((field) => field.replaceAll('_', ' ')).join(', ')}. Missing values are not treated as zero.</p>
              )}
            </Provenance>
            {(weatherData.provider_disagreements ?? []).length > 0 && (
              <div className="summary-data-gap" role="status">
                <strong>Forecast providers materially disagree for compatible values:</strong>
                <ul>
                  {weatherData.provider_disagreements?.map((item) => (
                    <li key={`${item.variable}-${item.valid_time}`}>
                      {item.variable.replaceAll('_', ' ')} at {formatTime(item.valid_time, locationTimezone)}:
                      {' '}{item.first_provider} {displayValue(item.first_value, item.unit)}
                      {' vs '}{item.second_provider} {displayValue(item.second_value, item.unit)}
                      {' · difference '}{displayValue(item.difference, item.unit)}
                      {' (display threshold '}{displayValue(item.materiality_threshold, item.unit)}).
                    </li>
                  ))}
                </ul>
                <p>Values are shown separately, not averaged. Provider agreement or disagreement does not establish which forecast is more accurate or independent.</p>
              </div>
            )}
            <p className="muted small">
              Time series: {weatherData.hourly.aggregation_period ?? 'hourly provider forecast'}.
              {' Daily periods: '}{weatherData.daily.aggregation_period ?? 'provider daily forecast'}.
            </p>
          </>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Can we assess local heat risk?</h3>
          {heatAssessmentData && (
            <span className={`provider-pill provider-${heatAssessmentData.status.toLowerCase()}`}>
              {plainStatus(heatAssessmentData.status)}
            </span>
          )}
        </div>
        {heatAssessment.kind === 'loading' && <ProviderLoading label="score-free heat assessment" />}
        {heatAssessment.kind === 'error' && (
          <p className="provider-error" role="status">Assessment unavailable · {heatAssessment.message}</p>
        )}
        {heatAssessment.kind === 'error' && heatAssessment.data && (
          <p className="stale-evidence" role="status">Showing the last successful assessment response; its evidence may be stale.</p>
        )}
        {heatAssessmentData && (
          <>
            <p className="decision-support-note">
              No numeric risk score is calculated. This is a forecast-based assessment, not a measured observation.
            </p>
            <p className="evidence-meta">
              Forecast time: {formatTime(heatAssessmentData.assessmentWindow.forecastTime, locationTimezone)}
              {' · Retrieved: '}{formatTime(heatAssessmentData.provenance.retrievedAt, locationTimezone)}
              {' · Location match: '}{plainStatus(heatAssessmentData.provenance.locationAssociation)}
            </p>
            {heatAssessmentData.missingInputs.length > 0 && (
              <>
                {heatAssessmentData.status === 'INCOMPLETE' && (
                  <p className="muted small">Not enough information is available for a heat score or risk level.</p>
                )}
                <p>Information still needed:</p>
                <ul>{heatAssessmentData.missingInputs.map((input) => <li key={input}>{inputLabel(input)}</li>)}</ul>
              </>
            )}
            {heatAssessmentData.reasons.length > 0 && (
              <ul aria-label="Heat assessment limitations">
                {heatAssessmentData.reasons.map((reason, index) => (
                  <li key={`${reason.code}-${reason.field}-${index}`}>{reason.message}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Heat evidence</h3>
          <span className="provider-pill provider-forecast">Forecast</span>
        </div>
        {weather.kind === 'loading' && <ProviderLoading label="heat evidence" />}
        {weather.kind === 'error' && <p className="provider-error">Heat evidence unavailable with the weather provider.</p>}
        {weatherData && (
          <>
            <dl className="evidence-metrics">
              <Metric label="Next-hour temperature" value={currentTemperature?.value} unit={currentTemperature?.unit} />
              <Metric label="Feels like" value={weatherData.forecast.apparent_temperature?.value} unit="°C" />
              <Metric label="Humidity" value={weatherData.forecast.humidity?.value} unit="%" />
              <Metric label="Wet-bulb temperature (heat + moisture measure)" value={wetBulb?.value} unit="°C" />
              <Metric label="Warmest forecast high" value={dailyPeak?.value ?? null} unit="°C" />
            </dl>
            <p className="evidence-meta">Highest hourly temperature · {hourlyWindow?.period ?? 'available hourly forecast'}: {hourlyPeak ? `${displayValue(hourlyPeak.value, '°C')} at ${formatTime(hourlyPeak.time, locationTimezone)}` : 'Not available'} · Warmest forecast date: {dailyPeak?.date ? formatForecastDate(dailyPeak.date) : 'Not available'}</p>
            <p className="evidence-meta">Evidence quality: {weatherData.provenance.quality}</p>
            {weatherData.historical_anomalies && (
              <Provenance title="Historical anomaly methodology">
                <p>{weatherData.historical_anomalies.model} reanalysis · {weatherData.historical_anomalies.baseline_period}</p>
                <p>{weatherData.historical_anomalies.methodology}</p>
                <p>Historical source quality: {weatherData.historical_anomalies.quality}</p>
              </Provenance>
            )}
          </>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Compared with historical conditions</h3>
          {weatherData?.historical_anomalies && (
            <span className={`provider-pill provider-${weatherData.historical_anomalies.status.toLowerCase()}`}>
              {plainStatus(weatherData.historical_anomalies.status)}
            </span>
          )}
        </div>
        {!weatherData?.historical_anomalies && <p className="muted small">Historical comparison unavailable.</p>}
        {weatherData?.historical_anomalies?.status === 'DEGRADED' && (
          <p className="muted small">Some daily comparisons are unavailable; the comparisons below use only available values.</p>
        )}
        {weatherData?.historical_anomalies && climateAnomalies.length === 0 && (
          <p className="muted small">No comparable ERA5 baseline values are available for this forecast date.</p>
        )}
        {weatherData?.historical_anomalies && climateAnomalies.length > 0 && additionalClimateComparisons.length === 0 && (
          <p className="muted small">The most notable historical comparison is shown in the overview; there are no additional comparisons to show.</p>
        )}
        {additionalClimateComparisons.map((point) => (
          <HistoricalSentence
            key={point.indicator}
            point={point}
            baselinePeriod={weatherData?.historical_anomalies?.baseline_period ?? '1991-2020'}
          />
        ))}
        {weatherData?.historical_anomalies && (
          <p className="evidence-meta">Historical average: ERA5 reanalysis, {weatherData.historical_anomalies.baseline_period} · approximate grid size: {weatherData.historical_anomalies.spatial_resolution}</p>
        )}
        {weatherData?.historical_anomalies && (
          <p className="muted small">A historical percentile compares the forecast with past values for similar dates. It is not the probability of dangerous heat, flooding, or other harm.</p>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Water / flood evidence</h3>
          {floodData && <span className={`provider-pill provider-${floodData.status.toLowerCase()}`}>{plainStatus(floodData.status)}</span>}
        </div>
        <p className="modeled-discharge-label">Estimated river flow (computer model)</p>
        <p className="muted small">This is not a measured river level. The connection between the modelled river and this location has not been verified.</p>
        {floodData?.status === 'DEGRADED' && (
          <p className="muted small">Some daily modeled flow values are unavailable; remaining values are estimates.</p>
        )}
        {flood.kind === 'loading' && <ProviderLoading label="modeled river-flow evidence" />}
        {flood.kind === 'error' && <p className="provider-error" role="status">River-flow data source unavailable · {flood.message}</p>}
        {flood.kind === 'error' && flood.data && (
          <p className="stale-evidence" role="status">Refresh failed. Showing the last successful response; its freshness is based on the original retrieval time.</p>
        )}
        {floodData && (
          <>
            <p className="evidence-meta">
              <a href={floodData.provenance.source_url} target="_blank" rel="noreferrer">{floodData.provenance.source}</a>
              {' · Retrieved: '}{formatTime(floodData.provenance.retrieved_at, locationTimezone)}
              {' · Update: '}{freshnessLabel(floodData.provenance.retrieved_at, 15 * 60 * 1000)}
            </p>
            <p className="evidence-meta">Next modeled date: {nextFloodDay?.time ? formatForecastDate(nextFloodDay.time) : 'Not available'}</p>
            <dl className="evidence-metrics">
              <Metric label="Estimated river flow" value={nextFloodDay?.discharge.value} unit="m³/s" />
              <Metric label="Middle estimate across model runs" value={nextFloodDay?.median.value} unit="m³/s" />
              <SpreadRangeMetric
                label="Middle half of model estimates (P25–P75)"
                lower={nextFloodDay?.p25.value ?? null}
                upper={nextFloodDay?.p75.value ?? null}
                unit="m³/s"
              />
              <Metric label="Width of middle-half range" value={nextFloodDay?.discharge_spread_iqr} unit="m³/s" precision={2} />
              <Metric label="Forecast horizon" value={floodData.provenance.forecast_horizon_days} unit="days" />
            </dl>
            <p>How model estimates compare: <strong>{uncertaintyText(nextFloodDay?.uncertainty)}</strong></p>
            <p className="muted small">Differences show how much the model runs disagree; they are not a flood probability or safety measure. Matching estimates do not guarantee the forecast is correct.</p>
            <p className="evidence-meta">Historical model period: {floodData.provenance.returned_historical_period ?? 'Not available in this response'}</p>
            <p className="evidence-meta">HeatShield does not assign a local flood category; no location-specific flood threshold is configured.</p>
            <Provenance title="GloFAS provenance and limitations">
              <p>{floodData.provenance.source} · {floodData.provenance.model}</p>
              <p>Requested coordinates: {floodData.location.requested_latitude}, {floodData.location.requested_longitude}</p>
              <p>Approximate forecast area: {floodData.location.grid_latitude ?? 'Not available'}, {floodData.location.grid_longitude ?? 'Not available'} · approximately {floodData.provenance.spatial_resolution_km} km across</p>
              <p>{floodData.provenance.quality}</p>
              <ul>{floodData.provenance.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </Provenance>
          </>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>How much forecasts differ</h3>
          {ensembleData && <span className={`provider-pill provider-${ensembleData.status.toLowerCase()}`}>{plainStatus(ensembleData.status)}</span>}
        </div>
        {ensemble.kind === 'loading' && <ProviderLoading label="ensemble summary" />}
        {ensemble.kind === 'error' && <p className="provider-error" role="status">Forecast comparison source unavailable · {ensemble.message}</p>}
        {ensemble.kind === 'error' && ensemble.data && (
          <p className="stale-evidence" role="status">Refresh failed. Showing the last successful response; its freshness is based on the original retrieval time.</p>
        )}
        {ensembleData && nextEnsembleDay && (
          <>
            {ensembleData.status === 'DEGRADED' && (
              <p className="muted small">Some forecast comparisons are unavailable. Only statistics returned by the source are shown.</p>
            )}
            <p className="evidence-meta">
              <a href={ensembleData.provenance.source_url} target="_blank" rel="noreferrer">{ensembleData.provenance.source}</a>
              {' · Forecast model: '}{ensembleData.provenance.model} · {ensembleData.member_count} model runs · {nextEnsembleDay.date} UTC date (may differ from local date)
              {' · Retrieved: '}{formatTime(ensembleData.provenance.retrieved_at, locationTimezone)}
              {' · Update: '}{freshnessLabel(ensembleData.provenance.retrieved_at, 15 * 60 * 1000)}
            </p>
            <dl className="evidence-metrics">
              <SpreadRangeMetric
                label="Middle half of forecast highs (P25–P75)"
                lower={nextEnsembleDay.temperature_max.p25}
                upper={nextEnsembleDay.temperature_max.p75}
                unit="°C"
              />
              <Metric label="Width of middle-half range" value={nextEnsembleDay.temperature_max.spread_iqr} unit="°C" precision={2} />
              <Metric label="Middle estimate of forecast high" value={nextEnsembleDay.temperature_max.median} unit="°C" precision={1} />
              <SpreadRangeMetric
                label="Middle half of precipitation estimates (P25–P75)"
                lower={nextEnsembleDay.precipitation_sum.p25}
                upper={nextEnsembleDay.precipitation_sum.p75}
                unit="mm"
              />
              <Metric label="Width of middle-half range" value={nextEnsembleDay.precipitation_sum.spread_iqr} unit="mm" precision={1} />
              <Metric label="Middle estimate of daily precipitation" value={nextEnsembleDay.precipitation_sum.median} unit="mm" precision={1} />
            </dl>
            <p>Forecast highs: <strong>{uncertaintyText(nextEnsembleDay.temperature_max.uncertainty)}</strong></p>
            <p>Daily precipitation: <strong>{uncertaintyText(nextEnsembleDay.precipitation_sum.uncertainty)}</strong></p>
            <p className="muted small">Model agreement is not a guarantee that a forecast is correct. Differences between models are not a measure of danger or safety.</p>
            <Provenance title="Ensemble provenance and method">
              <p>{ensembleData.provenance.source} · {ensembleData.provenance.model_id}</p>
              <p>{ensembleData.provenance.classification_method}</p>
              <ul>{ensembleData.provenance.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </Provenance>
          </>
        )}
      </section>

      <section className="evidence-section">
        <div className="evidence-section-heading">
          <h3>Historical forecast performance</h3>
          {validationData && (
            <span className={`provider-pill provider-${validationData.status.toLowerCase()}`}>
              {validationData.status}
            </span>
          )}
        </div>
        {validation.kind === 'loading' && <ProviderLoading label="historical forecast performance" />}
        {validation.kind === 'error' && <p className="provider-error" role="status">Historical forecast-check source unavailable · {validation.message}</p>}
        {validation.kind === 'error' && validation.data && (
          <p className="stale-evidence" role="status">Refresh failed. Showing the last successful response; its freshness is based on the original retrieval time.</p>
        )}
        {validationData && (
          <>
            <p className="evidence-meta">
              Verification period: {validationData.verification_period}
              {' · Retrieved: '}{formatTime(validationData.retrieved_at)}
              {' · Freshness: '}{freshnessLabel(validationData.retrieved_at, 24 * 60 * 60 * 1000)}
            </p>
            <dl className="evidence-metrics">
              {validationData.metrics.map((metric) => (
                <Metric
                  key={`${metric.variable}-${metric.forecast_horizon_hours}`}
                  label={`${metric.variable === 'temperature' ? 'Temperature' : 'Precipitation'} average difference · ${metric.forecast_horizon_hours}h forecasts`}
                  value={metric.mean_absolute_error}
                  unit={metric.unit}
                  precision={1}
                />
              ))}
            </dl>
            <p className="evidence-meta">{validationData.quality}</p>
            <p className="muted small">This is the average difference between past forecasts and historical reanalysis estimates, not a check against local weather stations or disaster predictions. Values under the displayed precision are marked as smaller than that amount.</p>
            <Provenance title="Historical performance methodology">
              <p>{validationData.forecast_source} · model selection: {validationData.forecast_model_selection}</p>
              <p>Reference: {validationData.reference_model} · {validationData.reference_data_type}</p>
              <ul>{validationData.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            </Provenance>
          </>
        )}
      </section>

        </div>
      </details>
    </div>
  )
}