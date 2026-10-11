import type { Alert } from '../types'

interface Props {
  alerts: Alert[]
  onSelectLocation: (id: string) => void
}

export function AlertsStrip({ alerts, onSelectLocation }: Props) {
  if (alerts.length === 0) return null

  return (
    <div className="alerts-strip" aria-label="HeatShield calculated alerts">
      {alerts.slice(0, 3).map((a) => (
        <button
          key={a.id}
          type="button"
          className={`alert-card severity-${a.severity}`}
          onClick={() => onSelectLocation(a.locationId)}
        >
          <span className="alert-icon">ALERT</span>
          <div>
            <strong>{a.title}</strong>
            <p>
              {a.locationName}: {a.reason}
            </p>
            <p className="muted small">{a.disclaimer}</p>
          </div>
        </button>
      ))}
    </div>
  )
}
