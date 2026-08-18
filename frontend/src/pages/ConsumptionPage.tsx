import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useDropzone } from 'react-dropzone'
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Download,
  Eye,
  FileText,
  Filter,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { consumptionApi } from '@/api/client'
import { DataPagination } from '@/components/ui/DataPagination'
import { useUrlPage } from '@/hooks/useUrlPage'
import { cn, fmtDateTime } from '@/lib/utils'
import type { ConsumptionRecord, ConsumptionRecordDetail } from '@/types'
import toast from 'react-hot-toast'

const PAGE_SIZE = 10
const REQUIRED_FIELDS = ['student_id', 'transaction_time', 'amount', 'transaction_id', 'channel_id']
const FIELD_LABELS: Record<string, string> = {
  student_id: '学号/消费卡号',
  student_name: '学生姓名',
  transaction_time: '消费时间',
  amount: '消费金额',
  transaction_id: '流水号',
  channel_id: '通道',
}

const FILTER_INPUT_CLASS = 'mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-60'

interface PreviewData {
  columns: string[]
  preview_rows: Record<string, string>[]
  suggested_mapping: Record<string, string>
  total_rows: number
}

interface ImportResult {
  batch_id: string
  imported: number
  skipped_duplicates: number
  skipped_by_location: number
  errors: { row: number; error: string }[]
  total_rows: number
}

interface ImportSettings {
  allowed_locations: string[]
}

interface BatchSummary {
  batch_id: string
  record_count: number
  first_transaction_time?: string | null
  last_transaction_time?: string | null
  created_at?: string | null
  total_amount: number
}

interface RecordFilters {
  batch: string
  date_from: string
  date_to: string
  student: string
  channel_id: string
  transaction_id: string
}

const EMPTY_RECORD_FILTERS: RecordFilters = {
  batch: '',
  date_from: '',
  date_to: '',
  student: '',
  channel_id: '',
  transaction_id: '',
}

const parseAllowedLocationsInput = (value: string) =>
  Array.from(new Set(
    value
      .split(/\r?\n|[，,；;]/)
      .map(item => item.trim())
      .filter(Boolean),
  ))

const normalizeRecordFilters = (filters: RecordFilters): RecordFilters => ({
  batch: filters.batch.trim(),
  date_from: filters.date_from,
  date_to: filters.date_to,
  student: filters.student.trim(),
  channel_id: filters.channel_id.trim(),
  transaction_id: filters.transaction_id.trim(),
})

const buildRecordParams = (filters: RecordFilters) => {
  const params: Record<string, string> = {}
  if (filters.batch) params.batch = filters.batch
  if (filters.date_from) params.date_from = filters.date_from
  if (filters.date_to) params.date_to = filters.date_to
  if (filters.student) params.student = filters.student
  if (filters.channel_id) params.channel_id = filters.channel_id
  if (filters.transaction_id) params.transaction_id = filters.transaction_id
  return params
}

const CALIBRATION_METHOD_LABELS = {
  same_minute: '同分钟采样',
  nearest: '最近采样',
  manual_fallback: '人工配置值',
}

const formatSignedSeconds = (value: number) => {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '±'
  return `${sign}${Math.abs(value).toFixed(3)} 秒`
}

const formatSampleDistance = (value?: number | null) => {
  if (value === null || value === undefined) return '—'
  return value < 1 ? `${value.toFixed(3)} 秒` : `${value.toFixed(1)} 秒`
}

interface RecordDetailDialogProps {
  record: ConsumptionRecord
  detail: ConsumptionRecordDetail | null
  loading: boolean
  error: string
  onClose: () => void
  onRetry: () => void
}

function RecordDetailDialog({ record, detail, loading, error, onClose, onRetry }: RecordDetailDialogProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const displayedRecord = detail?.record || record
  const calibration = detail?.time_calibration
  const offset = calibration?.offset_seconds ?? 0
  const isManualFallback = calibration?.resolution_method === 'manual_fallback'
  const direction = isManualFallback
    ? offset > 0
      ? '消费时间向后校正'
      : offset < 0
        ? '消费时间向前校正'
        : '消费时间无需校正'
    : offset > 0
      ? '源系统时钟快于本系统'
      : offset < 0
        ? '源系统时钟慢于本系统'
        : '两套系统时钟一致'

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/55 p-3 backdrop-blur-[2px] sm:p-6"
      onMouseDown={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="record-detail-title"
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-border bg-card shadow-2xl"
        onMouseDown={event => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4 sm:px-6">
          <div>
            <div className="mb-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Clock3 className="h-3.5 w-3.5" />
              消费记录 · 时间校准
            </div>
            <h2 id="record-detail-title" className="text-lg font-semibold">
              {displayedRecord.student_name || '未关联学生'}
            </h2>
            <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">
              {displayedRecord.transaction_id}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            autoFocus
            aria-label="关闭消费记录详情"
            className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="p-5 sm:p-6">
          {loading ? (
            <div className="space-y-3" aria-live="polite">
              <div className="h-36 animate-pulse rounded-xl bg-secondary" />
              <div className="h-24 animate-pulse rounded-xl bg-secondary/70" />
              <p className="text-center text-sm text-muted-foreground">正在查询这条记录当时的时差...</p>
            </div>
          ) : error ? (
            <div className="rounded-xl border border-health-red/25 bg-health-red/5 p-5 text-center">
              <AlertCircle className="mx-auto h-5 w-5 text-health-red" />
              <p className="mt-2 text-sm">{error}</p>
              <button
                type="button"
                onClick={onRetry}
                className="mt-4 rounded-lg border border-border bg-background px-3 py-2 text-sm transition-colors hover:bg-secondary"
              >
                重新加载
              </button>
            </div>
          ) : calibration ? (
            <div className="space-y-4">
              <div className="overflow-hidden rounded-xl border border-border bg-secondary/30">
                <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-end sm:justify-between">
                  <div>
                    <span className="inline-flex rounded-md border border-border bg-background px-2 py-1 text-xs text-muted-foreground">
                      {CALIBRATION_METHOD_LABELS[calibration.resolution_method]}
                    </span>
                    <p className="mt-3 text-xs text-muted-foreground">
                      {isManualFallback ? '人工校正量' : '当时系统间时差'}
                    </p>
                    <p className="mt-1 font-mono text-3xl font-semibold tabular-nums tracking-tight">
                      {formatSignedSeconds(offset)}
                    </p>
                  </div>
                  <div className="rounded-lg border border-border bg-background px-3 py-2 text-sm">
                    {direction}
                  </div>
                </div>
                <div className="border-t border-border px-5 py-3 text-xs leading-5 text-muted-foreground">
                  {isManualFallback
                    ? '人工配置值会直接叠加到消费时间：正值向后校正，负值向前校正。'
                    : '正值表示源系统比本系统快，负值表示源系统比本系统慢；自动校正会对消费时间应用相反数。'}
                </div>
              </div>

              <div className="grid gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-2">
                {[
                  ['消费时间', fmtDateTime(displayedRecord.transaction_time)],
                  ['校正后时间', fmtDateTime(calibration.aligned_transaction_time)],
                  ['实际校正量', formatSignedSeconds(calibration.adjustment_seconds)],
                  ['源系统采样时间', fmtDateTime(calibration.source_time)],
                  ['本系统采样时间', fmtDateTime(calibration.local_time)],
                  ['采样与消费时间距离', formatSampleDistance(calibration.sample_distance_seconds)],
                  ['采样往返耗时', calibration.rtt_ms == null ? '—' : `${calibration.rtt_ms.toFixed(1)} ms`],
                  ['采样记录时间', fmtDateTime(calibration.sample_created_at)],
                ].map(([label, value]) => (
                  <div key={label} className="bg-card px-4 py-3">
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <p className="mt-1 font-mono text-sm tabular-nums">{value}</p>
                  </div>
                ))}
              </div>

              {calibration.resolution_method === 'manual_fallback' ? (
                <p className="rounded-lg border border-health-amber/25 bg-health-amber/5 px-3 py-2 text-xs leading-5 text-muted-foreground">
                  当时没有可用的自动校准采样，本条记录展示并采用系统设置中的人工时差。
                </p>
              ) : null}

              <div className="grid gap-3 border-t border-border pt-4 text-sm sm:grid-cols-3">
                <div><span className="text-muted-foreground">金额：</span><span className="font-mono">¥{displayedRecord.amount.toFixed(2)}</span></div>
                <div><span className="text-muted-foreground">通道：</span><span>{displayedRecord.channel_id || '—'}</span></div>
                <div><span className="text-muted-foreground">学号：</span><span className="font-mono">{displayedRecord.student_no || '—'}</span></div>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  )
}

export default function ConsumptionPage() {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [settings, setSettings] = useState<ImportSettings>({ allowed_locations: [] })
  const [settingsLoading, setSettingsLoading] = useState(true)
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const [settingsError, setSettingsError] = useState('')
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [downloadingTemplate, setDownloadingTemplate] = useState(false)
  const [allowedLocationsInput, setAllowedLocationsInput] = useState('')
  const [records, setRecords] = useState<ConsumptionRecord[]>([])
  const [recordsTotal, setRecordsTotal] = useState(0)
  const [recordsPage, setRecordsPage] = useUrlPage()
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [recordFilters, setRecordFilters] = useState<RecordFilters>({ ...EMPTY_RECORD_FILTERS })
  const [filterDraft, setFilterDraft] = useState<RecordFilters>({ ...EMPTY_RECORD_FILTERS })
  const [batchQuery, setBatchQuery] = useState('')
  const [batchListQuery, setBatchListQuery] = useState('')
  const [batches, setBatches] = useState<BatchSummary[]>([])
  const [batchesLoading, setBatchesLoading] = useState(false)
  const [deletingRecordId, setDeletingRecordId] = useState<number | null>(null)
  const [deletingBatch, setDeletingBatch] = useState(false)
  const [selectedRecord, setSelectedRecord] = useState<ConsumptionRecord | null>(null)
  const [recordDetail, setRecordDetail] = useState<ConsumptionRecordDetail | null>(null)
  const [recordDetailLoading, setRecordDetailLoading] = useState(false)
  const [recordDetailError, setRecordDetailError] = useState('')
  const [importPopoverOpen, setImportPopoverOpen] = useState(false)
  const [popoverView, setPopoverView] = useState<'import' | 'settings'>('import')
  const importPopoverRef = useRef<HTMLDivElement | null>(null)
  const detailRequestIdRef = useRef(0)

  const loadImportSettings = useCallback(async () => {
    setSettingsLoading(true)
    setSettingsError('')
    try {
      const res = await consumptionApi.importSettings()
      const nextSettings: ImportSettings = {
        allowed_locations: Array.isArray(res.data.data?.allowed_locations) ? res.data.data.allowed_locations : [],
      }
      setSettings(nextSettings)
      setAllowedLocationsInput(nextSettings.allowed_locations.join('\n'))
      setSettingsLoaded(true)
    } catch {
      setSettingsLoaded(false)
      setSettingsError('导入设置加载失败，请刷新后重试')
    } finally {
      setSettingsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadImportSettings()
  }, [loadImportSettings])

  const toggleImportPopover = () => {
    setImportPopoverOpen((open) => {
      if (!open) void loadImportSettings()
      return !open
    })
  }

  useEffect(() => {
    if (!importPopoverOpen) return

    const handlePointerDown = (event: MouseEvent) => {
      if (!importPopoverRef.current?.contains(event.target as Node)) {
        setImportPopoverOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setImportPopoverOpen(false)
    }

    document.addEventListener('mousedown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [importPopoverOpen])

  const fetchRecords = useCallback(async (nextFilters: RecordFilters, nextPage: number) => {
    setRecordsLoading(true)
    try {
      const res = await consumptionApi.records({
        page: nextPage,
        page_size: PAGE_SIZE,
        ...buildRecordParams(nextFilters),
      })
      const data = res.data.data
      setRecords(Array.isArray(data?.items) ? data.items : [])
      setRecordsTotal(Number(data?.total || 0))
      setRecordsPage(Number(data?.page || nextPage))
    } catch {
      toast.error('消费记录加载失败')
    } finally {
      setRecordsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRecords(recordFilters, recordsPage)
  }, [fetchRecords, recordFilters, recordsPage])

  const loadBatches = useCallback(async (query = batchListQuery) => {
    setBatchesLoading(true)
    try {
      const res = await consumptionApi.recordBatches(query ? { batch: query } : undefined)
      const items = res.data.data?.items
      setBatches(Array.isArray(items) ? items : [])
    } catch {
      toast.error('导入批次加载失败')
    } finally {
      setBatchesLoading(false)
    }
  }, [batchListQuery])

  useEffect(() => {
    loadBatches()
  }, [loadBatches])

  const onDrop = useCallback(async (accepted: File[]) => {
    if (!accepted.length) return
    const f = accepted[0]
    setFile(f)
    setPreview(null)
    setResult(null)
    setLoading(true)
    try {
      const res = await consumptionApi.preview(f)
      const data: PreviewData = res.data.data
      setPreview(data)
      setMapping(data.suggested_mapping || {})
    } catch {
      toast.error('文件解析失败，请检查格式')
    } finally {
      setLoading(false)
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'text/csv': ['.csv'], 'application/vnd.ms-excel': ['.xls'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
    maxFiles: 1,
  })

  const applyBatchFilter = useCallback((batchId: string) => {
    const nextFilters = normalizeRecordFilters({ ...recordFilters, batch: batchId })
    setRecordFilters(nextFilters)
    setFilterDraft(nextFilters)
    setBatchQuery(batchId)
    setRecordsPage(1)
  }, [recordFilters])

  const handleImport = async () => {
    if (!file) return
    setImporting(true)
    try {
      const res = await consumptionApi.import(file, mapping)
      const importResult: ImportResult = res.data.data
      const nextFilters = normalizeRecordFilters({ ...EMPTY_RECORD_FILTERS, batch: importResult.batch_id })
      setResult(importResult)
      setRecordFilters(nextFilters)
      setFilterDraft(nextFilters)
      setBatchQuery('')
      setBatchListQuery('')
      setRecordsPage(1)
      await loadBatches('')
      toast.success(`成功导入 ${importResult.imported} 条记录`)
    } finally {
      setImporting(false)
    }
  }

  const handleSaveSettings = async () => {
    setSettingsSaving(true)
    try {
      const allowed_locations = parseAllowedLocationsInput(allowedLocationsInput)
      const res = await consumptionApi.updateImportSettings({ allowed_locations })
      const nextSettings: ImportSettings = {
        allowed_locations: Array.isArray(res.data.data?.allowed_locations) ? res.data.data.allowed_locations : [],
      }
      setSettings(nextSettings)
      setAllowedLocationsInput(nextSettings.allowed_locations.join('\n'))
      setSettingsLoaded(true)
      setSettingsError('')
      toast.success(nextSettings.allowed_locations.length ? '导入通道设置已保存' : '已清空通道限制')
    } finally {
      setSettingsSaving(false)
    }
  }

  const handleDownloadTemplate = async () => {
    setDownloadingTemplate(true)
    try {
      const res = await consumptionApi.downloadTemplate()
      const url = window.URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = '消费记录导入模板.xlsx'
      a.click()
      window.URL.revokeObjectURL(url)
    } catch {
      toast.error('下载模板失败')
    } finally {
      setDownloadingTemplate(false)
    }
  }

  const handleBatchSearch = () => {
    const nextBatch = batchQuery.trim()
    setBatchListQuery(nextBatch)
    const nextFilters = normalizeRecordFilters({ ...recordFilters, batch: nextBatch })
    setRecordFilters(nextFilters)
    setFilterDraft(nextFilters)
    setRecordsPage(1)
  }

  const clearBatchFilter = () => {
    const nextFilters = normalizeRecordFilters({ ...recordFilters, batch: '' })
    setBatchQuery('')
    setBatchListQuery('')
    setRecordFilters(nextFilters)
    setFilterDraft(nextFilters)
    setRecordsPage(1)
  }

  const updateFilterDraft = useCallback((field: keyof RecordFilters, value: string) => {
    setFilterDraft(current => ({ ...current, [field]: value }))
  }, [])

  const handleApplyFilters = () => {
    const nextFilters = normalizeRecordFilters(filterDraft)
    setRecordFilters(nextFilters)
    setBatchQuery(nextFilters.batch)
    setBatchListQuery(nextFilters.batch)
    setRecordsPage(1)
  }

  const handleResetFilters = () => {
    const nextFilters = { ...EMPTY_RECORD_FILTERS }
    setFilterDraft(nextFilters)
    setRecordFilters(nextFilters)
    setBatchQuery('')
    setBatchListQuery('')
    setRecordsPage(1)
  }

  const handleDeleteRecord = async (record: ConsumptionRecord) => {
    if (!window.confirm(`确认删除流水号 ${record.transaction_id}？`)) return
    setDeletingRecordId(record.id)
    try {
      await consumptionApi.deleteRecord(record.id)
      toast.success('记录已删除')
      await Promise.all([fetchRecords(recordFilters, recordsPage), loadBatches(batchListQuery)])
    } finally {
      setDeletingRecordId(null)
    }
  }

  const openRecordDetail = useCallback(async (record: ConsumptionRecord) => {
    const requestId = detailRequestIdRef.current + 1
    detailRequestIdRef.current = requestId
    setSelectedRecord(record)
    setRecordDetail(null)
    setRecordDetailError('')
    setRecordDetailLoading(true)
    try {
      const res = await consumptionApi.record(record.id)
      if (detailRequestIdRef.current !== requestId) return
      setRecordDetail(res.data.data as ConsumptionRecordDetail)
    } catch {
      if (detailRequestIdRef.current !== requestId) return
      setRecordDetailError('这条记录的时间校准信息加载失败')
    } finally {
      if (detailRequestIdRef.current === requestId) setRecordDetailLoading(false)
    }
  }, [])

  const closeRecordDetail = useCallback(() => {
    detailRequestIdRef.current += 1
    setSelectedRecord(null)
    setRecordDetail(null)
    setRecordDetailError('')
    setRecordDetailLoading(false)
  }, [])

  const handleDeleteBatch = async () => {
    const targetBatch = recordFilters.batch.trim()
    if (!targetBatch) return
    if (!window.confirm(`确认删除批次 ${targetBatch} 的所有导入记录？`)) return
    setDeletingBatch(true)
    try {
      const res = await consumptionApi.deleteBatch(targetBatch)
      toast.success(`已删除 ${res.data.data?.deleted || 0} 条记录`)
      setRecordsPage(1)
      await Promise.all([fetchRecords(recordFilters, 1), loadBatches(batchListQuery)])
    } finally {
      setDeletingBatch(false)
    }
  }

  const reset = () => { setFile(null); setPreview(null); setResult(null); setMapping({}) }

  const activeFilterEntries = useMemo(() => ([
    { key: 'date_from', label: '开始日期', value: recordFilters.date_from },
    { key: 'date_to', label: '结束日期', value: recordFilters.date_to },
    { key: 'student', label: '学生', value: recordFilters.student },
    { key: 'channel_id', label: '通道', value: recordFilters.channel_id },
    { key: 'transaction_id', label: '流水号', value: recordFilters.transaction_id },
    { key: 'batch', label: '批次', value: recordFilters.batch },
  ].filter(item => item.value)), [recordFilters])

  const draftHasValues = useMemo(() => Object.values(filterDraft).some(Boolean), [filterDraft])
  const locationFilterEnabled = settings.allowed_locations.length > 0
  const requiredFields = REQUIRED_FIELDS
  const mappingComplete = requiredFields.every(f => mapping[f])
  const mappingFields = ['student_id', 'student_name', 'transaction_time', 'amount', 'transaction_id', 'channel_id']
  const importBlockedBySettings = settingsLoading || !settingsLoaded
  const totalPages = Math.max(1, Math.ceil(recordsTotal / PAGE_SIZE))
  const hasActiveFilters = activeFilterEntries.length > 0

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">消费记录导入</h1>
          <p className="text-sm text-muted-foreground mt-0.5">默认显示已导入记录，可按日期、学生、通道和批次筛选</p>
        </div>

        <div ref={importPopoverRef} className="relative w-full self-start sm:w-auto">
          <button
            onClick={toggleImportPopover}
            aria-expanded={importPopoverOpen}
            aria-haspopup="dialog"
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 sm:w-auto"
          >
            <Upload className="w-4 h-4" />
            导入与设置
            <ChevronDown className={cn('w-4 h-4 transition-transform', importPopoverOpen && 'rotate-180')} />
          </button>

          {importPopoverOpen && (
            <div
              role="dialog"
              aria-label="消费记录导入与设置"
              className="absolute right-0 z-30 mt-2 w-[min(calc(100vw-2rem),760px)] max-h-[calc(100vh-7rem)] overflow-y-auto rounded-xl border border-border bg-popover text-popover-foreground shadow-xl"
            >
              <div className="flex items-start justify-between gap-3 border-b border-border p-4">
                <div>
                  <h2 className="text-sm font-medium">导入与设置</h2>
                  <p className="text-xs text-muted-foreground mt-0.5">上传消费流水前可先确认字段映射和通道限制</p>
                </div>
                <button
                  onClick={() => setImportPopoverOpen(false)}
                  className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                  title="关闭"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="border-b border-border px-4 py-3">
                <div className="grid grid-cols-2 rounded-lg border border-border bg-secondary/70 p-1">
                  <button
                    onClick={() => setPopoverView('import')}
                    className={cn(
                      'flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors',
                      popoverView === 'import' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <Upload className="w-4 h-4" />
                    导入
                  </button>
                  <button
                    onClick={() => setPopoverView('settings')}
                    className={cn(
                      'flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors',
                      popoverView === 'settings' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    <Settings2 className="w-4 h-4" />
                    设置
                  </button>
                </div>
              </div>

              <div className="p-4 sm:p-5">
                {popoverView === 'import' ? (
                  <div className="space-y-4">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <h3 className="text-sm font-medium">导入消费记录</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">支持 CSV、XLS、XLSX（UTF-8/GBK 自动检测）</p>
                      </div>
                      <button
                        onClick={handleDownloadTemplate}
                        disabled={downloadingTemplate}
                        className="flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm transition-colors hover:bg-secondary disabled:opacity-50 sm:w-auto"
                      >
                        <Download className="w-4 h-4" />
                        {downloadingTemplate ? '下载中...' : '下载模板'}
                      </button>
                    </div>

                    {!result ? (
                      <div className="space-y-4">
                        {!file ? (
                          <div
                            {...getRootProps()}
                            className={cn(
                              'cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition-all',
                              isDragActive ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/40 hover:bg-secondary/50'
                            )}
                          >
                            <input {...getInputProps()} />
                            <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
                            <p className="text-sm font-medium">{isDragActive ? '释放以上传' : '拖拽文件到此处或点击选择'}</p>
                            <p className="text-xs text-muted-foreground mt-1">导入后会自动刷新下方记录列表</p>
                          </div>
                        ) : (
                          <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-4">
                            <FileText className="w-5 h-5 text-muted-foreground flex-shrink-0" />
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium">{file.name}</p>
                              <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
                            </div>
                            <button onClick={reset} className="rounded-md p-1.5 transition-colors hover:bg-secondary" title="移除文件">
                              <X className="w-4 h-4 text-muted-foreground" />
                            </button>
                          </div>
                        )}

                        {loading && (
                          <div className="p-6 text-center text-sm text-muted-foreground">
                            解析文件中...
                          </div>
                        )}

                        {preview && (
                          <>
                            <div className="rounded-xl border border-border bg-card p-4">
                              <h4 className="mb-4 text-sm font-medium">字段映射配置</h4>
                              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                                {mappingFields.map((field) => (
                                  <div key={field}>
                                    <label className="text-xs text-muted-foreground">
                                      {FIELD_LABELS[field]}{requiredFields.includes(field) ? ' *' : ''}
                                    </label>
                                    <select
                                      value={mapping[field] || ''}
                                      onChange={e => setMapping(m => ({ ...m, [field]: e.target.value }))}
                                      className={FILTER_INPUT_CLASS}
                                    >
                                      <option value="">-- 未映射 --</option>
                                      {preview.columns.map(col => <option key={col} value={col}>{col}</option>)}
                                    </select>
                                  </div>
                                ))}
                              </div>
                              {!mappingComplete && (
                                <p className="mt-3 flex items-center gap-1.5 text-xs text-health-amber">
                                  <AlertCircle className="w-3.5 h-3.5" />请完成必填字段映射
                                </p>
                              )}
                              {importBlockedBySettings && (
                                <p className="mt-2 flex items-center gap-1.5 text-xs text-health-amber">
                                  <AlertCircle className="w-3.5 h-3.5" />导入前需先成功加载导入设置
                                </p>
                              )}
                              {locationFilterEnabled && (
                                <p className="mt-2 text-xs text-muted-foreground">
                                  已启用通道过滤，仅会导入通道为 {settings.allowed_locations.join('、')} 的记录。
                                </p>
                              )}
                            </div>

                            <div className="overflow-hidden rounded-xl border border-border bg-card">
                              <div className="flex items-center justify-between border-b border-border p-4">
                                <span className="text-sm font-medium">数据预览（前 {preview.preview_rows.length} 行 / 共 {preview.total_rows} 行）</span>
                              </div>
                              <div className="max-h-64 overflow-auto">
                                <table className="data-table">
                                  <thead>
                                    <tr>
                                      {preview.columns.slice(0, 8).map(col => <th key={col}>{col}</th>)}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {preview.preview_rows.slice(0, 5).map((row, i) => (
                                      <tr key={i}>
                                        {preview.columns.slice(0, 8).map(col => (
                                          <td key={col} className="max-w-32 truncate font-mono text-xs">{row[col] ?? '--'}</td>
                                        ))}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>

                            <div className="flex justify-end">
                              <button
                                onClick={handleImport}
                                disabled={importBlockedBySettings || !mappingComplete || importing}
                                className="flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                              >
                                {importing ? '导入中...' : (
                                  <>开始导入 <ArrowRight className="w-4 h-4" /></>
                                )}
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    ) : (
                      <div className="space-y-4">
                        <div className="rounded-xl border border-border bg-card p-5">
                          <div className="mb-5 flex items-center gap-3">
                            <CheckCircle2 className="w-6 h-6 text-health-green" />
                            <div>
                              <h4 className="font-medium">导入完成</h4>
                              <p className="text-xs text-muted-foreground">批次 ID: {result.batch_id}</p>
                            </div>
                          </div>
                          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
                            {[
                              { label: '总行数', value: result.total_rows, color: '' },
                              { label: '成功导入', value: result.imported, color: 'text-health-green' },
                              { label: '重复跳过', value: result.skipped_duplicates, color: 'text-health-amber' },
                              { label: '通道过滤跳过', value: result.skipped_by_location, color: 'text-muted-foreground' },
                              { label: '错误行数', value: result.errors.length, color: 'text-health-red' },
                            ].map(({ label, value, color }) => (
                              <div key={label} className="rounded-lg bg-secondary p-3 text-center">
                                <div className={cn('font-mono text-2xl font-light', color)}>{value}</div>
                                <div className="mt-1 text-xs text-muted-foreground">{label}</div>
                              </div>
                            ))}
                          </div>
                        </div>

                        {result.errors.length > 0 && (
                          <div className="rounded-xl border border-health-red/20 bg-card p-4">
                            <h4 className="mb-3 text-sm font-medium text-health-red">错误明细（前 20 行）</h4>
                            <div className="space-y-1.5">
                              {result.errors.slice(0, 20).map((e, i) => (
                                <div key={i} className="flex items-start gap-2 text-xs">
                                  <span className="font-mono text-muted-foreground">行 {e.row}</span>
                                  <span className="text-health-red">{e.error}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        <button onClick={reset} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
                          <X className="w-3.5 h-3.5" />重新导入
                        </button>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <h3 className="text-sm font-medium">导入设置</h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          设置允许导入的通道，一行一个；留空表示不过滤。可填写视频通道 ID，或与导入文件通道列一致的文本。
                        </p>
                      </div>
                      <button
                        onClick={handleSaveSettings}
                        disabled={settingsLoading || settingsSaving}
                        className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
                      >
                        {settingsSaving ? '保存中...' : '保存设置'}
                      </button>
                    </div>
                    <textarea
                      value={allowedLocationsInput}
                      onChange={e => setAllowedLocationsInput(e.target.value)}
                      rows={Math.max(5, (allowedLocationsInput.match(/\n/g)?.length || 0) + 2)}
                      disabled={settingsLoading}
                      placeholder={'例如：\n一食堂一楼\n二食堂档口A'}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-60"
                    />
                    <div className="text-xs text-muted-foreground">
                      {settingsLoading
                        ? '加载设置中...'
                        : settingsError
                          ? settingsError
                          : locationFilterEnabled
                            ? `当前已启用通道过滤：${settings.allowed_locations.join('、')}`
                            : '当前未限制通道，导入时不会按通道过滤。'}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[320px_minmax(0,1fr)]">
        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex items-center justify-between gap-3 border-b border-border p-4">
            <div>
              <h2 className="text-sm font-medium">导入批次</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">按批次号查询和回滚</p>
            </div>
            <button
              onClick={() => loadBatches(batchListQuery)}
              disabled={batchesLoading}
              className="rounded-lg border border-border p-2 transition-colors hover:bg-secondary disabled:opacity-50"
              title="刷新批次"
            >
              <RefreshCw className={cn('w-4 h-4', batchesLoading && 'animate-spin')} />
            </button>
          </div>
          <div className="border-b border-border p-4">
            <div className="flex gap-2">
              <input
                value={batchQuery}
                onChange={e => setBatchQuery(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') handleBatchSearch()
                }}
                placeholder="输入批次号"
                className="min-w-0 flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20"
              />
              <button
                onClick={handleBatchSearch}
                className="rounded-lg bg-primary p-2 text-primary-foreground transition-colors hover:bg-primary/90"
                title="查询批次"
              >
                <Search className="w-4 h-4" />
              </button>
            </div>
            {recordFilters.batch && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">当前批次：</span>
                <span className="rounded-md bg-secondary px-2 py-1 font-mono text-xs">{recordFilters.batch}</span>
                <button onClick={clearBatchFilter} className="text-xs text-muted-foreground hover:text-foreground">
                  清除
                </button>
              </div>
            )}
          </div>
          <div className="max-h-[520px] overflow-y-auto">
            {batchesLoading ? (
              <div className="p-5 text-sm text-muted-foreground">加载批次中...</div>
            ) : batches.length === 0 ? (
              <div className="p-5 text-sm text-muted-foreground">暂无导入批次</div>
            ) : (
              <div className="divide-y divide-border">
                {batches.map(batch => (
                  <button
                    key={batch.batch_id}
                    onClick={() => applyBatchFilter(batch.batch_id)}
                    className={cn(
                      'w-full p-4 text-left transition-colors hover:bg-secondary/70',
                      recordFilters.batch === batch.batch_id && 'bg-secondary'
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <span className="break-all font-mono text-xs">{batch.batch_id}</span>
                      <span className="rounded-md border border-border bg-background px-2 py-0.5 text-xs tabular-nums">
                        {batch.record_count} 条
                      </span>
                    </div>
                    <div className="mt-2 text-xs text-muted-foreground">
                      ¥{batch.total_amount.toFixed(2)} · {fmtDateTime(batch.created_at)}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <div className="flex flex-col gap-3 border-b border-border p-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-sm font-medium">已导入消费记录</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {hasActiveFilters ? '筛选结果' : '全部导入记录'} · 共 {recordsTotal} 条 · 点击记录查看当时时差
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {recordFilters.batch && (
                <button
                  onClick={handleDeleteBatch}
                  disabled={deletingBatch || recordsLoading || recordsTotal === 0}
                  className="flex items-center gap-1.5 rounded-lg border border-health-red/30 px-3 py-2 text-sm text-health-red transition-colors hover:bg-health-red/10 disabled:opacity-50"
                >
                  <Trash2 className="w-4 h-4" />
                  {deletingBatch ? '删除中...' : '删除此批次'}
                </button>
              )}
              <button
                onClick={() => fetchRecords(recordFilters, recordsPage)}
                disabled={recordsLoading}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm transition-colors hover:bg-secondary disabled:opacity-50"
              >
                <RefreshCw className={cn('w-4 h-4', recordsLoading && 'animate-spin')} />
                刷新
              </button>
            </div>
          </div>

          <form
            onSubmit={(event) => {
              event.preventDefault()
              handleApplyFilters()
            }}
            className="border-b border-border bg-secondary/30 p-4"
          >
            <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <Filter className="w-3.5 h-3.5" />
              筛选记录
            </div>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-6">
              <label className="text-xs text-muted-foreground">
                开始日期
                <input
                  type="date"
                  value={filterDraft.date_from}
                  onChange={e => updateFilterDraft('date_from', e.target.value)}
                  className={FILTER_INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                结束日期
                <input
                  type="date"
                  value={filterDraft.date_to}
                  onChange={e => updateFilterDraft('date_to', e.target.value)}
                  className={FILTER_INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                学生
                <input
                  value={filterDraft.student}
                  onChange={e => updateFilterDraft('student', e.target.value)}
                  placeholder="姓名或学号"
                  className={FILTER_INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                通道
                <input
                  value={filterDraft.channel_id}
                  onChange={e => updateFilterDraft('channel_id', e.target.value)}
                  placeholder="通道号或地点"
                  className={FILTER_INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                流水号
                <input
                  value={filterDraft.transaction_id}
                  onChange={e => updateFilterDraft('transaction_id', e.target.value)}
                  placeholder="输入关键字"
                  className={FILTER_INPUT_CLASS}
                />
              </label>
              <label className="text-xs text-muted-foreground">
                批次号
                <input
                  value={filterDraft.batch}
                  onChange={e => updateFilterDraft('batch', e.target.value)}
                  placeholder="完整批次号"
                  className={FILTER_INPUT_CLASS}
                />
              </label>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="submit"
                className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground transition-colors hover:bg-primary/90"
              >
                <Search className="w-4 h-4" />
                查询
              </button>
              <button
                type="button"
                onClick={handleResetFilters}
                disabled={!draftHasValues && !hasActiveFilters}
                className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm transition-colors hover:bg-background disabled:opacity-50"
              >
                <RotateCcw className="w-4 h-4" />
                重置
              </button>
              {hasActiveFilters && (
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-muted-foreground">当前筛选：</span>
                  {activeFilterEntries.map(item => (
                    <span key={item.key} className="rounded-md border border-border bg-background px-2 py-1">
                      {item.label}: <span className="font-mono">{item.value}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </form>

          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>消费时间</th>
                  <th>学生姓名</th>
                  <th>学号</th>
                  <th>消费卡号</th>
                  <th>金额</th>
                  <th>通道</th>
                  <th>流水号</th>
                  <th>批次号</th>
                  <th className="text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {recordsLoading ? (
                  <tr>
                    <td colSpan={9} className="text-sm text-muted-foreground">加载记录中...</td>
                  </tr>
                ) : records.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-sm text-muted-foreground">暂无导入记录</td>
                  </tr>
                ) : records.map(record => (
                  <tr
                    key={record.id}
                    tabIndex={0}
                    aria-label={`查看 ${record.student_name || record.transaction_id} 的消费记录及时差`}
                    onClick={() => void openRecordDetail(record)}
                    onKeyDown={event => {
                      if (event.target !== event.currentTarget) return
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        void openRecordDetail(record)
                      }
                    }}
                    className="cursor-pointer transition-colors hover:bg-secondary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
                  >
                    <td className="whitespace-nowrap">{fmtDateTime(record.transaction_time)}</td>
                    <td className="font-medium whitespace-nowrap">{record.student_name || '--'}</td>
                    <td className="font-mono text-xs whitespace-nowrap">{record.student_no || '--'}</td>
                    <td className="font-mono text-xs whitespace-nowrap">{record.card_code || '--'}</td>
                    <td className="font-mono tabular-nums">¥{record.amount.toFixed(2)}</td>
                    <td className="font-mono text-xs">{record.channel_id || '--'}</td>
                    <td className="max-w-[180px] truncate font-mono text-xs">{record.transaction_id}</td>
                    <td className="max-w-[180px] truncate font-mono text-xs">{record.import_batch || '--'}</td>
                    <td className="text-right">
                      <button
                        onClick={event => {
                          event.stopPropagation()
                          void openRecordDetail(record)
                        }}
                        className="inline-flex items-center justify-center rounded-lg p-2 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                        title="查看记录及时差"
                        aria-label="查看记录及时差"
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                      <button
                        onClick={event => {
                          event.stopPropagation()
                          void handleDeleteRecord(record)
                        }}
                        disabled={deletingRecordId === record.id}
                        className="inline-flex items-center justify-center rounded-lg p-2 text-health-red transition-colors hover:bg-health-red/10 disabled:opacity-50"
                        title="删除记录"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <DataPagination
            page={recordsPage}
            totalPages={totalPages}
            totalItems={recordsTotal}
            disabled={recordsLoading}
            onPageChange={setRecordsPage}
            className="border-t border-border p-4"
            ariaLabel="消费记录分页"
          />
        </div>
      </div>
      {selectedRecord ? (
        <RecordDetailDialog
          record={selectedRecord}
          detail={recordDetail}
          loading={recordDetailLoading}
          error={recordDetailError}
          onClose={closeRecordDetail}
          onRetry={() => void openRecordDetail(selectedRecord)}
        />
      ) : null}
    </div>
  )
}
