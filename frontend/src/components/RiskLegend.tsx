import { RISK_COLORS } from '../utils/riskColors'

const items = [
  { level: 'low', label: 'LOW' },
  { level: 'moderate', label: 'MODERATE' },
  { level: 'high', label: 'HIGH' },
  { level: 'critical', label: 'CRITICAL' },
] as const

export function RiskLegend() {
  return (
    <div className="risk-legend">
      <span className="legend-title">Risk legend</span>
      {items.map(({ level, label }) => (
        <div key={level} className="legend-item">
          <span className="legend-swatch" style={{ background: RISK_COLORS[level] }} />
          {label}
        </div>
      ))}
    </div>
  )
}
