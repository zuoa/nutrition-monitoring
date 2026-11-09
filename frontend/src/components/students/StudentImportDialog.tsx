import { useState } from 'react'
import { Download, FileSpreadsheet, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { studentApi } from '@/api/client'
import { AdminDialogShell, primaryButtonClassName, secondaryButtonClassName } from './AdminDialogShell'

type ImportPreview = {
  preview_token: string
  provided_fields: string[]
  summary: { total: number; creates: number; updates: number; errors: number; deactivates: number }
  missing_students: Array<{
    student_id: number
    student_no: string
    name: string
    class_name: string
  }>
  rows: Array<{
    row_number: number
    student_no: string
    name: string
    class_name: string
    gender: string | null
    birth_date: string | null
    action: 'create' | 'update' | 'error'
    errors: string[]
  }>
}

export function StudentImportDialog({ onClose, onImported }: { onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [removeMissing, setRemoveMissing] = useState(false)

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
    setPreview(null)
    try {
      const response = await studentApi.previewImport(file, removeMissing)
      setPreview(response.data?.data || null)
    } finally {
      setLoading(false)
    }
  }

  const applyImport = async () => {
    if (!file || !preview || preview.summary.errors > 0) return
    setLoading(true)
    try {
      const response = await studentApi.applyImport(file, removeMissing, preview.preview_token)
      const result = response.data?.data || {}
      toast.success(`导入完成：新增 ${result.imported || 0} 人，更新 ${result.updated || 0} 人，停用 ${result.deactivated || 0} 人`)
      onImported()
    } catch {
      setPreview(null)
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
            <p className="mt-1 text-xs leading-5 text-muted-foreground">模板要求学校、校区、学段、年级和班级名称完全一致。已有学号会更新姓名、班级及文件中提供的性别等信息。</p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">性别、出生年月日、年龄可选填。出生年月日优先用于按报告日期计算年龄；未提供时使用年龄或年级。文件中有对应列时，空白单元格会清除原值；缺少整列时保留原值。</p>
          </div>
          <button type="button" className={`${secondaryButtonClassName} w-full gap-2`} onClick={downloadTemplate}><Download className="h-4 w-4" />下载模板</button>
          <label className="block cursor-pointer rounded-lg border border-dashed border-border bg-background p-4 text-center transition hover:border-primary/40">
            <Upload className="mx-auto h-5 w-5 text-muted-foreground" />
            <span className="mt-2 block truncate text-sm">{file?.name || '选择 CSV 或 Excel 文件'}</span>
            <input type="file" className="hidden" accept=".csv,.xls,.xlsx" disabled={loading} onChange={event => { setFile(event.target.files?.[0] || null); setPreview(null) }} />
          </label>
          <label className="flex items-start gap-2 rounded-lg border border-border bg-background p-3 text-sm">
            <input type="checkbox" className="mt-1 accent-primary" checked={removeMissing} disabled={loading} onChange={event => { setRemoveMissing(event.target.checked); setPreview(null) }} />
            <span>
              停用本次名单涉及班级中未列出的学生
              <span className="mt-1 block text-xs leading-5 text-muted-foreground">勾选时请提供相关班级的完整名单。停用后保留历史记录，可在“已停用”中恢复。</span>
            </span>
          </label>
          <button type="button" className={`${primaryButtonClassName} w-full`} disabled={!file || loading} onClick={runPreview}>{loading ? '校验中…' : '校验并预览'}</button>
        </aside>

        <div className="min-w-0">
          {!preview ? (
            <div className="flex min-h-72 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">选择文件后先校验，确认无误再写入</div>
          ) : (
            <>
              <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
                {[
                  ['总行数', preview.summary.total],
                  ['新增', preview.summary.creates],
                  ['更新', preview.summary.updates],
                  ['待停用', preview.summary.deactivates],
                  ['错误', preview.summary.errors],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md border border-border px-3 py-2"><div className="text-xs text-muted-foreground">{label}</div><div className="mt-1 text-lg font-semibold tabular-nums">{value}</div></div>
                ))}
              </div>
              {removeMissing && preview.summary.errors === 0 ? (
                <div className="mb-3 rounded-lg border border-border bg-secondary/25 p-3 text-xs leading-5 text-muted-foreground">
                  本次将停用 {preview.summary.deactivates} 名名单外学生，仅涉及文件中列出的班级。
                </div>
              ) : null}
              <div className="max-h-[50vh] overflow-auto rounded-lg border border-border">
                <table className="data-table min-w-[860px]">
                  <thead><tr><th>行</th><th>学号</th><th>姓名</th><th>性别</th><th>出生年月日</th><th>班级</th><th>结果</th></tr></thead>
                  <tbody>
                    {preview.rows.map(row => (
                      <tr key={row.row_number}>
                        <td>{row.row_number}</td><td className="font-mono text-xs">{row.student_no || '—'}</td><td>{row.name || '—'}</td><td>{preview.provided_fields.includes('gender') ? row.gender || '—' : row.action === 'update' ? '保留原值' : '—'}</td><td>{preview.provided_fields.includes('birth_date') ? row.birth_date || '—' : row.action === 'update' ? '保留原值' : '—'}</td><td>{row.class_name || '—'}</td>
                        <td className={row.action === 'error' ? 'text-destructive' : ''}>{row.action === 'create' ? '新增' : row.action === 'update' ? '更新' : row.errors.join('；')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {preview.missing_students.length > 0 ? (
                <div className="mt-4">
                  <h3 className="mb-2 text-sm font-medium text-destructive">待停用学生（{preview.summary.deactivates} 人）</h3>
                  <div className="max-h-48 overflow-auto rounded-lg border border-border">
                    <table className="data-table w-full">
                      <thead><tr><th>学号</th><th>姓名</th><th>班级</th></tr></thead>
                      <tbody>
                        {preview.missing_students.map(student => (
                          <tr key={student.student_id}><td className="font-mono text-xs">{student.student_no}</td><td>{student.name}</td><td>{student.class_name}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : null}
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
