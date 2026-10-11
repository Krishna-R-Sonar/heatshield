import { useState } from 'react'
import type { RiskAssessment } from '../types'

interface Props {
  open: boolean
  assessment: RiskAssessment | null
  onClose: () => void
  onFloodPreset: (preset: 'normal' | 'heavy_rain' | 'river_rising') => void
  onHeatPreset: (preset: 'normal' | 'extreme_heat') => void
  onFloodParameters: (rainfallIntensityMmHr: number, cumulativeRainfallMm24h: number, riverLevelM: number) => void
  onStop: () => void
  busy: boolean
}

export function SimulationModal({
  open,
  assessment,
  onClose,
  onFloodPreset,
  onHeatPreset,
  onFloodParameters,
  onStop,
  busy,
}: Props) {
  const [rainfallIntensity, setRainfallIntensity] = useState(() => {
    const value = assessment?.providerObservations?.rainfall?.values.rainfallIntensityMmHr?.value
    return typeof value === 'number' ? value : 0
  })
  const [cumulativeRainfall, setCumulativeRainfall] = useState(() => {
    const value = assessment?.providerObservations?.rainfall?.values.cumulativeRainfallMm24h?.value
    return typeof value === 'number' ? value : 0
  })
  const [riverLevel, setRiverLevel] = useState(() => {
    const value = assessment?.providerObservations?.river?.values.riverLevelM?.value
    return typeof value === 'number' ? value : 0
  })

  if (!open || !assessment) return null

  const isFlood = assessment.scenario === 'flood_glof'

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="simulation-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="simulation-title">Climate scenario</h2>
        <p className="sim-banner inline">SIMULATION MODE — overrides demo inputs only</p>
        <p>
          Location: <strong>{assessment.location.name}</strong> (
          {isFlood ? 'Flood risk' : 'Extreme heat'})
        </p>

        {isFlood ? (
          <div className="sim-buttons">
            <button type="button" disabled={busy} onClick={() => onFloodPreset('normal')}>
              Normal
            </button>
            <button type="button" disabled={busy} onClick={() => onFloodPreset('heavy_rain')}>
              Heavy rainfall
            </button>
            <button type="button" disabled={busy} onClick={() => onFloodPreset('river_rising')}>
              River level rising
            </button>
          </div>
        ) : (
          <div className="sim-buttons">
            <button type="button" disabled={busy} onClick={() => onHeatPreset('normal')}>
              Normal temperature
            </button>
            <button type="button" disabled={busy} onClick={() => onHeatPreset('extreme_heat')}>
              Extreme heat
            </button>
          </div>
        )}

        {isFlood && (
          <section className="simulation-parameters" aria-label="Flood simulation parameters">
            <h3>Adjust simulation inputs</h3>
            <label htmlFor="rainfall-intensity">Rainfall intensity · {rainfallIntensity} mm/h</label>
            <input
              id="rainfall-intensity"
              type="range"
              min="0"
              max="120"
              step="1"
              value={rainfallIntensity}
              onChange={(event) => setRainfallIntensity(Number(event.target.value))}
            />
            <label htmlFor="rainfall-24h">24-hour rainfall · {cumulativeRainfall} mm</label>
            <input
              id="rainfall-24h"
              type="range"
              min="0"
              max="500"
              step="5"
              value={cumulativeRainfall}
              onChange={(event) => setCumulativeRainfall(Number(event.target.value))}
            />
            <label htmlFor="river-level">River level · {riverLevel.toFixed(1)} m</label>
            <input
              id="river-level"
              type="range"
              min="0"
              max="10"
              step="0.1"
              value={riverLevel}
              onChange={(event) => setRiverLevel(Number(event.target.value))}
            />
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => onFloodParameters(rainfallIntensity, cumulativeRainfall, riverLevel)}
            >
              Apply simulation inputs
            </button>
          </section>
        )}

        <div className="modal-footer">
          <button type="button" className="btn secondary" disabled={busy} onClick={onStop}>
            Exit simulation
          </button>
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
