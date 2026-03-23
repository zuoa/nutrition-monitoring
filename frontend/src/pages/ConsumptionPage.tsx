import { useState, useCallback } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, FileText, ArrowRight, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { consumptionApi } from '@/api/client'
import { cn } from '@/lib/utils'
import toast from 'react-hot-toast'

const REQUIRED_FIELDS = ['student_id', 'transaction_time', 'amount', 'transaction_id']
const FIELD_LABELS: Record<string, string> = {
  student_id: '学号/消费卡号 *',
  student_name: '学生姓名',
  transaction_time: '消费时间 *',
  amount: '消费金额 *',
  transaction_id: '流水号 *',
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
  errors: { row: number; error: string }[]
  total_rows: number
}

export default function ConsumptionPage() {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<PreviewData | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)

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

  const reset = () => { setFile(null); setPreview(null); setResult(null); setMapping({}) }

  const mappingComplete = REQUIRED_FIELDS.every(f => mapping[f])

  return (
    <div className="p-4 sm:p-6 max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-display">消费记录导入</h1>
        <p className="text-sm text-muted-foreground mt-0.5">支持 CSV、XLS、XLSX 格式</p>
      </div>

      {!result ? (
        <div className="space-y-5">
          {/* Drop zone */}
          {!file ? (
            <div
              {...getRootProps()}
              className={cn(
                'border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all',
                isDragActive ? 'border-foreground bg-foreground/5' : 'border-border hover:border-foreground/40 hover:bg-secondary/50'
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
                  {Object.entries(FIELD_LABELS).map(([field, label]) => (
                    <div key={field}>
                      <label className="text-xs text-muted-foreground">{label}</label>
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
                    <AlertCircle className="w-3.5 h-3.5" />请完成必填字段映射
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
                  disabled={!mappingComplete || importing}
                  className="flex items-center gap-2 bg-foreground text-background text-sm px-6 py-2.5 rounded-lg hover:bg-foreground/90 transition-colors disabled:opacity-50"
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
