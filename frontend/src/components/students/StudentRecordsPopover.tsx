import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import * as Tabs from '@radix-ui/react-tabs'
import {
  AlertCircle,
  ArrowRight,
  GitMerge,
  MapPin,
  ReceiptText,
  RefreshCw,
  X,
} from 'lucide-react'
import { consumptionApi } from '@/api/client'
import { cn, fmtDateTime } from '@/lib/utils'
import type { ConsumptionRecord, MatchResult, MatchStatus } from '@/types'

type StudentRecordIdentity = {
  id: number
  student_no: string
  name: string
  card_no: string | null
}

type PopoverPosition = {
  left: number
  top: number
  width: number
}

const RECORD_LIMIT = 10
const POPOVER_WIDTH = 640
const VIEWPORT_GUTTER = 12

const MATCH_STATUS: Record<MatchStatus, { label: string; className: string; dotClassName: string }> = {
  matched: {
    label: '已匹配',
    className: 'bg-health-green/10 text-health-green',
    dotClassName: 'bg-health-green',
  },
  time_matched_only: {
    label: '待确认',
    className: 'bg-health-amber/10 text-health-amber',
    dotClassName: 'bg-health-amber',
  },
  unmatched_image: {
    label: '无消费记录',
    className: 'bg-secondary text-muted-foreground',
    dotClassName: 'bg-muted-foreground',
  },
  unmatched_record: {
    label: '无图片',
    className: 'bg-secondary text-muted-foreground',
    dotClassName: 'bg-muted-foreground',
  },
  confirmed: {
    label: '已确认',
    className: 'bg-health-blue/10 text-health-blue',
    dotClassName: 'bg-health-blue',
  },
}

function formatAmount(amount?: number, preserveSign = false) {
  if (amount == null || !Number.isFinite(amount)) return '—'
  const prefix = preserveSign ? (amount < 0 ? '-' : amount > 0 ? '+' : '') : ''
  return `${prefix}¥${Math.abs(amount).toFixed(2)}`
}

function sourceLabel(source?: string) {
  if (source === 'ztk_plus') return '一卡通同步'
  if (source) return source
  return '手工导入'
}

function recordLocation(record?: ConsumptionRecord) {
  return String(record?.channel_id || '').trim() || '未记录地点'
}

export function StudentRecordsPopover({ student }: { student: StudentRecordIdentity }) {
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const panelRef = useRef<HTMLDivElement | null>(null)
  const requestIdRef = useRef(0)
  const popoverId = useId()
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<PopoverPosition | null>(null)
  const [matches, setMatches] = useState<MatchResult[]>([])
  const [records, setRecords] = useState<ConsumptionRecord[]>([])
  const [matchesTotal, setMatchesTotal] = useState(0)
  const [recordsTotal, setRecordsTotal] = useState(0)
  const [loadedKey, setLoadedKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const studentKey = student.student_no

  const loadRecords = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    try {
      const [matchesResponse, recordsResponse] = await Promise.all([
        consumptionApi.matches({
          student_no: student.student_no,
          page: 1,
          page_size: RECORD_LIMIT,
        }),
        consumptionApi.records({
          student_no: student.student_no,
          page: 1,
          page_size: RECORD_LIMIT,
        }),
      ])
      if (requestId !== requestIdRef.current) return

      const matchData = matchesResponse.data?.data || {}
      const recordData = recordsResponse.data?.data || {}
      setMatches(Array.isArray(matchData.items) ? matchData.items : [])
      setRecords(Array.isArray(recordData.items) ? recordData.items : [])
      setMatchesTotal(Number(matchData.total || 0))
      setRecordsTotal(Number(recordData.total || 0))
      setLoadedKey(studentKey)
    } catch {
      if (requestId === requestIdRef.current) {
        setError('记录加载失败，请重试')
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [student.student_no, studentKey])

  useEffect(() => () => {
    requestIdRef.current += 1
  }, [])

  useEffect(() => {
    if (!open) return

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setOpen(false)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      triggerRef.current?.focus()
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [open])

  useLayoutEffect(() => {
    if (!open) return

    const updatePosition = () => {
      const trigger = triggerRef.current
      if (!trigger) return

      const triggerRect = trigger.getBoundingClientRect()
      const width = Math.min(POPOVER_WIDTH, window.innerWidth - VIEWPORT_GUTTER * 2)
      const panelHeight = panelRef.current?.offsetHeight || Math.min(540, window.innerHeight - VIEWPORT_GUTTER * 2)
      const fitsBelow = triggerRect.bottom + 8 + panelHeight <= window.innerHeight - VIEWPORT_GUTTER
      const top = fitsBelow
        ? triggerRect.bottom + 8
        : Math.max(VIEWPORT_GUTTER, triggerRect.top - panelHeight - 8)
      const left = Math.min(
        window.innerWidth - width - VIEWPORT_GUTTER,
        Math.max(VIEWPORT_GUTTER, triggerRect.right - width),
      )
      const nextPosition = { left, top, width }
      setPosition(current => (
        current
        && Math.abs(current.left - nextPosition.left) < 1
        && Math.abs(current.top - nextPosition.top) < 1
        && Math.abs(current.width - nextPosition.width) < 1
          ? current
          : nextPosition
      ))
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    const resizeObserver = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(updatePosition)
    if (panelRef.current) resizeObserver?.observe(panelRef.current)

    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
      resizeObserver?.disconnect()
    }
  }, [open])

  const togglePopover = () => {
    const nextOpen = !open
    setOpen(nextOpen)
    if (nextOpen && loadedKey !== studentKey) {
      void loadRecords()
    }
  }

  const popover = open && typeof document !== 'undefined'
    ? createPortal(
      <div
        ref={panelRef}
        id={popoverId}
        role="dialog"
        aria-label={`${student.name}的消费记录`}
        className="fixed z-[70] max-h-[calc(100vh-24px)] overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl shadow-black/15"
        style={{
          left: position?.left ?? VIEWPORT_GUTTER,
          top: position?.top ?? VIEWPORT_GUTTER,
          width: position?.width ?? Math.min(POPOVER_WIDTH, 640),
          visibility: position ? 'visible' : 'hidden',
        }}
      >
        <div className="flex items-start justify-between gap-3 border-b border-border bg-primary/[0.04] px-4 py-3.5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary/10 text-sm font-semibold text-primary">
                {student.name.slice(0, 1)}
              </span>
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold">{student.name}的消费记录</h3>
                <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <span className="font-mono">学号 {student.student_no}</span>
                  <ArrowRight className="h-3 w-3 flex-shrink-0" aria-hidden="true" />
                  <span className={cn('font-mono', !student.card_no && 'text-health-amber')}>
                    {student.card_no ? `消费卡 ${student.card_no}` : '未绑定消费卡'}
                  </span>
                </div>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => void loadRecords()}
              disabled={loading}
              aria-label="刷新记录"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-50"
            >
              <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
            </button>
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                triggerRef.current?.focus()
              }}
              aria-label="关闭记录"
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <Tabs.Root defaultValue="matches">
          <Tabs.List className="grid grid-cols-2 border-b border-border px-4" aria-label="消费记录类型">
            <RecordTab value="matches" icon={GitMerge} label="匹配记录" count={matchesTotal} />
            <RecordTab value="raw" icon={ReceiptText} label="原始消费记录" count={recordsTotal} />
          </Tabs.List>

          <Tabs.Content value="matches" className="outline-none">
            <RecordPanel
              loading={loading}
              error={error}
              empty={matches.length === 0}
              emptyText="暂无匹配记录"
              onRetry={loadRecords}
              footer={<RecordFooter shown={matches.length} total={matchesTotal} />}
            >
              {matches.map(match => <MatchRecordRow key={match.id} match={match} />)}
            </RecordPanel>
          </Tabs.Content>

          <Tabs.Content value="raw" className="outline-none">
            <RecordPanel
              loading={loading}
              error={error}
              empty={records.length === 0}
              emptyText="暂无原始消费记录"
              onRetry={loadRecords}
              footer={<RecordFooter shown={records.length} total={recordsTotal} />}
            >
              {records.map(record => <RawRecordRow key={record.id} record={record} />)}
            </RecordPanel>
          </Tabs.Content>
        </Tabs.Root>
      </div>,
      document.body,
    )
    : null

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={togglePopover}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        aria-label={`查看${student.name}的消费记录`}
        className={cn(
          'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
          open ? 'bg-primary/10 text-primary' : 'text-primary hover:bg-primary/10',
        )}
      >
        <ReceiptText className="h-3.5 w-3.5" />查看
      </button>
      {popover}
    </>
  )
}

function RecordTab({ value, icon: Icon, label, count }: {
  value: string
  icon: typeof GitMerge
  label: string
  count: number
}) {
  return (
    <Tabs.Trigger
      value={value}
      className="group relative flex items-center justify-center gap-1.5 px-3 py-3 text-xs font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring data-[state=active]:text-primary"
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{label}</span>
      <span className="rounded-full bg-secondary px-1.5 font-mono text-[10px] text-muted-foreground group-data-[state=active]:bg-primary/10 group-data-[state=active]:text-primary">
        {count}
      </span>
      <span className="absolute inset-x-5 bottom-0 h-0.5 rounded-full bg-primary opacity-0 transition-opacity group-data-[state=active]:opacity-100" />
    </Tabs.Trigger>
  )
}

function RecordPanel({ loading, error, empty, emptyText, onRetry, footer, children }: {
  loading: boolean
  error: string
  empty: boolean
  emptyText: string
  onRetry: () => Promise<void>
  footer: React.ReactNode
  children: React.ReactNode
}) {
  if (loading) {
    return (
      <div className="h-[clamp(240px,42vh,360px)] space-y-3 overflow-hidden p-4" aria-live="polite" aria-busy="true">
        {[0, 1, 2, 3].map(item => (
          <div key={item} className="animate-pulse rounded-lg border border-border/70 p-3">
            <div className="flex items-center justify-between gap-4">
              <div className="h-3 w-36 rounded bg-secondary" />
              <div className="h-3 w-16 rounded bg-secondary" />
            </div>
            <div className="mt-3 h-2.5 w-52 rounded bg-secondary/80" />
          </div>
        ))}
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-[clamp(240px,42vh,360px)] flex-col items-center justify-center px-6 text-center" role="alert">
        <AlertCircle className="mb-2 h-6 w-6 text-health-red" />
        <p className="text-sm font-medium">{error}</p>
        <button type="button" onClick={() => void onRetry()} className="mt-3 rounded-md bg-secondary px-3 py-1.5 text-xs transition-colors hover:bg-secondary/70">
          重新加载
        </button>
      </div>
    )
  }

  if (empty) {
    return (
      <div className="flex h-[clamp(240px,42vh,360px)] flex-col items-center justify-center px-6 text-center">
        <ReceiptText className="mb-2 h-6 w-6 text-muted-foreground/60" />
        <p className="text-sm text-muted-foreground">{emptyText}</p>
        <p className="mt-1 text-xs text-muted-foreground/70">已按学号精确查询</p>
      </div>
    )
  }

  return (
    <>
      <div className="h-[clamp(240px,42vh,360px)] divide-y divide-border/60 overflow-y-auto px-4">
        {children}
      </div>
      {footer}
    </>
  )
}

function MatchRecordRow({ match }: { match: MatchResult }) {
  const record = match.consumption_record
  const status = MATCH_STATUS[match.status]
  const dishes = (match.image?.recognitions || [])
    .map(item => item.dish_name_raw)
    .filter(Boolean)
    .slice(0, 3)

  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium', status.className)}>
              <span className={cn('h-1.5 w-1.5 rounded-full', status.dotClassName)} />
              {status.label}
            </span>
            <span className="font-mono text-xs text-foreground">{fmtDateTime(record?.transaction_time)}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{recordLocation(record)}</span>
            {match.time_diff_seconds != null && <span>时间偏差 {match.time_diff_seconds.toFixed(1)}s</span>}
            {match.price_diff != null && <span>金额偏差 ¥{match.price_diff.toFixed(2)}</span>}
            <span>{match.is_manual ? '手动匹配' : '自动匹配'}</span>
          </div>
          {dishes.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {dishes.map((dish, index) => (
                <span key={`${dish}-${index}`} className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground">{dish}</span>
              ))}
            </div>
          )}
        </div>
        <span className="flex-shrink-0 font-mono text-sm font-medium">{formatAmount(record?.amount)}</span>
      </div>
    </div>
  )
}

function RawRecordRow({ record }: { record: ConsumptionRecord }) {
  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-foreground">{fmtDateTime(record.transaction_time)}</span>
            <span className="rounded-full bg-secondary px-2 py-0.5 text-[10px] text-muted-foreground">{sourceLabel(record.source_system)}</span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1"><MapPin className="h-3 w-3" />{recordLocation(record)}</span>
            <span className="max-w-[300px] truncate font-mono" title={record.transaction_id}>流水号 {record.transaction_id}</span>
          </div>
        </div>
        <span className={cn(
          'flex-shrink-0 font-mono text-sm font-medium',
          record.amount < 0 ? 'text-foreground' : 'text-health-green',
        )}>
          {formatAmount(record.amount, true)}
        </span>
      </div>
    </div>
  )
}

function RecordFooter({ shown, total }: { shown: number; total: number }) {
  return (
    <div className="border-t border-border bg-secondary/30 px-4 py-2 text-[11px] text-muted-foreground">
      显示最近 {shown} 条{total > shown ? `，共 ${total} 条` : ''}
    </div>
  )
}
