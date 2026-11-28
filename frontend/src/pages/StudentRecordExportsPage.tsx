import { useEffect, useState, type FormEvent } from 'react'
import { isAxiosError } from 'axios'
import { Activity, Download, FileSpreadsheet, Loader2, ReceiptText, Utensils } from 'lucide-react'
import { studentRecordExportApi, type StudentRecordExportDates } from '@/api/client'
import { saveDownload } from '@/lib/download'

const WORKSHEETS = [
  { icon: Utensils, title: '识别菜品', description: '每行一道菜，含就餐时间、餐次、菜名与识别置信度。' },
  { icon: ReceiptText, title: '消费流水', description: '每行一笔刷卡，含时间与金额，保留未匹配餐盘的流水。' },
  { icon: Activity, title: '运动记录', description: '每行一次运动或体测，含成绩、用时与设备原始指标。' },
]
const EMPTY_DATES = { date_from: '', date_to: '' }
const INPUT = 'w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60'

export default function StudentRecordExportsPage() {
  const [dates, setDates] = useState(EMPTY_DATES)
  const [defaults, setDefaults] = useState<(StudentRecordExportDates & { timezone: string }) | null>(null)
  const [studentNo, setStudentNo] = useState('')
  const [reload, setReload] = useState(0)
  const [defaultsFailed, setDefaultsFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<{ error: boolean; message: string } | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    studentRecordExportApi.defaults(controller.signal).then(({ data }) => {
      if (controller.signal.aborted) return
      setDefaults(data.data)
      setDates({ date_from: data.data.date_from, date_to: data.data.date_to })
    }).catch(() => {
      if (!controller.signal.aborted) setDefaultsFailed(true)
    })
    return () => controller.abort()
  }, [reload])

  const dateOrderInvalid = Boolean(dates.date_from && dates.date_to && dates.date_from > dates.date_to)

  const refreshDefaults = () => {
    setDefaults(null)
    setDefaultsFailed(false)
    setFeedback(null)
    setReload(previous => previous + 1)
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (busy || !defaults || !dates.date_from || !dates.date_to || dateOrderInvalid) return
    setBusy(true)
    setFeedback(null)
    try {
      const { data } = await studentRecordExportApi.download({ ...dates, student_no: studentNo.trim() })
      saveDownload(data, `学生饮食运动明细_${dates.date_from}至${dates.date_to}.xlsx`)
      setFeedback({ error: false, message: 'Excel 下载已发起。' })
    } catch (error) {
      const detail = isAxiosError<{ message?: string }>(error) ? error.response?.data?.message : undefined
      setFeedback({ error: true, message: detail || '导出失败，请重试或缩短日期范围。' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 md:p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">原始数据导出</h1>
        <p className="mt-1.5 text-sm leading-6 text-muted-foreground">将识别菜品、刷卡流水和运动明细下载为 Excel，三张表均可按学号筛选。</p>
      </header>

      <div className="grid items-start gap-5 xl:grid-cols-[1.2fr_1fr]">
        <form onSubmit={submit} className="rounded-xl border border-border bg-card" aria-busy={busy}>
          <div className="space-y-5 p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-base font-semibold">导出范围</h2>
              <button type="button" disabled={!defaults || busy} onClick={refreshDefaults}
                className="min-h-9 rounded-md border border-border px-3 text-xs font-medium hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
                最近 14 天
              </button>
            </div>

            <fieldset disabled={!defaults || busy} className="min-w-0 space-y-5">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex min-w-0 flex-col gap-1.5 text-sm font-medium">
                  开始日期
                  <input className={INPUT} type="date" required value={dates.date_from} onChange={event => {
                    setDates({ ...dates, date_from: event.target.value })
                    setFeedback(null)
                  }} />
                </label>
                <label className="flex min-w-0 flex-col gap-1.5 text-sm font-medium">
                  结束日期
                  <input className={INPUT} type="date" required min={dates.date_from || undefined} value={dates.date_to} onChange={event => {
                    setDates({ ...dates, date_to: event.target.value })
                    setFeedback(null)
                  }} />
                </label>
              </div>
              <label className="flex flex-col gap-1.5 text-sm font-medium">
                <span>学号 <span className="text-xs font-normal text-muted-foreground">（可选）</span></span>
                <input className={INPUT} type="text" maxLength={64} value={studentNo} placeholder="留空导出全部人员" onChange={event => {
                  setStudentNo(event.target.value)
                  setFeedback(null)
                }} />
              </label>
            </fieldset>

            {defaults && <p className="text-xs leading-5 text-muted-foreground">包含开始和结束当天，按系统时区 {defaults.timezone} 取数。</p>}
            {!defaults && !defaultsFailed && <p role="status" className="text-sm text-muted-foreground">正在读取默认日期…</p>}
            {defaultsFailed && <div className="flex flex-wrap items-center gap-3 text-sm">
              <p role="alert" className="text-destructive">日期加载失败。</p>
              <button type="button" className="text-primary underline underline-offset-4" onClick={refreshDefaults}>重试</button>
            </div>}
          </div>

          <div className="flex flex-wrap items-center gap-3 rounded-b-xl border-t border-border bg-secondary/25 px-5 py-4 sm:px-6">
            <button type="submit" disabled={busy || !defaults || !dates.date_from || !dates.date_to || dateOrderInvalid}
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
              {busy ? <Loader2 aria-hidden="true" className="h-4 w-4 motion-safe:animate-spin" /> : <Download aria-hidden="true" className="h-4 w-4" />}
              {busy ? '正在生成 Excel…' : '下载 Excel'}
            </button>
            {dateOrderInvalid && <p role="alert" className="text-sm text-destructive">开始日期不能晚于结束日期。</p>}
            {feedback && <p role={feedback.error ? 'alert' : 'status'} className={feedback.error ? 'text-sm text-destructive' : 'text-sm text-muted-foreground'}>{feedback.message}</p>}
          </div>
        </form>

        <section aria-labelledby="worksheet-title" className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex items-center gap-2.5 border-b border-border bg-primary/5 px-5 py-4 sm:px-6">
            <FileSpreadsheet aria-hidden="true" className="h-5 w-5 text-primary" />
            <h2 id="worksheet-title" className="text-base font-semibold">工作表内容</h2>
          </div>
          <ul className="divide-y divide-border px-5 sm:px-6">
            {WORKSHEETS.map(({ icon: Icon, title, description }) => <li key={title} className="flex gap-3 py-4">
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-primary">
                <Icon aria-hidden="true" className="h-4 w-4" />
              </span>
              <div>
                <h3 className="text-sm font-semibold">{title}</h3>
                <p className="mt-0.5 text-sm leading-5 text-muted-foreground">{description}</p>
              </div>
            </li>)}
          </ul>
        </section>
      </div>
    </div>
  )
}
