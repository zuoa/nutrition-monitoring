import { Settings } from 'lucide-react'
import { REPORT_GAIN_FIELDS, type ReportGainKey, type ReportNutrientGains } from '@/lib/nutrition'

interface Props {
  gains: ReportNutrientGains
  onChange: (key: ReportGainKey, value: string) => void
  onReset: () => void
  onSave: () => void
  saving: boolean
  dirty: boolean
}

export default function ReportNutrientGainsEditor({ gains, onChange, onReset, onSave, saving, dirty }: Props) {
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium">
        <Settings className="h-4 w-4 text-muted-foreground" />报告营养素增益
      </h2>
      <p id="report-nutrient-gains-help" className="text-xs leading-5 text-muted-foreground">
        六项营养素可分别设置倍数，报告营养值 = 原营养值 × 对应系数。例如 1.2 表示增加 20%，1 表示保持原值。范围 0.01～10。
      </p>
      <fieldset disabled={saving} aria-describedby="report-nutrient-gains-help" className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3 disabled:opacity-60">
        {REPORT_GAIN_FIELDS.map(field => (
          <label key={field.key} className="space-y-1">
            <span className="text-xs text-muted-foreground">{field.label}系数</span>
            <div className="flex items-center gap-2">
              <input
                type="number" min={0.01} max={10} step="any" inputMode="decimal" required
                value={gains[field.key]} onChange={event => onChange(field.key, event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
              />
              <span className="text-xs text-muted-foreground">倍</span>
            </div>
          </label>
        ))}
      </fieldset>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        用于新生成的个人、班级、年级和校区周报 / 月报，营养趋势及评价同步计算。原始菜品和营养记录保留原值；已有报告需重新生成。
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={onSave} disabled={saving || !dirty} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50">
          {saving ? '保存中...' : dirty ? '保存业务规则' : '业务规则已保存'}
        </button>
        <button type="button" onClick={onReset} disabled={saving} className="rounded-lg border border-border px-4 py-2 text-sm transition hover:bg-secondary disabled:opacity-50">
          系数恢复为 1
        </button>
        <span className="text-xs text-muted-foreground">与本页其他业务规则一起保存。</span>
      </div>
    </section>
  )
}
