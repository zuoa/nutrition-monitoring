import { RefreshCw, Trash2, Upload } from 'lucide-react'
import type { DropzoneInputProps, DropzoneRootProps } from 'react-dropzone'

import {
  ROLE_LABELS,
  STATUS_LABEL,
  STATUS_STYLE,
  TASK_TYPE_LABEL,
  formatTaskDuration,
} from '@/components/admin/adminPageShared'
import { cn, fmtDateTime } from '@/lib/utils'
import type { TaskLog, User } from '@/types'

export function UsersAdminTab({
  users,
  usersTotal,
  loading,
  deletingUserId,
  onRefresh,
  onUpdateUserRole,
  onDeleteUser,
  getRootProps,
  getInputProps,
}: {
  users: User[]
  usersTotal: number
  loading: boolean
  deletingUserId: number | null
  onRefresh: () => void | Promise<void>
  onUpdateUserRole: (user: User, role: string) => void | Promise<void>
  onDeleteUser: (user: User) => void | Promise<void>
  getRootProps: <T extends DropzoneRootProps>(props?: T) => T
  getInputProps: <T extends DropzoneInputProps>(props?: T) => T
}) {
  return (
    <div className="space-y-4">
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
            {users.map((user) => (
              <tr key={user.id} className={!user.is_active ? 'opacity-40' : ''}>
                <td>
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-foreground/10 flex items-center justify-center text-xs">{user.name[0]}</div>
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
