import { useEffect, useState } from 'react'
import { Users, Settings, RefreshCw, Upload } from 'lucide-react'
import { adminApi, analysisApi, syncApi } from '@/api/client'
import { fmtDateTime, cn } from '@/lib/utils'
import type { TaskLog, User } from '@/types'
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
  const [modelDownloadTasks, setModelDownloadTasks] = useState<TaskLog[]>([])
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; active_users: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [downloadingModelType, setDownloadingModelType] = useState<'embedding' | 'reranker' | null>(null)
  const [activatingModelType, setActivatingModelType] = useState<'embedding' | 'reranker' | null>(null)
  const [embeddingVariant, setEmbeddingVariant] = useState<'2B' | '8B'>('2B')
  const [rerankerVariant, setRerankerVariant] = useState<'2B' | '8B'>('2B')
  const [editUser, setEditUser] = useState<User | null>(null)

  const loadUsers = async () => {
    setLoading(true)
    try {
      const res = await adminApi.users({ page_size: 50 })
      setUsers(res.data.data.items)
      setUsersTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  const loadConfig = async (options?: { syncSelectedVariants?: boolean }) => {
    const res = await adminApi.config()
    setConfig(res.data.data)
    if (options?.syncSelectedVariants) {
      setEmbeddingVariant((res.data.data.local_qwen3_vl_embedding_active_variant || '2B') as '2B' | '8B')
      setRerankerVariant((res.data.data.local_qwen3_vl_reranker_active_variant || '2B') as '2B' | '8B')
    }
  }

  const loadModelDownloadTasks = async () => {
    const res = await analysisApi.tasks({ task_type: 'local_model_download', page_size: 20 })
    setModelDownloadTasks(res.data.data.items || [])
  }

  const loadSyncStatus = async () => {
    const res = await syncApi.status()
    setSyncStatus(res.data.data)
  }

  useEffect(() => {
    if (tab === 'users') loadUsers()
    else if (tab === 'config') {
      loadConfig({ syncSelectedVariants: true })
      loadModelDownloadTasks()
    }
    else if (tab === 'sync') loadSyncStatus()
  }, [tab])

  useEffect(() => {
    if (tab !== 'config') return undefined
    const timer = window.setInterval(() => {
      loadConfig()
      loadModelDownloadTasks()
    }, 3000)
    return () => window.clearInterval(timer)
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

  const handleDownloadLocalModel = async (modelType: 'embedding' | 'reranker') => {
    const variant = modelType === 'embedding' ? embeddingVariant : rerankerVariant
    setDownloadingModelType(modelType)
    try {
      const res = await adminApi.downloadLocalModel(modelType, variant)
      toast.success(res.data.data.message || '模型下载任务已提交')
      await loadConfig()
      await loadModelDownloadTasks()
    } finally {
      setDownloadingModelType(null)
    }
  }

  const handleActivateLocalModel = async (modelType: 'embedding' | 'reranker') => {
    const variant = modelType === 'embedding' ? embeddingVariant : rerankerVariant
    setActivatingModelType(modelType)
    try {
      const res = await adminApi.activateLocalModel(modelType, variant)
      toast.success(res.data.data.message || '当前模型已切换')
      await loadConfig({ syncSelectedVariants: true })
      await loadModelDownloadTasks()
    } finally {
      setActivatingModelType(null)
    }
  }

  const getLatestModelTask = (modelType: 'embedding' | 'reranker') =>
    modelDownloadTasks.find((task) => task.meta?.model_type === modelType) || null

  const formatBytes = (value?: number) => {
    if (!value || value <= 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let size = value
    let index = 0
    while (size >= 1024 && index < units.length - 1) {
      size /= 1024
      index += 1
    }
    return index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(1)} ${units[index]}`
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
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">系统管理</h1>
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
        <div className="space-y-4">
          <div className="bg-card border border-border rounded-xl p-5">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h2 className="text-sm font-medium flex items-center gap-2">
                  <Settings className="w-4 h-4 text-muted-foreground" />本地识别模型
                </h2>
                <p className="text-xs text-muted-foreground mt-1">
                  默认识别模式已切换为 <span className="font-mono">{String(config.dish_recognition_mode || 'yolo_embedding_local')}</span>。
                  可直接从 Hugging Face 下载 embedding 与 reranker 的 2B / 8B 版本到本地目录。
                </p>
                <p className="text-[11px] text-muted-foreground mt-1">
                  选中某个规格后，可先下载，再点击“设为当前”切换实际生效模型。
                  当前下载源：<span className="font-mono">{String(config.hf_endpoint || 'https://huggingface.co')}</span>
                </p>
              </div>
              <button onClick={() => loadConfig()} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
                <RefreshCw className="w-3.5 h-3.5" />刷新配置
              </button>
            </div>

            <div className="grid gap-3 lg:grid-cols-2">
              {[
                {
                  type: 'embedding' as const,
                  title: 'Embedding 模型',
                  repoId: String(config.local_qwen3_vl_embedding_repo_id || ''),
                  path: String(config.local_qwen3_vl_embedding_model_path || ''),
                  downloaded: Boolean(config.local_qwen3_vl_embedding_model_downloaded),
                  activeVariant: String(config.local_qwen3_vl_embedding_active_variant || '2B'),
                  selectedVariant: embeddingVariant,
                  task: getLatestModelTask('embedding'),
                  onVariantChange: setEmbeddingVariant,
                },
                {
                  type: 'reranker' as const,
                  title: 'Reranker 模型',
                  repoId: String(config.local_qwen3_vl_reranker_repo_id || ''),
                  path: String(config.local_qwen3_vl_reranker_model_path || ''),
                  downloaded: Boolean(config.local_qwen3_vl_reranker_model_downloaded),
                  activeVariant: String(config.local_qwen3_vl_reranker_active_variant || '2B'),
                  selectedVariant: rerankerVariant,
                  task: getLatestModelTask('reranker'),
                  onVariantChange: setRerankerVariant,
                },
              ].map((item) => {
                const task = item.task
                const isRunning = task?.status === 'running'
                const progress = Math.max(0, Math.min(Number(task?.meta?.progress_percent || 0), 100))
                const downloadedBytes = Number(task?.meta?.downloaded_bytes || 0)
                const totalBytes = Number(task?.meta?.total_bytes || 0)
                const downloadedFiles = Number(task?.meta?.downloaded_files || task?.success_count || 0)
                const totalFiles = Number(task?.meta?.total_files || task?.total_count || 0)
                const taskVariant = String(task?.meta?.variant || item.selectedVariant)

                return (
                <div key={item.type} className="rounded-xl border border-border bg-secondary/60 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-medium">{item.title}</h3>
                      <p className={cn(
                        'mt-1 text-xs font-medium',
                        item.downloaded ? 'text-health-green' : 'text-health-amber',
                      )}>
                        {item.downloaded ? '已检测到本地模型' : '本地模型未就绪'}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        当前默认启用规格: <span className="font-mono">{item.activeVariant}</span>
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <select
                        value={item.selectedVariant}
                        onChange={(event) => item.onVariantChange(event.target.value as '2B' | '8B')}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isRunning}
                        className="px-2 py-1.5 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                      >
                        {(config.local_model_variants || ['2B', '8B']).map((variant: string) => (
                          <option key={variant} value={variant}>{variant}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => handleDownloadLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isRunning}
                        className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
                      >
                        {downloadingModelType === item.type ? '提交中...' : isRunning ? '下载中...' : `下载 ${item.selectedVariant}`}
                      </button>
                      <button
                        onClick={() => handleActivateLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isRunning || item.selectedVariant === item.activeVariant}
                        className="px-3 py-1.5 text-xs bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50"
                      >
                        {activatingModelType === item.type ? '切换中...' : item.selectedVariant === item.activeVariant ? '当前生效中' : '设为当前'}
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 space-y-2">
                    <div>
                      <div className="text-[11px] text-muted-foreground font-mono">repo</div>
                      <div className="text-xs font-mono break-all">
                        {item.selectedVariant === item.activeVariant ? item.repoId || '—' : `Qwen/Qwen3-VL-${item.type === 'embedding' ? 'Embedding' : 'Reranker'}-${item.selectedVariant}`}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-muted-foreground font-mono">path</div>
                      <div className="text-xs font-mono break-all">
                        {item.selectedVariant === item.activeVariant
                          ? item.path || '—'
                          : `${String(config.local_model_storage_path || '/data/models')}/qwen3-vl-${item.type}-${item.selectedVariant.toLowerCase()}`}
                      </div>
                    </div>
                  </div>
                  {task && (
                    <div className="mt-4 rounded-lg border border-border bg-background/70 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-xs font-medium">
                            最近任务
                            <span className="ml-2 font-mono text-[11px] text-muted-foreground">{taskVariant}</span>
                          </div>
                          <div className="mt-1 text-[11px] text-muted-foreground">
                            {String(task.meta?.status_text || (task.status === 'running' ? '模型下载中' : task.status === 'success' ? '模型下载完成' : '模型下载失败'))}
                          </div>
                        </div>
                        <div className={cn(
                          'text-xs font-medium',
                          task.status === 'success' && 'text-health-green',
                          task.status === 'failed' && 'text-health-red',
                          task.status === 'running' && 'text-health-blue',
                        )}>
                          {task.status === 'running' ? `${progress.toFixed(1)}%` : task.status === 'success' ? '已完成' : '失败'}
                        </div>
                      </div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full bg-secondary">
                        <div
                          className={cn(
                            'h-full rounded-full transition-all',
                            task.status === 'failed' ? 'bg-health-red' : task.status === 'success' ? 'bg-health-green' : 'bg-health-blue',
                          )}
                          style={{ width: `${task.status === 'failed' ? Math.max(progress, 6) : progress}%` }}
                        />
                      </div>
                      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                        <span>开始: {fmtDateTime(task.started_at)}</span>
                        <span>结束: {fmtDateTime(task.finished_at)}</span>
                        <span>文件: {downloadedFiles}/{totalFiles}</span>
                        <span>体积: {formatBytes(downloadedBytes)} / {formatBytes(totalBytes)}</span>
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground font-mono break-all">
                        源: {String(task.meta?.hf_endpoint || config.hf_endpoint || 'https://huggingface.co')}
                      </div>
                      {task.error_message && (
                        <div className="mt-2 text-[11px] text-health-red break-words">
                          {task.error_message}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )})}
            </div>

            <p className="mt-4 text-xs text-muted-foreground">
              点击按钮后会提交后台下载任务，模型文件会写入 <span className="font-mono">{String(config.local_model_storage_path || '/data/models')}</span>。
              “设为当前”会写入 <span className="font-mono">{String(config.local_runtime_config_path || 'runtime_config.json')}</span>，后续识别服务会按该配置读取模型。
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              如果内网下载慢，可在部署环境里设置 <span className="font-mono">HF_ENDPOINT=https://hf-mirror.com</span> 后重启 `flask-api` 和 `celery-worker`。
            </p>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
              <Settings className="w-4 h-4 text-muted-foreground" />当前系统配置
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {Object.entries(config).map(([key, value]) => (
                <div key={key} className="p-3 bg-secondary rounded-lg">
                  <div className="text-xs text-muted-foreground font-mono mb-2 break-all">{key}</div>
                  <pre className="text-xs font-mono text-foreground whitespace-pre-wrap break-words leading-5">
                    {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-muted-foreground">如需修改配置，请编辑服务器环境变量后重启服务。</p>
          </div>
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
              className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50">
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
