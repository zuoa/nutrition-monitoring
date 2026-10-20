import { reportApi } from '@/api/client'
import type { Report } from '@/types'

export type LatestReportQuery = {
  scope: 'personal' | 'class' | 'grade' | 'campus'
  targetId: number
  type: string
  period?: string
}

type CacheEntry = { data?: Report | null; expiresAt: number; request: Promise<Report | null> }
const CACHE_TTL_MS = 60_000
const MAX_ENTRIES = 20
const cache = new Map<string, CacheEntry>()
let sessionToken: string | null = null

export function clearReportCache() {
  cache.clear()
}

function cacheKey(query: LatestReportQuery) {
  const token = localStorage.getItem('auth_token')
  if (token !== sessionToken) {
    clearReportCache()
    sessionToken = token
  }
  return JSON.stringify([query.scope, query.targetId, query.type, query.period || 'latest'])
}

function cachedEntry(key: string) {
  const entry = cache.get(key)
  if (entry && entry.expiresAt <= Date.now()) {
    cache.delete(key)
    return undefined
  }
  return entry
}

export function peekLatestReport(query: LatestReportQuery): Report | null | undefined {
  return cachedEntry(cacheKey(query))?.data
}

export function loadLatestReport(query: LatestReportQuery): Promise<Report | null> {
  const key = cacheKey(query)
  const cached = cachedEntry(key)
  if (cached) {
    cache.delete(key)
    cache.set(key, cached)
    return cached.request
  }
  const params = { type: query.type, period: query.period || undefined }
  const request = query.scope === 'personal'
    ? reportApi.studentLatest(query.targetId, params)
    : reportApi.groupLatest(query.scope, query.targetId, params)
  const entry: CacheEntry = {
    expiresAt: Infinity,
    request: request.then(response => {
      const data: Report | null = response.data.data ?? null
      entry.data = data
      entry.expiresAt = Date.now() + CACHE_TTL_MS
      return data
    }).catch(error => {
      if (cache.get(key) === entry) cache.delete(key)
      throw error
    }),
  }
  cache.set(key, entry)
  if (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value!)
  return entry.request
}
