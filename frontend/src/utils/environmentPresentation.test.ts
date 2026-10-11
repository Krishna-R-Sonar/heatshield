import { describe, expect, it } from 'vitest'
import type { HistoricalAnomalyPoint } from '../types'
import {
  displayNumber,
  formatLocalDate,
  historicalComparisonText,
  plainStatus,
  formatLocalDateTime,
  uncertaintyText,
} from './environmentPresentation'

const historicalPoint: HistoricalAnomalyPoint = {
  date: '2026-10-11',
  indicator: 'rainfall',
  current_value: 3.6,
  unit: 'mm',
  anomaly: 2.9,
  percentile: 95.8,
  historical_baseline: {
    mean: 0.7,
    median: 0.2,
    p25: 0,
    p75: 0.5,
    minimum: 0,
    maximum: 4,
    sample_count: 930,
  },
  status: 'AVAILABLE',
  forecast_data_type: 'FORECAST',
  baseline_data_type: 'REANALYSIS',
}

describe('environment presentation helpers', () => {
  it('rounds display values without turning small nonzero values into zero', () => {
    expect(displayNumber(22.746, '°C')).toBe('22.7 °C')
    expect(displayNumber(0.04, 'mm')).toBe('less than 0.1 mm')
    expect(displayNumber(0, 'mm')).toBe('0 mm')
    expect(displayNumber(null, 'mm')).toBe('Not available')
  })

  it('translates provider and assessment statuses for ordinary users', () => {
    expect(plainStatus('INCOMPLETE')).toBe('Not enough information to assess this yet')
    expect(plainStatus('DEGRADED')).toBe('Some information is missing')
    expect(plainStatus('MODEL_EVIDENCE')).toBe('Computer-model estimate')
  })

  it('describes historical comparisons without presenting percentile as danger probability', () => {
    const text = historicalComparisonText(historicalPoint, '1991–2020')
    expect(text).toContain('rainfall forecast')
    expect(text).toContain('2.9 mm above the historical October average (0.7 mm)')
    expect(text).toContain('higher than most comparable October days')
    expect(text).toContain('not a danger level, heat or flood probability, or warning')
    expect(historicalComparisonText({ ...historicalPoint, percentile: null }, '1991–2020')).toBeNull()
  })

  it('explains forecast disagreement without assigning risk or safety', () => {
    expect(uncertaintyText('HIGH')).toContain('Forecasts vary more')
    expect(uncertaintyText('LOW')).toContain('fairly close')
    expect(uncertaintyText('UNAVAILABLE')).toContain('not enough information')
  })

  it('formats date-only forecast labels without shifting them to a different day', () => {
    expect(formatLocalDate('2026-10-11')).toMatch(/11 Oct|Oct 11/)
    expect(formatLocalDate('invalid-date')).toBe('invalid-date')
  })

  it('formats offset forecast timestamps in the selected location time zone', () => {
    const formatted = formatLocalDateTime('2026-10-09T14:00:00+13:00', 'Pacific/Auckland')
    expect(formatted).toMatch(/9 Oct|Oct 9/)
    expect(formatted).toMatch(/2:00|14:00/)
  })
})
