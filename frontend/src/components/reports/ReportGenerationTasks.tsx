import { useEffect, useRef, useState } from 'react'
import { CircleAlert, Loader2, RefreshCw } from 'lucide-react'
import { reportApi } from '@/api/client'
import type { ReportGenerationTask } from '@/types'
import { cn } from '@/lib/utils'

const statusLabels = { pending: '排队中', running: '生成中', success: '已完成', partial: '部分失败', failed: '生成失败' }
const scopeLabels: Record<string, string> = { personal: '学生个人', class: '班级', grade: '年级', campus: '校区', school: '全校' }
const isActive = (task: ReportGenerationTask) => task.status === 'pending' || task.status === 'running'

export default function ReportGenerationTasks({ submittedTaskId, onFinished }: { submittedTaskId: number | null; onFinished: () => void }) {
  const [tasks, setTasks] = useState<ReportGenerationTask[]>([])
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  const previous = useRef(new Map<number, ReportGenerationTask['status']>())

  useEffect(() => {
    if (submittedTaskId !== null && !previous.current.has(submittedTaskId)) previous.current.set(submittedTaskId, 'pending')
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      let delay = 10000
      try {
        const response = await reportApi.generationTasks()
        if (cancelled) return
        const data = response.data.data
        const activeIds = new Set(data.filter(isActive).map(task => task.id))
        const finished = [...previous.current].some(([id, status]) => (status === 'pending' || status === 'running') && !activeIds.has(id))
        previous.current = new Map(data.map(task => [task.id, task.status]))
        setTasks(data)
        setError(false)
        if (finished) onFinished()
        if (data.some(isActive)) delay = 2000
      } catch {
        if (!cancelled) setError(true)
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void poll(), delay)
      }
    }
    void poll()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [submittedTaskId, retry, onFinished])

  if (!tasks.length && !error) return null
  return <section aria-label="报告生成任务" className="mb-6 space-y-3 rounded-xl border border-border bg-card p-4 sm:p-5">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="text-sm font-semibold">报告生成进度</h2>
      <span className="text-[11px] text-muted-foreground">{tasks.some(isActive) ? '自动更新，完成后刷新报告' : '最近生成任务'}</span>
    </div>
    {error ? <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-destructive" role="alert"><span>进度更新失败，正在重试。已提交的任务会继续执行。</span><button type="button" onClick={() => setRetry(value => value + 1)} className="inline-flex min-h-9 items-center gap-1 rounded px-2 hover:bg-secondary focus-visible:ring-2 focus-visible:ring-primary"><RefreshCw className="h-3 w-3" />重新获取</button></div> : null}
    {tasks.map(task => {
      const active = isActive(task)
      const percent = task.total_count ? Math.min(100, Math.floor(task.processed_count * 100 / task.total_count)) : task.status === 'success' ? 100 : 0
      const scope = scopeLabels[task.report_type.split('_')[0]] || '营养'
      const cadence = task.report_type.endsWith('monthly') ? '月报' : '周报'
      return <div key={task.id} className="space-y-2 border-t border-border pt-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="font-medium">{scope}{cadence} · {task.period_start} 至 {task.period_end}{task.overwrite ? ' · 覆盖已有报告' : ''}</span>
          <span role="status" className={cn('inline-flex items-center gap-1.5', task.status === 'failed' || task.status === 'partial' ? 'text-destructive' : 'text-muted-foreground')}>
            {active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : task.error_count || task.status === 'failed' ? <CircleAlert className="h-3.5 w-3.5" /> : null}
            {statusLabels[task.status]}{task.total_count > 0 ? ` · ${task.processed_count}/${task.total_count}（${percent}%）` : ''}
          </span>
        </div>
        <div role="progressbar" aria-label={`${scope}${cadence}生成进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={task.status === 'pending' ? undefined : percent} className="h-1.5 overflow-hidden rounded-full bg-secondary">
          <div className={cn('h-full rounded-full transition-[width] motion-reduce:transition-none', task.error_count || task.status === 'failed' ? 'bg-destructive' : 'bg-primary')} style={{ width: `${percent}%` }} />
        </div>
        <p className="text-[11px] leading-5 text-muted-foreground">已生成 {task.success_count} 份 · 跳过 {task.skipped_count} 份 · 失败 {task.error_count} 份{task.status === 'pending' ? ' · 等待任务开始' : task.status === 'success' && !task.total_count ? ' · 当前范围没有可生成的报告' : ''}</p>
        {task.error_message ? <p className="text-[11px] leading-5 text-destructive">{task.error_message}</p> : null}
      </div>
    })}
  </section>
}
