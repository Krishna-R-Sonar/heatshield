import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { getAlerts, getAllRisk, getAppConfig, searchWeatherLocations } from './api/client'
import { GlobalWeatherDashboard } from './components/GlobalWeatherDashboard'

vi.mock('./api/client', () => ({
  chatAboutRisk: vi.fn(),
  explainRisk: vi.fn(),
  getAppConfig: vi.fn(),
  getAlerts: vi.fn(),
  getAllRisk: vi.fn(),
  getRainfallEvidence: vi.fn(),
  getRisk: vi.fn(),
  searchWeatherLocations: vi.fn(),
  getWeatherForecast: vi.fn(),
  startFloodSimulation: vi.fn(),
  startHeatSimulation: vi.fn(),
  stopSimulation: vi.fn(),
  updateFloodSimulationParameters: vi.fn(),
}))

vi.mock('./components/IndiaMap', () => ({
  IndiaMap: () => <div data-testid="india-map">India map</div>,
}))

vi.mock('./components/GlobalWeatherMap', () => ({
  GlobalWeatherMap: () => <div data-testid="world-map">World map</div>,
}))

afterEach(cleanup)

describe('dashboard request states', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(getAppConfig).mockResolvedValue({ dataMode: 'demo' })
  })

  it('shows loading feedback while the first request is pending', async () => {
    vi.mocked(getAllRisk).mockImplementation(() => new Promise(() => {}))
    vi.mocked(getAlerts).mockImplementation(() => new Promise(() => {}))

    render(<App />)

    expect(await screen.findByText('Loading map…')).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('Loading location assessment')
  })

  it('shows an error and a recoverable empty location state when the API fails', async () => {
    vi.mocked(getAllRisk).mockRejectedValue(new Error('API offline'))
    vi.mocked(getAlerts).mockRejectedValue(new Error('API offline'))

    render(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent('API offline')
    expect(screen.getByText('Select a location')).toBeTruthy()
    expect(screen.getByTestId('india-map')).toBeTruthy()
  })

  it('uses the global live-weather view in real mode without requesting demo risk data', async () => {
    vi.mocked(getAppConfig).mockResolvedValue({ dataMode: 'real' })

    render(<App />)

    expect(await screen.findByText('Weather and water decision support')).toBeInTheDocument()
    expect(screen.getByTestId('world-map')).toBeInTheDocument()
    expect(screen.getByText('FORECAST EVIDENCE · NOT A WARNING')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Choose a location' })).toBeInTheDocument()
    expect(getAllRisk).not.toHaveBeenCalled()
    expect(getAlerts).not.toHaveBeenCalled()
  })

  it('only reports empty search results after a worldwide search completes', async () => {
    vi.mocked(searchWeatherLocations).mockResolvedValue([{
      id: 'open-meteo-123',
      name: 'Auckland',
      state: 'Auckland Region',
      country: 'New Zealand',
      lat: -36.85,
      lng: 174.76,
    }])
    render(<GlobalWeatherDashboard />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Find a place' }), {
      target: { value: 'Auckland' },
    })
    expect(screen.queryByText('No matching places.')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByRole('list', { name: 'Location results' })).toBeInTheDocument()
    expect(screen.getByText('Auckland Region, New Zealand')).toBeInTheDocument()
  })
})