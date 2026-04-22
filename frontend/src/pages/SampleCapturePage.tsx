import { useEffect, useRef, useState } from 'react'
import {
  Aperture,
  CalendarDays,
  Camera,
  CheckCircle2,
  Clock3,
  ImagePlus,
  Images,
  LoaderCircle,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Upload,
} from 'lucide-react'
import toast from 'react-hot-toast'

import { dishApi, menuApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, fmtDate } from '@/lib/utils'
import type { DailyMenu, Dish, DishSampleImage, EmbeddingStatus, MealDishIds, MealSlotKey } from '@/types'

const MAX_SAMPLE_IMAGES = 12

const MEAL_SLOTS: Array<{
  key: MealSlotKey
  label: string
  caption: string
}> = [
  { key: 'breakfast', label: '早餐', caption: '05:00-09:30' },
  { key: 'lunch', label: '午餐', caption: '10:30-13:30' },
  { key: 'dinner', label: '晚餐', caption: '17:00-19:30' },
  { key: 'late_night', label: '宵夜', caption: '21:00-23:59' },
]

const EMBEDDING_STATUS_META: Record<EmbeddingStatus, { label: string; className: string }> = {
  pending: { label: '待生成', className: 'bg-amber-100 text-amber-700' },
  processing: { label: '生成中', className: 'bg-sky-100 text-sky-700' },
  ready: { label: '已就绪', className: 'bg-emerald-100 text-emerald-700' },
  failed: { label: '失败', className: 'bg-rose-100 text-rose-700' },
}

interface PendingCapture {
  id: string
  file: File
  previewUrl: string
}

const createEmptyMealDishIds = (): MealDishIds => ({
  breakfast: [],
  lunch: [],
  dinner: [],
  late_night: [],
})

const normalizeMealDishIds = (
  value?: Partial<Record<MealSlotKey, number[]>> | null,
): MealDishIds => ({
  breakfast: Array.isArray(value?.breakfast) ? value?.breakfast || [] : [],
  lunch: Array.isArray(value?.lunch) ? value?.lunch || [] : [],
  dinner: Array.isArray(value?.dinner) ? value?.dinner || [] : [],
  late_night: Array.isArray(value?.late_night) ? value?.late_night || [] : [],
})

const toLocalDateValue = (date = new Date()) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const resolveDefaultMealSlot = (date = new Date()): MealSlotKey => {
  const minutes = date.getHours() * 60 + date.getMinutes()
  if (minutes >= 5 * 60 && minutes <= 9 * 60 + 30) return 'breakfast'
  if (minutes >= 10 * 60 + 30 && minutes <= 13 * 60 + 30) return 'lunch'
  if (minutes >= 17 * 60 && minutes <= 19 * 60 + 30) return 'dinner'
  if (minutes >= 21 * 60) return 'late_night'
  return 'lunch'
}

const dishSampleStats = (dish?: Dish | null) => {
  const stats = { ready: 0, pending: 0, processing: 0, failed: 0 }
  ;(dish?.sample_images || []).forEach((image) => {
    const status = image.embedding_status
    if (status in stats) stats[status] += 1
  })
  return stats
}

const getDishPreviewUrl = (dish: Dish) =>
  dish.sample_images?.find(image => image.is_cover)?.image_url ||
  dish.sample_images?.[0]?.image_url ||
  dish.image_url ||
  ''

const buildFileSignature = (file: File) => [file.name, file.size, file.lastModified].join(':')

export default function SampleCapturePage() {
  const { hasRole } = useAuth()
  const [selectedDate, setSelectedDate] = useState(toLocalDateValue())
  const [selectedMeal, setSelectedMeal] = useState<MealSlotKey>(resolveDefaultMealSlot())
  const [menu, setMenu] = useState<DailyMenu | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedDishId, setSelectedDishId] = useState<number | null>(null)
  const [pendingCaptures, setPendingCaptures] = useState<PendingCapture[]>([])
  const [uploading, setUploading] = useState(false)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const galleryInputRef = useRef<HTMLInputElement>(null)
  const pendingCapturesRef = useRef<PendingCapture[]>([])

  useEffect(() => {
    pendingCapturesRef.current = pendingCaptures
  }, [pendingCaptures])

  useEffect(() => () => {
    pendingCapturesRef.current.forEach(item => URL.revokeObjectURL(item.previewUrl))
  }, [])

  const revokePendingCapture = (item: PendingCapture) => {
    URL.revokeObjectURL(item.previewUrl)
  }

  const clearPendingCaptures = () => {
    setPendingCaptures((prev) => {
      prev.forEach(revokePendingCapture)
      return []
    })
  }

  const loadMenu = async (silent = false) => {
    if (silent) setRefreshing(true)
    else setLoading(true)

    try {
      const res = await menuApi.get(selectedDate)
      const nextMenu = res.data.data as DailyMenu
      setMenu({
        ...nextMenu,
        meal_dish_ids: normalizeMealDishIds(nextMenu.meal_dish_ids),
      })
    } catch {
      setMenu(null)
    } finally {
      if (silent) setRefreshing(false)
      else setLoading(false)
    }
  }

  useEffect(() => {
    void loadMenu()
  }, [selectedDate])

  const allDishes = menu?.dishes || []
  const normalizedMealDishIds = normalizeMealDishIds(menu?.meal_dish_ids)
  const dishById = new Map(allDishes.map(dish => [dish.id, dish]))
  const aggregatedDishIds: number[] = []
  const aggregatedDishSeen = new Set<number>()
  ;(Object.keys(createEmptyMealDishIds()) as MealSlotKey[]).forEach((mealKey) => {
    normalizedMealDishIds[mealKey].forEach((dishId) => {
      if (aggregatedDishSeen.has(dishId)) return
      aggregatedDishSeen.add(dishId)
      aggregatedDishIds.push(dishId)
    })
  })

  const currentMealDishIds = normalizedMealDishIds[selectedMeal]
  const candidateDishes = currentMealDishIds.length > 0
    ? currentMealDishIds.map(dishId => dishById.get(dishId)).filter((dish): dish is Dish => Boolean(dish))
    : aggregatedDishIds.length > 0
      ? aggregatedDishIds.map(dishId => dishById.get(dishId)).filter((dish): dish is Dish => Boolean(dish))
      : allDishes

  useEffect(() => {
    if (!candidateDishes.length) {
      setSelectedDishId(null)
      return
    }
    if (!candidateDishes.some(dish => dish.id === selectedDishId)) {
      setSelectedDishId(candidateDishes[0].id)
    }
  }, [selectedDate, selectedMeal, menu])

  const filteredDishes = candidateDishes.filter((dish) => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) return true
    return dish.name.toLowerCase().includes(keyword) || dish.category.toLowerCase().includes(keyword)
  })

  const selectedDish = selectedDishId ? dishById.get(selectedDishId) || null : null
  const selectedMealMeta = MEAL_SLOTS.find(item => item.key === selectedMeal) || MEAL_SLOTS[0]
  const selectedDishStats = dishSampleStats(selectedDish)
  const remainingSlots = Math.max(MAX_SAMPLE_IMAGES - Number(selectedDish?.sample_image_count || 0) - pendingCaptures.length, 0)
  const mealSourceLabel = currentMealDishIds.length > 0
    ? `当前只显示 ${selectedMealMeta.label} 菜单`
    : aggregatedDishIds.length > 0
      ? `${selectedMealMeta.label} 未单独配置，已回退到当日菜单`
      : '当天未配置菜单，已显示全部启用菜品'

  const handleRefresh = async () => {
    if (pendingCaptures.length > 0) {
      toast.error('请先上传或清空当前待上传队列')
      return
    }
    await loadMenu(true)
  }

  const handleDateChange = (value: string) => {
    if (!value || value === selectedDate) return
    if (pendingCaptures.length > 0) {
      toast.error('请先上传或清空当前待上传队列')
      return
    }
    setSelectedDate(value)
  }

  const handleMealChange = (meal: MealSlotKey) => {
    if (meal === selectedMeal) return
    if (pendingCaptures.length > 0) {
      toast.error('请先上传或清空当前待上传队列')
      return
    }
    setSelectedMeal(meal)
  }

  const handleSelectDish = (dishId: number) => {
    if (dishId === selectedDishId) return
    if (pendingCaptures.length > 0) {
      toast.error('请先上传或清空当前待上传队列')
      return
    }
    setSelectedDishId(dishId)
  }

  const appendFiles = (files: File[]) => {
    if (!selectedDish) {
      toast.error('请先选择菜品')
      return
    }

    const remaining = MAX_SAMPLE_IMAGES - Number(selectedDish.sample_image_count || 0) - pendingCaptures.length
    if (remaining <= 0) {
      toast.error(`该菜品样图已达到 ${MAX_SAMPLE_IMAGES} 张上限`)
      return
    }

    const existingSignatures = new Set(pendingCaptures.map(item => buildFileSignature(item.file)))
    const accepted = files
      .filter(file => file.type.startsWith('image/'))
      .filter((file, index, current) => current.findIndex(item => buildFileSignature(item) === buildFileSignature(file)) === index)
      .filter(file => !existingSignatures.has(buildFileSignature(file)))
      .slice(0, remaining)

    if (!accepted.length) {
      toast.error('没有可加入的图片，请检查格式或重复文件')
      return
    }

    const nextItems = accepted.map((file) => ({
      id: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      previewUrl: URL.createObjectURL(file),
    }))

    setPendingCaptures(prev => [...prev, ...nextItems])

    if (accepted.length < files.length) {
      toast.success(`已加入 ${accepted.length} 张样图，其余图片因重复或超出上限被忽略`)
      return
    }

    toast.success(`已加入 ${accepted.length} 张待上传样图`)
  }

  const handleInputFiles = (files: FileList | null, source: 'camera' | 'gallery') => {
    if (!files?.length) return
    appendFiles(Array.from(files))
    if (source === 'camera' && cameraInputRef.current) cameraInputRef.current.value = ''
    if (source === 'gallery' && galleryInputRef.current) galleryInputRef.current.value = ''
  }

  const removePendingCapture = (id: string) => {
    setPendingCaptures((prev) => {
      const target = prev.find(item => item.id === id)
      if (target) revokePendingCapture(target)
      return prev.filter(item => item.id !== id)
    })
  }

  const handleUpload = async () => {
    if (!selectedDish) {
      toast.error('请先选择菜品')
      return
    }
    if (!pendingCaptures.length) {
      toast.error('请先拍摄或选择样图')
      return
    }

    setUploading(true)
    try {
      await dishApi.uploadImages(selectedDish.id, pendingCaptures.map(item => item.file))
      toast.success(`已上传 ${pendingCaptures.length} 张样图，系统会自动尝试进入 embedding 队列`)
      clearPendingCaptures()
      await loadMenu(true)
    } finally {
      setUploading(false)
    }
  }

  const renderExistingSample = (image: DishSampleImage) => {
    const statusMeta = EMBEDDING_STATUS_META[image.embedding_status]
    return (
      <article
        key={image.id}
        className="overflow-hidden rounded-[20px] border border-border bg-white/90 shadow-[0_14px_30px_rgba(15,23,42,0.05)]"
      >
        <div className="relative aspect-[1.02] overflow-hidden bg-secondary">
          {image.image_url ? (
            <img
              src={image.image_url}
              alt={image.original_filename || `样图-${image.id}`}
              className="h-full w-full object-cover"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">无预览</div>
          )}
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/65 via-black/10 to-transparent p-3 pt-8">
            <span className={cn('rounded-full px-2 py-1 text-[10px] font-medium', statusMeta.className)}>
              {statusMeta.label}
            </span>
          </div>
        </div>
        <div className="space-y-1 p-3">
          <div className="truncate text-xs font-medium text-foreground">{image.original_filename || `样图 ${image.id}`}</div>
          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            <span>排序 #{image.sort_order}</span>
            {image.embedding_updated_at && <span>{fmtDate(image.embedding_updated_at)}</span>}
          </div>
          {image.error_message && (
            <p className="line-clamp-2 text-[11px] text-rose-600">{image.error_message}</p>
          )}
        </div>
      </article>
    )
  }

  if (!hasRole('admin', 'canteen_manager')) {
    return (
      <div className="px-4 py-4 sm:px-6 sm:py-6">
        <div className="mx-auto flex min-h-[60vh] max-w-3xl items-center justify-center rounded-[30px] border border-dashed border-border bg-card/95 px-6 text-center shadow-[0_16px_44px_rgba(15,23,42,0.05)]">
          <div>
            <Camera className="mx-auto h-9 w-9 text-muted-foreground" />
            <div className="mt-3 text-lg font-semibold text-foreground">当前角色无权使用样图采集</div>
            <p className="mt-1 text-sm text-muted-foreground">
              该页面仅面向系统管理员和食堂管理员，用于按菜单拍摄菜品样图并触发 embedding 流程。
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-4 sm:px-6 sm:py-6">
      <div className="mx-auto max-w-6xl space-y-4 sm:space-y-6">
        <section className="relative overflow-hidden rounded-[30px] border border-border bg-[linear-gradient(135deg,rgba(244,252,247,0.96),rgba(255,255,255,0.94)_58%,rgba(232,247,239,0.96))] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.08)] sm:p-6">
          <div className="pointer-events-none absolute -right-16 top-0 h-44 w-44 rounded-full bg-emerald-300/25 blur-3xl" />
          <div className="pointer-events-none absolute bottom-0 left-0 h-36 w-36 rounded-full bg-amber-200/25 blur-3xl" />
          <div className="relative grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)] lg:gap-6">
            <div>
              <div className="inline-flex items-center gap-2 rounded-full border border-foreground/10 bg-white/80 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground">
                <Sparkles className="h-3.5 w-3.5 text-primary" />
                Mobile Sample Flow
              </div>
              <h1 className="mt-3 text-2xl font-semibold tracking-tight text-foreground sm:text-[2rem]">
                移动样图采集
              </h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground sm:text-[15px]">
                按菜单快速选择菜品，直接调用手机相机拍样图上传。样图入库后会自动尝试触发本地 embedding 重建，方便后续识别召回。
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-3 py-1.5 text-xs text-foreground shadow-sm">
                  <CalendarDays className="h-3.5 w-3.5 text-primary" />
                  {selectedDate}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-3 py-1.5 text-xs text-foreground shadow-sm">
                  <Clock3 className="h-3.5 w-3.5 text-primary" />
                  当前餐次 {selectedMealMeta.label}
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/90 px-3 py-1.5 text-xs text-foreground shadow-sm">
                  <Upload className="h-3.5 w-3.5 text-primary" />
                  单菜最多 {MAX_SAMPLE_IMAGES} 张
                </span>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-3">
              {[
                { label: '候选菜品', value: candidateDishes.length, icon: Aperture },
                { label: '待上传', value: pendingCaptures.length, icon: Camera },
                { label: '已选菜品样图', value: Number(selectedDish?.sample_image_count || 0), icon: Images },
              ].map(({ label, value, icon: Icon }) => (
                <div
                  key={label}
                  className="rounded-[22px] border border-white/80 bg-white/85 p-4 shadow-[0_12px_30px_rgba(15,23,42,0.06)]"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{label}</span>
                    <Icon className="h-4 w-4 text-primary" />
                  </div>
                  <div className="mt-4 font-mono text-3xl font-light tabular-nums text-foreground">{value}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.08fr)_minmax(320px,0.92fr)] lg:gap-6">
          <section className="rounded-[30px] border border-border bg-card/95 p-4 shadow-[0_16px_44px_rgba(15,23,42,0.05)] sm:p-5">
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-foreground">按菜单选菜</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{mealSourceLabel}</p>
                </div>
                <button
                  type="button"
                  onClick={handleRefresh}
                  disabled={refreshing || loading}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl border border-border bg-white px-4 py-2 text-sm text-foreground transition-colors hover:bg-secondary disabled:opacity-50"
                >
                  <RefreshCw className={cn('h-4 w-4', (refreshing || loading) && 'animate-spin')} />
                  刷新菜单
                </button>
              </div>

              <div className="grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
                <label className="space-y-1.5">
                  <span className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">日期</span>
                  <input
                    type="date"
                    value={selectedDate}
                    onChange={(event) => handleDateChange(event.target.value)}
                    className="w-full rounded-2xl border border-border bg-background px-4 py-3 text-sm text-foreground outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium uppercase tracking-[0.16em] text-muted-foreground">搜索</span>
                  <div className="flex items-center gap-2 rounded-2xl border border-border bg-background px-4 py-3">
                    <Search className="h-4 w-4 text-muted-foreground" />
                    <input
                      value={search}
                      onChange={event => setSearch(event.target.value)}
                      placeholder="搜索菜名或分类"
                      className="w-full border-none bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
                    />
                  </div>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {MEAL_SLOTS.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => handleMealChange(item.key)}
                    className={cn(
                      'rounded-[22px] border px-3 py-3 text-left transition-all',
                      selectedMeal === item.key
                        ? 'border-primary/40 bg-[linear-gradient(135deg,rgba(17,163,109,0.12),rgba(17,163,109,0.03))] shadow-[0_10px_24px_rgba(17,163,109,0.12)]'
                        : 'border-border bg-background hover:border-primary/20 hover:bg-secondary/70',
                    )}
                  >
                    <div className="text-sm font-semibold text-foreground">{item.label}</div>
                    <div className="mt-1 text-[11px] text-muted-foreground">{item.caption}</div>
                  </button>
                ))}
              </div>

              {loading ? (
                <div className="flex min-h-[340px] items-center justify-center rounded-[24px] border border-dashed border-border bg-secondary/40">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                    加载菜单中...
                  </div>
                </div>
              ) : filteredDishes.length > 0 ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {filteredDishes.map((dish) => {
                    const previewUrl = getDishPreviewUrl(dish)
                    const stats = dishSampleStats(dish)
                    const isActive = dish.id === selectedDishId
                    return (
                      <button
                        key={dish.id}
                        type="button"
                        onClick={() => handleSelectDish(dish.id)}
                        className={cn(
                          'overflow-hidden rounded-[24px] border text-left transition-all',
                          isActive
                            ? 'border-primary/40 bg-[linear-gradient(135deg,rgba(17,163,109,0.12),rgba(255,255,255,0.96)_60%)] shadow-[0_16px_36px_rgba(17,163,109,0.14)]'
                            : 'border-border bg-background hover:-translate-y-0.5 hover:border-primary/20 hover:shadow-[0_14px_28px_rgba(15,23,42,0.06)]',
                        )}
                      >
                        <div className="flex gap-3 p-3">
                          <div className="relative h-24 w-24 overflow-hidden rounded-[18px] bg-[linear-gradient(135deg,rgba(17,163,109,0.16),rgba(255,255,255,0.92))]">
                            {previewUrl ? (
                              <img src={previewUrl} alt={dish.name} className="h-full w-full object-cover" />
                            ) : (
                              <div className="flex h-full w-full items-center justify-center text-2xl font-semibold text-primary/70">
                                {dish.name.slice(0, 1)}
                              </div>
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <div className="truncate text-base font-semibold text-foreground">{dish.name}</div>
                                <div className="mt-1 flex flex-wrap gap-2">
                                  <span className="rounded-full bg-secondary px-2.5 py-1 text-[11px] text-muted-foreground">
                                    {dish.category}
                                  </span>
                                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] text-muted-foreground shadow-sm">
                                    {Number(dish.sample_image_count || 0)} 张样图
                                  </span>
                                </div>
                              </div>
                              {isActive && <CheckCircle2 className="h-5 w-5 flex-shrink-0 text-primary" />}
                            </div>
                            <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-muted-foreground">
                              <div className="rounded-2xl bg-white/80 px-2.5 py-2">
                                已就绪 <span className="font-mono text-foreground">{stats.ready}</span>
                              </div>
                              <div className="rounded-2xl bg-white/80 px-2.5 py-2">
                                待处理 <span className="font-mono text-foreground">{stats.pending + stats.processing}</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="flex min-h-[300px] flex-col items-center justify-center rounded-[24px] border border-dashed border-border bg-secondary/30 px-6 text-center">
                  <Aperture className="h-8 w-8 text-muted-foreground" />
                  <div className="mt-3 text-base font-semibold text-foreground">当前没有可选菜品</div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    先确认当天菜单是否已经配置，或者换一个餐次 / 搜索词再试。
                  </p>
                </div>
              )}
            </div>
          </section>

          <section className="self-start rounded-[30px] border border-border bg-card/95 p-4 shadow-[0_16px_44px_rgba(15,23,42,0.05)] sm:p-5 lg:sticky lg:top-4">
            {selectedDish ? (
              <div className="space-y-5">
                <div className="overflow-hidden rounded-[26px] border border-border bg-[linear-gradient(145deg,rgba(17,163,109,0.12),rgba(255,255,255,0.96)_58%,rgba(248,250,249,0.96))] p-4">
                  <div className="flex items-start gap-4">
                    <div className="relative h-24 w-24 overflow-hidden rounded-[20px] bg-white/80 shadow-sm">
                      {getDishPreviewUrl(selectedDish) ? (
                        <img
                          src={getDishPreviewUrl(selectedDish)}
                          alt={selectedDish.name}
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-3xl font-semibold text-primary/70">
                          {selectedDish.name.slice(0, 1)}
                        </div>
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                        当前采样目标
                      </div>
                      <h2 className="mt-1 truncate text-xl font-semibold text-foreground">{selectedDish.name}</h2>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <span className="rounded-full bg-white/90 px-3 py-1 text-xs text-foreground shadow-sm">
                          {selectedDish.category}
                        </span>
                        <span className="rounded-full bg-white/90 px-3 py-1 text-xs text-foreground shadow-sm">
                          已存 {Number(selectedDish.sample_image_count || 0)} / {MAX_SAMPLE_IMAGES}
                        </span>
                      </div>
                      {selectedDish.description && (
                        <p className="mt-3 line-clamp-3 text-sm text-muted-foreground">
                          {selectedDish.description}
                        </p>
                      )}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-4 gap-2">
                  {[
                    { label: '已就绪', value: selectedDishStats.ready },
                    { label: '待生成', value: selectedDishStats.pending },
                    { label: '生成中', value: selectedDishStats.processing },
                    { label: '失败', value: selectedDishStats.failed },
                  ].map((item) => (
                    <div key={item.label} className="rounded-[20px] border border-border bg-background px-3 py-3 text-center">
                      <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">{item.label}</div>
                      <div className="mt-2 font-mono text-xl text-foreground">{item.value}</div>
                    </div>
                  ))}
                </div>

                <div className="rounded-[26px] border border-border bg-background p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-base font-semibold text-foreground">拍摄并上传</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        手机端可直接拉起后置相机；也支持从相册补传。若当前使用 `local_embedding` 模式，上传后会自动尝试重建索引。
                      </p>
                    </div>
                    <span className="rounded-full bg-secondary px-3 py-1 text-[11px] text-muted-foreground">
                      剩余 {remainingSlots} 张
                    </span>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-3">
                    <label className={cn(
                      'group flex cursor-pointer flex-col items-center justify-center rounded-[22px] border px-4 py-5 text-center transition-colors',
                      remainingSlots > 0 ? 'border-border bg-white hover:bg-secondary/70' : 'cursor-not-allowed border-border bg-secondary/40 opacity-60',
                    )}>
                      <Camera className="h-5 w-5 text-primary" />
                      <span className="mt-2 text-sm font-medium text-foreground">拍摄样图</span>
                      <span className="mt-1 text-[11px] text-muted-foreground">优先使用手机相机</span>
                      <input
                        ref={cameraInputRef}
                        type="file"
                        accept="image/*"
                        capture="environment"
                        onChange={(event) => handleInputFiles(event.target.files, 'camera')}
                        disabled={remainingSlots <= 0}
                        className="hidden"
                      />
                    </label>

                    <label className={cn(
                      'group flex cursor-pointer flex-col items-center justify-center rounded-[22px] border px-4 py-5 text-center transition-colors',
                      remainingSlots > 0 ? 'border-border bg-white hover:bg-secondary/70' : 'cursor-not-allowed border-border bg-secondary/40 opacity-60',
                    )}>
                      <ImagePlus className="h-5 w-5 text-primary" />
                      <span className="mt-2 text-sm font-medium text-foreground">相册补传</span>
                      <span className="mt-1 text-[11px] text-muted-foreground">支持多选图片</span>
                      <input
                        ref={galleryInputRef}
                        type="file"
                        accept="image/*"
                        multiple
                        onChange={(event) => handleInputFiles(event.target.files, 'gallery')}
                        disabled={remainingSlots <= 0}
                        className="hidden"
                      />
                    </label>
                  </div>

                  {pendingCaptures.length > 0 ? (
                    <div className="mt-5 space-y-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-sm font-semibold text-foreground">待上传队列</div>
                          <div className="mt-0.5 text-[11px] text-muted-foreground">
                            共 {pendingCaptures.length} 张，将上传到 {selectedDish.name}
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={clearPendingCaptures}
                          className="rounded-full border border-border px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-secondary"
                        >
                          清空
                        </button>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        {pendingCaptures.map((item) => (
                          <article
                            key={item.id}
                            className="overflow-hidden rounded-[20px] border border-border bg-white shadow-[0_12px_24px_rgba(15,23,42,0.05)]"
                          >
                            <div className="relative aspect-[1.02] overflow-hidden bg-secondary">
                              <img src={item.previewUrl} alt={item.file.name} className="h-full w-full object-cover" />
                              <button
                                type="button"
                                onClick={() => removePendingCapture(item.id)}
                                className="absolute right-2 top-2 rounded-full bg-black/60 p-1.5 text-white transition-colors hover:bg-black/75"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </div>
                            <div className="space-y-1 p-3">
                              <div className="truncate text-xs font-medium text-foreground">{item.file.name}</div>
                              <div className="text-[11px] text-muted-foreground">
                                {(item.file.size / 1024 / 1024).toFixed(2)} MB
                              </div>
                            </div>
                          </article>
                        ))}
                      </div>

                      <button
                        type="button"
                        onClick={handleUpload}
                        disabled={uploading}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-[22px] bg-primary px-4 py-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                      >
                        {uploading ? (
                          <>
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                            上传中...
                          </>
                        ) : (
                          <>
                            <Upload className="h-4 w-4" />
                            上传到 {selectedDish.name}
                          </>
                        )}
                      </button>
                    </div>
                  ) : (
                    <div className="mt-5 rounded-[22px] border border-dashed border-border bg-secondary/30 px-4 py-5 text-sm text-muted-foreground">
                      先拍照或从相册选图，确认无误后再上传。切换日期、餐次或菜品前请保持待上传队列为空，避免误传到其他菜品。
                    </div>
                  )}
                </div>

                <div>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-base font-semibold text-foreground">现有样图</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        这里展示当前菜品已入库的样图与 embedding 状态。
                      </p>
                    </div>
                    <span className="rounded-full bg-secondary px-3 py-1 text-[11px] text-muted-foreground">
                      {Number(selectedDish.sample_image_count || 0)} 张
                    </span>
                  </div>

                  {selectedDish.sample_images?.length ? (
                    <div className="grid grid-cols-2 gap-3">
                      {selectedDish.sample_images.slice(0, 6).map(renderExistingSample)}
                    </div>
                  ) : (
                    <div className="rounded-[22px] border border-dashed border-border bg-secondary/30 px-4 py-6 text-sm text-muted-foreground">
                      当前菜品还没有样图，拍摄后上传即可开始积累 embedding 样本。
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex min-h-[420px] flex-col items-center justify-center rounded-[24px] border border-dashed border-border bg-secondary/30 px-6 text-center">
                <Camera className="h-9 w-9 text-muted-foreground" />
                <div className="mt-3 text-lg font-semibold text-foreground">先选择一个菜品</div>
                <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                  左侧会根据日期和餐次过滤菜单。选中菜品后，这里会显示拍照上传入口和当前样图状态。
                </p>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
