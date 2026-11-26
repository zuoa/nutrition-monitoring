export const ASSESSMENT_STATUSES = [
  { key: 'low', label: '偏低' },
  { key: 'slightly_low', label: '轻微偏低' },
  { key: 'met', label: '达标' },
  { key: 'slightly_high', label: '轻微偏高' },
  { key: 'high', label: '偏高' },
] as const

export const ASSESSMENT_GRADE_STARTS = ['A', 'B', 'C'] as const

type Status = typeof ASSESSMENT_STATUSES[number]['key']
type GradeStart = typeof ASSESSMENT_GRADE_STARTS[number]

export type ReportAssessmentScoring = {
  status_points: Record<Status, number>
  grade_min_scores: Record<GradeStart, number>
}

export type ReportAssessmentScoringForm = {
  statusPoints: Record<Status, string>
  gradeStarts: Record<GradeStart, string>
}

const defaults: ReportAssessmentScoring = {
  status_points: { low: 5, slightly_low: 15, met: 20, slightly_high: 15, high: 5 },
  grade_min_scores: { A: 80, B: 70, C: 55 },
}

export function assessmentScoringForm(value: ReportAssessmentScoring = defaults): ReportAssessmentScoringForm {
  return {
    statusPoints: Object.fromEntries(ASSESSMENT_STATUSES.map(({ key }) =>
      [key, String(value.status_points[key])],
    )) as ReportAssessmentScoringForm['statusPoints'],
    gradeStarts: Object.fromEntries(ASSESSMENT_GRADE_STARTS.map(grade =>
      [grade, String(value.grade_min_scores[grade])],
    )) as ReportAssessmentScoringForm['gradeStarts'],
  }
}

function readPoints(text: string) {
  return text.trim() ? Number(text) : NaN
}

export function serializeAssessmentScoring(value: ReportAssessmentScoringForm): ReportAssessmentScoring {
  const status_points = Object.fromEntries(ASSESSMENT_STATUSES.map(({ key, label }) => {
    const points = readPoints(value.statusPoints[key])
    if (!Number.isInteger(points) || points < 0 || points > 20) throw new Error(`${label}分值必须是 0～20 的整数`)
    return [key, points]
  })) as ReportAssessmentScoring['status_points']
  const grade_min_scores = Object.fromEntries(ASSESSMENT_GRADE_STARTS.map(grade => {
    const minimum = readPoints(value.gradeStarts[grade])
    if (!Number.isInteger(minimum)) throw new Error(`${grade} 级起点必须是整数`)
    return [grade, minimum]
  })) as ReportAssessmentScoring['grade_min_scores']
  const points = Object.values(status_points)
  const minimum = 5 * Math.min(...points)
  const maximum = 5 * Math.max(...points)
  const { A, B, C } = grade_min_scores
  if (!(minimum < C && C < B && B < A && A <= maximum)) {
    throw new Error(`等级起点须满足 ${minimum} < C < B < A ≤ ${maximum}`)
  }
  return { status_points, grade_min_scores }
}
