import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Brain,
  Camera,
  Image as ImageIcon,
  Loader2,
  MessageSquare,
  Play,
  RefreshCw,
  Send,
  Settings,
  Square,
  Upload,
  Video,
  VideoOff,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { demoApi } from '@/api/client'
import { cn, fmtDateTime } from '@/lib/utils'
import toast from 'react-hot-toast'

interface RecognizedDish {
  name: string
  confidence: number
}

interface MatchedDish {
  id: number
  name: string
  category?: string
  confidence?: number
  calories?: number
  protein?: number
  fat?: number
  carbohydrate?: number
  sodium?: number
  fiber?: number
  price?: number
}

interface NutritionData {
  total: {
    calories: number
    protein: number
    fat: number
    carbohydrate: number
    sodium: number
    fiber: number
    [key: string]: number
  }
  recommended?: {
    calories: number
    protein: number
    fat: number
    carbohydrate: number
    sodium: number
    fiber: number
    [key: string]: number
  }
  percentages?: Record<string, number>
}

interface Suggestion {
  type: 'warning' | 'info' | 'success' | 'suggestion'
  title: string
  message: string
}

interface AnalysisResult {
  image_base64?: string
  recognized_dishes: RecognizedDish[]
  matched_dishes: MatchedDish[]
  nutrition: NutritionData
  suggestions: Suggestion[]
  notes?: string
  analyzed_at?: string
}

interface ChatMessage {
  id: string
  role: 'assistant' | 'user' | 'system'
  content: string
  meta?: string
  attachmentImage?: string
  variant?: 'default' | 'capture' | 'report'
  reportData?: AnalysisResult
}

type NumericRecord = Record<string, number>

const NUTRITION_LABELS: Record<string, string> = {
  calories: '热量',
  protein: '蛋白质',
  fat: '脂肪',
  carbohydrate: '碳水',
  sodium: '钠',
  fiber: '纤维',
}

const NUTRITION_UNITS: Record<string, string> = {
  calories: 'kcal',
  protein: 'g',
  fat: 'g',
  carbohydrate: 'g',
  sodium: 'mg',
  fiber: 'g',
}

const QUICK_PROMPTS = [
  '总结这份餐盘的风险',
  '蛋白质够不够',
  '给出更均衡的调整建议',
]

const REPORT_METRIC_KEYS = ['calories', 'protein', 'fat', 'carbohydrate', 'sodium', 'fiber'] as const

function createMessage(
  role: ChatMessage['role'],
  content: string,
  meta?: string,
  options?: Pick<ChatMessage, 'attachmentImage' | 'variant' | 'reportData'>,
): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role,
    content,
    meta,
    attachmentImage: options?.attachmentImage,
    variant: options?.variant ?? 'default',
    reportData: options?.reportData,
  }
}

function toFiniteNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : fallback
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : fallback
  }

  return fallback
}

function toOptionalNumber(value: unknown): number | undefined {
  if (value == null || value === '') return undefined
  const parsed = toFiniteNumber(value, Number.NaN)
  return Number.isFinite(parsed) ? parsed : undefined
}

function normalizeNumericRecord(source: unknown): NumericRecord {
  const record = source && typeof source === 'object' ? source as Record<string, unknown> : {}
  const normalized: NumericRecord = {}

  Object.entries(record).forEach(([key, value]) => {
    normalized[key] = toFiniteNumber(value)
  })

  return normalized
}

function normalizeNutritionValues(source: unknown, defaults: NutritionData['total']): NutritionData['total'] {
  const record = source && typeof source === 'object' ? source as Record<string, unknown> : {}
  const normalized: NutritionData['total'] = { ...defaults }

  Object.keys(defaults).forEach((key) => {
    normalized[key] = toFiniteNumber(record[key], defaults[key])
  })

  return normalized
}

function normalizeAnalysisResult(source: unknown): AnalysisResult {
  const data = source && typeof source === 'object' ? source as Record<string, unknown> : {}
  const nutritionData = data.nutrition && typeof data.nutrition === 'object'
    ? data.nutrition as Record<string, unknown>
    : {}
  const defaultNutrition = {
    calories: 0,
    protein: 0,
    fat: 0,
    carbohydrate: 0,
    sodium: 0,
    fiber: 0,
  }

  return {
    image_base64: typeof data.image_base64 === 'string' ? data.image_base64 : undefined,
    recognized_dishes: Array.isArray(data.recognized_dishes)
      ? data.recognized_dishes.map((dish) => {
          const item = dish && typeof dish === 'object' ? dish as Record<string, unknown> : {}
          return {
            name: typeof item.name === 'string' ? item.name : '',
            confidence: toFiniteNumber(item.confidence),
          }
        }).filter((dish) => dish.name)
      : [],
    matched_dishes: Array.isArray(data.matched_dishes)
      ? data.matched_dishes.map((dish) => {
          const item = dish && typeof dish === 'object' ? dish as Record<string, unknown> : {}
          return {
            id: toFiniteNumber(item.id),
            name: typeof item.name === 'string' ? item.name : '',
            category: typeof item.category === 'string' ? item.category : undefined,
            confidence: toOptionalNumber(item.confidence),
            calories: toOptionalNumber(item.calories),
            protein: toOptionalNumber(item.protein),
            fat: toOptionalNumber(item.fat),
            carbohydrate: toOptionalNumber(item.carbohydrate),
            sodium: toOptionalNumber(item.sodium),
            fiber: toOptionalNumber(item.fiber),
            price: toOptionalNumber(item.price),
          }
        }).filter((dish) => dish.name)
      : [],
    nutrition: {
      total: normalizeNutritionValues(nutritionData.total, defaultNutrition),
      recommended: normalizeNutritionValues(nutritionData.recommended, defaultNutrition),
      percentages: normalizeNumericRecord(nutritionData.percentages),
    },
    suggestions: Array.isArray(data.suggestions)
      ? data.suggestions.map((item) => {
          const suggestion = item && typeof item === 'object' ? item as Record<string, unknown> : {}
          const type: Suggestion['type'] = suggestion.type === 'warning' || suggestion.type === 'info' || suggestion.type === 'success' || suggestion.type === 'suggestion'
            ? suggestion.type
            : 'info'
          return {
            type,
            title: typeof suggestion.title === 'string' ? suggestion.title : '',
            message: typeof suggestion.message === 'string' ? suggestion.message : '',
          }
        }).filter((item) => item.title || item.message)
      : [],
    notes: typeof data.notes === 'string' ? data.notes : undefined,
    analyzed_at: typeof data.analyzed_at === 'string' ? data.analyzed_at : undefined,
  }
}

function getNutritionPercent(result: AnalysisResult, key: string): number {
  const explicit = result.nutrition.percentages?.[key]
  if (typeof explicit === 'number') return explicit

  const value = result.nutrition.total[key]
  const recommended = result.nutrition.recommended?.[key]
  if (typeof value !== 'number' || !recommended) return 0

  return (value / recommended) * 100
}

function formatNutritionValue(key: string, value: number): string {
  const unit = NUTRITION_UNITS[key] ?? ''
  const precision = key === 'sodium' || key === 'calories' ? 0 : value < 10 ? 1 : 0
  return `${value.toFixed(precision)} ${unit}`.trim()
}

function getDominantNutrition(result: AnalysisResult) {
  return Object.entries(result.nutrition.total)
    .map(([key, value]) => ({
      key,
      label: NUTRITION_LABELS[key] ?? key,
      value,
      percentage: getNutritionPercent(result, key),
    }))
    .sort((a, b) => b.percentage - a.percentage)[0]
}

function getAverageConfidence(result: AnalysisResult): number | null {
  const matched = result.matched_dishes
    .map((dish) => dish.confidence)
    .filter((value): value is number => typeof value === 'number')
  const recognized = result.recognized_dishes
    .map((dish) => dish.confidence)
    .filter((value): value is number => typeof value === 'number')
  const list = matched.length > 0 ? matched : recognized
  if (list.length === 0) return null
  return list.reduce((sum, value) => sum + value, 0) / list.length
}

function getResultStatus(result: AnalysisResult | null) {
  if (!result) {
    return {
      label: '等待分析',
      description: 'Agent 将在拿到新截图后生成判断',
      badgeClass: 'border-border bg-secondary text-muted-foreground',
      dotClass: 'bg-muted-foreground/60',
    }
  }

  const warningCount = result.suggestions.filter((item) => item.type === 'warning').length
  const dominant = getDominantNutrition(result)
  if (result.matched_dishes.length === 0) {
    return {
      label: '待人工复核',
      description: '本次截图没有稳定匹配到菜品',
      badgeClass: 'border-amber-200 bg-amber-50 text-amber-700',
      dotClass: 'bg-amber-500',
    }
  }

  if (warningCount > 0 || (dominant && dominant.percentage >= 85)) {
    return {
      label: '需重点关注',
      description: '本次结果存在高负荷指标或明确风险提示',
      badgeClass: 'border-rose-200 bg-rose-50 text-rose-700',
      dotClass: 'bg-rose-500',
    }
  }

  return {
    label: '结构基本稳定',
    description: '识别结果完整，可继续让 Agent 深挖建议',
    badgeClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    dotClass: 'bg-emerald-500',
  }
}

function buildAutoSummary(result: AnalysisResult): string {
  const dishes = result.matched_dishes.slice(0, 4).map((dish) => dish.name)
  const dominant = getDominantNutrition(result)
  const leadingSuggestion = result.suggestions[0]?.message
  const parts = [
    dishes.length > 0
      ? `已识别 ${result.matched_dishes.length} 道菜，当前餐盘包含 ${dishes.join('、')}`
      : '当前截图没有稳定识别出菜品',
    dominant
      ? `${dominant.label}达到建议摄入的 ${dominant.percentage.toFixed(0)}%`
      : '营养占比数据已经同步',
  ]

  if (leadingSuggestion) {
    parts.push(`优先建议是 ${leadingSuggestion}`)
  }

  return `${parts.join('，')}。`
}

function explainNutrition(result: AnalysisResult, key: string): string {
  const label = NUTRITION_LABELS[key] ?? key
  const value = result.nutrition.total[key]
  if (typeof value !== 'number') {
    return `当前结果里没有 ${label} 的可用数据。`
  }

  const percentage = getNutritionPercent(result, key)
  let assessment = '处于观察区间'
  if (percentage >= 85) assessment = '偏高，需要优先关注'
  else if (percentage >= 60) assessment = '占比不低，建议继续控制'
  else if (percentage <= 25) assessment = '相对偏低，可以补强'

  return `${label}约 ${formatNutritionValue(key, value)}，相当于建议摄入的 ${percentage.toFixed(0)}%，当前判断为${assessment}。`
}

function buildSuggestionDigest(result: AnalysisResult): string {
  if (result.suggestions.length === 0) {
    const dominant = getDominantNutrition(result)
    if (!dominant) return '当前没有额外建议，建议继续观察连续样本。'
    if (dominant.percentage >= 85) {
      return `先控制 ${dominant.label} 负荷，再观察下一次截图的变化。`
    }
    return '当前结构没有明显异常，可以继续保持并结合后续样本判断。'
  }

  return result.suggestions
    .slice(0, 3)
    .map((item) => `${item.title}：${item.message}`)
    .join('；')
}

function buildAgentReport(result: AnalysisResult): string {
  const status = getResultStatus(result)
  const dominant = getDominantNutrition(result)
  const recognizedDishes = result.matched_dishes.map((dish) => dish.name).slice(0, 6)
  const topNutrition = Object.entries(result.nutrition.total)
    .map(([key, value]) => ({
      key,
      label: NUTRITION_LABELS[key] ?? key,
      value,
      percentage: getNutritionPercent(result, key),
    }))
    .sort((a, b) => b.percentage - a.percentage)
    .slice(0, 3)

  const suggestionLines = result.suggestions.length > 0
    ? result.suggestions.slice(0, 3).map((item, index) => `${index + 1}. ${item.title}：${item.message}`)
    : [`1. ${buildSuggestionDigest(result)}`]

  const sections = [
    `结论：${status.label}。${status.description}。`,
    recognizedDishes.length > 0
      ? `识别菜品：${recognizedDishes.join('、')}。`
      : '识别菜品：本轮没有稳定匹配到菜品，建议补一张更清晰的截图再判断。',
    dominant
      ? `主要负荷：${dominant.label} ${formatNutritionValue(dominant.key, dominant.value)}，约为建议摄入的 ${dominant.percentage.toFixed(0)}%。`
      : null,
    topNutrition.length > 0
      ? `营养概览：\n${topNutrition.map((item) => `- ${item.label} ${formatNutritionValue(item.key, item.value)}，${item.percentage.toFixed(0)}%`).join('\n')}`
      : null,
    `执行建议：\n${suggestionLines.join('\n')}`,
    result.notes ? `补充说明：${result.notes}` : null,
  ]

  return sections.filter(Boolean).join('\n\n')
}

function getMetricTone(percentage: number) {
  if (percentage >= 85) {
    return {
      chip: 'border-rose-200 bg-rose-50 text-rose-700',
      bar: 'bg-rose-500',
      text: '偏高',
    }
  }

  if (percentage >= 60) {
    return {
      chip: 'border-amber-200 bg-amber-50 text-amber-700',
      bar: 'bg-amber-500',
      text: '偏满',
    }
  }

  return {
    chip: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    bar: 'bg-emerald-500',
    text: '可控',
  }
}

function getSuggestionTone(type: Suggestion['type']) {
  if (type === 'warning') return 'border-rose-200 bg-rose-50/80 text-rose-700'
  if (type === 'success') return 'border-emerald-200 bg-emerald-50/80 text-emerald-700'
  if (type === 'suggestion') return 'border-sky-200 bg-sky-50/80 text-sky-700'
  return 'border-border bg-secondary/70 text-foreground'
}

function NutritionReportCard({ result }: { result: AnalysisResult }) {
  const status = getResultStatus(result)
  const dominant = getDominantNutrition(result)
  const summary = buildAutoSummary(result)
  const recognizedDishes = Array.from(
    new Set(
      (result.matched_dishes.length > 0 ? result.matched_dishes : result.recognized_dishes)
        .map((dish) => dish.name)
        .filter(Boolean),
    ),
  ).slice(0, 8)
  const metrics = REPORT_METRIC_KEYS.map((key) => {
    const value = result.nutrition.total[key]
    const percentage = getNutritionPercent(result, key)
    const recommended = result.nutrition.recommended?.[key]
    return {
      key,
      label: NUTRITION_LABELS[key],
      value,
      percentage,
      recommended,
      tone: getMetricTone(percentage),
    }
  })
  const keySuggestions: Suggestion[] = result.suggestions.length > 0
    ? result.suggestions.slice(0, 4)
    : [{
        type: dominant && dominant.percentage >= 85 ? 'warning' : 'info',
        title: '执行建议',
        message: buildSuggestionDigest(result),
      }]

  return (
    <article className="overflow-hidden rounded-[22px] border border-slate-200 bg-[linear-gradient(180deg,#ffffff_0%,#f8fafc_100%)] shadow-[0_18px_50px_rgba(15,23,42,0.08)]">
      <div className="border-b border-slate-200/80 bg-[radial-gradient(circle_at_top_left,rgba(59,130,246,0.12),transparent_32%),linear-gradient(135deg,rgba(15,23,42,0.02),rgba(255,255,255,0.7))] px-5 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-2xl">
            <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-slate-500">AI Nutrition Report</div>
            <h3 className="mt-2 text-xl font-semibold tracking-tight text-slate-900">本次餐盘营养报告</h3>
            <p className="mt-3 text-sm leading-6 text-slate-600">{summary}</p>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <div className={cn('rounded-2xl border px-4 py-3 text-sm', status.badgeClass)}>
              <div className="text-[11px] font-mono uppercase tracking-[0.2em] opacity-70">Status</div>
              <div className="mt-2 flex items-center gap-2 font-medium">
                <span className={cn('h-2.5 w-2.5 rounded-full', status.dotClass)} />
                {status.label}
              </div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white/85 px-4 py-3 text-sm text-slate-700">
              <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-slate-400">Updated</div>
              <div className="mt-2 font-medium text-slate-900">
                {result.analyzed_at ? fmtDateTime(result.analyzed_at) : '刚刚生成'}
              </div>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white/85 px-4 py-3 text-sm text-slate-700">
              <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-slate-400">Matched Dishes</div>
              <div className="mt-2 font-medium text-slate-900">{result.matched_dishes.length || result.recognized_dishes.length} 项</div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid gap-4 px-5 py-5 xl:grid-cols-[minmax(0,1.2fr)_280px]">
        <div className="space-y-4">
          <section className="rounded-2xl border border-slate-200 bg-white/90 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-slate-900">营养指标</div>
                <div className="text-xs text-slate-500">按建议摄入占比展示，便于直接判断负荷</div>
              </div>
              {dominant && (
                <div className={cn('rounded-full border px-3 py-1 text-xs font-medium', getMetricTone(dominant.percentage).chip)}>
                  当前最高：{dominant.label}
                </div>
              )}
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {metrics.map((metric) => (
                <div key={metric.key} className="rounded-2xl border border-slate-200 bg-slate-50/70 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-medium uppercase tracking-[0.16em] text-slate-500">{metric.label}</div>
                      <div className="mt-2 text-lg font-semibold text-slate-900">
                        {formatNutritionValue(metric.key, metric.value)}
                      </div>
                    </div>
                    <div className={cn('rounded-full border px-2.5 py-1 text-xs font-medium', metric.tone.chip)}>
                      {metric.percentage.toFixed(0)}%
                    </div>
                  </div>

                  <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200">
                    <div
                      className={cn('h-full rounded-full transition-all', metric.tone.bar)}
                      style={{ width: `${Math.max(0, Math.min(metric.percentage, 100))}%` }}
                    />
                  </div>

                  <div className="mt-2 flex items-center justify-between text-xs text-slate-500">
                    <span>{metric.tone.text}</span>
                    <span>建议值 {metric.recommended ? formatNutritionValue(metric.key, metric.recommended) : '--'}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white/90 p-4">
            <div className="text-sm font-medium text-slate-900">执行建议</div>
            <div className="mt-3 space-y-3">
              {keySuggestions.map((item, index) => (
                <div key={`${item.title}-${index}`} className={cn('rounded-2xl border px-4 py-3', getSuggestionTone(item.type))}>
                  <div className="text-xs font-mono uppercase tracking-[0.18em] opacity-70">Action {index + 1}</div>
                  <div className="mt-1 text-sm font-medium">{item.title || '建议'}</div>
                  <div className="mt-1 text-sm leading-6 opacity-90">{item.message}</div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-2xl border border-slate-200 bg-white/90 p-4">
            <div className="text-sm font-medium text-slate-900">识别菜品</div>
            <div className="mt-3 flex flex-wrap gap-2">
              {recognizedDishes.length > 0 ? (
                recognizedDishes.map((dish) => (
                  <span
                    key={dish}
                    className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700"
                  >
                    {dish}
                  </span>
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-500">
                  本轮没有稳定识别到菜品，建议补拍更清晰的样本。
                </div>
              )}
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white/90 p-4">
            <div className="text-sm font-medium text-slate-900">结论摘要</div>
            <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 text-sm leading-6 text-slate-600">
              {dominant
                ? `${dominant.label} 是当前最需要关注的负荷项，已达到建议摄入的 ${dominant.percentage.toFixed(0)}%。`
                : status.description}
            </div>
            {result.notes && (
              <div className="mt-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm leading-6 text-slate-600">
                <div className="text-xs font-mono uppercase tracking-[0.18em] text-slate-400">Notes</div>
                <div className="mt-1">{result.notes}</div>
              </div>
            )}
          </section>
        </div>
      </div>
    </article>
  )
}

function ChatMarkdown({ content }: { content: string }) {
  return (
    <div className="markdown-body text-[14px] leading-7 text-inherit">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-0 [&:not(:first-child)]:mt-3">{children}</p>,
          ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="pl-1">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-primary/25 pl-4 text-muted-foreground">{children}</blockquote>
          ),
          pre: ({ children }) => (
            <pre className="my-3 overflow-x-auto rounded-xl bg-secondary/80 p-3 font-mono text-[13px] leading-6">
              {children}
            </pre>
          ),
          code: ({ className, children }) => (
            <code className={cn('font-mono text-[13px]', !className && 'rounded bg-secondary px-1.5 py-0.5')}>
              {children}
            </code>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline decoration-primary/30 underline-offset-4"
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}

function buildAgentReply(input: string, result: AnalysisResult | null): string {
  const normalized = input.toLowerCase()

  if (!result) {
    return '我还没有拿到当前餐盘的分析结果。先上传图片、摄像头抓拍，或者从实时流里截图，我再根据结果继续判断。'
  }

  const dishNames = result.matched_dishes.map((dish) => dish.name)
  const dominant = getDominantNutrition(result)
  const warningCount = result.suggestions.filter((item) => item.type === 'warning').length

  if (normalized.includes('识别') || normalized.includes('菜品') || normalized.includes('有什么')) {
    if (dishNames.length === 0) {
      return '这张图里暂时没有稳定匹配到菜品，建议换一个角度再抓拍一次，或者补一张更清晰的截图。'
    }
    return `当前识别到 ${dishNames.length} 道菜：${dishNames.join('、')}。如果你要，我可以继续按热量、蛋白质或风险优先级拆开说明。`
  }

  if (normalized.includes('热量') || normalized.includes('卡路里')) {
    return `${explainNutrition(result, 'calories')} ${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('蛋白')) {
    return `${explainNutrition(result, 'protein')} 如果这是正餐，建议结合主菜和豆制品一起判断是否需要补强。`
  }

  if (normalized.includes('脂肪')) {
    return `${explainNutrition(result, 'fat')} 如果后续还有油炸或重油菜，优先从烹调方式上做控制。`
  }

  if (normalized.includes('碳水')) {
    return `${explainNutrition(result, 'carbohydrate')} 如果需要更稳的午后状态，可以把部分精制主食替换成粗粮。`
  }

  if (normalized.includes('钠') || normalized.includes('盐')) {
    return `${explainNutrition(result, 'sodium')} 如果连续多餐偏高，建议重点看卤味、汤汁和加工食品。`
  }

  if (normalized.includes('纤维') || normalized.includes('蔬菜')) {
    return `${explainNutrition(result, 'fiber')} 纤维主要看蔬菜、豆类和全谷物是否足够。`
  }

  if (normalized.includes('风险') || normalized.includes('注意')) {
    if (warningCount === 0 && dominant && dominant.percentage < 85) {
      return '当前结果里没有明显高风险指标，主要是常规结构优化问题。建议继续结合多次餐盘样本看趋势。'
    }
    return `本次需要优先关注的点有 ${warningCount || 1} 项。${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('建议') || normalized.includes('优化') || normalized.includes('怎么吃')) {
    return `我给出一个执行版建议：${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('总结') || normalized.includes('概览') || normalized.includes('报告')) {
    return buildAutoSummary(result)
  }

  return `执行摘要：${buildAutoSummary(result)} 如果你想更具体一点，可以直接问我热量、蛋白质、风险点，或者让我要一个更均衡的调整方案。`
}

export default function DemoPage() {
  const [mode, setMode] = useState<'upload' | 'camera' | 'stream'>('upload')
  const [cameraHost, setCameraHost] = useState('')
  const [cameraPort, setCameraPort] = useState('80')
  const [cameraUsername, setCameraUsername] = useState('admin')
  const [cameraPassword, setCameraPassword] = useState('')
  const [channelId, setChannelId] = useState('1')
  const [capturedImage, setCapturedImage] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [showSettings, setShowSettings] = useState(false)

  const [streaming, setStreaming] = useState(false)
  const [streamUrl, setStreamUrl] = useState('')
  const [streamError, setStreamError] = useState<string | null>(null)

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    createMessage('assistant', '我是营养分析 Agent。左侧输入画面后，我会在右侧持续输出判断，你也可以直接追问。', '系统就绪'),
  ])
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chatViewportRef = useRef<HTMLDivElement>(null)
  const replyTimerRef = useRef<number | null>(null)

  const stopWebRTCStream = useCallback(() => {
    const ws = wsRef.current
    if (ws) {
      ws.close()
      wsRef.current = null
    }

    if (peerConnectionRef.current) {
      peerConnectionRef.current.close()
      peerConnectionRef.current = null
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }

    setStreaming(false)
  }, [])

  const startWebRTCStream = useCallback(async () => {
    const source = streamUrl.trim()
    if (!source) {
      setStreamError('请输入流名称，例如 test 或 camera1')
      return
    }

    setStreamError(null)
    setStreaming(true)

    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      })
      peerConnectionRef.current = pc

      const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const wsUrl = `${wsProtocol}://${window.location.host}/rtc/api/ws?src=${encodeURIComponent(source)}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      pc.ontrack = (event) => {
        if (!streamRef.current) {
          streamRef.current = new MediaStream()
          if (videoRef.current) {
            videoRef.current.srcObject = streamRef.current
          }
        }

        streamRef.current.addTrack(event.track)
      }

      pc.onicecandidate = (event) => {
        if (!event.candidate || ws.readyState !== WebSocket.OPEN) return

        ws.send(JSON.stringify({
          type: 'webrtc/candidate',
          value: event.candidate.candidate,
        }))
      }

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setStreamError('视频流连接断开，请重试')
          stopWebRTCStream()
        }
      }

      ws.onopen = async () => {
        pc.addTransceiver('video', { direction: 'recvonly' })
        pc.addTransceiver('audio', { direction: 'recvonly' })

        const offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        ws.send(JSON.stringify({
          type: 'webrtc/offer',
          value: pc.localDescription?.sdp,
        }))
      }

      ws.onmessage = async (event) => {
        try {
          const message = JSON.parse(event.data)

          if (message.type === 'webrtc/answer') {
            await pc.setRemoteDescription({
              type: 'answer',
              sdp: message.value,
            })
          } else if (message.type === 'webrtc/candidate') {
            await pc.addIceCandidate({
              candidate: message.value,
              sdpMid: '0',
            })
          }
        } catch (error) {
          console.error('Failed to parse WebRTC message:', error)
        }
      }

      ws.onerror = () => {
        setStreamError('WebSocket 连接失败')
        stopWebRTCStream()
      }

      ws.onclose = () => {
        if (pc.connectionState === 'new' || pc.connectionState === 'connecting') {
          setStreamError('实时流握手被关闭，请检查 go2rtc 流名称和服务状态')
          stopWebRTCStream()
        }
      }
    } catch (error) {
      console.error('WebRTC error:', error)
      setStreamError(error instanceof Error ? error.message : '连接失败')
      stopWebRTCStream()
    }
  }, [stopWebRTCStream, streamUrl])

  async function analyzeImage(base64: string) {
    const pureBase64 = base64.includes(',') ? base64.split(',')[1] : base64

    setAnalyzing(true)
    setChatBusy(true)
    try {
      const response = await demoApi.quickAnalyze(pureBase64)
      const normalized = normalizeAnalysisResult(response.data.data)
      setResult(normalized)
      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', buildAgentReport(normalized), '营养报告', { variant: 'report', reportData: normalized }),
      ])
    } catch (error) {
      toast.error('分析失败，请重试')
      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', '这张截图已收到，但本次分析没有成功完成。请重试，或换一张更清晰的图。', '分析失败'),
      ])
    } finally {
      setAnalyzing(false)
      setChatBusy(false)
    }
  }

  async function sendCaptureToAgent(displayImage: string, sourceLabel: string, analysisPayload?: string) {
    setCapturedImage(displayImage)
    setResult(null)
    setChatMessages((prev) => [
      ...prev,
      createMessage(
        'user',
        '请基于这张最新餐盘截图输出完整营养报告，并给出可以直接执行的建议。',
        sourceLabel,
        { attachmentImage: displayImage, variant: 'capture' },
      ),
    ])
    await analyzeImage(analysisPayload ?? displayImage)
  }

  const captureFrameFromStream = useCallback(() => {
    if (!videoRef.current || !streaming) return

    const video = videoRef.current
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.drawImage(video, 0, 0)
    const base64 = canvas.toDataURL('image/jpeg', 0.9)
    void sendCaptureToAgent(base64, `实时流截图 · ${streamUrl || '未命名流'}`)
  }, [streamUrl, streaming])

  useEffect(() => {
    return () => {
      stopWebRTCStream()
      if (replyTimerRef.current) {
        window.clearTimeout(replyTimerRef.current)
      }
    }
  }, [stopWebRTCStream])

  useEffect(() => {
    if (!chatViewportRef.current) return
    chatViewportRef.current.scrollTo({
      top: chatViewportRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [chatBusy, chatMessages])

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = async (loadEvent) => {
      const base64 = loadEvent.target?.result as string
      await sendCaptureToAgent(base64, '上传图片')
    }
    reader.readAsDataURL(file)
  }

  const captureFromCamera = async () => {
    if (!cameraHost) {
      toast.error('请先配置摄像头 IP 地址')
      return
    }

    setCapturing(true)
    try {
      const response = await demoApi.capture({
        channel_id: channelId,
        host: cameraHost,
        port: parseInt(cameraPort, 10) || 80,
        username: cameraUsername,
        password: cameraPassword,
      })

      const base64 = `data:${response.data.data.content_type};base64,${response.data.data.image_base64}`
      await sendCaptureToAgent(base64, '摄像头抓拍', response.data.data.image_base64)
    } catch (error) {
      toast.error('抓拍失败，请检查摄像头配置')
    } finally {
      setCapturing(false)
    }
  }

  const reanalyze = () => {
    if (capturedImage) {
      void sendCaptureToAgent(capturedImage, '重新分析当前截图')
    }
  }

  const clearAll = () => {
    setCapturedImage(null)
    setResult(null)
    setStreamError(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const submitChat = async (content: string) => {
    const trimmed = content.trim()
    if (!trimmed || chatBusy) return

    const history = chatMessages
      .filter((message): message is ChatMessage & { role: 'assistant' | 'user' } => (
        message.role === 'user' || message.role === 'assistant'
      ))
      .map((message) => ({
        role: message.role,
        content: message.content,
      }))

    setChatMessages((prev) => [...prev, createMessage('user', trimmed, '实时提问')])
    setChatBusy(true)

    if (replyTimerRef.current) {
      window.clearTimeout(replyTimerRef.current)
      replyTimerRef.current = null
    }

    try {
      const response = await demoApi.chat({
        message: trimmed,
        history,
        analysis_result: result,
      })

      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', response.data.data.reply || '当前没有拿到有效回复，请重试。', 'Agent 回复'),
      ])
    } catch (error) {
      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', '当前无法连接营养洞察Agent，请稍后重试。', 'Agent 暂不可用'),
      ])
    } finally {
      setChatBusy(false)
    }
  }

  const handleChatSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const current = chatInput
    setChatInput('')
    void submitChat(current)
  }

  const status = getResultStatus(result)
  const sourceText = mode === 'stream'
    ? streaming
      ? `实时流 ${streamUrl || '未命名'} 在线`
      : '等待连接实时流'
    : mode === 'camera'
      ? cameraHost
        ? `摄像头 ${cameraHost}:${cameraPort}`
        : '等待填写摄像头地址'
      : capturedImage
        ? '上传样本已载入'
        : '等待上传图片'

  return (
    <div className="min-h-full bg-background p-4 sm:p-6">
      <div className="space-y-5">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleFileSelect}
          className="hidden"
        />

        <section className="rounded-xl border border-border bg-card px-5 py-4 sm:px-6">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div className="space-y-2">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <Brain className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">
                    Agent Workspace
                  </div>
                  <h1 className="text-2xl font-semibold tracking-tight text-foreground">智能演示工作台</h1>
                </div>
              </div>
              <p className="max-w-3xl text-sm text-muted-foreground">
                输入区压缩为侧栏，营养洞察 Agent 在右侧持续输出首轮报告与后续问答。
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <div className="rounded-full border border-border bg-background px-3 py-1.5 text-sm text-foreground">
                <span className="text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">Source</span>
                <span className="ml-2 font-medium">{sourceText}</span>
              </div>
              <div className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm', status.badgeClass)}>
                <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
                {status.label}
              </div>
              <div className="rounded-full border border-border bg-background px-3 py-1.5 text-sm text-foreground">
                <span className="text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">Updated</span>
                <span className="ml-2 font-medium">{result?.analyzed_at ? fmtDateTime(result.analyzed_at) : '等待首个结果'}</span>
              </div>
            </div>
          </div>
        </section>

        <div className="grid items-start gap-4 xl:grid-cols-[340px_minmax(0,1.15fr)] 2xl:grid-cols-[360px_minmax(0,1.25fr)]">
          <section className="space-y-4 xl:sticky xl:top-6">
            <div className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Input Routing</div>
                  <h2 className="mt-2 text-base font-semibold text-foreground">采集模式与连接控制</h2>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">尽量少操作，送一张清晰样本给 Agent 即可。</p>
                </div>
                <div className="rounded-full border border-border bg-background px-3 py-1 text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">
                  {mode}
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {[
                  { id: 'upload', label: '上传', icon: ImageIcon },
                  { id: 'camera', label: '抓拍', icon: Camera },
                  { id: 'stream', label: '实时流', icon: Video },
                ].map(({ id, label, icon: Icon }) => (
                  <button
                    key={id}
                    onClick={() => {
                      if (id !== 'stream') stopWebRTCStream()
                      setMode(id as 'upload' | 'camera' | 'stream')
                    }}
                    className={cn(
                      'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-all',
                      mode === id
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-background text-muted-foreground hover:border-primary/20 hover:text-foreground',
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </button>
                ))}
              </div>

              <div className="mt-4 rounded-xl border border-border bg-background p-3">
                {mode === 'upload' && (
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="group flex min-h-[148px] w-full flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-4 text-center transition-colors hover:border-primary/30 hover:bg-secondary/60"
                  >
                    <div className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-secondary text-foreground transition-transform duration-200 group-hover:scale-105">
                      <Upload className="h-5 w-5" />
                    </div>
                    <div className="mt-3 text-sm font-medium text-foreground">点击上传餐盘图片</div>
                    <div className="mt-1 text-xs text-muted-foreground">支持 JPG、PNG，上传后立即分析</div>
                  </button>
                )}

                {mode === 'camera' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium text-foreground">摄像头抓拍</div>
                        <div className="text-xs text-muted-foreground">只保留最少必要参数</div>
                      </div>
                      <button
                        onClick={() => setShowSettings((value) => !value)}
                        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <Settings className="h-3 w-3" />
                        高级参数
                      </button>
                    </div>

                    <div className="grid gap-2">
                      <label className="block">
                        <div className="mb-1 text-[11px] font-medium text-muted-foreground">IP 地址</div>
                        <input
                          type="text"
                          value={cameraHost}
                          onChange={(event) => setCameraHost(event.target.value)}
                          placeholder="192.168.1.100"
                          className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                        />
                      </label>
                      <label className="block">
                        <div className="mb-1 text-[11px] font-medium text-muted-foreground">端口</div>
                        <input
                          type="text"
                          value={cameraPort}
                          onChange={(event) => setCameraPort(event.target.value)}
                          placeholder="80"
                          className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                        />
                      </label>
                    </div>

                    {showSettings && (
                      <div className="grid gap-2">
                        <label className="block">
                          <div className="mb-1 text-[11px] font-medium text-muted-foreground">通道</div>
                          <input
                            type="text"
                            value={channelId}
                            onChange={(event) => setChannelId(event.target.value)}
                            className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                          />
                        </label>
                        <label className="block">
                          <div className="mb-1 text-[11px] font-medium text-muted-foreground">用户名</div>
                          <input
                            type="text"
                            value={cameraUsername}
                            onChange={(event) => setCameraUsername(event.target.value)}
                            className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                          />
                        </label>
                        <label className="block">
                          <div className="mb-1 text-[11px] font-medium text-muted-foreground">密码</div>
                          <input
                            type="password"
                            value={cameraPassword}
                            onChange={(event) => setCameraPassword(event.target.value)}
                            className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                          />
                        </label>
                      </div>
                    )}

                    <button
                      onClick={captureFromCamera}
                      disabled={capturing || !cameraHost}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {capturing ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          抓拍中
                        </>
                      ) : (
                        <>
                          <Camera className="h-4 w-4" />
                          抓拍并分析
                        </>
                      )}
                    </button>
                  </div>
                )}

                {mode === 'stream' && (
                  <div className="space-y-3">
                    <label className="block">
                      <div className="mb-1 text-[11px] font-medium text-muted-foreground">流名称</div>
                      <input
                        type="text"
                        value={streamUrl}
                        onChange={(event) => setStreamUrl(event.target.value)}
                        placeholder="camera1"
                        className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                      />
                    </label>

                    <div className="flex items-center justify-between rounded-xl border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
                      <span>链路状态</span>
                      <span className="inline-flex items-center gap-2 font-medium text-foreground">
                        <span className={cn('h-2 w-2 rounded-full', streaming ? 'bg-emerald-500' : 'bg-muted-foreground/50')} />
                        {streaming ? '在线' : '待连接'}
                      </span>
                    </div>

                    {streamError && (
                      <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
                        <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                        {streamError}
                      </div>
                    )}

                    <div className="grid gap-2">
                      {!streaming ? (
                        <button
                          onClick={startWebRTCStream}
                          className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90"
                        >
                          <Play className="h-4 w-4" />
                          建立预览
                        </button>
                      ) : (
                        <>
                          <button
                            onClick={captureFrameFromStream}
                            disabled={analyzing}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                            截图并分析
                          </button>
                          <button
                            onClick={stopWebRTCStream}
                            className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary"
                          >
                            <Square className="h-4 w-4" />
                            停止预览
                          </button>
                        </>
                      )}
                    </div>

                    <div className="rounded-xl border border-border bg-card px-3 py-2 text-[11px] leading-5 text-muted-foreground">
                      go2rtc 已接入时只需填写流名称，预览建立后可直接截图送入 Agent。
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Aux Preview</div>
                  <h2 className="mt-2 text-base font-semibold text-foreground">实时预览与截图画面</h2>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">这里只做辅助确认，主输出看右侧报告与问答。</p>
                </div>
                <div className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs', status.badgeClass)}>
                  <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
                  {status.label}
                </div>
              </div>

              <div className="mt-4 space-y-3">
                <div className="relative overflow-hidden rounded-xl border border-border bg-[#0f172a]">
                  <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.04)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.04)_1px,transparent_1px)] bg-[size:24px_24px]" />
                  <div className="absolute left-3 top-3 z-10 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/35 px-3 py-1 text-[11px] text-white/80 backdrop-blur">
                    <span className={cn('h-2 w-2 rounded-full', streaming ? 'bg-emerald-400' : capturedImage ? 'bg-sky-400' : 'bg-white/40')} />
                    {mode === 'stream' ? 'Live feed' : 'Capture frame'}
                  </div>

                  {mode === 'stream' ? (
                    <div className="relative aspect-[16/11]">
                      <video
                        ref={videoRef}
                        autoPlay
                        playsInline
                        muted
                        className="h-full w-full object-cover"
                      />
                      {!streaming && (
                        <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60">
                          <div className="text-center text-white">
                            <VideoOff className="mx-auto h-10 w-10 opacity-50" />
                            <p className="mt-3 text-sm text-white/70">连接后在这里显示实时画面</p>
                          </div>
                        </div>
                      )}
                    </div>
                  ) : capturedImage ? (
                    <div className="aspect-[16/11]">
                      <img src={capturedImage} alt="Captured preview" className="h-full w-full object-cover" />
                    </div>
                  ) : (
                    <div className="flex aspect-[16/11] items-center justify-center bg-slate-950/40 px-6">
                      <div className="text-center text-white">
                        <Camera className="mx-auto h-10 w-10 opacity-50" />
                        <p className="mt-3 text-sm text-white/75">当前还没有样本画面</p>
                      </div>
                    </div>
                  )}

                  {analyzing && (
                    <div className="absolute inset-0 flex items-center justify-center bg-slate-950/55 backdrop-blur-sm">
                      <div className="text-center text-white">
                        <Loader2 className="mx-auto h-8 w-8 animate-spin" />
                        <p className="mt-3 text-sm font-medium">Agent 正在解析截图</p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="overflow-hidden rounded-xl border border-border bg-background">
                  <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
                    <div>
                      <div className="text-sm font-medium text-foreground">最新截图</div>
                      <div className="text-[11px] text-muted-foreground">当前发送给 Agent 的样本</div>
                    </div>
                    {capturedImage && (
                      <button
                        onClick={clearAll}
                        className="rounded-full border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>

                  {capturedImage ? (
                    <img src={capturedImage} alt="Snapshot" className="aspect-[16/10] w-full object-cover" />
                  ) : (
                    <div className="flex aspect-[16/10] items-center justify-center bg-secondary/70 px-6 text-center">
                      <div>
                        <ImageIcon className="mx-auto h-8 w-8 text-muted-foreground/60" />
                        <p className="mt-2 text-xs text-muted-foreground">截图会固定在这里，方便和右侧报告对照</p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="grid gap-2">
                  <button
                    onClick={reanalyze}
                    disabled={!capturedImage || analyzing}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RefreshCw className={cn('h-4 w-4', analyzing && 'animate-spin')} />
                    重新分析当前截图
                  </button>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary"
                  >
                    <Upload className="h-4 w-4" />
                    更换输入样本
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-primary/20 bg-[radial-gradient(circle_at_top_right,rgba(59,130,246,0.12),transparent_28%),linear-gradient(180deg,rgba(59,130,246,0.06),rgba(59,130,246,0.02))]">
            <div className="flex h-full min-h-[820px] flex-col p-5">
              <div className="flex flex-col gap-3 border-b border-primary/10 pb-4 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Nutrition Insight Agent</div>
                  <h2 className="mt-2 text-xl font-semibold text-foreground">营养洞察Agent</h2>
                  <p className="mt-1 text-sm text-muted-foreground">首轮输出用报告样式承载，后续追问保持轻量文字问答。</p>
                </div>
                <div className="rounded-xl border border-primary/15 bg-background/80 px-4 py-3 text-sm text-muted-foreground">
                  {result?.analyzed_at ? `最近分析 ${fmtDateTime(result.analyzed_at)}` : '等待首个截图消息'}
                </div>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {QUICK_PROMPTS.map((prompt) => (
                  <button
                    key={prompt}
                    onClick={() => { void submitChat(prompt) }}
                    disabled={chatBusy}
                    className="rounded-full border border-primary/15 bg-background/80 px-3 py-1.5 text-xs text-muted-foreground transition hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {prompt}
                  </button>
                ))}
              </div>

              <div
                ref={chatViewportRef}
                className="mt-5 flex-1 space-y-3 overflow-y-auto rounded-xl bg-background/75 p-2"
              >
                {chatMessages.map((message) => {
                  const reportData = message.variant === 'report' ? message.reportData : undefined

                  return (
                    <div
                      key={message.id}
                      className={cn(
                        'max-w-[92%] rounded-xl px-4 py-3 text-sm leading-7 shadow-sm',
                        message.role === 'user' && 'ml-auto w-fit bg-primary text-primary-foreground',
                        message.role === 'assistant' && 'border border-primary/10 bg-card text-foreground',
                        message.role === 'system' && 'border border-amber-200 bg-amber-50 text-amber-700',
                        reportData && 'max-w-full border-0 bg-transparent p-0 shadow-none',
                      )}
                    >
                      {reportData ? (
                        <NutritionReportCard result={reportData} />
                      ) : (
                        <>
                          <div className="mb-1 flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em] opacity-70">
                            {message.role === 'user' ? <Send className="h-3 w-3" /> : <MessageSquare className="h-3 w-3" />}
                            {message.meta || (message.role === 'user' ? 'User' : 'Agent')}
                          </div>
                          {message.attachmentImage && (
                            <div className={cn('mb-3 flex', message.role === 'user' ? 'justify-end' : 'justify-start')}>
                              <div
                                className={cn(
                                  'overflow-hidden rounded-lg border bg-black/10',
                                  message.variant === 'capture'
                                    ? 'max-w-[220px] border-white/15 bg-white/5 p-1.5'
                                    : 'w-full border-white/15',
                                )}
                              >
                                <img
                                  src={message.attachmentImage}
                                  alt="Sent capture"
                                  className={cn(
                                    message.variant === 'capture'
                                      ? 'max-h-56 w-auto max-w-full object-contain'
                                      : 'max-h-56 w-full object-cover',
                                  )}
                                />
                              </div>
                            </div>
                          )}
                          {message.role === 'assistant' ? (
                            <ChatMarkdown content={message.content} />
                          ) : (
                            <div className="whitespace-pre-line">{message.content}</div>
                          )}
                        </>
                      )}
                    </div>
                  )
                })}

                {chatBusy && (
                  <div className="max-w-[92%] rounded-xl border border-primary/10 bg-card px-4 py-3 text-sm text-foreground">
                    <div className="mb-1 flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">
                      <MessageSquare className="h-3 w-3" />
                      {analyzing ? '正在查看这张餐盘' : '正在整理回复'}
                    </div>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {analyzing ? '马上给你这顿饭的营养判断' : '稍等一下，回复马上出来'}
                    </div>
                  </div>
                )}
              </div>

              <form onSubmit={handleChatSubmit} className="mt-4 border-t border-primary/10 pt-4">
                <div className="flex gap-3">
                  <input
                    value={chatInput}
                    onChange={(event) => setChatInput(event.target.value)}
                    placeholder="直接问：风险在哪里？蛋白质够吗？怎么优化？"
                    className="h-12 flex-1 rounded-xl border border-primary/15 bg-background px-4 text-sm outline-none transition focus:border-primary/40"
                  />
                  <button
                    type="submit"
                    disabled={!chatInput.trim() || chatBusy}
                    className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Send className="h-4 w-4" />
                    发送
                  </button>
                </div>
              </form>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
