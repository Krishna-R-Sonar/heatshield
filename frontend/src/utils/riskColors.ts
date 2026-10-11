import type { RiskLevel } from '../types'

export const RISK_COLORS: Record<RiskLevel, string> = {
  low: '#22c55e',
  moderate: '#eab308',
  high: '#f97316',
  critical: '#ef4444',
}

export function riskLabel(level: RiskLevel): string {
  return level.toUpperCase()
}
