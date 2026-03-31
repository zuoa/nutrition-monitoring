import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

const LOCAL_RECOGNITION_MODES = new Set(['local_embedding', 'yolo_embedding_local'])

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
