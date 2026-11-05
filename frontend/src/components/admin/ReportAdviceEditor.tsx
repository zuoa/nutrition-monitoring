import { useEffect, useRef, useState } from 'react'
import { Download, MessageSquareText, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { adminApi } from '@/api/client'

interface AdviceRule {
  id: string
  scope: string
  category: string
  condition: string
  description: string
  enabled: boolean
  phrases: string[]
  placeholders: string[]
  match: { event?: string }
}
interface EditableRule {
  description: string
  enabled: boolean
  phrases: string
}
interface ImportPreview {
  errors: string[]
  strategy_count: number
  added_count: number
  updates: Record<string, { phrases: string[] }>
  changes: { id: string; condition: string; location: string; added_count: number; phrases: string[] }[]
}
const EMPTY_RULES: AdviceRule[] = []
const CATEGORY_LABELS: Record<string, string> = { diet: '膳食建议', sports: '运动建议', family: '家庭 / 家校', coordination: '食动配合' }
const inputClass = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm'
const buttonClass = 'rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary disabled:opacity-50'
const lines = (value: string) => [...new Set(value.split(/\r?\n/).map(line => line.trim()).filter(Boolean))]
const editable = (rules: AdviceRule[]) => Object.fromEntries(rules.map(rule => [rule.id, {
  description: rule.description, enabled: rule.enabled, phrases: rule.phrases.join('\n'),
}]))

export default function ReportAdviceEditor({ initialRules = EMPTY_RULES }: { initialRules?: AdviceRule[] }) {
  const [rules, setRules] = useState(initialRules)
  const [draft, setDraft] = useState<Record<string, EditableRule>>(() => editable(initialRules))
  const [dirty, setDirty] = useState(false)
  const [scope, setScope] = useState('personal')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    setRules(initialRules)
    setDraft(editable(initialRules))
    setDirty(false)
    setPreview(null)
  }, [initialRules])
  const visible = rules.filter(rule => rule.scope === scope && (!category || rule.category === category)
    && `${rule.id} ${rule.condition} ${draft[rule.id]?.description || ''}`.includes(search.trim()))

  const update = (id: string, change: Partial<EditableRule>) => {
    setDraft(previous => ({ ...previous, [id]: { ...previous[id], ...change } }))
    setDirty(true)
    setPreview(null)
  }
  const save = async (importing = false) => {
    if (importing && (!preview || preview.errors.length)) return
    const next = { ...draft }
    if (importing && preview) {
      for (const [id, change] of Object.entries(preview.updates)) {
        next[id] = { ...next[id], phrases: [...new Set([...lines(next[id].phrases), ...change.phrases])].join('\n') }
      }
    }
    const invalid = Object.entries(next).find(([, rule]) => rule.enabled && !lines(rule.phrases).length)
    if (invalid) { toast.error(`${invalid[0]}：启用策略至少填写一条话术`); return }
    setBusy(true)
    try {
      await adminApi.updateConfig({ report_advice_rules: Object.fromEntries(Object.entries(next).map(([id, value]) => [id, {
        ...value, phrases: lines(value.phrases),
      }])) })
      setDraft(next)
      setRules(previous => previous.map(rule => ({ ...rule, ...next[rule.id], phrases: lines(next[rule.id].phrases) })))
      setDirty(false)
      setPreview(null)
      toast.success(importing ? '话术已追加并保存' : '周报建议库已保存')
    } finally { setBusy(false) }
  }
  const importFile = async (file?: File) => {
    if (!file) return
    setBusy(true)
    setPreview(null)
    try {
      const response = await adminApi.previewReportAdvice(file)
      setPreview(response.data.data)
    } finally {
      setBusy(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }
  const downloadTemplate = async () => {
    setBusy(true)
    try {
      const response = await adminApi.reportAdviceTemplate()
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = '周报建议库.xlsx'
      anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } finally { setBusy(false) }
  }
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium"><MessageSquareText className="h-4 w-4 text-muted-foreground" />周报建议库</h2>
      <p className="text-xs leading-5 text-muted-foreground">适用于个人、班级、年级周报。每行一条候选话术，生成时随机选择一句并保存。同一份报告的预览与导出保持一致，历史报告需覆盖生成后更新。</p>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <label className="text-xs">报告类型
          <select aria-label="建议报告类型" value={scope} onChange={event => setScope(event.target.value)} className={`${inputClass} mt-1`}>
            <option value="personal">个人周报</option><option value="class">班级周报</option><option value="grade">年级周报</option>
          </select>
        </label>
        <label className="text-xs">建议类别
          <select aria-label="建议类别" value={category} onChange={event => setCategory(event.target.value)} className={`${inputClass} mt-1`}>
            <option value="">全部类别</option>{Object.entries(CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label className="min-w-40 flex-1 text-xs">搜索策略
          <input value={search} onChange={event => setSearch(event.target.value)} placeholder="指标、体型或策略编号" className={`${inputClass} mt-1`} />
        </label>
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
        <button type="button" disabled={busy} onClick={downloadTemplate} className={`${buttonClass} flex items-center gap-2`}><Download className="h-4 w-4" />下载导入模板</button>
        <button type="button" disabled={busy} onClick={() => fileInput.current?.click()} className={`${buttonClass} flex items-center gap-2`}><Upload className="h-4 w-4" />导入 Excel</button>
        <input ref={fileInput} type="file" accept=".xlsx" aria-label="导入建议话术 Excel" className="hidden" onChange={event => importFile(event.target.files?.[0])} />
      </div>
      <p className="mb-4 text-xs leading-5 text-muted-foreground">支持原始三工作表和标准模板。导入按固定策略追加、去重，保留已有启停与说明；模板导出已保存内容。触发条件固定，可编辑策略说明。</p>
      {preview && <div className="mb-4 rounded-lg border border-border bg-secondary/30 p-4" aria-live="polite">
        {preview.errors.length ? <><p className="text-sm font-medium text-destructive">校验未通过，未导入任何内容</p><ul className="mt-2 list-inside list-disc text-xs text-destructive">{preview.errors.map((error, index) => <li key={index}>{error}</li>)}</ul></> : <>
          <p className="text-sm font-medium">导入预览：匹配 {preview.strategy_count} 条策略，新增 {preview.added_count} 条话术</p>
          <div className="my-3 max-h-64 space-y-2 overflow-auto">{preview.changes.map(change => <details key={change.id} className="rounded border border-border p-2 text-xs">
            <summary className="cursor-pointer">{change.condition} · 新增 {change.added_count} 条 · {change.location}</summary>
            <p className="mt-2 whitespace-pre-line leading-5">{change.phrases.join('\n')}</p>
          </details>)}</div>
          <button type="button" disabled={busy} onClick={() => save(true)} className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">确认导入并保存建议库</button>
        </>}
        <button type="button" disabled={busy} onClick={() => setPreview(null)} className={`${buttonClass} ml-2 mt-2`}>关闭预览</button>
      </div>}
      <fieldset disabled={busy || !!preview} className="max-h-[36rem] space-y-2 overflow-y-auto pr-1 disabled:opacity-60">
        <legend className="mb-2 text-xs text-muted-foreground">共 {visible.length} 条策略，展开编辑</legend>
        {visible.map(rule => {
          const value = draft[rule.id]
          if (!value) return null
          return <details key={rule.id} className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm leading-6"><span className="mr-2 text-xs text-muted-foreground">{CATEGORY_LABELS[rule.category]}</span>{rule.condition}<span className="ml-2 text-xs text-muted-foreground">{value.enabled ? '已启用' : '已停用'} · {lines(value.phrases).length} 条话术</span></summary>
            <div className="mt-3 space-y-3">
              <p className="break-all text-xs text-muted-foreground">{rule.id} · {CATEGORY_LABELS[rule.category]}{rule.match.event?.startsWith('run_') ? ' · 待阳光跑消耗数据接入，暂不触发' : ''}</p>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={value.enabled} onChange={event => update(rule.id, { enabled: event.target.checked })} />启用此策略</label>
              <label className="block space-y-1 text-xs"><span>策略说明</span><input maxLength={500} value={value.description} onChange={event => update(rule.id, { description: event.target.value })} className={inputClass} /></label>
              <label className="block space-y-1 text-xs"><span>候选话术（每行一条）</span><textarea rows={4} value={value.phrases} onChange={event => update(rule.id, { phrases: event.target.value })} className={`${inputClass} leading-6`} /></label>
              {rule.placeholders.length > 0 && <p className="text-xs text-muted-foreground">可用占位符：{rule.placeholders.map(key => `【${key}】`).join('、')}</p>}
            </div>
          </details>
        })}
        {!visible.length && <p className="py-4 text-sm text-muted-foreground">没有匹配的策略</p>}
      </fieldset>
      <div className="mt-4 flex items-center gap-3">
        <button type="button" disabled={busy || !dirty || !!preview} onClick={() => save()} className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">{busy ? '处理中…' : dirty ? '保存建议库' : '建议库已保存'}</button>
        <span className="text-xs text-muted-foreground">此处单独保存建议库。</span>
      </div>
    </section>
  )
}
