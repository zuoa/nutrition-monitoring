import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Users, RefreshCw, Search, ChevronRight, ChevronDown, School as SchoolIcon,
  Pencil, Check, X, UserCog,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { orgApi, studentApi, studentSyncApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, fmtDateTime } from '@/lib/utils'

type ClassNode = { id: number; name: string; student_count?: number }
type GradeNode = { id: number; name: string; classes: ClassNode[] }
type StageNode = { id: number; name: string; stage_type?: string | null; grades: GradeNode[] }
type CampusNode = { id: number; name: string; stages: StageNode[] }
type SchoolNode = { id: number; name: string; campuses: CampusNode[] }

type Student = {
  id: number
  student_no: string
  name: string
  class_id: number | null
  class_name: string | null
  grade_name: string | null
  card_no: string | null
  source: string | null
  is_active: boolean
  is_locally_disabled: boolean
  dingtalk_user_id: string | null
}

type Guardian = {
  id: number
  name: string
  relation: string | null
  phone: string | null
  dingtalk_user_id: string | null
  user_id: number | null
}

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
  edu_mock: boolean
}

export default function StudentsPage() {
  const { hasRole } = useAuth()
  const isAdmin = hasRole('admin')

  const [tree, setTree] = useState<SchoolNode[]>([])
  const [treeLoading, setTreeLoading] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const [filter, setFilter] = useState<{ classId?: number; label: string }>({ label: '全部学生' })
  const [search, setSearch] = useState('')
  const [students, setStudents] = useState<Student[]>([])
  const [studentsLoading, setStudentsLoading] = useState(false)

  const [editingCard, setEditingCard] = useState<{ id: number; value: string } | null>(null)
  const [guardianOf, setGuardianOf] = useState<{ student: Student; list: Guardian[] } | null>(null)

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
      if (filter.classId) params.class_id = filter.classId
      if (search.trim()) params.search = search.trim()
      const res = await studentApi.list(params)
      setStudents(res.data?.data?.items || [])
    } catch {
      /* toast handled globally */
    } finally {
      setStudentsLoading(false)
    }
  }, [filter, search])

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
    try {
      const res = await studentApi.guardians(student.id)
      setGuardianOf({ student, list: res.data?.data || [] })
    } catch { /* ignore */ }
  }

  const studentCount = useMemo(
    () => tree.reduce((sum, sch) => sum + sch.campuses.reduce((cs, cam) => cs + cam.stages.reduce((gs, st) => gs + st.grades.reduce((cls, gr) => cls + gr.classes.reduce((c, cl) => c + (cl.student_count || 0), 0), 0), 0), 0), 0),
    [tree],
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Users className="h-5 w-5" />学生与组织</h1>
          <p className="text-sm text-muted-foreground">按 学校 / 校区 / 学段 / 年级 / 班级 浏览学生，绑定消费卡号；可从钉钉家校通讯录同步。</p>
        </div>
        {isAdmin && (
          <button
            onClick={triggerSync}
            disabled={syncing}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
          >
            <RefreshCw className={cn('h-4 w-4', syncing && 'animate-spin')} />
            {syncing ? '提交中…' : '同步钉钉家校通讯录'}
          </button>
        )}
      </div>

      {isAdmin && syncStatus && (
        <div className="rounded-md border bg-card p-3 text-sm">
          <span className="font-medium">同步状态：</span>
          {syncStatus.edu_mock && <span className="mr-2 rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">未配置钉钉凭据（mock）</span>}
          {syncStatus.last ? (
            <span>
              最近一次 <b>{syncStatus.last.status}</b>，学生 {syncStatus.last.success_count ?? 0} 人
              {syncStatus.last.error_message ? `，错误：${syncStatus.last.error_message}` : ''}
              {syncStatus.last.finished_at ? `，完成于 ${fmtDateTime(syncStatus.last.finished_at)}` : ''}
            </span>
          ) : <span className="text-muted-foreground">尚未同步</span>}
          <button onClick={loadSyncStatus} className="ml-3 text-xs text-primary underline">刷新</button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        {/* 组织树 */}
        <div className="rounded-md border bg-card p-3 max-h-[70vh] overflow-auto">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">组织</span>
            <button onClick={loadTree} className="text-xs text-muted-foreground hover:text-foreground">
              {treeLoading ? '加载中…' : '刷新'}
            </button>
          </div>
          <button
            onClick={() => setFilter({ label: '全部学生' })}
            className={cn('mb-1 block w-full rounded px-2 py-1 text-left text-sm hover:bg-accent', filter.classId === undefined && 'bg-accent')}
          >
            全部学生（{studentCount}）
          </button>
          {tree.length === 0 && !treeLoading && (
            <p className="px-2 py-4 text-xs text-muted-foreground">暂无组织数据。管理员可「同步钉钉家校通讯录」或导入学生名单生成。</p>
          )}
          {tree.map(school => (
            <TreeSchool key={school.id} school={school} expanded={expanded} toggle={toggle}
              selectedClassId={filter.classId} onSelectClass={(id, label) => setFilter({ classId: id, label })} />
          ))}
        </div>

        {/* 学生表 */}
        <div className="rounded-md border bg-card">
          <div className="flex items-center justify-between gap-2 border-b p-3">
            <div className="text-sm font-medium truncate">{filter.label}</div>
            <div className="relative w-64">
              <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="搜索姓名 / 学号"
                className="w-full rounded-md border bg-background py-1.5 pl-8 pr-2 text-sm"
              />
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">学号</th>
                  <th className="px-3 py-2">姓名</th>
                  <th className="px-3 py-2">班级</th>
                  <th className="px-3 py-2">消费卡号</th>
                  <th className="px-3 py-2">来源</th>
                  <th className="px-3 py-2">监护人</th>
                </tr>
              </thead>
              <tbody>
                {studentsLoading && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">加载中…</td></tr>
                )}
                {!studentsLoading && students.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-6 text-center text-muted-foreground">暂无学生</td></tr>
                )}
                {students.map(s => (
                  <tr key={s.id} className="border-t hover:bg-accent/40">
                    <td className="px-3 py-2 font-mono text-xs">{s.student_no}</td>
                    <td className="px-3 py-2">
                      {s.name}
                      {s.is_locally_disabled && <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">已禁用</span>}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{s.class_name || '—'}</td>
                    <td className="px-3 py-2">
                      {editingCard?.id === s.id ? (
                        <span className="flex items-center gap-1">
                          <input
                            autoFocus
                            value={editingCard.value}
                            onChange={e => setEditingCard({ id: s.id, value: e.target.value })}
                            onKeyDown={e => { if (e.key === 'Enter') saveCard(s.id); if (e.key === 'Escape') setEditingCard(null) }}
                            className="w-32 rounded border bg-background px-1.5 py-0.5 text-xs font-mono"
                          />
                          <button onClick={() => saveCard(s.id)} className="text-green-600 hover:text-green-700"><Check className="h-4 w-4" /></button>
                          <button onClick={() => setEditingCard(null)} className="text-muted-foreground hover:text-foreground"><X className="h-4 w-4" /></button>
                        </span>
                      ) : (
                        <span className="flex items-center gap-1">
                          <span className="font-mono text-xs">{s.card_no || '—'}</span>
                          {isAdmin && (
                            <button onClick={() => setEditingCard({ id: s.id, value: s.card_no || '' })} className="text-muted-foreground hover:text-foreground"><Pencil className="h-3.5 w-3.5" /></button>
                          )}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <SourceTag source={s.source} />
                    </td>
                    <td className="px-3 py-2">
                      <button onClick={() => openGuardians(s)} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                        <UserCog className="h-3.5 w-3.5" />查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {guardianOf && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setGuardianOf(null)}>
          <div className="w-full max-w-lg rounded-md border bg-card p-4 shadow-lg" onClick={e => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-medium">{guardianOf.student.name} 的监护人</h3>
              <button onClick={() => setGuardianOf(null)}><X className="h-4 w-4" /></button>
            </div>
            {guardianOf.list.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无监护人（可同步钉钉家校通讯录获取）</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground"><tr><th className="px-2 py-1">姓名</th><th className="px-2 py-1">关系</th><th className="px-2 py-1">手机</th><th className="px-2 py-1">已关联账号</th></tr></thead>
                <tbody>
                  {guardianOf.list.map(g => (
                    <tr key={g.id} className="border-t">
                      <td className="px-2 py-1">{g.name}</td>
                      <td className="px-2 py-1 text-muted-foreground">{g.relation || '—'}</td>
                      <td className="px-2 py-1 font-mono text-xs">{g.phone || '—'}</td>
                      <td className="px-2 py-1">{g.user_id ? <span className="text-green-600">已关联</span> : <span className="text-muted-foreground">未关联</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
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
      <button onClick={() => toggle(key)} className="flex w-full items-center gap-1 rounded px-1 py-1 text-left text-sm hover:bg-accent">
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <SchoolIcon className="h-3.5 w-3.5 text-primary" />
        <span className="font-medium">{school.name}</span>
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
      <button onClick={() => toggle(key)} className="flex w-full items-center gap-1 rounded px-1 py-1 text-left text-sm hover:bg-accent">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span>🏫 {campus.name}</span>
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
      <button onClick={() => toggle(key)} className="flex w-full items-center gap-1 rounded px-1 py-1 text-left text-sm hover:bg-accent">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span>📐 {stage.name}</span>
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
      <button onClick={() => toggle(key)} className="flex w-full items-center gap-1 rounded px-1 py-1 text-left text-sm hover:bg-accent">
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span>🎓 {grade.name}</span>
      </button>
      {open && grade.classes.map(cl => (
        <div key={cl.id} className="ml-3">
          <button
            onClick={() => onSelectClass(cl.id, cl.name)}
            className={cn('flex w-full items-center justify-between rounded px-1 py-1 text-left text-sm hover:bg-accent', selectedClassId === cl.id && 'bg-accent')}
          >
            <span>🏷️ {cl.name}</span>
            <span className="text-xs text-muted-foreground">{cl.student_count ?? 0}</span>
          </button>
        </div>
      ))}
    </div>
  )
}

function SourceTag({ source }: { source: string | null }) {
  if (source === 'dingtalk') return <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800">钉钉</span>
  if (source === 'csv') return <span className="rounded bg-purple-100 px-1.5 py-0.5 text-xs text-purple-800">CSV</span>
  return <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">本地</span>
}
