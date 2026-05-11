import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import * as Switch from '@radix-ui/react-switch'
import * as Tabs from '@radix-ui/react-tabs'
import {
  AlertCircle,
  Brain,
  Camera,
  Image as ImageIcon,
  Loader2,
  MessageSquare,
  Monitor,
  Play,
  RefreshCw,
  Send,
  Settings,
  Sparkles,
  Square,
  Upload,
  Video,
  VideoOff,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { demoApi } from '@/api/client'
import { MediaMTXWhepClient } from '@/lib/mediamtx'
import { cn, fmtDateTime } from '@/lib/utils'
import toast from 'react-hot-toast'

interface DemoBBox {
  x1: number
  y1: number
  x2: number
  y2: number
}

interface FrameSize {
  width: number
  height: number
}

interface RecognizedDish {
  name: string
  confidence: number
  bbox?: DemoBBox | null
  bbox_source?: string
  position?: string
  notes?: string
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
  bbox?: DemoBBox | null
  bbox_source?: string
  position?: string
}

interface PreviewRegion {
  index: number
  bbox: DemoBBox | null
  confidence?: number
  source?: string
}

interface PreviewRegionResult {
  index: number
  bbox: DemoBBox | null
  matched_name?: string
  confidence?: number
  notes?: string
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
  has_dishes?: boolean
  image_base64?: string
  recognized_dishes: RecognizedDish[]
  matched_dishes: MatchedDish[]
  regions?: PreviewRegion[]
  region_results?: PreviewRegionResult[]
  nutrition: NutritionData
  suggestions: Suggestion[]
  follow_up_questions?: string[]
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
  followUpQuestions?: string[]
}

interface PreviewOverlayBox {
  key: string
  label: string
  confidence?: number
  tone: 'matched' | 'recognized' | 'region'
  left: number
  top: number
  width: number
  height: number
}

interface DemoCameraOption {
  channel_id: string
  name: string
  host?: string
  port?: number
  supports_snapshot?: boolean
}

type DemoMode = 'upload' | 'browser' | 'camera' | 'stream'
type DemoTab = 'workspace' | 'live-display'
type BrowserCameraPermissionState = PermissionState | 'unsupported' | 'unknown'

type NumericRecord = Record<string, number>

const LIVE_DISPLAY_CONFIG_STORAGE_KEY = 'nutrition-demo-live-display-config'

interface LiveDisplayConfig {
  streamUrl: string
  autoConnect: boolean
  autoAnalyzeEnabled: boolean
}

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

const REPORT_METRIC_KEYS = ['calories', 'protein', 'fat', 'carbohydrate', 'sodium', 'fiber'] as const

function readLiveDisplayConfig(): LiveDisplayConfig | null {
  if (typeof window === 'undefined') return null

  try {
    const raw = window.localStorage.getItem(LIVE_DISPLAY_CONFIG_STORAGE_KEY)
    if (!raw) return null

    const parsed = JSON.parse(raw) as Partial<LiveDisplayConfig>
    const streamUrl = typeof parsed.streamUrl === 'string' ? parsed.streamUrl.trim() : ''
    if (!streamUrl) return null

    return {
      streamUrl,
      autoConnect: parsed.autoConnect !== false,
      autoAnalyzeEnabled: Boolean(parsed.autoAnalyzeEnabled),
    }
  } catch {
    return null
  }
}

function writeLiveDisplayConfig(config: LiveDisplayConfig) {
  if (typeof window === 'undefined') return

  window.localStorage.setItem(LIVE_DISPLAY_CONFIG_STORAGE_KEY, JSON.stringify(config))
}

function createMessage(
  role: ChatMessage['role'],
  content: string,
  meta?: string,
  options?: Pick<ChatMessage, 'attachmentImage' | 'variant' | 'reportData' | 'followUpQuestions'>,
): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role,
    content,
    meta,
    attachmentImage: options?.attachmentImage,
    variant: options?.variant ?? 'default',
    reportData: options?.reportData,
    followUpQuestions: options?.followUpQuestions,
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

function normalizeBBox(value: unknown): DemoBBox | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const x1 = toFiniteNumber(record.x1, Number.NaN)
  const y1 = toFiniteNumber(record.y1, Number.NaN)
  const x2 = toFiniteNumber(record.x2, Number.NaN)
  const y2 = toFiniteNumber(record.y2, Number.NaN)
  if (![x1, y1, x2, y2].every(Number.isFinite)) return null

  const left = Math.min(x1, x2)
  const top = Math.min(y1, y2)
  const right = Math.max(x1, x2)
  const bottom = Math.max(y1, y2)
  if (right - left < 2 || bottom - top < 2) return null

  return { x1: left, y1: top, x2: right, y2: bottom }
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
    has_dishes: typeof data.has_dishes === 'boolean' ? data.has_dishes : undefined,
    image_base64: typeof data.image_base64 === 'string' ? data.image_base64 : undefined,
    recognized_dishes: Array.isArray(data.recognized_dishes)
      ? data.recognized_dishes.map((dish) => {
          const item = dish && typeof dish === 'object' ? dish as Record<string, unknown> : {}
          return {
            name: typeof item.name === 'string' ? item.name : '',
            confidence: toFiniteNumber(item.confidence),
            bbox: normalizeBBox(item.bbox),
            bbox_source: typeof item.bbox_source === 'string' ? item.bbox_source : undefined,
            position: typeof item.position === 'string' ? item.position : undefined,
            notes: typeof item.notes === 'string' ? item.notes : undefined,
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
            bbox: normalizeBBox(item.bbox),
            bbox_source: typeof item.bbox_source === 'string' ? item.bbox_source : undefined,
            position: typeof item.position === 'string' ? item.position : undefined,
          }
        }).filter((dish) => dish.name)
      : [],
    regions: Array.isArray(data.regions)
      ? data.regions.map((region) => {
          const item = region && typeof region === 'object' ? region as Record<string, unknown> : {}
          return {
            index: toFiniteNumber(item.index),
            bbox: normalizeBBox(item.bbox),
            confidence: toOptionalNumber(item.confidence),
            source: typeof item.source === 'string' ? item.source : undefined,
          }
        }).filter((region) => region.index > 0 && region.bbox)
      : [],
    region_results: Array.isArray(data.region_results)
      ? data.region_results.map((region) => {
          const item = region && typeof region === 'object' ? region as Record<string, unknown> : {}
          return {
            index: toFiniteNumber(item.index),
            bbox: normalizeBBox(item.bbox),
            matched_name: typeof item.matched_name === 'string' ? item.matched_name : undefined,
            confidence: toOptionalNumber(item.confidence),
            notes: typeof item.notes === 'string' ? item.notes : undefined,
          }
        }).filter((region) => region.index > 0 && region.bbox)
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
    follow_up_questions: normalizeFollowUpQuestions(data.follow_up_questions),
    notes: typeof data.notes === 'string' ? data.notes : undefined,
    analyzed_at: typeof data.analyzed_at === 'string' ? data.analyzed_at : undefined,
  }
}

function resolvePreviewOverlayBoxes(result: AnalysisResult | null, frameSize: FrameSize): PreviewOverlayBox[] {
  if (!result || !frameSize.width || !frameSize.height) return []

  const boxes: PreviewOverlayBox[] = []
  const seen = new Set<string>()
  const regionResultByIndex = new Map((result.region_results || []).map((item) => [item.index, item]))

  const pushBox = (
    key: string,
    label: string,
    bbox: DemoBBox | null | undefined,
    tone: PreviewOverlayBox['tone'],
    confidence?: number,
  ) => {
    if (!bbox || !label) return
    const width = bbox.x2 - bbox.x1
    const height = bbox.y2 - bbox.y1
    if (width <= 0 || height <= 0) return

    const signature = `${label}-${bbox.x1}-${bbox.y1}-${bbox.x2}-${bbox.y2}`
    if (seen.has(signature)) return
    seen.add(signature)

    boxes.push({
      key,
      label,
      confidence,
      tone,
      left: (bbox.x1 / frameSize.width) * 100,
      top: (bbox.y1 / frameSize.height) * 100,
      width: (width / frameSize.width) * 100,
      height: (height / frameSize.height) * 100,
    })
  }

  result.matched_dishes.forEach((dish, index) => {
    pushBox(`matched-${dish.id || index}`, dish.name, dish.bbox, 'matched', dish.confidence)
  })

  const hasMatchedBoxes = boxes.length > 0
  result.recognized_dishes.forEach((dish, index) => {
    pushBox(`recognized-${index}-${dish.name}`, dish.name, dish.bbox, hasMatchedBoxes ? 'matched' : 'recognized', dish.confidence)
  })

  if (boxes.length === 0) {
    ;(result.regions || []).forEach((region, index) => {
      const regionResult = regionResultByIndex.get(region.index)
      pushBox(
        `region-${region.index}-${index}`,
        regionResult?.matched_name || `菜区 ${region.index}`,
        region.bbox,
        'region',
        regionResult?.confidence ?? region.confidence,
      )
    })
  }

  if (boxes.length === 0) {
    ;(result.region_results || []).forEach((region, index) => {
      pushBox(
        `region-result-${region.index}-${index}`,
        region.matched_name || `菜区 ${region.index}`,
        region.bbox,
        'region',
        region.confidence,
      )
    })
  }

  return boxes.slice(0, 12)
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

function getPriorityNutrition(result: AnalysisResult, threshold = 60) {
  const dominant = getDominantNutrition(result)
  if (!dominant || dominant.percentage < threshold) return null
  return dominant
}

function normalizeFollowUpQuestions(source: unknown): string[] {
  if (!Array.isArray(source)) return []

  const questions: string[] = []
  source.forEach((item) => {
    if (typeof item !== 'string') return
    const question = item.trim()
    if (!question || questions.includes(question)) return
    questions.push(question)
  })
  return questions.slice(0, 3)
}

function buildFollowUpQuestions(result: AnalysisResult | null): string[] {
  if (result?.has_dishes === false) return []

  const priority = result ? getPriorityNutrition(result) : null
  const recognizedDishes = result
    ? Array.from(new Set(
        (result.matched_dishes.length > 0 ? result.matched_dishes : result.recognized_dishes)
          .map((dish) => dish.name)
          .filter(Boolean),
      ))
    : []
  const questions = [
    priority
      ? `${priority.label}偏高主要是哪些菜导致的？`
      : '这顿饭最需要先改哪一项？',
    recognizedDishes.length > 0
      ? '如果只能调整两样，优先动哪两样？'
      : '如果换一张更清晰的图，最值得确认什么？',
    '下一餐怎么搭配会更均衡？',
  ]

  return normalizeFollowUpQuestions(questions)
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

  if (result.has_dishes === false) {
    return {
      label: '等待上菜',
      description: '当前预览画面还没有稳定检测到菜品',
      badgeClass: 'border-border bg-secondary text-muted-foreground',
      dotClass: 'bg-slate-400',
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
  if (result.has_dishes === false) {
    return '当前画面里还没有稳定检测到菜品，系统会继续等待下一帧。'
  }

  const dishes = result.matched_dishes.slice(0, 4).map((dish) => dish.name)
  const priority = getPriorityNutrition(result)
  const leadingSuggestion = result.suggestions[0]?.message
  const parts = [
    dishes.length > 0
      ? `已识别 ${result.matched_dishes.length} 道菜，当前餐盘包含 ${dishes.join('、')}`
      : '当前截图没有稳定识别出菜品',
    priority
      ? `${priority.label}达到当日建议摄入的 ${priority.percentage.toFixed(0)}%`
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

  return `${label}约 ${formatNutritionValue(key, value)}，相当于当日建议摄入的 ${percentage.toFixed(0)}%，当前判断为${assessment}。`
}

function buildSuggestionDigest(result: AnalysisResult): string {
  if (result.has_dishes === false) {
    return '当前画面里还没有稳定检测到菜品，建议等餐盘进入画面后再继续分析。'
  }

  if (result.suggestions.length === 0) {
    const priority = getPriorityNutrition(result, 85)
    if (!priority) return '当前没有额外建议，建议继续观察连续样本。'
    if (priority.percentage >= 85) {
      return `先控制 ${priority.label} 负荷，再观察下一次截图的变化。`
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
      ? `主要负荷：${dominant.label} ${formatNutritionValue(dominant.key, dominant.value)}，约为当日建议摄入的 ${dominant.percentage.toFixed(0)}%。`
      : null,
    topNutrition.length > 0
      ? `营养概览：\n${topNutrition.map((item) => `- ${item.label} ${formatNutritionValue(item.key, item.value)}，${item.percentage.toFixed(0)}%`).join('\n')}`
      : null,
    `温馨建议：\n${suggestionLines.join('\n')}`,
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
  const priority = getPriorityNutrition(result)
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
        title: '温馨建议',
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
                <div className="text-xs text-slate-500">按当日建议摄入量占比展示，不代表单餐标准</div>
              </div>
              {priority && (
                <div className={cn('rounded-full border px-3 py-1 text-xs font-medium', getMetricTone(priority.percentage).chip)}>
                  当前重点：{priority.label}
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
                    <span>日建议值 {metric.recommended ? formatNutritionValue(metric.key, metric.recommended) : '--'}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white/90 p-4">
            <div className="text-sm font-medium text-slate-900">温馨建议</div>
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
                    className="max-w-full whitespace-normal break-words rounded-2xl border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-medium leading-5 text-slate-700"
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
                ? `${dominant.label} 是当前最需要关注的负荷项，已达到当日建议摄入的 ${dominant.percentage.toFixed(0)}%。`
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

  if (result.has_dishes === false) {
    return '这帧画面里还没有稳定检测到菜品。等餐盘进入取景区后，我会继续识别并给出分析。'
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

function getBrowserCameraErrorMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError') return '浏览器未授权摄像头权限。若之前点过拒绝，请到地址栏左侧的网站设置里允许摄像头后再试'
    if (error.name === 'NotFoundError') return '当前设备没有检测到可用摄像头'
    if (error.name === 'NotReadableError') return '摄像头正被其他应用占用，请关闭后重试'
    if (error.name === 'OverconstrainedError') return '选中的摄像头暂时不可用，请切换其他设备'
    if (error.name === 'SecurityError') return '当前环境不允许访问本机摄像头，请使用 HTTPS 或 localhost'
  }

  return error instanceof Error ? error.message : '无法打开本机摄像头'
}

export default function DemoPage() {
  const [activeDemoTab, setActiveDemoTab] = useState<DemoTab>('workspace')
  const [mode, setMode] = useState<DemoMode>(() => {
    const savedConfig = readLiveDisplayConfig()
    return savedConfig?.autoConnect && savedConfig.streamUrl ? 'stream' : 'upload'
  })
  const [cameraHost, setCameraHost] = useState('')
  const [cameraPort, setCameraPort] = useState('80')
  const [cameraUsername, setCameraUsername] = useState('admin')
  const [cameraPassword, setCameraPassword] = useState('')
  const [channelId, setChannelId] = useState('1')
  const [cameraOptions, setCameraOptions] = useState<DemoCameraOption[]>([])
  const [cameraSourceLabel, setCameraSourceLabel] = useState('')
  const [cameraSourceSupportsSnapshot, setCameraSourceSupportsSnapshot] = useState(false)
  const [capturedImage, setCapturedImage] = useState<string | null>(null)
  const [capturedImageSize, setCapturedImageSize] = useState<FrameSize>({ width: 0, height: 0 })
  const [livePreviewSize, setLivePreviewSize] = useState<FrameSize>({ width: 0, height: 0 })
  const [analysisFrameSize, setAnalysisFrameSize] = useState<FrameSize>({ width: 0, height: 0 })
  const [livePreviewAnalysisFrameSize, setLivePreviewAnalysisFrameSize] = useState<FrameSize>({ width: 0, height: 0 })
  const [manualAnalyzing, setManualAnalyzing] = useState(false)
  const [autoAnalyzing, setAutoAnalyzing] = useState(false)
  const [autoAnalyzeEnabled, setAutoAnalyzeEnabled] = useState(() => Boolean(readLiveDisplayConfig()?.autoAnalyzeEnabled))
  const [autoAnalyzeError, setAutoAnalyzeError] = useState<string | null>(null)
  const [capturing, setCapturing] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [livePreviewResult, setLivePreviewResult] = useState<AnalysisResult | null>(null)
  const [showSettings, setShowSettings] = useState(false)

  const [browserDevices, setBrowserDevices] = useState<MediaDeviceInfo[]>([])
  const [browserDeviceId, setBrowserDeviceId] = useState('')
  const [browserCameraLabel, setBrowserCameraLabel] = useState('')
  const [browserError, setBrowserError] = useState<string | null>(null)
  const [browserPreviewing, setBrowserPreviewing] = useState(false)
  const [browserConnecting, setBrowserConnecting] = useState(false)
  const [browserPermissionState, setBrowserPermissionState] = useState<BrowserCameraPermissionState>('unknown')

  const [streaming, setStreaming] = useState(false)
  const [streamUrl, setStreamUrl] = useState(() => readLiveDisplayConfig()?.streamUrl ?? '')
  const [streamError, setStreamError] = useState<string | null>(null)
  const [hasSavedLiveDisplayConfig, setHasSavedLiveDisplayConfig] = useState(() => Boolean(readLiveDisplayConfig()?.streamUrl))
  const [savedLiveDisplayStreamUrl, setSavedLiveDisplayStreamUrl] = useState(() => readLiveDisplayConfig()?.streamUrl ?? '')
  const [liveDisplayAutoConnect, setLiveDisplayAutoConnect] = useState(() => readLiveDisplayConfig()?.autoConnect ?? true)
  const [liveDisplayConfigOpen, setLiveDisplayConfigOpen] = useState(() => !readLiveDisplayConfig()?.streamUrl)

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    createMessage('assistant', '我是营养分析 Agent。左侧输入画面后，我会在右侧持续输出判断，你也可以直接追问。', '系统就绪'),
  ])
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const previewClientRef = useRef<MediaMTXWhepClient | null>(null)
  const browserStreamRef = useRef<MediaStream | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chatViewportRef = useRef<HTMLDivElement>(null)
  const replyTimerRef = useRef<number | null>(null)
  const manualAnalyzingRef = useRef(false)
  const autoAnalyzingRef = useRef(false)
  const liveDisplayAutoConnectAttemptedRef = useRef(false)
  const browserCameraSupported = typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia)

  useEffect(() => {
    manualAnalyzingRef.current = manualAnalyzing
  }, [manualAnalyzing])

  useEffect(() => {
    autoAnalyzingRef.current = autoAnalyzing
  }, [autoAnalyzing])

  const loadImageSizeFromUrl = useCallback((src: string) => (
    new Promise<FrameSize>((resolve) => {
      const image = new window.Image()
      image.onload = () => {
        resolve({
          width: image.naturalWidth || 0,
          height: image.naturalHeight || 0,
        })
      }
      image.onerror = () => resolve({ width: 0, height: 0 })
      image.src = src
    })
  ), [])

  const syncLivePreviewSize = useCallback(() => {
    const video = videoRef.current
    if (!video?.videoWidth || !video.videoHeight) return

    setLivePreviewSize((current) => (
      current.width === video.videoWidth && current.height === video.videoHeight
        ? current
        : { width: video.videoWidth, height: video.videoHeight }
    ))
  }, [])

  const captureFrameFromVideo = useCallback((
    sourceLabel: string,
    notReadyMessage: string,
    showError = true,
  ) => {
    const video = videoRef.current
    if (!video) return null

    if (!video.videoWidth || !video.videoHeight) {
      if (showError) toast.error(notReadyMessage)
      return null
    }

    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight

    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    ctx.drawImage(video, 0, 0)
    const displayImage = canvas.toDataURL('image/jpeg', 0.88)
    const analysisPayload = displayImage.includes(',') ? displayImage.split(',', 2)[1] : displayImage

    return {
      displayImage,
      analysisPayload,
      frameSize: { width: video.videoWidth, height: video.videoHeight },
      sourceLabel,
    }
  }, [])

  const stopStreamPreview = useCallback(() => {
    if (previewClientRef.current) {
      previewClientRef.current.close()
      previewClientRef.current = null
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }

    setStreaming(false)
    setLivePreviewSize({ width: 0, height: 0 })
  }, [])

  const stopBrowserPreview = useCallback(() => {
    if (browserStreamRef.current) {
      browserStreamRef.current.getTracks().forEach((track) => track.stop())
      browserStreamRef.current = null
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null
    }

    setBrowserPreviewing(false)
    setBrowserConnecting(false)
    setBrowserCameraLabel('')
    setLivePreviewSize({ width: 0, height: 0 })
  }, [])

  const refreshBrowserPermissionState = useCallback(async () => {
    if (!browserCameraSupported) {
      setBrowserPermissionState('unsupported')
      return
    }

    if (!navigator.permissions?.query) {
      setBrowserPermissionState((prev) => (prev === 'unknown' ? 'prompt' : prev))
      return
    }

    try {
      const status = await navigator.permissions.query({ name: 'camera' as PermissionName })
      setBrowserPermissionState(status.state)
    } catch {
      setBrowserPermissionState((prev) => (prev === 'unknown' ? 'prompt' : prev))
    }
  }, [browserCameraSupported])

  const refreshBrowserDevices = useCallback(async (preferredDeviceId?: string) => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setBrowserDevices([])
      return
    }

    try {
      const devices = await navigator.mediaDevices.enumerateDevices()
      const videoInputs = devices.filter((device) => device.kind === 'videoinput')
      setBrowserDevices(videoInputs)
      setBrowserDeviceId((prev) => {
        const nextDeviceId = preferredDeviceId ?? prev
        if (nextDeviceId && videoInputs.some((device) => device.deviceId === nextDeviceId)) {
          return nextDeviceId
        }
        return videoInputs[0]?.deviceId ?? ''
      })
    } catch {
      setBrowserDevices([])
    }
  }, [])

  const startBrowserPreview = useCallback(async (preferredDeviceId?: string) => {
    if (!browserCameraSupported) {
      setBrowserError('当前浏览器不支持 MediaDevices API')
      setBrowserPermissionState('unsupported')
      return
    }

    if (!window.isSecureContext) {
      setBrowserError('本机摄像头仅支持 HTTPS 或 localhost 环境')
      return
    }

    const requestedDeviceId = preferredDeviceId ?? browserDeviceId
    stopStreamPreview()
    stopBrowserPreview()
    setBrowserError(null)
    setBrowserConnecting(true)

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: requestedDeviceId
          ? { deviceId: { exact: requestedDeviceId } }
          : { facingMode: 'environment' },
      })

      browserStreamRef.current = stream

      if (videoRef.current) {
        videoRef.current.srcObject = stream
        try {
          await videoRef.current.play()
        } catch {}
      }

      const activeTrack = stream.getVideoTracks()[0]
      const activeDeviceId = activeTrack?.getSettings().deviceId || requestedDeviceId
      const nextLabel = activeTrack?.label
        || browserDevices.find((device) => device.deviceId === activeDeviceId)?.label
        || '本机摄像头'

      setBrowserCameraLabel(nextLabel)
      setBrowserPermissionState('granted')
      setBrowserPreviewing(true)
      await refreshBrowserDevices(activeDeviceId)
    } catch (error) {
      console.error('Local browser camera error:', error)
      setBrowserError(getBrowserCameraErrorMessage(error))
      if (error instanceof DOMException && error.name === 'NotAllowedError') {
        setBrowserPermissionState('denied')
      } else {
        void refreshBrowserPermissionState()
      }
      stopBrowserPreview()
    } finally {
      setBrowserConnecting(false)
    }
  }, [browserCameraSupported, browserDeviceId, browserDevices, refreshBrowserDevices, refreshBrowserPermissionState, stopBrowserPreview, stopStreamPreview])

  const startStreamPreview = useCallback(async () => {
    const source = streamUrl.trim().replace(/^\/+|\/+$/g, '')
    if (!source) {
      setStreamError('请输入流名称，例如 camera1')
      return
    }

    stopBrowserPreview()
    stopStreamPreview()
    setStreamError(null)
    setStreaming(true)

    try {
      const client = new MediaMTXWhepClient({
        url: `/rtc/${source.split('/').map((segment) => encodeURIComponent(segment)).join('/')}/whep`,
        onTrack: (event) => {
          if (!streamRef.current) {
            streamRef.current = new MediaStream()
            if (videoRef.current) {
              videoRef.current.srcObject = streamRef.current
            }
          }

          const previewStream = streamRef.current
          if (!previewStream.getTracks().some((track) => track.id === event.track.id)) {
            previewStream.addTrack(event.track)
          }

          event.track.onended = () => {
            previewStream.removeTrack(event.track)
          }
        },
        onError: (message) => {
          setStreamError(message)
          stopStreamPreview()
        },
      })

      previewClientRef.current = client
      await client.start()
    } catch (error) {
      console.error('MediaMTX preview error:', error)
      setStreamError(error instanceof Error ? error.message : '连接失败')
      stopStreamPreview()
    }
  }, [stopStreamPreview, streamUrl])

  const persistLiveDisplayConfig = useCallback(() => {
    const source = streamUrl.trim().replace(/^\/+|\/+$/g, '')
    if (!source) {
      toast.error('请输入流名称，例如 camera1')
      return false
    }

    writeLiveDisplayConfig({
      streamUrl: source,
      autoConnect: liveDisplayAutoConnect,
      autoAnalyzeEnabled: autoAnalyzeEnabled,
    })
    setStreamUrl(source)
    setHasSavedLiveDisplayConfig(true)
    setSavedLiveDisplayStreamUrl(source)
    setLiveDisplayConfigOpen(false)
    return true
  }, [autoAnalyzeEnabled, liveDisplayAutoConnect, streamUrl])

  const saveLiveDisplayConfigAndConnect = useCallback(() => {
    if (!persistLiveDisplayConfig()) return
    setMode('stream')
    void startStreamPreview()
  }, [persistLiveDisplayConfig, startStreamPreview])

  useEffect(() => {
    const video = videoRef.current
    if (!video) return

    const activeStream = mode === 'stream'
      ? streamRef.current
      : mode === 'browser'
        ? browserStreamRef.current
        : null

    if (!activeStream || video.srcObject === activeStream) return

    video.srcObject = activeStream
    void video.play().catch(() => {})
    syncLivePreviewSize()
  }, [activeDemoTab, browserPreviewing, mode, streaming, syncLivePreviewSize])

  useEffect(() => {
    if (liveDisplayAutoConnectAttemptedRef.current) return
    if (!hasSavedLiveDisplayConfig) return
    if (!liveDisplayAutoConnect || !streamUrl.trim()) return
    if (streamUrl.trim() !== savedLiveDisplayStreamUrl) return

    liveDisplayAutoConnectAttemptedRef.current = true
    setMode('stream')
    void startStreamPreview()
  }, [hasSavedLiveDisplayConfig, liveDisplayAutoConnect, savedLiveDisplayStreamUrl, startStreamPreview, streamUrl])

  const analyzeCaptureManually = useCallback(async (
    displayImage: string,
    sourceLabel: string,
    options?: {
      analysisPayload?: string
      frameSize?: FrameSize
    },
  ) => {
    const analysisPayload = options?.analysisPayload ?? displayImage
    const frameSize = options?.frameSize ?? { width: 0, height: 0 }
    const pureBase64 = analysisPayload.includes(',') ? analysisPayload.split(',', 2)[1] : analysisPayload

    setCapturedImage(displayImage)
    if (frameSize.width && frameSize.height) {
      setAnalysisFrameSize(frameSize)
    }
    setResult(null)
    setAutoAnalyzeError(null)
    setChatMessages((prev) => [
      ...prev,
      createMessage(
        'user',
        '请基于这张最新餐盘截图输出完整营养报告，并给出可以直接执行的建议。',
        sourceLabel,
        { attachmentImage: displayImage, variant: 'capture' },
      ),
    ])

    manualAnalyzingRef.current = true
    setManualAnalyzing(true)
    setChatBusy(true)
    try {
      const response = await demoApi.quickAnalyze(pureBase64, { include_follow_up_questions: true })
      const normalized = normalizeAnalysisResult(response.data.data)
      setResult(normalized)
      setLivePreviewResult(normalized)
      if (frameSize.width && frameSize.height) {
        setAnalysisFrameSize(frameSize)
        setLivePreviewAnalysisFrameSize(frameSize)
      }
      const followUpQuestions = normalizeFollowUpQuestions(normalized.follow_up_questions)
        .concat(buildFollowUpQuestions(normalized))
        .filter((question, index, list) => question && list.indexOf(question) === index)
        .slice(0, 3)
      setChatMessages((prev) => [
        ...prev,
        normalized.has_dishes === false
          ? createMessage(
              'assistant',
              '这张截图里还没有稳定检测到菜品。把餐盘完整放进取景区后再截一帧，我会继续识别并分析。',
              '等待上菜',
              { followUpQuestions },
            )
          : createMessage('assistant', buildAgentReport(normalized), '营养报告', {
              variant: 'report',
              reportData: normalized,
              followUpQuestions,
            }),
      ])
    } catch (error) {
      toast.error('分析失败，请重试')
      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', '这张截图已收到，但本次分析没有成功完成。请重试，或换一张更清晰的图。', '分析失败'),
      ])
    } finally {
      manualAnalyzingRef.current = false
      setManualAnalyzing(false)
      setChatBusy(false)
    }
  }, [])

  const analyzeLivePreviewFrame = useCallback(async (
    _displayImage: string,
    frameSize: FrameSize,
    analysisPayload?: string,
  ) => {
    const pureBase64 = (analysisPayload ?? _displayImage).includes(',')
      ? (analysisPayload ?? _displayImage).split(',', 2)[1]
      : (analysisPayload ?? _displayImage)

    autoAnalyzingRef.current = true
    setAutoAnalyzing(true)
    setAutoAnalyzeError(null)
    try {
      const response = await demoApi.quickAnalyze(pureBase64, {
        include_follow_up_questions: false,
        silentErrors: true,
      })
      const normalized = normalizeAnalysisResult(response.data.data)
      setLivePreviewAnalysisFrameSize(frameSize)
      setLivePreviewResult(normalized)
    } catch (error) {
      console.error('Auto preview analysis failed:', error)
      setAutoAnalyzeError(error instanceof Error ? error.message : '自动分析失败')
      setAutoAnalyzeEnabled(false)
    } finally {
      autoAnalyzingRef.current = false
      setAutoAnalyzing(false)
    }
  }, [])

  const captureFrameFromBrowser = useCallback(() => {
    if (!browserPreviewing) return
    const sourceLabel = browserCameraLabel ? `本机摄像头截图 · ${browserCameraLabel}` : '本机摄像头截图'
    const frame = captureFrameFromVideo(sourceLabel, '本机摄像头画面还没准备好，请稍后再试')
    if (!frame) return
    void analyzeCaptureManually(frame.displayImage, frame.sourceLabel, {
      analysisPayload: frame.analysisPayload,
      frameSize: frame.frameSize,
    })
  }, [analyzeCaptureManually, browserCameraLabel, browserPreviewing, captureFrameFromVideo])

  const captureFrameFromStream = useCallback(() => {
    if (!streaming) return
    const frame = captureFrameFromVideo(`实时流截图 · ${streamUrl || '未命名流'}`, '实时画面还没准备好，请稍后再试')
    if (!frame) return
    void analyzeCaptureManually(frame.displayImage, frame.sourceLabel, {
      analysisPayload: frame.analysisPayload,
      frameSize: frame.frameSize,
    })
  }, [analyzeCaptureManually, captureFrameFromVideo, streamUrl, streaming])

  useEffect(() => {
    return () => {
      stopStreamPreview()
      stopBrowserPreview()
      if (replyTimerRef.current) {
        window.clearTimeout(replyTimerRef.current)
      }
    }
  }, [stopBrowserPreview, stopStreamPreview])

  useEffect(() => {
    if (!browserCameraSupported) return

    void refreshBrowserPermissionState()
    void refreshBrowserDevices()

    const handleDeviceChange = () => {
      void refreshBrowserDevices()
      void refreshBrowserPermissionState()
    }

    navigator.mediaDevices.addEventListener?.('devicechange', handleDeviceChange)

    return () => {
      navigator.mediaDevices.removeEventListener?.('devicechange', handleDeviceChange)
    }
  }, [browserCameraSupported, refreshBrowserDevices, refreshBrowserPermissionState])

  useEffect(() => {
    if (!chatViewportRef.current) return
    chatViewportRef.current.scrollTo({
      top: chatViewportRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [chatBusy, chatMessages])

  useEffect(() => {
    demoApi.cameras()
      .then((res) => {
        const data = res.data.data || {}
        const nextOptions = Array.isArray(data.cameras) ? data.cameras : []
        setCameraOptions(nextOptions)
        setCameraSourceSupportsSnapshot(Boolean(data.supports_snapshot))
        const activeSource = data.active_video_source
        setCameraSourceLabel(activeSource?.name ? `${activeSource.name} · ${activeSource.source_type}` : '')
        if (nextOptions.length > 0) {
          setChannelId((prev) => (
            nextOptions.some((camera: DemoCameraOption) => String(camera.channel_id) === prev)
              ? prev
              : String(nextOptions[0].channel_id || prev || '1')
          ))
        }
      })
      .catch(() => {
        setCameraOptions([])
        setCameraSourceLabel('')
        setCameraSourceSupportsSnapshot(false)
      })
  }, [])

  useEffect(() => {
    if (mode === 'browser' || mode === 'stream') return
    setAutoAnalyzeEnabled(false)
    setAutoAnalyzeError(null)
    setLivePreviewResult(null)
    setLivePreviewAnalysisFrameSize({ width: 0, height: 0 })
  }, [mode])

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = async (loadEvent) => {
      const base64 = loadEvent.target?.result as string
      const frameSize = await loadImageSizeFromUrl(base64)
      await analyzeCaptureManually(base64, '上传图片', { frameSize })
    }
    reader.readAsDataURL(file)
  }

  const captureFromCamera = async () => {
    if (!cameraHost && !cameraSourceSupportsSnapshot) {
      toast.error('请先在后台配置海康视频源，或临时填写摄像头地址')
      return
    }

    setCapturing(true)
    try {
      const payload: { channel_id?: string; host?: string; port?: number; username?: string; password?: string } = {
        channel_id: channelId,
      }
      if (cameraHost.trim()) {
        payload.host = cameraHost.trim()
        payload.port = parseInt(cameraPort, 10) || 80
        payload.username = cameraUsername
        payload.password = cameraPassword
      }
      const response = await demoApi.capture(payload)

      const base64 = `data:${response.data.data.content_type};base64,${response.data.data.image_base64}`
      const frameSize = await loadImageSizeFromUrl(base64)
      await analyzeCaptureManually(base64, '摄像头抓拍', {
        analysisPayload: response.data.data.image_base64,
        frameSize,
      })
    } catch (error) {
      toast.error('抓拍失败，请检查摄像头配置')
    } finally {
      setCapturing(false)
    }
  }

  const reanalyze = () => {
    if (capturedImage) {
      void analyzeCaptureManually(capturedImage, '重新分析当前截图', {
        frameSize: analysisFrameSize.width && analysisFrameSize.height ? analysisFrameSize : capturedImageSize,
      })
    }
  }

  const clearAll = () => {
    setCapturedImage(null)
    setCapturedImageSize({ width: 0, height: 0 })
    setAnalysisFrameSize({ width: 0, height: 0 })
    setLivePreviewAnalysisFrameSize({ width: 0, height: 0 })
    setResult(null)
    setLivePreviewResult(null)
    setStreamError(null)
    setBrowserError(null)
    setAutoAnalyzeError(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  useEffect(() => {
    if (!autoAnalyzeEnabled) return
    if (mode !== 'browser' && mode !== 'stream') return
    if (mode === 'browser' && !browserPreviewing) return
    if (mode === 'stream' && !streaming) return

    const timer = window.setInterval(() => {
      if (manualAnalyzingRef.current || autoAnalyzingRef.current) return

      const frame = mode === 'browser'
        ? captureFrameFromVideo(
            browserCameraLabel ? `本机摄像头截图 · ${browserCameraLabel}` : '本机摄像头截图',
            '本机摄像头画面还没准备好，请稍后再试',
            false,
          )
        : captureFrameFromVideo(
            `实时流截图 · ${streamUrl || '未命名流'}`,
            '实时画面还没准备好，请稍后再试',
            false,
          )

      if (!frame) return
      void analyzeLivePreviewFrame(frame.displayImage, frame.frameSize, frame.analysisPayload)
    }, 1000)

    return () => window.clearInterval(timer)
  }, [
    analyzeLivePreviewFrame,
    autoAnalyzeEnabled,
    browserCameraLabel,
    browserPreviewing,
    captureFrameFromVideo,
    mode,
    streamUrl,
    streaming,
  ])

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
        createMessage(
          'assistant',
          response.data.data.reply || '当前没有拿到有效回复，请重试。',
          'Agent 回复',
          {
            followUpQuestions: normalizeFollowUpQuestions(response.data.data.follow_up_questions)
              .concat(buildFollowUpQuestions(result))
              .filter((question, index, list) => question && list.indexOf(question) === index)
              .slice(0, 3),
          },
        ),
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

  const livePreviewing = mode === 'stream' ? streaming : mode === 'browser' ? browserPreviewing : false
  const previewAnalysisResult = livePreviewing && livePreviewResult ? livePreviewResult : result
  const status = getResultStatus(previewAnalysisResult)
  const anyAnalysisRunning = manualAnalyzing || autoAnalyzing
  const latestAssistantMessageId = [...chatMessages].reverse().find((message) => message.role === 'assistant')?.id
  const browserAccessLabel = browserPermissionState === 'granted'
    ? '已授权'
    : browserPermissionState === 'denied'
      ? '已拒绝'
      : browserPermissionState === 'unsupported'
        ? '不支持'
        : browserConnecting
          ? '请求中'
          : '待授权'
  const browserActionLabel = browserPermissionState === 'denied'
    ? '重新请求授权'
    : browserPermissionState === 'granted'
      ? '建立预览'
      : '请求授权并预览'
  const sourceText = mode === 'stream'
    ? streaming
      ? `实时流 ${streamUrl || '未命名'} 在线`
      : '等待连接实时流'
    : mode === 'browser'
      ? browserConnecting
        ? '正在连接本机摄像头'
        : browserPreviewing
          ? `本机摄像头 ${browserCameraLabel || '在线'}`
          : '等待连接本机摄像头'
      : mode === 'camera'
      ? cameraHost
        ? `摄像头 ${cameraHost}:${cameraPort}`
        : '等待填写摄像头地址'
      : capturedImage
        ? '上传样本已载入'
        : '发来一张餐盘图就能开始'
  const autoAnalyzeSupported = mode === 'browser' || mode === 'stream'
  const livePreviewTitle = mode === 'stream' ? 'Live feed' : mode === 'browser' ? 'Browser camera' : 'Capture frame'
  const prioritizeLivePreview = livePreviewing && (mode === 'stream' || mode === 'browser')
  const modeBadgeLabel = mode === 'upload' ? '上传'
    : mode === 'browser' ? '本机'
      : mode === 'camera' ? '抓拍'
        : '实时流'
  const browserStatusText = browserConnecting ? '连接中' : browserPreviewing ? '在线' : browserAccessLabel
  const streamStatusText = streaming ? '在线' : '待连接'
  const previewSurfaceSize = livePreviewing
    ? (livePreviewSize.width && livePreviewSize.height ? livePreviewSize : livePreviewAnalysisFrameSize)
    : (capturedImageSize.width && capturedImageSize.height ? capturedImageSize : analysisFrameSize)
  const previewOverlayFrameSize = livePreviewing && livePreviewAnalysisFrameSize.width && livePreviewAnalysisFrameSize.height
    ? livePreviewAnalysisFrameSize
    : analysisFrameSize.width && analysisFrameSize.height
    ? analysisFrameSize
    : previewSurfaceSize
  const previewAspectRatio = previewSurfaceSize.width && previewSurfaceSize.height
    ? `${previewSurfaceSize.width} / ${previewSurfaceSize.height}`
    : '16 / 11'
  const previewOverlayBoxes = resolvePreviewOverlayBoxes(previewAnalysisResult, previewOverlayFrameSize)
  const autoAnalyzeStatusText = !autoAnalyzeEnabled
    ? '手动触发'
    : !livePreviewing
      ? '预览就绪后开始'
      : autoAnalyzing
        ? '正在分析最新帧'
        : '每秒自动截图分析'
  const liveDisplayDishes = previewAnalysisResult
    ? Array.from(new Set(
        (previewAnalysisResult.matched_dishes.length > 0
          ? previewAnalysisResult.matched_dishes
          : previewAnalysisResult.recognized_dishes)
          .map((dish) => dish.name)
          .filter(Boolean),
      )).slice(0, 6)
    : []
  const liveDisplayDominant = previewAnalysisResult ? getDominantNutrition(previewAnalysisResult) : null
  const liveDisplaySuggestion = previewAnalysisResult
    ? buildSuggestionDigest(previewAnalysisResult)
    : '连接实时流并开启自动分析后，系统会在画面上叠加菜品识别与营养摘要。'
  const liveDisplayConfigured = Boolean(streamUrl.trim()) && !liveDisplayConfigOpen

  const liveDisplayPanel = (
    <section className="relative min-h-[calc(100vh-8rem)] overflow-hidden rounded-xl border border-slate-800 bg-slate-950 text-white shadow-[0_24px_80px_rgba(15,23,42,0.35)]">
      <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.035)_1px,transparent_1px)] bg-[size:34px_34px]" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_15%,rgba(16,185,129,0.16),transparent_28%),radial-gradient(circle_at_82%_18%,rgba(59,130,246,0.14),transparent_30%),linear-gradient(180deg,rgba(2,6,23,0.08),rgba(2,6,23,0.72))]" />

      <div className="relative z-10 flex min-h-[calc(100vh-8rem)] flex-col">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 bg-black/20 px-4 py-3 backdrop-blur sm:px-5">
          <div className="min-w-0">
            <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-white/50">Live Recognition Display</div>
            <h2 className="mt-1 truncate text-lg font-semibold tracking-tight text-white sm:text-xl">
              {streamUrl.trim() ? `实时视频 · ${streamUrl.trim()}` : '实时视频'}
            </h2>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.08] px-3 py-1.5 text-xs text-white/80">
              <span className={cn('h-2 w-2 rounded-full', streaming ? 'bg-emerald-400' : 'bg-white/35')} />
              {streaming ? '视频在线' : '等待连接'}
            </div>
            <div className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs', status.badgeClass)}>
              <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
              {status.label}
            </div>
            <button
              onClick={() => setLiveDisplayConfigOpen(true)}
              className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.08] px-3 py-1.5 text-xs text-white/80 transition hover:bg-white/[0.14]"
            >
              <Settings className="h-3.5 w-3.5" />
              配置视频
            </button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-0 xl:grid-cols-[minmax(0,1fr)_340px]">
          <div className="relative flex min-h-[56vh] items-center justify-center overflow-hidden bg-black xl:min-h-0">
            <div className="relative w-full overflow-hidden bg-black" style={{ aspectRatio: previewAspectRatio }}>
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                onLoadedMetadata={syncLivePreviewSize}
                onCanPlay={syncLivePreviewSize}
                className="h-full w-full object-contain"
              />

              {!streaming && (
                <div className="absolute inset-0 flex items-center justify-center bg-slate-950/70">
                  <div className="px-6 text-center">
                    <VideoOff className="mx-auto h-12 w-12 text-white/35" />
                    <p className="mt-4 text-sm text-white/70">
                      {streamUrl.trim() ? '点击连接后显示实时画面' : '请先配置实时流名称'}
                    </p>
                  </div>
                </div>
              )}

              {previewOverlayBoxes.length > 0 && (
                <div className="pointer-events-none absolute inset-0">
                  {previewOverlayBoxes.map((box) => (
                    <div
                      key={`live-${box.key}`}
                      className={cn(
                        'absolute rounded-lg border-2 shadow-[0_0_24px_rgba(15,23,42,0.28)]',
                        box.tone === 'matched' && 'border-emerald-300/95 bg-emerald-400/12',
                        box.tone === 'recognized' && 'border-sky-300/95 bg-sky-400/12',
                        box.tone === 'region' && 'border-amber-300/95 bg-amber-300/12',
                      )}
                      style={{
                        left: `${box.left}%`,
                        top: `${box.top}%`,
                        width: `${box.width}%`,
                        height: `${box.height}%`,
                      }}
                    >
                      <div className={cn(
                        'absolute left-2 top-2 rounded-md px-2.5 py-1 text-xs font-medium leading-none text-white shadow-sm',
                        box.tone === 'matched' && 'bg-emerald-600',
                        box.tone === 'recognized' && 'bg-sky-600',
                        box.tone === 'region' && 'bg-amber-500 text-black',
                      )}>
                        {box.label}
                        {typeof box.confidence === 'number' ? ` ${(box.confidence * 100).toFixed(0)}%` : ''}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {autoAnalyzing && (
                <div className="absolute left-4 top-4 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/40 px-3 py-1.5 text-xs text-white/85 backdrop-blur">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  正在分析最新帧
                </div>
              )}
            </div>

            {(liveDisplayConfigOpen || !liveDisplayConfigured) && (
              <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/72 px-4 backdrop-blur-sm">
                <div className="w-full max-w-md rounded-xl border border-white/[0.12] bg-slate-950/[0.92] p-5 shadow-[0_24px_80px_rgba(0,0,0,0.42)]">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-[11px] font-mono uppercase tracking-[0.24em] text-white/45">Stream Config</div>
                      <h3 className="mt-2 text-lg font-semibold text-white">配置实时视频</h3>
                    </div>
                    {streamUrl.trim() && (
                      <button
                        onClick={() => setLiveDisplayConfigOpen(false)}
                        className="rounded-full border border-white/10 p-1.5 text-white/60 transition hover:text-white"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    )}
                  </div>

                  <div className="mt-5 space-y-4">
                    <label className="block">
                      <div className="mb-1.5 text-xs font-medium text-white/60">流名称</div>
                      <input
                        type="text"
                        value={streamUrl}
                        onChange={(event) => setStreamUrl(event.target.value)}
                        placeholder="camera1"
                        className="h-11 w-full rounded-lg border border-white/[0.12] bg-white/[0.08] px-3 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-emerald-300/60"
                      />
                    </label>

                    <label className="flex items-center justify-between gap-4 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2.5">
                      <span className="text-sm text-white/75">刷新后自动连接</span>
                      <input
                        type="checkbox"
                        checked={liveDisplayAutoConnect}
                        onChange={(event) => setLiveDisplayAutoConnect(event.target.checked)}
                        className="h-4 w-4 rounded border-white/20"
                      />
                    </label>

                    <label className="flex items-center justify-between gap-4 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2.5">
                      <span className="text-sm text-white/75">自动分析画面</span>
                      <input
                        type="checkbox"
                        checked={autoAnalyzeEnabled}
                        onChange={(event) => setAutoAnalyzeEnabled(event.target.checked)}
                        className="h-4 w-4 rounded border-white/20"
                      />
                    </label>

                    {streamError && (
                      <div className="flex items-start gap-2 rounded-lg border border-rose-300/25 bg-rose-500/[0.12] px-3 py-2.5 text-xs leading-5 text-rose-100">
                        <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                        {streamError}
                      </div>
                    )}

                    <div className="grid gap-2 sm:grid-cols-2">
                      <button
                        onClick={saveLiveDisplayConfigAndConnect}
                        className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-emerald-500 px-4 text-sm font-medium text-white transition hover:bg-emerald-400"
                      >
                        <Play className="h-4 w-4" />
                        保存并连接
                      </button>
                      <button
                        onClick={() => {
                          if (!persistLiveDisplayConfig()) return
                          toast.success('实时视频配置已保存')
                        }}
                        className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-white/[0.12] bg-white/[0.08] px-4 text-sm font-medium text-white/80 transition hover:bg-white/[0.12]"
                      >
                        保存配置
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <aside className="border-t border-white/10 bg-slate-950/86 p-4 backdrop-blur xl:border-l xl:border-t-0">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
              <div className="rounded-xl border border-white/10 bg-white/[0.06] p-4">
                <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-white/40">Analysis</div>
                <div className="mt-3 flex items-center gap-2 text-sm text-white/80">
                  {autoAnalyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4 text-emerald-300" />}
                  {autoAnalyzeStatusText}
                </div>
                <div className="mt-2 text-xs leading-5 text-white/45">
                  {previewAnalysisResult?.analyzed_at ? fmtDateTime(previewAnalysisResult.analyzed_at) : '暂无分析时间'}
                </div>
              </div>

              <div className="rounded-xl border border-white/10 bg-white/[0.06] p-4">
                <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-white/40">Detected</div>
                <div className="mt-3 text-3xl font-semibold leading-none text-white">{liveDisplayDishes.length}</div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {liveDisplayDishes.length > 0 ? liveDisplayDishes.map((dish) => (
                    <span key={dish} className="rounded-full border border-emerald-300/20 bg-emerald-400/10 px-2.5 py-1 text-xs text-emerald-100">
                      {dish}
                    </span>
                  )) : (
                    <span className="text-xs leading-5 text-white/45">等待识别菜品</span>
                  )}
                </div>
              </div>

              <div className="rounded-xl border border-white/10 bg-white/[0.06] p-4">
                <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-white/40">Nutrition Focus</div>
                {liveDisplayDominant ? (
                  <div className="mt-3">
                    <div className="flex items-end justify-between gap-3">
                      <div className="text-lg font-semibold text-white">{liveDisplayDominant.label}</div>
                      <div className="font-mono text-sm text-emerald-200">{liveDisplayDominant.percentage.toFixed(0)}%</div>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-emerald-400"
                        style={{ width: `${Math.min(100, Math.max(0, liveDisplayDominant.percentage))}%` }}
                      />
                    </div>
                    <div className="mt-2 text-xs text-white/45">{formatNutritionValue(liveDisplayDominant.key, liveDisplayDominant.value)}</div>
                  </div>
                ) : (
                  <div className="mt-3 text-xs leading-5 text-white/45">识别后显示当前最突出的营养指标。</div>
                )}
              </div>

              <div className="rounded-xl border border-white/10 bg-white/[0.06] p-4">
                <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-white/40">Suggestion</div>
                <p className="mt-3 text-sm leading-6 text-white/[0.72]">{liveDisplaySuggestion}</p>
              </div>
            </div>

            <div className="mt-4 grid gap-2">
              {!streaming ? (
                <button
                  onClick={() => {
                    setMode('stream')
                    void startStreamPreview()
                  }}
                  disabled={!streamUrl.trim()}
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-white px-4 text-sm font-medium text-slate-950 transition hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  <Play className="h-4 w-4" />
                  连接实时视频
                </button>
              ) : (
                <button
                  onClick={stopStreamPreview}
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-white/[0.12] bg-white/[0.08] px-4 text-sm font-medium text-white/80 transition hover:bg-white/[0.12]"
                >
                  <Square className="h-4 w-4" />
                  停止预览
                </button>
              )}
            </div>
          </aside>
        </div>
      </div>
    </section>
  )

  const inputRoutingPanel = (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Input</div>
          <h2 className="mt-1.5 text-base font-semibold text-foreground">输入源</h2>
        </div>
        <div className="rounded-full border border-border bg-background px-3 py-1 text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">
          {modeBadgeLabel}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {[
          { id: 'upload', label: '上传', icon: ImageIcon },
          { id: 'browser', label: '本机', icon: Monitor },
          { id: 'camera', label: '抓拍', icon: Camera },
          { id: 'stream', label: '实时流', icon: Video },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => {
              if (id !== 'stream') stopStreamPreview()
              if (id !== 'browser') stopBrowserPreview()
              setMode(id as DemoMode)
              if (id === 'browser') {
                void refreshBrowserPermissionState()
                if (!browserPreviewing && !browserConnecting) {
                  void startBrowserPreview()
                }
              }
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
            className="group flex min-h-[120px] w-full flex-col items-center justify-center rounded-xl border border-dashed border-border bg-card px-4 text-center transition-colors hover:border-primary/30 hover:bg-secondary/60"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full border border-border bg-secondary text-foreground transition-transform duration-200 group-hover:scale-105">
              <Upload className="h-5 w-5" />
            </div>
            <div className="mt-3 text-sm font-medium text-foreground">点击上传餐盘图片</div>
            <div className="mt-1 text-xs text-muted-foreground">JPG / PNG</div>
          </button>
        )}

        {mode === 'browser' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-medium text-foreground">本机摄像头</div>
              <div className="rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-muted-foreground">
                {browserStatusText}
              </div>
            </div>

            <label className="block">
              <div className="mb-1 text-[11px] font-medium text-muted-foreground">设备</div>
              <select
                value={browserDeviceId}
                onChange={(event) => {
                  const nextDeviceId = event.target.value
                  setBrowserDeviceId(nextDeviceId)
                  if (browserPreviewing) {
                    void startBrowserPreview(nextDeviceId)
                  }
                }}
                disabled={!browserCameraSupported || browserConnecting}
                className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {browserDevices.length > 0 ? (
                  browserDevices.map((device, index) => (
                    <option key={device.deviceId || `${index}`} value={device.deviceId}>
                      {device.label || `摄像头 ${index + 1}`}
                    </option>
                  ))
                ) : (
                  <option value="">系统默认摄像头</option>
                )}
              </select>
            </label>

            {browserError && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                {browserError}
              </div>
            )}

            {browserPermissionState === 'denied' && (
              <div className="rounded-xl border border-border bg-card px-3 py-2 text-[11px] leading-5 text-muted-foreground">
                摄像头权限被拒绝，请到浏览器站点设置里改成允许。
              </div>
            )}

            <div className="grid gap-2">
              {!browserPreviewing ? (
                <button
                  onClick={() => void startBrowserPreview()}
                  disabled={!browserCameraSupported || browserConnecting}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {browserConnecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                  {browserActionLabel}
                </button>
              ) : (
                <>
                  <button
                    onClick={captureFrameFromBrowser}
                    disabled={anyAnalysisRunning || browserConnecting}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {anyAnalysisRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                    截图并分析
                  </button>
                  <button
                    onClick={stopBrowserPreview}
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary"
                  >
                    <Square className="h-4 w-4" />
                    停止预览
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {mode === 'camera' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium text-foreground">摄像头抓拍</div>
              <button
                onClick={() => setShowSettings((value) => !value)}
                className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
              >
                <Settings className="h-3 w-3" />
                高级参数
              </button>
            </div>

            {cameraSourceLabel && (
              <div className="rounded-xl border border-border bg-card px-3 py-2 text-[11px] leading-5 text-muted-foreground">
                视频源：{cameraSourceLabel}
              </div>
            )}

            <div className="grid gap-2">
              <label className="block">
                <div className="mb-1 text-[11px] font-medium text-muted-foreground">通道</div>
                <select
                  value={channelId}
                  onChange={(event) => setChannelId(event.target.value)}
                  className="w-full rounded-xl border border-border bg-card px-3 py-2.5 text-sm outline-none transition focus:border-primary/40"
                >
                  {cameraOptions.length > 0 ? (
                    cameraOptions.map((camera) => (
                      <option key={camera.channel_id} value={camera.channel_id}>
                        {camera.name || `通道 ${camera.channel_id}`}
                      </option>
                    ))
                  ) : (
                    <option value={channelId || '1'}>{channelId || '1'}</option>
                  )}
                </select>
              </label>
              <label className="block">
                <div className="mb-1 text-[11px] font-medium text-muted-foreground">临时 IP 地址</div>
                <input
                  type="text"
                  value={cameraHost}
                  onChange={(event) => setCameraHost(event.target.value)}
                  placeholder={cameraSourceSupportsSnapshot ? '留空则使用当前激活视频源' : '192.168.1.100'}
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
              disabled={capturing || (!cameraHost && !cameraSourceSupportsSnapshot)}
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
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-medium text-foreground">实时流</div>
              <div className="rounded-full border border-border bg-card px-2.5 py-1 text-[11px] text-muted-foreground">
                {streamStatusText}
              </div>
            </div>

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

            {streamError && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                {streamError}
              </div>
            )}

            <div className="grid gap-2">
              {!streaming ? (
                <button
                  onClick={startStreamPreview}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90"
                >
                  <Play className="h-4 w-4" />
                  建立预览
                </button>
              ) : (
                <>
                  <button
                    onClick={captureFrameFromStream}
                    disabled={anyAnalysisRunning}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {anyAnalysisRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                    截图并分析
                  </button>
                  <button
                    onClick={stopStreamPreview}
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary"
                  >
                    <Square className="h-4 w-4" />
                    停止预览
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )

  const previewPanel = (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Preview</div>
          <h2 className="mt-1.5 text-base font-semibold text-foreground">预览</h2>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {autoAnalyzeSupported && (
            <div
              className={cn(
                'inline-flex select-none items-center gap-3 rounded-full border px-3 py-1.5 text-xs transition-colors',
                autoAnalyzeEnabled
                  ? 'border-primary/25 bg-primary/5 text-foreground'
                  : 'border-border bg-background text-foreground hover:border-primary/30',
              )}
            >
              <span className="whitespace-nowrap text-[11px] font-medium text-muted-foreground">自动分析</span>
              <Switch.Root
                checked={autoAnalyzeEnabled}
                onCheckedChange={setAutoAnalyzeEnabled}
                aria-label="自动分析"
                className={cn(
                  'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors outline-none',
                  'focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                  autoAnalyzeEnabled
                    ? 'border-primary/40 bg-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.22)]'
                    : 'border-slate-300 bg-slate-200/90',
                )}
              >
                <Switch.Thumb
                  className={cn(
                    'block h-5 w-5 rounded-full bg-white shadow-[0_2px_6px_rgba(15,23,42,0.22)] transition-transform',
                    autoAnalyzeEnabled ? 'translate-x-5' : 'translate-x-0.5',
                  )}
                />
              </Switch.Root>
              <span
                className={cn(
                  'min-w-[2.5rem] whitespace-nowrap text-right text-[11px] font-medium',
                  autoAnalyzeEnabled ? 'text-primary' : 'text-muted-foreground',
                )}
              >
                {autoAnalyzeEnabled ? '开启' : '关闭'}
              </span>
            </div>
          )}
          <div className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs', status.badgeClass)}>
            <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
            {status.label}
          </div>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <div className="relative overflow-hidden rounded-xl border border-border bg-[#0f172a]" style={{ aspectRatio: previewAspectRatio }}>
          <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.04)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.04)_1px,transparent_1px)] bg-[size:24px_24px]" />
          <div className="absolute left-3 top-3 z-10 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/35 px-3 py-1 text-[11px] text-white/80 backdrop-blur">
            <span className={cn('h-2 w-2 rounded-full', livePreviewing ? 'bg-emerald-400' : capturedImage ? 'bg-sky-400' : 'bg-white/40')} />
            {livePreviewTitle}
          </div>
          {autoAnalyzeSupported && (
            <div className="absolute right-3 top-3 z-10 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/35 px-3 py-1 text-[11px] text-white/80 backdrop-blur">
              {autoAnalyzing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              {autoAnalyzeStatusText}
            </div>
          )}

          {mode === 'stream' || mode === 'browser' ? (
            <div className="relative h-full w-full">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                onLoadedMetadata={syncLivePreviewSize}
                onCanPlay={syncLivePreviewSize}
                className="h-full w-full object-contain"
              />
              {!livePreviewing && (
                <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60">
                  <div className="text-center text-white">
                    {mode === 'browser' ? (
                      <Monitor className="mx-auto h-10 w-10 opacity-50" />
                    ) : (
                      <VideoOff className="mx-auto h-10 w-10 opacity-50" />
                    )}
                    <p className="mt-3 text-sm text-white/70">
                      {mode === 'browser' ? '授权后在这里显示本机摄像头画面' : '连接后在这里显示实时画面'}
                    </p>
                  </div>
                </div>
              )}
            </div>
          ) : capturedImage ? (
            <div className="h-full w-full">
              <img
                src={capturedImage}
                alt="Captured preview"
                className="h-full w-full object-contain"
                onLoad={(event) => {
                  setCapturedImageSize({
                    width: event.currentTarget.naturalWidth || 0,
                    height: event.currentTarget.naturalHeight || 0,
                  })
                }}
              />
            </div>
          ) : (
            <div className="flex h-full items-center justify-center bg-slate-950/40 px-6">
              <div className="text-center text-white">
                <Camera className="mx-auto h-10 w-10 opacity-50" />
                <p className="mt-3 text-sm text-white/75">当前还没有样本画面</p>
              </div>
            </div>
          )}

          {previewOverlayBoxes.length > 0 && (
            <div className="pointer-events-none absolute inset-0">
              {previewOverlayBoxes.map((box) => (
                <div
                  key={box.key}
                  className={cn(
                    'absolute rounded-xl border-2 shadow-[0_0_0_1px_rgba(255,255,255,0.12)]',
                    box.tone === 'matched' && 'border-emerald-400/90 bg-emerald-500/12',
                    box.tone === 'recognized' && 'border-sky-400/90 bg-sky-500/12',
                    box.tone === 'region' && 'border-amber-300/90 bg-amber-400/12',
                  )}
                  style={{
                    left: `${box.left}%`,
                    top: `${box.top}%`,
                    width: `${box.width}%`,
                    height: `${box.height}%`,
                  }}
                >
                  <div className={cn(
                    'absolute left-1.5 top-1.5 rounded-md px-2 py-1 text-[10px] font-medium leading-none text-white shadow-sm',
                    box.tone === 'matched' && 'bg-emerald-600',
                    box.tone === 'recognized' && 'bg-sky-600',
                    box.tone === 'region' && 'bg-amber-500 text-black',
                  )}>
                    {box.label}
                    {typeof box.confidence === 'number' ? ` ${(box.confidence * 100).toFixed(0)}%` : ''}
                  </div>
                </div>
              ))}
            </div>
          )}

          {manualAnalyzing && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-950/55 backdrop-blur-sm">
              <div className="text-center text-white">
                <Loader2 className="mx-auto h-8 w-8 animate-spin" />
                <p className="mt-3 text-sm font-medium">Agent 正在解析截图</p>
              </div>
            </div>
          )}
        </div>

        {autoAnalyzeSupported && (
          <div className="rounded-xl border border-border bg-background px-3 py-2.5 text-xs leading-5 text-muted-foreground">
            打开自动分析后，系统会每秒抓取一帧，先判断当前画面是否出现菜品；一旦识别到菜区，就会在预览画面上刷新对应框和菜名。
          </div>
        )}

        {autoAnalyzeError && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            自动分析暂时失败：{autoAnalyzeError}
          </div>
        )}

        {capturedImage && (
          <div className="overflow-hidden rounded-xl border border-border bg-background">
            <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
              <div className="text-sm font-medium text-foreground">最新截图</div>
              <button
                onClick={clearAll}
                className="rounded-full border border-border p-1.5 text-muted-foreground transition-colors hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <img
              src={capturedImage}
              alt="Snapshot"
              className="block max-h-[260px] w-full object-contain bg-slate-950/80"
              onLoad={(event) => {
                setCapturedImageSize({
                  width: event.currentTarget.naturalWidth || 0,
                  height: event.currentTarget.naturalHeight || 0,
                })
              }}
            />
          </div>
        )}

        <div className="grid gap-2">
          {capturedImage && (
            <button
              onClick={reanalyze}
              disabled={anyAnalysisRunning}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw className={cn('h-4 w-4', anyAnalysisRunning && 'animate-spin')} />
              重新分析
            </button>
          )}
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
  )

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

        <Tabs.Root
          value={activeDemoTab}
          onValueChange={(value) => {
            const nextTab = value as DemoTab
            setActiveDemoTab(nextTab)
            if (nextTab === 'live-display') {
              stopBrowserPreview()
              setMode('stream')
              if (
                hasSavedLiveDisplayConfig
                && streamUrl.trim() === savedLiveDisplayStreamUrl
                && liveDisplayAutoConnect
                && !streaming
              ) {
                void startStreamPreview()
              }
            }
          }}
          className="space-y-5"
        >
          <div className="flex flex-col gap-3 rounded-xl border border-border bg-card px-3 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-4">
            <Tabs.List className="inline-flex w-full rounded-lg border border-border bg-background p-1 sm:w-auto">
              <Tabs.Trigger
                value="workspace"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-muted-foreground transition data-[state=active]:bg-primary data-[state=active]:text-primary-foreground sm:flex-none"
              >
                <Brain className="h-4 w-4" />
                智能工作台
              </Tabs.Trigger>
              <Tabs.Trigger
                value="live-display"
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium text-muted-foreground transition data-[state=active]:bg-primary data-[state=active]:text-primary-foreground sm:flex-none"
              >
                <Video className="h-4 w-4" />
                实时大屏
              </Tabs.Trigger>
            </Tabs.List>

            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="rounded-full border border-border bg-background px-3 py-1">
                {streaming ? '实时流在线' : streamUrl.trim() ? '实时流已配置' : '实时流未配置'}
              </span>
              <span className="rounded-full border border-border bg-background px-3 py-1">
                {autoAnalyzeEnabled ? '自动分析开启' : '自动分析关闭'}
              </span>
            </div>
          </div>

          {activeDemoTab === 'workspace' && (
            <Tabs.Content value="workspace" className="space-y-5 outline-none">
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
                <span className="ml-2 font-medium">{result?.analyzed_at ? fmtDateTime(result.analyzed_at) : '上传后会显示时间'}</span>
              </div>
            </div>
          </div>
        </section>

        <div className="grid items-start gap-4 xl:grid-cols-[340px_minmax(0,1.15fr)] 2xl:grid-cols-[360px_minmax(0,1.25fr)]">
          <section className="flex flex-col gap-4 xl:sticky xl:top-6">
            <div className={cn(prioritizeLivePreview ? 'order-2' : 'order-1')}>
              {inputRoutingPanel}
            </div>
            <div className={cn(prioritizeLivePreview ? 'order-1' : 'order-2')}>
              {previewPanel}
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
                  {result?.analyzed_at ? `最近分析 ${fmtDateTime(result.analyzed_at)}` : '发来一张图片后，这里会开始给出判断'}
                </div>
              </div>

              <div
                ref={chatViewportRef}
                className="mt-5 flex-1 space-y-3 overflow-y-auto rounded-xl bg-background/75 p-2"
              >
                {chatMessages.map((message) => {
                  const reportData = message.variant === 'report' ? message.reportData : undefined
                  const isLatestAssistantMessage = message.id === latestAssistantMessageId
                  const followUpQuestions = isLatestAssistantMessage
                    ? normalizeFollowUpQuestions(message.followUpQuestions).length > 0
                      ? normalizeFollowUpQuestions(message.followUpQuestions)
                      : buildFollowUpQuestions(result)
                    : []

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
                      <div className="space-y-3">
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
                        {message.role === 'assistant' && followUpQuestions.length > 0 && (
                          <div className="flex flex-wrap gap-2">
                            {followUpQuestions.map((question) => (
                              <button
                                key={`${message.id}-${question}`}
                                onClick={() => { void submitChat(question) }}
                                disabled={chatBusy}
                                className="rounded-full border border-primary/15 bg-background/80 px-3 py-1.5 text-xs text-muted-foreground transition hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                {question}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })}

                {chatBusy && (
                  <div className="max-w-[92%] rounded-xl border border-primary/10 bg-card px-4 py-3 text-sm text-foreground">
                    <div className="mb-1 flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">
                      <MessageSquare className="h-3 w-3" />
                      {manualAnalyzing ? '正在查看这张餐盘' : '正在整理回复'}
                    </div>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {manualAnalyzing ? '这张餐盘的营养重点正在整理中' : '我组织一下表达，尽快把结论说清楚'}
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
            </Tabs.Content>
          )}

          {activeDemoTab === 'live-display' && (
            <Tabs.Content value="live-display" className="outline-none">
              {liveDisplayPanel}
            </Tabs.Content>
          )}
        </Tabs.Root>
      </div>
    </div>
  )
}
