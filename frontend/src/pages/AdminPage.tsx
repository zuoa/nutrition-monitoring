import { useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import { Bot, Braces, FileJson, ImageUp, RefreshCw, SendHorizontal, Settings, Upload, X } from 'lucide-react'
import { adminApi, analysisApi, menuApi, syncApi } from '@/api/client'
import type { ManagedModelType } from '@/api/client'
import { fmtDateTime, cn, isLocalRecognitionMode } from '@/lib/utils'
import type { Dish, TaskLog, User } from '@/types'
import toast from 'react-hot-toast'
import { useDropzone } from 'react-dropzone'

const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员', teacher: '班主任', grade_leader: '年级组长',
  parent: '家长', canteen_manager: '食堂管理员',
}

const STATUS_STYLE: Record<string, string> = {
  running: 'text-health-blue',
  success: 'text-health-green',
  failed: 'text-health-red',
  partial: 'text-health-amber',
  pending: 'text-muted-foreground',
}

const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  success: '完成',
  failed: '失败',
  partial: '部分成功',
  pending: '待处理',
}

const TASK_TYPE_LABEL: Record<string, string> = {
  nvr_download: 'NVR 下载',
  ai_recognition: 'AI 识别',
  manual_upload: '手动上传',
  region_proposal: '菜区提议',
  local_model_download: '模型下载',
  dish_embedding: '样图 embedding',
  report_gen: '报告生成',
}

const DEFAULT_VL_USER_PROMPT = '请详细描述这张图片中的内容。如果适合结构化输出，请同时给出要点列表或 JSON。'
const DEFAULT_VL_SYSTEM_PROMPT = `你是一个学校食堂菜品识别助手，任务是尽可能完整地识别餐盘里的所有独立菜品。

识别原则：
1. 先按餐盘分区逐个扫描，再汇总，不要只返回最显眼的 1 到 2 个菜。
2. 目标是“宁可给出低置信候选，也不要漏掉明显可见的菜品”。
3. 只识别候选列表中的菜品；如果不在候选列表中，不要臆造新菜名。
4. 同一菜品不要重复输出；同一区域若明显是混合菜，只输出最贴近的一个候选。
5. 米饭、主菜、配菜、青菜、汤类等如果是独立取餐区域，应该分别判断。
6. 调味汁、少量点缀、不可独立成菜的碎料不要单独算一道菜。
7. 同一位置不要重复输出多个候选；如果两个结果明显覆盖同一菜区，只保留更可信的一项。

如果画面存在遮挡、反光、堆叠、模糊，请在 notes 里说明，但仍要尽量给出候选。
只返回 JSON 格式，不要输出其他内容。`
const DEFAULT_VL_USER_PROMPT_TEMPLATE = `候选菜品列表：
{dish_list_with_desc}

请按下面流程识别：
1. 先判断餐盘里大约有几个独立取餐区域或独立菜品。
2. 逐个区域与候选菜品列表比对，给出最可能的菜名。
3. 对清晰可见但不够确定的菜，也可以保留较低 confidence，而不是直接漏掉。
4. 输出时按你看到的区域顺序排列。
5. 每个结果尽量补充 position 和 bbox；bbox 使用整张图相对百分比，范围 0 到 100。
6. 同一位置不要重复输出多个候选；如果两个结果明显覆盖同一菜区，只保留更可信的一项。

confidence 取值建议：
- 0.85~0.98：画面清晰且高度确定
- 0.65~0.84：大概率匹配
- 0.40~0.64：存在遮挡或相似菜，但仍值得保留为候选

返回格式：
{
  "dishes": [
    {
      "name": "菜品名",
      "confidence": 0.95,
      "position": "左上/中间/右下等",
      "bbox": {"x1": 5, "y1": 8, "x2": 45, "y2": 42}
    }
  ],
  "notes": "可选备注，说明遮挡、相似菜、低置信原因"
}`

type ImportedMenuInfo = {
  date: string
  count: number
  isDefault: boolean
}

type VariantModelType = 'embedding' | 'reranker'
type AdminTab = 'users' | 'config' | 'vl' | 'sync' | 'tasks'
type VlTestResult = {
  filename: string
  content_type: string
  prompt: string
  system_prompt: string
  model: string
  temperature: number | null
  request_format: string
  content: string
  parsed_json: Record<string, any> | null
  json_parse_error: string
  raw_response: Record<string, any> | null
}
type VlDebugBox = {
  name: string
  confidence?: number
  position: string
  bbox: { x1: number; y1: number; x2: number; y2: number }
}
const VARIANT_MODEL_TYPES: VariantModelType[] = ['embedding', 'reranker']
const hasVariants = (modelType: ManagedModelType): modelType is VariantModelType =>
  VARIANT_MODEL_TYPES.includes(modelType as VariantModelType)

const formatDateForApi = (date: Date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const formatCandidateDishList = (dishes: Pick<Dish, 'name' | 'description'>[]) => {
  if (!dishes.length) return '所有菜品'
  return dishes.map((dish) => {
    const description = String(dish.description || '').trim()
    return description ? `- ${dish.name}（${description}）` : `- ${dish.name}`
  }).join('\n')
}

const injectDishListIntoPrompt = (prompt: string, dishes: Pick<Dish, 'name' | 'description'>[]) => {
  const normalizedPrompt = (prompt || '').trim()
  const dishList = formatCandidateDishList(dishes)

  if (!normalizedPrompt) return `候选菜品列表：\n${dishList}`
  if (normalizedPrompt.includes('{dish_list_with_desc}')) {
    return normalizedPrompt.replace('{dish_list_with_desc}', dishList)
  }

  const sectionPattern = /(候选菜品列表：\s*\n)([\s\S]*?)(\n\s*请按下面流程识别：)/
  if (sectionPattern.test(normalizedPrompt)) {
    return normalizedPrompt.replace(sectionPattern, `$1${dishList}$3`)
  }

  if (normalizedPrompt.includes('候选菜品列表：')) {
    return `${normalizedPrompt}\n${dishList}`
  }

  return `${normalizedPrompt}\n\n候选菜品列表：\n${dishList}`
}

const normalizeVlDebugBoxes = (parsedJson: Record<string, any> | null): VlDebugBox[] => {
  const items = Array.isArray(parsedJson?.dishes) ? parsedJson.dishes : []
  return items.flatMap((item: any) => {
    const bbox = item?.bbox
    if (!bbox || typeof bbox !== 'object') return []

    const x1 = Number(bbox.x1)
    const y1 = Number(bbox.y1)
    const x2 = Number(bbox.x2)
    const y2 = Number(bbox.y2)
    if (![x1, y1, x2, y2].every(Number.isFinite)) return []
    if (x2 <= x1 || y2 <= y1) return []

    const confidence = Number(item?.confidence)
    return [{
      name: String(item?.name || '').trim() || '未命名',
      confidence: Number.isFinite(confidence) ? confidence : undefined,
      position: String(item?.position || '').trim(),
      bbox: { x1, y1, x2, y2 },
    }]
  })
}

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('users')
  const [users, setUsers] = useState<User[]>([])
  const [usersTotal, setUsersTotal] = useState(0)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [modelDownloadTasks, setModelDownloadTasks] = useState<TaskLog[]>([])
  const [allTasks, setAllTasks] = useState<TaskLog[]>([])
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; active_users: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [downloadingModelType, setDownloadingModelType] = useState<ManagedModelType | null>(null)
  const [activatingModelType, setActivatingModelType] = useState<ManagedModelType | null>(null)
  const [embeddingVariant, setEmbeddingVariant] = useState<'2B' | '8B'>('2B')
  const [rerankerVariant, setRerankerVariant] = useState<'2B' | '8B'>('2B')
  const [editUser, setEditUser] = useState<User | null>(null)
  const [vlImageFile, setVlImageFile] = useState<File | null>(null)
  const [vlImagePreviewUrl, setVlImagePreviewUrl] = useState('')
  const [vlUserPrompt, setVlUserPrompt] = useState(DEFAULT_VL_USER_PROMPT)
  const [vlSystemPrompt, setVlSystemPrompt] = useState('')
  const [vlTemperature, setVlTemperature] = useState('0.1')
  const [vlLoading, setVlLoading] = useState(false)
  const [vlDefaultsLoading, setVlDefaultsLoading] = useState(false)
  const [vlResult, setVlResult] = useState<VlTestResult | null>(null)
  const [vlImportedMenuInfo, setVlImportedMenuInfo] = useState<ImportedMenuInfo | null>(null)
  const localRecognitionModeEnabled = isLocalRecognitionMode(String(config.dish_recognition_mode || ''))
  const vlDebugBoxes = normalizeVlDebugBoxes(vlResult?.parsed_json ?? null)

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

  const loadAllTasks = async () => {
    setTasksLoading(true)
    try {
      const res = await analysisApi.tasks({ page_size: 50 })
      setAllTasks(res.data.data.items || [])
    } finally {
      setTasksLoading(false)
    }
  }

  const applyVlPromptDefaults = (
    nextConfig: Record<string, any>,
    menuData?: { dishes?: Dish[]; is_default?: boolean } | null,
  ) => {
    const nextTemperature = nextConfig.qwen_temperature
    const dishes = Array.isArray(menuData?.dishes) ? menuData?.dishes : []

    setVlSystemPrompt(DEFAULT_VL_SYSTEM_PROMPT)
    setVlUserPrompt(
      dishes.length
        ? injectDishListIntoPrompt(DEFAULT_VL_USER_PROMPT_TEMPLATE, dishes)
        : DEFAULT_VL_USER_PROMPT_TEMPLATE,
    )
    setVlTemperature(
      nextTemperature === undefined || nextTemperature === null || String(nextTemperature).trim() === ''
        ? '0.1'
        : String(nextTemperature),
    )
    setVlResult(null)
    if (menuData) {
      setVlImportedMenuInfo({
        date: formatDateForApi(new Date()),
        count: dishes.length,
        isDefault: Boolean(menuData.is_default),
      })
    } else {
      setVlImportedMenuInfo(null)
    }
  }

  const loadVlDefaults = async () => {
    setVlDefaultsLoading(true)
    const today = formatDateForApi(new Date())
    try {
      const [configRes, menuRes] = await Promise.allSettled([
        adminApi.config(),
        menuApi.get(today),
      ])

      if (configRes.status !== 'fulfilled') throw configRes.reason

      const nextConfig = configRes.value.data.data || {}
      setConfig(nextConfig)

      if (menuRes.status === 'fulfilled') {
        applyVlPromptDefaults(nextConfig, menuRes.value.data.data || null)
      } else {
        applyVlPromptDefaults(nextConfig)
        toast.error('今日菜单导入失败，已仅加载识别提示词')
      }
    } finally {
      setVlDefaultsLoading(false)
    }
  }

  useEffect(() => {
    if (tab === 'users') loadUsers()
    else if (tab === 'config') {
      loadConfig({ syncSelectedVariants: true })
      loadModelDownloadTasks()
    }
    else if (tab === 'vl') loadVlDefaults()
    else if (tab === 'sync') loadSyncStatus()
    else if (tab === 'tasks') loadAllTasks()
  }, [tab])

  useEffect(() => {
    if (!vlImageFile) {
      setVlImagePreviewUrl('')
      return undefined
    }
    const nextUrl = URL.createObjectURL(vlImageFile)
    setVlImagePreviewUrl(nextUrl)
    return () => URL.revokeObjectURL(nextUrl)
  }, [vlImageFile])

  useEffect(() => {
    if (tab !== 'config') return undefined
    const timer = window.setInterval(() => {
      loadConfig()
      loadModelDownloadTasks()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [tab])

  useEffect(() => {
    if (tab !== 'tasks') return undefined
    const timer = window.setInterval(() => {
      loadAllTasks()
    }, 5000)
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

  const handleDownloadLocalModel = async (modelType: ManagedModelType) => {
    const variant = modelType === 'embedding' ? embeddingVariant : modelType === 'reranker' ? rerankerVariant : undefined
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

  const handleActivateLocalModel = async (modelType: ManagedModelType) => {
    const variant = modelType === 'embedding' ? embeddingVariant : modelType === 'reranker' ? rerankerVariant : undefined
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

  const getLatestModelTask = (modelType: ManagedModelType) =>
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

  const formatTaskDuration = (task: TaskLog) => {
    if (task.started_at && task.finished_at) {
      const seconds = Math.round((new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000)
      return `${seconds}s`
    }
    return task.status === 'running' ? '运行中' : '—'
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

  const {
    getRootProps: getVlRootProps,
    getInputProps: getVlInputProps,
    isDragActive: isVlDragActive,
  } = useDropzone({
    onDrop: (files) => {
      if (!files.length) return
      setVlImageFile(files[0])
      setVlResult(null)
    },
    onDropRejected: () => {
      toast.error('请上传 JPG、PNG、WEBP 或 BMP 图片')
    },
    accept: {
      'image/jpeg': ['.jpg', '.jpeg'],
      'image/png': ['.png'],
      'image/webp': ['.webp'],
      'image/bmp': ['.bmp'],
    },
    maxFiles: 1,
    multiple: false,
  })

  const handleVlSubmit = async () => {
    if (!vlImageFile) {
      toast.error('请先上传测试图片')
      return
    }
    if (!vlUserPrompt.trim()) {
      toast.error('请输入提示词')
      return
    }
    const normalizedTemperature = vlTemperature.trim()
    let parsedTemperature: number | undefined
    if (normalizedTemperature) {
      parsedTemperature = Number(normalizedTemperature)
      if (!Number.isFinite(parsedTemperature) || parsedTemperature < 0 || parsedTemperature > 1) {
        toast.error('temperature 需在 0 到 1 之间')
        return
      }
    }
    setVlLoading(true)
    try {
      const res = await adminApi.vlTest(vlImageFile, {
        userPrompt: vlUserPrompt.trim(),
        systemPrompt: vlSystemPrompt.trim(),
        temperature: parsedTemperature,
      })
      setVlResult(res.data.data)
      toast.success('VL 调试完成')
    } finally {
      setVlLoading(false)
    }
  }

  const clearVlImage = () => {
    setVlImageFile(null)
    setVlResult(null)
  }

  const handleImportTodayMenu = async () => {
    setVlDefaultsLoading(true)
    const today = formatDateForApi(new Date())
    try {
      const res = await menuApi.get(today)
      const menu = res.data.data || {}
      const dishes = Array.isArray(menu.dishes) ? menu.dishes : []
      setVlUserPrompt((currentPrompt) => injectDishListIntoPrompt(
        currentPrompt || DEFAULT_VL_USER_PROMPT_TEMPLATE,
        dishes,
      ))
      setVlImportedMenuInfo({
        date: today,
        count: dishes.length,
        isDefault: Boolean(menu.is_default),
      })
      setVlResult(null)
      toast.success(`${today} 菜单已导入测试提示词`)
    } finally {
      setVlDefaultsLoading(false)
    }
  }

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">系统管理</h1>
        <p className="text-sm text-muted-foreground mt-0.5">用户管理 · 系统配置 · VL 测试 · 数据同步 · 任务总览</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-full sm:w-fit overflow-x-auto mb-5">
        {(['users', 'config', 'vl', 'sync', 'tasks'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 text-sm rounded-md transition-colors', tab === t ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground')}>
            {t === 'users' ? '用户管理' : t === 'config' ? '系统配置' : t === 'vl' ? 'VL 测试' : t === 'sync' ? '数据同步' : '全部任务'}
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
                  当前识别模式：
                  {' '}
                  <span className="font-mono">{String(config.dish_recognition_mode || 'local_embedding')}</span>
                  。
                  {localRecognitionModeEnabled
                    ? ' 可直接从 Hugging Face 下载 embedding 与 reranker 模型到本地目录。'
                    : ' 当前为 VL 模式，本地 embedding / reranker 相关功能已隐藏。'}
                </p>
                {localRecognitionModeEnabled && (
                  <p className="text-[11px] text-muted-foreground mt-1">
                    规格型模型可先选 2B / 8B 再下载并切换当前版本。
                    当前下载源：<span className="font-mono">{String(config.hf_endpoint || 'https://huggingface.co')}</span>
                  </p>
                )}
              </div>
              <button onClick={() => loadConfig()} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
                <RefreshCw className="w-3.5 h-3.5" />刷新配置
              </button>
            </div>

            {localRecognitionModeEnabled ? (
            <>
            <div className="grid gap-3 lg:grid-cols-2">
              {([
                {
                  type: 'embedding' as const,
                  supportsVariants: true,
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
                  supportsVariants: true,
                  title: 'Reranker 模型',
                  repoId: String(config.local_qwen3_vl_reranker_repo_id || ''),
                  path: String(config.local_qwen3_vl_reranker_model_path || ''),
                  downloaded: Boolean(config.local_qwen3_vl_reranker_model_downloaded),
                  activeVariant: String(config.local_qwen3_vl_reranker_active_variant || '2B'),
                  selectedVariant: rerankerVariant,
                  task: getLatestModelTask('reranker'),
                  onVariantChange: setRerankerVariant,
                },
              ] satisfies Array<{
                type: ManagedModelType
                supportsVariants: true
                title: string
                repoId: string
                path: string
                downloaded: boolean
                activeVariant: string
                selectedVariant: '2B' | '8B'
                task: TaskLog | null
                onVariantChange: Dispatch<SetStateAction<'2B' | '8B'>>
              }>).map((item) => {
                const task = item.task
                const isRunning = task?.status === 'running'
                const progress = Math.max(0, Math.min(Number(task?.meta?.progress_percent || 0), 100))
                const downloadedBytes = Number(task?.meta?.downloaded_bytes || 0)
                const totalBytes = Number(task?.meta?.total_bytes || 0)
                const downloadedFiles = Number(task?.meta?.downloaded_files || task?.success_count || 0)
                const totalFiles = Number(task?.meta?.total_files || task?.total_count || 0)
                const taskVariant = String(task?.meta?.variant || (item.supportsVariants ? item.selectedVariant : item.repoId || ''))
                const showVariantSelector = item.supportsVariants
                const variantIsActive = item.supportsVariants ? item.selectedVariant === item.activeVariant : true
                const activateLabel = item.supportsVariants
                  ? (item.selectedVariant === item.activeVariant ? '当前生效中' : '设为当前')
                  : '当前路径'
                const repoPreview = item.supportsVariants
                  ? (item.selectedVariant === item.activeVariant
                    ? item.repoId || '—'
                    : `Qwen/Qwen3-VL-${item.type === 'embedding' ? 'Embedding' : 'Reranker'}-${item.selectedVariant}`)
                  : item.repoId || '—'
                const pathPreview = item.supportsVariants
                  ? (item.selectedVariant === item.activeVariant
                    ? item.path || '—'
                    : `${String(config.local_model_storage_path || '/data/models')}/qwen3-vl-${item.type}-${String(item.selectedVariant).toLowerCase()}`)
                  : item.path || '—'

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
                      {item.supportsVariants ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          当前默认启用规格: <span className="font-mono">{item.activeVariant}</span>
                        </p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-2">
                      {showVariantSelector && (
                        <select
                          value={item.selectedVariant}
                          onChange={(event) => item.onVariantChange?.(event.target.value as '2B' | '8B')}
                          disabled={downloadingModelType !== null || activatingModelType !== null || isRunning}
                          className="px-2 py-1.5 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        >
                          {(config.local_model_variants || ['2B', '8B']).map((variant: string) => (
                            <option key={variant} value={variant}>{variant}</option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() => handleDownloadLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isRunning}
                        className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
                      >
                        {downloadingModelType === item.type ? '提交中...' : isRunning ? '下载中...' : showVariantSelector ? `下载 ${item.selectedVariant}` : '下载'}
                      </button>
                      <button
                        onClick={() => handleActivateLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isRunning || variantIsActive}
                        className="px-3 py-1.5 text-xs bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50"
                      >
                        {activatingModelType === item.type ? '切换中...' : activateLabel}
                      </button>
                    </div>
                  </div>
                  <div className="mt-3 space-y-2">
                    <div>
                      <div className="text-[11px] text-muted-foreground font-mono">repo</div>
                      <div className="text-xs font-mono break-all">
                        {repoPreview}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-muted-foreground font-mono">path</div>
                      <div className="text-xs font-mono break-all">
                        {pathPreview}
                      </div>
                    </div>
                  </div>
                  {task && (
                    <div className="mt-4 rounded-lg border border-border bg-background/70 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-xs font-medium">
                            最近任务
                            {taskVariant && <span className="ml-2 font-mono text-[11px] text-muted-foreground">{taskVariant}</span>}
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
            </>
            ) : (
              <div className="rounded-xl border border-border bg-secondary/40 p-4 text-sm text-muted-foreground">
                当前识别结果直接由 VL 模型生成，不依赖本地样图 embedding 索引，因此不显示 embedding / reranker 下载、切换与重建相关配置。
              </div>
            )}
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

      {tab === 'vl' && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
          <div className="space-y-4">
            <div className="overflow-hidden rounded-2xl border border-border bg-card">
              <div className="border-b border-border bg-[linear-gradient(135deg,rgba(16,185,129,0.08),rgba(15,23,42,0.02))] px-5 py-4">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 rounded-xl border border-border bg-background p-2.5">
                    <Bot className="h-4 w-4 text-primary" />
                  </div>
                  <div>
                    <h2 className="text-sm font-medium">视觉模型调试工作台</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      上传单张图片，自定义系统提示词和用户提示词，直接查看 VL 模型原始返回。
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                      <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                        model: {String(config.qwen_model || '未配置')}
                      </span>
                      <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                        mode: remote-vl
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="space-y-4 p-5">
                <div
                  {...getVlRootProps()}
                  className={cn(
                    'group rounded-2xl border border-dashed p-4 transition-colors',
                    isVlDragActive ? 'border-primary bg-primary/5' : 'border-border bg-secondary/30 hover:border-primary/30 hover:bg-secondary/60',
                  )}
                >
                  <input {...getVlInputProps()} />
                  {vlImagePreviewUrl ? (
                    <div className="space-y-3">
                      <div className="overflow-hidden rounded-xl border border-border bg-background">
                        <div className="flex justify-center bg-secondary/20 p-2">
                          <div className="relative inline-block">
                            <img src={vlImagePreviewUrl} alt="VL test preview" className="block max-h-[280px] max-w-full" />
                            {vlDebugBoxes.length > 0 && (
                              <div className="pointer-events-none absolute inset-0">
                                {vlDebugBoxes.map((item, index) => (
                                  <div
                                    key={`vl-debug-box-${index}-${item.name}-${item.bbox.x1}-${item.bbox.y1}`}
                                    className="absolute rounded-lg border-2 border-emerald-500/90 bg-emerald-500/10"
                                    style={{
                                      left: `${item.bbox.x1}%`,
                                      top: `${item.bbox.y1}%`,
                                      width: `${item.bbox.x2 - item.bbox.x1}%`,
                                      height: `${item.bbox.y2 - item.bbox.y1}%`,
                                    }}
                                  >
                                    <div className="absolute left-0 top-0 -translate-y-full rounded-md bg-emerald-600 px-2 py-1 text-[10px] leading-none text-white shadow-sm">
                                      {item.name}
                                      {item.confidence !== undefined ? ` ${(item.confidence * 100).toFixed(0)}%` : ''}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation()
                                clearVlImage()
                              }}
                              className="absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-full border border-border bg-background/90 text-muted-foreground transition-colors hover:text-foreground"
                            >
                              <X className="h-4 w-4" />
                            </button>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{vlImageFile?.name}</div>
                          <div className="text-[11px] text-muted-foreground">
                            {vlImageFile ? `${(vlImageFile.size / 1024 / 1024).toFixed(2)} MB` : ''}
                          </div>
                          {vlDebugBoxes.length > 0 && (
                            <div className="mt-1 text-[11px] text-emerald-700">
                              已解析 {vlDebugBoxes.length} 个 bbox，可在图片上直接查看框选区域。
                            </div>
                          )}
                        </div>
                        <div className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-mono text-muted-foreground">
                          单图测试
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="flex min-h-[220px] flex-col items-center justify-center text-center">
                      <div className="mb-4 rounded-2xl border border-border bg-background p-4">
                        <ImageUp className="h-7 w-7 text-primary" />
                      </div>
                      <div className="text-sm font-medium">拖拽图片到这里，或点击选择文件</div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        支持 JPG、PNG、WEBP、BMP。建议使用原图，便于复现线上响应。
                      </div>
                    </div>
                  )}
                </div>

                <div className="space-y-3">
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-foreground">系统提示词</div>
                    <textarea
                      value={vlSystemPrompt}
                      onChange={(event) => setVlSystemPrompt(event.target.value)}
                      rows={4}
                      placeholder="可选。为空时不附带 system message。"
                      className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                    />
                  </div>
                  <div>
                    <div className="mb-1.5 text-xs font-medium text-foreground">Temperature</div>
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.1"
                      value={vlTemperature}
                      onChange={(event) => setVlTemperature(event.target.value)}
                      className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm font-mono outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                    />
                    <div className="mt-1 text-[11px] text-muted-foreground">调试范围 0 到 1，值越高随机性越强。</div>
                  </div>
                  <div>
                    <div className="mb-1.5 flex items-center justify-between gap-3">
                      <div className="text-xs font-medium text-foreground">用户提示词</div>
                      <button
                        type="button"
                        onClick={handleImportTodayMenu}
                        disabled={vlDefaultsLoading}
                        className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                      >
                        {vlDefaultsLoading ? '导入中...' : '导入今日菜单'}
                      </button>
                    </div>
                    <textarea
                      value={vlUserPrompt}
                      onChange={(event) => setVlUserPrompt(event.target.value)}
                      rows={8}
                      placeholder="输入要发给 VL 模型的用户提示词"
                      className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                    />
                    {vlImportedMenuInfo && (
                      <div className="mt-1 text-[11px] text-muted-foreground">
                        已导入 {vlImportedMenuInfo.date} 菜单，候选菜品 {vlImportedMenuInfo.count} 道。
                        {vlImportedMenuInfo.isDefault ? ' 当前日期未单独配置菜单，因此使用全部启用菜品。' : ''}
                      </div>
                    )}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={handleVlSubmit}
                    disabled={vlLoading}
                    className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                  >
                    <SendHorizontal className={cn('h-4 w-4', vlLoading && 'animate-pulse')} />
                    {vlLoading ? '请求模型中...' : '发送测试请求'}
                  </button>
                  <button
                    type="button"
                    onClick={loadVlDefaults}
                    disabled={vlDefaultsLoading}
                    className="rounded-xl border border-border bg-background px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {vlDefaultsLoading ? '重置中...' : '重置提示词'}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <DebugMetricCard
                icon={<Bot className="h-4 w-4" />}
                label="模型"
                value={vlResult?.model || String(config.qwen_model || '—')}
              />
              <DebugMetricCard
                icon={<SendHorizontal className="h-4 w-4" />}
                label="Temperature"
                value={String(vlResult?.temperature ?? (vlTemperature || '—'))}
              />
              <DebugMetricCard
                icon={<Braces className="h-4 w-4" />}
                label="请求格式"
                value={vlResult?.request_format || '—'}
              />
              <DebugMetricCard
                icon={<ImageUp className="h-4 w-4" />}
                label="文件"
                value={vlResult?.filename || vlImageFile?.name || '未选择'}
              />
            </div>

            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex items-center gap-2">
                <FileJson className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">解析文本</h3>
              </div>
              {vlResult ? (
                <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-secondary/40 p-4 text-sm leading-6 text-foreground">
                  {vlResult.content || '模型未返回可提取文本'}
                </pre>
              ) : (
                <EmptyDebugState text="发起测试后，这里会显示从原始响应中提取出的文本内容。" />
              )}
            </div>

            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex items-center gap-2">
                <Braces className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">解析后的 JSON</h3>
              </div>
              {vlResult?.parsed_json ? (
                <pre className="max-h-[320px] overflow-auto rounded-xl bg-secondary/40 p-4 text-xs leading-6 text-foreground">
                  {formatDebugJson(vlResult.parsed_json)}
                </pre>
              ) : (
                <EmptyDebugState text={vlResult?.json_parse_error || '未识别到可解析的 JSON 结果。'} />
              )}
            </div>

            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex items-center gap-2">
                <ImageUp className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">BBox 结果</h3>
              </div>
              {vlDebugBoxes.length > 0 ? (
                <div className="space-y-2">
                  {vlDebugBoxes.map((item, index) => (
                    <div key={`vl-debug-box-row-${index}-${item.name}`} className="rounded-xl bg-secondary/40 px-3 py-2.5 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-700">
                          {item.name}
                        </span>
                        {item.position && (
                          <span className="text-muted-foreground">位置 {item.position}</span>
                        )}
                        {item.confidence !== undefined && (
                          <span className="text-muted-foreground">置信度 {(item.confidence * 100).toFixed(0)}%</span>
                        )}
                      </div>
                      <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                        ({item.bbox.x1}, {item.bbox.y1}) - ({item.bbox.x2}, {item.bbox.y2})
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyDebugState text="当前解析结果里没有可视化的 bbox。可让提示词返回 dishes[].bbox 后再测试。" />
              )}
            </div>

            <div className="rounded-2xl border border-border bg-card p-5">
              <div className="mb-3 flex items-center gap-2">
                <FileJson className="h-4 w-4 text-muted-foreground" />
                <h3 className="text-sm font-medium">原始响应</h3>
              </div>
              {vlResult ? (
                <pre className="max-h-[520px] overflow-auto rounded-xl bg-[linear-gradient(180deg,rgba(15,23,42,0.96),rgba(15,23,42,0.88))] p-4 text-xs leading-6 text-slate-100">
                  {formatDebugJson(vlResult.raw_response)}
                </pre>
              ) : (
                <EmptyDebugState text="还没有请求记录。上传图片并发送测试请求后，这里会展示服务端返回的完整 JSON。" />
              )}
            </div>
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

      {tab === 'tasks' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted-foreground">最近 {allTasks.length} 条任务记录</span>
            <button onClick={loadAllTasks} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
              <RefreshCw className={cn('w-3.5 h-3.5', tasksLoading && 'animate-spin')} />刷新
            </button>
          </div>
          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <table className="data-table min-w-[960px]">
              <thead><tr><th>任务类型</th><th>日期</th><th>状态</th><th>总数</th><th>成功</th><th>低置信</th><th>失败</th><th>开始时间</th><th>结束时间</th><th>耗时</th></tr></thead>
              <tbody>
                {tasksLoading && <tr><td colSpan={10} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
                {!tasksLoading && allTasks.length === 0 && <tr><td colSpan={10} className="text-center py-12 text-muted-foreground">暂无任务记录</td></tr>}
                {!tasksLoading && allTasks.map((task) => (
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
      )}
    </div>
  )
}

function DebugMetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4">
      <div className="mb-3 inline-flex rounded-xl border border-border bg-secondary/50 p-2 text-muted-foreground">
        {icon}
      </div>
      <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className="mt-1 break-all font-mono text-sm text-foreground">{value || '—'}</div>
    </div>
  )
}

function EmptyDebugState({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-secondary/20 px-4 py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  )
}

function formatDebugJson(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
