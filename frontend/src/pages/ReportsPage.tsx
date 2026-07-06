import { useEffect, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  TrendingUp, AlertTriangle, CheckCircle2, Send, RefreshCw,
  Flame, Beef, Wheat, Leaf, Droplet, Droplets,
  Soup, Salad, Milk, Apple, CookingPot, Drumstick,
  BarChart3, CalendarDays, Heart, Lightbulb, UtensilsCrossed,
} from 'lucide-react'
import { reportApi, adminApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, scoreColor, fmtDate } from '@/lib/utils'
import type { Student, PersonalReportContent, Report, NutrientData } from '@/types'
import toast from 'react-hot-toast'

// ─── Report identity (adjust to your deployment) ─────────────────────────────
const REPORT_ORG = '杭州第四中学江东校区'
const REPORT_TITLE = '学生营养食谱周报'
const REPORT_SUBTITLES = ['营养摄入分析', '膳食结构评估', '下周优化建议']

// ─── Nutrient metadata ────────────────────────────────────────────────────────
type NutrientKey = keyof NutrientData
interface NutrientMeta { key: NutrientKey; label: string; unit: string; icon: LucideIcon }

const NUTRIENT_META: NutrientMeta[] = [
  { key: 'calories', label: '热量', unit: 'kcal', icon: Flame },
  { key: 'protein', label: '蛋白质', unit: 'g', icon: Beef },
  { key: 'carbohydrate', label: '碳水', unit: 'g', icon: Wheat },
  { key: 'fiber', label: '膳食纤维', unit: 'g', icon: Leaf },
  { key: 'fat', label: '脂肪', unit: 'g', icon: Droplet },
  { key: 'sodium', label: '钠', unit: 'mg', icon: Droplets },
]

const STRUCTURE_META: { key: NutrientKey; label: string; icon: LucideIcon }[] = [
  { key: 'fiber', label: '蔬菜水果', icon: Apple },
  { key: 'protein', label: '优质蛋白', icon: Milk },
  { key: 'fat', label: '油脂适量', icon: Droplet },
  { key: 'sodium', label: '盐分控制', icon: Droplets },
]

const SUGGESTION_ICONS: LucideIcon[] = [Milk, Leaf, Droplet, CookingPot, Wheat, Drumstick, Apple, Salad]
const ALERT_ICON: Record<string, LucideIcon> = {
  deficiency: Leaf, excess: Droplet, no_meal: Soup, diversity: Salad,
}
const ALERT_LABEL: Record<string, string> = {
  deficiency: '摄入不足', excess: '摄入偏多', no_meal: '就餐不规律', diversity: '膳食单一',
}

type Tone = 'low' | 'ok' | 'high'
const TONE_BADGE: Record<Tone, string> = {
  low: 'bg-health-red/10 text-health-red ring-health-red/20',
  ok: 'bg-health-green/10 text-health-green ring-health-green/20',
  high: 'bg-health-amber/10 text-health-amber ring-health-amber/20',
}
const TONE_DOT: Record<Tone, string> = {
  low: 'bg-health-red', ok: 'bg-health-green', high: 'bg-health-amber',
}

// ─── Derived metrics ──────────────────────────────────────────────────────────
function ratioOf(avg: number, rec: number): number {
  return rec > 0 ? avg / rec : 0
}

function coreStatus(ratio: number): { tone: Tone; label: string } {
  if (ratio < 0.8) return { tone: 'low', label: '不达标' }
  if (ratio > 1.2) return { tone: 'high', label: '超标' }
  return { tone: 'ok', label: '达标' }
}

function structureStatus(ratio: number): { label: string; ok: boolean } {
  if (ratio >= 0.8 && ratio <= 1.2) return { label: '达标', ok: true }
  return { label: ratio > 1.2 ? '偏高' : '一般', ok: false }
}

function compliancePct(avg: NutrientData, rec: NutrientData): number {
  const scores = NUTRIENT_META.map(({ key }) => {
    const r = ratioOf(avg[key], rec[key])
    if (r >= 0.8 && r <= 1.2) return 100
    if (r < 0.8) return (r / 0.8) * 100
    return Math.max(0, 100 - (r - 1.2) * 150)
  })
  return Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)
}

function energySplit(avg: NutrientData) {
  const eCarb = (avg.carbohydrate || 0) * 4
  const eProtein = (avg.protein || 0) * 4
  const eFat = (avg.fat || 0) * 9
  const total = eCarb + eProtein + eFat || 1
  return [
    { key: 'carb', label: '碳水', value: Math.round((eCarb / total) * 100), color: '#f59e0b' },
    { key: 'fat', label: '脂肪', value: Math.round((eFat / total) * 100), color: '#2FAF7F' },
    { key: 'protein', label: '蛋白质', value: Math.round((eProtein / total) * 100), color: '#2563eb' },
  ]
}

function overallAssessment(pct: number): { label: string; tone: Tone } {
  if (pct >= 85) return { label: '较均衡', tone: 'ok' }
  if (pct >= 70) return { label: '基本均衡', tone: 'high' }
  return { label: '待改善', tone: 'low' }
}

function scoreBadgeClass(score: number): string {
  if (score >= 90) return 'bg-health-green/10 text-health-green ring-health-green/20'
  if (score >= 75) return 'bg-health-blue/10 text-health-blue ring-health-blue/20'
  if (score >= 60) return 'bg-health-amber/10 text-health-amber ring-health-amber/20'
  return 'bg-health-red/10 text-health-red ring-health-red/20'
}

function buildStudentFromLatestReport(studentId: number, report: Report | null): Student | null {
  const content = report?.content as PersonalReportContent | null | undefined
  if (!content) return null

  return {
    id: studentId,
    student_no: String(studentId),
    name: content.student_name,
    class_id: content.class_name || `student-${studentId}`,
    class_name: content.class_name,
    is_active: true,
    latest_report: {
      report_id: report?.id || 0,
      overall_score: content.overall_score,
      alert_count: content.alerts.length,
      period_start: report?.period_start,
      period_end: report?.period_end,
      summary: report?.summary,
      created_at: report?.created_at,
    },
  }
}

// ─── Charts (inline SVG, matches the flat reference style) ────────────────────
function Donut({ value, size = 150, stroke = 14, color = '#2FAF7F', track = '#eef0ec' }: {
  value: number; size?: number; stroke?: number; color?: string; track?: string
}) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const offset = c * (1 - Math.max(0, Math.min(100, value)) / 100)
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color}
          strokeWidth={stroke} strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-3xl font-mono font-semibold text-foreground">{value}<span className="text-base">%</span></span>
        <span className="text-[11px] text-muted-foreground mt-0.5">整体达标率</span>
      </div>
    </div>
  )
}

function DonutPie({ segments, size = 160, stroke = 30, children }: {
  segments: { value: number; color: string }[]; size?: number; stroke?: number; children?: React.ReactNode
}) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  let acc = 0
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        {segments.map((s, i) => {
          const len = c * (s.value / 100)
          const el = (
            <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none"
              stroke={s.color} strokeWidth={stroke}
              strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-acc} />
          )
          acc += len
          return el
        })}
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  )
}

function SectionTitle({ n, title }: { n: number; title: string }) {
  return (
    <div className="flex items-center gap-2 bg-health-green text-white px-4 py-2.5">
      <span className="w-5 h-5 rounded-full bg-white/25 flex items-center justify-center text-xs font-semibold">{n}</span>
      <h3 className="text-sm font-semibold tracking-wide">{title}</h3>
    </div>
  )
}

function Section({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('bg-card border border-health-green/30 rounded-xl overflow-hidden', className)}>
      {children}
    </section>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function ReportsPage() {
  const { user, hasRole } = useAuth()
  const [students, setStudents] = useState<Student[]>([])
  const [selectedStudent, setSelectedStudent] = useState<Student | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [content, setContent] = useState<PersonalReportContent | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedGradeId, setSelectedGradeId] = useState('')
  const [selectedClassId, setSelectedClassId] = useState('')

  const loadReport = async (student: Student) => {
    setSelectedStudent(student)
    setLoading(true)
    try {
      const res = await reportApi.studentLatest(student.id)
      const r: Report | null = res.data.data
      setReport(r)
      setContent(r?.content as PersonalReportContent | null)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    let cancelled = false

    async function loadStudents() {
      if (hasRole('admin', 'teacher', 'grade_leader')) {
        const res = await adminApi.students({ page_size: 200, include_latest_report: true })
        if (!cancelled) setStudents(res.data.data.items)
        return
      }

      if (hasRole('parent') && user?.student_ids?.length) {
        const results = await Promise.allSettled(
          user.student_ids.map(async studentId => {
            const res = await reportApi.studentLatest(studentId)
            return buildStudentFromLatestReport(studentId, res.data.data as Report | null)
          })
        )
        const myStudents = results
          .flatMap(result => result.status === 'fulfilled' && result.value ? [result.value] : [])
          .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

        if (!cancelled) {
          setStudents(myStudents)
          if (myStudents.length > 0) void loadReport(myStudents[0])
        }
        return
      }

      if (!cancelled) setStudents([])
    }

    void loadStudents()
    return () => { cancelled = true }
  }, [hasRole, user?.student_ids])

  const generateReport = async () => {
    setGenerating(true)
    try {
      await reportApi.generate('personal_weekly')
      toast.success('报告生成任务已提交，请稍后刷新')
    } finally { setGenerating(false) }
  }

  const pushReport = async () => {
    if (!report) return
    await reportApi.push(report.id)
    toast.success('推送任务已提交')
  }

  const gradeOptions = Array.from(new Map(
    students
      .filter(student => student.grade_id)
      .map(student => [student.grade_id as string, student.grade_name || student.grade_id || ''])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

  const classOptions = Array.from(new Map(
    students
      .filter(student => (!selectedGradeId || student.grade_id === selectedGradeId) && student.class_id)
      .map(student => [student.class_id, student.class_name || student.class_id])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

  const normalizedSearch = search.trim().toLowerCase()
  const filteredStudents = students.filter(student => {
    if (selectedGradeId && student.grade_id !== selectedGradeId) return false
    if (selectedClassId && student.class_id !== selectedClassId) return false
    if (!normalizedSearch) return true

    return [
      student.name,
      student.student_no,
      student.class_name,
      student.grade_name,
    ].some(value => String(value || '').toLowerCase().includes(normalizedSearch))
  })

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">营养报告</h1>
          <p className="text-sm text-muted-foreground mt-0.5">学生个人营养摄入分析</p>
        </div>
        <div className="flex gap-2">
          {hasRole('admin') && (
            <button onClick={generateReport} disabled={generating} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-3.5 h-3.5', generating && 'animate-spin')} />生成报告
            </button>
          )}
          {report && (
            <button onClick={pushReport} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
              <Send className="w-3.5 h-3.5" />推送报告
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        {/* Student list */}
        <div className="lg:col-span-1">
          <div className="bg-card border border-border rounded-xl overflow-hidden sticky top-4">
            <div className="p-3 border-b border-border space-y-3">
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索学生..."
                className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20" />
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={selectedGradeId}
                  onChange={e => {
                    setSelectedGradeId(e.target.value)
                    setSelectedClassId('')
                  }}
                  className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                >
                  <option value="">全部年级</option>
                  {gradeOptions.map(option => (
                    <option key={option.id} value={option.id}>{option.name}</option>
                  ))}
                </select>
                <select
                  value={selectedClassId}
                  onChange={e => setSelectedClassId(e.target.value)}
                  className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-50"
                  disabled={classOptions.length === 0}
                >
                  <option value="">全部班级</option>
                  {classOptions.map(option => (
                    <option key={option.id} value={option.id}>{option.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>当前范围 {filteredStudents.length} 人</span>
                <span>右侧分值取最新周报</span>
              </div>
            </div>
            <div className="overflow-y-auto max-h-[30rem] sm:max-h-[34rem] lg:max-h-[calc(100vh-10rem)]">
              {filteredStudents.length === 0 ? (
                <div className="p-4 text-center text-xs text-muted-foreground">暂无学生</div>
              ) : filteredStudents.map(s => (
                <button key={s.id} onClick={() => loadReport(s)}
                  className={cn('w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-secondary transition-colors border-b border-border/50 last:border-0',
                    selectedStudent?.id === s.id && 'bg-secondary/90'
                  )}>
                  <div className="w-8 h-8 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium flex-shrink-0">
                    {s.name[0]}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-medium truncate">{s.name}</div>
                      {!!s.latest_report?.alert_count && (
                        <span className="rounded-full bg-health-amber/10 px-1.5 py-0.5 text-[10px] text-health-amber">
                          预警 {s.latest_report.alert_count}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {[s.grade_name, s.class_name].filter(Boolean).join(' · ') || '未分班'}
                    </div>
                    <div className="text-[10px] text-muted-foreground font-mono">{s.student_no}</div>
                  </div>
                  <div className="shrink-0 text-right">
                    {typeof s.latest_report?.overall_score === 'number' ? (
                      <>
                        <div className={cn(
                          'inline-flex min-w-[2.75rem] items-center justify-center rounded-full px-2 py-1 text-xs font-mono ring-1',
                          scoreBadgeClass(s.latest_report.overall_score)
                        )}>
                          {s.latest_report.overall_score}
                        </div>
                        <div className="mt-1 text-[10px] text-muted-foreground font-mono">
                          {fmtDate(s.latest_report.period_end)}
                        </div>
                      </>
                    ) : (
                      <div className="text-[10px] text-muted-foreground">待生成</div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Report content */}
        <div className="lg:col-span-3 space-y-4">
          {!selectedStudent ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center">
              <TrendingUp className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
              <p className="text-sm text-muted-foreground">请从左侧选择学生查看报告</p>
            </div>
          ) : loading ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center text-muted-foreground text-sm">加载中...</div>
          ) : !content ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center">
              <p className="text-sm text-muted-foreground">该学生暂无营养报告，请先生成报告</p>
            </div>
          ) : (
            <ReportCard content={content} report={report} />
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Report card (matches the reference weekly-report layout) ────────────────
function ReportCard({ content, report }: { content: PersonalReportContent; report: Report | null }) {
  const avg = content.avg_nutrients
  const rec = content.recommended_nutrients
  const pct = compliancePct(avg, rec)
  const assessment = overallAssessment(pct)
  const energy = energySplit(avg)
  const score = content.overall_score ?? 0

  const periodLabel = `${fmtDate(content.period_start)} — ${fmtDate(content.period_end)}`

  return (
    <div className="space-y-4">
      {/* Header */}
      <Section className="bg-gradient-to-br from-health-green/5 to-card">
        <div className="px-5 sm:px-7 py-5 sm:py-6">
          <div className="flex items-center gap-4">
            {/* Emblem */}
            <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-full bg-health-green/10 border border-health-green/25 flex items-center justify-center flex-shrink-0">
              <Leaf className="w-7 h-7 sm:w-8 sm:h-8 text-health-green" />
            </div>
            {/* Title */}
            <div className="flex-1 min-w-0">
              <p className="text-xs sm:text-sm font-medium text-health-green tracking-wide truncate">{REPORT_ORG}</p>
              <h2 className="text-xl sm:text-3xl font-bold text-foreground leading-tight mt-0.5">{REPORT_TITLE}</h2>
              <div className="mt-1.5 text-[11px] sm:text-xs text-muted-foreground">
                {REPORT_SUBTITLES.join('  |  ')}
              </div>
            </div>
            {/* Score badge */}
            <div className="hidden sm:flex flex-col items-center justify-center px-4 py-2 rounded-xl bg-card border border-health-green/25">
              <span className={cn('text-2xl font-mono font-semibold', scoreColor(score))}>{score}</span>
              <span className="text-[10px] text-muted-foreground mt-0.5">综合评分</span>
            </div>
          </div>

          {/* Meta line */}
          <div className="mt-4 pt-4 border-t border-health-green/15 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <UtensilsCrossed className="w-3.5 h-3.5 text-health-green" />
              报告对象：<span className="text-foreground font-medium">{content.student_name}</span>
              {content.class_name ? <span className="text-foreground/70"> · {content.class_name}</span> : null}
            </span>
            <span className="flex items-center gap-1.5">
              <CalendarDays className="w-3.5 h-3.5 text-health-green" />
              统计周期：<span className="text-foreground/80">{periodLabel}</span>
            </span>
            <span>就餐 <span className="font-mono font-medium text-foreground">{content.meal_days}</span> / {content.total_days} 天</span>
          </div>
        </div>
      </Section>

      {/* 1. Core nutrient indicators */}
      <Section>
        <SectionTitle n={1} title="学生摄入核心指标" />
        <div className="p-4 sm:p-5">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {NUTRIENT_META.map(({ key, label, unit, icon: Icon }) => {
              const a = avg[key] || 0
              const r = rec[key] || 0
              const ratio = ratioOf(a, r)
              const { tone, label: statusLabel } = coreStatus(ratio)
              return (
                <div key={key} className="flex items-center gap-3 rounded-xl border border-border/70 bg-background/40 px-3 py-2.5">
                  <div className={cn('w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0', TONE_BADGE[tone])}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-muted-foreground truncate">{label}</span>
                      <span className={cn('inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1', TONE_BADGE[tone])}>
                        <span className={cn('w-1.5 h-1.5 rounded-full', TONE_DOT[tone])} />
                        {statusLabel}
                      </span>
                    </div>
                    <div className="mt-0.5 text-sm font-mono">
                      <span className="text-foreground">{Math.round(a)}</span>
                      <span className="text-[10px] text-muted-foreground"> / {Math.round(r)} {unit}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="mt-3 flex items-center gap-4 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-health-green" />达标</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-health-amber" />超标</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-health-red" />不达标</span>
            <span className="ml-auto">数值：实际摄入 / 推荐摄入</span>
          </div>
        </div>
      </Section>

      {/* 2. Compliance rate */}
      <Section>
        <SectionTitle n={2} title="营养达标率" />
        <div className="p-4 sm:p-5 flex flex-col sm:flex-row items-center gap-6">
          <Donut value={pct} />
          <div className="flex-1 text-center sm:text-left">
            <div className="inline-flex items-center gap-2">
              <span className="text-sm text-muted-foreground">整体评估</span>
              <span className={cn('inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-sm font-medium', TONE_BADGE[assessment.tone])}>
                <span className={cn('w-1.5 h-1.5 rounded-full', TONE_DOT[assessment.tone])} />
                {assessment.label}
              </span>
            </div>
            <p className="mt-2 text-sm text-foreground/80 leading-relaxed">
              {report?.summary || `本周整体营养${assessment.label}，实际摄入与推荐值整体偏差较小，建议关注标红的营养素项目。`}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-muted-foreground">
              <span className="flex items-center gap-1.5"><CheckCircle2 className="w-3.5 h-3.5 text-health-green" />较好</span>
              <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-health-amber rounded" />一般</span>
              <span className="flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5 text-health-red" />不足</span>
            </div>
          </div>
        </div>
      </Section>

      {/* 3. Energy split + dietary structure */}
      <Section>
        <SectionTitle n={3} title="营养搭配比例" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 p-4 sm:p-5">
          {/* Pie */}
          <div className="flex flex-col items-center">
            <h4 className="self-start text-sm font-medium text-foreground/90">三大营养素供能比例</h4>
            <div className="mt-3 flex items-center gap-4">
              <DonutPie segments={energy}>
                <Leaf className="w-6 h-6 text-health-green" />
              </DonutPie>
              <div className="space-y-2">
                {energy.map(e => (
                  <div key={e.key} className="flex items-center gap-2 text-xs">
                    <span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: e.color }} />
                    <span className="text-muted-foreground w-12">{e.label}</span>
                    <span className="font-mono font-medium text-foreground">{e.value}%</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          {/* Structure */}
          <div>
            <h4 className="text-sm font-medium text-foreground/90">膳食结构合理性</h4>
            <div className="mt-3 space-y-2">
              {STRUCTURE_META.map(({ key, label, icon: Icon }) => {
                const ratio = ratioOf(avg[key] || 0, rec[key] || 0)
                const { label: statusLabel, ok } = structureStatus(ratio)
                return (
                  <div key={key} className="flex items-center gap-3 rounded-lg border border-border/70 bg-background/40 px-3 py-2">
                    <Icon className={cn('w-4 h-4 flex-shrink-0', ok ? 'text-health-green' : 'text-health-amber')} />
                    <span className="flex-1 text-xs text-foreground/80">{label}</span>
                    <span className={cn('text-[11px] font-medium', ok ? 'text-health-green' : 'text-health-amber')}>{statusLabel}</span>
                  </div>
                )
              })}
            </div>
          </div>
          <div className="sm:col-span-2 flex items-start gap-2 rounded-lg bg-health-amber/5 border border-health-amber/20 px-3 py-2 text-xs text-foreground/80">
            <Lightbulb className="w-3.5 h-3.5 text-health-amber mt-0.5 flex-shrink-0" />
            <span>建议适度提升蔬菜水果和优质蛋白（奶类、豆制品）摄入，控制油脂与盐分，优化膳食结构。</span>
          </div>
        </div>
      </Section>

      {/* 4. Dining characteristics */}
      <Section>
        <SectionTitle n={4} title="学生就餐特征分析" />
        <div className="p-4 sm:p-5">
          {content.alerts.length === 0 ? (
            <div className="flex items-center gap-3 rounded-xl border border-health-green/25 bg-health-green/5 px-4 py-3 text-sm text-health-green">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
              本周就餐规律、膳食多样，营养摄入整体良好，请继续保持。
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {content.alerts.map((alert, i) => {
                const Icon = ALERT_ICON[alert.type] || UtensilsCrossed
                const tone = alert.type === 'excess' ? 'high' : 'low'
                return (
                  <div key={i} className="flex items-start gap-3 rounded-xl border border-border/70 bg-background/40 px-3 py-2.5">
                    <div className={cn('w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0', TONE_BADGE[tone])}>
                      <Icon className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-foreground">{ALERT_LABEL[alert.type] || '就餐提醒'}</span>
                        {alert.nutrient && (
                          <span className="text-[10px] text-muted-foreground">· {alert.nutrient}</span>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-muted-foreground leading-relaxed">{alert.message}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </Section>

      {/* 5. Conclusion + suggestions */}
      <Section>
        <SectionTitle n={5} title="本周结论与下周优化建议" />
        <div className="p-4 sm:p-5 space-y-4">
          {/* Conclusion */}
          <div className="rounded-xl bg-health-green text-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <CheckCircle2 className="w-4 h-4" />本周结论
            </div>
            <p className="mt-1.5 text-sm text-white/90 leading-relaxed">
              {report?.summary || `本周膳食整体${assessment.label}，综合评分 ${score} 分${content.alerts.length ? `，存在 ${content.alerts.length} 项需关注的摄入问题` : ''}。`}
            </p>
          </div>
          {/* Suggestions */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Lightbulb className="w-4 h-4 text-health-amber" />
              <span className="text-sm font-semibold text-foreground">下周优化建议</span>
            </div>
            {content.suggestions.length === 0 ? (
              <p className="text-xs text-muted-foreground">暂无建议，请保持当前膳食习惯。</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {content.suggestions.map((s, i) => {
                  const Icon = SUGGESTION_ICONS[i % SUGGESTION_ICONS.length]
                  return (
                    <div key={i} className="flex items-start gap-3 rounded-xl border border-border/70 bg-background/40 px-3 py-2.5">
                      <div className="w-8 h-8 rounded-lg bg-health-green/10 text-health-green flex items-center justify-center flex-shrink-0">
                        <Icon className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="text-[11px] font-mono text-muted-foreground">建议 {String(i + 1).padStart(2, '0')}</div>
                        <p className="text-xs text-foreground/80 leading-relaxed mt-0.5">{s}</p>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </Section>

      {/* Footer */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-2 px-2 py-3 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <BarChart3 className="w-3.5 h-3.5" />数据来源：学校营养管理系统
        </span>
        <span className="flex items-center gap-1.5">
          <CalendarDays className="w-3.5 h-3.5" />统计时间：{periodLabel}
        </span>
        <span className="flex items-center gap-1.5 text-health-green font-medium">
          <Heart className="w-3.5 h-3.5" />科学营养 · 健康成长
        </span>
      </div>
    </div>
  )
}
