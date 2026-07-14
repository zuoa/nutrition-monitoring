import { useState } from 'react'
import { Download, FileSpreadsheet, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { studentApi } from '@/api/client'
import { AdminDialogShell, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'

type ImportPreview = {
  summary: { total: number; creates: number; updates: number; errors: number }
  rows: Array<{
    row_number: number
    student_no: string
    name: string
    class_name: string
    action: 'create' | 'update' | 'error'
    errors: string[]
  }>
}

export function StudentImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [loading, setLoading] = useState(false)

  const downloadTemplate = async () => {
    const response = await studentApi.importTemplate()
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = '学生导入模板.xlsx'
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const runPreview = async () => {
    if (!file) return
    setLoading(true)
    try {
      const response = await studentApi.previewImport(file)
      setPreview(response.data?.data || null)
    } finally {
      setLoading(false)
    }
  }

  const applyImport = async () => {
    if (!file || !preview || preview.summary.errors > 0) return
    setLoading(true)
    try {
      const response = await studentApi.applyImport(file)
      const result = response.data?.data || {}
      toast.success(`导入完成：新增 ${result.imported || 0} 人，更新 ${result.updated || 0} 人`)
      onImported()
    } finally {
      setLoading(false)
    }
  }

  return (
    <AdminDialogShell title="导入学生名单" description="导入只匹配已建立的组织路径，不会因拼写错误自动创建年级或班级。" onClose={onClose} wide>
      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-4 rounded-lg border border-border bg-secondary/25 p-4">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/10 text-primary"><FileSpreadsheet className="h-5 w-5" /></div>
          <div>
            <h3 className="text-sm font-medium">先准备组织，再填写名单</h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">模板要求学校、校区、学段、年级和班级名称完全一致。重复学号会更新原学生。</p>
          </div>
          <button type="button" className={`${secondaryButtonClassName} w-full gap-2`} onClick={downloadTemplate}><Download className="h-4 w-4" />下载模板</button>
          <label className="block cursor-pointer rounded-lg border border-dashed border-border bg-background p-4 text-center transition hover:border-primary/40">
            <Upload className="mx-auto h-5 w-5 text-muted-foreground" />
            <span className="mt-2 block truncate text-sm">{file?.name || '选择 CSV 或 Excel 文件'}</span>
            <input type="file" className="hidden" accept=".csv,.xls,.xlsx" onChange={event => { setFile(event.target.files?.[0] || null); setPreview(null) }} />
          </label>
          <button type="button" className={`${primaryButtonClassName} w-full`} disabled={!file || loading} onClick={runPreview}>{loading ? '校验中…' : '校验并预览'}</button>
        </aside>

        <div className="min-w-0">
          {!preview ? (
            <div className="flex min-h-72 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">选择文件后先校验，确认无误再写入</div>
          ) : (
            <>
              <div className="mb-3 grid grid-cols-4 gap-2">
                {[
                  ['总行数', preview.summary.total],
                  ['新增', preview.summary.creates],
                  ['更新', preview.summary.updates],
                  ['错误', preview.summary.errors],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold tabular-nums">{value}</div></div>
                ))}
              </div>
              <div className="max-h-[50vh] overflow-auto rounded-lg border border-border">
                <table className="data-table min-w-[680px]">
                  <thead><tr><th>行</th><th>学号</th><th>姓名</th><th>班级</th><th>结果</th></tr></thead>
                  <tbody>
                    {preview.rows.map(row => (
                      <tr key={row.row_number}>
                        <td>{row.row_number}</td><td className="font-mono text-xs">{row.student_no || '—'}</td><td>{row.name || '—'}</td><td>{row.class_name || '—'}</td>
                        <td className={row.action === 'error' ? 'text-destructive' : ''}>{row.action === 'create' ? '新增' : row.action === 'update' ? '更新' : row.errors.join('；')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
      <div className="mt-5 flex items-center justify-between gap-3 border-t border-border pt-4">
        <p className="text-xs text-muted-foreground">有错误时不会写入任何学生数据。</p>
        <div className="flex gap-2"><button type="button" className={secondaryButtonClassName} onClick={onClose}>取消</button><button type="button" className={primaryButtonClassName} disabled={!preview || preview.summary.errors > 0 || loading} onClick={applyImport}>{loading ? '导入中…' : '确认导入'}</button></div>
      </div>
    </AdminDialogShell>
  )
}
