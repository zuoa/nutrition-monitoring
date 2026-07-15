import { useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { BellRing, CalendarClock, CheckCircle2, CircleAlert, Loader2, RefreshCw, Send, Settings } from 'lucide-react'
import { adminApi, analysisApi, dishApi, menuApi, syncApi } from '@/api/client'
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

type RetrievalPipeline = 'qwen' | 'visual'
type DingTalkNotificationMode = 'app' | 'webhook'
type WeekdayKey = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday'

const WEEKDAY_OPTIONS: Array<{ value: WeekdayKey; label: string }> = [
  { value: 'monday', label: '周一' },
  { value: 'tuesday', label: '周二' },
  { value: 'wednesday', label: '周三' },
  { value: 'thursday', label: '周四' },
  { value: 'friday', label: '周五' },
  { value: 'saturday', label: '周六' },
  { value: 'sunday', label: '周日' },
]

const normalizeWeekday = (value: unknown): WeekdayKey => (
  WEEKDAY_OPTIONS.some((option) => option.value === value) ? value as WeekdayKey : 'sunday'
)

type RequestedRebuild = {
  pipeline: RetrievalPipeline
  previousTaskId: number | null
}

const ADMIN_TAB_META: Record<AdminTab, { label: string; description: string }> = {
  users: {
    label: '用户与组织',
    description: '维护组织结构、用户角色与学生名单。',
  },
  business: {
    label: '业务规则',
    description: '配置菜单召回范围、餐次时间和视频处理参数。',
  },
  notifications: {
    label: '提醒通知',
    description: '设置业务提醒、营养预警通知和周报生成时间。',
  },
  models: {
    label: '模型管理',
    description: '管理识别模型、检索模式、向量索引和 YOLO 模型。',
  },
  embedding: {
    label: 'Embedding 调试',
    description: '验证样图向量、召回结果与本地检索链路。',
  },
  vl: {
    label: 'VL 调试',
    description: '调试视觉语言模型的提示词、识别结果与区域定位。',
  },
  sync: {
    label: '数据同步',
    description: '管理钉钉组织与一卡通消费数据的同步连接。',
  },
  operations: {
    label: '运行状态',
    description: '查看后台任务和当前生效的系统运行参数。',
  },
}

const ADMIN_TAB_GROUPS: Array<{ label: string; tabs: AdminTab[] }> = [
  { label: '基础管理', tabs: ['users', 'business', 'notifications'] },
  { label: '智能识别', tabs: ['models', 'embedding', 'vl'] },
  { label: '系统运维', tabs: ['sync', 'operations'] },
]

const isTaskInFlight = (task?: TaskLog | null) => task?.status === 'pending' || task?.status === 'running'

function IndexRebuildProgress({
  task,
  queued,
}: {
  task: TaskLog | null
  queued: boolean
}) {
  if (!task && !queued) return null

  const total = Number(task?.total_count || 0)
  const processed = Math.max(0, Math.min(Number(task?.meta?.processed ?? task?.success_count ?? 0), total || Number.MAX_SAFE_INTEGER))
  const rawProgress = Number(task?.meta?.progress_percent)
  const fallbackProgress = total > 0 ? (processed / total) * 90 : 0
  const progress = task?.status === 'success' || task?.status === 'partial'
    ? 100
    : Math.max(0, Math.min(Number.isFinite(rawProgress) ? rawProgress : fallbackProgress, 100))
  const statusText = queued
    ? '任务已提交，等待 Worker 接收'
    : String(task?.meta?.status_text || (task?.status === 'pending' ? '等待开始' : '正在重建索引'))
  const statusLabel = queued
    ? '排队中'
    : task?.status === 'pending'
      ? '排队中'
      : task?.status === 'running'
        ? `${progress.toFixed(0)}%`
        : task?.status === 'success'
          ? '已完成'
          : task?.status === 'partial'
            ? '部分完成'
            : '失败'
  const statusClass = task?.status === 'failed'
    ? 'text-health-red'
    : task?.status === 'success'
      ? 'text-health-green'
      : task?.status === 'partial'
        ? 'text-health-amber'
        : 'text-health-blue'

  return (
    <div className="mt-4 rounded-lg border border-border bg-background/80 p-3" aria-live="polite">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          {queued || isTaskInFlight(task) ? (
            <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-health-blue" aria-hidden="true" />
          ) : task?.status === 'failed' || task?.status === 'partial' ? (
            <CircleAlert
              className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', task.status === 'failed' ? 'text-health-red' : 'text-health-amber')}
              aria-hidden="true"
            />
          ) : (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-health-green" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <div className="text-xs font-medium">索引重建进度</div>
            <div className="mt-0.5 text-[11px] leading-5 text-muted-foreground">{statusText}</div>
          </div>
        </div>
        <span className={cn('shrink-0 text-xs font-medium tabular-nums', statusClass)}>{statusLabel}</span>
      </div>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-secondary"
        role="progressbar"
        aria-label="索引重建进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={queued ? undefined : Math.round(progress)}
        aria-valuetext={queued ? statusText : `${statusText}，${progress.toFixed(0)}%`}
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none',
            queued && 'animate-pulse bg-health-blue',
            task?.status === 'failed' && 'bg-health-red',
            task?.status === 'partial' && 'bg-health-amber',
            task?.status === 'success' && 'bg-health-green',
            task?.status !== 'failed' && task?.status !== 'partial' && task?.status !== 'success' && !queued && 'bg-health-blue',
          )}
          style={{ width: `${queued ? 8 : progress}%` }}
        />
      </div>
      {task ? (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {total > 0 ? <span>已处理 {processed}/{total} 张</span> : null}
          <span>复用 {Number(task.meta?.reused_count || 0)} 张</span>
          <span>新生成 {Number(task.meta?.generated_count || 0)} 张</span>
          {task.error_count > 0 ? <span className="text-health-red">失败 {task.error_count} 张</span> : null}
          <span>开始 {fmtDateTime(task.started_at)}</span>
        </div>
      ) : null}
      {task?.error_message ? <div className="mt-2 text-[11px] text-health-red">{task.error_message}</div> : null}
    </div>
  )
}

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>('users')
  const [users, setUsers] = useState<User[]>([])
  const [usersTotal, setUsersTotal] = useState(0)
  const [departments, setDepartments] = useState<Department[]>([])
  const [selectedDepartmentId, setSelectedDepartmentId] = useState<string | null>(null)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [localModelTasks, setLocalModelTasks] = useState<TaskLog[]>([])
  const [allTasks, setAllTasks] = useState<TaskLog[]>([])
  const [syncStatus, setSyncStatus] = useState<{ last_sync: string | null; active_users: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [tasksLoading, setTasksLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [savingSystemConfig, setSavingSystemConfig] = useState(false)
  const [downloadingModelType, setDownloadingModelType] = useState<ManagedModelType | null>(null)
  const [activatingModelType, setActivatingModelType] = useState<ManagedModelType | null>(null)
  const [switchingPipeline, setSwitchingPipeline] = useState(false)
  const [rebuildingPipeline, setRebuildingPipeline] = useState<RetrievalPipeline | null>(null)
  const [requestedRebuild, setRequestedRebuild] = useState<RequestedRebuild | null>(null)
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
  const [videoAnalysisMaxConcurrency, setVideoAnalysisMaxConcurrency] = useState('2')
  const [videoAnalysisMaxConcurrencyDirty, setVideoAnalysisMaxConcurrencyDirty] = useState(false)
  const [timeOffsetCalibration, setTimeOffsetCalibration] = useState('0')
  const [timeOffsetCalibrationDirty, setTimeOffsetCalibrationDirty] = useState(false)
  const [recognitionMenuScope, setRecognitionMenuScope] = useState<RecognitionMenuScope>('meal')
  const [recognitionMenuScopeDirty, setRecognitionMenuScopeDirty] = useState(false)
  const [menuReminderResponsibleUserIds, setMenuReminderResponsibleUserIds] = useState<number[]>([])
  const [menuReminderResponsibleUserIdsDirty, setMenuReminderResponsibleUserIdsDirty] = useState(false)
  const [menuReminderDingTalkMode, setMenuReminderDingTalkMode] = useState<DingTalkNotificationMode>('app')
  const [menuReminderDingTalkWebhook, setMenuReminderDingTalkWebhook] = useState('')
  const [menuReminderDingTalkWebhookPrefix, setMenuReminderDingTalkWebhookPrefix] = useState('[营养监测系统提醒]')
  const [menuReminderDingTalkConfigDirty, setMenuReminderDingTalkConfigDirty] = useState(false)
  const [nutritionAlertNotificationEnabled, setNutritionAlertNotificationEnabled] = useState(true)
  const [weeklyReportDayOfWeek, setWeeklyReportDayOfWeek] = useState<WeekdayKey>('sunday')
  const [weeklyReportTime, setWeeklyReportTime] = useState('08:00')
  const [scheduledNotificationsDirty, setScheduledNotificationsDirty] = useState(false)
  const [testingMenuReminderWebhook, setTestingMenuReminderWebhook] = useState(false)
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null)
  const localRecognitionModeEnabled = isLocalRecognitionMode(String(config.dish_recognition_mode || ''))
  const rebuildTaskInFlight = localModelTasks.some((task) => task.task_type === 'dish_embedding' && isTaskInFlight(task))
  const rebuildBusy = rebuildingPipeline !== null || requestedRebuild !== null || rebuildTaskInFlight
  const businessConfigDirty = mealSlotsDirty
    || videoAnalysisMaxConcurrencyDirty
    || timeOffsetCalibrationDirty
    || recognitionMenuScopeDirty
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
      setVideoAnalysisMaxConcurrency(String(res.data.data.video_analysis_max_concurrency || 2))
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
      setMenuReminderDingTalkMode(
        res.data.data.menu_reminder_dingtalk_mode === 'webhook' ? 'webhook' : 'app',
      )
      setMenuReminderDingTalkWebhook('')
      setMenuReminderDingTalkWebhookPrefix(
        String(res.data.data.menu_reminder_dingtalk_webhook_prefix || '[营养监测系统提醒]'),
      )
      setMenuReminderDingTalkConfigDirty(false)
      setNutritionAlertNotificationEnabled(
        res.data.data.nutrition_alert_notification_enabled !== false,
      )
      setWeeklyReportDayOfWeek(normalizeWeekday(res.data.data.weekly_report_day_of_week))
      setWeeklyReportTime(String(res.data.data.weekly_report_time || '08:00'))
      setScheduledNotificationsDirty(false)
    }
    if (options?.syncSelectedVariants) {
      setEmbeddingVariant((res.data.data.local_qwen3_vl_embedding_active_variant || '2B') as '2B' | '8B')
      setRerankerVariant((res.data.data.local_qwen3_vl_reranker_active_variant || '2B') as '2B' | '8B')
    }
  }

  const loadLocalModelTasks = async () => {
    const res = await analysisApi.tasks({ task_types: 'local_model_download,dish_embedding', page_size: 50 })
    const tasks = (res.data.data.items || []) as TaskLog[]
    setLocalModelTasks(tasks)
    setRequestedRebuild((current) => {
      if (!current) return current
      const latestTask = tasks.find((task) => (
        task.task_type === 'dish_embedding' && String(task.meta?.pipeline || 'qwen') === current.pipeline
      ))
      if (!latestTask || latestTask.id === current.previousTaskId) return current
      return null
    })
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
    else if (tab === 'business') loadConfig()
    else if (tab === 'notifications') {
      Promise.all([loadConfig(), loadConfigUsers()])
    }
    else if (tab === 'models') {
      loadConfig({ syncSelectedVariants: true, syncEditableFields: false })
      loadLocalModelTasks()
    }
    else if (tab === 'embedding') loadConfig({ syncEditableFields: false })
    else if (tab === 'vl') loadVlDefaults()
    else if (tab === 'sync') loadSyncStatus()
    else if (tab === 'operations') {
      loadConfig({ syncEditableFields: false })
      loadAllTasks()
    }
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
    if (tab !== 'models') return undefined
    const timer = window.setInterval(() => {
      loadConfig({ syncEditableFields: false })
      loadLocalModelTasks()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [tab])

  useEffect(() => {
    if (tab !== 'operations') return undefined
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

  const saveBusinessConfig = async () => {
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
      })
      toast.success(res.data.data.message || '业务规则已更新')
      await loadConfig()
    } finally {
      setSavingSystemConfig(false)
    }
  }

  const saveMenuReminderConfig = async () => {
    const webhookConfigured = Boolean(config.menu_reminder_dingtalk_webhook_configured)
    const normalizedWebhook = menuReminderDingTalkWebhook.trim()
    const normalizedWebhookPrefix = menuReminderDingTalkWebhookPrefix.trim()
    if (menuReminderDingTalkMode === 'webhook' && !normalizedWebhook && !webhookConfigured) {
      toast.error('请输入钉钉机器人 Webhook')
      return
    }
    if (menuReminderDingTalkMode === 'webhook' && !normalizedWebhookPrefix) {
      toast.error('请输入 Webhook 推送前缀')
      return
    }

    setSavingSystemConfig(true)
    try {
      const updates: Record<string, unknown> = {
        menu_reminder_responsible_user_ids: menuReminderResponsibleUserIds,
        menu_reminder_dingtalk_mode: menuReminderDingTalkMode,
        menu_reminder_dingtalk_webhook_prefix: normalizedWebhookPrefix,
      }
      if (normalizedWebhook) updates.menu_reminder_dingtalk_webhook = normalizedWebhook

      const res = await adminApi.updateConfig(updates)
      toast.success(res.data.data.message || '提醒配置已更新')
      await loadConfig()
    } finally {
      setSavingSystemConfig(false)
    }
  }

  const saveScheduledNotifications = async () => {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(weeklyReportTime)) {
      toast.error('请选择有效的周报生成时间')
      return
    }
    setSavingSystemConfig(true)
    try {
      const res = await adminApi.updateConfig({
        nutrition_alert_notification_enabled: nutritionAlertNotificationEnabled,
        weekly_report_day_of_week: weeklyReportDayOfWeek,
        weekly_report_time: weeklyReportTime,
      })
      toast.success(res.data.data.message || '定时通知设置已更新')
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

  const updateMenuReminderDingTalkMode = (mode: DingTalkNotificationMode) => {
    setMenuReminderDingTalkMode(mode)
    setMenuReminderDingTalkConfigDirty(true)
  }

  const updateMenuReminderDingTalkWebhook = (value: string) => {
    setMenuReminderDingTalkWebhook(value)
    setMenuReminderDingTalkConfigDirty(true)
  }

  const updateMenuReminderDingTalkWebhookPrefix = (value: string) => {
    setMenuReminderDingTalkWebhookPrefix(value)
    setMenuReminderDingTalkConfigDirty(true)
  }

  const testMenuReminderWebhook = async () => {
    if (!config.menu_reminder_dingtalk_webhook_configured || menuReminderDingTalkConfigDirty) return

    setTestingMenuReminderWebhook(true)
    try {
      const res = await adminApi.testMenuReminderWebhook()
      toast.success(res.data.data.message || '测试消息已发送')
    } finally {
      setTestingMenuReminderWebhook(false)
    }
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
      await loadLocalModelTasks()
    } finally {
      setDownloadingModelType(null)
    }
  }

  const getLatestModelTask = (modelType: ManagedModelType) =>
    localModelTasks.find((task) => task.task_type === 'local_model_download' && task.meta?.model_type === modelType) || null

  const getLatestRebuildTask = (pipeline: RetrievalPipeline) =>
    localModelTasks.find((task) => (
      task.task_type === 'dish_embedding' && String(task.meta?.pipeline || 'qwen') === pipeline
    )) || null

  const submitPipelineRebuild = async (pipeline: RetrievalPipeline) => {
    const previousTaskId = getLatestRebuildTask(pipeline)?.id ?? null
    setRebuildingPipeline(pipeline)
    try {
      const res = await dishApi.rebuildSampleEmbeddings(pipeline)
      setRequestedRebuild({ pipeline, previousTaskId })
      toast.success(res.data.data.message || '索引重建任务已提交')
      await Promise.all([loadConfig(), loadLocalModelTasks()])
    } finally {
      setRebuildingPipeline(null)
    }
  }

  const handleActivateLocalModel = async (modelType: ManagedModelType) => {
    const variant = modelType === 'embedding' ? embeddingVariant : modelType === 'reranker' ? rerankerVariant : undefined
    const activePipeline = String(config.retrieval_pipeline || 'qwen') as RetrievalPipeline
    if (
      modelType === 'embedding'
      && variant !== String(config.local_qwen3_vl_embedding_active_variant || '2B')
      && !window.confirm(
        activePipeline === 'qwen'
          ? `切换到 Embedding ${variant} 后，旧 Qwen 索引将失效并自动提交重建。重建完成前 Qwen 本地识别暂不可用，确定继续吗？`
          : `切换到 Embedding ${variant} 后，旧 Qwen 索引将失效。当前使用纯视觉检索，因此不会自动重建；切换到 Qwen 前需手动重建索引。确定继续吗？`,
      )
    ) return

    setActivatingModelType(modelType)
    try {
      const res = await adminApi.activateLocalModel(modelType, variant)
      const invalidatedPipeline = res.data.data.invalidated_pipeline as RetrievalPipeline | null
      if (res.data.data.requires_index_rebuild && invalidatedPipeline) {
        if (invalidatedPipeline === activePipeline) {
          toast.success('模型已切换，当前模式的旧索引已停用')
          await submitPipelineRebuild(invalidatedPipeline)
        } else {
          toast.success('模型已切换，未启用模式的旧索引已停用；切换前再手动重建即可')
          await Promise.all([
            loadConfig({ syncSelectedVariants: true }),
            loadLocalModelTasks(),
          ])
        }
      } else {
        toast.success(res.data.data.message || '当前模型已切换')
        await Promise.all([
          loadConfig({ syncSelectedVariants: true }),
          loadLocalModelTasks(),
        ])
      }
    } finally {
      setActivatingModelType(null)
    }
  }

  const handleSwitchPipeline = async (pipeline: RetrievalPipeline) => {
    if (!window.confirm(`确定切换到 ${pipeline === 'visual' ? '纯视觉' : 'Qwen3-VL'} 检索模式吗？`)) return
    setSwitchingPipeline(true)
    try {
      const res = await adminApi.activateRetrievalPipeline(pipeline)
      toast.success(res.data.data.message || '检索模式已切换')
      await Promise.all([loadConfig(), loadLocalModelTasks()])
    } finally {
      setSwitchingPipeline(false)
    }
  }

  const handleRebuildPipeline = async (pipeline: RetrievalPipeline) => {
    await submitPipelineRebuild(pipeline)
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
        <p className="mt-0.5 text-sm text-muted-foreground">{ADMIN_TAB_META[tab].description}</p>
      </div>

      <nav className="mb-5 overflow-x-auto rounded-xl border border-border bg-card p-2" aria-label="系统管理分区">
        <div className="flex min-w-max items-end gap-2" role="tablist" aria-label="系统管理">
          {ADMIN_TAB_GROUPS.map((group, groupIndex) => (
            <div
              key={group.label}
              className={cn('px-1', groupIndex > 0 && 'border-l border-border pl-3')}
            >
              <div className="mb-1 px-2 text-[10px] font-medium tracking-[0.14em] text-muted-foreground">
                {group.label}
              </div>
              <div className="flex gap-1">
                {group.tabs.map((item) => (
                  <button
                    key={item}
                    id={`admin-tab-${item}`}
                    type="button"
                    role="tab"
                    aria-selected={tab === item}
                    aria-controls="admin-tab-panel"
                    onClick={() => setTab(item)}
                    className={cn(
                      'min-h-9 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50',
                      tab === item
                        ? 'bg-primary text-primary-foreground shadow-sm'
                        : 'text-muted-foreground hover:bg-secondary hover:text-foreground',
                    )}
                  >
                    {ADMIN_TAB_META[item].label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </nav>

      <div id="admin-tab-panel" role="tabpanel" aria-labelledby={`admin-tab-${tab}`}>

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

      {tab === 'models' && (
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
                    当前下载源：<span className="font-mono">{String(config.hf_endpoint || 'https://hf-mirror.com')}</span>
                  </p>
                )}
              </div>
              <button onClick={() => loadConfig()} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-lg hover:bg-secondary transition-colors">
                <RefreshCw className="w-3.5 h-3.5" />刷新配置
              </button>
            </div>

            {localRecognitionModeEnabled ? (
            <>
            <div className="mb-4 rounded-xl border border-health-blue/20 bg-health-blue/5 p-3 text-xs leading-5">
              <div className="font-medium text-foreground">模型切换与索引的关系</div>
              <div className="mt-1 text-muted-foreground">
                切换 Embedding 规格会改变向量空间，系统将停用旧 Qwen 索引并自动提交重建；切换 Reranker 不需要重建。
                在 Qwen3-VL 与纯视觉模式间切换时，只要目标模式的索引已就绪，就不需要重复重建。
              </div>
            </div>
            <div className="mb-4 grid gap-3 lg:grid-cols-2" aria-label="检索模式">
              {([
                {
                  id: 'qwen' as const,
                  title: 'Qwen3-VL 检索',
                  description: '跨模态 embedding 召回，可选 Qwen reranker 精排。',
                  modelsReady: Boolean(
                    config.local_qwen3_vl_embedding_model_downloaded
                    && (!config.local_rerank_enabled || config.local_qwen3_vl_reranker_model_downloaded),
                  ),
                  indexReady: Boolean(config.local_embedding_index_ready),
                },
                {
                  id: 'visual' as const,
                  title: '纯视觉检索',
                  description: 'SigLIP2 + DINOv3 融合召回与 patch MaxSim 精排。',
                  modelsReady: Boolean(
                    config.local_siglip2_model_downloaded
                    && config.local_dinov3_model_downloaded
                    && (!config.local_rerank_enabled || config.local_qwen3_vl_reranker_model_downloaded),
                  ),
                  indexReady: Boolean(config.visual_embedding_index_ready),
                },
              ]).map((pipeline) => {
                const active = String(config.retrieval_pipeline || 'qwen') === pipeline.id
                const ready = pipeline.modelsReady && pipeline.indexReady
                const latestRebuildTask = getLatestRebuildTask(pipeline.id)
                const waitingForTask = requestedRebuild?.pipeline === pipeline.id
                  && (!latestRebuildTask || latestRebuildTask.id === requestedRebuild.previousTaskId)
                const visibleRebuildTask = waitingForTask ? null : latestRebuildTask
                const rebuildingThisPipeline = rebuildingPipeline === pipeline.id
                  || waitingForTask
                  || isTaskInFlight(visibleRebuildTask)
                return (
                  <section
                    key={pipeline.id}
                    className={cn(
                      'rounded-xl border p-4 transition-colors',
                      active ? 'border-health-blue bg-health-blue/5' : 'border-border bg-secondary/30',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="text-sm font-semibold">{pipeline.title}</h3>
                          {active ? <span className="rounded-full bg-health-blue/10 px-2 py-0.5 text-[10px] font-medium text-health-blue">当前模式</span> : null}
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">{pipeline.description}</p>
                      </div>
                      {ready ? <CheckCircle2 className="h-4 w-4 shrink-0 text-health-green" /> : <CircleAlert className="h-4 w-4 shrink-0 text-health-amber" />}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                      <span className={cn('rounded-md px-2 py-1', pipeline.modelsReady ? 'bg-health-green/10 text-health-green' : 'bg-health-amber/10 text-health-amber')}>
                        模型 {pipeline.modelsReady ? '已就绪' : '未就绪'}
                      </span>
                      <span className={cn('rounded-md px-2 py-1', pipeline.indexReady ? 'bg-health-green/10 text-health-green' : 'bg-health-amber/10 text-health-amber')}>
                        索引 {pipeline.indexReady ? '已就绪' : '未构建'}
                      </span>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => handleRebuildPipeline(pipeline.id)}
                        disabled={!pipeline.modelsReady || rebuildBusy || switchingPipeline}
                        className="min-h-11 cursor-pointer rounded-lg bg-secondary px-3 py-2 text-xs transition-colors hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-health-blue disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {rebuildingPipeline === pipeline.id ? '提交中...' : rebuildingThisPipeline ? '重建中...' : '重建索引'}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleSwitchPipeline(pipeline.id)}
                        disabled={active || !ready || switchingPipeline || rebuildBusy}
                        className="min-h-11 cursor-pointer rounded-lg bg-primary px-3 py-2 text-xs text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-health-blue disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {active ? '当前生效中' : switchingPipeline ? '切换中...' : '切换到此模式'}
                      </button>
                    </div>
                    {!pipeline.modelsReady ? (
                      <p className="mt-2 text-[11px] text-muted-foreground">先完成缺失模型下载，再重建该模式索引。</p>
                    ) : !pipeline.indexReady ? (
                      <p className={cn('mt-2 text-[11px]', active ? 'font-medium text-health-red' : 'text-muted-foreground')} role={active ? 'alert' : undefined}>
                        {active ? '当前生效模式的索引不可用，重建完成前本地识别会失败。' : '模型已就绪，完成索引重建后即可切换到该模式。'}
                      </p>
                    ) : null}
                    <IndexRebuildProgress task={visibleRebuildTask} queued={waitingForTask} />
                  </section>
                )
              })}
            </div>
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
                {
                  type: 'siglip2' as const,
                  supportsVariants: false,
                  title: 'SigLIP2 全局语义模型',
                  repoId: String(config.local_siglip2_repo_id || 'google/siglip2-so400m-patch16-512'),
                  path: String(config.local_siglip2_model_path || ''),
                  downloaded: Boolean(config.local_siglip2_model_downloaded),
                  activeVariant: '',
                  selectedVariant: '2B' as const,
                  task: getLatestModelTask('siglip2'),
                  onVariantChange: setEmbeddingVariant,
                },
                {
                  type: 'dinov3' as const,
                  supportsVariants: false,
                  title: 'DINOv3 ViT-B/16 结构模型（公开版）',
                  repoId: String(config.local_dinov3_repo_id || 'timm/vit_base_patch16_dinov3.lvd1689m'),
                  path: String(config.local_dinov3_model_path || ''),
                  downloaded: Boolean(config.local_dinov3_model_downloaded),
                  activeVariant: '',
                  selectedVariant: '2B' as const,
                  task: getLatestModelTask('dinov3'),
                  onVariantChange: setEmbeddingVariant,
                },
              ] satisfies Array<{
                type: ManagedModelType
                supportsVariants: boolean
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
                  ? (item.selectedVariant === item.activeVariant
                    ? '当前生效中'
                    : item.type === 'embedding' ? '切换并重建' : '设为当前')
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
                      {item.type === 'embedding' ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">切换规格后会自动重建 Qwen 索引。</p>
                      ) : item.type === 'reranker' ? (
                        <p className="mt-1 text-[11px] text-muted-foreground">Reranker 在线精排，切换规格无需重建索引。</p>
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
                      {item.supportsVariants ? <button
                        onClick={() => handleActivateLocalModel(item.type)}
                        disabled={downloadingModelType !== null || activatingModelType !== null || isTaskInFlight || variantIsActive || rebuildBusy}
                        className="min-h-11 cursor-pointer rounded-lg bg-secondary px-3 py-2 text-xs transition-colors hover:bg-secondary/80 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {activatingModelType === item.type ? '切换中...' : activateLabel}
                      </button> : null}
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
                        源: {String(task.meta?.hf_endpoint || config.hf_endpoint || 'https://hf-mirror.com')}
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
              下载前会默认使用 <span className="font-mono">HF_ENDPOINT=https://hf-mirror.com</span>，
              并设置 <span className="font-mono">HF_HUB_DISABLE_XET=1</span> 回退到普通 LFS 下载；修改后需重启 retrieval-api。
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
        </div>
      )}

      {tab === 'business' && (
        <div className="space-y-4">
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
                <div className="mt-4 rounded-lg border border-border bg-background px-3 py-2 text-xs leading-5 text-muted-foreground">
                  与本页餐次和视频处理参数一起保存。
                </div>
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
                    录像按通道轮询进入下载与抽帧流水线。共享 GPU 默认 2，部署时的抽帧 worker 并发数应与此保持一致。
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    当前解码后端：{String(config.video_extract_decode_backend || 'opencv')}
                    {' '}· GPU 并发上限：{Number(config.video_extract_gpu_max_concurrency || 2)}
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
                  onClick={saveBusinessConfig}
                  disabled={savingSystemConfig || !businessConfigDirty}
                  className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingSystemConfig ? '保存中...' : businessConfigDirty ? '保存业务规则' : '业务规则已保存'}
                </button>
                <div className="mt-3 text-xs text-muted-foreground">
                  默认值：
                  {' '}
                  {DEFAULT_MEAL_SLOTS.map((item) => `${item.label} ${item.start}-${item.end}`).join(' / ')}
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  抽帧最大并发默认值：2
                </div>
              </div>
            </div>
          </div>

        </div>
      )}

      {tab === 'notifications' && (
        <div className="space-y-4">
          <div className="bg-card border border-border rounded-xl p-5">
            <h2 className="text-sm font-medium mb-4 flex items-center gap-2">
              <Settings className="w-4 h-4 text-muted-foreground" />菜单与样图提醒
            </h2>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
              <div>
                <div className="text-xs text-muted-foreground mb-3">
                  系统会在每顿餐开始前 {Number(config.menu_reminder_before_minutes ?? 30)} 分钟检查当天该餐菜单和菜品样图，发现缺失后通过所选钉钉方式推送。
                </div>
                <div className="mb-4 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="钉钉推送方式">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={menuReminderDingTalkMode === 'app'}
                    onClick={() => updateMenuReminderDingTalkMode('app')}
                    className={cn(
                      'rounded-lg border border-border bg-secondary/30 p-3 text-left transition hover:bg-secondary/60',
                      menuReminderDingTalkMode === 'app' && 'border-primary/50 bg-primary/5 ring-1 ring-primary/20',
                    )}
                  >
                    <span className="block text-sm font-medium">钉钉应用</span>
                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">按 userId 定向通知一个或多个责任人</span>
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={menuReminderDingTalkMode === 'webhook'}
                    onClick={() => updateMenuReminderDingTalkMode('webhook')}
                    className={cn(
                      'rounded-lg border border-border bg-secondary/30 p-3 text-left transition hover:bg-secondary/60',
                      menuReminderDingTalkMode === 'webhook' && 'border-primary/50 bg-primary/5 ring-1 ring-primary/20',
                    )}
                  >
                    <span className="block text-sm font-medium">群机器人 Webhook</span>
                    <span className="mt-1 block text-xs leading-5 text-muted-foreground">直接推送到 Webhook 所属的钉钉群</span>
                  </button>
                </div>

                {menuReminderDingTalkMode === 'app' ? (
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
                            {!user.dingtalk_user_id ? (
                              <span className="mt-1 block text-[11px] text-health-amber">缺少钉钉 userId</span>
                            ) : null}
                          </span>
                        </label>
                      )
                    })}
                    {users.length === 0 ? (
                      <div className="rounded-lg border border-border bg-secondary/30 p-4 text-sm text-muted-foreground">
                        暂无可选通讯录用户，请先在数据同步里同步钉钉组织。
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <div className="rounded-xl border border-border bg-secondary/30 p-4">
                    <label htmlFor="menu-reminder-dingtalk-webhook" className="text-sm font-medium">
                      钉钉机器人 Webhook
                    </label>
                    <input
                      id="menu-reminder-dingtalk-webhook"
                      type="password"
                      autoComplete="new-password"
                      value={menuReminderDingTalkWebhook}
                      onChange={(event) => updateMenuReminderDingTalkWebhook(event.target.value)}
                      placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
                      className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                    <label htmlFor="menu-reminder-dingtalk-webhook-prefix" className="mt-4 block text-sm font-medium">
                      推送前缀
                    </label>
                    <input
                      id="menu-reminder-dingtalk-webhook-prefix"
                      type="text"
                      value={menuReminderDingTalkWebhookPrefix}
                      onChange={(event) => updateMenuReminderDingTalkWebhookPrefix(event.target.value)}
                      placeholder="[营养监测系统提醒]"
                      maxLength={64}
                      className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                    <div className="mt-2 text-xs leading-5 text-muted-foreground">
                      {config.menu_reminder_dingtalk_webhook_configured
                        ? '已保存 Webhook；留空保存会继续使用现有地址，输入新地址可替换。'
                        : '请粘贴钉钉群自定义机器人的完整 Webhook 地址。'}
                      {' '}前缀会添加到正式提醒和测试消息开头；如启用了关键词校验，请确保前缀包含对应关键词。
                    </div>
                  </div>
                )}
              </div>
              <div className="rounded-xl border border-border bg-secondary/40 p-4">
                <div className="text-sm font-medium">当前推送方式</div>
                <div className="mt-2 text-lg font-semibold">
                  {menuReminderDingTalkMode === 'webhook' ? '群机器人' : '钉钉应用'}
                </div>
                <div className="mt-1 text-xs leading-5 text-muted-foreground">
                  {menuReminderDingTalkMode === 'webhook'
                    ? (config.menu_reminder_dingtalk_webhook_configured || menuReminderDingTalkWebhook.trim()
                        ? 'Webhook 已配置'
                        : '等待填写 Webhook')
                    : `已选择 ${menuReminderResponsibleUserIds.length} 位责任人`}
                </div>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button
                    onClick={saveMenuReminderConfig}
                    disabled={savingSystemConfig || (!menuReminderResponsibleUserIdsDirty && !menuReminderDingTalkConfigDirty)}
                    className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                  >
                    {savingSystemConfig
                      ? '保存中...'
                      : menuReminderResponsibleUserIdsDirty || menuReminderDingTalkConfigDirty
                        ? '保存提醒配置'
                        : '提醒配置已保存'}
                  </button>
                  {menuReminderDingTalkMode === 'webhook' ? (
                    <button
                      type="button"
                      onClick={() => void testMenuReminderWebhook()}
                      disabled={
                        testingMenuReminderWebhook
                        || savingSystemConfig
                        || !config.menu_reminder_dingtalk_webhook_configured
                        || menuReminderDingTalkConfigDirty
                      }
                      className="inline-flex items-center gap-2 rounded-lg border border-border bg-background px-4 py-2 text-sm text-foreground transition hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {testingMenuReminderWebhook
                        ? <Loader2 className="h-4 w-4 animate-spin" />
                        : <Send className="h-4 w-4" />}
                      {testingMenuReminderWebhook ? '推送中...' : '测试推送'}
                    </button>
                  ) : null}
                </div>
                {menuReminderDingTalkMode === 'webhook' ? (
                  <div className="mt-2 text-xs leading-5 text-muted-foreground">
                    {menuReminderDingTalkConfigDirty
                      ? '配置有改动，请先保存后再测试。'
                      : config.menu_reminder_dingtalk_webhook_configured
                        ? '将向已保存的 Webhook 发送一条测试消息。'
                        : '保存 Webhook 后即可测试推送。'}
                  </div>
                ) : null}
                <div className="mt-3 text-xs text-muted-foreground">
                  {menuReminderDingTalkMode === 'webhook'
                    ? 'Webhook 模式无需配置钉钉应用，也无需选择责任人。'
                    : '未选择责任人时，后端会默认推送给活跃的食堂管理员。'}
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-5">
            <h2 className="mb-4 flex items-center gap-2 text-sm font-medium">
              <CalendarClock className="h-4 w-4 text-muted-foreground" />营养预警与个人周报
            </h2>
            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-border bg-secondary/30 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <BellRing className="h-4 w-4 text-health-amber" />营养预警通知
                    </div>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      开启后，系统每天 08:00 检查学生营养预警，并向关联家长发送钉钉通知。关闭不会影响首页和报告中的预警计算。
                    </p>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-label="营养预警通知"
                    aria-checked={nutritionAlertNotificationEnabled}
                    onClick={() => {
                      setNutritionAlertNotificationEnabled((current) => !current)
                      setScheduledNotificationsDirty(true)
                    }}
                    className={cn(
                      'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/30',
                      nutritionAlertNotificationEnabled
                        ? 'border-primary bg-primary'
                        : 'border-border bg-muted',
                    )}
                  >
                    <span
                      className={cn(
                        'block h-5 w-5 rounded-full bg-white shadow-sm transition-transform motion-reduce:transition-none',
                        nutritionAlertNotificationEnabled ? 'translate-x-5' : 'translate-x-0.5',
                      )}
                    />
                  </button>
                </div>
                <div className={cn(
                  'mt-4 inline-flex rounded-full px-2.5 py-1 text-xs font-medium',
                  nutritionAlertNotificationEnabled
                    ? 'bg-health-green/10 text-health-green'
                    : 'bg-secondary text-muted-foreground',
                )}
                >
                  {nutritionAlertNotificationEnabled ? '通知已开启' : '通知已关闭'}
                </div>
              </div>

              <div className="rounded-xl border border-border bg-secondary/30 p-4">
                <div className="text-sm font-medium">个人周报生成时间</div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  到达设定时间后生成本周一至周日的个人周报；修改保存后立即生效，无需重启后台服务。
                </p>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <label className="text-xs text-muted-foreground">
                    星期
                    <select
                      value={weeklyReportDayOfWeek}
                      onChange={(event) => {
                        setWeeklyReportDayOfWeek(event.target.value as WeekdayKey)
                        setScheduledNotificationsDirty(true)
                      }}
                      className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                    >
                      {WEEKDAY_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-muted-foreground">
                    时间
                    <input
                      type="time"
                      value={weeklyReportTime}
                      onChange={(event) => {
                        setWeeklyReportTime(event.target.value)
                        setScheduledNotificationsDirty(true)
                      }}
                      className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
                    />
                  </label>
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void saveScheduledNotifications()}
              disabled={savingSystemConfig || !scheduledNotificationsDirty}
              className="mt-4 inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {savingSystemConfig
                ? '保存中...'
                : scheduledNotificationsDirty
                  ? '保存定时通知设置'
                  : '定时通知设置已保存'}
            </button>
          </div>

        </div>
      )}

      {tab === 'operations' && (
        <div className="space-y-4">
          <TasksAdminTab
            tasks={allTasks}
            loading={tasksLoading}
            onRefresh={loadAllTasks}
          />

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
      </div>
    </div>
  )
}
