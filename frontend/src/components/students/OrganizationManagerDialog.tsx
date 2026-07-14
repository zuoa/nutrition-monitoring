import { useCallback, useEffect, useMemo, useState } from 'react'
import { Archive, CornerDownRight, Pencil, Plus, RotateCcw, Save } from 'lucide-react'
import toast from 'react-hot-toast'
import { orgApi } from '@/api/client'
import { AdminDialogShell, fieldClassName, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'
import type { SchoolNode } from './adminTypes'

type Kind = 'schools' | 'campuses' | 'stages' | 'grades' | 'classes'
type FlatNode = { kind: Kind; id: number; name: string; path: string; active: boolean; depth: number }

const kindLabels: Record<Kind, string> = { schools: '学校', campuses: '校区', stages: '学段', grades: '年级', classes: '班级' }
const parentFields: Partial<Record<Kind, string>> = { campuses: 'school_id', stages: 'campus_id', grades: 'stage_id', classes: 'grade_id' }

function flattenTree(tree: SchoolNode[]): FlatNode[] {
  const result: FlatNode[] = []
  for (const school of tree) {
    result.push({ kind: 'schools', id: school.id, name: school.name, path: school.name, active: school.is_active, depth: 0 })
    for (const campus of school.campuses) {
      const campusPath = `${school.name} / ${campus.name}`
      result.push({ kind: 'campuses', id: campus.id, name: campus.name, path: campusPath, active: campus.is_active, depth: 1 })
      for (const stage of campus.stages) {
        const stagePath = `${campusPath} / ${stage.name}`
        result.push({ kind: 'stages', id: stage.id, name: stage.name, path: stagePath, active: stage.is_active, depth: 2 })
        for (const grade of stage.grades) {
          const gradePath = `${stagePath} / ${grade.name}`
          result.push({ kind: 'grades', id: grade.id, name: grade.name, path: gradePath, active: grade.is_active, depth: 3 })
          for (const classNode of grade.classes) {
            result.push({ kind: 'classes', id: classNode.id, name: classNode.name, path: `${gradePath} / ${classNode.name}`, active: classNode.is_active, depth: 4 })
          }
        }
      }
    }
  }
  return result
}

export function OrganizationManagerDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [tree, setTree] = useState<SchoolNode[]>([])
  const [loading, setLoading] = useState(true)
  const [kind, setKind] = useState<Kind>('classes')
  const [name, setName] = useState('')
  const [parentId, setParentId] = useState('')
  const [stageType, setStageType] = useState('other')
  const [editing, setEditing] = useState<{ kind: Kind; id: number; name: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const response = await orgApi.tree(true)
      setTree(response.data?.data || [])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])
  const nodes = useMemo(() => flattenTree(tree), [tree])
  const parentKind: Partial<Record<Kind, Kind>> = { campuses: 'schools', stages: 'campuses', grades: 'stages', classes: 'grades' }
  const parentOptions = nodes.filter(node => node.kind === parentKind[kind] && node.active)

  const create = async () => {
    if (!name.trim() || (kind !== 'schools' && !parentId)) {
      toast.error('请填写名称并选择上级组织')
      return
    }
    const payload: Record<string, any> = { name: name.trim() }
    const parentField = parentFields[kind]
    if (parentField) payload[parentField] = Number(parentId)
    if (kind === 'stages') payload.stage_type = stageType
    await orgApi.create(kind, payload)
    toast.success(`${kindLabels[kind]}已创建`)
    setName('')
    await load()
    onChanged()
  }

  const saveEdit = async () => {
    if (!editing?.name.trim()) return
    await orgApi.update(editing.kind, editing.id, { name: editing.name.trim() })
    toast.success('名称已保存')
    setEditing(null)
    await load()
    onChanged()
  }

  const toggleArchived = async (node: FlatNode) => {
    if (node.active) await orgApi.archive(node.kind, node.id)
    else await orgApi.restore(node.kind, node.id)
    toast.success(node.active ? '组织节点已归档' : '组织节点已恢复')
    await load()
    onChanged()
  }

  return (
    <AdminDialogShell title="组织维护" description="按学校到班级逐级维护。归档前必须先清空活跃下级或在校学生。" onClose={onClose} wide>
      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <aside className="h-fit rounded-lg border border-border bg-secondary/25 p-4">
          <div className="mb-4 flex items-center gap-2 text-sm font-medium"><Plus className="h-4 w-4 text-primary" />新建组织节点</div>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-sm"><span>类型</span><select className={fieldClassName} value={kind} onChange={event => { setKind(event.target.value as Kind); setParentId('') }}>{Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            {kind !== 'schools' ? <label className="block space-y-1.5 text-sm"><span>上级组织</span><select className={fieldClassName} value={parentId} onChange={event => setParentId(event.target.value)}><option value="">请选择</option>{parentOptions.map(node => <option key={`${node.kind}-${node.id}`} value={node.id}>{node.path}</option>)}</select></label> : null}
            <label className="block space-y-1.5 text-sm"><span>名称</span><input className={fieldClassName} value={name} onChange={event => setName(event.target.value)} placeholder={`例如：${kind === 'classes' ? '七年级（1）班' : kindLabels[kind]}`} /></label>
            {kind === 'stages' ? <label className="block space-y-1.5 text-sm"><span>学段类型</span><select className={fieldClassName} value={stageType} onChange={event => setStageType(event.target.value)}><option value="kindergarten">幼儿园</option><option value="primary">小学</option><option value="junior">初中</option><option value="senior">高中</option><option value="other">其他</option></select></label> : null}
            <button type="button" className={`${primaryButtonClassName} w-full`} onClick={create}>创建{kindLabels[kind]}</button>
          </div>
        </aside>

        <div className="min-w-0 overflow-hidden rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-border px-4 py-3"><div><h3 className="text-sm font-medium">组织结构</h3><p className="text-xs text-muted-foreground">共 {nodes.length} 个节点，灰色节点已归档</p></div><button type="button" className={secondaryButtonClassName} onClick={load}>{loading ? '刷新中…' : '刷新'}</button></div>
          <div className="max-h-[58vh] overflow-auto divide-y divide-border">
            {!loading && nodes.length === 0 ? <div className="p-8 text-center text-sm text-muted-foreground">先从创建学校开始建立组织结构</div> : null}
            {nodes.map(node => (
              <div key={`${node.kind}-${node.id}`} className={`flex items-center gap-3 px-3 py-2.5 ${node.active ? '' : 'bg-secondary/40 text-muted-foreground'}`}>
                <div className="flex min-w-0 flex-1 items-center" style={{ paddingLeft: `${node.depth * 18}px` }}>
                  {node.depth > 0 ? <CornerDownRight className="mr-2 h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" /> : null}
                  {editing?.kind === node.kind && editing.id === node.id ? <input autoFocus className={`${fieldClassName} py-1`} value={editing.name} onChange={event => setEditing(current => current ? { ...current, name: event.target.value } : current)} onKeyDown={event => { if (event.key === 'Enter') saveEdit(); if (event.key === 'Escape') setEditing(null) }} /> : <div className="min-w-0"><div className="truncate text-sm">{node.name}</div><div className="text-[11px] text-muted-foreground">{kindLabels[node.kind]} · #{node.id}{node.active ? '' : ' · 已归档'}</div></div>}
                </div>
                {editing?.kind === node.kind && editing.id === node.id ? <button type="button" onClick={saveEdit} className="rounded p-2 text-primary hover:bg-secondary" aria-label="保存"><Save className="h-4 w-4" /></button> : <button type="button" onClick={() => setEditing({ kind: node.kind, id: node.id, name: node.name })} className="rounded p-2 text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label="改名"><Pencil className="h-4 w-4" /></button>}
                <button type="button" onClick={() => toggleArchived(node)} className="rounded p-2 text-muted-foreground hover:bg-secondary hover:text-foreground" aria-label={node.active ? '归档' : '恢复'}>{node.active ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}</button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </AdminDialogShell>
  )
}
