import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Building2, Pencil, Plus, RefreshCw, Search, Trash2, UserRound, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { adminApi } from '@/api/client'
import { ROLE_LABELS } from '@/components/admin/adminPageShared'
import { DataPagination } from '@/components/ui/DataPagination'
import { fieldClassName, primaryButtonClassName, secondaryButtonClassName } from '@/components/students/AdminDialogShell'
import { useAuth } from '@/contexts/AuthContext'
import { cn } from '@/lib/utils'
import type { Department, Role, User } from '@/types'

const PAGE_SIZE = 20
type UserForm = {
  name: string; username: string; password: string; role: Role; dept_id: string; is_active: boolean
}
const emptyForm: UserForm = { name: '', username: '', password: '', role: 'canteen_manager', dept_id: '', is_active: true }

export default function UsersPage({ embedded = false }: { embedded?: boolean }) {
  const { user: currentUser } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [departments, setDepartments] = useState<Department[]>([])
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [role, setRole] = useState('')
  const [status, setStatus] = useState('active')
  const [department, setDepartment] = useState('')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<User | null>(null)
  const [form, setForm] = useState<UserForm>(emptyForm)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<number | null>(null)
  const requestId = useRef(0)

  useEffect(() => {
    const timer = window.setTimeout(() => { setQuery(search.trim()); setPage(1) }, 300)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    let active = true
    adminApi.departments().then(res => { if (active) setDepartments(res.data.data || []) }).catch(() => {})
    return () => { active = false }
  }, [])

  const load = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    setError(false)
    try {
      const res = await adminApi.users({ page, page_size: PAGE_SIZE, search: query, role, dept_id: department,
        include_descendants: true, active_only: status === 'active' ? 'true' : 'false', status })
      if (id !== requestId.current) return
      const data = res.data.data
      setUsers(data.items || [])
      setTotal(data.total || 0)
      if (page > Math.max(1, data.total_pages)) setPage(Math.max(1, data.total_pages))
    } catch {
      if (id === requestId.current) setError(true)
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [page, query, role, department, status])

  useEffect(() => { void load(); return () => { requestId.current += 1 } }, [load])

  const edit = (user: User | null) => {
    setEditing(user)
    setForm(user ? { name: user.name, username: user.username || '', password: '', role: user.role,
      dept_id: user.dept_id || '', is_active: user.is_active } : { ...emptyForm, dept_id: department })
    setOpen(true)
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    const data: Record<string, unknown> = { name: form.name.trim(), role: form.role, is_active: form.is_active }
    if (!editing || form.dept_id !== (editing.dept_id || '')) data.dept_id = form.dept_id || null
    if (form.username.trim()) data.username = form.username.trim()
    if (form.password) data.password = form.password
    try {
      if (editing) await adminApi.updateUser(editing.id, data)
      else await adminApi.createUser(data)
      toast.success(editing ? '用户已更新' : '用户已创建')
      setOpen(false)
      setForm(emptyForm)
      await load()
    } catch { /* The API client displays the server's validation message. */ }
    finally { setSaving(false) }
  }

  const remove = async (user: User) => {
    if (!window.confirm(`删除用户「${user.name}」（${user.username || '钉钉登录'}）？账号将停用并无法登录，历史记录会保留，可在“已停用”中恢复。`)) return
    setDeleting(user.id)
    try {
      await adminApi.deleteUser(user.id)
      toast.success('用户已删除，账号已停用')
      await load()
    } catch { /* Reported by the API client. */ }
    finally { setDeleting(null) }
  }

  const isSelf = editing?.id === currentUser?.id
  const needsPassword = !editing || Boolean(form.username.trim() && !editing.has_password)

  return (
    <div className={embedded ? '' : 'p-4 sm:p-6'}>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">组织用户</h1>
          <p className="mt-1 text-sm text-muted-foreground">管理登录账号、人员信息与角色权限</p>
        </div>
        <button className={primaryButtonClassName} onClick={() => edit(null)}><Plus className="mr-1.5 h-4 w-4" />新增用户</button>
      </div>
      <div className="mb-4 grid gap-3 rounded-xl border border-border bg-card p-4 sm:grid-cols-2 xl:grid-cols-[minmax(220px,1fr)_200px_160px_140px_auto]">
        <label className="relative">
          <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
          <input aria-label="搜索姓名或登录名" placeholder="搜索姓名或登录名" className={cn(fieldClassName, 'pl-9')} value={search} onChange={e => setSearch(e.target.value)} />
        </label>
        <select aria-label="筛选组织部门" className={fieldClassName} value={department} onChange={e => { setDepartment(e.target.value); setPage(1) }}>
          <option value="">全部组织部门</option>
          {departments.map(item => <option key={item.id} value={item.dingtalk_dept_id}>{item.name}</option>)}
        </select>
        <select aria-label="筛选角色" className={fieldClassName} value={role} onChange={e => { setRole(e.target.value); setPage(1) }}>
          <option value="">全部角色</option>
          {Object.entries(ROLE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <select aria-label="筛选状态" className={fieldClassName} value={status} onChange={e => { setStatus(e.target.value); setPage(1) }}>
          <option value="active">正常使用</option><option value="inactive">已停用</option><option value="all">全部状态</option>
        </select>
        <button className={secondaryButtonClassName} disabled={loading} onClick={() => void load()}><RefreshCw className={cn('mr-1.5 h-4 w-4', loading && 'animate-spin')} />刷新</button>
      </div>
      <div className="overflow-x-auto rounded-xl border border-border bg-card">
        <table className="data-table min-w-[760px] [&_th]:whitespace-nowrap [&_td]:whitespace-nowrap">
          <thead><tr><th>姓名</th><th>登录名</th><th>角色</th><th>组织部门</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            {loading ? <tr><td colSpan={6} className="py-12 text-center text-muted-foreground">加载中…</td></tr>
              : error ? <tr><td colSpan={6} className="py-12 text-center text-health-red">用户加载失败，请点击刷新重试。</td></tr>
                : users.length === 0 ? <tr><td colSpan={6} className="py-12 text-center text-muted-foreground">没有符合条件的用户，可调整筛选或新增用户。</td></tr>
                  : users.map(user => (
                    <tr key={user.id}>
                      <td><span className="flex items-center gap-2 font-medium"><UserRound className="h-4 w-4 text-muted-foreground" />{user.name}{user.id === currentUser?.id && <span className="text-xs text-muted-foreground">（当前）</span>}</span></td>
                      <td className="font-mono text-sm">{user.username || <span className="font-sans text-xs text-muted-foreground">未设置（钉钉登录）</span>}</td>
                      <td>{ROLE_LABELS[user.role]}</td>
                      <td><span className="flex items-center gap-1.5 text-muted-foreground"><Building2 className="h-3.5 w-3.5" />{user.dept_name || '未分配部门'}</span></td>
                      <td><span className={cn('rounded-full px-2 py-1 text-xs', user.is_active ? 'bg-health-green/10 text-health-green' : 'bg-secondary text-muted-foreground')}>{user.is_active ? '正常' : '已停用'}</span></td>
                      <td><div className="flex gap-2">
                        <button className={secondaryButtonClassName} onClick={() => edit(user)} aria-label={`编辑${user.name}`}><Pencil className="mr-1 h-3.5 w-3.5" />编辑</button>
                        <button className={cn(secondaryButtonClassName, 'text-health-red')} disabled={user.id === currentUser?.id || !user.is_active || deleting === user.id} onClick={() => void remove(user)} aria-label={`删除${user.name}`}><Trash2 className="mr-1 h-3.5 w-3.5" />删除</button>
                      </div></td>
                    </tr>
                  ))}
          </tbody>
        </table>
      </div>
      <DataPagination className="mt-4" page={page} totalPages={Math.ceil(total / PAGE_SIZE)} totalItems={total} disabled={loading} onPageChange={setPage} />
      <Dialog.Root open={open} onOpenChange={value => { if (!saving) { setOpen(value); if (!value) setForm(emptyForm) } }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border border-border bg-card p-6 shadow-xl">
            <Dialog.Title className="text-lg font-semibold">{editing ? '编辑用户' : '新增用户'}</Dialog.Title>
            <Dialog.Description className="mt-1 text-sm text-muted-foreground">{editing ? '修改账号资料，或填写新密码重置登录密码。' : '创建登录账号并分配角色。食堂管理员只使用菜单与菜品管理。'}</Dialog.Description>
            <Dialog.Close className="absolute right-4 top-4 rounded p-1.5 hover:bg-secondary" disabled={saving} aria-label="关闭"><X className="h-4 w-4" /></Dialog.Close>
            <form className="mt-5 space-y-4" onSubmit={save}>
              <label className="block text-sm">姓名<input className={cn(fieldClassName, 'mt-1')} required maxLength={64} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
              <label className="block text-sm">登录名<input className={cn(fieldClassName, 'mt-1 font-mono')} required={!editing || Boolean(editing.username)} maxLength={64} autoComplete="off" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label>
              <label className="block text-sm">{editing ? '新密码（留空保留原密码）' : '初始密码'}<input className={cn(fieldClassName, 'mt-1')} type="password" minLength={8} maxLength={128} required={needsPassword} autoComplete="new-password" placeholder="8–128 个字符" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block text-sm">角色<select aria-label="角色" className={cn(fieldClassName, 'mt-1')} disabled={isSelf} value={form.role} onChange={e => setForm({ ...form, role: e.target.value as Role })}>{Object.entries(ROLE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
                <label className="block text-sm">组织部门<select aria-label="组织部门" className={cn(fieldClassName, 'mt-1')} value={form.dept_id} onChange={e => setForm({ ...form, dept_id: e.target.value })}><option value="">未分配部门</option>{editing?.dept_id && !departments.some(item => item.dingtalk_dept_id === editing.dept_id) && <option value={editing.dept_id} disabled>{editing.dept_name || editing.dept_id}（原部门）</option>}{departments.map(item => <option key={item.id} value={item.dingtalk_dept_id}>{item.name}</option>)}</select></label>
              </div>
              {form.role === 'canteen_manager' && <p className="rounded-lg bg-primary/5 p-3 text-xs text-muted-foreground">登录后进入每日菜单，可维护菜品、价格、营养信息和样图。</p>}
              {editing?.sync_at && <p className="text-xs text-muted-foreground">此用户来自组织同步，姓名和部门可能随下一次同步更新。</p>}
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={isSelf} checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />允许此用户登录</label>
              <div className="flex justify-end gap-2 border-t border-border pt-4"><Dialog.Close className={secondaryButtonClassName} disabled={saving}>取消</Dialog.Close><button className={primaryButtonClassName} type="submit" disabled={saving}>{saving ? '保存中…' : '保存用户'}</button></div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  )
}
