import { useEffect, useState } from 'react'
import { Download, Loader2, PackageOpen } from 'lucide-react'
import { reportApi } from '@/api/client'
import type { ReportExportPeriod, ReportExportTask, ReportFormat } from '@/types'
import type { ReportCadence } from '@/lib/reportPeriods'

export function saveDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename.replace(/[\\/:*?"<>|]/g, '_')
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function ReportDownload({ reportId, name, period, format = 'detailed' }: { reportId: number; name: string; period: string; format?: ReportFormat }) {
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const response = await reportApi.downloadPng(reportId, format)
      saveDownload(response.data, `${name}_营养${format === 'brief' ? '简报' : '报告'}_${period}.png`)
    } catch { /* The API client displays the error. */ } finally { setBusy(false) }
  }
  return <button type="button" disabled={busy} onClick={() => void download()} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-medium transition-colors hover:bg-secondary disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}{busy ? '生成图片中…' : '下载 PNG'}
  </button>
}

export default function ReportExports({ classes, preferredClassId, userId, cadence = 'weekly', preferredPeriod, preferredFormat = 'detailed' }: {
  classes: { id: number; name: string }[]; preferredClassId?: number; userId: number; cadence?: ReportCadence; preferredPeriod?: ReportExportPeriod | null; preferredFormat?: ReportFormat
}) {
  const reportType = `personal_${cadence}`
  const storageKey = `nutrition-report-export:${userId}`
  const [classId, setClassId] = useState<number>(preferredClassId || classes[0]?.id || 0)
  const [format, setFormat] = useState<ReportFormat>(preferredFormat)
  const [periods, setPeriods] = useState<ReportExportPeriod[]>([])
  const [periodKey, setPeriodKey] = useState('')
  const [loadingPeriods, setLoadingPeriods] = useState(false)
  const [periodError, setPeriodError] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [task, setTask] = useState<ReportExportTask | null>(null)
  const [pollError, setPollError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [taskId, setTaskId] = useState(() => {
    try { return Number(localStorage.getItem(storageKey)) || 0 } catch { return 0 }
  })

  useEffect(() => {
    setClassId(current => classes.some(c => c.id === current) ? current : classes[0]?.id || 0)
  }, [classes])

  useEffect(() => {
    if (preferredClassId && classes.some(c => c.id === preferredClassId)) setClassId(preferredClassId)
  }, [preferredClassId, classes])

  useEffect(() => {
    if (!classId) return
    let cancelled = false
    setLoadingPeriods(true)
    setPeriodError(false)
    setPeriods([])
    setPeriodKey('')
    void reportApi.exportPeriods(classId, reportType).then(response => {
      if (cancelled) return
      setPeriods(response.data.data)
      const first = response.data.data.find(p => p.period_start === preferredPeriod?.period_start && p.period_end === preferredPeriod?.period_end) || response.data.data[0]
      setPeriodKey(first ? `${first.period_start}/${first.period_end}` : '')
    }).catch(() => { if (!cancelled) setPeriodError(true) }).finally(() => {
      if (!cancelled) setLoadingPeriods(false)
    })
    return () => { cancelled = true }
  }, [classId, reportType, preferredPeriod?.period_start, preferredPeriod?.period_end, refresh])

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const response = await reportApi.exportTask(taskId)
        if (cancelled) return
        setTask(response.data.data)
        setPollError(false)
        if (['pending', 'running'].includes(response.data.data.status)) timer = setTimeout(() => void poll(), 2000)
      } catch { if (!cancelled) setPollError(true) }
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [taskId, refresh])

  const create = async () => {
    const period = periods.find(p => `${p.period_start}/${p.period_end}` === periodKey)
    if (!period) return
    setSubmitting(true)
    try {
      const response = await reportApi.exportClass(classId, period, reportType, format)
      setTask(response.data.data)
      setTaskId(response.data.data.id)
      try { localStorage.setItem(storageKey, String(response.data.data.id)) } catch { /* Export still works without storage. */ }
    } catch { /* The API client displays the error. */ } finally { setSubmitting(false) }
  }
  const download = async () => {
    if (!task) return
    setDownloading(true)
    try {
      const response = await reportApi.downloadClass(task.id)
      saveDownload(response.data, `${task.class_name}_营养${task.report_type === 'personal_monthly' ? '月报' : '周报'}${task.report_format === 'brief' ? '_简报' : ''}_${task.period_start}_${task.period_end}.zip`)
    } catch { /* The API client displays the error. */ } finally { setDownloading(false) }
  }
  const active = submitting || (task && ['pending', 'running'].includes(task.status))
  if (!classes.length) return null
  return <section aria-label="班级报告导出" className="space-y-4">
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">报告版本<select aria-label="导出报告版本" value={format} disabled={!!active} onChange={e => setFormat(e.target.value as ReportFormat)} className="min-h-10 rounded-lg border border-border bg-background px-3 text-sm text-foreground"><option value="detailed">详细报告（现有版本）</option><option value="brief">简要报告</option></select></label>
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-muted-foreground">班级<select aria-label="导出班级" value={classId} disabled={!!active} onChange={e => setClassId(Number(e.target.value))} className="min-h-10 rounded-lg border border-border bg-background px-3 text-sm text-foreground">{classes.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs text-muted-foreground">{cadence === 'weekly' ? '周报周期' : '月报周期'}<select aria-label="导出周期" value={periodKey} disabled={loadingPeriods || !!active} onChange={e => setPeriodKey(e.target.value)} className="min-h-10 rounded-lg border border-border bg-background px-3 text-sm text-foreground">
        {!periods.length && <option value="">{loadingPeriods ? '加载中…' : periodError ? '加载失败，请重试' : '暂无可导出周期'}</option>}
        {periods.map(p => <option key={`${p.period_start}/${p.period_end}`} value={`${p.period_start}/${p.period_end}`}>{p.period_start} — {p.period_end}</option>)}
      </select></label>
    </div>
    <div className="flex flex-col gap-3 rounded-xl bg-secondary/50 p-4 sm:flex-row sm:items-center">
      <p className="flex-1 text-xs leading-6 text-muted-foreground">每位学生一张 PNG，打包为 ZIP 文件。缺失和失败情况附在清单中，下载链接保留 24 小时。</p>
      <button type="button" onClick={() => void create()} disabled={!periodKey || !!active || loadingPeriods} className="flex min-h-10 shrink-0 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2">{active ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackageOpen className="h-4 w-4" />}导出全班 PNG</button>
    </div>
    {(pollError || periodError) && <div className="mt-3 text-xs text-health-red">{pollError ? '任务状态获取失败。' : '周期加载失败。'}<button type="button" className="ml-2 underline" onClick={() => setRefresh(v => v + 1)}>重试</button></div>}
    {task && <div className="mt-4 rounded-lg bg-secondary/60 p-3 text-xs" role="status" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-medium">{task.class_name} · {task.period_start} — {task.period_end} · {task.report_format === 'brief' ? '简要报告' : '详细报告'}</span><span>{task.status === 'pending' ? '排队中' : task.status === 'running' ? '生成中' : task.status === 'failed' ? '导出失败' : task.expired ? '下载已过期' : task.status === 'partial' ? '部分完成' : '导出完成'}</span></div>
      <progress aria-label="班级导出进度" className="mt-3 h-2 w-full accent-green-700" value={task.processed_count} max={task.total_count || 1} />
      <p className="mt-2 text-muted-foreground">已处理 {task.processed_count}/{task.total_count} 人 · 成功 {task.success_count} · 缺失 {task.missing_count} · 失败 {task.error_count}</p>
      {task.error_message && <p className="mt-2 text-health-red">{task.error_message}</p>}
      {task.download_ready && <button type="button" disabled={downloading} onClick={() => void download()} className="mt-3 flex min-h-9 items-center gap-2 rounded-lg bg-primary px-3 text-primary-foreground disabled:opacity-50"><Download className="h-3.5 w-3.5" />{downloading ? '下载中…' : '下载 ZIP 压缩包'}</button>}
    </div>}
  </section>
}
