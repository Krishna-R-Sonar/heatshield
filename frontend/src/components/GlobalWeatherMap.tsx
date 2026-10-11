import { CircleMarker, MapContainer, Popup, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { useEffect } from 'react'
import type { WeatherLocation } from '../types'
import 'leaflet/dist/leaflet.css'

const WORLD_CENTER: [number, number] = [20, 0]

interface Props {
  location: WeatherLocation | null
  onPick: (latitude: number, longitude: number) => void
}

function MapSelection({ onPick }: Pick<Props, 'onPick'>) {
  useMapEvents({
    click(event) {
      onPick(event.latlng.lat, event.latlng.lng)
    },
  })
  return null
}

function FocusLocation({ location }: Pick<Props, 'location'>) {
  const map = useMap()
  useEffect(() => {
    if (location) map.flyTo([location.lat, location.lng], Math.max(map.getZoom(), 5), { duration: 0.6 })
  }, [location, map])
  return null
}

export function GlobalWeatherMap({ location, onPick }: Props) {
  return (
    <MapContainer center={WORLD_CENTER} zoom={2} minZoom={2} className="world-map" scrollWheelZoom>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <MapSelection onPick={onPick} />
      <FocusLocation location={location} />
      {location && (
        <CircleMarker
          center={[location.lat, location.lng]}
          radius={8}
          pathOptions={{ color: '#fff', weight: 2, fillColor: '#167d8d', fillOpacity: 0.95 }}
        >
          <Popup>{location.name}</Popup>
        </CircleMarker>
      )}
    </MapContainer>
  )
}