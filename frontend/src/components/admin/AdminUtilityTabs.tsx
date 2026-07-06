import { useEffect, useMemo, useState } from 'react'
import { Building2, ChevronDown, ChevronRight, Database, RefreshCw, Save, Trash2, Upload, Users, Zap } from 'lucide-react'
import type { DropzoneInputProps, DropzoneRootProps } from 'react-dropzone'
import toast from 'react-hot-toast'

import {
  ROLE_LABELS,
  STATUS_LABEL,
  STATUS_STYLE,
  TASK_TYPE_LABEL,
  formatTaskDuration,
} from '@/components/admin/adminPageShared'
import { consumptionApi } from '@/api/client'
import { cn, fmtDateTime } from '@/lib/utils'
import type { Department, TaskLog, User } from '@/types'

type DepartmentTreeNode = Department & {
  children: DepartmentTreeNode[]
  subtreeUserCount: number
}

function buildDepartmentTree(departments: Department[]): DepartmentTreeNode[] {
  const nodes = new Map<string, DepartmentTreeNode>()
  const sortedDepartments = [...departments].sort((a, b) => (
    (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name, 'zh-Hans-CN')
  ))

  sortedDepartments.forEach((department) => {
    nodes.set(department.dingtalk_dept_id, {
      ...department,
      children: [],
      subtreeUserCount: department.user_count || 0,
    })
  })

  const roots: DepartmentTreeNode[] = []
  nodes.forEach((node) => {
    const parentId = node.parent_dingtalk_dept_id || null
    const parent = parentId ? nodes.get(parentId) : null
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  })

  const finalize = (node: DepartmentTreeNode): number => {
    node.children.sort((a, b) => (
      (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name, 'zh-Hans-CN')
    ))
    node.subtreeUserCount = (node.user_count || 0) + node.children.reduce((total, child) => total + finalize(child), 0)
    return node.subtreeUserCount
  }
  roots.forEach(finalize)
  return roots
}

export function UsersAdminTab({
  users,
  usersTotal,
  departments,
  selectedDepartmentId,
  loading,
  deletingUserId,
  onSelectDepartment,
  onRefresh,
  onUpdateUserRole,
  onDeleteUser,
  getRootProps,
  getInputProps,
}: {
  users: User[]
  usersTotal: number
  departments: Department[]
  selectedDepartmentId: string | null
  loading: boolean
  deletingUserId: number | null
  onSelectDepartment: (deptId: string | null) => void
  onRefresh: () => void | Promise<void>
  onUpdateUserRole: (user: User, role: string) => void | Promise<void>
  onDeleteUser: (user: User) => void | Promise<void>
  getRootProps: <T extends DropzoneRootProps>(props?: T) => T
  getInputProps: <T extends DropzoneInputProps>(props?: T) => T
}) {
  const departmentTree = useMemo(() => buildDepartmentTree(departments), [departments])
  const [collapsedDepartmentIds, setCollapsedDepartmentIds] = useState<Set<string>>(new Set())

  const toggleDepartment = (deptId: string) => {
    setCollapsedDepartmentIds((prev) => {
      const next = new Set(prev)
      if (next.has(deptId)) next.delete(deptId)
      else next.add(deptId)
      return next
    })
  }

  const renderDepartmentNode = (node: DepartmentTreeNode, depth = 0) => {
    const hasChildren = node.children.length > 0
    const collapsed = collapsedDepartmentIds.has(node.dingtalk_dept_id)
    const selected = selectedDepartmentId === node.dingtalk_dept_id

    return (
      <div key={node.dingtalk_dept_id}>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => hasChildren && toggleDepartment(node.dingtalk_dept_id)}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-30"
            disabled={!hasChildren}
            title={collapsed ? '展开部门' : '收起部门'}
            aria-label={collapsed ? '展开部门' : '收起部门'}
            style={{ marginLeft: depth * 14 }}
          >
            {hasChildren ? (
              collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <span className="h-1.5 w-1.5 rounded-full bg-border" />
            )}
          </button>
          <button
            type="button"
            onClick={() => onSelectDepartment(node.dingtalk_dept_id)}
            className={cn(
              'flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors',
              selected ? 'bg-primary text-primary-foreground' : 'text-foreground hover:bg-secondary',
            )}
            title={node.name}
          >
            <span className="truncate">{node.name}</span>
            <span className={cn('shrink-0 font-mono text-[11px]', selected ? 'text-primary-foreground/80' : 'text-muted-foreground')}>
              {node.subtreeUserCount}
            </span>
          </button>
        </div>
        {hasChildren && !collapsed && (
          <div className="mt-0.5 space-y-0.5">
            {node.children.map((child) => renderDepartmentNode(child, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="bg-card border border-border rounded-xl p-4 lg:sticky lg:top-4 lg:self-start">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <Building2 className="h-4 w-4 text-muted-foreground" />组织部门
          </h2>
          <span className="text-xs text-muted-foreground">{departments.length} 个部门</span>
        </div>
        <button
          type="button"
          onClick={() => onSelectDepartment(null)}
          className={cn(
            'mb-2 flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors',
            selectedDepartmentId === null ? 'bg-primary text-primary-foreground' : 'hover:bg-secondary',
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <Users className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">全部组织</span>
          </span>
          <span className={cn('shrink-0 font-mono text-[11px]', selectedDepartmentId === null ? 'text-primary-foreground/80' : 'text-muted-foreground')}>
            {selectedDepartmentId === null ? usersTotal : '全部'}
          </span>
        </button>
        <div className="max-h-[560px] space-y-0.5 overflow-y-auto pr-1">
          {departmentTree.length > 0 ? (
            departmentTree.map((node) => renderDepartmentNode(node))
          ) : (
            <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
              暂无部门，请先同步钉钉组织。
            </div>
          )}
        </div>
      </aside>

      <div className="min-w-0 space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-sm text-muted-foreground">共 {usersTotal} 个用户</span>
          <button onClick={onRefresh} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
          </button>
        </div>
        <div className="bg-card border border-border rounded-xl overflow-x-auto">
          <table className="data-table min-w-[720px]">
            <thead><tr><th>姓名</th><th>角色</th><th>部门</th><th>状态</th><th>同步时间</th><th>修改角色</th><th>操作</th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={7} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
              {!loading && users.length === 0 && <tr><td colSpan={7} className="text-center py-12 text-muted-foreground">暂无用户</td></tr>}
              {!loading && users.map((user) => (
                <tr key={user.id} className={!user.is_active ? 'opacity-40' : ''}>
                  <td>
                    <div className="flex items-center gap-2">
                      <div className="w-6 h-6 rounded-full bg-foreground/10 flex items-center justify-center text-xs">{user.name?.[0] || '?'}</div>
                      <span className="text-sm font-medium">{user.name}</span>
                    </div>
                  </td>
                  <td><span className="text-xs">{ROLE_LABELS[user.role] || user.role}</span></td>
                  <td><span className="text-xs text-muted-foreground">{user.dept_name || '—'}</span></td>
                  <td>
                    <span className={cn('text-xs', user.is_active ? 'text-health-green' : 'text-muted-foreground')}>
                      {user.is_active ? '正常' : '已停用'}
                    </span>
                  </td>
                  <td><span className="text-xs font-mono text-muted-foreground">{fmtDateTime(user.sync_at)}</span></td>
                  <td>
                    <select
                      value={user.role}
                      onChange={(event) => onUpdateUserRole(user, event.target.value)}
                      className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none"
                    >
                      {Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => onDeleteUser(user)}
                      disabled={deletingUserId === user.id}
                      title="删除用户"
                      className="inline-flex h-8 w-8 items-center justify-center rounded border border-border text-muted-foreground transition-colors hover:border-health-red/40 hover:bg-health-red/10 hover:text-health-red disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-3 flex items-center gap-2">
            <Upload className="w-4 h-4 text-muted-foreground" />导入学生名单
          </h2>
          <p className="text-xs text-muted-foreground mb-3">
            CSV/Excel 格式，需包含：学号(student_no)、姓名(name)、班级(class_id) 列
          </p>
          <div {...getRootProps()} className="border-2 border-dashed border-border rounded-lg p-6 text-center cursor-pointer hover:border-foreground/30 transition-colors">
            <input {...getInputProps()} />
            <p className="text-sm text-muted-foreground">拖拽文件或点击上传学生名单</p>
          </div>
        </div>
      </div>
    </div>
  )
}

type DbSyncConfigForm = {
  host: string
  port: string
  database: string
  user: string
  password: string
  payment_books_table: string
  accounts_table: string
  sync_enabled: boolean
}

type DbSyncConfig = DbSyncConfigForm & { has_password: boolean; configured: boolean }

type DbSyncTestResult = {
  ok: boolean
  message: string
  latency_ms: number
  server_version: string | null
  tables: { payment_books: boolean; accounts: boolean }
}

const EMPTY_DB_SYNC_FORM: DbSyncConfigForm = {
  host: '',
  port: '1433',
  database: '',
  user: '',
  password: '',
  payment_books_table: 'ac_PaymentBooks',
  accounts_table: 'ac_dict_Accounts',
  sync_enabled: false,
}

const DB_SYNC_INPUT_CLASS = 'w-full rounded-lg border border-border bg-card py-2 px-3 text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20'

function buildPayloadFromForm(form: DbSyncConfigForm) {
  const payload: Record<string, any> = {
    host: form.host.trim(),
    database: form.database.trim(),
    user: form.user.trim(),
    payment_books_table: form.payment_books_table.trim(),
    accounts_table: form.accounts_table.trim(),
    sync_enabled: form.sync_enabled,
  }
  // Send the port verbatim (as a string) when present so the backend parses +
  // range-checks it; omit when blank so the saved/default value is kept. Do NOT
  // coerce with Number()||1433 — that would silently turn a typo like '1443x'
  // into 1433 and test against the wrong port.
  const port = form.port.trim()
  if (port) payload.port = port
  // Only send password when the user typed one; backend keeps the saved value otherwise.
  if (form.password.trim()) payload.password = form.password
  return payload
}

function ConsumptionDbSyncCard() {
  const [form, setForm] = useState<DbSyncConfigForm>(EMPTY_DB_SYNC_FORM)
  const [hasPassword, setHasPassword] = useState(false)
  const [configured, setConfigured] = useState(false)
  const [status, setStatus] = useState<{ enabled: boolean; configured: boolean; state: any } | null>(null)
  const [loading, setLoading] = useState(true)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [testResult, setTestResult] = useState<DbSyncTestResult | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const [cfgRes, statusRes] = await Promise.all([
        consumptionApi.getDbSyncConfig(),
        consumptionApi.dbSyncStatus(),
      ])
      const cfg: DbSyncConfig = cfgRes.data.data
      setForm({
        host: cfg.host || '',
        port: String(cfg.port ?? 1433),
        database: cfg.database || '',
        user: cfg.user || '',
        password: '',
        payment_books_table: cfg.payment_books_table || 'ac_PaymentBooks',
        accounts_table: cfg.accounts_table || 'ac_dict_Accounts',
        sync_enabled: Boolean(cfg.sync_enabled),
      })
      setHasPassword(Boolean(cfg.has_password))
      setConfigured(Boolean(cfg.configured))
      setStatus(statusRes.data.data)
    } catch {
      toast.error('加载一卡通数据库配置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const update = <K extends keyof DbSyncConfigForm>(key: K, value: DbSyncConfigForm[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await consumptionApi.testDbSync(buildPayloadFromForm(form))
      setTestResult(res.data.data)
      if (res.data.data.ok) toast.success(res.data.data.message)
      else toast.error(res.data.data.message)
    } catch (err: any) {
      const msg = err?.response?.data?.error || '测试连接失败'
      toast.error(msg)
      setTestResult({ ok: false, message: msg, latency_ms: 0, server_version: null, tables: { payment_books: false, accounts: false } })
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await consumptionApi.updateDbSyncConfig(buildPayloadFromForm(form))
      toast.success('一卡通数据库配置已保存')
      // Reload config + status through the single shared path (parallel fetch,
      // surfaces errors instead of silently swallowing them).
      await load()
    } catch (err: any) {
      toast.error(err?.response?.data?.error || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleTrigger = async () => {
    setTriggering(true)
    try {
      await consumptionApi.dbSyncTrigger()
      toast.success('已提交一卡通数据库同步任务')
    } catch (err: any) {
      toast.error(err?.response?.data?.error || '触发同步失败')
    } finally {
      setTriggering(false)
    }
  }

  const state = status?.state
  const tableCheck = testResult?.tables

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h2 className="text-sm font-medium flex items-center gap-2">
            <Database className="w-4 h-4 text-muted-foreground" />一卡通消费数据库
          </h2>
          <p className="text-xs text-muted-foreground mt-1">
            配置消费记录同步的 SQL Server 连接，可先「测试连接」再保存。{configured ? '' : '（当前未配置完整连接信息）'}
          </p>
        </div>
        <button onClick={() => void load()} disabled={loading}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50">
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-muted-foreground sm:col-span-2">
          主机地址
          <input value={form.host} onChange={(e) => update('host', e.target.value)} placeholder="如 10.0.0.1" className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground">
          端口
          <input value={form.port} onChange={(e) => update('port', e.target.value)} inputMode="numeric" className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground">
          数据库名
          <input value={form.database} onChange={(e) => update('database', e.target.value)} placeholder="如 ZYTK40_PLUS" className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground">
          用户名
          <input value={form.user} onChange={(e) => update('user', e.target.value)} className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground">
          密码
          <input type="password" value={form.password} onChange={(e) => update('password', e.target.value)}
            placeholder={hasPassword ? '已配置，留空保持不变' : '请输入密码'} className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground sm:col-span-2">
          交易表名
          <input value={form.payment_books_table} onChange={(e) => update('payment_books_table', e.target.value)} className={DB_SYNC_INPUT_CLASS} />
        </label>
        <label className="text-xs text-muted-foreground sm:col-span-2">
          账户表名
          <input value={form.accounts_table} onChange={(e) => update('accounts_table', e.target.value)} className={DB_SYNC_INPUT_CLASS} />
        </label>
      </div>

      <label className="flex items-center gap-2 mt-3 text-sm cursor-pointer">
        <input type="checkbox" checked={form.sync_enabled} onChange={(e) => update('sync_enabled', e.target.checked)} className="accent-foreground" />
        启用定时同步
        <span className="text-xs text-muted-foreground">（勾选后由 Celery Beat 定时拉取；修改后需重启 worker 生效，也可点「立即同步」手动触发）</span>
      </label>

      <div className="flex flex-wrap items-center gap-2 mt-4">
        <button onClick={() => void handleTest()} disabled={testing || saving}
          className="flex items-center gap-2 border border-border bg-secondary text-sm px-4 py-2 rounded-lg hover:bg-secondary/70 transition-colors disabled:opacity-50">
          <Zap className={cn('w-3.5 h-3.5', testing && 'animate-pulse')} />
          {testing ? '测试中...' : '测试连接'}
        </button>
        <button onClick={() => void handleSave()} disabled={saving || testing}
          className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50">
          <Save className="w-3.5 h-3.5" />
          {saving ? '保存中...' : '保存配置'}
        </button>
        <button onClick={() => void handleTrigger()} disabled={triggering || !configured}
          className="flex items-center gap-2 border border-border text-sm px-4 py-2 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50"
          title={configured ? '' : '请先填写并保存连接配置'}>
          <RefreshCw className={cn('w-3.5 h-3.5', triggering && 'animate-spin')} />
          {triggering ? '提交中...' : '立即同步'}
        </button>
      </div>

      {testResult && (
        <div className={cn('mt-4 rounded-lg border p-3 text-xs', testResult.ok ? 'border-health-green/40 bg-health-green/5' : 'border-health-red/40 bg-health-red/5')}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className={cn('font-medium', testResult.ok ? 'text-health-green' : 'text-health-red')}>
              {testResult.ok ? '✓ 连接成功' : '✗ 连接失败'}
            </span>
            <span className="text-muted-foreground">耗时 {testResult.latency_ms} ms</span>
            {testResult.server_version && <span className="text-muted-foreground truncate max-w-full">版本：{testResult.server_version}</span>}
          </div>
          <div className="mt-1 text-muted-foreground">{testResult.message}</div>
          {testResult.ok && tableCheck && (
            <div className="mt-2 flex flex-wrap gap-3">
              <span className={cn('inline-flex items-center gap-1', tableCheck.payment_books ? 'text-health-green' : 'text-health-red')}>
                {tableCheck.payment_books ? '✓' : '✗'} 交易表
              </span>
              <span className={cn('inline-flex items-center gap-1', tableCheck.accounts ? 'text-health-green' : 'text-health-red')}>
                {tableCheck.accounts ? '✓' : '✗'} 账户表
              </span>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5 pt-4 border-t border-border">
        <div>
          <div className="text-xs text-muted-foreground">连接状态</div>
          <div className={cn('text-sm font-medium mt-0.5', configured ? 'text-health-green' : 'text-health-amber')}>
            {configured ? '已配置' : '未配置完整'}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">上次同步</div>
          <div className="text-sm font-mono mt-0.5">{fmtDateTime(state?.last_synced_at || undefined) || '从未'}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">成功 / 跳过</div>
          <div className="text-sm font-mono mt-0.5">
            <span className="text-health-green">{state?.last_success_count ?? 0}</span>
            <span className="text-muted-foreground"> / </span>
            <span className="text-health-amber">{state?.last_skipped_count ?? 0}</span>
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">失败</div>
          <div className={cn('text-sm font-mono mt-0.5', (state?.last_error_count ?? 0) > 0 ? 'text-health-red' : '')}>
            {state?.last_error_count ?? 0}
          </div>
        </div>
      </div>
      {state?.last_error && (
        <p className="mt-2 text-xs text-health-red truncate" title={String(state.last_error)}>最近错误：{String(state.last_error)}</p>
      )}
    </div>
  )
}

export function SyncAdminTab({
  syncStatus,
  syncing,
  onTriggerSync,
}: {
  syncStatus: { last_sync: string | null; active_users: number } | null
  syncing: boolean
  onTriggerSync: () => void | Promise<void>
}) {
  return (
    <div className="space-y-4">
      <div className="bg-card border border-border rounded-xl p-5">
        <h2 className="text-sm font-medium mb-4">钉钉组织同步</h2>
        <div className="flex flex-col sm:flex-row sm:items-center gap-4 sm:gap-6 mb-5">
          <div>
            <div className="text-xs text-muted-foreground">上次同步</div>
            <div className="text-sm font-mono mt-0.5">{fmtDateTime(syncStatus?.last_sync || undefined) || '从未'}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">活跃用户</div>
            <div className="text-sm font-mono mt-0.5">{syncStatus?.active_users ?? '—'}</div>
          </div>
        </div>
        <button onClick={onTriggerSync} disabled={syncing}
          className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50">
          <RefreshCw className={cn('w-3.5 h-3.5', syncing && 'animate-spin')} />
          {syncing ? '同步中...' : '立即同步'}
        </button>
        <p className="mt-3 text-xs text-muted-foreground">系统每日凌晨 02:00 自动全量同步。</p>
      </div>

      <ConsumptionDbSyncCard />
    </div>
  )
}

export function TasksAdminTab({
  tasks,
  loading,
  onRefresh,
}: {
  tasks: TaskLog[]
  loading: boolean
  onRefresh: () => void | Promise<void>
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-muted-foreground">最近 {tasks.length} 条任务记录</span>
        <button onClick={onRefresh} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
        </button>
      </div>
      <div className="bg-card border border-border rounded-xl overflow-x-auto">
        <table className="data-table min-w-[960px]">
          <thead><tr><th>任务类型</th><th>日期</th><th>状态</th><th>总数</th><th>成功</th><th>低置信</th><th>失败</th><th>开始时间</th><th>结束时间</th><th>耗时</th></tr></thead>
          <tbody>
            {loading && <tr><td colSpan={10} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
            {!loading && tasks.length === 0 && <tr><td colSpan={10} className="text-center py-12 text-muted-foreground">暂无任务记录</td></tr>}
            {!loading && tasks.map((task) => (
              <tr key={task.id}>
                <td>
                  <div className="font-mono text-xs">{TASK_TYPE_LABEL[task.task_type] || task.task_type}</div>
                  {task.meta?.status_text && (
                    <div className="mt-1 text-[11px] text-muted-foreground max-w-[240px] truncate">{String(task.meta.status_text)}</div>
                  )}
                </td>
                <td><span className="font-mono text-xs">{task.task_date || '—'}</span></td>
                <td><span className={cn('text-xs font-medium', STATUS_STYLE[task.status] || 'text-muted-foreground')}>{STATUS_LABEL[task.status] || task.status}</span></td>
                <td><span className="font-mono">{task.total_count}</span></td>
                <td><span className="font-mono text-health-green">{task.success_count}</span></td>
                <td><span className="font-mono text-health-amber">{task.low_confidence_count}</span></td>
                <td><span className="font-mono text-health-red">{task.error_count}</span></td>
                <td><span className="font-mono text-xs text-muted-foreground">{fmtDateTime(task.started_at)}</span></td>
                <td><span className="font-mono text-xs text-muted-foreground">{fmtDateTime(task.finished_at)}</span></td>
                <td><span className="font-mono text-xs text-muted-foreground">{formatTaskDuration(task)}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
