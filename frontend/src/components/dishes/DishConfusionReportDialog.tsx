import { useEffect, useRef } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Images,
  Info,
  ScanSearch,
  ShieldCheck,
  X,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { DishConfusionPair, DishConfusionReport } from '@/types'

interface DishConfusionReportDialogProps {
  report: DishConfusionReport
  onClose: () => void
  onInspectDish: (dishId: number) => void | Promise<void>
}

const RISK_STYLE = {
  high: {
    label: '高风险',
    badge: 'border-red-200 bg-red-50 text-red-700',
    rail: 'bg-red-500',
  },
  medium: {
    label: '中风险',
    badge: 'border-amber-200 bg-amber-50 text-amber-700',
    rail: 'bg-amber-500',
  },
} as const

const formatPercent = (value: number) => `${(value * 100).toFixed(1)}%`

function SamplePreview({ pair, side }: { pair: DishConfusionPair; side: 'left' | 'right' }) {
  const item = pair[side]
  return (
    <div className="min-w-0 flex-1">
      <div className="relative aspect-[4/3] overflow-hidden rounded-2xl border border-slate-200 bg-slate-100">
        {item.sample_image_url ? (
          <img
            src={item.sample_image_url}
            alt={`${item.dish_name}最高相似样图`}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-slate-400">
            <Images className="h-6 w-6" />
            <span className="text-xs">样图不可预览</span>
          </div>
        )}
        <span className="absolute left-3 top-3 rounded-full bg-slate-950/75 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-sm">
          {item.category || '未分类'}
        </span>
      </div>
      <div className="mt-3 min-w-0">
        <p className="truncate font-semibold text-slate-950">
          {item.dish_name}
          {item.exists === false
            ? <span className="ml-1 text-xs font-medium text-red-600">（已删除）</span>
            : item.is_active === false && <span className="ml-1 text-xs font-medium text-red-600">（已停用）</span>}
        </p>
        <p className="mt-0.5 truncate text-xs text-slate-500">
          {item.sample_filename || `样图 #${item.sample_image_id || '—'}`} · 共 {item.sample_count} 张
        </p>
      </div>
    </div>
  )
}

function RiskPairCard({ pair, onInspectDish }: { pair: DishConfusionPair; onInspectDish: (dishId: number) => void | Promise<void> }) {
  const style = RISK_STYLE[pair.risk_level]
  const inspectableDishes = [pair.left, pair.right].filter(dish => dish.exists !== false)
  return (
    <article className="[content-visibility:auto] overflow-hidden rounded-[22px] border border-slate-200 bg-white shadow-[0_18px_45px_rgba(15,23,42,0.06)]">
      <div className={cn('h-1.5', style.rail)} />
      <div className="p-4 sm:p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <span className={cn('rounded-full border px-2.5 py-1 text-xs font-semibold', style.badge)}>
            {style.label}
          </span>
          <div className="flex items-baseline gap-2">
            <span className="text-xs text-slate-500">最高相似度</span>
            <span className="font-mono text-xl font-semibold tabular-nums text-slate-950">
              {formatPercent(pair.max_similarity)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-3 sm:gap-5">
          <SamplePreview pair={pair} side="left" />
          <div className="flex shrink-0 flex-col items-center gap-2 text-slate-400">
            <div className="h-px w-5 bg-slate-200 sm:w-8" />
            <ArrowRight className="h-5 w-5" aria-hidden="true" />
            <div className="h-px w-5 bg-slate-200 sm:w-8" />
          </div>
          <SamplePreview pair={pair} side="right" />
        </div>

        <div className="mt-5 grid gap-3 rounded-2xl bg-slate-50 p-3 text-xs text-slate-600 sm:grid-cols-[1fr_auto] sm:items-center">
          <p>
            有 {pair.similar_sample_pair_count} 组跨菜品样图达到预警线。建议先核对上方最相似样图是否错标或裁剪范围过近。
          </p>
          {inspectableDishes.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {inspectableDishes.map(dish => (
                <button
                  key={dish.dish_id}
                  type="button"
                  onClick={() => onInspectDish(dish.dish_id)}
                  className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-400"
                >
                  检查{dish.dish_name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </article>
  )
}

export function DishConfusionReportDialog({ report, onClose, onInspectDish }: DishConfusionReportDialogProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const { summary } = report
  const coverage = summary.total_active_dish_count > 0
    ? summary.indexed_dish_count / summary.total_active_dish_count
    : 0
  const pipelineLabel = report.pipeline === 'visual' ? '纯视觉（SigLIP2 + DINOv3）' : 'Qwen3-VL'

  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose])

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/70 p-2 backdrop-blur-sm sm:p-5">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="dish-confusion-report-title"
        className="flex max-h-[96vh] w-full max-w-6xl flex-col overflow-hidden rounded-[28px] bg-[#f7f9f8] shadow-2xl"
      >
        <header className="relative overflow-hidden bg-slate-950 px-5 py-5 text-white sm:px-7 sm:py-6">
          <div className="absolute inset-y-0 right-0 w-2/5 bg-[radial-gradient(circle_at_center,rgba(16,185,129,0.22),transparent_65%)]" />
          <div className="relative flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-emerald-300">
                <ScanSearch className="h-4 w-4" />
                识别风险体检单
              </div>
              <h2 id="dish-confusion-report-title" className="mt-2 text-xl font-semibold sm:text-2xl">
                菜品混淆分析报告
              </h2>
              <p className="mt-1 text-sm text-slate-400">
                {pipelineLabel} · {new Date(report.generated_at).toLocaleString('zh-CN')}
              </p>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="关闭菜品混淆分析报告"
              className="rounded-xl border border-white/10 bg-white/10 p-2 text-white transition-colors hover:bg-white/20 focus:outline-none focus:ring-2 focus:ring-emerald-300"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="overflow-y-auto px-4 py-5 sm:px-7 sm:py-6">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-red-100 bg-red-50 p-4 text-red-800">
              <p className="text-xs font-medium">高风险菜品对</p>
              <p className="mt-2 font-mono text-3xl font-semibold tabular-nums">{summary.high_risk_pair_count}</p>
              <p className="mt-1 text-xs text-red-600">相似度 ≥ {formatPercent(report.thresholds.high)}</p>
            </div>
            <div className="rounded-2xl border border-amber-100 bg-amber-50 p-4 text-amber-800">
              <p className="text-xs font-medium">中风险菜品对</p>
              <p className="mt-2 font-mono text-3xl font-semibold tabular-nums">{summary.medium_risk_pair_count}</p>
              <p className="mt-1 text-xs text-amber-600">相似度 ≥ {formatPercent(report.thresholds.medium)}</p>
            </div>
            <div className="rounded-2xl border border-emerald-100 bg-emerald-50 p-4 text-emerald-800">
              <p className="text-xs font-medium">已完成对比</p>
              <p className="mt-2 font-mono text-3xl font-semibold tabular-nums">{summary.analyzed_pair_count}</p>
              <p className="mt-1 text-xs text-emerald-600">{summary.safe_pair_count} 对低于预警线</p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-4 text-slate-800">
              <p className="text-xs font-medium">菜品覆盖率</p>
              <p className="mt-2 font-mono text-3xl font-semibold tabular-nums">{formatPercent(coverage)}</p>
              <p className="mt-1 text-xs text-slate-500">{summary.indexed_dish_count} / {summary.total_active_dish_count} 个菜品</p>
            </div>
          </div>

          <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <main className="min-w-0 space-y-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="font-semibold text-slate-950">需要复核的菜品对</h3>
                  <p className="mt-0.5 text-xs text-slate-500">按最高相似度从高到低排列</p>
                </div>
                <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs text-slate-500">
                  {report.pairs.length} 对
                </span>
              </div>

              {report.pairs.length > 0 ? (
                report.pairs.map(pair => (
                  <RiskPairCard
                    key={`${pair.left.dish_id}-${pair.right.dish_id}`}
                    pair={pair}
                    onInspectDish={onInspectDish}
                  />
                ))
              ) : !report.index_ready ? (
                <div className="rounded-[22px] border border-amber-200 bg-amber-50 p-8 text-center text-amber-800">
                  <AlertTriangle className="mx-auto h-9 w-9" />
                  <h4 className="mt-3 font-semibold">当前样图索引尚未构建</h4>
                  <p className="mx-auto mt-1 max-w-lg text-sm text-amber-700">
                    请先重建 {pipelineLabel} 样图索引，再重新发起混淆体检。
                  </p>
                </div>
              ) : summary.analyzed_pair_count === 0 ? (
                <div className="rounded-[22px] border border-amber-200 bg-amber-50 p-8 text-center text-amber-800">
                  <AlertTriangle className="mx-auto h-9 w-9" />
                  <h4 className="mt-3 font-semibold">可对比菜品不足</h4>
                  <p className="mx-auto mt-1 max-w-lg text-sm text-amber-700">
                    至少需要两个已完成向量化的菜品才能分析混淆风险，当前没有执行跨菜品对比。
                  </p>
                </div>
              ) : (
                <div className="rounded-[22px] border border-emerald-200 bg-emerald-50 p-8 text-center text-emerald-800">
                  <ShieldCheck className="mx-auto h-9 w-9" />
                  <h4 className="mt-3 font-semibold">未发现达到预警线的菜品对</h4>
                  <p className="mx-auto mt-1 max-w-lg text-sm text-emerald-700">
                    当前索引内的跨菜品样图相似度均低于 {formatPercent(report.thresholds.medium)}。
                  </p>
                </div>
              )}

              {summary.truncated_pair_count > 0 && (
                <p className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-500">
                  报告仅展示相似度最高的 {summary.returned_pair_count} 对，另有 {summary.truncated_pair_count} 对未展开。
                </p>
              )}
            </main>

            <aside className="space-y-4">
              <section className="rounded-[20px] border border-slate-200 bg-white p-4">
                <div className="flex items-center gap-2 text-slate-900">
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  <h3 className="text-sm font-semibold">处理建议</h3>
                </div>
                <ol className="mt-3 space-y-3">
                  {report.recommendations.map((recommendation, index) => (
                    <li key={recommendation} className="flex gap-2.5 text-xs leading-5 text-slate-600">
                      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-slate-100 font-mono text-[10px] text-slate-600">
                        {index + 1}
                      </span>
                      <span>{recommendation}</span>
                    </li>
                  ))}
                </ol>
              </section>

              <section className="rounded-[20px] border border-slate-200 bg-white p-4">
                <div className="flex items-center gap-2 text-slate-900">
                  <AlertTriangle className="h-4 w-4 text-amber-600" />
                  <h3 className="text-sm font-semibold">未纳入分析</h3>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  {summary.not_analyzed_dish_count > 0
                    ? `${summary.not_analyzed_dish_count} 个启用菜品不在当前索引中。`
                    : '所有启用菜品均已进入当前索引。'}
                </p>
                {report.not_analyzed_dishes.length > 0 && (
                  <div className="mt-3 max-h-48 space-y-2 overflow-y-auto pr-1">
                    {report.not_analyzed_dishes.map(dish => (
                      <button
                        key={dish.dish_id}
                        type="button"
                        onClick={() => onInspectDish(dish.dish_id)}
                        className="flex w-full items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2 text-left text-xs transition-colors hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300"
                      >
                        <span className="truncate font-medium text-slate-700">{dish.dish_name}</span>
                        <span className="shrink-0 text-slate-400">样图 {dish.sample_image_count}</span>
                      </button>
                    ))}
                  </div>
                )}
              </section>

              <section className="rounded-[20px] border border-blue-100 bg-blue-50 p-4 text-blue-800">
                <div className="flex items-center gap-2">
                  <Info className="h-4 w-4" />
                  <h3 className="text-sm font-semibold">如何理解结果</h3>
                </div>
                <p className="mt-2 text-xs leading-5 text-blue-700">
                  报告比较当前识别索引中的全局样图向量，用于预警潜在混淆，不代表一定会误识别。实际结果还会受餐盘裁剪、光线、召回候选和重排模型影响。
                </p>
              </section>
            </aside>
          </div>
        </div>
      </section>
    </div>
  )
}
