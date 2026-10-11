import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet'
import { useEffect } from 'react'
import type { RiskAssessment } from '../types'
import { RISK_COLORS } from '../utils/riskColors'
import 'leaflet/dist/leaflet.css'

const INDIA_CENTER: [number, number] = [22.5, 79.0]
const INDIA_ZOOM = 5

function FitIndia() {
  const map = useMap()
  useEffect(() => {
    map.setView(INDIA_CENTER, INDIA_ZOOM)
  }, [map])
  return null
}

interface Props {
  assessments: RiskAssessment[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function IndiaMap({ assessments, selectedId, onSelect }: Props) {
  return (
    <MapContainer
      center={INDIA_CENTER}
      zoom={INDIA_ZOOM}
      className="india-map"
      scrollWheelZoom
    >
      <FitIndia />
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {assessments.map((a) => {
        const color = RISK_COLORS[a.riskLevel]
        const radius = selectedId === a.location.id ? 14 : 10
        return (
          <CircleMarker
            key={a.location.id}
            center={[a.location.lat, a.location.lng]}
            radius={radius}
            pathOptions={{
              color: selectedId === a.location.id ? '#fff' : color,
              weight: selectedId === a.location.id ? 3 : 2,
              fillColor: color,
              fillOpacity: 0.85,
            }}
            eventHandlers={{ click: () => onSelect(a.location.id) }}
          >
            <Popup>
              <strong>{a.location.name}</strong>
              <br />
              Risk: {a.riskLevel.toUpperCase()} ({a.riskScore})
            </Popup>
          </CircleMarker>
        )
      })}
    </MapContainer>
  )
}
