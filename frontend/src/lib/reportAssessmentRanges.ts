import { NUTRITION_FIELDS } from '@/lib/nutrition'

export const ASSESSMENT_RANGE_KEYS = ['calories', 'protein', 'fat', 'carbohydrate', 'fiber'] as const
export type AssessmentRangeKey = typeof ASSESSMENT_RANGE_KEYS[number]
export const ASSESSMENT_RANGE_FIELDS = ASSESSMENT_RANGE_KEYS.map(key => ({ ...NUTRITION_FIELDS.find(field => field.key === key)!, key }))
export type ReportAssessmentRanges = {
  enabled: boolean
  nutrients: Record<AssessmentRangeKey, { lower: number; upper: number | null }>
}
export type ReportAssessmentRangesForm = {
  enabled: boolean
  nutrients: Record<AssessmentRangeKey, { lower: string; upper: string }>
}

const defaults: ReportAssessmentRanges = {
  enabled: false,
  nutrients: {
    calories: { lower: 1800, upper: 2200 }, protein: { lower: 48, upper: 72 },
    fat: { lower: 44.4, upper: 66.7 }, carbohydrate: { lower: 250, upper: 325 }, fiber: { lower: 25, upper: null },
  },
}

export function assessmentRangesForm(value: ReportAssessmentRanges = defaults): ReportAssessmentRangesForm {
  return { enabled: value.enabled, nutrients: Object.fromEntries(ASSESSMENT_RANGE_KEYS.map(key => [key, {
    lower: String(value.nutrients[key].lower), upper: value.nutrients[key].upper === null ? '' : String(value.nutrients[key].upper),
  }])) as ReportAssessmentRangesForm['nutrients'] }
}

export function serializeAssessmentRanges(value: ReportAssessmentRangesForm): ReportAssessmentRanges {
  const nutrients = Object.fromEntries(ASSESSMENT_RANGE_FIELDS.map(field => {
    const lowerText = value.nutrients[field.key].lower.trim()
    const upperText = value.nutrients[field.key].upper.trim()
    const lower = Number(lowerText)
    const upper = upperText ? Number(upperText) : null
    if (!lowerText || !Number.isFinite(lower) || lower < 0) throw new Error(`${field.label}下限必须是大于等于 0 的数字`)
    if (upper !== null && (!Number.isFinite(upper) || upper <= lower)) throw new Error(`${field.label}上限必须大于下限，或留空表示无上限`)
    return [field.key, { lower, upper }]
  })) as ReportAssessmentRanges['nutrients']
  return { enabled: value.enabled, nutrients }
}
