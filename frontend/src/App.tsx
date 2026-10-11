import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  chatAboutRisk,
  explainRisk,
  getAppConfig,
  getAlerts,
  getAllRisk,
  getRisk,
  startFloodSimulation,
  startHeatSimulation,
  stopSimulation,
  updateFloodSimulationParameters,
} from './api/client'
import { AlertsStrip } from './components/AlertsStrip'
import { IndiaMap } from './components/IndiaMap'
import { LocationPanel } from './components/LocationPanel'
import { GlobalWeatherDashboard } from './components/GlobalWeatherDashboard'
import { RiskLegend } from './components/RiskLegend'
import { SimulationModal } from './components/SimulationModal'
import type { AiExplanation, Alert, RiskAssessment } from './types'
import './App.css'

function DemoDashboard() {
  const [assessments, setAssessments] = useState<RiskAssessment[]>([])
  const [selectedId, setSelectedId] = useState<string | null>('sikkim')
  const [selected, setSelected] = useState<RiskAssessment | null>(null)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [simulationMode, setSimulationMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [panelLoading, setPanelLoading] = useState(false)
  const [simModalOpen, setSimModalOpen] = useState(false)
  const [simBusy, setSimBusy] = useState(false)
  const [explanation, setExplanation] = useState<AiExplanation | null>(null)
  const [aiQuestion, setAiQuestion] = useState('Why is this high?')
  const [explainLoading, setExplainLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshAll = useCallback(async () => {
    const [riskData, alertList] = await Promise.all([getAllRisk(), getAlerts()])
    setAssessments(riskData.assessments)
    setSimulationMode(riskData.simulationMode)
    setAlerts(alertList)
    return riskData.assessments
  }, [])

  const loadSelection = useCallback(async (id: string) => {
    setPanelLoading(true)
    setExplanation(null)
    try {
      const a = await getRisk(id)
      setSelected(a)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load this location')
    } finally {
      setPanelLoading(false)
    }
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const loadedAssessments = await refreshAll()
        const initial = loadedAssessments.find((assessment) => assessment.location.id === selectedId)
        if (initial) setSelected(initial)
        else if (selectedId) await loadSelection(selectedId)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load data')
      } finally {
        setLoading(false)
      }
    })()
  }, [refreshAll, loadSelection, selectedId])

  const selectedAssessment = useMemo(() => {
    if (selected) return selected
    return assessments.find((a) => a.location.id === selectedId) ?? null
  }, [selected, assessments, selectedId])

  const handleSelect = (id: string) => {
    setSelectedId(id)
    void loadSelection(id)
  }

  const handleExplain = async () => {
    if (!selectedId || !aiQuestion.trim()) return
    setExplainLoading(true)
    try {
      const exp = await chatAboutRisk(selectedId, aiQuestion.trim())
      setExplanation(exp)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Explanation failed')
    } finally {
      setExplainLoading(false)
    }
  }

  const applySimulation = async (
    fn: () => Promise<RiskAssessment>,
    simulationQuestion = 'What changed in this simulation, and which factors contribute most?',
  ) => {
    setSimBusy(true)
    setExplanation(null)
    try {
      const a = await fn()
      setSelected(a)
      setSimulationMode(true)
      await refreshAll()
      setAiQuestion(simulationQuestion)
      setExplainLoading(true)
      try {
        setExplanation(await explainRisk(selectedId!, simulationQuestion))
      } catch (e) {
        setError(e instanceof Error ? `Simulation updated; explanation unavailable: ${e.message}` : 'Simulation updated; explanation unavailable')
      } finally {
        setExplainLoading(false)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Simulation failed')
    } finally {
      setSimBusy(false)
    }
  }

  const handleStopSim = async () => {
    setSimBusy(true)
    try {
      await stopSimulation()
      setSimulationMode(false)
      await refreshAll()
      if (selectedId) await loadSelection(selectedId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not exit simulation')
    } finally {
      setSimBusy(false)
    }
  }

  const handleFloodParameters = (
    rainfallIntensityMmHr: number,
    cumulativeRainfallMm24h: number,
    riverLevelM: number,
  ) => {
    if (!selectedId) return
    const simulationQuestion = `What happens when rainfall is ${rainfallIntensityMmHr} mm/h and river level is ${riverLevelM} m?`
    void applySimulation(
      () => updateFloodSimulationParameters(
        selectedId,
        rainfallIntensityMmHr,
        cumulativeRainfallMm24h,
        riverLevelM,
      ),
      simulationQuestion,
    )
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>HEATSHIELD</h1>
          <p className="tagline">Climate Risk Intelligence</p>
        </div>
        <div className="header-meta">
          {simulationMode && <span className="sim-banner header-sim">SIMULATION MODE</span>}
          <span className="demo-badge">DEMO DATA</span>
        </div>
      </header>

      {error && (
        <div className="error-banner" role="alert">
          {error}
          <button type="button" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      <main className="main">
        <div className="map-column">
          {loading ? (
            <div className="map-placeholder">Loading map…</div>
          ) : (
            <>
              <IndiaMap
                assessments={assessments}
                selectedId={selectedId}
                onSelect={handleSelect}
              />
              <RiskLegend />
            </>
          )}
        </div>
        <LocationPanel
          assessment={selectedAssessment}
          loading={loading || panelLoading}
          explanation={explanation}
          explainLoading={explainLoading}
          question={aiQuestion}
          onQuestionChange={setAiQuestion}
          onExplain={() => void handleExplain()}
          onOpenSimulation={() => setSimModalOpen(true)}
        />
      </main>

      <AlertsStrip alerts={alerts} onSelectLocation={handleSelect} />

      <SimulationModal
        key={selectedAssessment?.timestamp}
        open={simModalOpen}
        assessment={selectedAssessment}
        onClose={() => setSimModalOpen(false)}
        busy={simBusy}
        onStop={() => void handleStopSim()}
        onFloodPreset={(preset) =>
          void applySimulation(() => startFloodSimulation(selectedId!, preset))
        }
        onHeatPreset={(preset) =>
          void applySimulation(() => startHeatSimulation(selectedId!, preset))
        }
        onFloodParameters={handleFloodParameters}
      />

      <footer className="footer">
        HeatShield assessments are decision-support only — not official government warnings.
      </footer>
    </div>
  )
}

export default function App() {
  const [dataMode, setDataMode] = useState<'demo' | 'real' | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getAppConfig()
      .then((config) => {
        if (!active) return
        if (config.dataMode !== 'demo' && config.dataMode !== 'real') {
          throw new Error('The API returned an unknown data mode.')
        }
        setDataMode(config.dataMode)
      })
      .catch((error: unknown) => {
        if (active) setConfigError(error instanceof Error ? error.message : 'Could not load API configuration.')
      })
    return () => {
      active = false
    }
  }, [])

  if (dataMode === 'real') return <GlobalWeatherDashboard />
  if (dataMode === 'demo') return <DemoDashboard />

  return (
    <div className="app config-loading">
      <header className="header">
        <div>
          <h1>HEATSHIELD</h1>
          <p className="tagline">Climate Risk Intelligence</p>
        </div>
      </header>
      <main className="config-status">
        <p role={configError ? 'alert' : 'status'}>
          {configError ?? 'Connecting to HeatShield API…'}
        </p>
      </main>
    </div>
  )
}
