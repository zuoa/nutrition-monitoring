import { useEffect, useRef, useState } from 'react'
import * as Tabs from '@radix-ui/react-tabs'
import { Plus, Search, Edit2, Trash2, ChevronLeft, ChevronRight, X, Sparkles, Download, Upload, ImagePlus, Wand2, RefreshCw, Images, Clock3, CheckCircle2, AlertTriangle, Inbox } from 'lucide-react'
import { adminApi, dishApi } from '@/api/client'
import { fmtDate, cn, isLocalRecognitionMode, STRUCTURED_DESCRIPTION_FIELDS, STRUCTURED_DESCRIPTION_SECTION, buildStructuredDescription, emptyStructuredDescription, type StructuredDescriptionKey } from '@/lib/utils'
import type { Dish, DishCategory, DishSampleImage } from '@/types'
import toast from 'react-hot-toast'

const CATEGORIES: DishCategory[] = ['主食', '荤菜', '素菜', '汤', '其他']
const CATEGORY_COLORS: Record<string, string> = {
  '主食': 'bg-amber-100 text-amber-700',
  '荤菜': 'bg-red-100 text-red-700',
  '素菜': 'bg-green-100 text-green-700',
  '汤': 'bg-blue-100 text-blue-700',
  '其他': 'bg-gray-100 text-gray-600',
}
const EMBEDDING_STATUS_LABELS: Record<string, string> = {
  pending: '待生成',
  processing: '生成中',
  ready: '已就绪',
  failed: '失败',
}
const EMBEDDING_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-700',
  processing: 'bg-blue-100 text-blue-700',
  ready: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
}
const MAX_SAMPLE_IMAGES = 12

interface DishFormData {
  name: string
  description: string
  ingredients: string
  price: string
  category: string
  weight: string
  calories: string
  protein: string
  fat: string
  carbohydrate: string
  sodium: string
  fiber: string
}

type StructuredDescriptionForm = Record<StructuredDescriptionKey, string>
type StructuredDescriptionPayload = Partial<Record<StructuredDescriptionKey | 'main_ingredients' | 'confusable_with', string>>

interface PendingSampleImage {
  id: string
  file: File
  previewUrl: string
}

interface GeneratedDescriptionItem {
  position: string
  description: string
  structuredDescription: StructuredDescriptionForm
  notes: string
}

const EMPTY_FORM: DishFormData = {
  name: '', description: '', ingredients: '', price: '', category: '荤菜', weight: '100',
  calories: '', protein: '', fat: '', carbohydrate: '', sodium: '', fiber: '',
}
const EMPTY_STRUCTURED_DESCRIPTION: StructuredDescriptionForm = emptyStructuredDescription()

const normalizeStructuredDescriptionPayload = (raw: unknown): StructuredDescriptionForm => {
  const details = { ...EMPTY_STRUCTURED_DESCRIPTION }
  if (!raw || typeof raw !== 'object') return details

  const source = raw as StructuredDescriptionPayload
  details.mainIngredients = String(source.mainIngredients ?? source.main_ingredients ?? '').trim()
  details.colors = String(source.colors ?? '').trim()
  details.cuts = String(source.cuts ?? '').trim()
  details.texture = String(source.texture ?? '').trim()
  details.sauce = String(source.sauce ?? '').trim()
  details.garnishes = String(source.garnishes ?? '').trim()
  details.confusableWith = String(source.confusableWith ?? source.confusable_with ?? '').trim()
  return details
}

const hasStructuredDescriptionValues = (details: StructuredDescriptionForm) =>
  Object.values(details).some(value => value.trim())

const normalizeGeneratedDescriptionItem = (raw: unknown): GeneratedDescriptionItem | null => {
  if (!raw || typeof raw !== 'object') return null

  const source = raw as {
    position?: unknown
    description?: unknown
    notes?: unknown
    structured_description?: unknown
  }

  const description = String(source.description ?? '').trim()
  const notes = String(source.notes ?? '').trim()
  const structuredDescription = normalizeStructuredDescriptionPayload(source.structured_description)
  if (!description && !notes && !hasStructuredDescriptionValues(structuredDescription)) return null

  return {
    position: String(source.position ?? '').trim(),
    description,
    notes,
    structuredDescription,
  }
}

const normalizeGeneratedDescriptionItems = (raw: unknown): GeneratedDescriptionItem[] => {
  if (!raw || typeof raw !== 'object') return []

  const source = raw as {
    descriptions?: unknown[]
    position?: unknown
    description?: unknown
    notes?: unknown
    structured_description?: unknown
  }

  const items = Array.isArray(source.descriptions)
    ? source.descriptions
      .map(normalizeGeneratedDescriptionItem)
      .filter((item): item is GeneratedDescriptionItem => item !== null)
    : []

  if (items.length) return items

  const fallback = normalizeGeneratedDescriptionItem(source)
  return fallback ? [fallback] : []
}

const parseStructuredDescription = (raw: string): { summary: string; details: StructuredDescriptionForm } => {
  const details = { ...EMPTY_STRUCTURED_DESCRIPTION }
  const normalized = String(raw || '').replace(/\r\n/g, '\n').trim()
  if (!normalized) return { summary: '', details }

  const summaryLines: string[] = []
  let inStructuredSection = false
  for (const rawLine of normalized.split('\n')) {
    const line = rawLine.trim()
    if (!line) {
      if (!inStructuredSection && summaryLines[summaryLines.length - 1] !== '') {
        summaryLines.push('')
      }
      continue
    }
    if (line === STRUCTURED_DESCRIPTION_SECTION) {
      inStructuredSection = true
      continue
    }
    if (!inStructuredSection) {
      summaryLines.push(line)
      continue
    }

    let matched = false
    for (const field of STRUCTURED_DESCRIPTION_FIELDS) {
      for (const separator of ['：', ':']) {
        const prefix = `${field.label}${separator}`
        if (line.startsWith(prefix)) {
          details[field.key] = line.slice(prefix.length).trim()
          matched = true
          break
        }
      }
      if (matched) break
    }

    if (!matched) {
      summaryLines.push(line)
    }
  }

  return {
    summary: summaryLines.join('\n').replace(/\n{3,}/g, '\n\n').trim(),
    details,
  }
}

export default function DishesPage() {
  const [dishes, setDishes] = useState<Dish[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<Dish | null>(null)
  const [form, setForm] = useState<DishFormData>(EMPTY_FORM)
  const [visualSummary, setVisualSummary] = useState('')
  const [structuredDescription, setStructuredDescription] = useState<StructuredDescriptionForm>(EMPTY_STRUCTURED_DESCRIPTION)
  const [generatedDescriptionItems, setGeneratedDescriptionItems] = useState<GeneratedDescriptionItem[]>([])
  const [saving, setSaving] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [importing, setImporting] = useState(false)
  const [generatingDesc, setGeneratingDesc] = useState(false)
  const [rebuildingEmbeddings, setRebuildingEmbeddings] = useState(false)
  const [showConfirmModal, setShowConfirmModal] = useState(false)
  const [pendingAiData, setPendingAiData] = useState<any>(null)
  const [batchAnalyzing, setBatchAnalyzing] = useState(false)
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number; dishName: string } | null>(null)
  const [existingSampleImages, setExistingSampleImages] = useState<DishSampleImage[]>([])
  const [pendingSampleImages, setPendingSampleImages] = useState<PendingSampleImage[]>([])
  const [deletingImageId, setDeletingImageId] = useState<number | null>(null)
  const [activeModalTab, setActiveModalTab] = useState('basic')
  const [recognitionMode, setRecognitionMode] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)
  const descImageInputRef = useRef<HTMLInputElement>(null)
  const sampleImagesInputRef = useRef<HTMLInputElement>(null)
  const pendingSampleImagesRef = useRef<PendingSampleImage[]>([])

  const PAGE_SIZE = 15
  const localRecognitionModeEnabled = isLocalRecognitionMode(recognitionMode)
  const totalSampleImages = existingSampleImages.length + pendingSampleImages.length
  const remainingSampleSlots = Math.max(MAX_SAMPLE_IMAGES - totalSampleImages, 0)
  const readySampleCount = existingSampleImages.filter(image => image.embedding_status === 'ready').length
  const processingSampleCount = existingSampleImages.filter(image => image.embedding_status === 'processing').length
  const failedSampleCount = existingSampleImages.filter(image => image.embedding_status === 'failed').length
  const pendingQueueCount = pendingSampleImages.length

  const revokePendingSampleImages = (images: PendingSampleImage[]) => {
    images.forEach(image => URL.revokeObjectURL(image.previewUrl))
  }

  const resetPendingSampleImages = () => {
    setPendingSampleImages(prev => {
      revokePendingSampleImages(prev)
      return []
    })
  }

  const resetModalState = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setVisualSummary('')
    setStructuredDescription(EMPTY_STRUCTURED_DESCRIPTION)
    setGeneratedDescriptionItems([])
    setExistingSampleImages([])
    resetPendingSampleImages()
    setActiveModalTab('basic')
    setShowModal(false)
  }

  const load = async () => {
    setLoading(true)
    try {
      const res = await dishApi.list({ page, page_size: PAGE_SIZE, search, category, active_only: 'false' })
      setDishes(res.data.data.items)
      setTotal(res.data.data.total)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, category])
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t) }, [search])
  useEffect(() => {
    adminApi.config().then((res) => {
      setRecognitionMode(String(res.data.data.dish_recognition_mode || ''))
    }).catch(() => {})
  }, [])
  useEffect(() => {
    pendingSampleImagesRef.current = pendingSampleImages
  }, [pendingSampleImages])
  useEffect(() => () => revokePendingSampleImages(pendingSampleImagesRef.current), [])
  useEffect(() => {
    if (!localRecognitionModeEnabled && activeModalTab === 'samples') {
      setActiveModalTab('basic')
    }
  }, [activeModalTab, localRecognitionModeEnabled])

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setVisualSummary('')
    setStructuredDescription(EMPTY_STRUCTURED_DESCRIPTION)
    setExistingSampleImages([])
    resetPendingSampleImages()
    setActiveModalTab('basic')
    setShowModal(true)
  }

  const openEdit = (dish: Dish) => {
    const parsedDescription = parseStructuredDescription(dish.description || '')
    setEditing(dish)
    setForm({
      name: dish.name,
      description: dish.description || '',
      ingredients: dish.ingredients || '',
      price: String(dish.price),
      category: dish.category,
      weight: String(dish.weight ?? 100),
      calories: String(dish.calories ?? ''),
      protein: String(dish.protein ?? ''),
      fat: String(dish.fat ?? ''),
      carbohydrate: String(dish.carbohydrate ?? ''),
      sodium: String(dish.sodium ?? ''),
      fiber: String(dish.fiber ?? ''),
    })
    setVisualSummary(parsedDescription.summary)
    setStructuredDescription(parsedDescription.details)
    setGeneratedDescriptionItems([])
    setExistingSampleImages(dish.sample_images || [])
    resetPendingSampleImages()
    setActiveModalTab('basic')
    setShowModal(true)
  }

  const hasNutritionData = () => {
    return form.calories || form.protein || form.fat || form.carbohydrate || form.sodium || form.fiber || form.description
  }

  const applyAiData = (data: any) => {
    const nutrition = data.nutrition
    const aiCategory = data.category
    const validCategory = CATEGORIES.includes(aiCategory) ? aiCategory : form.category
    const parsedDescription = parseStructuredDescription(String(data.description || ''))
    const structuredFromApi = normalizeStructuredDescriptionPayload(data.structured_description)
    const nextStructured = hasStructuredDescriptionValues(structuredFromApi)
      ? structuredFromApi
      : parsedDescription.details
    const nextSummary = parsedDescription.summary || String(data.description || visualSummary || '')
    setForm(f => ({
      ...f,
      category: validCategory,
      description: buildStructuredDescription(nextSummary, nextStructured),
      calories: String(nutrition.calories ?? ''),
      protein: String(nutrition.protein ?? ''),
      fat: String(nutrition.fat ?? ''),
      carbohydrate: String(nutrition.carbohydrate ?? ''),
      sodium: String(nutrition.sodium ?? ''),
      fiber: String(nutrition.fiber ?? ''),
    }))
    setVisualSummary(nextSummary)
    setStructuredDescription(nextStructured)
    toast.success('AI分析完成：已生成营养成分、分类和结构化描述')
  }

  const handleAnalyze = async () => {
    if (!form.name.trim()) {
      toast.error('请先输入菜品名称')
      return
    }
    const weight = parseInt(form.weight) || 100
    if (weight <= 0 || weight > 10000) {
      toast.error('重量必须在 1-10000g 之间')
      return
    }
    setAnalyzing(true)
    try {
      const res = await dishApi.analyzePreview(form.name.trim(), weight, form.ingredients)
      const data = res.data.data
      if (hasNutritionData()) {
        setPendingAiData(data)
        setShowConfirmModal(true)
      } else {
        applyAiData(data)
      }
    } finally {
      setAnalyzing(false)
    }
  }

  const handleConfirmOverwrite = (overwrite: boolean) => {
    if (overwrite && pendingAiData) {
      applyAiData(pendingAiData)
    } else {
      toast('已跳过数据填充')
    }
    setShowConfirmModal(false)
    setPendingAiData(null)
  }

  const applyGeneratedDescription = (item: GeneratedDescriptionItem) => {
    const parsedDescription = parseStructuredDescription(item.description)
    const nextStructured = hasStructuredDescriptionValues(item.structuredDescription)
      ? item.structuredDescription
      : parsedDescription.details
    const nextSummary = parsedDescription.summary || item.description

    setVisualSummary(nextSummary)
    setStructuredDescription(nextStructured)
    setForm(f => ({ ...f, description: buildStructuredDescription(nextSummary, nextStructured) }))
  }

  const handleGenerateDescription = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp']
    if (!allowedTypes.includes(file.type)) {
      toast.error('请上传 JPG、PNG 或 WebP 格式的图片')
      if (descImageInputRef.current) descImageInputRef.current.value = ''
      return
    }

    setGeneratingDesc(true)
    try {
      const res = await dishApi.generateDescription(file, form.name.trim() || undefined)
      const payload = res.data.data
      const items = normalizeGeneratedDescriptionItems(payload)
      setGeneratedDescriptionItems(items)

      if (!items.length) {
        toast.error('未能解析出可用的菜品描述')
        return
      }

      if (items.length === 1) {
        applyGeneratedDescription(items[0])
        toast.success('已从图片生成视觉描述')
        return
      }

      toast.success(`图片中识别到 ${items.length} 道菜，请选择当前菜品对应的一条描述`)
    } finally {
      setGeneratingDesc(false)
      if (descImageInputRef.current) descImageInputRef.current.value = ''
    }
  }

  const handleSelectSampleImages = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return

    const allowedTypes = ['image/jpeg', 'image/png', 'image/webp']
    const invalidFile = files.find(file => !allowedTypes.includes(file.type))
    if (invalidFile) {
      toast.error(`文件 ${invalidFile.name} 格式不支持`)
      if (sampleImagesInputRef.current) sampleImagesInputRef.current.value = ''
      return
    }

    if (existingSampleImages.length + pendingSampleImages.length + files.length > MAX_SAMPLE_IMAGES) {
      toast.error(`每个菜品最多上传 ${MAX_SAMPLE_IMAGES} 张样图`)
      if (sampleImagesInputRef.current) sampleImagesInputRef.current.value = ''
      return
    }

    setPendingSampleImages(prev => [
      ...prev,
      ...files.map(file => ({
        id: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        previewUrl: URL.createObjectURL(file),
      })),
    ])

    if (sampleImagesInputRef.current) sampleImagesInputRef.current.value = ''
  }

  const removePendingSampleImage = (imageId: string) => {
    setPendingSampleImages(prev => {
      const target = prev.find(image => image.id === imageId)
      if (target) URL.revokeObjectURL(target.previewUrl)
      return prev.filter(image => image.id !== imageId)
    })
  }

  const deleteExistingSampleImage = async (imageId: number) => {
    setDeletingImageId(imageId)
    try {
      await dishApi.deleteImage(imageId)
      setExistingSampleImages(prev => prev.filter(image => image.id !== imageId))
      setEditing(prev => prev ? {
        ...prev,
        sample_images: (prev.sample_images || []).filter(image => image.id !== imageId),
        sample_image_count: Math.max((prev.sample_image_count || 1) - 1, 0),
      } : prev)
      toast.success('样图已删除')
      load()
    } finally {
      setDeletingImageId(null)
    }
  }

  const save = async () => {
    if (!form.name.trim()) { toast.error('菜品名称不能为空'); return }
    if (!form.price) { toast.error('价格不能为空'); return }

    setSaving(true)
    try {
      const payload = {
        ...form,
        price: parseFloat(form.price),
        weight: form.weight ? parseFloat(form.weight) : 100,
        calories: form.calories ? parseFloat(form.calories) : null,
        protein: form.protein ? parseFloat(form.protein) : null,
        fat: form.fat ? parseFloat(form.fat) : null,
        carbohydrate: form.carbohydrate ? parseFloat(form.carbohydrate) : null,
        sodium: form.sodium ? parseFloat(form.sodium) : null,
        fiber: form.fiber ? parseFloat(form.fiber) : null,
      }

      let dishId = editing?.id
      if (editing) {
        await dishApi.update(editing.id, payload)
      } else {
        const res = await dishApi.create(payload)
        dishId = res.data.data.id
      }

      if (dishId && pendingSampleImages.length > 0) {
        await dishApi.uploadImages(dishId, pendingSampleImages.map(image => image.file))
      }

      toast.success(
        `${editing ? '菜品已更新' : '菜品已创建'}${pendingSampleImages.length > 0 ? `，已上传 ${pendingSampleImages.length} 张样图` : ''}`,
      )
      resetModalState()
      load()
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async (dish: Dish) => {
    await dishApi.update(dish.id, { is_active: !dish.is_active })
    toast.success(dish.is_active ? '已停用菜品' : '已启用菜品')
    load()
  }

  const handleDownloadTemplate = async () => {
    try {
      const res = await dishApi.downloadTemplate()
      const url = window.URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = '菜品导入模板.xlsx'
      a.click()
      window.URL.revokeObjectURL(url)
    } catch {
      toast.error('下载模板失败')
    }
  }

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (!file.name.endsWith('.xlsx') && !file.name.endsWith('.xls')) {
      toast.error('请上传 Excel 文件 (.xlsx 或 .xls)')
      return
    }
    setImporting(true)
    try {
      const res = await dishApi.import(file)
      const data = res.data.data
      toast.success(`导入完成：新增 ${data.created_count} 条，更新 ${data.updated_count} 条`)
      if (data.warnings?.length) {
        setTimeout(() => toast.error(data.warnings.slice(0, 3).join('\n')), 500)
      }
      load()
    } finally {
      setImporting(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleBatchAnalyze = async () => {
    setBatchAnalyzing(true)
    setBatchProgress(null)

    try {
      const allDishes: Dish[] = []
      let currentPage = 1
      const pageSize = 100
      while (true) {
        const res = await dishApi.list({ page: currentPage, page_size: pageSize, active_only: 'true' })
        const items = res.data.data.items as Dish[]
        allDishes.push(...items)
        if (items.length < pageSize) break
        currentPage++
      }

      const toAnalyze = allDishes.filter(dish => dish.calories === null || dish.calories === undefined)
      if (toAnalyze.length === 0) {
        toast.success('所有菜品都已分析过')
        return
      }

      let successCount = 0
      let failCount = 0
      const errors: string[] = []

      for (let i = 0; i < toAnalyze.length; i++) {
        const dish = toAnalyze[i]
        setBatchProgress({ current: i + 1, total: toAnalyze.length, dishName: dish.name })

        try {
          await dishApi.analyze(dish.id, dish.weight ?? 100)
          successCount++
        } catch (err: any) {
          failCount++
          errors.push(`${dish.name}: ${err.response?.data?.message || err.message || '失败'}`)
        }

        if (i < toAnalyze.length - 1) {
          await new Promise(resolve => setTimeout(resolve, 300))
        }
      }

      if (successCount > 0) {
        toast.success(`分析完成：成功 ${successCount} 个${failCount > 0 ? `，失败 ${failCount} 个` : ''}`)
      } else {
        toast.error('所有分析均失败')
      }
      if (errors.length > 0) {
        setTimeout(() => toast.error(errors.slice(0, 3).join('\n')), 500)
      }
      load()
    } catch (err: any) {
      toast.error(err.response?.data?.message || '获取菜品列表失败')
    } finally {
      setBatchAnalyzing(false)
      setBatchProgress(null)
    }
  }

  const handleRebuildSampleEmbeddings = async () => {
    setRebuildingEmbeddings(true)
    try {
      await dishApi.rebuildSampleEmbeddings()
      toast.success('样图 embedding 重建任务已提交')
    } finally {
      setRebuildingEmbeddings(false)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const composedDescription = buildStructuredDescription(visualSummary, structuredDescription)

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold">菜品管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">共 {total} 个菜品</p>
        </div>
        <div className="flex items-center gap-2 sm:w-auto w-full">
          {localRecognitionModeEnabled && (
            <button
              onClick={handleRebuildSampleEmbeddings}
              disabled={rebuildingEmbeddings}
              className="flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-amber-100 text-amber-700 hover:bg-amber-200 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn('w-4 h-4', rebuildingEmbeddings && 'animate-spin')} />
              {rebuildingEmbeddings ? '重建中...' : '重建样图'}
            </button>
          )}
          <button
            onClick={handleBatchAnalyze}
            disabled={batchAnalyzing}
            className="flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-purple-100 text-purple-700 hover:bg-purple-200 transition-colors disabled:opacity-50"
          >
            <Wand2 className="w-4 h-4" />
            {batchAnalyzing && batchProgress
              ? `分析中 ${batchProgress.current}/${batchProgress.total}`
              : batchAnalyzing ? '分析中...' : '一键分析'}
          </button>
          <button
            onClick={handleDownloadTemplate}
            className="flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-border hover:bg-secondary transition-colors"
          >
            <Download className="w-4 h-4" />
            模板
          </button>
          <label className="flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-border hover:bg-secondary transition-colors cursor-pointer">
            <Upload className="w-4 h-4" />
            {importing ? '导入中...' : '导入'}
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              onChange={handleImportFile}
              className="hidden"
              disabled={importing}
            />
          </label>
          <button
            onClick={openCreate}
            className="flex items-center justify-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors"
          >
            <Plus className="w-4 h-4" />
            新增菜品
          </button>
        </div>
      </div>

      {batchAnalyzing && batchProgress && (
        <div className="mb-4 p-4 bg-purple-50 border border-purple-200 rounded-xl">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-purple-700">正在批量分析菜品营养成分</span>
            <span className="text-sm text-purple-600">{batchProgress.current} / {batchProgress.total}</span>
          </div>
          <div className="h-2.5 bg-purple-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-purple-500 transition-all duration-300 ease-out"
              style={{ width: `${(batchProgress.current / batchProgress.total) * 100}%` }}
            />
          </div>
          <div className="mt-2 text-xs text-purple-600 truncate" title={batchProgress.dishName}>
            当前: {batchProgress.dishName}
          </div>
        </div>
      )}

      <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-4">
        <div className="relative flex-1 sm:max-w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
          <input
            value={search}
            onChange={e => { setSearch(e.target.value); setPage(1) }}
            placeholder="搜索菜品名称..."
            className="w-full pl-8 pr-4 py-2 text-sm bg-card border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
        </div>
        <div className="flex gap-1.5 flex-wrap">
          <button
            onClick={() => { setCategory(''); setPage(1) }}
            className={cn('px-3 py-1.5 text-xs rounded-md transition-colors', !category ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}
          >全部</button>
          {CATEGORIES.map(item => (
            <button
              key={item}
              onClick={() => { setCategory(item); setPage(1) }}
              className={cn('px-3 py-1.5 text-xs rounded-md transition-colors', category === item ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}
            >{item}</button>
          ))}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-x-auto">
        <table className="data-table min-w-[640px]">
          <thead>
            <tr>
              <th>菜品名称</th>
              <th>分类</th>
              <th>单价</th>
              <th>热量<span className="normal-case font-normal ml-1 opacity-60">kcal</span></th>
              <th>蛋白质<span className="normal-case font-normal ml-1 opacity-60">g</span></th>
              <th>状态</th>
              <th>更新时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="text-center text-muted-foreground py-12">加载中...</td></tr>
            ) : dishes.length === 0 ? (
              <tr><td colSpan={8} className="text-center text-muted-foreground py-12">暂无数据</td></tr>
            ) : dishes.map(dish => (
              <tr key={dish.id} className={!dish.is_active ? 'opacity-40' : ''}>
                <td>
                  <span className="font-medium">{dish.name}</span>
                  {dish.description && <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-48">{dish.description}</p>}
                  {localRecognitionModeEnabled && (
                    <p className="text-xs text-muted-foreground mt-1">样图 {dish.sample_image_count || 0} 张</p>
                  )}
                </td>
                <td>
                  <span className={cn('text-xs px-2 py-0.5 rounded-full font-medium', CATEGORY_COLORS[dish.category])}>{dish.category}</span>
                </td>
                <td><span className="font-mono">¥{dish.price.toFixed(2)}</span></td>
                <td><span className="font-mono">{dish.calories ?? '—'}</span></td>
                <td><span className="font-mono">{dish.protein ?? '—'}</span></td>
                <td>
                  <span className={cn('text-xs', dish.is_active ? 'text-health-green' : 'text-muted-foreground')}>
                    {dish.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="text-xs text-muted-foreground font-mono">{fmtDate(dish.updated_at)}</td>
                <td>
                  <div className="flex items-center gap-1">
                    <button onClick={() => openEdit(dish)} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
                      <Edit2 className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                    <button onClick={() => toggleActive(dish)} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
                      <Trash2 className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-xs text-muted-foreground">共 {total} 条</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors">
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-xs font-mono px-2">{page} / {totalPages}</span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors">
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-3xl shadow-xl animate-fade-in max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-border">
              <h3 className="font-medium">{editing ? '编辑菜品' : '新增菜品'}</h3>
              <button onClick={resetModalState} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <Tabs.Root value={activeModalTab} onValueChange={setActiveModalTab} className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-border px-5 pt-4">
                <Tabs.List className="flex gap-2">
                  {[
                    { value: 'basic', label: '基础信息' },
                    { value: 'nutrition', label: '营养成分' },
                    ...(localRecognitionModeEnabled
                      ? [{ value: 'samples', label: 'Embedding 样图', count: existingSampleImages.length + pendingSampleImages.length }]
                      : []),
                  ].map(tab => (
                    <Tabs.Trigger
                      key={tab.value}
                      value={tab.value}
                      className="inline-flex items-center gap-2 rounded-t-lg border border-transparent px-3 py-2 text-sm text-muted-foreground transition-colors data-[state=active]:border-border data-[state=active]:border-b-white data-[state=active]:bg-white data-[state=active]:text-foreground"
                    >
                      <span>{tab.label}</span>
                      {typeof tab.count === 'number' && (
                        <span className="rounded-full bg-secondary px-1.5 py-0.5 text-[11px] text-muted-foreground">
                          {tab.count}
                        </span>
                      )}
                    </Tabs.Trigger>
                  ))}
                </Tabs.List>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-5">
                <Tabs.Content value="basic" className="space-y-5 focus:outline-none">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="md:col-span-2">
                      <label className="text-xs font-medium text-muted-foreground">菜品名称 *</label>
                      <div className="mt-1 flex flex-col gap-2 sm:flex-row">
                        <input
                          value={form.name}
                          onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                          className="flex-1 px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        />
                        <div className="flex items-center gap-1 px-2 bg-secondary rounded-lg border border-border sm:w-[92px]">
                          <input
                            type="number"
                            value={form.weight}
                            onChange={e => setForm(f => ({ ...f, weight: e.target.value }))}
                            className="w-full text-sm bg-transparent text-right focus:outline-none"
                            placeholder="100"
                          />
                          <span className="text-xs text-muted-foreground">g</span>
                        </div>
                        <button
                          onClick={handleAnalyze}
                          disabled={analyzing || !form.name.trim()}
                          className="flex items-center justify-center gap-1.5 px-3 py-2 text-sm bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors disabled:opacity-50 whitespace-nowrap"
                        >
                          <Sparkles className="w-4 h-4" />
                          {analyzing ? '分析中...' : 'AI分析'}
                        </button>
                      </div>
                    </div>

                    <div className="md:col-span-2">
                      <label className="text-xs font-medium text-muted-foreground">配菜描述（选填）</label>
                      <textarea
                        value={form.ingredients}
                        onChange={e => setForm(f => ({ ...f, ingredients: e.target.value }))}
                        rows={2}
                        placeholder="描述菜品的主要食材、配菜组成，例如：红烧肉配土豆、青菜炒香菇。可用于更精确的营养成分分析..."
                        className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 resize-none"
                      />
                      <p className="mt-1 text-xs text-muted-foreground">输入菜品名称和重量，点击 AI 分析自动生成描述和营养成分</p>
                    </div>

                    <div>
                      <label className="text-xs font-medium text-muted-foreground">分类 *</label>
                      <select
                        value={form.category}
                        onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                        className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                      >
                        {CATEGORIES.map(item => <option key={item}>{item}</option>)}
                      </select>
                    </div>

                    <div>
                      <label className="text-xs font-medium text-muted-foreground">单价(元) *</label>
                      <input
                        type="number"
                        value={form.price}
                        onChange={e => setForm(f => ({ ...f, price: e.target.value }))}
                        className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                      />
                    </div>
                  </div>

                  <div className="rounded-xl border border-border bg-secondary/20 p-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground">视觉描述（复用 description 字段）</label>
                        <p className="mt-1 text-xs text-muted-foreground">下面的摘要和结构化特征会自动拼成一个 description 文本保存，不新增数据库字段。</p>
                      </div>
                      <label className="flex items-center gap-1.5 text-xs text-purple-600 cursor-pointer hover:text-purple-700 transition-colors whitespace-nowrap">
                        <ImagePlus className="w-3.5 h-3.5" />
                        {generatingDesc ? '生成中...' : '上传图片生成'}
                        <input
                          ref={descImageInputRef}
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          onChange={handleGenerateDescription}
                          className="hidden"
                          disabled={generatingDesc}
                        />
                      </label>
                    </div>
                    <textarea
                      value={visualSummary}
                      onChange={e => {
                        const nextSummary = e.target.value
                        setVisualSummary(nextSummary)
                        setForm(f => ({ ...f, description: buildStructuredDescription(nextSummary, structuredDescription) }))
                      }}
                      rows={4}
                      placeholder="先写一段简洁视觉摘要，例如：红烧排骨呈深红褐色，排骨块较大，表面有油亮酱汁，常配土豆块和青椒。"
                      className="mt-3 w-full px-3 py-2 text-sm bg-white border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 resize-none"
                    />
                    {generatedDescriptionItems.length > 1 && (
                      <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50/80 p-3">
                        <p className="text-xs font-medium text-blue-700">
                          当前图片识别到 {generatedDescriptionItems.length} 道菜，请选择当前菜品对应的一条描述写入表单。
                        </p>
                        <div className="mt-3 space-y-2">
                          {generatedDescriptionItems.map((item, index) => (
                            <div
                              key={`generated-description-${index}-${item.position}-${item.description}`}
                              className="rounded-lg border border-blue-100 bg-white px-3 py-3"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                  <p className="text-xs font-medium text-blue-700">
                                    菜品 {index + 1}
                                    {item.position ? ` · ${item.position}` : ''}
                                  </p>
                                  <p className="mt-1 text-sm text-foreground whitespace-pre-wrap break-words">
                                    {item.description || '无摘要描述'}
                                  </p>
                                  {item.notes && (
                                    <p className="mt-1 text-xs text-muted-foreground whitespace-pre-wrap break-words">
                                      备注：{item.notes}
                                    </p>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  onClick={() => applyGeneratedDescription(item)}
                                  className="shrink-0 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700"
                                >
                                  使用这条
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                      {STRUCTURED_DESCRIPTION_FIELDS.map(field => (
                        <div key={field.key}>
                          <label className="text-xs font-medium text-muted-foreground">{field.label}</label>
                          <input
                            value={structuredDescription[field.key]}
                            onChange={e => {
                              const nextValue = e.target.value
                              setStructuredDescription(prev => {
                                const next = { ...prev, [field.key]: nextValue }
                                setForm(current => ({ ...current, description: buildStructuredDescription(visualSummary, next) }))
                                return next
                              })
                            }}
                            placeholder={field.placeholder}
                            className="mt-1 w-full px-3 py-2 text-sm bg-white border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                          />
                        </div>
                      ))}
                    </div>
                    <div className="mt-4">
                      <label className="text-xs font-medium text-muted-foreground">最终保存到 description 的文本</label>
                      <textarea
                        value={composedDescription}
                        readOnly
                        rows={Math.max(4, composedDescription ? composedDescription.split('\n').length : 4)}
                        className="mt-1 w-full px-3 py-2 text-sm bg-slate-50 border border-border rounded-lg text-muted-foreground focus:outline-none resize-none"
                      />
                    </div>
                  </div>
                </Tabs.Content>

                <Tabs.Content value="nutrition" className="space-y-4 focus:outline-none">
                  <div className="flex items-center justify-between rounded-xl border border-border bg-secondary/20 px-4 py-3">
                    <div>
                      <label className="text-xs font-medium text-muted-foreground">营养成分（每100g）</label>
                      <p className="mt-1 text-xs text-muted-foreground">AI 分析会优先填充这里；也可以手动微调。</p>
                    </div>
                    <button
                      onClick={handleAnalyze}
                      disabled={analyzing || !form.name.trim()}
                      className="flex items-center gap-1.5 px-3 py-2 text-sm bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors disabled:opacity-50 whitespace-nowrap"
                    >
                      <Sparkles className="w-4 h-4" />
                      {analyzing ? '分析中...' : '重新分析'}
                    </button>
                  </div>

                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                    {[
                      { key: 'calories', label: '热量 kcal' },
                      { key: 'protein', label: '蛋白质 g' },
                      { key: 'fat', label: '脂肪 g' },
                      { key: 'carbohydrate', label: '碳水化合物 g' },
                      { key: 'sodium', label: '钠 mg' },
                      { key: 'fiber', label: '膳食纤维 g' },
                    ].map(({ key, label }) => (
                      <div key={key} className="rounded-lg border border-border bg-white p-3">
                        <label className="text-xs text-muted-foreground">{label}</label>
                        <input
                          type="number"
                          value={form[key as keyof DishFormData]}
                          onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                          className="mt-2 w-full px-2 py-2 text-sm bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        />
                      </div>
                    ))}
                  </div>
                </Tabs.Content>

                {localRecognitionModeEnabled && (
                <Tabs.Content value="samples" className="focus:outline-none">
                  <div className="rounded-[24px] border border-border bg-[linear-gradient(180deg,rgba(249,251,250,0.98),rgba(242,247,244,0.96))] p-4 shadow-[0_18px_40px_rgba(15,23,42,0.06)]">
                    <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                      <div className="max-w-2xl">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="inline-flex items-center gap-1.5 rounded-full bg-foreground px-3 py-1 text-[11px] font-medium text-background">
                            <Images className="h-3.5 w-3.5" />
                            Embedding 样图库
                          </span>
                          <span className="rounded-full border border-border bg-white/85 px-3 py-1 text-[11px] font-medium text-muted-foreground">
                            {totalSampleImages} / {MAX_SAMPLE_IMAGES}
                          </span>
                          <span className="rounded-full border border-border bg-white/85 px-3 py-1 text-[11px] font-medium text-muted-foreground">
                            剩余 {remainingSampleSlots} 张
                          </span>
                        </div>
                        <p className="mt-3 text-sm leading-relaxed text-foreground">
                          把这组图片当作检索样本，而不是普通附件。优先保留真实出餐环境、角度稳定、菜品主体清晰的画面。
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          最佳实践：一组样图里同时覆盖正视角、轻微遮挡、不同光线和常见摆盘变化，避免摆拍图和强滤镜图混入。
                        </p>
                      </div>
                      <div className="rounded-[20px] border border-border bg-white/90 p-3 shadow-sm xl:w-[280px]">
                        <p className="text-xs font-medium text-muted-foreground">样图操作台</p>
                        <div className="mt-3 flex flex-col gap-2">
                          <label className={cn(
                            'inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors',
                            remainingSampleSlots > 0
                              ? 'cursor-pointer bg-foreground text-background hover:bg-foreground/90'
                              : 'cursor-not-allowed bg-secondary text-muted-foreground',
                          )}>
                            <ImagePlus className="h-4 w-4" />
                            {remainingSampleSlots > 0 ? '继续添加样图' : '样图已达上限'}
                            <input
                              ref={sampleImagesInputRef}
                              type="file"
                              accept="image/jpeg,image/png,image/webp"
                              multiple
                              onChange={handleSelectSampleImages}
                              className="hidden"
                              disabled={remainingSampleSlots <= 0}
                            />
                          </label>
                          <div className="rounded-xl bg-secondary/60 px-3 py-2">
                            <p className="text-[11px] font-medium text-foreground">保存说明</p>
                            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                              新选中的图片会先进入待上传区，点击底部“保存”后才会真正写入菜品样图库。
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      {[
                        {
                          label: '已就绪',
                          value: readySampleCount,
                          sub: '可参与检索',
                          icon: CheckCircle2,
                          tone: 'bg-green-50 text-green-700 border-green-100',
                        },
                        {
                          label: '生成中',
                          value: processingSampleCount,
                          sub: '等待 embedding',
                          icon: RefreshCw,
                          tone: 'bg-blue-50 text-blue-700 border-blue-100',
                        },
                        {
                          label: '待上传',
                          value: pendingQueueCount,
                          sub: '本次编辑新增',
                          icon: Clock3,
                          tone: 'bg-amber-50 text-amber-700 border-amber-100',
                        },
                        {
                          label: '失败',
                          value: failedSampleCount,
                          sub: '建议替换样本',
                          icon: AlertTriangle,
                          tone: 'bg-red-50 text-red-700 border-red-100',
                        },
                      ].map(({ label, value, sub, icon: Icon, tone }) => (
                        <div key={label} className={cn('rounded-[18px] border px-4 py-3', tone)}>
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <p className="text-[11px] font-medium uppercase tracking-[0.16em] opacity-70">{label}</p>
                              <p className="mt-2 text-2xl font-semibold">{value}</p>
                            </div>
                            <Icon className={cn('h-5 w-5', label === '生成中' && value > 0 && 'animate-spin')} />
                          </div>
                          <p className="mt-2 text-[11px] opacity-80">{sub}</p>
                        </div>
                      ))}
                    </div>

                    <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_320px]">
                      <div className="space-y-4">
                        {pendingSampleImages.length > 0 && (
                          <section className="rounded-[20px] border border-amber-200 bg-[linear-gradient(180deg,rgba(255,251,235,0.98),rgba(255,247,214,0.9))] p-4">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-amber-900">待上传队列</p>
                                <p className="mt-1 text-[11px] text-amber-800/80">
                                  这些图片尚未提交到后端，保存菜品后会一并上传。
                                </p>
                              </div>
                              <span className="rounded-full bg-white/90 px-3 py-1 text-[11px] font-medium text-amber-700">
                                {pendingSampleImages.length} 张待提交
                              </span>
                            </div>
                            <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
                              {pendingSampleImages.map(image => (
                                <article
                                  key={`pending-${image.id}`}
                                  className="overflow-hidden rounded-[18px] border border-amber-200 bg-white shadow-[0_12px_30px_rgba(245,158,11,0.08)]"
                                >
                                  <div className="relative aspect-[0.96] overflow-hidden bg-secondary">
                                    <img src={image.previewUrl} alt={image.file.name} className="h-full w-full object-cover transition-transform duration-300 hover:scale-[1.03]" />
                                    <div className="absolute left-3 top-3 rounded-full bg-amber-500 px-2 py-1 text-[10px] font-medium text-white">
                                      待上传
                                    </div>
                                  </div>
                                  <div className="space-y-3 p-3">
                                    <div>
                                      <p className="truncate text-xs font-medium text-foreground">{image.file.name}</p>
                                      <p className="mt-1 text-[11px] text-muted-foreground">
                                        {(image.file.size / 1024 / 1024).toFixed(2)} MB
                                      </p>
                                    </div>
                                    <button
                                      type="button"
                                      onClick={() => removePendingSampleImage(image.id)}
                                      className="w-full rounded-xl border border-red-200 px-3 py-2 text-xs font-medium text-red-700 transition-colors hover:bg-red-50"
                                    >
                                      移出队列
                                    </button>
                                  </div>
                                </article>
                              ))}
                            </div>
                          </section>
                        )}

                        <section className="rounded-[20px] border border-border bg-white/92 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-foreground">已入库样图</p>
                              <p className="mt-1 text-[11px] text-muted-foreground">
                                优先保留覆盖不同角度和摆盘变化的高质量样本。
                              </p>
                            </div>
                            <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                              <span className="rounded-full bg-secondary px-2.5 py-1">已保存 {existingSampleImages.length}</span>
                              <span className="rounded-full bg-secondary px-2.5 py-1">总计 {totalSampleImages}</span>
                            </div>
                          </div>

                          {existingSampleImages.length > 0 ? (
                            <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
                              {existingSampleImages.map(image => (
                                <article
                                  key={`existing-${image.id}`}
                                  className="group overflow-hidden rounded-[18px] border border-border bg-[linear-gradient(180deg,rgba(255,255,255,1),rgba(248,250,249,0.96))] shadow-[0_14px_30px_rgba(15,23,42,0.05)] transition-transform duration-200 hover:-translate-y-0.5"
                                >
                                  <div className="relative aspect-[0.96] overflow-hidden bg-secondary">
                                    {image.image_url ? (
                                      <img
                                        src={image.image_url}
                                        alt={image.original_filename || `样图-${image.id}`}
                                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
                                      />
                                    ) : (
                                      <div className="flex h-full w-full items-center justify-center text-xs text-muted-foreground">无预览</div>
                                    )}
                                    <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 via-black/10 to-transparent p-3 pt-8">
                                      <div className="flex flex-wrap items-center gap-2">
                                        <span className={cn('rounded-full px-2 py-1 text-[10px] font-medium', EMBEDDING_STATUS_COLORS[image.embedding_status] || 'bg-secondary text-muted-foreground')}>
                                          {EMBEDDING_STATUS_LABELS[image.embedding_status] || image.embedding_status}
                                        </span>
                                        {image.is_cover && (
                                          <span className="rounded-full bg-white/90 px-2 py-1 text-[10px] font-medium text-slate-700">
                                            封面
                                          </span>
                                        )}
                                      </div>
                                    </div>
                                  </div>
                                  <div className="space-y-3 p-3">
                                    <div>
                                      <p className="truncate text-xs font-medium text-foreground">
                                        {image.original_filename || `样图 ${image.id}`}
                                      </p>
                                      <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                                        <span>排序 #{image.sort_order}</span>
                                        {image.embedding_updated_at && <span>{fmtDate(image.embedding_updated_at)}</span>}
                                      </div>
                                      {image.error_message && (
                                        <p className="mt-2 line-clamp-2 text-[11px] text-red-600">
                                          {image.error_message}
                                        </p>
                                      )}
                                    </div>
                                    <button
                                      type="button"
                                      onClick={() => deleteExistingSampleImage(image.id)}
                                      disabled={deletingImageId === image.id}
                                      className="w-full rounded-xl border border-red-200 px-3 py-2 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
                                    >
                                      {deletingImageId === image.id ? '删除中...' : '删除样图'}
                                    </button>
                                  </div>
                                </article>
                              ))}
                            </div>
                          ) : (
                            <div className="mt-4 rounded-[18px] border border-dashed border-border bg-secondary/25 px-4 py-10 text-center">
                              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-white text-muted-foreground shadow-sm">
                                <Inbox className="h-5 w-5" />
                              </div>
                              <p className="mt-4 text-sm font-medium text-foreground">当前还没有已入库样图</p>
                              <p className="mt-1 text-xs text-muted-foreground">
                                先添加 3-6 张能覆盖常见真实场景的图片，保存后会开始进入 embedding 流程。
                              </p>
                            </div>
                          )}
                        </section>
                      </div>

                      <aside className="space-y-4">
                        <div className="rounded-[20px] border border-border bg-white/92 p-4">
                          <p className="text-sm font-semibold text-foreground">采样建议</p>
                          <div className="mt-3 space-y-3">
                            {[
                              '至少保留一张正视角清晰图，作为稳定基准样本。',
                              '补充 1-2 张轻微遮挡、不同光线或餐盘边缘裁切的真实场景图。',
                              '删除模糊、过曝、滤镜重、主体太小的图，避免干扰 embedding。',
                            ].map((tip, index) => (
                              <div key={tip} className="flex gap-3 rounded-2xl bg-secondary/45 px-3 py-3">
                                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white text-[11px] font-semibold text-foreground shadow-sm">
                                  {index + 1}
                                </span>
                                <p className="text-[12px] leading-relaxed text-muted-foreground">{tip}</p>
                              </div>
                            ))}
                          </div>
                        </div>

                        <div className="rounded-[20px] border border-border bg-white/92 p-4">
                          <p className="text-sm font-semibold text-foreground">当前状态</p>
                          <div className="mt-3 space-y-2 text-[12px] text-muted-foreground">
                            <div className="flex items-center justify-between rounded-xl bg-secondary/45 px-3 py-2">
                              <span>已入库样图</span>
                              <span className="font-medium text-foreground">{existingSampleImages.length}</span>
                            </div>
                            <div className="flex items-center justify-between rounded-xl bg-secondary/45 px-3 py-2">
                              <span>待上传队列</span>
                              <span className="font-medium text-foreground">{pendingQueueCount}</span>
                            </div>
                            <div className="flex items-center justify-between rounded-xl bg-secondary/45 px-3 py-2">
                              <span>剩余名额</span>
                              <span className="font-medium text-foreground">{remainingSampleSlots}</span>
                            </div>
                          </div>
                        </div>
                      </aside>
                    </div>
                  </div>
                </Tabs.Content>
                )}
              </div>
            </Tabs.Root>
            <div className="flex gap-3 p-5 border-t border-border">
              <button onClick={resetModalState} className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors">取消</button>
              <button onClick={save} disabled={saving} className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50">
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showConfirmModal && (
        <div className="fixed inset-0 bg-black/60 z-[60] flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-sm shadow-xl animate-fade-in">
            <div className="p-5">
              <h3 className="font-medium mb-2">数据已存在</h3>
              <p className="text-sm text-muted-foreground">
                表单中已有营养成分或描述数据，是否要用AI分析结果覆盖现有数据？
              </p>
            </div>
            <div className="flex gap-3 p-5 border-t border-border">
              <button
                onClick={() => handleConfirmOverwrite(false)}
                className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors"
              >
                跳过
              </button>
              <button
                onClick={() => handleConfirmOverwrite(true)}
                className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors"
              >
                覆盖
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
