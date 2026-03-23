import { useEffect, useState } from 'react'
import { Users, Settings, RefreshCw, Upload } from 'lucide-react'
import { adminApi, syncApi } from '@/api/client'
import { fmtDateTime, cn } from '@/lib/utils'
import type { User } from '@/types'
import toast from 'react-hot-toast'
import { useDropzone } from 'react-dropzone'

const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员', teacher: '班主任', grade_leader: '年级组长',
  parent: '家长', canteen_manager: '食堂管理员',
}

export default function AdminPage() {
  const [tab, setTab] = useState<'users' | 'config' | 'sync'>('users')
  const [users, setUsers] = useState<User[]>([])
  const [usersTotal, setUsersTotal] = useState(0)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; active_users: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [editUser, setEditUser] = useState<User | null>(null)

  const loadUsers = async () => {
    setLoading(true)
    try {
      const res = await adminApi.users({ page_size: 50 })
      setUsers(res.data.data.items)
      setUsersTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  const loadConfig = async () => {
    const res = await adminApi.config()
    setConfig(res.data.data)
  }

  const loadSyncStatus = async () => {
    const res = await syncApi.status()
    setSyncStatus(res.data.data)
  }

  useEffect(() => {
    if (tab === 'users') loadUsers()
    else if (tab === 'config') loadConfig()
    else if (tab === 'sync') loadSyncStatus()
  }, [tab])

  const triggerSync = async () => {
    setSyncing(true)
    try {
      await syncApi.trigger()
      toast.success('钉钉组织同步任务已提交')
      loadSyncStatus()
    } finally { setSyncing(false) }
  }

  const updateUserRole = async (user: User, role: string) => {
    await adminApi.updateUser(user.id, { role })
    toast.success('角色已更新')
    loadUsers()
  }

  const { getRootProps, getInputProps } = useDropzone({
    onDrop: async (files) => {
      if (!files.length) return
      try {
        const res = await syncApi.importStudents(files[0])
        toast.success(`导入完成：新增 ${res.data.data.imported}，更新 ${res.data.data.updated}`)
      } catch {
        toast.error('导入失败')
      }
    },
    accept: { 'text/csv': ['.csv'], 'application/vnd.ms-excel': ['.xls'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    maxFiles: 1,
  })

  return (
    <div className="p-4 sm:p-6 max-w-5xl">
      <div className="mb-6">
        <h1 className="text-2xl font-display">系统管理</h1>
        <p className="text-sm text-muted-foreground mt-0.5">用户管理 · 系统配置 · 数据同步</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-full sm:w-fit overflow-x-auto mb-5">
        {(['users', 'config', 'sync'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 text-sm rounded-md transition-colors', tab === t ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground')}>
            {t === 'users' ? '用户管理' : t === 'config' ? '系统配置' : '数据同步'}
          </button>
        ))}
      </div>

      {tab === 'users' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">共 {usersTotal} 个用户</span>
            <button onClick={loadUsers} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
              <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
            </button>
          </div>
          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <table className="data-table min-w-[640px]">
              <thead><tr><th>姓名</th><th>角色</th><th>部门</th><th>状态</th><th>同步时间</th><th>修改角色</th></tr></thead>
              <tbody>
                {loading && <tr><td colSpan={6} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
                {users.map(u => (
                  <tr key={u.id} className={!u.is_active ? 'opacity-40' : ''}>
                    <td>
                      <div className="flex items-center gap-2">
                        <div className="w-6 h-6 rounded-full bg-foreground/10 flex items-center justify-center text-xs">{u.name[0]}</div>
                        <span className="text-sm font-medium">{u.name}</span>
                      </div>
                    </td>
                    <td><span className="text-xs">{ROLE_LABELS[u.role] || u.role}</span></td>
                    <td><span className="text-xs text-muted-foreground">{u.dept_name || '—'}</span></td>
                    <td>
                      <span className={cn('text-xs', u.is_active ? 'text-health-green' : 'text-muted-foreground')}>
                        {u.is_active ? '正常' : '已停用'}
                      </span>
                    </td>
                    <td><span className="text-xs font-mono text-muted-foreground">{fmtDateTime(u.sync_at)}</span></td>
                    <td>
                      <select
                        value={u.role}
                        onChange={e => updateUserRole(u, e.target.value)}
                        className="text-xs bg-background border border-border rounded px-2 py-1 focus:outline-none"
                      >
                        {Object.entries(ROLE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Student import */}
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
      )}

      {tab === 'config' && (
        <div className="bg-card border border-border rounded-xl p-5">
          <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
            <Settings className="w-4 h-4 text-muted-foreground" />当前系统配置
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {Object.entries(config).map(([key, value]) => (
              <div key={key} className="flex items-center justify-between p-3 bg-secondary rounded-lg">
                <span className="text-xs text-muted-foreground font-mono">{key}</span>
                <span className="text-xs font-mono text-foreground">{JSON.stringify(value)}</span>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs text-muted-foreground">如需修改配置，请编辑服务器环境变量后重启服务。</p>
        </div>
      )}

      {tab === 'sync' && (
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
            <button onClick={triggerSync} disabled={syncing}
              className="flex items-center gap-2 bg-foreground text-background text-sm px-4 py-2 rounded-lg hover:bg-foreground/90 transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-3.5 h-3.5', syncing && 'animate-spin')} />
              {syncing ? '同步中...' : '立即同步'}
            </button>
            <p className="mt-3 text-xs text-muted-foreground">系统每日凌晨 02:00 自动全量同步。</p>
          </div>
        </div>
      )}
    </div>
  )
}
