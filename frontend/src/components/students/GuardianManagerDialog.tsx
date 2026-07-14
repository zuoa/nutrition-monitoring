import { useCallback, useEffect, useState } from 'react'
import { Pencil, Plus, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { adminApi, studentApi } from '@/api/client'
import { AdminDialogShell, fieldClassName, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'
import type { Guardian, Student } from './adminTypes'

const emptyForm = { name: '', relation: '', phone: '', user_id: '' }

export function GuardianManagerDialog({ student, isAdmin, onClose }: { student: Student; isAdmin: boolean; onClose: () => void }) {
  const [guardians, setGuardians] = useState<Guardian[]>([])
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState(emptyForm)
  const [loading, setLoading] = useState(true)
  const [parents, setParents] = useState<Array<{ id: number; name: string; username: string | null }>>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [guardianResponse, parentResponse] = await Promise.all([
        studentApi.guardians(student.id),
        isAdmin ? adminApi.users({ role: 'parent', page_size: 100 }) : Promise.resolve(null),
      ])
      setGuardians(guardianResponse.data?.data || [])
      setParents(parentResponse?.data?.data?.items || [])
    } finally {
      setLoading(false)
    }
  }, [isAdmin, student.id])

  useEffect(() => { load() }, [load])

  const edit = (guardian: Guardian) => {
    setEditingId(guardian.id)
    setForm({ name: guardian.name, relation: guardian.relation || '', phone: guardian.phone || '', user_id: guardian.user_id ? String(guardian.user_id) : '' })
  }

  const save = async () => {
    if (!form.name.trim()) {
      toast.error('请填写监护人姓名')
      return
    }
    const payload = { ...form, name: form.name.trim(), user_id: form.user_id ? Number(form.user_id) : null }
    if (editingId) await studentApi.updateGuardian(student.id, editingId, payload)
    else await studentApi.createGuardian(student.id, payload)
    toast.success(editingId ? '监护人信息已保存' : '监护人已添加')
    setEditingId(null)
    setForm(emptyForm)
    await load()
  }

  const remove = async (guardian: Guardian) => {
    if (!window.confirm(`确认删除与“${guardian.name}”的监护关系？`)) return
    await studentApi.deleteGuardian(student.id, guardian.id)
    toast.success('监护关系已删除')
    await load()
  }

  return (
    <AdminDialogShell title={`${student.name} 的监护人`} description="手机号用于联系；关联已有家长账号后，该账号可查看此学生。" onClose={onClose}>
      {isAdmin ? <div className="mb-5 rounded-lg border border-border bg-secondary/20 p-4"><h3 className="mb-3 flex items-center gap-2 text-sm font-medium"><Plus className="h-4 w-4 text-primary" />{editingId ? '编辑监护人' : '添加监护人'}</h3><div className="grid gap-3 sm:grid-cols-2"><input className={fieldClassName} placeholder="姓名 *" value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} /><input className={fieldClassName} placeholder="关系，如父、母" value={form.relation} onChange={event => setForm(current => ({ ...current, relation: event.target.value }))} /><input className={fieldClassName} placeholder="手机号" value={form.phone} onChange={event => setForm(current => ({ ...current, phone: event.target.value }))} /><select className={fieldClassName} value={form.user_id} onChange={event => setForm(current => ({ ...current, user_id: event.target.value }))}><option value="">不关联家长账号</option>{parents.map(parent => <option key={parent.id} value={parent.id}>{parent.name}{parent.username ? `（${parent.username}）` : ''} · #{parent.id}</option>)}</select></div><div className="mt-3 flex justify-end gap-2">{editingId ? <button type="button" className={secondaryButtonClassName} onClick={() => { setEditingId(null); setForm(emptyForm) }}>取消编辑</button> : null}<button type="button" className={primaryButtonClassName} onClick={save}>{editingId ? '保存修改' : '添加监护人'}</button></div></div> : null}
      <div className="overflow-hidden rounded-lg border border-border">
        {loading ? <div className="p-8 text-center text-sm text-muted-foreground">加载中…</div> : guardians.length === 0 ? <div className="p-8 text-center text-sm text-muted-foreground">暂无监护人，可在上方手工添加</div> : <table className="w-full text-sm"><thead className="bg-secondary/40 text-left text-xs text-muted-foreground"><tr><th className="px-3 py-2">姓名</th><th className="px-3 py-2">关系</th><th className="px-3 py-2">手机</th><th className="px-3 py-2">家长账号</th>{isAdmin ? <th className="px-3 py-2 text-right">操作</th> : null}</tr></thead><tbody>{guardians.map(guardian => <tr key={guardian.id} className="border-t border-border"><td className="px-3 py-2">{guardian.name}</td><td className="px-3 py-2 text-muted-foreground">{guardian.relation || '—'}</td><td className="px-3 py-2 font-mono text-xs">{guardian.phone || '—'}</td><td className="px-3 py-2">{guardian.user_id ? `#${guardian.user_id}` : '未关联'}</td>{isAdmin ? <td className="px-3 py-2"><div className="flex justify-end"><button type="button" className="rounded p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground" onClick={() => edit(guardian)} aria-label="编辑"><Pencil className="h-3.5 w-3.5" /></button><button type="button" className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" onClick={() => remove(guardian)} aria-label="删除"><Trash2 className="h-3.5 w-3.5" /></button></div></td> : null}</tr>)}</tbody></table>}
      </div>
    </AdminDialogShell>
  )
}
