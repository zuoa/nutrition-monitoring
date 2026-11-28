import { Settings } from 'lucide-react'
import {
  ASSESSMENT_GRADE_STARTS,
  ASSESSMENT_STATUSES,
  assessmentScoringForm,
  serializeAssessmentScoring,
  type ReportAssessmentScoringForm,
} from '@/lib/reportAssessmentScoring'

type Props = {
  value: ReportAssessmentScoringForm
  onChange: (value: ReportAssessmentScoringForm) => void
  onSave: () => void
  saving: boolean
  dirty: boolean
}

const pointInputClass = 'min-h-10 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary'

function gradePreview(value: ReportAssessmentScoringForm) {
  try {
    const { status_points, grade_min_scores: { A, B, C } } = serializeAssessmentScoring(value)
    const points = Object.values(status_points)
    const ranges = [[A, 5 * Math.max(...points)], [B, A - 1], [C, B - 1], [5 * Math.min(...points), C - 1]]
    return { ranges, error: '' }
  } catch (error) {
    return { ranges: [], error: error instanceof Error ? error.message : '请检查营养报告计分配置' }
  }
}

export default function ReportAssessmentScoringEditor({ value, onChange, onSave, saving, dirty }: Props) {
  const { ranges, error } = gradePreview(value)

  return <section className="rounded-xl border border-border bg-card p-5">
    <h2 className="mb-3 flex items-center gap-2 text-sm font-medium"><Settings className="h-4 w-4 text-muted-foreground" />营养报告计分</h2>
    <p className="text-xs leading-5 text-muted-foreground">五项营养指标按状态计分，总分决定报告等级。保存后，个人报告的预览和新导出使用当前配置，群体报告在生成时使用当前配置。</p>
    <fieldset disabled={saving} className="mt-4">
      <legend className="text-xs font-medium">每项指标的状态分值（0–20 分）</legend>
      <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {ASSESSMENT_STATUSES.map(({ key, label }) => <label key={key} className="space-y-1 text-xs text-muted-foreground">
          <span>{label}</span>
          <input type="number" min={0} max={20} step={1} inputMode="numeric" value={value.statusPoints[key]}
            onChange={event => onChange({ ...value, statusPoints: { ...value.statusPoints, [key]: event.target.value } })}
            className={pointInputClass} />
        </label>)}
      </div>
    </fieldset>
    <fieldset disabled={saving} className="mt-4">
      <legend className="text-xs font-medium">等级起点（含该分值）</legend>
      <div className="mt-2 grid grid-cols-3 gap-3">
        {ASSESSMENT_GRADE_STARTS.map(grade => <label key={grade} className="space-y-1 text-xs text-muted-foreground">
          <span>{grade} 级最低分</span>
          <input type="number" min={0} max={100} step={1} inputMode="numeric" value={value.gradeStarts[grade]}
            onChange={event => onChange({ ...value, gradeStarts: { ...value.gradeStarts, [grade]: event.target.value } })}
            className={pointInputClass} />
        </label>)}
      </div>
    </fieldset>
    <div aria-live="polite" className="mt-3 text-xs leading-5">
      {error ? <p className="text-destructive">{error}</p> :
        <ul aria-label="等级分数区间" className="grid grid-cols-2 gap-x-3 text-muted-foreground sm:grid-cols-4">
          {([...ASSESSMENT_GRADE_STARTS, 'D'] as const).map((grade, index) => {
            const [from, to] = ranges[index]
            return <li key={grade}><span className="font-medium text-foreground">{grade} 级</span> {from === to ? from : `${from}–${to}`} 分</li>
          })}
        </ul>}
    </div>
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button type="button" onClick={onSave} disabled={saving || !dirty || !!error}
        className="min-h-10 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
        {saving ? '保存中…' : dirty ? '保存业务规则' : '业务规则已保存'}
      </button>
      <button type="button" onClick={() => onChange(assessmentScoringForm())} disabled={saving}
        className="min-h-10 rounded-lg border border-border px-4 py-2 text-sm hover:bg-secondary disabled:opacity-50">恢复默认计分</button>
    </div>
  </section>
}
