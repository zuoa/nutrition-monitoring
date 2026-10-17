import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  TrendingUp, CheckCircle2, Send, RefreshCw,
  Flame, Beef, Wheat, Leaf, Droplet, Droplets,
  Milk, Apple, Drumstick,
  BarChart3, CalendarDays, Heart,
  Building2, GraduationCap, School as SchoolIcon, Users, UserRound,
} from 'lucide-react'
import { reportApi, adminApi, orgApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, scoreColor, fmtDate } from '@/lib/utils'
import { NUTRITION_FIELDS } from '@/lib/nutrition'
import type {
  GroupReportContent,
  Student,
  PersonalReportContent,
  Report,
  NutrientData,
} from '@/types'
import type { SchoolNode } from '@/components/students/adminTypes'
import toast from 'react-hot-toast'
import ReportPreview from '@/components/reports/ReportPreview'
import ReportExports, { ReportDownload } from '@/components/reports/ReportExports'

// ─── Report identity (adjust to your deployment) ─────────────────────────────
const REPORT_ORG = '杭州第四中学江东校区'

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
  const reportRequest = useRef(0)

  const availableScopes = useMemo<ReportScope[]>(() => {
    if (role === 'admin') return ['personal', 'class', 'grade', 'campus']
    if (role === 'grade_leader') return ['personal', 'class', 'grade']
    if (role === 'teacher') return ['personal', 'class']
    return ['personal']
  }, [role])

  const loadReport = useCallback(async (student: Student) => {
    const requestId = ++reportRequest.current
    setSelectedStudent(student)
    setReport(null)
    setContent(null)
    setLoading(true)
    try {
      const res = await reportApi.studentLatest(student.id)
      if (requestId !== reportRequest.current) return
      const r: Report | null = res.data.data
      setReport(r)
      setContent(r?.content as PersonalReportContent | null)
    } catch { /* The API client displays the error. */ } finally {
      if (requestId === reportRequest.current) setLoading(false)
    }
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

  const exportClasses = useMemo(() => {
    const allowedClasses = new Set((user?.managed_class_ids || []).map(Number))
    const allowedGrades = new Set((user?.managed_grade_ids || []).map(Number))
    return organizationTree.flatMap(school => school.campuses.flatMap(campus => campus.stages.flatMap(stage => stage.grades.flatMap(grade =>
      grade.classes.filter(c => role === 'admin' || (role === 'teacher' && allowedClasses.has(c.id)) || (role === 'grade_leader' && allowedGrades.has(grade.id)))
        .map(c => ({ id: c.id, name: `${campus.name} / ${grade.name} / ${c.name}` }))
    ))))
  }, [organizationTree, role, user?.managed_class_ids, user?.managed_grade_ids])

  const ActiveScopeIcon = REPORT_SCOPES[activeScope].icon

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">营养报告</h1>
          <p className="text-sm text-muted-foreground mt-0.5">{REPORT_SCOPES[activeScope].description}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {activeScope === 'personal' && report && !loading && <ReportDownload key={report.id} reportId={report.id} name={content?.student_name || '学生'} period={`${report.period_start}_${report.period_end}`} />}
          {hasRole('admin') && (
            <button onClick={generateReport} disabled={generating} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-3.5 h-3.5', generating && 'animate-spin')} />生成报告
            </button>
          )}
          {activeScope === 'personal' && report && hasRole('admin') && (
            <button onClick={pushReport} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
              <Send className="w-3.5 h-3.5" />推送报告
            </button>
          )}
        </div>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-2 rounded-xl border border-border bg-card p-1.5 sm:flex sm:flex-wrap" role="tablist" aria-label="报告层级">
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
        <div className="lg:col-span-3 space-y-4 min-w-0">
          {user && exportClasses.length > 0 && <ReportExports key={user.id} classes={exportClasses} preferredClassId={Number(selectedClassId) || undefined} userId={user.id} />}
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
            <ReportPreview content={content} />
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
