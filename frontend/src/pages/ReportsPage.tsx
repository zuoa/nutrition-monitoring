import { useCallback, useEffect, useMemo, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  TrendingUp, AlertTriangle, CheckCircle2, Send, RefreshCw,
  Flame, Beef, Wheat, Leaf, Droplet, Droplets,
  Soup, Salad, Milk, Apple, CookingPot, Drumstick,
  BarChart3, CalendarDays, Heart, Lightbulb, UtensilsCrossed,
  Building2, GraduationCap, School as SchoolIcon, Users, UserRound,
} from 'lucide-react'
import { reportApi, adminApi, orgApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, scoreColor, fmtDate } from '@/lib/utils'
import { NUTRITION_FIELDS, UPPER_LIMIT_NUTRITION_KEYS, type NutritionKey } from '@/lib/nutrition'
import type {
  GroupReportContent,
  Student,
  PersonalReportContent,
  Report,
  NutrientData,
  NutrientSampleCounts,
} from '@/types'
import type { SchoolNode } from '@/components/students/adminTypes'
import toast from 'react-hot-toast'

// ─── Report identity (adjust to your deployment) ─────────────────────────────
const REPORT_ORG = '杭州第四中学江东校区'
const REPORT_TITLE = '学生营养食谱周报'
const REPORT_SUBTITLES = ['周期日均摄入', '膳食结构评估', '下周期优化建议']

type ReportScope = 'personal' | 'class' | 'grade' | 'campus'

const REPORT_SCOPES: Record<ReportScope, { label: string; description: string; icon: LucideIcon }> = {
  personal: { label: '个人', description: '周期日均摄入分析', icon: UserRound },
  class: { label: '班级', description: '班级整体分布分析', icon: Users },
  grade: { label: '年级', description: '年级整体分布分析', icon: GraduationCap },
  campus: { label: '校区', description: '校区整体分布分析', icon: Building2 },
}

// ─── Nutrient metadata ────────────────────────────────────────────────────────
type NutrientKey = keyof NutrientData
interface NutrientMeta { key: NutrientKey; label: string; unit: string; icon: LucideIcon }

const NUTRIENT_ICONS: Record<NutrientKey, LucideIcon> = {
  calories: Flame,
  protein: Beef,
  fat: Droplet,
  cholesterol: Heart,
  carbohydrate: Wheat,
  added_sugar: Apple,
  fiber: Leaf,
  sodium: Droplets,
  calcium: Milk,
  iron: Beef,
  zinc: Drumstick,
  vitamin_a: Leaf,
  vitamin_c: Apple,
  vitamin_d: Flame,
}

const NUTRIENT_META: NutrientMeta[] = NUTRITION_FIELDS.map(field => ({
  key: field.key as NutrientKey,
  label: field.label,
  unit: field.unit,
  icon: NUTRIENT_ICONS[field.key as NutrientKey],
}))

const STRUCTURE_META: { key: NutrientKey; label: string; icon: LucideIcon }[] = [
  { key: 'fiber', label: '蔬菜水果', icon: Apple },
  { key: 'protein', label: '优质蛋白', icon: Milk },
  { key: 'fat', label: '油脂适量', icon: Droplet },
  { key: 'sodium', label: '盐分控制', icon: Droplets },
  { key: 'calcium', label: '钙摄入', icon: Milk },
]

const SUGGESTION_ICONS: LucideIcon[] = [Milk, Leaf, Droplet, CookingPot, Wheat, Drumstick, Apple, Salad]
const ALERT_ICON: Record<string, LucideIcon> = {
  deficiency: Leaf, excess: Droplet, no_meal: Soup, diversity: Salad,
}
const ALERT_LABEL: Record<string, string> = {
  deficiency: '日均摄入不足', excess: '日均摄入偏多', no_meal: '数据不足', diversity: '结构单一',
}

type Tone = 'low' | 'ok' | 'high' | 'unknown'
const TONE_BADGE: Record<Tone, string> = {
  low: 'bg-health-red/10 text-health-red ring-health-red/20',
  ok: 'bg-health-green/10 text-health-green ring-health-green/20',
  high: 'bg-health-amber/10 text-health-amber ring-health-amber/20',
  unknown: 'bg-secondary text-muted-foreground ring-border',
}
const TONE_DOT: Record<Tone, string> = {
  low: 'bg-health-red', ok: 'bg-health-green', high: 'bg-health-amber', unknown: 'bg-muted-foreground/50',
}

// ─── Derived metrics ──────────────────────────────────────────────────────────
function ratioOf(avg: number, rec: number): number {
  return rec > 0 ? avg / rec : 0
}

function isUpperLimitMetric(key: NutrientKey): boolean {
  return UPPER_LIMIT_NUTRITION_KEYS.has(key as NutritionKey)
}

function coreStatus(ratio: number, upperLimit = false): { tone: Tone; label: string } {
  if (upperLimit) {
    if (ratio > 1.2) return { tone: 'high', label: '超标' }
    return { tone: 'ok', label: '控制良好' }
  }
  if (ratio < 0.8) return { tone: 'low', label: '不达标' }
  if (ratio > 1.2) return { tone: 'high', label: '超标' }
  return { tone: 'ok', label: '达标' }
}

function structureStatus(ratio: number, upperLimit = false): { label: string; ok: boolean } {
  if (upperLimit) {
    return ratio <= 1.2 ? { label: '良好', ok: true } : { label: '偏高', ok: false }
  }
  if (ratio >= 0.8 && ratio <= 1.2) return { label: '达标', ok: true }
  return { label: ratio > 1.2 ? '偏高' : '一般', ok: false }
}

function hasNutrientData(counts: NutrientSampleCounts | undefined, key: NutrientKey): boolean {
  return counts ? (counts[key] || 0) > 0 : true
}

function compliancePct(avg: NutrientData, rec: NutrientData, counts?: NutrientSampleCounts): number {
  const scores = NUTRIENT_META.flatMap(({ key }) => {
    if (!hasNutrientData(counts, key)) return []
    const recommended = rec[key] || 0
    if (!recommended) return []
    const r = ratioOf(avg[key] || 0, recommended)
    if (isUpperLimitMetric(key)) {
      return [r <= 1 ? 100 : Math.max(0, 100 - (r - 1) * 100)]
    }
    if (r >= 0.8 && r <= 1.2) return [100]
    if (r < 0.8) return [(r / 0.8) * 100]
    return [Math.max(0, 100 - (r - 1.2) * 150)]
  })
  return scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0
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
  const role = user?.role
  const studentIdKey = (user?.student_ids || []).join(',')
  const [activeScope, setActiveScope] = useState<ReportScope>('personal')
  const [students, setStudents] = useState<Student[]>([])
  const [selectedStudent, setSelectedStudent] = useState<Student | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [content, setContent] = useState<PersonalReportContent | null>(null)
  const [loading, setLoading] = useState(false)
  const [organizationTree, setOrganizationTree] = useState<SchoolNode[]>([])
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null)
  const [groupReport, setGroupReport] = useState<Report | null>(null)
  const [groupContent, setGroupContent] = useState<GroupReportContent | null>(null)
  const [groupLoading, setGroupLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedGradeId, setSelectedGradeId] = useState('')
  const [selectedClassId, setSelectedClassId] = useState('')

  const availableScopes = useMemo<ReportScope[]>(() => {
    if (role === 'admin') return ['personal', 'class', 'grade', 'campus']
    if (role === 'grade_leader') return ['personal', 'class', 'grade']
    if (role === 'teacher') return ['personal', 'class']
    return ['personal']
  }, [role])

  const loadReport = useCallback(async (student: Student) => {
    setSelectedStudent(student)
    setLoading(true)
    try {
      const res = await reportApi.studentLatest(student.id)
      const r: Report | null = res.data.data
      setReport(r)
      setContent(r?.content as PersonalReportContent | null)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function loadStudents() {
      if (role === 'admin' || role === 'teacher' || role === 'grade_leader') {
        const res = await adminApi.students({ page_size: 200, include_latest_report: true })
        if (!cancelled) setStudents(res.data.data.items)
        return
      }

      const studentIds = studentIdKey.split(',').filter(Boolean).map(Number)
      if (role === 'parent' && studentIds.length) {
        const results = await Promise.allSettled(
          studentIds.map(async studentId => {
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
  }, [loadReport, role, studentIdKey])

  useEffect(() => {
    if (role !== 'admin' && role !== 'teacher' && role !== 'grade_leader') return
    let cancelled = false
    void orgApi.tree().then(res => {
      if (!cancelled) setOrganizationTree(res.data?.data || [])
    })
    return () => { cancelled = true }
  }, [role])

  const groupOptions = useMemo(() => {
    if (activeScope === 'personal') return []
    const result: { id: number; name: string }[] = []
    const managedClassIds = new Set((user?.managed_class_ids || []).map(Number))
    const managedGradeIds = new Set((user?.managed_grade_ids || []).map(Number))

    for (const school of organizationTree) {
      for (const campus of school.campuses) {
        if (activeScope === 'campus' && role === 'admin') {
          result.push({ id: campus.id, name: `${school.name} / ${campus.name}` })
        }
        for (const stage of campus.stages) {
          for (const grade of stage.grades) {
            if (activeScope === 'grade' && (role === 'admin' || managedGradeIds.has(grade.id))) {
              result.push({ id: grade.id, name: `${campus.name} / ${stage.name} / ${grade.name}` })
            }
            if (activeScope === 'class') {
              for (const classNode of grade.classes) {
                const canView = role === 'admin'
                  || (role === 'teacher' && managedClassIds.has(classNode.id))
                  || (role === 'grade_leader' && managedGradeIds.has(grade.id))
                if (canView) {
                  result.push({ id: classNode.id, name: `${grade.name} / ${classNode.name}` })
                }
              }
            }
          }
        }
      }
    }
    return result
  }, [activeScope, organizationTree, role, user?.managed_class_ids, user?.managed_grade_ids])

  useEffect(() => {
    if (activeScope === 'personal') return
    if (!groupOptions.length) {
      setSelectedGroupId(null)
      setGroupReport(null)
      setGroupContent(null)
      return
    }
    if (!groupOptions.some(option => option.id === selectedGroupId)) {
      setSelectedGroupId(groupOptions[0].id)
    }
  }, [activeScope, groupOptions, selectedGroupId])

  useEffect(() => {
    if (activeScope === 'personal' || !selectedGroupId) return
    let cancelled = false
    setGroupLoading(true)
    void reportApi.groupLatest(activeScope, selectedGroupId).then(res => {
      if (cancelled) return
      const nextReport = res.data.data as Report | null
      setGroupReport(nextReport)
      setGroupContent(nextReport?.content as GroupReportContent | null)
    }).finally(() => {
      if (!cancelled) setGroupLoading(false)
    })
    return () => { cancelled = true }
  }, [activeScope, selectedGroupId])

  const generateReport = async () => {
    setGenerating(true)
    try {
      const reportType = activeScope === 'personal' ? 'personal_weekly' : `${activeScope}_weekly`
      await reportApi.generate(reportType)
      toast.success(`${REPORT_SCOPES[activeScope].label}报告生成任务已提交，请稍后刷新`)
    } finally { setGenerating(false) }
  }

  const pushReport = async () => {
    if (!report) return
    await reportApi.push(report.id)
    toast.success('推送任务已提交')
  }

  const gradeOptions = useMemo(() => Array.from(new Map(
    students
      .filter(student => student.grade_id)
      .map(student => [String(student.grade_id), String(student.grade_name || student.grade_id || '')])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')), [students])

  const classOptions = useMemo(() => Array.from(new Map(
    students
      .filter(student => (!selectedGradeId || String(student.grade_id) === selectedGradeId) && student.class_id)
      .map(student => [String(student.class_id), String(student.class_name || student.class_id)])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')), [selectedGradeId, students])

  const normalizedSearch = search.trim().toLowerCase()
  const filteredStudents = useMemo(() => students.filter(student => {
    if (selectedGradeId && String(student.grade_id) !== selectedGradeId) return false
    if (selectedClassId && String(student.class_id) !== selectedClassId) return false
    if (!normalizedSearch) return true

    return [
      student.name,
      student.student_no,
      student.class_name,
      student.grade_name,
    ].some(value => String(value || '').toLowerCase().includes(normalizedSearch))
  }), [normalizedSearch, selectedClassId, selectedGradeId, students])

  const ActiveScopeIcon = REPORT_SCOPES[activeScope].icon

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">营养报告</h1>
          <p className="text-sm text-muted-foreground mt-0.5">{REPORT_SCOPES[activeScope].description}</p>
        </div>
        <div className="flex gap-2">
          {hasRole('admin') && (
            <button onClick={generateReport} disabled={generating} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-3.5 h-3.5', generating && 'animate-spin')} />生成报告
            </button>
          )}
          {activeScope === 'personal' && report && (
            <button onClick={pushReport} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
              <Send className="w-3.5 h-3.5" />推送报告
            </button>
          )}
        </div>
      </div>

      <div className="mb-5 flex flex-wrap gap-2 rounded-xl border border-border bg-card p-1.5" role="tablist" aria-label="报告层级">
        {availableScopes.map(scope => {
          const ScopeIcon = REPORT_SCOPES[scope].icon
          return (
            <button
              key={scope}
              type="button"
              role="tab"
              aria-selected={activeScope === scope}
              onClick={() => setActiveScope(scope)}
              className={cn(
                'flex min-w-24 flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-health-green/40',
                activeScope === scope
                  ? 'bg-health-green text-white shadow-sm'
                  : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
              )}
            >
              <ScopeIcon className="h-4 w-4" />
              {REPORT_SCOPES[scope].label}报告
            </button>
          )
        })}
      </div>

      {activeScope === 'personal' ? (
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
                <button key={s.id} onClick={() => void loadReport(s)}
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
            <ReportCard content={content} />
          )}
        </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-health-green/10 text-health-green">
                <ActiveScopeIcon className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-medium">选择{REPORT_SCOPES[activeScope].label}范围</div>
                <div className="text-xs text-muted-foreground">仅呈现群体统计，不包含个人名单与个人明细</div>
              </div>
            </div>
            <select
              value={selectedGroupId || ''}
              onChange={event => setSelectedGroupId(Number(event.target.value) || null)}
              className="min-w-64 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-health-green/30"
            >
              {groupOptions.length === 0 ? <option value="">暂无可查看范围</option> : null}
              {groupOptions.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}
            </select>
          </div>

          {!selectedGroupId ? (
            <div className="rounded-xl border border-border bg-card p-12 text-center text-sm text-muted-foreground">暂无可查看的组织范围</div>
          ) : groupLoading ? (
            <div className="rounded-xl border border-border bg-card p-12 text-center text-sm text-muted-foreground">加载中...</div>
          ) : !groupContent ? (
            <div className="rounded-xl border border-border bg-card p-12 text-center">
              <SchoolIcon className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">该范围暂无新版群体报告，请先生成报告</p>
            </div>
          ) : (
            <GroupReportCard content={groupContent} report={groupReport} />
          )}
        </div>
      )}
    </div>
  )
}

function GroupReportCard({ content, report }: { content: GroupReportContent; report: Report | null }) {
  const scopeLabel = REPORT_SCOPES[content.scope_type].label
  const periodLabel = `${fmtDate(content.period_start)} — ${fmtDate(content.period_end)}`
  const scoredCount = Math.max(1, content.students_with_data)
  const scoreBands = [
    { key: 'excellent' as const, label: '优秀（90–100）', color: 'bg-health-green' },
    { key: 'good' as const, label: '良好（75–89）', color: 'bg-health-blue' },
    { key: 'attention' as const, label: '关注（60–74）', color: 'bg-health-amber' },
    { key: 'improve' as const, label: '待改善（<60）', color: 'bg-health-red' },
  ]

  return (
    <div className="space-y-4">
      <Section className="bg-gradient-to-br from-health-green/5 to-card">
        <div className="px-5 py-5 sm:px-7 sm:py-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-health-green/25 bg-health-green/10">
              <BarChart3 className="h-7 w-7 text-health-green" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium tracking-wide text-health-green">{REPORT_ORG}</p>
              <h2 className="mt-0.5 text-2xl font-bold tracking-tight text-foreground sm:text-3xl">{scopeLabel}营养群体周报</h2>
              <p className="mt-1 text-xs text-muted-foreground">群体均值 · 达标分布 · 共性问题 · 改善方向</p>
            </div>
            <div className="rounded-xl border border-health-green/25 bg-card px-5 py-2.5 text-center">
              <div className={cn('font-mono text-2xl font-semibold', scoreColor(content.average_score))}>{content.average_score}</div>
              <div className="text-[10px] text-muted-foreground">群体平均分</div>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-health-green/15 pt-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Building2 className="h-3.5 w-3.5 text-health-green" />
              报告范围：<span className="font-medium text-foreground">{content.scope_name}</span>
            </span>
            <span className="flex items-center gap-1.5">
              <CalendarDays className="h-3.5 w-3.5 text-health-green" />
              统计周期：<span className="text-foreground/80">{periodLabel}</span>
            </span>
            <span className="rounded-full bg-health-green/10 px-2 py-1 text-health-green">群体统计 · 不含个人明细</span>
          </div>
        </div>
      </Section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          { label: '范围人数', value: content.student_count, suffix: '人' },
          { label: '有效覆盖', value: content.students_with_data, suffix: '人' },
          { label: '数据覆盖率', value: content.data_coverage_rate, suffix: '%' },
          { label: '共性关注项', value: content.focus_nutrients.length, suffix: '项' },
        ].map(item => (
          <div key={item.label} className="rounded-xl border border-border bg-card px-4 py-3">
            <div className="text-xs text-muted-foreground">{item.label}</div>
            <div className="mt-1 font-mono text-2xl font-semibold text-foreground">
              {item.value}<span className="ml-1 text-xs font-normal text-muted-foreground">{item.suffix}</span>
            </div>
          </div>
        ))}
      </div>

      <Section>
        <SectionTitle n={1} title="群体周期日均与达标分布" />
        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 sm:p-5 xl:grid-cols-3">
          {NUTRIENT_META.map(({ key, label, unit, icon: Icon }) => {
            const distribution = content.nutrient_distributions[key]
            const average = content.avg_nutrients[key]
            const recommended = content.recommended_nutrients[key]
            const hasData = Boolean(distribution?.measured_count && average !== null)
            return (
              <div key={key} className="rounded-xl border border-border/70 bg-background/40 p-3">
                <div className="flex items-center gap-2">
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-health-green/10 text-health-green">
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-foreground">{label}</span>
                      <span className="text-[10px] text-muted-foreground">覆盖 {distribution?.coverage_rate || 0}%</span>
                    </div>
                    <div className="mt-0.5 font-mono text-sm">
                      {hasData ? (
                        <>{Math.round(average || 0)} <span className="text-[10px] text-muted-foreground">/ {Math.round(recommended || 0)} {unit}</span></>
                      ) : <span className="text-xs text-muted-foreground">暂无有效数据</span>}
                    </div>
                  </div>
                </div>
                <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-secondary" aria-label={`${label}达标分布`}>
                  <span className="bg-health-red" style={{ width: `${distribution?.low_rate || 0}%` }} />
                  <span className="bg-health-green" style={{ width: `${distribution?.ok_rate || 0}%` }} />
                  <span className="bg-health-amber" style={{ width: `${distribution?.high_rate || 0}%` }} />
                </div>
                <div className="mt-2 flex items-center justify-between text-[10px] text-muted-foreground">
                  <span>偏低 {distribution?.low_rate || 0}%</span>
                  <span className="text-health-green">达标 {distribution?.ok_rate || 0}%</span>
                  <span>偏高 {distribution?.high_rate || 0}%</span>
                </div>
              </div>
            )
          })}
        </div>
        <div className="border-t border-border/60 px-4 py-3 text-[11px] text-muted-foreground sm:px-5">
          每名学生先计算周期日均，再按学生等权汇总；数值为群体日均 / 推荐参考值。
        </div>
      </Section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section>
          <SectionTitle n={2} title="群体评分分布" />
          <div className="space-y-4 p-4 sm:p-5">
            {scoreBands.map(band => {
              const count = content.score_distribution[band.key]
              const rate = Math.round(count * 100 / scoredCount)
              return (
                <div key={band.key}>
                  <div className="mb-1.5 flex items-center justify-between text-xs">
                    <span className="text-foreground/80">{band.label}</span>
                    <span className="font-mono text-muted-foreground">{count} 人 · {rate}%</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-secondary">
                    <div className={cn('h-full rounded-full', band.color)} style={{ width: `${rate}%` }} />
                  </div>
                </div>
              )
            })}
            {content.score_distribution.no_data > 0 ? (
              <p className="text-[11px] text-muted-foreground">另有 {content.score_distribution.no_data} 人暂无足够数据，未纳入评分分布。</p>
            ) : null}
          </div>
        </Section>

        <Section>
          <SectionTitle n={3} title="群体共性关注项" />
          <div className="space-y-3 p-4 sm:p-5">
            {content.focus_nutrients.length === 0 ? (
              <div className="flex items-center gap-3 rounded-xl border border-health-green/25 bg-health-green/5 px-4 py-3 text-sm text-health-green">
                <CheckCircle2 className="h-4 w-4" />群体各项营养指标整体分布稳定。
              </div>
            ) : content.focus_nutrients.map(item => (
              <div key={item.nutrient} className="rounded-xl border border-border/70 bg-background/40 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm font-medium text-foreground">{item.label}</span>
                  <span className={cn(
                    'rounded-full px-2 py-0.5 text-[10px] font-medium',
                    item.dominant_status === 'low' ? 'bg-health-red/10 text-health-red' : 'bg-health-amber/10 text-health-amber',
                  )}>
                    {item.attention_rate}% 需关注
                  </span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  在 {item.measured_count} 名有数据的学生中，偏低占 {item.low_rate}%，偏高占 {item.high_rate}%。
                </p>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section>
        <SectionTitle n={4} title="整体结论与改善方向" />
        <div className="grid grid-cols-1 gap-4 p-4 sm:p-5 lg:grid-cols-2">
          <div className="rounded-xl bg-health-green px-4 py-3 text-white">
            <div className="flex items-center gap-2 text-sm font-semibold"><BarChart3 className="h-4 w-4" />整体结论</div>
            <p className="mt-1.5 text-sm leading-relaxed text-white/90">
              {report?.summary || `${content.scope_name}群体平均营养评分 ${content.average_score} 分，数据覆盖率 ${content.data_coverage_rate}%。`}
            </p>
          </div>
          <div className="space-y-2">
            {content.suggestions.map((suggestion, index) => (
              <div key={suggestion} className="flex items-start gap-3 rounded-xl border border-border/70 bg-background/40 px-3 py-2.5">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-health-amber/10 font-mono text-[10px] text-health-amber">{index + 1}</span>
                <p className="text-xs leading-relaxed text-foreground/80">{suggestion}</p>
              </div>
            ))}
          </div>
        </div>
      </Section>

      <div className="flex flex-col items-center justify-between gap-2 px-2 py-3 text-[11px] text-muted-foreground sm:flex-row">
        <span className="flex items-center gap-1.5"><BarChart3 className="h-3.5 w-3.5" />数据来源：学校营养管理系统</span>
        <span className="flex items-center gap-1.5"><CalendarDays className="h-3.5 w-3.5" />统计时间：{periodLabel}</span>
      </div>
    </div>
  )
}

// ─── Report card (matches the reference weekly-report layout) ────────────────
function ReportCard({ content }: { content: PersonalReportContent }) {
  const avg = content.avg_nutrients
  const rec = content.recommended_nutrients
  const sampleCounts = content.nutrient_sample_counts
  const pct = compliancePct(avg, rec, sampleCounts)
  const assessment = overallAssessment(pct)
  const energy = energySplit(avg)
  const score = content.overall_score ?? 0
  const personalSummary = `周期日均营养整体${assessment.label}，综合评分 ${score} 分${content.alerts.length ? `，有 ${content.alerts.length} 项平均摄入指标需要关注` : ''}。`

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
            <span className="rounded-full bg-health-green/10 px-2 py-1 text-health-green">周期日均口径</span>
          </div>
        </div>
      </Section>

      {/* 1. Core nutrient indicators */}
      <Section>
        <SectionTitle n={1} title="周期日均摄入核心指标" />
        <div className="p-4 sm:p-5">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {NUTRIENT_META.map(({ key, label, unit, icon: Icon }) => {
              const hasData = hasNutrientData(sampleCounts, key)
              const a = avg[key] || 0
              const r = rec[key] || 0
              const ratio = ratioOf(a, r)
              const { tone, label: statusLabel } = hasData
                ? coreStatus(ratio, isUpperLimitMetric(key))
                : { tone: 'unknown' as Tone, label: '暂无数据' }
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
                      {hasData ? (
                        <>
                          <span className="text-foreground">{Math.round(a)}</span>
                          <span className="text-[10px] text-muted-foreground"> / {Math.round(r)} {unit}</span>
                        </>
                      ) : (
                        <span className="text-xs text-muted-foreground">历史日志未记录</span>
                      )}
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
            <span className="ml-auto">数值：周期日均摄入 / 推荐参考值</span>
          </div>
        </div>
      </Section>

      {/* 2. Compliance rate */}
      <Section>
        <SectionTitle n={2} title="周期平均营养达标率" />
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
              {personalSummary}
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
        <SectionTitle n={3} title="周期平均营养搭配比例" />
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
                const hasData = hasNutrientData(sampleCounts, key)
                const ratio = ratioOf(avg[key] || 0, rec[key] || 0)
                const { label: statusLabel, ok } = hasData
                  ? structureStatus(ratio, isUpperLimitMetric(key))
                  : { label: '暂无数据', ok: false }
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

      {/* 4. Period-average focus */}
      <Section>
        <SectionTitle n={4} title="周期平均摄入关注项" />
        <div className="p-4 sm:p-5">
          {content.alerts.length === 0 ? (
            <div className="flex items-center gap-3 rounded-xl border border-health-green/25 bg-health-green/5 px-4 py-3 text-sm text-health-green">
              <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
              周期日均营养摄入未发现明显偏离，请继续保持整体膳食结构。
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
                        <span className="text-xs font-medium text-foreground">{ALERT_LABEL[alert.type] || '摄入提醒'}</span>
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
        <SectionTitle n={5} title="周期结论与下一周期优化建议" />
        <div className="p-4 sm:p-5 space-y-4">
          {/* Conclusion */}
          <div className="rounded-xl bg-health-green text-white px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <CheckCircle2 className="w-4 h-4" />周期结论
            </div>
            <p className="mt-1.5 text-sm text-white/90 leading-relaxed">
              {personalSummary}
            </p>
          </div>
          {/* Suggestions */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Lightbulb className="w-4 h-4 text-health-amber" />
              <span className="text-sm font-semibold text-foreground">下一周期优化建议</span>
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
