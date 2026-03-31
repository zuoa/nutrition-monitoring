import { useState, useCallback, useEffect } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, FileText, ArrowRight, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { consumptionApi } from '@/api/client'
import { cn } from '@/lib/utils'
import toast from 'react-hot-toast'

const REQUIRED_FIELDS = ['student_id', 'transaction_time', 'amount', 'transaction_id']
const FIELD_LABELS: Record<string, string> = {
  student_id: '学号/消费卡号',
  student_name: '学生姓名',
  transaction_time: '消费时间',
  amount: '消费金额',
  transaction_id: '流水号',
  transaction_location: '交易地点',
}

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

const parseAllowedLocationsInput = (value: string) =>
  Array.from(new Set(
    value
      .split(/\r?\n|[，,；;]/)
      .map(item => item.trim())
      .filter(Boolean),
  ))

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
  const [allowedLocationsInput, setAllowedLocationsInput] = useState('')

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

  const handleImport = async () => {
    if (!file) return
    setImporting(true)
    try {
      const res = await consumptionApi.import(file, mapping)
      setResult(res.data.data)
      toast.success(`成功导入 ${res.data.data.imported} 条记录`)
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
      toast.success(nextSettings.allowed_locations.length ? '导入地点设置已保存' : '已清空地点限制')
    } finally {
      setSettingsSaving(false)
    }
  }

  const reset = () => { setFile(null); setPreview(null); setResult(null); setMapping({}) }

  const locationFilterEnabled = settings.allowed_locations.length > 0
  const requiredFields = locationFilterEnabled ? [...REQUIRED_FIELDS, 'transaction_location'] : REQUIRED_FIELDS
  const mappingComplete = requiredFields.every(f => mapping[f])
  const mappingFields = ['student_id', 'student_name', 'transaction_time', 'amount', 'transaction_id', 'transaction_location']
  const importBlockedBySettings = settingsLoading || !settingsLoaded

  return (
    <div className="p-4 sm:p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">消费记录导入</h1>
        <p className="text-sm text-muted-foreground mt-0.5">支持 CSV、XLS、XLSX 格式</p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 mb-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-medium">导入设置</h2>
            <p className="text-xs text-muted-foreground mt-1">
              设置允许导入的交易地点，一行一个；留空表示不过滤。
            </p>
          </div>
          <button
            onClick={handleSaveSettings}
            disabled={settingsLoading || settingsSaving}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {settingsSaving ? '保存中...' : '保存设置'}
          </button>
        </div>
        <textarea
          value={allowedLocationsInput}
          onChange={e => setAllowedLocationsInput(e.target.value)}
          rows={Math.max(4, (allowedLocationsInput.match(/\n/g)?.length || 0) + 2)}
          disabled={settingsLoading}
          placeholder={'例如：\n一食堂一楼\n二食堂档口A'}
          className="mt-4 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-60"
        />
        <div className="mt-3 text-xs text-muted-foreground">
          {settingsLoading
            ? '加载设置中...'
            : settingsError
              ? settingsError
            : locationFilterEnabled
              ? `当前已启用地点过滤：${settings.allowed_locations.join('、')}`
              : '当前未限制交易地点，导入时不会按地点过滤。'}
        </div>
      </div>

      {!result ? (
        <div className="space-y-5">
          {/* Drop zone */}
          {!file ? (
            <div
              {...getRootProps()}
              className={cn(
                'border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all',
                isDragActive ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/40 hover:bg-secondary/50'
              )}
            >
              <input {...getInputProps()} />
              <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
              <p className="text-sm font-medium">{isDragActive ? '释放以上传' : '拖拽文件到此处或点击选择'}</p>
              <p className="text-xs text-muted-foreground mt-1">支持 CSV、XLS、XLSX（UTF-8/GBK 自动检测）</p>
            </div>
          ) : (
            <div className="flex items-center gap-3 p-4 bg-card border border-border rounded-xl">
              <FileText className="w-5 h-5 text-muted-foreground flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{file.name}</p>
                <p className="text-xs text-muted-foreground">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
              <button onClick={reset} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
                <X className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          )}

          {/* Loading */}
          {loading && (
            <div className="p-8 text-center text-sm text-muted-foreground">
              解析文件中...
            </div>
          )}

          {/* Preview & mapping */}
          {preview && (
            <>
              {/* Field mapping */}
              <div className="bg-card border border-border rounded-xl p-5">
                <h2 className="text-sm font-medium mb-4">字段映射配置</h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {mappingFields.map((field) => (
                    <div key={field}>
                      <label className="text-xs text-muted-foreground">
                        {FIELD_LABELS[field]}{requiredFields.includes(field) ? ' *' : ''}
                      </label>
                      <select
                        value={mapping[field] || ''}
                        onChange={e => setMapping(m => ({ ...m, [field]: e.target.value }))}
                        className="mt-1 w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                      >
                        <option value="">— 未映射 —</option>
                        {preview.columns.map(col => <option key={col} value={col}>{col}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
                {!mappingComplete && (
                  <p className="mt-3 text-xs text-health-amber flex items-center gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5" />请完成必填字段映射{locationFilterEnabled ? '，并映射交易地点字段' : ''}
                  </p>
                )}
                {importBlockedBySettings && (
                  <p className="mt-2 text-xs text-health-amber flex items-center gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5" />导入前需先成功加载导入设置
                  </p>
                )}
                {locationFilterEnabled && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    已启用交易地点过滤，仅会导入地点为 {settings.allowed_locations.join('、')} 的记录。
                  </p>
                )}
              </div>

              {/* Preview table */}
              <div className="bg-card border border-border rounded-xl overflow-hidden">
                <div className="flex items-center justify-between p-4 border-b border-border">
                  <span className="text-sm font-medium">数据预览（前 {preview.preview_rows.length} 行 / 共 {preview.total_rows} 行）</span>
                </div>
                <div className="overflow-x-auto">
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
                            <td key={col} className="text-xs font-mono max-w-32 truncate">{row[col] ?? '—'}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Import button */}
              <div className="flex justify-end">
                <button
                  onClick={handleImport}
                  disabled={importBlockedBySettings || !mappingComplete || importing}
                  className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-6 py-2.5 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
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
        /* Result */
        <div className="space-y-5">
          <div className="bg-card border border-border rounded-xl p-6">
            <div className="flex items-center gap-3 mb-5">
              <CheckCircle2 className="w-6 h-6 text-health-green" />
              <div>
                <h2 className="font-medium">导入完成</h2>
                <p className="text-xs text-muted-foreground">批次 ID: {result.batch_id}</p>
              </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
              {[
                { label: '总行数', value: result.total_rows, color: '' },
                { label: '成功导入', value: result.imported, color: 'text-health-green' },
                { label: '重复跳过', value: result.skipped_duplicates, color: 'text-health-amber' },
                { label: '地点过滤跳过', value: result.skipped_by_location, color: 'text-muted-foreground' },
                { label: '错误行数', value: result.errors.length, color: 'text-health-red' },
              ].map(({ label, value, color }) => (
                <div key={label} className="text-center p-3 sm:p-4 bg-secondary rounded-lg">
                  <div className={cn('text-2xl font-mono font-light', color)}>{value}</div>
                  <div className="text-xs text-muted-foreground mt-1">{label}</div>
                </div>
              ))}
            </div>
          </div>

          {result.errors.length > 0 && (
            <div className="bg-card border border-health-red/20 rounded-xl p-5">
              <h3 className="text-sm font-medium text-health-red mb-3">错误明细（前 20 行）</h3>
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

          <button onClick={reset} className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1.5">
            <X className="w-3.5 h-3.5" />重新导入
          </button>
        </div>
      )}
    </div>
  )
}
