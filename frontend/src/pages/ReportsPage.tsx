import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import * as Dialog from '@radix-ui/react-dialog'
import {
  ArrowRight, Building2, CalendarDays, ChevronDown, ChevronLeft, ChevronRight, FileBarChart2,
  GraduationCap, Loader2, Network, PackageOpen, RefreshCw, Search, Send, Sparkles, UserRound, Users, X,
} from 'lucide-react'
import { reportApi, adminApi, orgApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, fmtDate } from '@/lib/utils'
import { previousReportPeriod, reportPeriodRange } from '@/lib/reportPeriods'
import type { ReportCadence } from '@/lib/reportPeriods'
import type { GroupReportContent, Student, PersonalReportContent, Report } from '@/types'
import type { SchoolNode } from '@/components/students/adminTypes'
import toast from 'react-hot-toast'
import ReportPreview from '@/components/reports/ReportPreview'
import ReportExports, { ReportDownload } from '@/components/reports/ReportExports'
import ReportOrganizationTree, { buildReportOrganization, flattenReportOrganization } from '@/components/reports/ReportOrganizationTree'
import type { ReportOrganizationNode, ReportScope } from '@/components/reports/ReportOrganizationTree'

const GroupReportCard = lazy(() => import('@/components/reports/GroupReportCard'))
const STUDENT_PAGE_SIZE = 30
const EMPTY_IDS: string[] = []
const REPORT_SCOPES: Record<ReportScope, { label: string; icon: LucideIcon }> = {
  personal: { label: '个人', icon: UserRound },
  class: { label: '班级', icon: Users },
  grade: { label: '年级', icon: GraduationCap },
  campus: { label: '校区', icon: Building2 },
}
const actionClass = 'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-medium transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary'

function buildStudentFromLatestReport(studentId: number, report: Report | null): Student | null {
  const content = report?.content as PersonalReportContent | null | undefined
  if (!content) return null
  return {
    id: studentId, student_no: String(studentId), name: content.student_name,
    class_id: content.class_name || `student-${studentId}`, class_name: content.class_name, is_active: true,
    latest_report: report?.report_type === 'personal_monthly' ? undefined : {
      report_id: report?.id || 0, overall_score: content.overall_score, alert_count: content.alerts?.length || 0,
      period_start: report?.period_start, period_end: report?.period_end, summary: report?.summary, created_at: report?.created_at,
    },
  }
}

function ReportState({ title, description, loading = false, error = false, onRetry }: {
  title: string; description?: string; loading?: boolean; error?: boolean; onRetry?: () => void
}) {
  return <div className="flex min-h-[380px] flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-6 py-16 text-center" role="status" aria-live="polite">
    <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/5 text-primary/70">
      {loading ? <Loader2 className="h-7 w-7 animate-spin" /> : <FileBarChart2 className="h-7 w-7" />}
    </div>
    <h3 className="text-sm font-medium">{title}</h3>
    {description ? <p className="mt-2 max-w-sm text-xs leading-6 text-muted-foreground">{description}</p> : null}
    {error && onRetry ? <button type="button" onClick={onRetry} className={cn(actionClass, 'mt-5')}><RefreshCw className="h-3.5 w-3.5" />重新加载</button> : null}
  </div>
}

export default function ReportsPage() {
  const { user, hasRole } = useAuth()
  const role = user?.role
  const staff = role === 'admin' || role === 'teacher' || role === 'grade_leader'
  const studentIdKey = (user?.student_ids || EMPTY_IDS).join(',')
  const [activeScope, setActiveScope] = useState<ReportScope>('personal')
  const [cadence, setCadence] = useState<ReportCadence>('weekly')
  const [weekPeriod, setWeekPeriod] = useState('')
  const [monthPeriod, setMonthPeriod] = useState('')
  const period = cadence === 'weekly' ? weekPeriod : monthPeriod
  const periodLabel = cadence === 'weekly' ? '周报' : '月报'
  const reportType = `${activeScope}_${cadence}`
  const selectedPeriodRange = period ? reportPeriodRange(cadence, period) : null
  const generationRange = reportPeriodRange(cadence, period || previousReportPeriod(cadence))
  const [organizationTree, setOrganizationTree] = useState<SchoolNode[]>([])
  const [organizationLoading, setOrganizationLoading] = useState(false)
  const [organizationError, setOrganizationError] = useState(false)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [students, setStudents] = useState<Student[]>([])
  const [studentsLoading, setStudentsLoading] = useState(false)
  const [studentsError, setStudentsError] = useState(false)
  const [studentsTotal, setStudentsTotal] = useState(0)
  const [studentPage, setStudentPage] = useState(1)
  const [studentSearch, setStudentSearch] = useState('')
  const [selectedStudent, setSelectedStudent] = useState<Student | null>(null)
  const [reportResult, setReportResult] = useState<{ key: string; data: Report | null } | null>(null)
  const [reportLoading, setReportLoading] = useState(false)
  const [reportError, setReportError] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [mobileSelectorOpen, setMobileSelectorOpen] = useState(true)
  const selectionRef = useRef<string | null>(null)
  const refreshReports = useCallback(() => setRefresh(value => value + 1), [])

  const availableScopes = useMemo<ReportScope[]>(() => {
    if (role === 'admin') return ['personal', 'class', 'grade', 'campus']
    if (role === 'grade_leader') return ['personal', 'class', 'grade']
    if (role === 'teacher') return ['personal', 'class']
    return ['personal']
  }, [role])
  const nodes = useMemo(() => buildReportOrganization(organizationTree, role, user?.managed_class_ids, user?.managed_grade_ids), [organizationTree, role, user?.managed_class_ids, user?.managed_grade_ids])
  const flatNodes = useMemo(() => flattenReportOrganization(nodes), [nodes])
  const nodeIndex = useMemo(() => new Map(flatNodes.map(node => [node.key, node])), [flatNodes])
  const selectedNode = selectedKey ? nodeIndex.get(selectedKey) : undefined
  const selectedOrganizationKind = selectedNode?.kind
  const selectedOrganizationId = selectedNode?.id
  const exportClasses = useMemo(() => flatNodes.filter(node => node.kind === 'class').map(node => ({ id: node.id, name: node.path.slice(1).join(' / ') })), [flatNodes])

  useEffect(() => {
    if (!staff) return
    let cancelled = false
    setOrganizationLoading(true)
    setOrganizationError(false)
    void orgApi.tree().then(response => {
      if (!cancelled) setOrganizationTree(response.data?.data || [])
    }).catch(() => {
      if (!cancelled) setOrganizationError(true)
    }).finally(() => { if (!cancelled) setOrganizationLoading(false) })
    return () => { cancelled = true }
  }, [staff, refresh])

  useEffect(() => {
    if (activeScope === 'personal' || selectedNode?.kind === activeScope) return
    setSelectedKey(flatNodes.find(node => node.kind === activeScope)?.key || null)
  }, [activeScope, flatNodes, selectedNode])

  useEffect(() => {
    if (!staff || activeScope !== 'personal') return
    let cancelled = false
    setStudentsLoading(true)
    setStudentsError(false)
    const timer = window.setTimeout(() => {
      const params: Record<string, string | number | boolean> = { page: studentPage, page_size: STUDENT_PAGE_SIZE, include_latest_report: true }
      if (selectedOrganizationKind && selectedOrganizationId) params[`${selectedOrganizationKind}_id`] = selectedOrganizationId
      if (studentSearch.trim()) params.search = studentSearch.trim()
      void adminApi.students(params).then(response => {
        if (cancelled) return
        const data = response.data.data
        setStudents(data.items)
        setStudentsTotal(data.total)
      }).catch(() => {
        if (!cancelled) { setStudentsError(true); setStudents([]) }
      }).finally(() => { if (!cancelled) setStudentsLoading(false) })
    }, studentSearch.trim() ? 250 : 0)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [staff, activeScope, selectedOrganizationKind, selectedOrganizationId, studentSearch, studentPage, refresh])

  useEffect(() => {
    if (role !== 'parent') return
    let cancelled = false
    setStudentsLoading(true)
    setStudentsError(false)
    void Promise.allSettled(studentIdKey.split(',').filter(Boolean).map(async id => {
      const response = await reportApi.studentLatest(Number(id))
      const report = response.data.data || (await reportApi.studentLatest(Number(id), { type: 'personal_monthly' })).data.data
      return buildStudentFromLatestReport(Number(id), report)
    })).then(results => {
      if (cancelled) return
      const items = results.flatMap(result => result.status === 'fulfilled' && result.value ? [result.value] : [])
        .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
      setStudents(items)
      setStudentsTotal(items.length)
      setStudentsError(results.some(result => result.status === 'rejected'))
      setSelectedStudent(current => current || items[0] || null)
    }).finally(() => { if (!cancelled) setStudentsLoading(false) })
    return () => { cancelled = true }
  }, [role, studentIdKey, refresh])

  const targetKey = activeScope === 'personal'
    ? selectedStudent ? `personal-${selectedStudent.id}` : null
    : selectedNode?.kind === activeScope ? `${activeScope}-${selectedNode.id}` : null
  const selectionKey = targetKey ? `${targetKey}-${cadence}-${period || 'latest'}` : null
  selectionRef.current = selectionKey
  const selectedStudentId = selectedStudent?.id
  const selectedGroupId = activeScope !== 'personal' && selectedNode?.kind === activeScope ? selectedNode.id : null

  useEffect(() => {
    if (!selectionKey) return
    let cancelled = false
    setReportLoading(true)
    setReportError(false)
    const request = activeScope === 'personal'
      ? reportApi.studentLatest(selectedStudentId!, { type: reportType, period: period || undefined })
      : reportApi.groupLatest(activeScope, selectedGroupId!, { type: reportType, period: period || undefined })
    void request.then(response => {
      if (!cancelled) setReportResult({ key: selectionKey, data: response.data.data })
    }).catch(() => {
      if (!cancelled) { setReportError(true); setReportResult({ key: selectionKey, data: null }) }
    }).finally(() => { if (!cancelled) setReportLoading(false) })
    return () => { cancelled = true }
  }, [activeScope, selectionKey, selectedStudentId, selectedGroupId, reportType, period, refresh])

  const report = reportResult?.key === selectionKey ? reportResult.data : null
  const loading = !!selectionKey && (reportLoading || reportResult?.key !== selectionKey)
  const personalContent = activeScope === 'personal' ? report?.content as PersonalReportContent | null : null
  const groupContent = activeScope !== 'personal' ? report?.content as GroupReportContent | null : null
  const visibleStudents = staff ? students : students.filter(student => `${student.name} ${student.student_no}`.toLowerCase().includes(studentSearch.trim().toLowerCase()))
  const pageCount = Math.max(1, Math.ceil(studentsTotal / STUDENT_PAGE_SIZE))
  const currentTitle = activeScope === 'personal' ? selectedStudent?.name : selectedNode?.name
  const currentPath = activeScope === 'personal'
    ? selectedStudent ? [...(flatNodes.find(node => node.kind === 'class' && String(node.id) === String(selectedStudent.class_id))?.path || [selectedStudent.grade_name, selectedStudent.class_name].filter((value): value is string => !!value)), selectedStudent.name] : selectedNode?.path
    : selectedNode?.path

  const selectOrganization = (node: ReportOrganizationNode | null) => {
    setSelectedKey(node?.key || null)
    setSelectedStudent(null)
    setStudentSearch('')
    setStudentPage(1)
  }
  const changeScope = (scope: ReportScope) => {
    if (scope === activeScope) return
    setActiveScope(scope)
    setStudentPage(1)
    if (scope !== 'personal') {
      const contextNode = activeScope === 'personal' && selectedStudent ? nodeIndex.get(`class-${selectedStudent.class_id}`) || selectedNode : selectedNode
      const related = contextNode ? [contextNode.key, ...contextNode.ancestors.slice().reverse()].map(key => nodeIndex.get(key)).find(node => node?.kind === scope)
        || flatNodes.find(node => node.kind === scope && node.ancestors.includes(contextNode.key)) : undefined
      setSelectedKey(related?.key || flatNodes.find(node => node.kind === scope)?.key || null)
    }
  }
  const generateReport = async () => {
    if (!generationRange || generating) return
    setGenerating(true)
    try {
      await reportApi.generate(reportType, generationRange.start, generationRange.end)
      toast.success(`${REPORT_SCOPES[activeScope].label}${periodLabel}（${generationRange.start} 至 ${generationRange.end}）生成任务已提交，完成后可刷新查看`)
    } catch { /* The API client displays the error. */ } finally { setGenerating(false) }
  }
  const pushReport = async () => {
    if (!report || pushing) return
    const key = selectionKey
    setPushing(true)
    try {
      await reportApi.push(report.id)
      toast.success('报告推送任务已提交')
      if (selectionRef.current === key) refreshReports()
    } catch { /* The API client displays the error. */ } finally { setPushing(false) }
  }

  return <div className="mx-auto max-w-[1800px] p-4 sm:p-6">
    <header className="mb-6 flex flex-col justify-between gap-4 xl:flex-row xl:items-center">
      <div>
        <div className="flex items-center gap-3"><h1 className="text-2xl font-semibold tracking-tight">营养报告</h1><span className="rounded-md border border-primary/15 bg-primary/5 px-2 py-1 text-[10px] font-medium text-primary">膳食{periodLabel}</span></div>
        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">按组织查看学生膳食记录与群体营养分析</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {user && exportClasses.length > 0 ? <Dialog.Root>
          <Dialog.Trigger asChild><button type="button" className={actionClass}><PackageOpen className="h-4 w-4" />批量导出</button></Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
            <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl focus:outline-none sm:p-6">
              <Dialog.Title className="pr-8 text-lg font-semibold">班级报告批量导出</Dialog.Title>
              <Dialog.Description className="mb-5 mt-1.5 text-xs leading-5 text-muted-foreground">选择班级和{periodLabel}周期，将学生个人报告打包下载。</Dialog.Description>
              <Dialog.Close asChild><button type="button" aria-label="关闭批量导出" className="absolute right-4 top-4 rounded-lg p-2 text-muted-foreground hover:bg-secondary focus-visible:ring-2 focus-visible:ring-primary"><X className="h-4 w-4" /></button></Dialog.Close>
              <ReportExports classes={exportClasses} preferredClassId={selectedNode?.kind === 'class' ? selectedNode.id : Number(selectedStudent?.class_id) || undefined} userId={user.id} cadence={cadence} preferredPeriod={selectedPeriodRange ? { period_start: selectedPeriodRange.start, period_end: selectedPeriodRange.end } : null} />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root> : null}
        {hasRole('admin') ? <button type="button" onClick={() => void generateReport()} disabled={generating || !generationRange}
          title={`批量生成全部${activeScope === 'personal' ? '学生个人' : REPORT_SCOPES[activeScope].label}${periodLabel}：${generationRange?.start || ''} 至 ${generationRange?.end || ''}`}
          className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2">
          {generating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}{generating ? '正在提交…' : `生成${REPORT_SCOPES[activeScope].label}${periodLabel}`}
        </button> : null}
      </div>
    </header>

    <div className="grid min-w-0 grid-cols-1 items-start gap-5 lg:grid-cols-[288px_minmax(0,1fr)]">
      <aside className="overflow-hidden rounded-xl border border-border bg-card lg:sticky lg:top-4 lg:h-[calc(100dvh-12rem)] lg:min-h-[28rem]" aria-label="报告对象选择">
        <button type="button" aria-expanded={mobileSelectorOpen} aria-controls="report-selector" onClick={() => setMobileSelectorOpen(open => !open)} className="flex min-h-12 w-full items-center justify-between gap-2 px-4 text-left text-sm font-medium lg:hidden">
          <span className="flex min-w-0 items-center gap-2"><Network className="h-4 w-4 shrink-0 text-primary" /><span className="truncate">{selectedStudent?.name || selectedNode?.name || (staff ? '选择组织与学生' : '选择学生')}</span></span>
          <ChevronDown className={cn('h-4 w-4 shrink-0 transition-transform', mobileSelectorOpen && 'rotate-180')} />
        </button>
        <div id="report-selector" className={cn('lg:flex lg:h-full lg:min-h-0 lg:flex-col', !mobileSelectorOpen && 'hidden')}>
          {staff ? <ReportOrganizationTree key={activeScope} nodes={nodes} scope={activeScope} selectedKey={selectedKey} loading={organizationLoading} error={organizationError} onSelect={selectOrganization} onRetry={refreshReports} /> : <h2 className="px-4 pt-4 text-sm font-semibold">我的学生</h2>}
          {activeScope === 'personal' ? <section className={cn('pb-1 lg:flex lg:min-h-0 lg:flex-[1_1_55%] lg:flex-col', staff && 'border-t border-border')} aria-label="学生选择">
            <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-3 pt-4">
              <h3 className="truncate text-xs font-semibold">{selectedNode?.name || (staff ? '学生名单' : '关联学生')}</h3>
              <span className="shrink-0 text-[10px] text-muted-foreground">{studentsLoading ? '加载中' : `${staff ? studentsTotal : visibleStudents.length} 位学生`}</span>
            </div>
            <div className="relative mx-3 mb-3 shrink-0">
              <Search className="pointer-events-none absolute left-3 top-3 h-3.5 w-3.5 text-muted-foreground" />
              <input aria-label="搜索学生" placeholder="搜索姓名 / 学号" value={studentSearch} onChange={event => { setStudentSearch(event.target.value); setStudentPage(1); setSelectedStudent(null) }}
                className="h-10 w-full rounded-lg border border-border bg-background/70 pl-9 pr-8 text-xs outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/15" />
              {studentSearch ? <button type="button" aria-label="清空学生搜索" onClick={() => { setStudentSearch(''); setStudentPage(1) }} className="absolute right-1 top-1 flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground"><X className="h-3.5 w-3.5" /></button> : null}
            </div>
            <div className="max-h-80 space-y-1 overflow-y-auto px-2 pb-2 lg:min-h-0 lg:max-h-none lg:flex-1" aria-busy={studentsLoading}>
              {studentsLoading ? <p role="status" className="p-6 text-center text-xs text-muted-foreground">正在加载学生…</p>
                : studentsError ? <p className="p-6 text-center text-xs text-muted-foreground">学生加载失败 <button type="button" onClick={refreshReports} className="text-primary underline">重试</button></p>
                  : !visibleStudents.length ? <p className="p-6 text-center text-xs leading-6 text-muted-foreground">{studentSearch ? '未找到匹配的学生，试试其他姓名或学号' : '当前组织暂无学生，可选择其他组织'}</p>
                    : visibleStudents.map(student => <button key={student.id} type="button" onClick={() => { setSelectedStudent(student); setMobileSelectorOpen(false) }} aria-pressed={selectedStudent?.id === student.id}
                      className={cn('group flex min-h-16 w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary', selectedStudent?.id === student.id ? 'bg-primary/10' : 'hover:bg-secondary/70')}>
                      <span className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-xs font-medium', selectedStudent?.id === student.id ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground')}>{student.name[0]}</span>
                      <span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{student.name}</span><span className="mt-1 block truncate text-[10px] text-muted-foreground">{[student.grade_name, student.class_name].filter(Boolean).join(' · ') || student.student_no}</span></span>
                      {cadence === 'weekly' && !period ? <span className="shrink-0 text-right"><span className={cn('inline-flex items-center gap-1 text-[10px]', student.latest_report ? 'text-primary' : 'text-muted-foreground')}><span className={cn('h-1.5 w-1.5 rounded-full', student.latest_report ? 'bg-primary' : 'bg-muted-foreground/35')} />{student.latest_report ? '有周报' : '待生成'}</span>{student.latest_report?.period_end ? <span className="mt-1 block font-mono text-[9px] text-muted-foreground">{fmtDate(student.latest_report.period_end)}</span> : null}</span> : null}
                    </button>)}
            </div>
            {staff && pageCount > 1 ? <div className="flex shrink-0 items-center justify-between border-t border-border px-4 py-2 text-[10px] text-muted-foreground">
              <span>第 {studentPage} / {pageCount} 页</span><div className="flex gap-1">
                <button type="button" aria-label="上一页学生" disabled={studentsLoading || studentPage <= 1} onClick={() => { setStudentPage(page => page - 1); setSelectedStudent(null) }} className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-secondary disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-primary"><ChevronLeft className="h-4 w-4" /></button>
                <button type="button" aria-label="下一页学生" disabled={studentsLoading || studentPage >= pageCount} onClick={() => { setStudentPage(page => page + 1); setSelectedStudent(null) }} className="flex h-8 w-8 items-center justify-center rounded-md hover:bg-secondary disabled:opacity-30 focus-visible:ring-2 focus-visible:ring-primary"><ChevronRight className="h-4 w-4" /></button>
              </div>
            </div> : null}
          </section> : <p className="shrink-0 border-t border-border px-4 py-3 text-[11px] leading-5 text-muted-foreground">选择左侧{REPORT_SCOPES[activeScope].label}节点，查看对应群体的营养{periodLabel}。</p>}
        </div>
      </aside>

      <section className="min-w-0 space-y-4" aria-label="营养报告工作区">
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
            <div className="flex max-w-full gap-1 overflow-x-auto" role="group" aria-label="报告层级">
              {availableScopes.map(scope => {
                const Icon = REPORT_SCOPES[scope].icon
                return <button key={scope} type="button" aria-label={`${REPORT_SCOPES[scope].label}报告`} aria-pressed={activeScope === scope} onClick={() => changeScope(scope)}
                  className={cn('flex min-h-10 min-w-10 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary sm:px-4', activeScope === scope ? 'bg-primary/10 font-semibold text-primary' : 'text-muted-foreground hover:bg-secondary hover:text-foreground')}><Icon className="hidden h-3.5 w-3.5 sm:block" /><span>{REPORT_SCOPES[scope].label}<span className="hidden sm:inline">报告</span></span></button>
              })}
            </div>
            <button type="button" onClick={refreshReports} disabled={loading || organizationLoading || studentsLoading} className="ml-auto flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />刷新</button>
          </div>
          <div className="flex flex-wrap items-end gap-3 border-b border-border px-4 py-3 sm:px-5">
            <div className="flex rounded-lg bg-secondary p-1" role="group" aria-label="报告频率">
              {(['weekly', 'monthly'] as const).map(value => <button key={value} type="button" aria-pressed={cadence === value} onClick={() => setCadence(value)}
                className={cn('min-h-9 rounded-md px-4 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', cadence === value ? 'bg-card font-semibold text-primary shadow-sm' : 'text-muted-foreground hover:text-foreground')}>{value === 'weekly' ? '周报' : '月报'}</button>)}
            </div>
            <label className="flex min-w-0 flex-col gap-1 text-[11px] text-muted-foreground">
              {cadence === 'weekly' ? '选择周（周一至周日）' : '选择月份'}
              <input key={cadence} type={cadence === 'weekly' ? 'week' : 'month'} aria-label={cadence === 'weekly' ? '报告周' : '报告月份'}
                placeholder={cadence === 'weekly' ? 'YYYY-Www' : 'YYYY-MM'} value={period}
                onChange={event => cadence === 'weekly' ? setWeekPeriod(event.target.value) : setMonthPeriod(event.target.value)}
                className="min-h-10 max-w-full rounded-lg border border-border bg-background px-3 text-xs text-foreground outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/15" />
            </label>
            <button type="button" aria-pressed={!period} onClick={() => cadence === 'weekly' ? setWeekPeriod('') : setMonthPeriod('')} className={cn(actionClass, !period && 'border-primary/20 bg-primary/5 text-primary')}>最新{periodLabel}</button>
            <p className="flex basis-full flex-wrap gap-x-4 gap-y-1 text-[11px] leading-5 text-muted-foreground">
              <span>{selectedPeriodRange ? `所选周期：${selectedPeriodRange.start} 至 ${selectedPeriodRange.end}` : `正在查看最新${periodLabel}`}</span>
              {hasRole('admin') && generationRange ? <span>生成周期：{generationRange.start} 至 {generationRange.end}{!period ? `（${cadence === 'weekly' ? '上一周' : '上个月'}）` : ''} · 全部{REPORT_SCOPES[activeScope].label}</span> : null}
            </p>
          </div>
          <div className="flex flex-col justify-between gap-4 p-4 sm:p-5 xl:flex-row xl:items-center">
            <div className="min-w-0">
              {currentPath?.length ? <p className="mb-2 truncate text-[10px] text-muted-foreground" title={currentPath.join(' / ')}>{currentPath.join(' / ')}</p> : <p className="mb-2 text-[10px] text-muted-foreground">{activeScope === 'personal' ? '学生膳食记录' : '群体膳食分析'}</p>}
              <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">{currentTitle ? `${currentTitle}的营养报告` : '选择一份营养报告'}{report && !loading ? <span className="rounded bg-primary/5 px-1.5 py-0.5 text-[10px] font-normal text-primary">{period ? '指定周期' : '最新'}{periodLabel}</span> : null}</h2>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                {report && !loading ? <span className="inline-flex items-center gap-1.5 font-mono"><CalendarDays className="h-3 w-3" />{fmtDate(report.period_start)} — {fmtDate(report.period_end)}</span> : <span>{activeScope === 'personal' ? `从左侧组织中选择学生，查看营养${periodLabel}` : '按组织汇总膳食记录与营养变化'}</span>}
                {activeScope === 'personal' && selectedStudent ? <span>学号 {selectedStudent.student_no}</span> : null}
              </div>
            </div>
            {activeScope === 'personal' && report && !loading ? <div className="flex shrink-0 flex-wrap items-center gap-2">
              <ReportDownload key={report.id} reportId={report.id} name={personalContent?.student_name || '学生'} period={`${report.period_start}_${report.period_end}`} />
              {hasRole('admin') ? <button type="button" onClick={() => void pushReport()} disabled={pushing} className={actionClass}>{pushing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}{pushing ? '正在推送…' : '推送报告'}</button> : null}
            </div> : null}
          </div>
        </div>
        {!selectionKey ? <ReportState title={activeScope === 'personal' ? '从一位学生的膳食记录开始' : '选择要查看的组织'} description={activeScope === 'personal' ? `在左侧展开组织，选择班级和学生，即可查看营养${periodLabel}。也可以直接搜索学生姓名或学号。` : `在左侧选择${REPORT_SCOPES[activeScope].label}，查看该范围的整体营养情况。`} />
          : loading ? <ReportState title="正在加载营养报告" description={`正在获取所选对象的${period ? '指定周期' : '最新'}${periodLabel}`} loading />
            : reportError ? <ReportState title="营养报告加载失败" description="请检查网络连接后重新加载。" error onRetry={refreshReports} />
              : personalContent ? <ReportPreview content={personalContent} />
                : groupContent ? <Suspense fallback={<ReportState title="正在加载报告图表" loading />}><GroupReportCard key={selectionKey} content={groupContent} report={report} /></Suspense>
                  : <ReportState title={`暂无营养${periodLabel}`} description={hasRole('admin') ? `当前${activeScope === 'personal' ? '学生' : '组织'}${period ? '在所选周期' : ''}还没有可查看的${periodLabel}。点击页面上方“生成${REPORT_SCOPES[activeScope].label}${periodLabel}”，任务完成后刷新查看。` : `当前对象${period ? '在所选周期' : ''}还没有可查看的${periodLabel}，请联系管理员生成报告。`} />}
        {!selectionKey && staff ? <div className="flex items-center justify-center gap-2 text-[11px] text-muted-foreground"><Network className="h-3.5 w-3.5" />选择组织<ArrowRight className="h-3 w-3" />选择学生<ArrowRight className="h-3 w-3" />查看报告</div> : null}
      </section>
    </div>
  </div>
}
