import { Settings } from 'lucide-react'
import { ASSESSMENT_RANGE_FIELDS, assessmentRangesForm, type ReportAssessmentRangesForm } from '@/lib/reportAssessmentRanges'

type Props = {
  value: ReportAssessmentRangesForm
  onChange: (value: ReportAssessmentRangesForm) => void
  onSave: () => void
  saving: boolean
  dirty: boolean
}

export default function ReportAssessmentRangesEditor({ value, onChange, onSave, saving, dirty }: Props) {
  return <section className="rounded-xl border border-border bg-card p-5">
    <h2 className="mb-3 flex items-center gap-2 text-sm font-medium"><Settings className="h-4 w-4 text-muted-foreground" />报告营养达标区间</h2>
    <p id="report-assessment-ranges-help" className="text-xs leading-5 text-muted-foreground">直接填写每天的营养值下限和上限。启用后，个人、班级、年级和校区报告统一采用这些固定区间；营养素增益系数只调整采集数据。</p>
    <label className="mt-4 flex min-h-10 cursor-pointer items-center gap-3 text-sm">
      <input type="checkbox" checked={value.enabled} disabled={saving} onChange={event => onChange({ ...value, enabled: event.target.checked })} className="h-4 w-4 accent-primary focus-visible:ring-2 focus-visible:ring-primary" />
      使用自定义固定达标区间
    </label>
    <fieldset disabled={saving || !value.enabled} aria-describedby="report-assessment-ranges-help" className="mt-3 space-y-3 disabled:opacity-50">
      {ASSESSMENT_RANGE_FIELDS.map(field => <div key={field.key} className="grid items-center gap-2 rounded-lg border border-border p-3 sm:grid-cols-[110px_minmax(0,1fr)_minmax(0,1fr)]">
        <span className="text-xs font-medium">{field.label}<span className="ml-1 text-[11px] font-normal text-muted-foreground">{field.unit}/天</span></span>
        {(['lower', 'upper'] as const).map(bound => <label key={bound} className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          {bound === 'lower' ? '下限' : '上限'}
          <input type="number" min={0} step="any" inputMode="decimal" value={value.nutrients[field.key][bound]}
            aria-label={`${field.label}${bound === 'lower' ? '下限' : '上限'}`} placeholder={bound === 'upper' ? '无上限' : '必填'}
            onChange={event => onChange({ ...value, nutrients: { ...value.nutrients, [field.key]: { ...value.nutrients[field.key], [bound]: event.target.value } } })}
            className="min-h-10 w-full min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
        </label>)}
      </div>)}
    </fieldset>
    <p className="mt-3 text-xs leading-5 text-muted-foreground">上限留空表示不限制上限。钙展示供给数值，不参与营养等级评价。未启用时按年龄段参考；性别未提供时取同年龄段较低值。保存后请覆盖重新生成已有报告。</p>
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <button type="button" onClick={onSave} disabled={saving || !dirty} className="min-h-10 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">{saving ? '保存中…' : dirty ? '保存业务规则' : '业务规则已保存'}</button>
      <button type="button" onClick={() => onChange(assessmentRangesForm())} disabled={saving} className="min-h-10 rounded-lg border border-border px-4 py-2 text-sm hover:bg-secondary disabled:opacity-50">恢复默认区间</button>
    </div>
  </section>
}
