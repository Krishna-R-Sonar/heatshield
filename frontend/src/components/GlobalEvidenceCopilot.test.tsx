import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { GlobalEvidenceAnswer, WeatherLocation } from '../types'
import { GlobalEvidenceCopilot } from './GlobalEvidenceCopilot'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const location: WeatherLocation = {
  id: 'auckland',
  name: 'Auckland',
  state: 'Auckland Region',
  country: 'New Zealand',
  lat: -36.85,
  lng: 174.76,
}

const mapPoint: WeatherLocation = {
  id: 'map-point-28.1177-85.4736',
  name: 'Map point 28.1177, 85.4736',
  state: 'Selected map point',
  country: '',
  lat: 28.1177,
  lng: 85.4736,
  selectionType: 'map_point',
}

const answer: GlobalEvidenceAnswer = {
  summary: 'Retrieved structured forecast evidence for Auckland. No risk score was calculated.',
  ai_response: 'The available forecast does not show hot conditions; humidity alone is not a heat-risk assessment.',
  environmental_assessment: {
    status: 'AI_GENERATED',
    location_summary: 'The available forecast does not show hot conditions; humidity alone is not a heat-risk assessment.',
    location_summary_evidence_refs: [],
    key_findings: [],
    environmental_concerns: [],
    evidence_based_suggestions: [],
    general_preventive_ideas: [{
      suggestion: 'Review local water-saving practices.',
      why_it_may_help: 'Using water thoughtfully can help avoid unnecessary demand.',
      evidence_refs: [],
      who_can_act: 'Households and businesses',
      effort: 'low',
      expected_benefit: 'May reduce avoidable water use.',
      confidence: 'general',
      limitations: 'Current local water availability was not assessed.',
      category: 'water',
    }],
    community_opportunities: [],
    missing_information: ['Local water availability and exposure information.'],
    recommended_next_steps: ['Decision-support suggestion: review local official river information.'],
    limitations: ['No validated local heat-risk score is available.'],
    generated_at: '2026-10-09T00:00:00Z',
    model_used: 'test-model',
    evidence_references: [],
  },
  sections: {
    evidence: ['Temperature forecast: 29 °C (FORECAST).'],
    interpreted: ['Unavailable evidence: modeled river-discharge evidence.'],
    recommended: ['Decision-support suggestion: review local official river information.'],
    recommendations: {
      residents_and_workers: [{
        topic: 'heat',
        recommendation: 'Use shade during the forecast period.',
        evidence_type: 'FORECAST',
        evidence_basis: 'Open-Meteo temperature forecast: 29 °C.',
        period: '2026-10-09T14:00:00+13:00',
        scope: 'forecast-preparedness',
      }],
      municipal_planners: [{
        topic: 'heat',
        recommendation: 'Review access to shade and cooling.',
        evidence_type: 'FORECAST',
        evidence_basis: 'Open-Meteo temperature forecast: 29 °C.',
        period: '2026-10-09T14:00:00+13:00',
        scope: 'forecast-preparedness',
      }],
    },
  },
  disclaimer: 'Not an official warning.',
  source: 'openrouter_ai_evidence_guidance',
}

describe('GlobalEvidenceCopilot', () => {
  it('sends selected coordinates and avoids repeating the detailed evidence panels', async () => {
    const request = vi.spyOn(api, 'askGlobalEvidence').mockResolvedValue(answer)
    render(<GlobalEvidenceCopilot location={location} />)

    expect(screen.getByPlaceholderText('Ask a follow-up about the retrieved evidence')).toHaveValue('')
    expect(await screen.findByText('AI environmental assessment · based on retrieved evidence.')).toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(
      location,
      expect.stringContaining('Assess the available environmental evidence'),
      expect.any(AbortSignal),
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Ask a follow-up about this location’s evidence' }), {
      target: { value: 'Why is the heat forecast unusual?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Ask follow-up' }))

    expect(await screen.findByText('AI environmental assessment · based on retrieved evidence.')).toBeInTheDocument()
    expect(screen.getByText('The available forecast does not show hot conditions; humidity alone is not a heat-risk assessment.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What is happening?' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Why it matters' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Suggestions to improve environmental conditions' })).toBeInTheDocument()
    expect(screen.getByText('Review local water-saving practices.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What to check next' })).toBeInTheDocument()
    expect(screen.getByText('Decision-support suggestion: review local official river information.')).toBeInTheDocument()
    expect(screen.queryByText('Temperature forecast: 29 °C (FORECAST).')).not.toBeInTheDocument()
    expect(screen.queryByText(answer.summary)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental conditions and personal preparedness' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental and infrastructure considerations' })).not.toBeInTheDocument()
    expect(request).toHaveBeenNthCalledWith(2, location, 'Why is the heat forecast unusual?')
  })

  it('automatically labels deterministic fallback insights when model guidance is unavailable', async () => {
    const request = vi.spyOn(api, 'askGlobalEvidence').mockResolvedValue({
      ...answer,
      ai_response: 'The selected-hour forecast is 7.7 °C. No validated heat score is available.',
      source: 'deterministic_evidence_fallback',
      environmental_assessment: {
        ...answer.environmental_assessment!,
        status: 'DETERMINISTIC_FALLBACK',
        location_summary: 'The selected-hour forecast is 7.7 °C. No validated heat score is available.',
        evidence_based_suggestions: [],
        general_preventive_ideas: [],
        community_opportunities: [],
      },
    })
    render(<GlobalEvidenceCopilot location={location} />)

    expect(await screen.findByText(
      'Deterministic evidence summary · AI assessment unavailable.',
    )).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What is happening?' })).toBeInTheDocument()
    expect(screen.getByText(/The selected-hour forecast is 7.7 °C/)).toBeInTheDocument()
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('keeps map-pin guidance weather-focused instead of implying settlement context', async () => {
    const request = vi.spyOn(api, 'askGlobalEvidence').mockResolvedValue({
      ...answer,
      ai_response: null,
      sections: {
        ...answer.sections,
        recommended: [
          'Weather evidence is for the selected coordinates and provider grid cells. No nearby settlement or population-exposure context is verified for this pin.',
        ],
      },
      source: 'deterministic_evidence_fallback',
      environmental_assessment: {
        ...answer.environmental_assessment!,
        status: 'DETERMINISTIC_FALLBACK',
        evidence_based_suggestions: [],
        general_preventive_ideas: [],
        community_opportunities: [],
        recommended_next_steps: [],
      },
    })
    render(<GlobalEvidenceCopilot location={mapPoint} />)

    expect(screen.getByPlaceholderText('Ask a follow-up about the retrieved evidence')).toHaveValue('')

    expect(await screen.findByText(
      'Deterministic evidence summary · AI assessment unavailable.',
    )).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What to check next' })).toBeInTheDocument()
    expect(screen.getByText(
      'Weather evidence is for the selected coordinates and provider grid cells. No nearby settlement or population-exposure context is verified for this pin.',
    )).toBeInTheDocument()
    expect(screen.queryByText(/Weather insights for Map point/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental conditions and personal preparedness' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Environmental and infrastructure considerations' })).not.toBeInTheDocument()
    expect(screen.queryByText(/your neighborhood|cooling breaks/i)).not.toBeInTheDocument()
    expect(request).toHaveBeenCalledWith(
      mapPoint,
      expect.stringContaining('selected coordinates'),
      expect.any(AbortSignal),
    )
  })

  it('refreshes the automatic interpretation and replaces stale content', async () => {
    const refreshedAnswer = {
      ...answer,
      ai_response: 'The refreshed forecast provides updated evidence for this location.',
      environmental_assessment: {
        ...answer.environmental_assessment!,
        location_summary: 'The refreshed forecast provides updated evidence for this location.',
      },
    }
    let resolveRefresh: ((value: GlobalEvidenceAnswer) => void) | undefined
    const request = vi.spyOn(api, 'askGlobalEvidence')
      .mockResolvedValueOnce(answer)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve }))
    const { rerender } = render(<GlobalEvidenceCopilot location={location} refreshVersion={0} />)

    expect(await screen.findByText(answer.ai_response!)).toBeInTheDocument()
    rerender(<GlobalEvidenceCopilot location={location} refreshVersion={1} />)

    expect(await screen.findByText('Preparing an interpretation from structured provider evidence…')).toBeInTheDocument()
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    expect(screen.queryByText(answer.ai_response!)).not.toBeInTheDocument()
    expect(resolveRefresh).toBeDefined()

    await act(async () => resolveRefresh?.(refreshedAnswer))
    expect(await screen.findByText(refreshedAnswer.ai_response!)).toBeInTheDocument()
  })

  it('shows a recoverable error when the automatic evidence request times out', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (signal?.aborted) {
        reject(signal.reason)
        return
      }
      signal?.addEventListener('abort', () => reject(signal.reason), { once: true })
    }))
    vi.stubGlobal('fetch', fetchMock)
    render(<GlobalEvidenceCopilot location={location} />)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000)
    })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Evidence interpretation request timed out after 20 seconds. Please retry.',
    )
    expect(screen.queryByText('Preparing an interpretation from structured provider evidence…')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: 'Ask a follow-up about this location’s evidence' }), {
      target: { value: 'Try again' },
    })
    expect(screen.getByRole('button', { name: 'Ask follow-up' })).toBeEnabled()
  })

  it('shows an explicit error instead of fabricating an answer', async () => {
    vi.spyOn(api, 'askGlobalEvidence').mockRejectedValue(new Error('Provider tools unavailable'))
    render(<GlobalEvidenceCopilot location={location} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('Provider tools unavailable')
    expect(screen.queryByText(/risk score was calculated/)).not.toBeInTheDocument()
  })
})