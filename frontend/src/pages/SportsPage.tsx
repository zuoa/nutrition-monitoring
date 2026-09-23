import { Fragment, useEffect, useState } from 'react'
import { sportsApi, type SportRecord, type SportRecordDetail, type SportsMeta } from '@/api/client'
import { DataPagination } from '@/components/ui/DataPagination'
import { useUrlPage } from '@/hooks/useUrlPage'
import { fmtDateTime } from '@/lib/utils'

const METRICS: Record<string, string> = { all_time: '运动时长（秒）', interrupt_count: '中断次数', jump_speed: '平均速度（m/s）', jump_height: '腾空高度（cm）', jump_angle: '起跳角度', arm_angle: '摆臂振幅', reaction_time: '反应时间（ms）', weight: '体重（kg）', height: '身高（cm）' }
const EMPTY_FILTERS = { student: '', school_id: '', sport_type: '', mode: '', date_from: '', date_to: '' }
const EMPTY_META: SportsMeta = { sports: [], products: [], modes: [] }
const INPUT = 'mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm'

type DetailState = SportRecordDetail | 'loading' | 'failed'

function RecordDetails({ detail }: { detail: DetailState }) {
  if (detail === 'loading') return <div className="p-4 text-sm text-muted-foreground">正在加载详情…</div>
  if (detail === 'failed') return <div className="p-4 text-sm text-destructive">详情加载失败，请收起后重试。</div>
  const raw = detail.raw_result ?? {}
  return <div className="p-4 space-y-4 bg-secondary/30">
    <dl className="grid gap-4 sm:grid-cols-3 text-sm">
      <div><dt className="text-muted-foreground">设备产品</dt><dd>{detail.product_name || detail.product_type}</dd></div>
      <div><dt className="text-muted-foreground">接收时间</dt><dd>{fmtDateTime(detail.received_at)}</dd></div>
      {Object.entries(METRICS).filter(([key]) => raw[key] != null).map(([key, label]) => <div key={key}><dt className="text-muted-foreground">{label}</dt><dd>{String(raw[key])}</dd></div>)}
      <div className="break-all"><dt className="text-muted-foreground">运动视频编号</dt><dd>{detail.video_file_id || '未提供'}</dd></div>
      <div className="break-all"><dt className="text-muted-foreground">抓拍照片编号</dt><dd>{detail.face_file_id || '未提供'}</dd></div>
    </dl>
    <details><summary className="cursor-pointer text-xs text-muted-foreground">完整推送指标</summary><pre className="mt-2 overflow-auto max-h-72 text-xs whitespace-pre-wrap break-all">{JSON.stringify(raw, null, 2)}</pre></details>
  </div>
}

export default function SportsPage() {
  const [draft, setDraft] = useState(EMPTY_FILTERS)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [page, setPage] = useUrlPage()
  const [result, setResult] = useState<{ items: SportRecord[]; total: number; total_pages: number }>({ items: [], total: 0, total_pages: 0 })
  const [meta, setMeta] = useState<SportsMeta>(EMPTY_META)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [details, setDetails] = useState<Record<number, DetailState>>({})

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setFailed(false)
    setExpanded(null)
    setDetails({})
    sportsApi.records({ ...filters, page, page_size: 20 }, controller.signal).then(({ data }) => {
      if (controller.signal.aborted) return
      setResult(data.data)
      // Records may have been pruned since this page was opened; pull the page
      // back inside the real range instead of showing a false "no data" state.
      if (data.data.total_pages > 0 && page > data.data.total_pages) setPage(data.data.total_pages)
    }).catch(() => { if (!controller.signal.aborted) setFailed(true) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [filters, page])

  useEffect(() => {
    sportsApi.meta().then(({ data }) => setMeta(data.data)).catch(() => {})
  }, [])

  const setField = (key: keyof typeof EMPTY_FILTERS, value: string) => setDraft(previous => ({ ...previous, [key]: value }))
  const toggleDetails = (record: SportRecord) => {
    if (expanded === record.id) {
      setExpanded(null)
      return
    }
    setExpanded(record.id)
    if (details[record.id]) return
    setDetails(previous => ({ ...previous, [record.id]: 'loading' }))
    const controller = new AbortController()
    sportsApi.record(record.id, controller.signal).then(({ data }) => {
      setDetails(previous => ({ ...previous, [record.id]: data.data }))
    }).catch(() => {
      if (!controller.signal.aborted) setDetails(previous => ({ ...previous, [record.id]: 'failed' }))
    })
  }

  return <div className="p-4 md:p-6 space-y-5 max-w-screen-2xl mx-auto">
    <div className="flex items-center justify-between gap-4"><div><h1 className="text-xl font-semibold">体育数据</h1><p className="text-sm text-muted-foreground mt-1">查看设备推送的运动成绩与体测指标，按运动时间倒序排列。</p></div><button disabled={loading} onClick={() => setFilters(previous => ({ ...previous }))} className="rounded-lg border border-border px-4 py-2 text-sm disabled:opacity-50">刷新</button></div>
    <form className="rounded-xl border border-border bg-card p-4 space-y-4" onSubmit={event => { event.preventDefault(); setPage(1); setFilters({ ...draft }) }}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="text-xs text-muted-foreground">学生姓名 / 学号<input className={INPUT} value={draft.student} onChange={e => setField('student', e.target.value)} placeholder="输入姓名或学号" /></label>
        <label className="text-xs text-muted-foreground">学校编号<input className={INPUT} value={draft.school_id} onChange={e => setField('school_id', e.target.value)} placeholder="全部学校" /></label>
        <label className="text-xs text-muted-foreground">运动项目<select className={INPUT} value={draft.sport_type} onChange={e => setField('sport_type', e.target.value)}><option value="">全部项目</option>{meta.sports.map(sport => <option key={sport.id} value={sport.id}>{sport.name}</option>)}</select></label>
        <label className="text-xs text-muted-foreground">运动模式<select className={INPUT} value={draft.mode} onChange={e => setField('mode', e.target.value)}><option value="">全部模式</option>{meta.modes.map(mode => <option key={mode.id} value={mode.id}>{mode.name}</option>)}</select></label>
        <label className="text-xs text-muted-foreground">开始日期<input type="date" className={INPUT} value={draft.date_from} max={draft.date_to || undefined} onChange={e => setField('date_from', e.target.value)} /></label>
        <label className="text-xs text-muted-foreground">结束日期<input type="date" className={INPUT} value={draft.date_to} min={draft.date_from || undefined} onChange={e => setField('date_to', e.target.value)} /></label>
      </div>
      <div className="flex gap-3"><button className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm">查询</button><button type="button" className="rounded-lg border border-border px-4 py-2 text-sm" onClick={() => { setDraft(EMPTY_FILTERS); setPage(1); setFilters({ ...EMPTY_FILTERS }) }}>重置</button></div>
    </form>
    <section aria-label="运动成绩" className="rounded-xl border border-border bg-card overflow-hidden">
      {loading ? <p role="status" className="p-12 text-center text-muted-foreground">正在加载体育数据…</p> : failed ? <p role="alert" className="p-12 text-center text-destructive">加载失败，请点击刷新重试。</p> : result.items.length === 0 ? <div className="p-12 text-center"><p>暂无体育数据</p><p className="mt-2 text-sm text-muted-foreground">请调整筛选条件，或在系统设置的数据同步中配置体育推送并等待设备上传。</p></div> : <div className="overflow-x-auto"><table className="w-full text-sm text-left whitespace-nowrap"><thead className="bg-secondary/50 text-xs text-muted-foreground"><tr>{['学生 / 学号', '学校编号', '运动项目', '模式', '成绩', '运动时间', '详情'].map(label => <th className="px-4 py-3 font-medium" key={label}>{label}</th>)}</tr></thead><tbody>
        {result.items.map(record => <Fragment key={record.id}><tr className="border-t border-border">
          <td className="px-4 py-3"><div>{record.student_name || (record.person_id ? '学生未建档' : '未识别人员')}</div><div className="text-xs text-muted-foreground font-mono">{record.person_id || '无学号'}</div></td>
          <td className="px-4 py-3">{record.school_id}</td><td className="px-4 py-3">{record.sport_name || `项目 ${record.sport_type}`}</td><td className="px-4 py-3">{record.mode === 1 ? '练习' : '测试'}</td>
          <td className="px-4 py-3 font-mono font-semibold">{record.score} <span className="text-xs text-muted-foreground font-normal">{record.score_unit || '单位未提供'}</span></td><td className="px-4 py-3">{fmtDateTime(record.start_time)}</td>
          <td className="px-4 py-3"><button aria-expanded={expanded === record.id} aria-label={`查看${record.student_name || record.person_id || '未识别人员'}的运动详情`} className="text-primary underline underline-offset-4" onClick={() => toggleDetails(record)}>{expanded === record.id ? '收起' : '查看'}</button></td>
        </tr>{expanded === record.id ? <tr><td colSpan={7} className="whitespace-normal"><RecordDetails detail={details[record.id] || 'loading'} /></td></tr> : null}</Fragment>)}
      </tbody></table></div>}
      {!failed ? <DataPagination className="p-4 border-t border-border" page={page} totalPages={result.total_pages} totalItems={result.total} onPageChange={setPage} disabled={loading} /> : null}
    </section>
  </div>
}
