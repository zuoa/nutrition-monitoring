import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Users, RefreshCw, Search, ChevronRight, ChevronDown, School as SchoolIcon,
  Pencil, Check, X, UserCog, Building2, GraduationCap, BookOpen, Tag,
  Plus, Upload, Settings2, ArrowUpRight, Trash2,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { orgApi, studentApi, studentSyncApi } from '@/api/client'
import { DataPagination } from '@/components/ui/DataPagination'
import { StudentRecordsPopover } from '@/components/students/StudentRecordsPopover'
import { useUrlPage } from '@/hooks/useUrlPage'
import { useAuth } from '@/contexts/AuthContext'
import { cn, fmtDateTime } from '@/lib/utils'
import { StudentEditorDialog } from '@/components/students/StudentEditorDialog'
import { StudentImportDialog } from '@/components/students/StudentImportDialog'
import { OrganizationManagerDialog } from '@/components/students/OrganizationManagerDialog'
import { PromotionDialog } from '@/components/students/PromotionDialog'
import { GuardianManagerDialog } from '@/components/students/GuardianManagerDialog'
import { classOptions, type CampusNode, type ClassNode, type GradeNode, type SchoolNode, type StageNode, type Student } from '@/components/students/adminTypes'

type SyncStatus = {
  last: {
    status: string
    total_count: number
    success_count: number
    error_message: string | null
    started_at: string | null
    finished_at: string | null
    meta: Record<string, any>
  } | null
  provider: {
    key: string
    label: string
    configured: boolean
    mock: boolean
    can_sync: boolean
    error?: string
  }
}

const STUDENT_PAGE_SIZE = 20

export default function StudentsPage() {
  const { hasRole } = useAuth()
  const isAdmin = hasRole('admin')

  const [tree, setTree] = useState<SchoolNode[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const [filter, setFilter] = useState<{ classId?: number; label: string }>({ label: '全部学生' })
  const [search, setSearch] = useState('')
  const [students, setStudents] = useState<Student[]>([])
  const [studentsPage, setStudentsPage] = useUrlPage()
  const [studentsTotal, setStudentsTotal] = useState(0)
  const [studentsTotalPages, setStudentsTotalPages] = useState(1)
  const [studentsLoading, setStudentsLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<'enrolled' | 'disabled' | 'graduated' | 'all'>('enrolled')

  const [editingCard, setEditingCard] = useState<{ id: number; value: string } | null>(null)
  const [guardianOf, setGuardianOf] = useState<Student | null>(null)
  const [studentEditor, setStudentEditor] = useState<{ student: Student | null } | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [showOrganization, setShowOrganization] = useState(false)
  const [showPromotion, setShowPromotion] = useState(false)
  const [deletingStudentId, setDeletingStudentId] = useState<number | null>(null)

  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)

  const loadTree = useCallback(async () => {
    setTreeLoading(true)
    try {
      const res = await orgApi.tree()
      setTree(res.data?.data || [])
    } catch {
      /* toast handled globally */
    } finally {
      setTreeLoading(false)
    }
  }, [])

  const loadStudents = useCallback(async () => {
    setStudentsLoading(true)
    try {
      const params: Record<string, any> = {}
      params.page = studentsPage
      params.page_size = STUDENT_PAGE_SIZE
      params.status = statusFilter
      if (filter.classId) params.class_id = filter.classId
      if (search.trim()) params.search = search.trim()
      const res = await studentApi.list(params)
      const data = res.data?.data || {}
      setStudents(data.items || [])
      setStudentsTotal(Number(data.total || 0))
      setStudentsTotalPages(Math.max(1, Number(data.total_pages || 1)))
      if (data.page && Number(data.page) !== studentsPage) {
        setStudentsPage(Number(data.page))
      }
    } catch {
      /* toast handled globally */
    } finally {
      setStudentsLoading(false)
    }
  }, [filter, search, statusFilter, studentsPage])

  const loadSyncStatus = useCallback(async () => {
    try {
      const res = await studentSyncApi.status()
      setSyncStatus(res.data?.data || null)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => { loadTree(); loadSyncStatus() }, [loadTree, loadSyncStatus])
  useEffect(() => { loadStudents() }, [loadStudents])

  const toggle = (key: string) => setExpanded(s => ({ ...s, [key]: !s[key] }))

  const triggerSync = async () => {
    setSyncing(true)
    try {
      await studentSyncApi.trigger()
      toast.success('同步任务已提交，稍后刷新查看结果')
      setTimeout(loadSyncStatus, 1500)
    } finally {
      setSyncing(false)
    }
  }

  const saveCard = async (studentId: number) => {
    if (!editingCard) return
    try {
      await studentApi.update(studentId, { card_no: editingCard.value || null })
      setStudents(list => list.map(s => s.id === studentId ? { ...s, card_no: editingCard.value || null } : s))
      toast.success('消费卡号已保存')
    } finally {
      setEditingCard(null)
    }
  }

  const openGuardians = async (student: Student) => {
    setGuardianOf(student)
  }

  const removeStudent = async (student: Student) => {
    if (!window.confirm(`确定删除学生“${student.name}”吗？学生将移入“已停用”，历史记录会保留，可随时恢复。`)) return

    setDeletingStudentId(student.id)
    try {
      await studentApi.delete(student.id)
      toast.success('学生已删除，可在“已停用”中恢复')
      await Promise.all([loadTree(), loadStudents()])
    } finally {
      setDeletingStudentId(null)
    }
  }

  const studentCount = useMemo(
    () => tree.reduce((sum, sch) => sum + sch.campuses.reduce((cs, cam) => cs + cam.stages.reduce((gs, st) => gs + st.grades.reduce((cls, gr) => cls + gr.classes.reduce((c, cl) => c + (cl.student_count || 0), 0), 0), 0), 0), 0),
    [tree],
  )
  const activeClassOptions = useMemo(() => classOptions(tree), [tree])

  const refreshManagementData = useCallback(() => {
    loadTree()
    loadStudents()
  }, [loadStudents, loadTree])

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold"><Users className="h-5 w-5" />学生与组织</h1>
          <p className="text-sm text-muted-foreground">按学校、校区、学段、年级和班级维护学生；支持本地录入、名单导入与整班升年级。</p>
        </div>
        {isAdmin && <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setShowOrganization(true)} className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm transition hover:bg-secondary"><Settings2 className="h-4 w-4" />组织维护</button>
          <button type="button" onClick={() => setShowImport(true)} className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm transition hover:bg-secondary"><Upload className="h-4 w-4" />导入名单</button>
          <button type="button" onClick={() => setShowPromotion(true)} className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm transition hover:bg-secondary"><ArrowUpRight className="h-4 w-4" />批量升年级</button>
          <button type="button" onClick={() => setStudentEditor({ student: null })} className="inline-flex items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"><Plus className="h-4 w-4" />添加学生</button>
        </div>}
      </div>

      {isAdmin && syncStatus && (
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-card p-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <div><span className="font-medium">{syncStatus.provider.label}：</span>{syncStatus.provider.mock && <span className="mr-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">开发模拟数据</span>}{!syncStatus.provider.can_sync && <span className="mr-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">未完成配置</span>}{syncStatus.last ? <span>最近一次 <b>{syncStatus.last.status}</b>，学生 {syncStatus.last.success_count ?? 0} 人{syncStatus.last.error_message ? `，错误：${syncStatus.last.error_message}` : ''}{syncStatus.last.finished_at ? `，完成于 ${fmtDateTime(syncStatus.last.finished_at)}` : ''}</span> : <span className="text-muted-foreground">尚未同步；也可完全使用本地维护</span>}</div>
          <div className="flex flex-shrink-0 gap-3"><button type="button" onClick={loadSyncStatus} className="text-xs text-primary underline">刷新状态</button><button type="button" onClick={triggerSync} disabled={syncing || !syncStatus.provider.can_sync} className="inline-flex items-center gap-1 text-xs text-primary underline disabled:opacity-50"><RefreshCw className={cn('h-3.5 w-3.5', syncing && 'animate-spin')} />{syncing ? '提交中…' : '立即同步'}</button></div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        {/* 组织树 */}
        <div className="max-h-[70vh] overflow-auto rounded-xl border border-border bg-card p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">组织</span>
            <button type="button" onClick={loadTree} className="text-xs text-muted-foreground transition-colors hover:text-foreground">
              {treeLoading ? '加载中…' : '刷新'}
            </button>
          </div>
          <button
            type="button"
            onClick={() => { setFilter({ label: '全部学生' }); setStudentsPage(1) }}
            className={cn('mb-1 block w-full rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-secondary', filter.classId === undefined && 'bg-secondary')}
          >
            全部学生（{studentCount}）
          </button>
          {tree.length === 0 && !treeLoading && (
            <p className="px-2 py-4 text-xs text-muted-foreground">暂无组织数据。管理员可通过外部数据源同步，或导入学生名单生成。</p>
          )}
          {tree.map(school => (
            <TreeSchool key={school.id} school={school} expanded={expanded} toggle={toggle}
              selectedClassId={filter.classId} onSelectClass={(id, label) => { setFilter({ classId: id, label }); setStudentsPage(1) }} />
          ))}
        </div>

        {/* 学生表 */}
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex flex-col gap-3 border-b border-border p-3 xl:flex-row xl:items-center xl:justify-between">
            <div className="text-sm font-medium truncate">{filter.label}</div>
            <div className="flex w-full flex-col gap-2 sm:flex-row xl:w-auto">
              <select value={statusFilter} onChange={event => { setStatusFilter(event.target.value as typeof statusFilter); setStudentsPage(1) }} className="rounded-md border border-border bg-background px-2 py-1.5 text-sm">
                <option value="enrolled">在校学生</option><option value="disabled">已停用</option><option value="graduated">已毕业</option><option value="all">全部状态</option>
              </select>
              <div className="relative w-full sm:w-64">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <input value={search} onChange={e => { setSearch(e.target.value); setStudentsPage(1) }} placeholder="搜索姓名 / 学号" className="w-full rounded-md border border-border bg-background py-1.5 pl-8 pr-2 text-sm outline-none transition-colors focus:border-foreground/30 focus:ring-1 focus:ring-foreground/20" />
              </div>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="data-table min-w-[840px]">
              <thead>
                <tr>
                  <th>学号</th>
                  <th>姓名</th>
                  <th>班级</th>
                  <th>消费卡号</th>
                  <th>来源</th>
                  <th>状态</th>
                  <th>消费记录</th>
                  <th>监护人</th>
                  {isAdmin && <th>操作</th>}
                </tr>
              </thead>
              <tbody>
                {studentsLoading && (
                  <tr><td colSpan={isAdmin ? 10 : 9} className="py-12 text-center text-muted-foreground">加载中…</td></tr>
                )}
                {!studentsLoading && students.length === 0 && (
                  <tr><td colSpan={isAdmin ? 10 : 9} className="py-12 text-center text-muted-foreground">暂无学生</td></tr>
                )}
                {students.map(s => (
                  <tr key={s.id}>
                    <td className="font-mono text-xs">{s.student_no}</td>
                    <td>
                      {s.name}
                      {s.is_locally_disabled && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">已禁用</span>}
                    </td>
                    <td className="text-muted-foreground">{s.class_name || '—'}</td>
                    <td>
                      {editingCard?.id === s.id ? (
                        <span className="flex items-center gap-1">
                          <input
                            autoFocus
                            value={editingCard.value}
                            onChange={e => setEditingCard({ id: s.id, value: e.target.value })}
                            onKeyDown={e => { if (e.key === 'Enter') saveCard(s.id); if (e.key === 'Escape') setEditingCard(null) }}
                            className="w-32 rounded border border-border bg-background px-1.5 py-0.5 text-xs font-mono outline-none focus:border-foreground/30 focus:ring-1 focus:ring-foreground/20"
                          />
                          <button type="button" onClick={() => saveCard(s.id)} className="rounded p-1 text-green-600 transition-colors hover:bg-secondary hover:text-green-700"><Check className="h-4 w-4" /></button>
                          <button type="button" onClick={() => setEditingCard(null)} className="rounded p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-1">
                          <span className="font-mono text-xs">{s.card_no || '—'}</span>
                          {isAdmin && (
                            <button type="button" onClick={() => setEditingCard({ id: s.id, value: s.card_no || '' })} className="rounded p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                          )}
                        </span>
                      )}
                    </td>
                    <td>
                      <SourceTag source={s.source} />
                    </td>
                    <td><StudentStatusTag student={s} /></td>
                    <td>
                      <StudentRecordsPopover student={s} />
                    </td>
                    <td>
                      <button type="button" onClick={() => openGuardians(s)} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                        <UserCog className="h-3.5 w-3.5" />查看
                      </button>
                    </td>
                    {isAdmin && <td><div className="flex items-center gap-3">
                      <button type="button" onClick={() => setStudentEditor({ student: s })} className="inline-flex items-center gap-1 text-xs text-primary hover:underline"><Pencil className="h-3.5 w-3.5" />编辑</button>
                      {!s.is_locally_disabled ? <button type="button" onClick={() => removeStudent(s)} disabled={deletingStudentId === s.id} className="inline-flex items-center gap-1 text-xs text-destructive hover:underline disabled:cursor-not-allowed disabled:opacity-50"><Trash2 className="h-3.5 w-3.5" />{deletingStudentId === s.id ? '删除中…' : '删除'}</button> : null}
                    </div></td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {studentsTotalPages > 1 && (
            <DataPagination
              page={studentsPage}
              totalPages={studentsTotalPages}
              totalItems={studentsTotal}
              disabled={studentsLoading}
              onPageChange={setStudentsPage}
              className="border-t border-border p-3"
              ariaLabel="学生分页"
            />
          )}
        </div>
      </div>

      {guardianOf ? <GuardianManagerDialog student={guardianOf} isAdmin={isAdmin} onClose={() => setGuardianOf(null)} /> : null}
      {studentEditor ? <StudentEditorDialog student={studentEditor.student} classes={activeClassOptions} onClose={() => setStudentEditor(null)} onSaved={() => { setStudentEditor(null); refreshManagementData() }} /> : null}
      {showImport ? <StudentImportDialog onClose={() => setShowImport(false)} onImported={() => { setShowImport(false); refreshManagementData() }} /> : null}
      {showOrganization ? <OrganizationManagerDialog onClose={() => setShowOrganization(false)} onChanged={refreshManagementData} /> : null}
      {showPromotion ? <PromotionDialog tree={tree} onClose={() => setShowPromotion(false)} onCompleted={() => { setShowPromotion(false); refreshManagementData() }} /> : null}
    </div>
  )
}

function TreeSchool({ school, expanded, toggle, selectedClassId, onSelectClass }: {
  school: SchoolNode
  expanded: Record<string, boolean>
  toggle: (k: string) => void
  selectedClassId?: number
  onSelectClass: (id: number, label: string) => void
}) {
  const key = `s${school.id}`
  const open = expanded[key]
  return (
    <div>
      <button type="button" onClick={() => toggle(key)} className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-secondary">
        {open ? <ChevronDown className="h-3.5 w-3.5 flex-shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 flex-shrink-0" />}
        <SchoolIcon className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
        <span className="truncate font-medium">{school.name}</span>
      </button>
      {open && school.campuses.map(cam => (
        <TreeCampus key={cam.id} campus={cam} expanded={expanded} toggle={toggle} prefix={`${key}`} selectedClassId={selectedClassId} onSelectClass={onSelectClass} />
      ))}
    </div>
  )
}

function TreeCampus({ campus, expanded, toggle, prefix, selectedClassId, onSelectClass }: {
  campus: CampusNode; expanded: Record<string, boolean>; toggle: (k: string) => void; prefix: string; selectedClassId?: number; onSelectClass: (id: number, label: string) => void
}) {
  const key = `${prefix}-c${campus.id}`
  const open = expanded[key]
  return (
    <div className="ml-3">
      <button type="button" onClick={() => toggle(key)} className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-secondary">
        {open ? <ChevronDown className="h-3 w-3 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 flex-shrink-0" />}
        <Building2 className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="truncate">{campus.name}</span>
      </button>
      {open && campus.stages.map(st => (
        <TreeStage key={st.id} stage={st} expanded={expanded} toggle={toggle} prefix={key} selectedClassId={selectedClassId} onSelectClass={onSelectClass} />
      ))}
    </div>
  )
}

function TreeStage({ stage, expanded, toggle, prefix, selectedClassId, onSelectClass }: {
  stage: StageNode; expanded: Record<string, boolean>; toggle: (k: string) => void; prefix: string; selectedClassId?: number; onSelectClass: (id: number, label: string) => void
}) {
  const key = `${prefix}-st${stage.id}`
  const open = expanded[key]
  return (
    <div className="ml-3">
      <button type="button" onClick={() => toggle(key)} className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-secondary">
        {open ? <ChevronDown className="h-3 w-3 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 flex-shrink-0" />}
        <BookOpen className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="truncate">{stage.name}</span>
      </button>
      {open && stage.grades.map(gr => (
        <TreeGrade key={gr.id} grade={gr} expanded={expanded} toggle={toggle} prefix={key} selectedClassId={selectedClassId} onSelectClass={onSelectClass} />
      ))}
    </div>
  )
}

function TreeGrade({ grade, expanded, toggle, prefix, selectedClassId, onSelectClass }: {
  grade: GradeNode; expanded: Record<string, boolean>; toggle: (k: string) => void; prefix: string; selectedClassId?: number; onSelectClass: (id: number, label: string) => void
}) {
  const key = `${prefix}-g${grade.id}`
  const open = expanded[key]
  return (
    <div className="ml-3">
      <button type="button" onClick={() => toggle(key)} className="flex w-full items-center gap-1.5 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-secondary">
        {open ? <ChevronDown className="h-3 w-3 flex-shrink-0" /> : <ChevronRight className="h-3 w-3 flex-shrink-0" />}
        <GraduationCap className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="truncate">{grade.name}</span>
      </button>
      {open && grade.classes.map(cl => (
        <div key={cl.id} className="ml-3">
          <button
            type="button"
            onClick={() => onSelectClass(cl.id, cl.name)}
            className={cn('flex w-full items-center justify-between gap-2 rounded-md px-1.5 py-1.5 text-left text-sm transition-colors hover:bg-secondary', selectedClassId === cl.id && 'bg-secondary text-foreground')}
          >
            <span className="flex min-w-0 items-center gap-1.5">
              <Tag className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
              <span className="truncate">{cl.name}</span>
            </span>
            <span className="text-xs text-muted-foreground">{cl.student_count ?? 0}</span>
          </button>
        </div>
      ))}
    </div>
  )
}

function SourceTag({ source }: { source: string | null }) {
  if (source === 'dingtalk') return <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800">钉钉</span>
  if (source === 'api') return <span className="rounded bg-cyan-100 px-1.5 py-0.5 text-xs text-cyan-800">外部接口</span>
  if (source === 'csv') return <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-800">CSV</span>
  return <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">本地</span>
}

function StudentStatusTag({ student }: { student: Student }) {
  if (student.enrollment_status === 'graduated') return <span className="rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-700">已毕业</span>
  if (student.is_locally_disabled) return <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">已停用</span>
  return <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-800">在校</span>
}
