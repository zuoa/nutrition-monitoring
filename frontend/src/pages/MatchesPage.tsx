import { useEffect, useState } from 'react'
import { RefreshCw, CheckCircle2, ChevronLeft, ChevronRight, RotateCcw } from 'lucide-react'
import { consumptionApi } from '@/api/client'
import { fmtDateTime, cn } from '@/lib/utils'
import type { MatchResult } from '@/types'
import toast from 'react-hot-toast'

const STATUS_STYLES: Record<string, { label: string; class: string }> = {
  matched: { label: '已匹配', class: 'text-health-green bg-health-green/10' },
  time_matched_only: { label: '待确认', class: 'text-health-amber bg-health-amber/10' },
  unmatched_image: { label: '无消费', class: 'text-muted-foreground bg-secondary' },
  unmatched_record: { label: '无图片', class: 'text-muted-foreground bg-secondary' },
  confirmed: { label: '已确认', class: 'text-health-green bg-health-green/10' },
}

export default function MatchesPage() {
  const [matches, setMatches] = useState<MatchResult[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [confirmingId, setConfirmingId] = useState<number | null>(null)
  const [dateFilter, setDateFilter] = useState(new Date().toISOString().split('T')[0])

  const PAGE_SIZE = 20

  const load = async () => {
    setLoading(true)
    try {
      const res = await consumptionApi.matches({ page, page_size: PAGE_SIZE, status: status || undefined, date: dateFilter })
      setMatches(res.data.data.items)
      setTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [page, status, dateFilter])

  const confirm = async (id: number) => {
    setConfirmingId(id)
    try {
      await consumptionApi.confirmMatch(id)
      toast.success('已手动确认匹配')
      load()
    } finally { setConfirmingId(null) }
  }

  const rematch = async () => {
    await consumptionApi.rematch(dateFilter)
    toast.success('重新匹配任务已提交')
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  // Summary counts
  const statusCounts = matches.reduce<Record<string, number>>((acc, m) => {
    acc[m.status] = (acc[m.status] || 0) + 1
    return acc
  }, {})

  return (
    <div className="p-6 max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-display">匹配管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">消费记录与图像关联</p>
        </div>
        <div className="flex items-center gap-2">
          <input type="date" value={dateFilter} onChange={e => { setDateFilter(e.target.value); setPage(1) }}
            className="px-3 py-2 text-sm bg-card border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20" />
          <button onClick={rematch} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RotateCcw className="w-3.5 h-3.5" />重新匹配
          </button>
          <button onClick={load} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
          </button>
        </div>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-5 gap-3 mb-5">
        {[
          { key: '', label: '全部', count: total },
          { key: 'matched', label: '已匹配', count: undefined },
          { key: 'time_matched_only', label: '待确认', count: undefined },
          { key: 'unmatched_image', label: '无消费', count: undefined },
          { key: 'unmatched_record', label: '无图片', count: undefined },
        ].map(({ key, label, count }) => (
          <button key={key} onClick={() => { setStatus(key); setPage(1) }}
            className={cn('p-3 rounded-xl border text-left transition-all', status === key ? 'border-foreground bg-foreground/5' : 'border-border bg-card hover:border-foreground/20')}>
            <div className={cn('text-xs font-medium mb-0.5', STATUS_STYLES[key]?.class?.split(' ')[0] || 'text-foreground')}>{label}</div>
            <div className="text-lg font-mono">{count ?? statusCounts[key] ?? '—'}</div>
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="data-table">
          <thead>
            <tr>
              <th>状态</th>
              <th>学生</th>
              <th>消费时间</th>
              <th>金额</th>
              <th>时间偏差</th>
              <th>金额偏差</th>
              <th>方式</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={8} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
            {!loading && matches.length === 0 && <tr><td colSpan={8} className="text-center py-12 text-muted-foreground">暂无匹配记录</td></tr>}
            {matches.map(m => {
              const s = STATUS_STYLES[m.status] || { label: m.status, class: 'text-muted-foreground bg-secondary' }
              const rec = m.consumption_record
              return (
                <tr key={m.id}>
                  <td>
                    <span className={cn('text-xs px-2 py-0.5 rounded-full font-medium', s.class)}>{s.label}</span>
                  </td>
                  <td>
                    <div className="text-sm font-medium">{m.student?.name ?? '—'}</div>
                    <div className="text-xs text-muted-foreground font-mono">{m.student?.student_no}</div>
                  </td>
                  <td><span className="text-xs font-mono">{fmtDateTime(rec?.transaction_time)}</span></td>
                  <td><span className="font-mono">¥{rec?.amount?.toFixed(2) ?? '—'}</span></td>
                  <td>
                    <span className={cn('text-xs font-mono', (m.time_diff_seconds ?? 99) > 1 ? 'text-health-amber' : 'text-muted-foreground')}>
                      {m.time_diff_seconds != null ? `${m.time_diff_seconds.toFixed(1)}s` : '—'}
                    </span>
                  </td>
                  <td>
                    <span className={cn('text-xs font-mono', (m.price_diff ?? 99) > 0.5 ? 'text-health-amber' : 'text-muted-foreground')}>
                      {m.price_diff != null ? `¥${m.price_diff.toFixed(2)}` : '—'}
                    </span>
                  </td>
                  <td><span className="text-xs text-muted-foreground">{m.is_manual ? '手动' : '自动'}</span></td>
                  <td>
                    {m.status === 'time_matched_only' && (
                      <button
                        onClick={() => confirm(m.id)}
                        disabled={confirmingId === m.id}
                        className="flex items-center gap-1 text-xs text-health-blue hover:underline disabled:opacity-50"
                      >
                        <CheckCircle2 className="w-3 h-3" />确认
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <span className="text-xs text-muted-foreground">共 {total} 条</span>
          <div className="flex items-center gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
            <span className="text-xs font-mono px-2">{page} / {totalPages}</span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40 transition-colors"><ChevronRight className="w-4 h-4" /></button>
          </div>
        </div>
      )}
    </div>
  )
}
