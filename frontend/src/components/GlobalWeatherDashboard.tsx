import { useState, type FormEvent } from 'react'
import { searchWeatherLocations } from '../api/client'
import type { WeatherLocation } from '../types'
import { GlobalWeatherMap } from './GlobalWeatherMap'
import { GlobalEvidencePanel } from './GlobalEvidencePanel'

function coordinateText(latitude: number, longitude: number): string {
  return `${latitude.toFixed(4)}, ${longitude.toFixed(4)}`
}

export function GlobalWeatherDashboard() {
  const [query, setQuery] = useState('')
  const [locations, setLocations] = useState<WeatherLocation[]>([])
  const [selected, setSelected] = useState<WeatherLocation | null>(null)
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (query.trim().length < 2) return
    setSearching(true)
    setError(null)
    try {
      setLocations(await searchWeatherLocations(query.trim()))
      setHasSearched(true)
    } catch (cause) {
      setLocations([])
      setHasSearched(true)
      setError(cause instanceof Error ? cause.message : 'Location search failed.')
    } finally {
      setSearching(false)
    }
  }

  const pickCoordinates = (latitude: number, longitude: number) => {
    const lat = Number(latitude.toFixed(5))
    const lng = Number(longitude.toFixed(5))
    const coordinates = coordinateText(lat, lng)
    setSelected({
      id: `map-${lat}-${lng}`,
      name: `Map point ${coordinates}`,
      state: 'Selected map point',
      country: '',
      lat,
      lng,
      selectionType: 'map_point',
    })
    setLocations([])
    setHasSearched(false)
    setError(null)
  }

  const selectLocation = (location: WeatherLocation) => {
    setSelected({ ...location, selectionType: 'named_place' })
    setLocations([])
    setHasSearched(false)
    setError(null)
  }

  return (
    <div className="app real-weather-app">
      <header className="header">
        <div>
          <h1>HEATSHIELD</h1>
          <p className="tagline">Weather and water decision support</p>
        </div>
        <span className="live-weather-badge">FORECAST EVIDENCE · NOT A WARNING</span>
      </header>

      <section className="global-search" aria-label="Worldwide location search">
        <form onSubmit={(event) => void search(event)}>
          <label htmlFor="global-location-search">Find a place</label>
          <div className="global-search-controls">
            <input
              id="global-location-search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                setLocations([])
                setHasSearched(false)
                setError(null)
              }}
              placeholder="City, region, or country"
              minLength={2}
              maxLength={100}
            />
            <button className="btn primary" type="submit" disabled={searching || query.trim().length < 2}>
              {searching ? 'Searching…' : 'Search'}
            </button>
          </div>
        </form>
        {error && <p className="global-search-error" role="alert">{error}</p>}
        {locations.length > 0 && (
          <ul className="global-search-results" aria-label="Location results">
            {locations.map((location) => (
              <li key={location.id}>
                <button type="button" onClick={() => selectLocation(location)}>
                  <strong>{location.name}</strong>
                  <span>{[location.state, location.country].filter(Boolean).join(', ')}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {!searching && hasSearched && locations.length === 0 && !error && (
          <p className="global-search-empty">No matching places.</p>
        )}
      </section>

      <main className="main global-weather-main">
        <div className="map-column">
          <GlobalWeatherMap location={selected} onPick={pickCoordinates} />
        </div>
        <aside className="panel live-weather-panel">
          {selected ? (
            <>
              <p className="weather-panel-kicker">LOCAL FORECAST AND EVIDENCE</p>
              <h2>{selected.name}</h2>
              <p className="state-line">
                {[selected.state, selected.country].filter(Boolean).join(', ') || coordinateText(selected.lat, selected.lng)}
              </p>
              <GlobalEvidencePanel key={selected.id} location={selected} />
            </>
          ) : (
            <>
              <p className="weather-panel-kicker">LOCAL FORECAST AND EVIDENCE</p>
              <h2>Choose a location</h2>
              <p>Search for a place or select a point on the map to see forecast conditions, historical context, and what the available evidence cannot tell us.</p>
            </>
          )}
          <p className="muted small weather-disclaimer">
            Forecasts are model estimates, not ground measurements. HeatShield is not an official warning service and does not assign real-world heat or flood risk from weather alone.
          </p>
        </aside>
      </main>

      <footer className="footer">HeatShield is decision support: check verified local observations and official advisories for current conditions.</footer>
    </div>
  )
}