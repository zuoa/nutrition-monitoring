import { useEffect, useState, useRef } from 'react'
import { Plus, Search, Edit2, Trash2, ChevronLeft, ChevronRight, X, Sparkles, Download, Upload, ImagePlus, Wand2 } from 'lucide-react'
import { dishApi } from '@/api/client'
import { fmtDate, cn } from '@/lib/utils'
import type { Dish, DishCategory } from '@/types'
import toast from 'react-hot-toast'

const CATEGORIES: DishCategory[] = ['主食', '荤菜', '素菜', '汤', '其他']
const CATEGORY_COLORS: Record<string, string> = {
  '主食': 'bg-amber-100 text-amber-700',
  '荤菜': 'bg-red-100 text-red-700',
  '素菜': 'bg-green-100 text-green-700',
  '汤': 'bg-blue-100 text-blue-700',
  '其他': 'bg-gray-100 text-gray-600',
}

interface DishFormData {
  name: string; description: string; ingredients: string; price: string; category: string;
  weight: string;
  calories: string; protein: string; fat: string; carbohydrate: string;
  sodium: string; fiber: string;
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
  const [showConfirmModal, setShowConfirmModal] = useState(false)
  const [pendingAiData, setPendingAiData] = useState<any>(null)
  const [batchAnalyzing, setBatchAnalyzing] = useState(false)
  const [batchProgress, setBatchProgress] = useState<{ current: number; total: number; dishName: string } | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const descImageInputRef = useRef<HTMLInputElement>(null)

  const PAGE_SIZE = 15

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

  const openCreate = () => { setEditing(null); setForm(EMPTY_FORM); setShowModal(true) }
  const openEdit = (d: Dish) => {
    setEditing(d)
    setForm({
      name: d.name, description: d.description || '', ingredients: d.ingredients || '', price: String(d.price),
      category: d.category, weight: String(d.weight ?? 100),
      calories: String(d.calories ?? ''), protein: String(d.protein ?? ''),
      fat: String(d.fat ?? ''), carbohydrate: String(d.carbohydrate ?? ''),
      sodium: String(d.sodium ?? ''), fiber: String(d.fiber ?? ''),
    })
    setShowModal(true)
  }

  // Check if form has nutrition data
  const hasNutritionData = () => {
    return form.calories || form.protein || form.fat || form.carbohydrate || form.sodium || form.fiber || form.description
  }

  // Apply AI analysis data to form
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

      // If form has existing data, show confirmation modal
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
      if (editing) {
        await dishApi.update(editing.id, payload)
        toast.success('菜品已更新')
      } else {
        await dishApi.create(payload)
        toast.success('菜品已创建')
      }
      setShowModal(false)
      load()
    } finally {
      setSaving(false)
    }
  }

  const toggleActive = async (d: Dish) => {
    await dishApi.update(d.id, { is_active: !d.is_active })
    toast.success(d.is_active ? '已停用菜品' : '已启用菜品')
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
      const msg = `导入完成：新增 ${data.created_count} 条，更新 ${data.updated_count} 条`
      toast.success(msg)
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
      // 1. 获取所有菜品（分页加载）
      const allDishes: Dish[] = []
      let page = 1
      const pageSize = 100
      while (true) {
        const res = await dishApi.list({ page, page_size: pageSize, active_only: 'true' })
        const items = res.data.data.items as Dish[]
        allDishes.push(...items)
        if (items.length < pageSize) break
        page++
      }

      // 2. 筛选出没有营养成分数据的菜品
      const toAnalyze = allDishes.filter(d =>
        d.calories === null || d.calories === undefined
      )

      if (toAnalyze.length === 0) {
        toast.success('所有菜品都已分析过')
        return
      }

      // 3. 逐个调用分析 API
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

        // 每次请求后短暂延迟，避免请求过快
        if (i < toAnalyze.length - 1) {
          await new Promise(r => setTimeout(r, 300))
        }
      }

      // 4. 显示结果
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

      {/* Batch Analysis Progress Bar */}
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

      {/* Filters */}
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
          {CATEGORIES.map(c => (
            <button
              key={c}
              onClick={() => { setCategory(c); setPage(1) }}
              className={cn('px-3 py-1.5 text-xs rounded-md transition-colors', category === c ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}
            >{c}</button>
          ))}
        </div>
      </div>

      {/* Table */}
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
            ) : dishes.map(d => (
              <tr key={d.id} className={!d.is_active ? 'opacity-40' : ''}>
                <td>
                  <span className="font-medium">{d.name}</span>
                  {d.description && <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-48">{d.description}</p>}
                </td>
                <td>
                  <span className={cn('text-xs px-2 py-0.5 rounded-full font-medium', CATEGORY_COLORS[d.category])}>{d.category}</span>
                </td>
                <td><span className="font-mono">¥{d.price.toFixed(2)}</span></td>
                <td><span className="font-mono">{d.calories ?? '—'}</span></td>
                <td><span className="font-mono">{d.protein ?? '—'}</span></td>
                <td>
                  <span className={cn('text-xs', d.is_active ? 'text-health-green' : 'text-muted-foreground')}>
                    {d.is_active ? '启用' : '停用'}
                  </span>
                </td>
                <td className="text-xs text-muted-foreground font-mono">{fmtDate(d.updated_at)}</td>
                <td>
                  <div className="flex items-center gap-1">
                    <button onClick={() => openEdit(d)} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
                      <Edit2 className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                    <button onClick={() => toggleActive(d)} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
                      <Trash2 className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
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

      {/* Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-lg shadow-xl animate-fade-in max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-border">
              <h3 className="font-medium">{editing ? '编辑菜品' : '新增菜品'}</h3>
              <button onClick={() => setShowModal(false)} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 space-y-4 overflow-y-auto max-h-[70vh]">
              {/* 基本信息区 */}
              <div className="grid grid-cols-2 gap-4">
                <div className="col-span-2">
                  <label className="text-xs font-medium text-muted-foreground">菜品名称 *</label>
                  <div className="mt-1 flex gap-2">
                    <input
                      value={form.name}
                      onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                      className="flex-1 px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                    />
                    <div className="flex items-center gap-1 px-2 bg-secondary rounded-lg border border-border">
                      <input
                        type="number"
                        value={form.weight}
                        onChange={e => setForm(f => ({ ...f, weight: e.target.value }))}
                        className="w-14 text-sm bg-transparent text-right focus:outline-none"
                        placeholder="100"
                      />
                      <span className="text-xs text-muted-foreground">g</span>
                    </div>
                    <button
                      onClick={handleAnalyze}
                      disabled={analyzing || !form.name.trim()}
                      className="flex items-center gap-1.5 px-3 py-2 text-sm bg-purple-100 text-purple-700 rounded-lg hover:bg-purple-200 transition-colors disabled:opacity-50 whitespace-nowrap"
                    >
                      <Sparkles className="w-4 h-4" />
                      {analyzing ? '分析中...' : 'AI分析'}
                    </button>
                  </div>
                </div>
                <div className="col-span-2">
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
                  <select value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
                    className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20">
                    {CATEGORIES.map(c => <option key={c}>{c}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">单价(元) *</label>
                  <input type="number" value={form.price} onChange={e => setForm(f => ({ ...f, price: e.target.value }))}
                    className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20" />
                </div>
              </div>

              {/* 描述和营养成分 */}
              <div className="space-y-4">
                <div>
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-medium text-muted-foreground">视觉描述（用于AI图像识别）</label>
                    <label className="flex items-center gap-1.5 text-xs text-purple-600 cursor-pointer hover:text-purple-700 transition-colors">
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
                    rows={2}
                    placeholder="描述菜品的颜色、形状、质地等视觉特征，帮助AI更准确识别..."
                    className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 resize-none"
                  />
                </div>
                <div className="border-t border-border pt-4">
                  <div className="flex items-center justify-between mb-3">
                    <label className="text-xs font-medium text-muted-foreground">营养成分（每100g）</label>
                  </div>
                  <div className="grid grid-cols-3 gap-3">
                    {[
                      { key: 'calories', label: '热量 kcal' },
                      { key: 'protein', label: '蛋白质 g' },
                      { key: 'fat', label: '脂肪 g' },
                      { key: 'carbohydrate', label: '碳水化合物 g' },
                      { key: 'sodium', label: '钠 mg' },
                      { key: 'fiber', label: '膳食纤维 g' },
                    ].map(({ key, label }) => (
                      <div key={key}>
                        <label className="text-xs text-muted-foreground">{label}</label>
                        <input
                          type="number"
                          value={(form as any)[key]}
                          onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                          className="mt-1 w-full px-2 py-1.5 text-sm bg-background border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-foreground/20"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
            <div className="flex gap-3 p-5 border-t border-border">
              <button onClick={() => setShowModal(false)} className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors">取消</button>
              <button onClick={save} disabled={saving} className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50">
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Overwrite Modal */}
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
