import { useEffect, useRef, useState } from 'react'
import * as Tabs from '@radix-ui/react-tabs'
import { Plus, Search, Edit2, Trash2, ChevronLeft, ChevronRight, X, Sparkles, Download, Upload, ImagePlus, Wand2, RefreshCw } from 'lucide-react'
import { dishApi } from '@/api/client'
import { fmtDate, cn } from '@/lib/utils'
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

interface PendingSampleImage {
  id: string
  file: File
  previewUrl: string
}

const EMPTY_FORM: DishFormData = {
  name: '', description: '', ingredients: '', price: '', category: '荤菜', weight: '100',
  calories: '', protein: '', fat: '', carbohydrate: '', sodium: '', fiber: '',
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
  const fileInputRef = useRef<HTMLInputElement>(null)
  const descImageInputRef = useRef<HTMLInputElement>(null)
  const sampleImagesInputRef = useRef<HTMLInputElement>(null)
  const pendingSampleImagesRef = useRef<PendingSampleImage[]>([])

  const PAGE_SIZE = 15

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
    pendingSampleImagesRef.current = pendingSampleImages
  }, [pendingSampleImages])
  useEffect(() => () => revokePendingSampleImages(pendingSampleImagesRef.current), [])

  const openCreate = () => {
    setEditing(null)
    setForm(EMPTY_FORM)
    setExistingSampleImages([])
    resetPendingSampleImages()
    setActiveModalTab('basic')
    setShowModal(true)
  }

  const openEdit = (dish: Dish) => {
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
    setForm(f => ({
      ...f,
      category: validCategory,
      description: data.description || f.description,
      calories: String(nutrition.calories ?? ''),
      protein: String(nutrition.protein ?? ''),
      fat: String(nutrition.fat ?? ''),
      carbohydrate: String(nutrition.carbohydrate ?? ''),
      sodium: String(nutrition.sodium ?? ''),
      fiber: String(nutrition.fiber ?? ''),
    }))
    toast.success('AI分析完成：已生成营养成分、分类和视觉描述')
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
      const description = res.data.data.description
      setForm(f => ({ ...f, description }))
      toast.success('已从图片生成视觉描述')
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

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-xl sm:text-2xl font-semibold">菜品管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">共 {total} 个菜品</p>
        </div>
        <div className="flex items-center gap-2 sm:w-auto w-full">
          <button
            onClick={handleRebuildSampleEmbeddings}
            disabled={rebuildingEmbeddings}
            className="flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-lg bg-amber-100 text-amber-700 hover:bg-amber-200 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={cn('w-4 h-4', rebuildingEmbeddings && 'animate-spin')} />
            {rebuildingEmbeddings ? '重建中...' : '重建样图'}
          </button>
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
                  <p className="text-xs text-muted-foreground mt-1">样图 {dish.sample_image_count || 0} 张</p>
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
                    { value: 'samples', label: 'Embedding 样图', count: existingSampleImages.length + pendingSampleImages.length },
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
                        <label className="text-xs font-medium text-muted-foreground">视觉描述（用于AI图像识别）</label>
                        <p className="mt-1 text-xs text-muted-foreground">这里保留给识别模型看的文本特征；样图管理已单独放到 Embedding tab。</p>
                      </div>
                      <label className="flex items-center gap-1.5 text-xs text-purple-600 cursor-pointer hover:text-purple-700 transition-colors whitespace-nowrap">
                        <ImagePlus className="w-3.5 h-3.5" />
                        {generatingDesc ? '生成中...' : '上传样图生成'}
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
                      value={form.description}
                      onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                      rows={4}
                      placeholder="描述菜品的颜色、形状、质地等视觉特征，帮助AI更准确识别..."
                      className="mt-3 w-full px-3 py-2 text-sm bg-white border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 resize-none"
                    />
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

                <Tabs.Content value="samples" className="focus:outline-none">
                  <div className="border border-dashed border-border rounded-xl p-4 bg-secondary/30">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <label className="text-xs font-medium text-muted-foreground">Embedding 样图</label>
                        <p className="mt-1 text-xs text-muted-foreground">
                          建议上传真实出餐图而不是摆拍图，后续可直接用于 embedding 检索。最多 {MAX_SAMPLE_IMAGES} 张。
                        </p>
                      </div>
                      <label className="inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs bg-white border border-border rounded-lg cursor-pointer hover:bg-secondary transition-colors whitespace-nowrap">
                        <ImagePlus className="w-3.5 h-3.5" />
                        添加样图
                        <input
                          ref={sampleImagesInputRef}
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          multiple
                          onChange={handleSelectSampleImages}
                          className="hidden"
                        />
                      </label>
                    </div>

                    {(existingSampleImages.length > 0 || pendingSampleImages.length > 0) ? (
                      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
                        {existingSampleImages.map(image => (
                          <div key={`existing-${image.id}`} className="bg-white border border-border rounded-lg overflow-hidden">
                            <div className="aspect-square bg-secondary overflow-hidden">
                              {image.image_url ? (
                                <img src={image.image_url} alt={image.original_filename || `样图-${image.id}`} className="w-full h-full object-cover" />
                              ) : (
                                <div className="w-full h-full flex items-center justify-center text-xs text-muted-foreground">无预览</div>
                              )}
                            </div>
                            <div className="p-2 space-y-2">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[11px] text-muted-foreground truncate">{image.original_filename || `样图 ${image.id}`}</span>
                                {image.is_cover && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-700">封面</span>}
                              </div>
                              <div className="flex items-center justify-between gap-2">
                                <span className={cn('text-[10px] px-1.5 py-0.5 rounded-full', EMBEDDING_STATUS_COLORS[image.embedding_status] || 'bg-secondary text-muted-foreground')}>
                                  {EMBEDDING_STATUS_LABELS[image.embedding_status] || image.embedding_status}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => deleteExistingSampleImage(image.id)}
                                  disabled={deletingImageId === image.id}
                                  className="text-[11px] text-red-600 hover:text-red-700 disabled:opacity-50"
                                >
                                  {deletingImageId === image.id ? '删除中...' : '删除'}
                                </button>
                              </div>
                            </div>
                          </div>
                        ))}
                        {pendingSampleImages.map(image => (
                          <div key={`pending-${image.id}`} className="bg-white border border-border rounded-lg overflow-hidden">
                            <div className="aspect-square bg-secondary overflow-hidden">
                              <img src={image.previewUrl} alt={image.file.name} className="w-full h-full object-cover" />
                            </div>
                            <div className="p-2 space-y-2">
                              <div className="text-[11px] text-muted-foreground truncate">{image.file.name}</div>
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700">待上传</span>
                                <button
                                  type="button"
                                  onClick={() => removePendingSampleImage(image.id)}
                                  className="text-[11px] text-red-600 hover:text-red-700"
                                >
                                  移除
                                </button>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-4 rounded-lg border border-border bg-white px-3 py-6 text-center text-xs text-muted-foreground">
                        还没有上传样图
                      </div>
                    )}
                  </div>
                </Tabs.Content>
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
