import { useCallback, useEffect, useMemo, useState } from 'react'
import { Camera, GitMerge, AlertTriangle, CheckCircle2, Clock, TrendingUp, RefreshCw } from 'lucide-react'
import { startOfMonth, startOfWeek } from 'date-fns'
import { analysisApi, reportApi } from '@/api/client'
import { cn, fmtLocalDateInput } from '@/lib/utils'
import type { DailySummary } from '@/types'
import toast from 'react-hot-toast'

type OverviewRange = 'day' | 'week' | 'month'

const OVERVIEW_RANGE_OPTIONS: Array<{ value: OverviewRange; label: string }> = [
  { value: 'day', label: '本日' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
]

function getOverviewRangeMeta(range: OverviewRange) {
  const now = new Date()
  const endDate = fmtLocalDateInput(now)
  const startDate = fmtLocalDateInput(
    range === 'week'
      ? startOfWeek(now, { weekStartsOn: 1 })
      : range === 'month'
        ? startOfMonth(now)
        : now,
  )
  const label = OVERVIEW_RANGE_OPTIONS.find(option => option.value === range)?.label || '本日'

  return {
    label,
    startDate,
    endDate,
    dateLabel: startDate === endDate ? endDate : `${startDate} 至 ${endDate}`,
  }
}

interface StatCardProps {
  label: string
  value: string | number
  sub?: string
  icon: React.ReactNode
  accent?: string
}

function StatCard({ label, value, sub, icon, accent = '' }: StatCardProps) {
  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-start justify-between mb-3">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{label}</span>
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${accent || 'bg-secondary'}`}>
          {icon}
        </div>
      </div>
      <div className="stat-number text-foreground">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-1.5">{sub}</div>}
    </div>
  )
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<DailySummary | null>(null)
  const [alerts, setAlerts] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [overviewRange, setOverviewRange] = useState<OverviewRange>('day')

  const today = useMemo(() => fmtLocalDateInput(), [])
  const rangeMeta = useMemo(() => getOverviewRangeMeta(overviewRange), [overviewRange])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [sumRes, alertRes] = await Promise.allSettled([
        analysisApi.summary({
          start_date: rangeMeta.startDate,
          end_date: rangeMeta.endDate,
        }),
        reportApi.alerts(),
      ])
      if (sumRes.status === 'fulfilled') setSummary(sumRes.value.data.data)
      else setSummary(null)
      if (alertRes.status === 'fulfilled') setAlerts(alertRes.value.data.data || [])
    } finally {
      setLoading(false)
    }
  }, [rangeMeta.endDate, rangeMeta.startDate])

  useEffect(() => { load() }, [load])

  const matchRate = summary
    ? summary.total_images > 0
      ? ((summary.matched / summary.total_images) * 100).toFixed(0)
      : '—'
    : '—'

  return (
    <div className="p-4 sm:p-6 space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold text-foreground">{rangeMeta.label}概览</h1>
          <p className="text-xs sm:text-sm text-muted-foreground mt-0.5">{rangeMeta.dateLabel}</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-border bg-card p-0.5">
            {OVERVIEW_RANGE_OPTIONS.map(option => (
              <button
                key={option.value}
                type="button"
                aria-pressed={overviewRange === option.value}
                onClick={() => setOverviewRange(option.value)}
                className={cn(
                  'h-8 px-3 rounded-md text-xs font-medium transition-colors',
                  overviewRange === option.value
                    ? 'bg-primary text-primary-foreground shadow-sm'
                    : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={load}
            className="flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline">刷新</span>
          </button>
        </div>
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="采集图片"
          value={summary?.total_images ?? '—'}
          sub={`待处理 ${summary?.pending ?? 0} 张`}
          icon={<Camera className="w-4 h-4 text-foreground" />}
          accent="bg-primary/10"
        />
        <StatCard
          label="已识别"
          value={summary?.identified ?? '—'}
          sub={`低置信 ${summary?.low_confidence_recognitions ?? 0} 条`}
          icon={<CheckCircle2 className="w-4 h-4 text-health-green" />}
          accent="bg-health-green/10"
        />
        <StatCard
          label="匹配成功"
          value={summary?.matched ?? '—'}
          sub={matchRate === '—' ? '暂无匹配率' : `匹配率 ${matchRate}%`}
          icon={<GitMerge className="w-4 h-4 text-health-blue" />}
          accent="bg-health-blue/10"
        />
        <StatCard
          label="营养预警"
          value={alerts.length}
          sub="需关注学生"
          icon={<AlertTriangle className={`w-4 h-4 ${alerts.length > 0 ? 'text-health-amber' : 'text-muted-foreground'}`} />}
          accent={alerts.length > 0 ? "bg-health-amber/10" : "bg-secondary"}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Analysis progress */}
        <div className="lg:col-span-2 bg-card border border-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
            <Clock className="w-4 h-4 text-muted-foreground" />
            {rangeMeta.label}分析进度
          </h2>
          {summary ? (
            <div className="space-y-3">
              {[
                { label: '图片采集', value: summary.total_images, color: 'bg-primary' },
                { label: '已识别', value: summary.identified, color: 'bg-health-green' },
                { label: '已匹配', value: summary.matched, color: 'bg-health-blue' },
                { label: '异常', value: summary.error, color: 'bg-health-red' },
              ].map(({ label, value, color }) => {
                const pct = summary.total_images > 0 ? (value / summary.total_images) * 100 : 0
                return (
                  <div key={label} className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground w-14 text-right">{label}</span>
                    <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
                      <div
                        className={`h-full ${color} rounded-full transition-all duration-500`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-xs font-mono tabular-nums w-8 text-right">{value}</span>
                  </div>
                )
              })}
            </div>
          ) : (
            <div className="h-24 flex items-center justify-center text-sm text-muted-foreground">
              {loading ? '加载中...' : `暂无${rangeMeta.label}数据`}
            </div>
          )}
        </div>

        {/* Alerts */}
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-muted-foreground" />
            营养预警
          </h2>
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-center">
              <CheckCircle2 className="w-6 h-6 text-health-green mb-2" />
              <p className="text-xs text-muted-foreground">暂无营养预警</p>
            </div>
          ) : (
            <div className="space-y-2">
              {alerts.slice(0, 5).map((alert, i) => (
                <div key={i} className="flex items-start gap-2 p-2 bg-health-amber/5 border border-health-amber/20 rounded-lg">
                  <AlertTriangle className="w-3.5 h-3.5 text-health-amber mt-0.5 flex-shrink-0" />
                  <div>
                    <span className="text-xs font-medium">{alert.student_name}</span>
                    <p className="text-xs text-muted-foreground">{alert.message}</p>
                  </div>
                </div>
              ))}
              {alerts.length > 5 && (
                <p className="text-xs text-muted-foreground text-center">还有 {alerts.length - 5} 条预警</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Quick actions */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-5">
        <h2 className="text-sm font-medium mb-4">快捷操作</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: '触发视频分析', icon: Camera, action: () => analysisApi.triggerAnalysis(today).then(() => toast.success('已触发分析任务')) },
            { label: '重新匹配', icon: GitMerge, action: () => {} },
            { label: '生成周报', icon: TrendingUp, action: () => {} },
            { label: '刷新概览', icon: RefreshCw, action: load },
          ].map(({ label, icon: Icon, action }) => (
            <button
              key={label}
              onClick={action}
              className="flex flex-col items-center gap-2 p-4 rounded-lg border border-border hover:bg-secondary transition-colors group"
            >
              <Icon className="w-5 h-5 text-muted-foreground group-hover:text-foreground transition-colors" />
              <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">{label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
