import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { RefreshCw, Settings } from 'lucide-react'
import { adminApi, analysisApi, menuApi, syncApi } from '@/api/client'
import type { ManagedModelType } from '@/api/client'
import {
  DEFAULT_MEAL_SLOTS,
  DEFAULT_VL_BBOX_SYSTEM_PROMPT,
  DEFAULT_VL_BBOX_USER_PROMPT,
  DEFAULT_VL_SYSTEM_PROMPT,
  DEFAULT_VL_USER_PROMPT,
  DEFAULT_VL_USER_PROMPT_TEMPLATE,
  RECOGNITION_MENU_SCOPE_OPTIONS,
  ROLE_LABELS,
  formatBytes,
  formatDateForApi,
  injectDishListIntoPrompt,
  normalizeRecognitionMenuScope,
  normalizeVlDebugBoxes,
  type AdminTab,
  type ImportedMenuInfo,
  type RecognitionMenuScope,
  type VlTestResult,
} from '@/components/admin/adminPageShared'
import { SyncAdminTab, TasksAdminTab, UsersAdminTab } from '@/components/admin/AdminUtilityTabs'
import LocalEmbeddingDebugPanel from '@/components/admin/LocalEmbeddingDebugPanel'
import VlDebugTab from '@/components/admin/VlDebugTab'
import { fmtDateTime, cn, isLocalRecognitionMode } from '@/lib/utils'
import type { Department, Dish, MealSlot, TaskLog, User } from '@/types'
import toast from 'react-hot-toast'
import { useDropzone } from 'react-dropzone'

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('users')
  const [users, setUsers] = useState<User[]>([])
  const [usersTotal, setUsersTotal] = useState(0)
  const [departments, setDepartments] = useState<Department[]>([])
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | null>(null)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [modelDownloadTasks, setModelDownloadTasks] = useState<TaskLog[]>([])
  const [allTasks, setAllTasks] = useState<TaskLog[]>([])
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; active_users: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [savingSystemConfig, setSavingSystemConfig] = useState(false)
  const [downloadingModelType, setDownloadingModelType] = useState<ManagedModelType | null>(null)
  const [activatingModelType, setActivatingModelType] = useState<ManagedModelType | null>(null)
  const [embeddingVariant, setEmbeddingVariant] = useState<'2B' | '8B'>('2B')
  const [rerankerVariant, setRerankerVariant] = useState<'2B' | '8B'>('2B')
  const [vlImageFile, setVlImageFile] = useState<File | null>(null)
  const [vlImagePreviewUrl, setVlImagePreviewUrl] = useState('')
  const [vlUserPrompt, setVlUserPrompt] = useState(DEFAULT_VL_USER_PROMPT)
  const [vlSystemPrompt, setVlSystemPrompt] = useState('')
  const [vlTemperature, setVlTemperature] = useState('0.1')
  const [vlLoading, setVlLoading] = useState(false)
  const [vlDefaultsLoading, setVlDefaultsLoading] = useState(false)
  const [vlResult, setVlResult] = useState<VlTestResult | null>(null)
  const [vlImportedMenuInfo, setVlImportedMenuInfo] = useState<ImportedMenuInfo | null>(null)
  const [yoloModelFile, setYoloModelFile] = useState<File | null>(null)
  const [yoloUploadLoading, setYoloUploadLoading] = useState(false)
  const [mealSlots, setMealSlots] = useState<MealSlot[]>(DEFAULT_MEAL_SLOTS)
  const [mealSlotsDirty, setMealSlotsDirty] = useState(false)
  const [videoAnalysisMaxConcurrency, setVideoAnalysisMaxConcurrency] = useState('3')
  const [videoAnalysisMaxConcurrencyDirty, setVideoAnalysisMaxConcurrencyDirty] = useState(false)
  const [timeOffsetCalibration, setTimeOffsetCalibration] = useState('0')
  const [timeOffsetCalibrationDirty, setTimeOffsetCalibrationDirty] = useState(false)
  const [recognitionMenuScope, setRecognitionMenuScope] = useState<RecognitionMenuScope>('meal')
  const [recognitionMenuScopeDirty, setRecognitionMenuScopeDirty] = useState(false)
  const [menuReminderResponsibleUserIds, setMenuReminderResponsibleUserIds] = useState<number[]>([])
  const [menuReminderResponsibleUserIdsDirty, setMenuReminderResponsibleUserIdsDirty] = useState(false)
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null)
  const localRecognitionModeEnabled = isLocalRecognitionMode(String(config.dish_recognition_mode || ''))
  const vlDebugBoxes = normalizeVlDebugBoxes(vlResult?.parsed_json ?? null)
  const vlPromptSupportsDishList = vlUserPrompt.includes('{dish_list_with_desc}') || vlUserPrompt.includes('候选菜品列表：')

  const loadUsers = async (deptId = selectedDepartmentId) => {
    setLoading(true)
    try {
      const res = await adminApi.users({
        page_size: 50,
        ...(deptId ? { dept_id: deptId, include_descendants: true } : {}),
      })
      setUsers(res.data.data.items)
      setUsersTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  const loadDepartments = async () => {
    const res = await adminApi.departments()
    setDepartments(res.data.data || [])
  }

  const refreshUsersPanel = async () => {
    await Promise.all([loadDepartments(), loadUsers()])
  }

  const loadConfigUsers = async () => {
    const res = await adminApi.users({ page_size: 200, active_only: true })
    setUsers(res.data.data.items || [])
  }

  const loadConfig = async (options?: { syncSelectedVariants?: boolean; syncEditableFields?: boolean }) => {
    const res = await adminApi.config()
    setConfig(res.data.data)
    if (options?.syncEditableFields !== false) {
      const nextMealSlots = Array.isArray(res.data.data.meal_slots) && res.data.data.meal_slots.length > 0
        ? res.data.data.meal_slots
        : DEFAULT_MEAL_SLOTS
      setMealSlots(nextMealSlots.map((item: MealSlot) => ({
        key: String(item.key || ''),
        label: String(item.label || ''),
        start: String(item.start || ''),
        end: String(item.end || ''),
      })))
      setMealSlotsDirty(false)
      setVideoAnalysisMaxConcurrency(String(res.data.data.video_analysis_max_concurrency || 3))
      setVideoAnalysisMaxConcurrencyDirty(false)
      setTimeOffsetCalibration(
        res.data.data.time_offset_calibration === undefined || res.data.data.time_offset_calibration === null
          ? '0'
          : String(res.data.data.time_offset_calibration),
      )
      setTimeOffsetCalibrationDirty(false)
      setRecognitionMenuScope(normalizeRecognitionMenuScope(res.data.data.recognition_menu_scope))
      setRecognitionMenuScopeDirty(false)
      setMenuReminderResponsibleUserIds(
        Array.isArray(res.data.data.menu_reminder_responsible_user_ids)
          ? res.data.data.menu_reminder_responsible_user_ids.map((id: number | string) => Number(id)).filter(Number.isFinite)
          : [],
      )
      setMenuReminderResponsibleUserIdsDirty(false)
    }
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

  const applyVlBboxDefaults = (temperature?: string) => {
    setVlSystemPrompt(DEFAULT_VL_BBOX_SYSTEM_PROMPT)
    setVlUserPrompt(DEFAULT_VL_BBOX_USER_PROMPT)
    setVlTemperature((temperature && temperature.trim()) || '0.1')
    setVlImportedMenuInfo(null)
    setVlResult(null)
  }

  useEffect(() => {
    if (tab === 'users') refreshUsersPanel()
    else if (tab === 'config') {
      loadConfig({ syncSelectedVariants: true })
      loadConfigUsers()
      loadModelDownloadTasks()
    }
    else if (tab === 'embedding') loadConfig()
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
      loadConfig({ syncEditableFields: !(mealSlotsDirty || videoAnalysisMaxConcurrencyDirty || timeOffsetCalibrationDirty || recognitionMenuScopeDirty || menuReminderResponsibleUserIdsDirty) })
      loadModelDownloadTasks()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [tab, mealSlotsDirty, videoAnalysisMaxConcurrencyDirty, timeOffsetCalibrationDirty, recognitionMenuScopeDirty, menuReminderResponsibleUserIdsDirty])

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

  const saveSystemConfig = async () => {
    const normalizedMealSlots = mealSlots.map((item) => ({
      key: String(item.key || '').trim(),
      label: String(item.label || '').trim(),
      start: String(item.start || '').trim(),
      end: String(item.end || '').trim(),
    }))
    if (!normalizedMealSlots.length) {
      toast.error('至少保留一个餐次')
      return
    }
    const keyPattern = /^[a-zA-Z0-9_]+$/
    for (const slot of normalizedMealSlots) {
      if (!slot.key || !slot.label || !slot.start || !slot.end) {
        toast.error('每个餐次都需要填写 key、label、start、end')
        return
      }
      if (!keyPattern.test(slot.key)) {
        toast.error(`餐次 key 只能包含字母、数字、下划线: ${slot.key}`)
        return
      }
    }
    const uniqueKeys = new Set(normalizedMealSlots.map((slot) => slot.key))
    if (uniqueKeys.size !== normalizedMealSlots.length) {
      toast.error('餐次 key 不能重复')
      return
    }
    const normalizedConcurrency = Number.parseInt(videoAnalysisMaxConcurrency.trim(), 10)
    if (!Number.isFinite(normalizedConcurrency) || normalizedConcurrency < 1) {
      toast.error('最大分析并发必须是大于等于 1 的整数')
      return
    }
    const normalizedTimeOffset = Number.parseFloat(timeOffsetCalibration.trim())
    if (!Number.isFinite(normalizedTimeOffset) || Math.abs(normalizedTimeOffset) > 86400) {
      toast.error('时间偏移校正必须是数字，且绝对值不能超过 86400 秒')
      return
    }

    setSavingSystemConfig(true)
    try {
      const res = await adminApi.updateConfig({
        meal_slots: normalizedMealSlots,
        video_analysis_max_concurrency: normalizedConcurrency,
        time_offset_calibration: normalizedTimeOffset,
        recognition_menu_scope: recognitionMenuScope,
        menu_reminder_responsible_user_ids: menuReminderResponsibleUserIds,
      })
      toast.success(res.data.data.message || '系统配置已更新')
      await loadConfig()
    } finally {
      setSavingSystemConfig(false)
    }
  }

  const saveRecognitionMenuScope = async () => {
    setSavingSystemConfig(true)
    try {
      const res = await adminApi.updateConfig({
        recognition_menu_scope: recognitionMenuScope,
      })
      toast.success(res.data.data.message || '召回配置已更新')
      await loadConfig()
    } finally {
      setSavingSystemConfig(false)
    }
  }

  const updateMealSlot = (index: number, patch: Partial<MealSlot>) => {
    setMealSlots((prev) => prev.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )))
    setMealSlotsDirty(true)
  }

  const addMealSlot = () => {
    setMealSlots((prev) => [...prev, { key: '', label: '', start: '', end: '' }])
    setMealSlotsDirty(true)
  }

  const removeMealSlot = (index: number) => {
    setMealSlots((prev) => (
      prev.length > 1 ? prev.filter((_, itemIndex) => itemIndex !== index) : prev
    ))
    setMealSlotsDirty(true)
  }

  const resetMealSlots = () => {
    setMealSlots(DEFAULT_MEAL_SLOTS.map((item) => ({ ...item })))
    setMealSlotsDirty(true)
  }

  const updateVideoAnalysisMaxConcurrency = (value: string) => {
    setVideoAnalysisMaxConcurrency(value)
    setVideoAnalysisMaxConcurrencyDirty(true)
  }

  const updateTimeOffsetCalibration = (value: string) => {
    setTimeOffsetCalibration(value)
    setTimeOffsetCalibrationDirty(true)
  }

  const updateRecognitionMenuScope = (value: RecognitionMenuScope) => {
    setRecognitionMenuScope(value)
    setRecognitionMenuScopeDirty(true)
  }

  const toggleMenuReminderResponsibleUser = (userId: number) => {
    setMenuReminderResponsibleUserIds((prev) => (
      prev.includes(userId)
        ? prev.filter((id) => id !== userId)
        : [...prev, userId]
    ))
    setMenuReminderResponsibleUserIdsDirty(true)
  }

  const updateUserRole = async (user: User, role: string) => {
    await adminApi.updateUser(user.id, { role })
    toast.success('角色已更新')
    loadUsers()
  }

  const deleteUser = async (user: User) => {
    if (!window.confirm(`确定删除用户「${user.name}」吗？删除后该用户将无法登录。`)) return

    setDeletingUserId(user.id)
    try {
      await adminApi.deleteUser(user.id)
      toast.success('用户已删除')
      await loadUsers()
    } finally {
      setDeletingUserId(null)
    }
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

  const {
    getRootProps: getYoloRootProps,
    getInputProps: getYoloInputProps,
    isDragActive: isYoloDragActive,
  } = useDropzone({
    onDrop: (files) => {
      if (!files.length) return
      setYoloModelFile(files[0])
    },
    onDropRejected: () => {
      toast.error('请上传 .pt 格式的 YOLO 模型文件')
    },
    accept: { 'application/octet-stream': ['.pt'] },
    maxFiles: 1,
    multiple: false,
  })

  const handleUploadYoloModel = async () => {
    if (!yoloModelFile) {
      toast.error('请先选择 YOLO 模型文件')
      return
    }
    setYoloUploadLoading(true)
    try {
      await adminApi.uploadYoloModel(yoloModelFile)
      toast.success('YOLO 模型上传成功')
      setYoloModelFile(null)
      await loadConfig()
    } finally {
      setYoloUploadLoading(false)
    }
  }

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
    if (!vlPromptSupportsDishList) {
      toast('当前提示词没有候选菜品列表占位，BBox 预设通常不需要导入今日菜单')
      return
    }
    setVlDefaultsLoading(true)
    const today = formatDateForApi(new Date())
    try {
      const res = await menuApi.get(today)
      const menu = res.data.data || {}
      const dishes = Boolean(menu.is_default) ? [] : (Array.isArray(menu.dishes) ? menu.dishes : [])
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
      if (dishes.length) {
        toast.success(`${today} 菜单已导入测试提示词`)
      } else {
        toast.error(`${today} 未配置菜单，请先配置后再导入`)
      }
    } finally {
      setVlDefaultsLoading(false)
    }
  }

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">系统管理</h1>
        <p className="text-sm text-muted-foreground mt-0.5">用户管理 · 系统配置 · Embedding 测试 · VL 测试 · 数据同步 · 任务总览</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-full sm:w-fit overflow-x-auto mb-5">
        {(['users', 'config', 'embedding', 'vl', 'sync', 'tasks'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 text-sm rounded-md transition-colors', tab === t ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground')}>
            {t === 'users'
              ? '用户管理'
              : t === 'config'
                  ? '系统配置'
                  : t === 'embedding'
                    ? 'Embedding 测试'
                    : t === 'vl'
                      ? 'VL 测试'
                      : t === 'sync'
                        ? '数据同步'
                        : '全部任务'}
          </button>
        ))}
      </div>

      {tab === 'users' && (
        <UsersAdminTab
          users={users}
          usersTotal={usersTotal}
          departments={departments}
          selectedDepartmentId={selectedDepartmentId}
          loading={loading}
          deletingUserId={deletingUserId}
          onSelectDepartment={(deptId) => {
            setSelectedDepartmentId(deptId)
            loadUsers(deptId)
          }}
          onRefresh={refreshUsersPanel}
          onUpdateUserRole={updateUserRole}
          onDeleteUser={deleteUser}
          getRootProps={getRootProps}
          getInputProps={getInputProps}
        />
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
                  。下载与切换统一转发到 retrieval-api 执行，不再依赖主服务本地模型目录。
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
                const isTaskInFlight = task?.status === 'pending' || task?.status === 'running'
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
                          disabled={downloadingModelType !== null || activatingModelType !== null || isTaskInFlight}
                          className="px-2 py-1.5 text-xs bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        >
                          {(config.local_model_variants || ['2B', '8B']).map((variant: string) => (
                            <option key={variant} value={variant}>{variant}</option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() => handleDownloadLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isTaskInFlight}
                        className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
                      >
                        {downloadingModelType === item.type ? '提交中...' : task?.status === 'pending' ? '排队中...' : task?.status === 'running' ? '下载中...' : showVariantSelector ? `下载 ${item.selectedVariant}` : '下载'}
                      </button>
                      <button
                        onClick={() => handleActivateLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isTaskInFlight || variantIsActive}
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
                            {String(task.meta?.status_text || (task.status === 'pending' ? '等待下载开始' : task.status === 'running' ? '模型下载中' : task.status === 'success' ? '模型下载完成' : '模型下载失败'))}
                          </div>
                        </div>
                        <div className={cn(
                          'text-xs font-medium',
                          task.status === 'success' && 'text-health-green',
                          task.status === 'failed' && 'text-health-red',
                          task.status === 'running' && 'text-health-blue',
                          task.status === 'pending' && 'text-muted-foreground',
                        )}>
                          {task.status === 'pending' ? '等待中' : task.status === 'running' ? `${progress.toFixed(1)}%` : task.status === 'success' ? '已完成' : '失败'}
                        </div>
                      </div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full bg-secondary">
                        <div
                          className={cn(
                            'h-full rounded-full transition-all',
                            task.status === 'failed' ? 'bg-health-red' : task.status === 'success' ? 'bg-health-green' : 'bg-health-blue',
                          )}
                          style={{ width: `${task.status === 'failed' ? Math.max(progress, 6) : task.status === 'pending' ? 6 : progress}%` }}
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
              “设为当前”会写入 <span className="font-mono">{String(config.local_runtime_config_path || 'runtime_config.json')}</span>，
              由 retrieval-api 按该配置读取模型。
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              如果内网下载慢，可在部署环境里设置 <span className="font-mono">HF_ENDPOINT=https://hf-mirror.com</span>
              后重启 retrieval-api。
            </p>
            {config.retrieval_api_status_error && (
              <p className="mt-1 text-[11px] text-health-red break-words">
                retrieval-api 状态读取失败：{String(config.retrieval_api_status_error)}
              </p>
            )}
            </>
            ) : (
              <div className="rounded-xl border border-border bg-secondary/40 p-4 text-sm text-muted-foreground">
                当前识别结果直接由 VL 模型生成，不依赖本地样图 embedding 索引，因此不显示 embedding / reranker 下载、切换与重建相关配置。
              </div>
            )}

            <div className="mt-6 rounded-xl border border-border bg-secondary/60 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-medium">YOLO 检测模型</h3>
                  <p className={cn(
                    'mt-1 text-xs font-medium',
                    config.yolo_model_ready ? 'text-health-green' : 'text-health-amber',
                  )}>
                    {config.yolo_model_ready ? '模型已就绪' : '模型未就绪'}
                  </p>
                  {config.yolo_model_path && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      路径：<span className="font-mono break-all">{String(config.yolo_model_path)}</span>
                    </p>
                  )}
                  {config.yolo_model_filename && (
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      文件：<span className="font-mono">{String(config.yolo_model_filename)}</span>
                    </p>
                  )}
                  {config.yolo_model_status_error && (
                    <p className="mt-1 text-[11px] text-health-red break-words">
                      状态读取失败：{String(config.yolo_model_status_error)}
                    </p>
                  )}
                </div>
              </div>

              <div
                {...getYoloRootProps()}
                className={cn(
                  'mt-4 border-2 border-dashed border-border rounded-xl p-6 flex flex-col items-center justify-center text-center cursor-pointer hover:bg-secondary/50 transition',
                  isYoloDragActive && 'border-primary bg-primary/5',
                )}
              >
                <input {...getYoloInputProps()} />
                {yoloModelFile ? (
                  <div className="w-full">
                    <p className="text-sm font-medium">{yoloModelFile.name}</p>
                    <p className="text-xs text-muted-foreground mt-1">{formatBytes(yoloModelFile.size)}</p>
                  </div>
                ) : (
                  <>
                    <p className="text-sm font-medium">拖拽或点击上传 .pt 模型文件</p>
                    <p className="text-xs text-muted-foreground mt-1">支持 YOLO PyTorch 模型，建议文件名如 best.pt</p>
                  </>
                )}
              </div>

              <div className="mt-4 flex items-center gap-3">
                <button
                  onClick={handleUploadYoloModel}
                  disabled={!yoloModelFile || yoloUploadLoading}
                  className="px-4 py-1.5 text-xs bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
                >
                  {yoloUploadLoading ? '上传中...' : '上传模型'}
                </button>
                {yoloModelFile && (
                  <button
                    onClick={() => setYoloModelFile(null)}
                    disabled={yoloUploadLoading}
                    className="px-3 py-1.5 text-xs bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50"
                  >
                    清除选择
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
              <div>
                <h2 className="text-sm font-medium flex items-center gap-2">
                  <Settings className="w-4 h-4 text-muted-foreground" />识别召回配置
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  控制正式识别和 pipeline 调试传给召回服务的候选菜品。
                </p>
                <div className="mt-4 grid gap-2 md:grid-cols-3">
                  {RECOGNITION_MENU_SCOPE_OPTIONS.map((option) => {
                    const selected = recognitionMenuScope === option.value
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => updateRecognitionMenuScope(option.value)}
                        className={cn(
                          'min-h-[104px] rounded-lg border px-4 py-3 text-left transition',
                          selected
                            ? 'border-primary/60 bg-primary/10 text-foreground'
                            : 'border-border bg-secondary/30 text-muted-foreground hover:bg-secondary/60 hover:text-foreground',
                        )}
                      >
                        <span className="block text-sm font-medium">{option.label}</span>
                        <span className="mt-1 block text-xs leading-5">{option.description}</span>
                      </button>
                    )
                  })}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-secondary/40 p-4">
                <div className="text-sm font-medium">当前范围</div>
                <div className="mt-2 text-lg font-semibold">
                  {RECOGNITION_MENU_SCOPE_OPTIONS.find((option) => option.value === recognitionMenuScope)?.label || '当顿餐菜单'}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {recognitionMenuScopeDirty ? '有未保存更改' : '已同步当前配置'}
                </div>
                <button
                  onClick={saveRecognitionMenuScope}
                  disabled={savingSystemConfig}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingSystemConfig ? '保存中...' : '保存召回配置'}
                </button>
              </div>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
              <Settings className="w-4 h-4 text-muted-foreground" />餐次配置
            </h2>
            <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_240px]">
              <div>
                <div className="text-xs text-muted-foreground mb-2">
                  统一配置各餐次的标识、显示名称与起止时间。视频同步、菜单管理、提醒触发都会使用这里的时间窗口。
                </div>
                <div className="space-y-3">
                  {mealSlots.map((slot, index) => (
                    <div key={`meal-slot-${index}`} className="grid gap-3 rounded-lg border border-border bg-secondary/30 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
                      <label className="space-y-1">
                        <div className="text-xs text-muted-foreground">标识 key</div>
                        <input
                          type="text"
                          value={slot.key}
                          placeholder="breakfast"
                          onChange={(event) => updateMealSlot(index, { key: event.target.value })}
                          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                        />
                      </label>
                      <label className="space-y-1">
                        <div className="text-xs text-muted-foreground">显示名</div>
                        <input
                          type="text"
                          value={slot.label}
                          placeholder="早餐"
                          onChange={(event) => updateMealSlot(index, { label: event.target.value })}
                          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                        />
                      </label>
                      <label className="space-y-1">
                        <div className="text-xs text-muted-foreground">开始</div>
                        <input
                          type="time"
                          value={slot.start}
                          onChange={(event) => updateMealSlot(index, { start: event.target.value })}
                          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                        />
                      </label>
                      <label className="space-y-1">
                        <div className="text-xs text-muted-foreground">结束</div>
                        <input
                          type="time"
                          value={slot.end}
                          onChange={(event) => updateMealSlot(index, { end: event.target.value })}
                          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                        />
                      </label>
                      <div className="flex items-end">
                        <button
                          onClick={() => removeMealSlot(index)}
                          disabled={mealSlots.length <= 1}
                          className="rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-background disabled:opacity-50"
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 rounded-lg border border-border bg-secondary/30 p-3">
                  <label className="space-y-1">
                    <div className="text-xs text-muted-foreground">抽帧最大并发</div>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={videoAnalysisMaxConcurrency}
                      onChange={(event) => updateVideoAnalysisMaxConcurrency(event.target.value)}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                  <div className="mt-2 text-[11px] text-muted-foreground">
                    录像按通道轮询进入下载与抽帧流水线。默认 3，部署时的抽帧 worker 并发数应与此保持一致。
                  </div>
                </div>
                <div className="mt-4 rounded-lg border border-border bg-secondary/30 p-3">
                  <label className="space-y-1">
                    <div className="text-xs text-muted-foreground">消费-视频时间偏移校正（秒）</div>
                    <input
                      type="number"
                      step={0.1}
                      value={timeOffsetCalibration}
                      onChange={(event) => updateTimeOffsetCalibration(event.target.value)}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    />
                  </label>
                  <div className="mt-2 text-[11px] text-muted-foreground">
                    用于校正消费刷卡时间与视频画面时间之间的系统性偏差（一卡通与摄像头两套时钟）。该值会叠加到消费记录时间上再与视频匹配：正值表示消费刷卡时间整体早于视频画面（需后移对齐），负值相反。可为小数，默认 0。
                  </div>
                </div>
              </div>
              <div className="rounded-xl border border-border bg-secondary/40 p-4">
                <div className="text-sm font-medium">操作</div>
                <div className="mt-3 flex flex-col gap-2">
                  <button
                    onClick={addMealSlot}
                    className="inline-flex items-center justify-center rounded-lg border border-border bg-background px-4 py-2 text-sm transition hover:bg-secondary"
                  >
                    添加餐次
                  </button>
                  <button
                    onClick={resetMealSlots}
                    className="inline-flex items-center justify-center rounded-lg border border-border bg-background px-4 py-2 text-sm transition hover:bg-secondary"
                  >
                    恢复默认
                  </button>
                </div>
                <button
                  onClick={saveSystemConfig}
                  disabled={savingSystemConfig}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingSystemConfig ? '保存中...' : '保存系统配置'}
                </button>
                <div className="mt-3 text-xs text-muted-foreground">
                  默认值：
                  {' '}
                  {DEFAULT_MEAL_SLOTS.map((item) => `${item.label} ${item.start}-${item.end}`).join(' / ')}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  抽帧最大并发默认值：3
                </div>
              </div>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
              <Settings className="w-4 h-4 text-muted-foreground" />菜单与样图提醒
            </h2>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
              <div>
                <div className="text-xs text-muted-foreground mb-3">
                  系统会在每顿餐开始前 {Number(config.menu_reminder_before_minutes ?? 30)} 分钟检查当天该餐菜单和菜品样图，发现缺失后通过钉钉推送给所选责任人。
                </div>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {users.map((user) => {
                    const checked = menuReminderResponsibleUserIds.includes(user.id)
                    const disabled = !user.dingtalk_user_id && !checked
                    return (
                      <label
                        key={`menu-reminder-user-${user.id}`}
                        className={cn(
                          'flex min-h-[76px] cursor-pointer items-start gap-3 rounded-lg border border-border bg-secondary/30 p-3 transition hover:bg-secondary/60',
                          checked && 'border-primary/50 bg-primary/5',
                          disabled && 'cursor-not-allowed opacity-50',
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleMenuReminderResponsibleUser(user.id)}
                          className="mt-1 h-4 w-4 rounded border-border"
                        />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium text-foreground">{user.name}</span>
                          <span className="mt-1 block truncate text-xs text-muted-foreground">
                            {ROLE_LABELS[user.role] || user.role}{user.dept_name ? ` · ${user.dept_name}` : ''}
                          </span>
                          {!user.dingtalk_user_id && (
                            <span className="mt-1 block text-[11px] text-health-amber">缺少钉钉 userId</span>
                          )}
                        </span>
                      </label>
                    )
                  })}
                  {users.length === 0 && (
                    <div className="rounded-lg border border-border bg-secondary/30 p-4 text-sm text-muted-foreground">
                      暂无可选通讯录用户，请先在数据同步里同步钉钉组织。
                    </div>
                  )}
                </div>
              </div>
              <div className="rounded-xl border border-border bg-secondary/40 p-4">
                <div className="text-sm font-medium">当前责任人</div>
                <div className="mt-2 text-2xl font-semibold">{menuReminderResponsibleUserIds.length}</div>
                <div className="mt-1 text-xs text-muted-foreground">支持选择多个通讯录用户</div>
                <button
                  onClick={saveSystemConfig}
                  disabled={savingSystemConfig}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingSystemConfig ? '保存中...' : '保存提醒配置'}
                </button>
                <div className="mt-3 text-xs text-muted-foreground">
                  未选择责任人时，后端会默认推送给活跃的食堂管理员。
                </div>
              </div>
            </div>
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
            <p className="mt-4 text-xs text-muted-foreground">视频源接入与通道 ROI 已统一移至“通道管理”，其余系统配置仍以部署配置和运行时配置为准。</p>
          </div>
        </div>
      )}

      {tab === 'embedding' && (
        <LocalEmbeddingDebugPanel config={config} onRefreshConfig={loadConfig} />
      )}

      {tab === 'vl' && (
        <VlDebugTab
          config={config}
          imageFile={vlImageFile}
          imagePreviewUrl={vlImagePreviewUrl}
          debugBoxes={vlDebugBoxes}
          systemPrompt={vlSystemPrompt}
          setSystemPrompt={setVlSystemPrompt}
          userPrompt={vlUserPrompt}
          setUserPrompt={setVlUserPrompt}
          temperature={vlTemperature}
          setTemperature={setVlTemperature}
          importedMenuInfo={vlImportedMenuInfo}
          promptSupportsDishList={vlPromptSupportsDishList}
          defaultsLoading={vlDefaultsLoading}
          loading={vlLoading}
          result={vlResult}
          getRootProps={getVlRootProps}
          getInputProps={getVlInputProps}
          isDragActive={isVlDragActive}
          onClearImage={clearVlImage}
          onSubmit={handleVlSubmit}
          onLoadDefaults={loadVlDefaults}
          onApplyBboxDefaults={applyVlBboxDefaults}
          onImportTodayMenu={handleImportTodayMenu}
        />
      )}

      {tab === 'sync' && (
        <SyncAdminTab
          syncStatus={syncStatus}
          syncing={syncing}
          onTriggerSync={triggerSync}
        />
      )}

      {tab === 'tasks' && (
        <TasksAdminTab
          tasks={allTasks}
          loading={tasksLoading}
          onRefresh={loadAllTasks}
        />
      )}
    </div>
  )
}
