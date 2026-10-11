import { useEffect, useState, type FormEvent } from 'react'
import { askGlobalEvidence } from '../api/client'
import type {
  GlobalEnvironmentalSuggestion,
  GlobalEvidenceAnswer,
  GlobalEvidenceReference,
  WeatherLocation,
} from '../types'

export function GlobalEvidenceCopilot({
  location,
  refreshVersion = 0,
}: {
  location: WeatherLocation
  refreshVersion?: number
}) {
  const isMapPoint = location.selectionType === 'map_point'
  const initialQuestion = isMapPoint
    ? 'Summarize the forecast for these selected coordinates using only retrieved weather evidence. Explain temperature, apparent temperature, rainfall, and any modeled river data; distinguish estimates from observations, note missing inputs, and do not infer local impacts or give unsupported action advice.'
    : 'Assess the available environmental evidence for this location. Summarize notable conditions and why they may matter; separate locally evidence-based suggestions from general preventive ideas and community opportunities; identify missing information and limitations. Do not invent unsupported topics or local impacts.'
  const requestKey = JSON.stringify([
    location.id,
    location.name,
    location.state,
    location.country,
    location.lat,
    location.lng,
    location.selectionType ?? 'named_place',
    refreshVersion,
  ])
  const [question, setQuestion] = useState('')
  const [requestState, setRequestState] = useState<{
    key: string
    loading: boolean
    answer?: GlobalEvidenceAnswer
    error?: string
  }>({ key: requestKey, loading: true })
  const isCurrentRequest = requestState.key === requestKey
  const loading = !isCurrentRequest || requestState.loading
  const answer = isCurrentRequest ? requestState.answer ?? null : null
  const error = isCurrentRequest ? requestState.error ?? null : null
  const assessment = answer?.environmental_assessment
  const recommendedNextSteps = assessment?.recommended_next_steps.length
    ? assessment.recommended_next_steps
    : answer?.sections.recommended ?? []
  const evidenceReference = (id: string): GlobalEvidenceReference | undefined =>
    assessment?.evidence_references.find((reference) => reference.id === id)
  const evidenceReferenceLabel = (id: string): string => {
    const reference = evidenceReference(id)
    if (!reference) return id
    const value = [reference.value, reference.unit].filter(Boolean).join(' ')
    const period = reference.period ? `, ${reference.period}` : ''
    return `${reference.label}: ${value} (${reference.evidence_type}${period}; ${reference.source})`
  }
  const confidenceLabel = (confidence: string): string => ({
    strong: 'Strongly supported by local evidence',
    partial: 'Partly supported by local evidence',
    general: 'General idea; local need is not established',
  })[confidence] ?? 'Support not established'
  const effortLabel = (effort: string): string => ({
    low: 'Low',
    medium: 'Moderate',
    high: 'High',
    unknown: 'Not estimated',
  })[effort] ?? 'Not estimated'
  const renderSuggestions = (
    heading: string,
    suggestions: GlobalEnvironmentalSuggestion[] | undefined,
  ) => {
    if (!suggestions?.length) return null
    return (
      <div className="environmental-suggestion-group">
        <h5>{heading}</h5>
        <div className="environmental-suggestion-list">
          {suggestions.map((suggestion, index) => (
            <article className="environmental-suggestion" key={`${suggestion.category}-${index}`}>
              <h6>{suggestion.suggestion}</h6>
              <p className="muted small">
                {suggestion.category.replaceAll('_', ' ')} · {confidenceLabel(suggestion.confidence)}
              </p>
              <p>{suggestion.why_it_may_help}</p>
              <dl>
                <div><dt>Who can act</dt><dd>{suggestion.who_can_act}</dd></div>
                <div><dt>Effort</dt><dd>{effortLabel(suggestion.effort)}</dd></div>
                <div><dt>Possible benefit</dt><dd>{suggestion.expected_benefit}</dd></div>
              </dl>
              {suggestion.evidence_refs.length > 0 && (
                <p className="environmental-suggestion-evidence">
                  Evidence:{' '}
                  {suggestion.evidence_refs.map((id) => {
                    const reference = evidenceReference(id)
                    return reference
                      ? evidenceReferenceLabel(id)
                      : id
                  }).join('; ')}
                </p>
              )}
              {suggestion.evidence_refs.length === 0 && (
                <p className="environmental-suggestion-evidence">
                  General idea; no local measurement establishes a need for this action.
                </p>
              )}
              <p className="muted small">Limitations: {suggestion.limitations}</p>
            </article>
          ))}
        </div>
      </div>
    )
  }

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    void askGlobalEvidence(location, initialQuestion, controller.signal).then(
      (result) => {
        if (active) {
          setRequestState({ key: requestKey, loading: false, answer: result })
        }
      },
      (cause: unknown) => {
        if (active) {
          const message = cause instanceof Error ? cause.message : 'Evidence explanation unavailable.'
          setRequestState({
            key: requestKey,
            loading: false,
            error: message,
          })
        }
      },
    )
    return () => {
      active = false
      controller.abort()
    }
  }, [initialQuestion, location, requestKey])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!question.trim() || loading) return
    setRequestState({ key: requestKey, loading: true })
    try {
      const result = await askGlobalEvidence(location, question.trim())
      setRequestState({
        key: requestKey,
        loading: false,
        answer: result,
      })
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Evidence explanation unavailable.'
      setRequestState({
        key: requestKey,
        loading: false,
        error: message,
      })
    }
  }

  return (
    <section className="evidence-section evidence-copilot">
      <h3>Climate Copilot</h3>
      <p className="muted small">
        {isMapPoint
          ? 'HeatShield prepares a weather-evidence summary for these coordinates automatically. Ask a follow-up; it does not infer nearby settlements or population-specific impacts.'
          : 'HeatShield prepares an evidence interpretation automatically. Ask a follow-up for more detail; AI analysis is identified separately from the deterministic fallback.'}
      </p>
      <form className="copilot-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="global-evidence-question">Ask a follow-up about this location’s evidence</label>
        <textarea
          id="global-evidence-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask a follow-up about the retrieved evidence"
          maxLength={2000}
          rows={2}
        />
        <button className="btn primary" type="submit" disabled={loading || !question.trim()}>
          {loading ? 'Reviewing evidence…' : 'Ask follow-up'}
        </button>
      </form>
      {loading && <p role="status" className="muted small">Preparing an interpretation from structured provider evidence…</p>}
      {error && <p role="alert" className="provider-error">Evidence answer unavailable: {error}</p>}
      {answer && (
        <div className="global-evidence-answer" aria-live="polite">
          <p className="muted small" role="status">
            {answer.source === 'openrouter_ai_evidence_guidance'
              || answer.source === 'strands_ai_evidence_guidance'
              ? 'AI environmental assessment · based on retrieved evidence.'
              : 'Deterministic evidence summary · AI assessment unavailable.'}
          </p>
          <section className="copilot-answer-section">
            <h4>What is happening?</h4>
            <p>{assessment?.location_summary ?? answer.ai_response ?? 'A concise interpretation is not available from the current evidence.'}</p>
            {assessment?.location_summary_evidence_refs.length ? (
              <p className="muted small">
                Evidence: {assessment.location_summary_evidence_refs.map(evidenceReferenceLabel).join('; ')}
              </p>
            ) : null}
            {assessment?.key_findings.length ? (
              <ul>
                {assessment.key_findings.map((finding, index) => (
                  <li key={`${finding.finding}-${index}`}>
                    {finding.finding}
                    <span className="muted small">
                      {' '}Evidence: {finding.evidence_refs.map(evidenceReferenceLabel).join('; ')}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
          <section className="copilot-answer-section">
            <h4>Why it matters</h4>
            {assessment?.environmental_concerns.length ? (
              <ul>
                {assessment.environmental_concerns.map((concern, index) => (
                  <li key={`${concern.concern}-${index}`}>
                    {concern.concern}
                    <span className="muted small">
                      {' '}Evidence: {concern.evidence_refs.map(evidenceReferenceLabel).join('; ')}
                    </span>
                  </li>
                ))}
              </ul>
            ) : answer.sections.interpreted.length > 0
              ? <ul>{answer.sections.interpreted.map((item) => <li key={item}>{item}</li>)}</ul>
              : <p>The available evidence does not support a more specific interpretation.</p>}
          </section>
          <section className="copilot-answer-section environmental-suggestions-section">
            <h4>Suggestions to improve environmental conditions</h4>
            {assessment ? (
              <>
                {renderSuggestions('Evidence-based suggestions', assessment.evidence_based_suggestions)}
                {renderSuggestions('General preventive ideas', assessment.general_preventive_ideas)}
                {renderSuggestions('Community or authority opportunities', assessment.community_opportunities)}
                {!assessment.evidence_based_suggestions.length
                  && !assessment.general_preventive_ideas.length
                  && !assessment.community_opportunities.length
                  && <p>No specific improvement action is supported by the available information. No action is better than an unsupported local recommendation.</p>}
              </>
            ) : (
              <p>No structured improvement suggestions were returned. Review the evidence and limitations before deciding on local action.</p>
            )}
          </section>
          <section className="copilot-answer-section">
            <h4>What information is missing?</h4>
            {assessment?.missing_information.length
              ? <ul>{assessment.missing_information.map((item) => <li key={item}>{item}</li>)}</ul>
              : <p>No additional gaps were listed in this response; individual provider status and coverage still apply.</p>}
          </section>
          <section className="copilot-answer-section">
            <h4>What to check next</h4>
            {recommendedNextSteps.length > 0
              || answer.sections.recommendations.residents_and_workers.length > 0 ? (
              <ul>
                {recommendedNextSteps.map((item) => <li key={item}>{item}</li>)}
                {answer.sections.recommendations.residents_and_workers.map((item) => (
                  <li key={`${item.topic}-${item.recommendation}`}>
                    {item.recommendation}
                    <span className="muted small"> Evidence: {item.evidence_basis}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p>No further location-specific action is supported by the available evidence. Check relevant official local updates for current conditions.</p>
            )}
          </section>
          {assessment && (
            <details className="copilot-evidence-details">
              <summary>Explore the evidence and assessment limits</summary>
              <p className="muted small">
                {assessment.status === 'AI_GENERATED'
                  ? `AI assessment${assessment.model_used ? ` · ${assessment.model_used}` : ''}`
                  : 'Deterministic evidence summary; AI suggestions were unavailable.'}
                {assessment.generated_at
                  ? ` · Prepared ${new Date(assessment.generated_at).toLocaleString()}`
                  : ''}
              </p>
              {assessment.limitations.length > 0 && (
                <ul>{assessment.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
              )}
              {assessment.evidence_completeness && (
                <div>
                  <p><strong>Evidence completeness (not hazard severity):</strong></p>
                  <ul>
                    {Object.entries(assessment.evidence_completeness).map(([hazard, coverage]) => (
                      <li key={hazard}>
                        <strong>{hazard.replaceAll('_', ' ')}: {coverage.status.replaceAll('_', ' ')}</strong>
                        {' — '}{coverage.rationale}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <p>Forecasts, reanalysis and modeled evidence are not verified local measurements or official warnings.</p>
            </details>
          )}
          <p className="muted small">{answer.disclaimer}</p>
        </div>
      )}
    </section>
  )
}