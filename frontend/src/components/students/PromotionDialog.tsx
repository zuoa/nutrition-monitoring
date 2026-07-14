import { useMemo, useState } from 'react'
import { ArrowRight, GraduationCap, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import { orgApi } from '@/api/client'
import { AdminDialogShell, fieldClassName, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'
import { classOptions, type SchoolNode } from './adminTypes'

type PreviewRow = {
  student_id: number
  student_no: string
  student_name: string
  source_class_id: number
  source_class_name: string
  action: 'promote' | 'graduate' | 'skip'
  target_class_id: number | null
  target_class_name: string | null
}

type PromotionPreview = {
  preview_token: string
  summary: { promoted: number; graduated: number; skipped: number; total: number }
  students: PreviewRow[]
}

function decisionFromValue(value: string) {
  if (value.startsWith('promote:')) return { action: 'promote', target_class_id: Number(value.slice(8)) }
  return { action: value as 'graduate' | 'skip' }
}

function valueFromDecision(action: string, targetClassId: number | null) {
  return action === 'promote' && targetClassId ? `promote:${targetClassId}` : action
}

export function PromotionDialog({ tree, onClose, onCompleted }: { tree: SchoolNode[]; onClose: () => void; onCompleted: () => void }) {
  const classes = useMemo(() => classOptions(tree), [tree])
  const [mappingValues, setMappingValues] = useState<Record<number, string>>({})
  const [overrides, setOverrides] = useState<Record<number, string>>({})
  const [preview, setPreview] = useState<PromotionPreview | null>(null)
  const [previewDirty, setPreviewDirty] = useState(false)
  const [loading, setLoading] = useState(false)

  const buildPayload = () => ({
    mappings: Object.entries(mappingValues)
      .filter(([, value]) => value && value !== 'skip')
      .map(([sourceClassId, value]) => ({ source_class_id: Number(sourceClassId), ...decisionFromValue(value) })),
    overrides: Object.entries(overrides).map(([studentId, value]) => ({ student_id: Number(studentId), ...decisionFromValue(value) })),
  })

  const runPreview = async () => {
    const payload = buildPayload()
    if (payload.mappings.length === 0) {
      toast.error('请至少为一个来源班级选择升班或毕业')
      return
    }
    setLoading(true)
    try {
      const response = await orgApi.previewPromotion(payload)
      setPreview(response.data?.data || null)
      setPreviewDirty(false)
    } finally {
      setLoading(false)
    }
  }

  const apply = async () => {
    if (!preview || previewDirty) return
    setLoading(true)
    try {
      const response = await orgApi.applyPromotion({ ...buildPayload(), preview_token: preview.preview_token })
      const result = response.data?.data || {}
      toast.success(`操作完成：升班 ${result.promoted || 0} 人，毕业 ${result.graduated || 0} 人`)
      onCompleted()
    } finally {
      setLoading(false)
    }
  }

  const setStudentDecision = (row: PreviewRow, value: string) => {
    setOverrides(current => ({ ...current, [row.student_id]: value }))
    setPreviewDirty(true)
  }

  return (
    <AdminDialogShell title="批量升年级" description="先映射整班，再为留级、转班等个别学生改派。执行时会再次核对当前班级，数据变化则拒绝提交。" onClose={onClose} wide>
      <div className="grid gap-5 xl:grid-cols-[420px_1fr]">
        <aside className="rounded-lg border border-border">
          <div className="border-b border-border bg-secondary/25 px-4 py-3"><h3 className="flex items-center gap-2 text-sm font-medium"><GraduationCap className="h-4 w-4 text-primary" />整班映射</h3><p className="mt-1 text-xs text-muted-foreground">目标班级需提前在组织维护中建立。</p></div>
          <div className="max-h-[58vh] divide-y divide-border overflow-auto">
            {classes.map(source => (
              <label key={source.id} className="block px-4 py-3">
                <span className="mb-1.5 block truncate text-sm" title={source.label}>{source.label}</span>
                <select className={`${fieldClassName} py-1.5 text-xs`} value={mappingValues[source.id] || 'skip'} onChange={event => { setMappingValues(current => ({ ...current, [source.id]: event.target.value })); setPreview(null); setOverrides({}) }}>
                  <option value="skip">本次不处理</option>
                  <option value="graduate">全班毕业</option>
                  {classes.filter(target => target.id !== source.id).map(target => <option key={target.id} value={`promote:${target.id}`}>升入 → {target.label}</option>)}
                </select>
              </label>
            ))}
          </div>
          <div className="border-t border-border p-3"><button type="button" className={`${primaryButtonClassName} w-full gap-2`} disabled={loading} onClick={runPreview}><ArrowRight className="h-4 w-4" />{preview ? '更新预览' : '生成预览'}</button></div>
        </aside>

        <div className="min-w-0">
          {!preview ? <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border px-8 text-center text-sm leading-6 text-muted-foreground">在左侧选择来源班级的去向。系统只处理当前仍属于来源班级的在校学生。</div> : (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-secondary/20 px-4 py-3">
                <div className="flex gap-4 text-sm"><span>共 <b>{preview.summary.total}</b> 人</span><span className="text-primary">升班 {preview.summary.promoted}</span><span>毕业 {preview.summary.graduated}</span><span className="text-muted-foreground">跳过 {preview.summary.skipped}</span></div>
                <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><ShieldCheck className="h-3.5 w-3.5" />事务化执行</span>
              </div>
              <div className="max-h-[58vh] overflow-auto rounded-lg border border-border">
                <table className="data-table min-w-[760px]">
                  <thead><tr><th>学号</th><th>姓名</th><th>来源班级</th><th>本次去向</th></tr></thead>
                  <tbody>{preview.students.map(row => {
                    const currentValue = overrides[row.student_id] || valueFromDecision(row.action, row.target_class_id)
                    return <tr key={row.student_id}><td className="font-mono text-xs">{row.student_no}</td><td>{row.student_name}</td><td>{row.source_class_name}</td><td><select className={`${fieldClassName} min-w-64 py-1.5 text-xs`} value={currentValue} onChange={event => setStudentDecision(row, event.target.value)}><option value="skip">个别跳过</option><option value="graduate">标记毕业</option>{classes.filter(target => target.id !== row.source_class_id).map(target => <option key={target.id} value={`promote:${target.id}`}>升入 → {target.label}</option>)}</select></td></tr>
                  })}</tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-border pt-4"><p className="text-xs text-muted-foreground">{previewDirty ? '个别学生去向已修改，请先更新预览。' : '执行失败时整批回滚，不会出现部分学生已升班。'}</p><div className="flex gap-2"><button type="button" className={secondaryButtonClassName} onClick={onClose}>取消</button><button type="button" className={primaryButtonClassName} disabled={!preview || previewDirty || loading} onClick={apply}>{loading ? '执行中…' : '确认执行'}</button></div></div>
    </AdminDialogShell>
  )
}
