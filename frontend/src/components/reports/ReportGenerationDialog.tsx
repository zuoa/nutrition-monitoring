import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { CalendarDays, Loader2, Sparkles, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { reportApi } from '@/api/client'
import { previousReportPeriod, reportPeriodRange } from '@/lib/reportPeriods'
import type { ReportCadence } from '@/lib/reportPeriods'
import { cn } from '@/lib/utils'
import type { ReportScope } from './ReportOrganizationTree'

type Props = {
  scope: ReportScope
  scopeLabel: string
  cadence: ReportCadence
  preferredPeriod: string
  onSubmitted: (cadence: ReportCadence, period: string, taskId: number) => void
}

const primaryClass = 'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2'

export default function ReportGenerationDialog({ scope, scopeLabel, cadence, preferredPeriod, onSubmitted }: Props) {
  const [open, setOpen] = useState(false)
  const [generationCadence, setGenerationCadence] = useState(cadence)
  const [week, setWeek] = useState('')
  const [month, setMonth] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [generating, setGenerating] = useState(false)
  const submittingRef = useRef(false)
  const period = generationCadence === 'weekly' ? week : month
  const range = reportPeriodRange(generationCadence, period)
  const periodLabel = generationCadence === 'weekly' ? '周报' : '月报'
  const scopeDescription = scope === 'personal' ? '学生个人' : scopeLabel

  const changeOpen = (value: boolean) => {
    if (submittingRef.current) return
    if (value) {
      setGenerationCadence(cadence)
      setWeek(cadence === 'weekly' && preferredPeriod ? preferredPeriod : previousReportPeriod('weekly'))
      setMonth(cadence === 'monthly' && preferredPeriod ? preferredPeriod : previousReportPeriod('monthly'))
      setOverwrite(false)
    }
    setOpen(value)
  }

  const generateReport = async (event: FormEvent) => {
    event.preventDefault()
    if (!range || submittingRef.current) return
    submittingRef.current = true
    setGenerating(true)
    try {
      const response = await reportApi.generate(`${scope}_${generationCadence}`, range.start, range.end, overwrite)
      toast.success(`${scopeLabel}${periodLabel}生成任务已提交，可在页面查看进度`, { id: 'report-generation' })
      setOpen(false)
      onSubmitted(generationCadence, period, response.data.data.id)
    } catch { /* The API client displays the error. */ } finally {
      submittingRef.current = false
      setGenerating(false)
    }
  }

  return <Dialog.Root open={open} onOpenChange={changeOpen}>
    <Dialog.Trigger asChild>
      <button type="button" className={primaryClass}><Sparkles className="h-4 w-4" />生成{scopeLabel}{cadence === 'weekly' ? '周报' : '月报'}</button>
    </Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
      <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl focus:outline-none sm:p-6">
        <Dialog.Title className="pr-8 text-lg font-semibold">生成{scopeLabel}营养报告</Dialog.Title>
        <Dialog.Description className="mt-1.5 text-xs leading-5 text-muted-foreground">选择报告周期，批量生成全部{scopeDescription}的营养报告。</Dialog.Description>
        <Dialog.Close asChild><button type="button" aria-label="关闭报告生成" disabled={generating} className="absolute right-4 top-4 rounded-lg p-2 text-muted-foreground hover:bg-secondary disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-primary"><X className="h-4 w-4" /></button></Dialog.Close>
        <form onSubmit={event => void generateReport(event)} className="mt-6 space-y-5">
          <fieldset disabled={generating} className="space-y-4">
            <legend className="mb-2 text-xs font-medium">报告周期</legend>
            <div className="flex rounded-lg bg-secondary p-1" role="group" aria-label="生成报告频率">
              {(['weekly', 'monthly'] as const).map(value => <button key={value} type="button" aria-pressed={generationCadence === value} onClick={() => setGenerationCadence(value)}
                className={cn('min-h-10 flex-1 rounded-md px-4 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', generationCadence === value ? 'bg-card font-semibold text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground')}>{value === 'weekly' ? '周报' : '月报'}</button>)}
            </div>
            <label className="flex min-w-0 flex-col gap-2 text-xs font-medium">
              {generationCadence === 'weekly' ? '选择周（周一至周五）' : '选择月份'}
              <input key={generationCadence} type={generationCadence === 'weekly' ? 'week' : 'month'} aria-label={generationCadence === 'weekly' ? '生成报告周' : '生成报告月份'}
                required placeholder={generationCadence === 'weekly' ? 'YYYY-Www' : 'YYYY-MM'} value={period}
                onChange={event => generationCadence === 'weekly' ? setWeek(event.target.value) : setMonth(event.target.value)}
                className="min-h-11 w-full min-w-0 rounded-lg border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/15" />
            </label>
            <p className="flex items-center gap-2 text-xs leading-5 text-muted-foreground" aria-live="polite"><CalendarDays className="h-4 w-4 shrink-0" />{range ? `${range.start} 至 ${range.end}` : '请选择有效的报告周期'}</p>
            <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border p-4">
              <input type="checkbox" checked={overwrite} onChange={event => setOverwrite(event.target.checked)} aria-describedby="report-overwrite-description" className="mt-0.5 h-4 w-4 shrink-0 accent-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" />
              <span><span className="block text-xs font-medium">覆盖已生成报告</span><span id="report-overwrite-description" className="mt-1 block text-[11px] leading-5 text-muted-foreground">勾选后重新生成并更新同一周期的已有报告；未勾选时跳过已有报告，只生成缺失报告。</span></span>
            </label>
          </fieldset>
          <div className="rounded-lg bg-primary/5 px-3 py-2.5 text-[11px] leading-5 text-muted-foreground">本次生成范围：全部{scopeDescription} · {periodLabel}{overwrite ? ' · 覆盖已有报告' : ' · 仅生成缺失报告'}</div>
          <p className="text-[11px] leading-5 text-muted-foreground">生成时使用已保存的营养素系数。调整系数后，请勾选“覆盖已生成报告”更新本周期报告。</p>
          <div className="flex justify-end gap-2 border-t border-border pt-4">
            <Dialog.Close asChild><button type="button" disabled={generating} className="min-h-10 rounded-lg border border-border px-4 text-xs font-medium hover:bg-secondary disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">取消</button></Dialog.Close>
            <button type="submit" disabled={generating || !range} className={primaryClass}>{generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{generating ? '正在提交…' : overwrite ? '生成并覆盖' : '开始生成'}</button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}
