import { useEffect, useState } from 'react'
import { Play, RefreshCw, CheckCircle2, AlertTriangle, Clock, X, ChevronLeft, ChevronRight, Eye } from 'lucide-react'
import { analysisApi, dishApi } from '@/api/client'
import { fmtDateTime, cn } from '@/lib/utils'
import type { TaskLog, CapturedImage, Dish } from '@/types'
import toast from 'react-hot-toast'

const STATUS_STYLE: Record<string, string> = {
  running: 'text-health-blue',
  success: 'text-health-green',
  failed: 'text-health-red',
  partial: 'text-health-amber',
  pending: 'text-muted-foreground',
  identified: 'text-health-blue',
  matched: 'text-health-green',
  error: 'text-health-red',
}

const STATUS_LABEL: Record<string, string> = {
  running: '运行中', success: '完成', failed: '失败', partial: '部分成功',
  pending: '待处理', identified: '已识别', matched: '已匹配', error: '错误',
}

export default function AnalysisPage() {
  const [tab, setTab] = useState<'tasks' | 'images'>('tasks')
  const [tasks, setTasks] = useState<TaskLog[]>([])
  const [images, setImages] = useState<CapturedImage[]>([])
  const [imagesTotal, setImagesTotal] = useState(0)
  const [imagePage, setImagePage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [today] = useState(new Date().toISOString().split('T')[0])
  const [reviewModal, setReviewModal] = useState<CapturedImage | null>(null)
  const [allDishes, setAllDishes] = useState<Dish[]>([])
  const [reviewDishIds, setReviewDishIds] = useState<number[]>([])
  const [saving, setSaving] = useState(false)

  const loadTasks = async () => {
    setLoading(true)
    try {
      const res = await analysisApi.tasks({ page_size: 20 })
      setTasks(res.data.data.items)
    } finally { setLoading(false) }
  }

  const loadImages = async () => {
    setLoading(true)
    try {
      const res = await analysisApi.images({ page: imagePage, page_size: 20, date: today, status: statusFilter || undefined })
      setImages(res.data.data.items)
      setImagesTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    if (tab === 'tasks') loadTasks()
    else loadImages()
  }, [tab, imagePage, statusFilter])

  useEffect(() => {
    dishApi.list({ active_only: 'true', page_size: 100 }).then(res => setAllDishes(res.data.data.items))
  }, [])

  const triggerAnalysis = async () => {
    await analysisApi.triggerAnalysis(today)
    toast.success('已触发今日视频分析任务')
    loadTasks()
  }

  const retryTask = async (id: number) => {
    await analysisApi.retryTask(id)
    toast.success('已提交重试任务')
    loadTasks()
  }

  const openReview = (img: CapturedImage) => {
    setReviewModal(img)
    const current = img.recognitions?.filter(r => !r.is_low_confidence).map(r => r.dish_id).filter(Boolean) as number[]
    setReviewDishIds(current || [])
  }

  const saveReview = async () => {
    if (!reviewModal) return
    setSaving(true)
    try {
      await analysisApi.reviewImage(reviewModal.id, reviewDishIds)
      toast.success('已保存人工复核结果')
      setReviewModal(null)
      loadImages()
    } finally { setSaving(false) }
  }

  const totalImagePages = Math.ceil(imagesTotal / 20)

  return (
    <div className="p-4 sm:p-6 max-w-6xl">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-display">视频分析</h1>
          <p className="text-sm text-muted-foreground mt-0.5">NVR 录像下载 · AI 菜品识别</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => tab === 'tasks' ? loadTasks() : loadImages()} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
          </button>
          <button onClick={triggerAnalysis} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
            <Play className="w-3.5 h-3.5" />触发今日分析
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-fit mb-5">
        {(['tasks', 'images'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 text-sm rounded-md transition-colors', tab === t ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground')}>
            {t === 'tasks' ? '分析任务' : '采集图片'}
          </button>
        ))}
      </div>

      {tab === 'tasks' ? (
        <div className="bg-card border border-border rounded-xl overflow-x-auto">
          <table className="data-table min-w-[768px]">
            <thead><tr><th>任务类型</th><th>日期</th><th>状态</th><th>总数</th><th>成功</th><th>低置信</th><th>失败</th><th>耗时</th><th></th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={9} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
              {!loading && tasks.length === 0 && <tr><td colSpan={9} className="text-center py-12 text-muted-foreground">暂无任务记录</td></tr>}
              {tasks.map(t => {
                const typeLabel: Record<string, string> = { nvr_download: 'NVR 下载', ai_recognition: 'AI 识别', report_gen: '报告生成' }
                const duration = t.started_at && t.finished_at
                  ? `${Math.round((new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()) / 1000)}s`
                  : t.status === 'running' ? '运行中' : '—'
                return (
                  <tr key={t.id}>
                    <td><span className="font-mono text-xs">{typeLabel[t.task_type] || t.task_type}</span></td>
                    <td><span className="font-mono text-xs">{t.task_date || '—'}</span></td>
                    <td><span className={cn('text-xs font-medium', STATUS_STYLE[t.status])}>{STATUS_LABEL[t.status] || t.status}</span></td>
                    <td><span className="font-mono">{t.total_count}</span></td>
                    <td><span className="font-mono text-health-green">{t.success_count}</span></td>
                    <td><span className="font-mono text-health-amber">{t.low_confidence_count}</span></td>
                    <td><span className="font-mono text-health-red">{t.error_count}</span></td>
                    <td><span className="font-mono text-xs text-muted-foreground">{duration}</span></td>
                    <td>
                      {['failed', 'partial'].includes(t.status) && (
                        <button onClick={() => retryTask(t.id)} className="text-xs text-health-blue hover:underline">重试</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          {/* Image filters */}
          <div className="flex gap-1.5 mb-4">
            {['', 'pending', 'identified', 'matched', 'error'].map(s => (
              <button key={s} onClick={() => { setStatusFilter(s); setImagePage(1) }}
                className={cn('px-3 py-1.5 text-xs rounded-md transition-colors', statusFilter === s ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
                {s === '' ? '全部' : STATUS_LABEL[s]}
              </button>
            ))}
          </div>

          {/* Image grid */}
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
            {loading && Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="aspect-video bg-secondary rounded-lg animate-pulse" />
            ))}
            {!loading && images.map(img => (
              <div key={img.id} onClick={() => openReview(img)}
                className="group relative aspect-video bg-secondary rounded-lg overflow-hidden cursor-pointer border border-border hover:border-foreground/30 transition-all">
                <div className="absolute inset-0 flex items-center justify-center">
                  <Eye className="w-6 h-6 text-muted-foreground/50 group-hover:text-muted-foreground transition-colors" />
                </div>
                {/* Status badge */}
                <div className={cn('absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium',
                  img.status === 'matched' ? 'bg-health-green/20 text-health-green' :
                  img.status === 'identified' ? 'bg-health-blue/20 text-health-blue' :
                  img.status === 'error' ? 'bg-health-red/20 text-health-red' :
                  'bg-secondary text-muted-foreground')}>
                  {STATUS_LABEL[img.status]}
                </div>
                {/* Time */}
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-2">
                  <span className="text-[10px] font-mono text-white/80">{fmtDateTime(img.captured_at)}</span>
                </div>
                {/* Dish tags */}
                {img.recognitions && img.recognitions.length > 0 && (
                  <div className="absolute top-1.5 left-1.5 flex flex-wrap gap-0.5">
                    {img.recognitions.slice(0, 2).map((r, i) => (
                      <span key={i} className={cn('px-1 py-0.5 rounded text-[9px]', r.is_low_confidence ? 'bg-health-amber/20 text-health-amber' : 'bg-foreground/60 text-background')}>
                        {r.dish_name_raw}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalImagePages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4">
              <button onClick={() => setImagePage(p => Math.max(1, p - 1))} disabled={imagePage <= 1} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40"><ChevronLeft className="w-4 h-4" /></button>
              <span className="text-xs font-mono">{imagePage} / {totalImagePages}</span>
              <button onClick={() => setImagePage(p => Math.min(totalImagePages, p + 1))} disabled={imagePage >= totalImagePages} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40"><ChevronRight className="w-4 h-4" /></button>
            </div>
          )}
        </>
      )}

      {/* Review modal */}
      {reviewModal && (
        <div className="fixed inset-0 bg-foreground/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-card border border-border rounded-xl w-full max-w-lg shadow-xl animate-fade-in">
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h3 className="font-medium text-sm">人工复核 — {fmtDateTime(reviewModal.captured_at)}</h3>
              <button onClick={() => setReviewModal(null)} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-4">
              {/* Image placeholder */}
              <div className="aspect-video bg-secondary rounded-lg mb-4 flex items-center justify-center">
                <span className="text-xs text-muted-foreground font-mono">{reviewModal.image_path}</span>
              </div>
              {/* AI result */}
              {reviewModal.recognitions && reviewModal.recognitions.length > 0 && (
                <div className="mb-4">
                  <p className="text-xs font-medium text-muted-foreground mb-2">AI 识别结果</p>
                  <div className="flex flex-wrap gap-1.5">
                    {reviewModal.recognitions.map((r, i) => (
                      <span key={i} className={cn('px-2 py-1 rounded-full text-xs', r.is_low_confidence ? 'bg-health-amber/10 text-health-amber border border-health-amber/20' : 'bg-health-green/10 text-health-green border border-health-green/20')}>
                        {r.dish_name_raw} <span className="opacity-60">({(r.confidence * 100).toFixed(0)}%)</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {/* Manual selection */}
              <p className="text-xs font-medium text-muted-foreground mb-2">手动修正（选择实际菜品）</p>
              <div className="grid grid-cols-3 gap-1.5 max-h-48 overflow-y-auto">
                {allDishes.map(dish => {
                  const sel = reviewDishIds.includes(dish.id)
                  return (
                    <button key={dish.id} onClick={() => setReviewDishIds(prev => sel ? prev.filter(id => id !== dish.id) : [...prev, dish.id])}
                      className={cn('px-2 py-1.5 rounded text-xs text-left border transition-colors', sel ? 'border-primary/30 bg-primary/5 font-medium' : 'border-border hover:border-primary/20')}>
                      {dish.name}
                    </button>
                  )
                })}
              </div>
            </div>
            <div className="flex gap-3 p-4 border-t border-border">
              <button onClick={() => setReviewModal(null)} className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg">取消</button>
              <button onClick={saveReview} disabled={saving} className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50">
                {saving ? '保存中...' : '确认修正'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
