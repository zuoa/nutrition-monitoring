import { useEffect, useState } from 'react'
import { RefreshCw, CheckCircle2, ChevronLeft, ChevronRight, RotateCcw, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { analysisApi, consumptionApi } from '@/api/client'
import { fmtDateTime, cn } from '@/lib/utils'
import type { MatchResult, CapturedImage } from '@/types'
import toast from 'react-hot-toast'

const STATUS_STYLES: Record<string, { label: string; class: string }> = {
  matched: { label: '已匹配', class: 'text-health-green bg-health-green/10' },
  time_matched_only: { label: '待确认', class: 'text-health-amber bg-health-amber/10' },
  unmatched_record: { label: '无图片', class: 'text-muted-foreground bg-secondary' },
  confirmed: { label: '已确认', class: 'text-health-green bg-health-green/10' },
}

const IMAGE_STATUS_LABEL: Record<string, string> = {
  pending: '待处理',
  identified: '已识别',
  matched: '已匹配',
  error: '错误',
}

const resolveImageUrl = (img?: Pick<CapturedImage, 'image_url' | 'image_path'> | null) => {
  if (!img) return ''
  if (img.image_url) return img.image_url
  if (!img.image_path) return ''

  const normalizedPath = img.image_path.replace(/\\/g, '/')
  if (normalizedPath.startsWith('http://') || normalizedPath.startsWith('https://') || normalizedPath.startsWith('/images/')) {
    return normalizedPath
  }
  const marker = '/data/images/'
  const markerIndex = normalizedPath.indexOf(marker)
  if (markerIndex >= 0) {
    return `/images/${normalizedPath.slice(markerIndex + marker.length)}`
  }
  return normalizedPath
}

export default function MatchesPage() {
  const navigate = useNavigate()
  const [view, setView] = useState<'records' | 'images'>('records')
  const [matches, setMatches] = useState<MatchResult[]>([])
  const [unmatchedImages, setUnmatchedImages] = useState<MatchResult[]>([])
  const [activeMatchPreview, setActiveMatchPreview] = useState<MatchResult | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [confirmingId, setConfirmingId] = useState<number | null>(null)
  const [recognizingImageId, setRecognizingImageId] = useState<number | null>(null)
  const [dateFilter, setDateFilter] = useState(new Date().toISOString().split('T')[0])
  const [previewImageUrl, setPreviewImageUrl] = useState<string | null>(null)

  const PAGE_SIZE = 20

  const loadMatches = async () => {
    const res = await consumptionApi.matches({
      page,
      page_size: PAGE_SIZE,
      status: status || undefined,
      date: dateFilter,
    })
    setMatches(res.data.data.items)
    setTotal(res.data.data.total)
  }

  const loadUnmatchedImages = async () => {
    const res = await consumptionApi.unmatchedImages({
      page,
      page_size: PAGE_SIZE,
      date: dateFilter,
    })
    setUnmatchedImages(res.data.data.items)
    setTotal(res.data.data.total)
  }

  const load = async () => {
    setLoading(true)
    try {
      if (view === 'records') {
        await loadMatches()
      } else {
        await loadUnmatchedImages()
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [page, status, dateFilter, view])

  const confirm = async (id: number) => {
    setConfirmingId(id)
    try {
      await consumptionApi.confirmMatch(id)
      toast.success('已手动确认匹配')
      closeMatchPreview()
      load()
    } finally {
      setConfirmingId(null)
    }
  }

  const rematch = async () => {
    await consumptionApi.rematch(dateFilter)
    toast.success('重新匹配任务已提交')
  }

  const openPreview = (imageUrl: string) => {
    if (!imageUrl) return
    setPreviewImageUrl(imageUrl)
  }

  const closePreview = () => {
    setPreviewImageUrl(null)
  }

  const openMatchPreview = (match: MatchResult) => {
    if (!match.image) return
    setActiveMatchPreview(match)
  }

  const closeMatchPreview = () => {
    setActiveMatchPreview(null)
  }

  const rerunImageRecognition = async (imageId: number) => {
    setRecognizingImageId(imageId)
    try {
      const res = await analysisApi.recognizeImage(imageId)
      const updatedImage = res.data.data as CapturedImage
      setUnmatchedImages(prev => prev.map(item => item.image?.id === imageId ? {
        ...item,
        image: updatedImage,
      } : item))
      toast.success('已提交图片识别任务')
    } finally {
      setRecognizingImageId(null)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const statusCounts = matches.reduce<Record<string, number>>((acc, m) => {
    acc[m.status] = (acc[m.status] || 0) + 1
    return acc
  }, {})
  const recognizedDishCount = unmatchedImages.reduce((acc, item) => acc + (item.image?.recognitions?.length || 0), 0)
  const candidateCount = unmatchedImages.filter(item => item.image?.is_candidate).length

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">匹配管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {view === 'records' ? '消费记录主视图' : '图片侧未匹配残留'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={dateFilter}
            onChange={e => { setDateFilter(e.target.value); setPage(1) }}
            className="px-3 py-2 text-sm bg-card border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
          />
          <button onClick={rematch} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RotateCcw className="w-3.5 h-3.5" />重新匹配
          </button>
          <button onClick={load} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
          </button>
        </div>
      </div>

      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-fit mb-5">
        {([
          { key: 'records', label: '消费记录' },
          { key: 'images', label: '未匹配图片' },
        ] as const).map(item => (
          <button
            key={item.key}
            onClick={() => {
              setView(item.key)
              setPage(1)
              if (item.key !== 'records') {
                setStatus('')
              }
            }}
            className={cn(
              'px-4 py-1.5 text-sm rounded-md transition-colors',
              view === item.key ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {view === 'records' ? (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-5">
            {[
              { key: '', label: '全部', count: total },
              { key: 'matched', label: '已匹配', count: undefined },
              { key: 'time_matched_only', label: '待确认', count: undefined },
              { key: 'unmatched_record', label: '无图片', count: undefined },
              { key: 'confirmed', label: '已确认', count: undefined },
            ].map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => { setStatus(key); setPage(1) }}
                className={cn('p-3 rounded-xl border text-left transition-all', status === key ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/20')}
              >
                <div className={cn('text-xs font-medium mb-0.5', STATUS_STYLES[key]?.class?.split(' ')[0] || 'text-foreground')}>{label}</div>
                <div className="text-lg font-mono">{count ?? statusCounts[key] ?? '—'}</div>
              </button>
            ))}
          </div>

          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <table className="data-table min-w-[768px]">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>学生</th>
                  <th>消费时间</th>
                  <th>金额</th>
                  <th>时间偏差</th>
                  <th>金额偏差</th>
                  <th>方式</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={8} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
                {!loading && matches.length === 0 && <tr><td colSpan={8} className="text-center py-12 text-muted-foreground">暂无匹配记录</td></tr>}
                {matches.map(m => {
                  const s = STATUS_STYLES[m.status] || { label: m.status, class: 'text-muted-foreground bg-secondary' }
                  const rec = m.consumption_record
                  return (
                    <tr key={m.id}>
                      <td>
                        <span className={cn('text-xs px-2 py-0.5 rounded-full font-medium', s.class)}>{s.label}</span>
                      </td>
                      <td>
                        <div className="text-sm font-medium">{m.student?.name ?? rec?.student_name ?? '—'}</div>
                        <div className="text-xs text-muted-foreground font-mono">{m.student?.student_no ?? rec?.student_no ?? '—'}</div>
                      </td>
                      <td><span className="text-xs font-mono">{fmtDateTime(rec?.transaction_time)}</span></td>
                      <td><span className="font-mono">¥{rec?.amount?.toFixed(2) ?? '—'}</span></td>
                      <td>
                        <span className={cn('text-xs font-mono', (m.time_diff_seconds ?? 99) > 1 ? 'text-health-amber' : 'text-muted-foreground')}>
                          {m.time_diff_seconds != null ? `${m.time_diff_seconds.toFixed(1)}s` : '—'}
                        </span>
                      </td>
                      <td>
                        <span className={cn('text-xs font-mono', (m.price_diff ?? 99) > 0.5 ? 'text-health-amber' : 'text-muted-foreground')}>
                          {m.price_diff != null ? `¥${m.price_diff.toFixed(2)}` : '—'}
                        </span>
                      </td>
                      <td><span className="text-xs text-muted-foreground">{m.is_manual ? '手动' : '自动'}</span></td>
                      <td>
                        <div className="flex flex-col items-start gap-1.5">
                          {m.image && (
                            <button
                              onClick={() => openMatchPreview(m)}
                              className="text-xs text-muted-foreground hover:underline"
                            >
                              查看图片
                            </button>
                          )}
                          {m.status === 'time_matched_only' && (
                            <button
                              onClick={() => confirm(m.id)}
                              disabled={confirmingId === m.id}
                              className="flex items-center gap-1 text-xs text-health-blue hover:underline disabled:opacity-50"
                            >
                              <CheckCircle2 className="w-3 h-3" />确认
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
            <div className="p-3 rounded-xl border border-border bg-card">
              <div className="text-xs font-medium text-muted-foreground mb-0.5">未匹配图片</div>
              <div className="text-lg font-mono">{total}</div>
            </div>
            <div className="p-3 rounded-xl border border-border bg-card">
              <div className="text-xs font-medium text-muted-foreground mb-0.5">当前页识别结果</div>
              <div className="text-lg font-mono">{recognizedDishCount}</div>
            </div>
            <div className="p-3 rounded-xl border border-border bg-card">
              <div className="text-xs font-medium text-muted-foreground mb-0.5">候选帧</div>
              <div className="text-lg font-mono">{candidateCount}</div>
            </div>
          </div>

          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <table className="data-table min-w-[960px]">
              <thead>
                <tr>
                  <th>图片</th>
                  <th>采集时间</th>
                  <th>通道</th>
                  <th>图片状态</th>
                  <th>识别结果</th>
                  <th>来源</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={7} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
                {!loading && unmatchedImages.length === 0 && <tr><td colSpan={7} className="text-center py-12 text-muted-foreground">暂无未匹配图片</td></tr>}
                {unmatchedImages.map(item => {
                  const img = item.image
                  const imageUrl = resolveImageUrl(img)
                  return (
                    <tr key={item.id}>
                      <td>
                        <div className="flex items-center gap-3">
                          <div className="h-16 w-24 overflow-hidden rounded-lg bg-secondary border border-border flex items-center justify-center">
                            {imageUrl ? (
                              <button
                                type="button"
                                onClick={() => openPreview(imageUrl)}
                                className="h-full w-full"
                              >
                                <img src={imageUrl} alt="unmatched" className="h-full w-full object-cover" />
                              </button>
                            ) : (
                              <span className="text-xs text-muted-foreground">无预览</span>
                            )}
                          </div>
                          <div className="text-xs text-muted-foreground font-mono">
                            #{img?.id ?? item.image_id ?? '—'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="text-sm font-mono">{fmtDateTime(img?.captured_at)}</div>
                        {img?.is_candidate && (
                          <div className="mt-1 inline-flex rounded-full bg-health-amber/15 px-2 py-0.5 text-[11px] font-medium text-health-amber">
                            候选帧
                          </div>
                        )}
                      </td>
                      <td><span className="font-mono text-sm">{img?.channel_id ? `CH${img.channel_id}` : '—'}</span></td>
                      <td>
                        <span className="text-xs text-muted-foreground">
                          {img?.status ? IMAGE_STATUS_LABEL[img.status] || img.status : '—'}
                        </span>
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {img?.recognitions && img.recognitions.length > 0 ? img.recognitions.map(rec => (
                            <span
                              key={rec.id}
                              className={cn(
                                'rounded-full px-2 py-0.5 text-xs',
                                rec.is_low_confidence ? 'bg-health-amber/10 text-health-amber' : 'bg-secondary text-foreground',
                              )}
                            >
                              {rec.dish_name_raw}
                            </span>
                          )) : (
                            <span className="text-xs text-muted-foreground">暂无识别结果</span>
                          )}
                        </div>
                      </td>
                      <td>
                        <span className="text-xs text-muted-foreground break-all">
                          {img?.source_video || '—'}
                        </span>
                      </td>
                      <td>
                        <div className="flex flex-col items-start gap-1.5">
                          {imageUrl && (
                            <button
                              onClick={() => openPreview(imageUrl)}
                              className="text-xs text-muted-foreground hover:underline"
                            >
                              预览
                            </button>
                          )}
                          {img?.id && (
                            <button
                              onClick={() => rerunImageRecognition(img.id)}
                              disabled={recognizingImageId === img.id}
                              className="text-xs text-muted-foreground hover:underline disabled:opacity-50"
                            >
                              {recognizingImageId === img.id ? '提交中...' : '重新识别'}
                            </button>
                          )}
                          {img?.id && (
                            <button
                              onClick={() => navigate(`/analysis?review_image_id=${img.id}`)}
                              className="text-xs text-health-blue hover:underline"
                            >
                              去复核
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-xs text-muted-foreground">共 {total} 条</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
            <span className="text-xs font-mono px-2">{page} / {totalPages}</span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors"><ChevronRight className="w-4 h-4" /></button>
          </div>
        </div>
      )}

      {previewImageUrl && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={closePreview}
        >
          <button
            type="button"
            onClick={closePreview}
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
          >
            <X className="w-5 h-5" />
          </button>
          <div
            className="relative flex max-h-[92vh] max-w-[92vw] items-center justify-center overflow-hidden rounded-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={previewImageUrl}
              alt="Preview"
              className="max-h-[92vh] max-w-[92vw] rounded-xl bg-white object-contain shadow-2xl"
            />
          </div>
        </div>
      )}

      {activeMatchPreview?.image && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
          onClick={closeMatchPreview}
        >
          <div
            className="w-full max-w-4xl rounded-xl border border-border bg-card shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h3 className="text-sm font-medium">匹配图片详情</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {fmtDateTime(activeMatchPreview.image.captured_at)} · {activeMatchPreview.image.channel_id ? `CH${activeMatchPreview.image.channel_id}` : '—'}
                </p>
              </div>
              <button onClick={closeMatchPreview} className="rounded-md p-1 hover:bg-secondary">
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(280px,0.8fr)]">
              <div className="overflow-hidden rounded-xl border border-border bg-secondary">
                {resolveImageUrl(activeMatchPreview.image) ? (
                  <img
                    src={resolveImageUrl(activeMatchPreview.image)}
                    alt="matched"
                    className="max-h-[70vh] w-full object-contain"
                  />
                ) : (
                  <div className="flex h-[320px] items-center justify-center text-sm text-muted-foreground">无图片预览</div>
                )}
              </div>
              <div className="space-y-4">
                <div className="rounded-xl border border-border bg-background p-4">
                  <p className="text-xs font-medium text-muted-foreground mb-2">匹配信息</p>
                  <div className="space-y-2 text-sm">
                    <div>状态：{STATUS_STYLES[activeMatchPreview.status]?.label || activeMatchPreview.status}</div>
                    <div>消费金额：{activeMatchPreview.consumption_record?.amount != null ? `¥${activeMatchPreview.consumption_record.amount.toFixed(2)}` : '—'}</div>
                    <div>图片菜价合计：{activeMatchPreview.image_price_total != null ? `¥${activeMatchPreview.image_price_total.toFixed(2)}` : '—'}</div>
                    <div>时间偏差：{activeMatchPreview.time_diff_seconds != null ? `${activeMatchPreview.time_diff_seconds.toFixed(1)}s` : '—'}</div>
                    <div>金额偏差：{activeMatchPreview.price_diff != null ? `¥${activeMatchPreview.price_diff.toFixed(2)}` : '—'}</div>
                    <div>图片状态：{IMAGE_STATUS_LABEL[activeMatchPreview.image.status] || activeMatchPreview.image.status}</div>
                  </div>
                </div>
                <div className="rounded-xl border border-border bg-background p-4">
                  <p className="text-xs font-medium text-muted-foreground mb-2">识别结果</p>
                  <div className="flex flex-wrap gap-1.5">
                    {activeMatchPreview.image.recognitions && activeMatchPreview.image.recognitions.length > 0 ? activeMatchPreview.image.recognitions.map(rec => (
                      <span
                        key={rec.id}
                        className={cn(
                          'rounded-full px-2 py-1 text-xs',
                          rec.is_low_confidence ? 'bg-health-amber/10 text-health-amber' : 'bg-secondary text-foreground',
                        )}
                      >
                        {rec.dish_name_raw}
                      </span>
                    )) : (
                      <span className="text-xs text-muted-foreground">暂无识别结果</span>
                    )}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => openPreview(resolveImageUrl(activeMatchPreview.image))}
                    className="rounded-lg bg-secondary px-3 py-2 text-sm hover:bg-secondary/80"
                  >
                    大图预览
                  </button>
                  {activeMatchPreview.status === 'time_matched_only' && (
                    <button
                      onClick={() => confirm(activeMatchPreview.id)}
                      disabled={confirmingId === activeMatchPreview.id}
                      className="rounded-lg bg-secondary px-3 py-2 text-sm hover:bg-secondary/80 disabled:opacity-50"
                    >
                      {confirmingId === activeMatchPreview.id ? '确认中...' : '直接确认'}
                    </button>
                  )}
                  {activeMatchPreview.image.id && (
                    <button
                      onClick={() => navigate(`/analysis?review_image_id=${activeMatchPreview.image!.id}`)}
                      className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                    >
                      去复核
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
