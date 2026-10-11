import type { HistoricalAnomalyPoint } from '../types'

const STATUS_LABELS: Record<string, string> = {
  AVAILABLE: 'Available',
  DEGRADED: 'Some information is missing',
  INCOMPLETE: 'Not enough information to assess this yet',
  UNAVAILABLE: 'Unavailable',
  STALE: 'May be out of date',
  FRESH: 'Recently retrieved',
  FORECAST: 'Forecast',
  REANALYSIS: 'Historical estimate',
  MODEL_EVIDENCE: 'Computer-model estimate',
  DEMO: 'Demo data',
  SIMULATION: 'Simulation',
  CALCULATED: 'Calculated by HeatShield',
  OBSERVATION: 'Observation',
}

const INDICATOR_LABELS: Record<HistoricalAnomalyPoint['indicator'], string> = {
  temperature_max: 'forecast high',
  precipitation: 'total precipitation forecast',
  rainfall: 'rainfall forecast',
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function displayNumber(value: number | null | undefined, unit = '', precision = 1): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Not available'
  const rounded = Number(value.toFixed(precision))
  if (value !== 0 && rounded === 0) {
    const displayLimit = (10 ** -precision).toFixed(precision)
    return `${value < 0 ? '−' : ''}less than ${displayLimit}${unit ? ` ${unit}` : ''}`
  }
  return `${rounded.toLocaleString(undefined, { maximumFractionDigits: precision })}${unit ? ` ${unit}` : ''}`
}

export function plainStatus(status: string | null | undefined): string {
  return status ? STATUS_LABELS[status.toUpperCase()] ?? status : 'Status not available'
}

export function formatLocalDateTime(value: string | null | undefined, timezone?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Time not available'
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      ...(timezone ? { timeZone: timezone, timeZoneName: 'short' } : {}),
    }).format(new Date(value))
  } catch {
    return new Date(value).toLocaleString()
  }
}

export function formatLocalDate(value: string | null | undefined): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return value ?? 'Date not available'
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(year, month - 1, day, 12)
  if (!Number.isFinite(date.getTime())) return value
  return date.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
}

export function historicalComparisonText(
  point: HistoricalAnomalyPoint,
  baselinePeriod: string,
): string | null {
  if (
    point.status !== 'AVAILABLE'
    || point.current_value === null
    || point.anomaly === null
    || point.percentile === null
    || !point.historical_baseline
  ) return null

  const month = MONTHS[Number(point.date.slice(5, 7)) - 1]
  if (!month) return null
  const label = INDICATOR_LABELS[point.indicator]
  const difference = Math.abs(point.anomaly)
  const direction = point.anomaly > 0 ? 'above' : point.anomaly < 0 ? 'below' : 'about the same as'
  const comparison = point.percentile >= 99
    ? 'near the top of'
    : point.percentile >= 75
      ? 'higher than most'
      : point.percentile <= 1
        ? 'near the bottom of'
        : point.percentile <= 25
          ? 'lower than most'
          : 'near the middle of'
  const baseline = displayNumber(point.historical_baseline.mean, point.unit, 2)
  const differenceText = point.anomaly === 0
    ? 'about the same as'
    : `${displayNumber(difference, point.unit)} ${direction} the`

  return `The ${label} for ${formatLocalDate(point.date)} is ${displayNumber(point.current_value, point.unit)}. That is ${differenceText} historical ${month} average (${baseline}) from ${baselinePeriod}; it is ${comparison} comparable ${month} days in that record. This is historical context, not a danger level, heat or flood probability, or warning.`
}

export function uncertaintyText(level: string | null | undefined): string {
  switch (level?.toUpperCase()) {
    case 'LOW':
      return 'Forecasts are fairly close to one another, but they can still be wrong.'
    case 'MODERATE':
      return 'Forecasts differ somewhat, so the estimate is less certain. Agreement does not guarantee accuracy.'
    case 'HIGH':
      return 'Forecasts vary more, so this amount or temperature is less certain. Agreement does not guarantee accuracy.'
    default:
      return 'There is not enough information to compare the forecasts.'
  }
}
