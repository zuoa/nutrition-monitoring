import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

const LOCAL_RECOGNITION_MODES = new Set(['local_embedding', 'yolo_embedding_local'])
export const STRUCTURED_DESCRIPTION_SECTION = '【识别特征】'

export type StructuredDescriptionKey =
  | 'mainIngredients'
  | 'colors'
  | 'cuts'
  | 'texture'
  | 'sauce'
  | 'garnishes'
  | 'confusableWith'

export const STRUCTURED_DESCRIPTION_FIELDS: Array<{
  key: StructuredDescriptionKey
  label: string
  placeholder: string
}> = [
  { key: 'mainIngredients', label: '主食材', placeholder: '排骨、土豆、青椒' },
  { key: 'colors', label: '颜色', placeholder: '红褐色为主，夹少量绿色' },
  { key: 'cuts', label: '切法/形态', placeholder: '块状、片状、丝状、叶片状' },
  { key: 'texture', label: '质地', placeholder: '表面油亮、外焦里嫩、软烂' },
  { key: 'sauce', label: '汁感', placeholder: '带浓汁、干炒、清汤、少芡' },
  { key: 'garnishes', label: '常见配菜', placeholder: '胡萝卜、木耳、葱花' },
  { key: 'confusableWith', label: '易混淆菜', placeholder: '宫保鸡丁、土豆烧鸡' },
]

export const emptyStructuredDescription = (): Record<StructuredDescriptionKey, string> => ({
  mainIngredients: '',
  colors: '',
  cuts: '',
  texture: '',
  sauce: '',
  garnishes: '',
  confusableWith: '',
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

function parseDate(value?: string | null): Date | null {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function fmtDate(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date)
}

export function fmtDateTime(value?: string | null): string {
  const date = parseDate(value)
  if (!date) return '—'

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date)
}

export function scoreColor(score: number): string {
  if (score >= 90) return 'text-health-green'
  if (score >= 75) return 'text-health-blue'
  if (score >= 60) return 'text-health-amber'
  return 'text-health-red'
}

export function isLocalRecognitionMode(mode?: string | null): boolean {
  return LOCAL_RECOGNITION_MODES.has(String(mode || '').trim())
}

export function buildStructuredDescription(
  summary: string,
  details: Record<StructuredDescriptionKey, string>,
): string {
  const sections: string[] = []
  const trimmedSummary = summary.trim()
  if (trimmedSummary) sections.push(trimmedSummary)

  const detailLines = STRUCTURED_DESCRIPTION_FIELDS
    .map(field => {
      const value = String(details[field.key] || '').trim()
      return value ? `${field.label}：${value}` : ''
    })
    .filter(Boolean)

  if (detailLines.length > 0) {
    sections.push([STRUCTURED_DESCRIPTION_SECTION, ...detailLines].join('\n'))
  }

  return sections.join('\n\n').trim()
}
