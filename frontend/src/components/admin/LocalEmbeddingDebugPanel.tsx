import { useEffect, useState, type ReactNode } from 'react'
import { Database, FileJson, ImageUp, RefreshCw, ScanSearch, SendHorizontal, Target, X } from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import toast from 'react-hot-toast'

import { adminApi, menuApi } from '@/api/client'
import { cn, isLocalRecognitionMode } from '@/lib/utils'
import type { Dish } from '@/types'

const IMAGE_ACCEPT = {
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/png': ['.png'],
  'image/webp': ['.webp'],
  'image/bmp': ['.bmp'],
}

type ImportedMenuInfo = {
  date: string
  count: number
  isDefault: boolean
}

type CandidateDish = Pick<Dish, 'id' | 'name' | 'description'>

type LocalEmbeddingHit = {
  dish_id?: number
  dish_name?: string
  similarity?: number
  score?: number
}

type LocalEmbeddingRegionResult = {
  index: number
  bbox: { x1: number; y1: number; x2: number; y2: number } | null
  embedding_dim?: number
  recall_hits?: LocalEmbeddingHit[]
  reranked_hits?: LocalEmbeddingHit[]
}

type LocalEmbeddingRegion = {
  index: number
  bbox: { x1: number; y1: number; x2: number; y2: number } | null
  confidence?: number
  source?: string
}

type LocalEmbeddingTestResult = {
  filename: string
  content_type: string
  candidate_source: string
  candidate_count: number
  candidate_dishes: CandidateDish[]
  recognized_dishes: Array<{ name: string; confidence: number }>
  regions: LocalEmbeddingRegion[]
  region_results: LocalEmbeddingRegionResult[]
  detector_backend: string
  model_version: string
  notes: string
  raw_response: Record<string, any> | null
  timings_ms?: { total?: number }
}

type OverlayBox = {
  key: string
  label: string
  source: string
  confidence?: number
  left: number
  top: number
  width: number
  height: number
}

const formatDateForApi = (date: Date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const formatDishList = (dishes: CandidateDish[]) => {
  if (!dishes.length) return '未显式指定候选菜品，将由后端使用全部启用菜品。'
  return dishes.map((dish) => {
    const description = String(dish.description || '').trim()
    return description ? `- ${dish.name}（${description}）` : `- ${dish.name}`
  }).join('\n')
}

const formatDebugJson = (value: unknown): string => {
  if (value === null || value === undefined) return 'null'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const resolveOverlayBoxes = (
  result: LocalEmbeddingTestResult | null,
  naturalSize: { width: number; height: number },
): OverlayBox[] => {
  if (!result || !naturalSize.width || !naturalSize.height) return []

  return (result.regions || []).flatMap((region) => {
    const bbox = region.bbox
    if (!bbox) return []
    const width = bbox.x2 - bbox.x1
    const height = bbox.y2 - bbox.y1
    if (width <= 0 || height <= 0) return []

    const regionResult = (result.region_results || []).find((item) => item.index === region.index)
    const topHit = regionResult?.reranked_hits?.[0] || regionResult?.recall_hits?.[0]
    const label = String(topHit?.dish_name || `区域 ${region.index}`)
    return [{
      key: `region-${region.index}-${bbox.x1}-${bbox.y1}`,
      label,
      source: String(region.source || result.detector_backend || 'detector'),
      confidence: Number.isFinite(Number(region.confidence)) ? Number(region.confidence) : undefined,
      left: (bbox.x1 / naturalSize.width) * 100,
      top: (bbox.y1 / naturalSize.height) * 100,
      width: (width / naturalSize.width) * 100,
      height: (height / naturalSize.height) * 100,
    }]
  })
}

const formatHitScore = (hit: LocalEmbeddingHit) => {
  if (typeof hit.score === 'number') return `${(hit.score * 100).toFixed(1)}%`
  if (typeof hit.similarity === 'number') return `${(hit.similarity * 100).toFixed(1)}%`
  return '—'
}

export default function LocalEmbeddingDebugPanel({ config }: { config: Record<string, any> }) {
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [imagePreviewUrl, setImagePreviewUrl] = useState('')
  const [imageNaturalSize, setImageNaturalSize] = useState({ width: 0, height: 0 })
  const [loading, setLoading] = useState(false)
  const [defaultsLoading, setDefaultsLoading] = useState(false)
  const [result, setResult] = useState<LocalEmbeddingTestResult | null>(null)
  const [candidateDishes, setCandidateDishes] = useState<CandidateDish[]>([])
  const [importedMenuInfo, setImportedMenuInfo] = useState<ImportedMenuInfo | null>(null)

  const overlayBoxes = resolveOverlayBoxes(result, imageNaturalSize)
  const localRecognitionModeEnabled = isLocalRecognitionMode(String(config.dish_recognition_mode || ''))

  const loadTodayMenu = async () => {
    setDefaultsLoading(true)
    const today = formatDateForApi(new Date())
    try {
      const res = await menuApi.get(today)
      const menu = res.data.data || {}
      const dishes = Array.isArray(menu.dishes) ? menu.dishes : []
      setCandidateDishes(dishes.map((dish: Dish) => ({
        id: dish.id,
        name: dish.name,
        description: dish.description,
      })))
      setImportedMenuInfo({
        date: today,
        count: dishes.length,
        isDefault: Boolean(menu.is_default),
      })
      setResult(null)
      toast.success(`${today} 菜单已导入候选菜品`)
    } catch {
      toast.error('今日菜单导入失败')
    } finally {
      setDefaultsLoading(false)
    }
  }

  useEffect(() => {
    void loadTodayMenu()
  }, [])

  useEffect(() => {
    if (!imageFile) {
      setImagePreviewUrl('')
      setImageNaturalSize({ width: 0, height: 0 })
      return undefined
    }
    const nextUrl = URL.createObjectURL(imageFile)
    setImagePreviewUrl(nextUrl)
    return () => URL.revokeObjectURL(nextUrl)
  }, [imageFile])

  const {
    getRootProps,
    getInputProps,
    isDragActive,
  } = useDropzone({
    onDrop: (files) => {
      if (!files.length) return
      setImageFile(files[0])
      setResult(null)
    },
    onDropRejected: () => {
      toast.error('请上传 JPG、PNG、WEBP 或 BMP 图片')
    },
    accept: IMAGE_ACCEPT,
    maxFiles: 1,
    multiple: false,
  })

  const clearImage = () => {
    setImageFile(null)
    setResult(null)
  }

  const useAllActiveDishes = () => {
    setCandidateDishes([])
    setImportedMenuInfo(null)
    setResult(null)
    toast.success('已切换为全部启用菜品模式')
  }

  const submit = async () => {
    if (!localRecognitionModeEnabled) {
      toast.error('当前识别模式不是 local_embedding')
      return
    }
    if (!imageFile) {
      toast.error('请先上传测试图片')
      return
    }

    setLoading(true)
    try {
      const res = await adminApi.localEmbeddingTest(imageFile, {
        candidateDishIds: candidateDishes.map((dish) => dish.id),
      })
      setResult(res.data.data)
      toast.success('Embedding 调试完成')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
      <div className="space-y-4">
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border bg-[linear-gradient(135deg,rgba(59,130,246,0.08),rgba(15,23,42,0.02))] px-5 py-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 rounded-xl border border-border bg-background p-2.5">
                <ScanSearch className="h-4 w-4 text-primary" />
              </div>
              <div>
                <h2 className="text-sm font-medium">Local Embedding 调试工作台</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  上传单张图片，直接走正式识别同一条 `DishRecognitionService` 链路，便于核对检测框、召回候选和最终识别结果。
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                    mode: {String(config.dish_recognition_mode || 'unknown')}
                  </span>
                  <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                    retrieval: {String(config.retrieval_api_base_url || '未配置')}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4 p-5">
            {!localRecognitionModeEnabled && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
                当前系统识别模式不是 `local_embedding`，调试结果将没有参考价值。请先切回 `local_embedding` 模式。
              </div>
            )}

            <div
              {...getRootProps()}
              className={cn(
                'group rounded-2xl border border-dashed p-4 transition-colors',
                isDragActive ? 'border-primary bg-primary/5' : 'border-border bg-secondary/30 hover:border-primary/30 hover:bg-secondary/60',
              )}
            >
              <input {...getInputProps()} />
              {imagePreviewUrl ? (
                <div className="space-y-3">
                  <div className="overflow-hidden rounded-xl border border-border bg-background">
                    <div className="flex justify-center bg-secondary/20 p-2">
                      <div className="relative inline-block">
                        <img
                          src={imagePreviewUrl}
                          alt="Local embedding test preview"
                          className="block max-h-[280px] max-w-full"
                          onLoad={(event) => {
                            setImageNaturalSize({
                              width: event.currentTarget.naturalWidth,
                              height: event.currentTarget.naturalHeight,
                            })
                          }}
                        />
                        {overlayBoxes.length > 0 && (
                          <div className="pointer-events-none absolute inset-0">
                            {overlayBoxes.map((item) => (
                              <div
                                key={item.key}
                                className="absolute rounded-lg border-2 border-sky-500/90 bg-sky-500/10"
                                style={{
                                  left: `${item.left}%`,
                                  top: `${item.top}%`,
                                  width: `${item.width}%`,
                                  height: `${item.height}%`,
                                }}
                              >
                                <div className="absolute left-0 top-0 -translate-y-full rounded-md bg-sky-600 px-2 py-1 text-[10px] leading-none text-white shadow-sm">
                                  {item.label}
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
                            clearImage()
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
                      <div className="truncate text-sm font-medium">{imageFile?.name}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {imageFile ? `${(imageFile.size / 1024 / 1024).toFixed(2)} MB` : ''}
                      </div>
                    </div>
                    <div className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-mono text-muted-foreground">
                      单图识别
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
                    支持 JPG、PNG、WEBP、BMP。建议使用和正式识别相同的原图。
                  </div>
                </div>
              )}
            </div>

            <div className="rounded-2xl border border-border bg-secondary/20 p-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-medium text-foreground">候选菜品</div>
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    {candidateDishes.length
                      ? `当前显式指定 ${candidateDishes.length} 道候选菜品`
                      : '当前未显式指定候选菜品，后端会自动使用全部启用菜品'}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={loadTodayMenu}
                    disabled={defaultsLoading}
                    className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                  >
                    {defaultsLoading ? '导入中...' : '导入今日菜单'}
                  </button>
                  <button
                    type="button"
                    onClick={useAllActiveDishes}
                    className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                  >
                    使用全部启用菜品
                  </button>
                </div>
              </div>
              {importedMenuInfo && (
                <div className="mb-2 text-[11px] text-muted-foreground">
                  已导入 {importedMenuInfo.date} 菜单，候选菜品 {importedMenuInfo.count} 道。
                  {importedMenuInfo.isDefault ? ' 当前日期未单独配置菜单，因此使用的是默认菜单内容。' : ''}
                </div>
              )}
              <pre className="max-h-[180px] overflow-auto rounded-xl bg-background px-3 py-3 text-xs leading-6 text-foreground">
                {formatDishList(candidateDishes)}
              </pre>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={submit}
                disabled={loading}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                <SendHorizontal className={cn('h-4 w-4', loading && 'animate-pulse')} />
                {loading ? '识别中...' : '发送测试请求'}
              </button>
              <button
                type="button"
                onClick={loadTodayMenu}
                disabled={defaultsLoading}
                className="rounded-xl border border-border bg-background px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                {defaultsLoading ? '加载中...' : '重新导入菜单'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard
            icon={<Database className="h-4 w-4" />}
            label="模型版本"
            value={result?.model_version || String(config.local_recognition_model_version || '—')}
          />
          <MetricCard
            icon={<Target className="h-4 w-4" />}
            label="Detector"
            value={result?.detector_backend || '—'}
          />
          <MetricCard
            icon={<ScanSearch className="h-4 w-4" />}
            label="候选数"
            value={String(result?.candidate_count ?? (candidateDishes.length > 0 ? candidateDishes.length : '—'))}
          />
          <MetricCard
            icon={<RefreshCw className="h-4 w-4" />}
            label="耗时"
            value={result?.timings_ms?.total ? `${result.timings_ms.total} ms` : '—'}
          />
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Target className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">识别结果</h3>
          </div>
          {result ? (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                {(result.recognized_dishes || []).length > 0 ? result.recognized_dishes.map((item, index) => (
                  <div key={`recognized-${index}-${item.name}`} className="rounded-xl bg-secondary/40 px-3 py-2 text-sm">
                    <span className="font-medium">{item.name}</span>
                    <span className="ml-2 text-muted-foreground">{(Number(item.confidence || 0) * 100).toFixed(1)}%</span>
                  </div>
                )) : (
                  <EmptyState text="当前没有识别出高于阈值的菜品。" />
                )}
              </div>
              {result.notes ? (
                <div className="rounded-xl border border-border bg-secondary/20 px-3 py-2 text-sm text-muted-foreground">
                  {result.notes}
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState text="发起测试后，这里会显示最终识别出的菜品列表和诊断说明。" />
          )}
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ScanSearch className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">区域调试</h3>
          </div>
          {result && result.region_results?.length ? (
            <div className="space-y-3">
              {result.region_results.map((item) => (
                <div key={`region-result-${item.index}`} className="rounded-xl border border-border bg-secondary/20 p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="rounded-full bg-background px-2.5 py-1 font-mono text-[11px] text-muted-foreground">
                      区域 {item.index}
                    </span>
                    {item.embedding_dim ? (
                      <span className="text-[11px] text-muted-foreground">embedding {item.embedding_dim} 维</span>
                    ) : null}
                    {item.bbox ? (
                      <span className="text-[11px] font-mono text-muted-foreground">
                        ({item.bbox.x1}, {item.bbox.y1}) - ({item.bbox.x2}, {item.bbox.y2})
                      </span>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">整图回退</span>
                    )}
                  </div>
                  <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    <HitList title="Recall Hits" hits={item.recall_hits || []} />
                    <HitList title="Reranked Hits" hits={item.reranked_hits || []} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="请求完成后，这里会展示每个区域的召回和 rerank 明细。" />
          )}
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <FileJson className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">原始响应</h3>
          </div>
          {result ? (
            <pre className="max-h-[520px] overflow-auto rounded-xl bg-[linear-gradient(180deg,rgba(15,23,42,0.96),rgba(15,23,42,0.88))] p-4 text-xs leading-6 text-slate-100">
              {formatDebugJson(result.raw_response)}
            </pre>
          ) : (
            <EmptyState text="还没有请求记录。上传图片并发送测试请求后，这里会展示正式识别链路返回的原始 JSON。" />
          )}
        </div>
      </div>
    </div>
  )
}

function MetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
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

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-secondary/20 px-4 py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  )
}

function HitList({ title, hits }: { title: string; hits: LocalEmbeddingHit[] }) {
  return (
    <div className="rounded-xl bg-background px-3 py-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground">{title}</div>
      {hits.length ? (
        <div className="mt-2 space-y-2">
          {hits.slice(0, 5).map((hit, index) => (
            <div key={`${title}-${index}-${hit.dish_id || hit.dish_name || 'unknown'}`} className="flex items-center justify-between gap-3 text-xs">
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{String(hit.dish_name || '未命名')}</div>
                <div className="font-mono text-[11px] text-muted-foreground">dish_id {String(hit.dish_id ?? '—')}</div>
              </div>
              <div className="font-mono text-muted-foreground">{formatHitScore(hit)}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-2 text-xs text-muted-foreground">没有命中结果</div>
      )}
    </div>
  )
}
