import { useMemo, useState } from 'react'
import { Building2, ChevronDown, ChevronRight, RefreshCw, Trash2, Upload, Users } from 'lucide-react'
import type { DropzoneInputProps, DropzoneRootProps } from 'react-dropzone'

import {
  ROLE_LABELS,
  STATUS_LABEL,
  STATUS_STYLE,
  TASK_TYPE_LABEL,
  formatTaskDuration,
} from '@/components/admin/adminPageShared'
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
