import { useEffect, useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, Radar } from 'recharts'
import { TrendingUp, AlertTriangle, CheckCircle2, Star, Send, RefreshCw } from 'lucide-react'
import { reportApi, adminApi } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { cn, scoreColor, fmtDate } from '@/lib/utils'
import type { Student, PersonalReportContent, Report } from '@/types'
import toast from 'react-hot-toast'

const NUTRIENT_LABELS: Record<string, string> = {
  calories: '热量(kcal)', protein: '蛋白质(g)', fat: '脂肪(g)',
  carbohydrate: '碳水(g)', sodium: '钠(mg)', fiber: '膳食纤维(g)',
}

function scoreBadgeClass(score: number): string {
  if (score >= 90) return 'bg-health-green/10 text-health-green ring-health-green/20'
  if (score >= 75) return 'bg-health-blue/10 text-health-blue ring-health-blue/20'
  if (score >= 60) return 'bg-health-amber/10 text-health-amber ring-health-amber/20'
  return 'bg-health-red/10 text-health-red ring-health-red/20'
}

function buildStudentFromLatestReport(studentId: number, report: Report | null): Student | null {
  const content = report?.content as PersonalReportContent | null | undefined
  if (!content) return null

  return {
    id: studentId,
    student_no: String(studentId),
    name: content.student_name,
    class_id: content.class_name || `student-${studentId}`,
    class_name: content.class_name,
    is_active: true,
    latest_report: {
      report_id: report?.id || 0,
      overall_score: content.overall_score,
      alert_count: content.alerts.length,
      period_start: report?.period_start,
      period_end: report?.period_end,
      summary: report?.summary,
      created_at: report?.created_at,
    },
  }
}

export default function ReportsPage() {
  const { user, hasRole } = useAuth()
  const [students, setStudents] = useState<Student[]>([])
  const [selectedStudent, setSelectedStudent] = useState<Student | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [content, setContent] = useState<PersonalReportContent | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedGradeId, setSelectedGradeId] = useState('')
  const [selectedClassId, setSelectedClassId] = useState('')

  const loadReport = async (student: Student) => {
    setSelectedStudent(student)
    setLoading(true)
    try {
      const res = await reportApi.studentLatest(student.id)
      const r: Report | null = res.data.data
      setReport(r)
      setContent(r?.content as PersonalReportContent | null)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    let cancelled = false

    async function loadStudents() {
      if (hasRole('admin', 'teacher', 'grade_leader')) {
        const res = await adminApi.students({ page_size: 200, include_latest_report: true })
        if (!cancelled) setStudents(res.data.data.items)
        return
      }

      if (hasRole('parent') && user?.student_ids?.length) {
        const results = await Promise.allSettled(
          user.student_ids.map(async studentId => {
            const res = await reportApi.studentLatest(studentId)
            return buildStudentFromLatestReport(studentId, res.data.data as Report | null)
          })
        )
        const myStudents = results
          .flatMap(result => result.status === 'fulfilled' && result.value ? [result.value] : [])
          .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

        if (!cancelled) {
          setStudents(myStudents)
          if (myStudents.length > 0) void loadReport(myStudents[0])
        }
        return
      }

      if (!cancelled) setStudents([])
    }

    void loadStudents()
    return () => { cancelled = true }
  }, [hasRole, user?.student_ids])

  const generateReport = async () => {
    setGenerating(true)
    try {
      await reportApi.generate('personal_weekly')
      toast.success('报告生成任务已提交，请稍后刷新')
    } finally { setGenerating(false) }
  }

  const pushReport = async () => {
    if (!report) return
    await reportApi.push(report.id)
    toast.success('推送任务已提交')
  }

  const gradeOptions = Array.from(new Map(
    students
      .filter(student => student.grade_id)
      .map(student => [student.grade_id as string, student.grade_name || student.grade_id || ''])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

  const classOptions = Array.from(new Map(
    students
      .filter(student => (!selectedGradeId || student.grade_id === selectedGradeId) && student.class_id)
      .map(student => [student.class_id, student.class_name || student.class_id])
  ).entries())
    .map(([id, name]) => ({ id, name }))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))

  const normalizedSearch = search.trim().toLowerCase()
  const filteredStudents = students.filter(student => {
    if (selectedGradeId && student.grade_id !== selectedGradeId) return false
    if (selectedClassId && student.class_id !== selectedClassId) return false
    if (!normalizedSearch) return true

    return [
      student.name,
      student.student_no,
      student.class_name,
      student.grade_name,
    ].some(value => String(value || '').toLowerCase().includes(normalizedSearch))
  })

  // Build chart data
  const nutrientChartData = content ? Object.entries(content.avg_nutrients).map(([key, avg]) => ({
    name: NUTRIENT_LABELS[key]?.split('(')[0] || key,
    avg: Math.round(avg),
    rec: Math.round((content.recommended_nutrients as any)[key] || 0),
    pct: Math.round((avg / ((content.recommended_nutrients as any)[key] || 1)) * 100),
  })) : []

  const radarData = nutrientChartData.map(d => ({
    subject: d.name,
    value: Math.min(150, d.pct),
    fullMark: 150,
  }))

  const score = content?.overall_score ?? 0

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">营养报告</h1>
          <p className="text-sm text-muted-foreground mt-0.5">学生个人营养摄入分析</p>
        </div>
        <div className="flex gap-2">
          {hasRole('admin') && (
            <button onClick={generateReport} disabled={generating} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors disabled:opacity-50">
              <RefreshCw className={cn('w-3.5 h-3.5', generating && 'animate-spin')} />生成报告
            </button>
          )}
          {report && (
            <button onClick={pushReport} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
              <Send className="w-3.5 h-3.5" />推送报告
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        {/* Student list */}
        <div className="lg:col-span-1">
          <div className="bg-card border border-border rounded-xl overflow-hidden sticky top-4">
            <div className="p-3 border-b border-border space-y-3">
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索学生..."
                className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20" />
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={selectedGradeId}
                  onChange={e => {
                    setSelectedGradeId(e.target.value)
                    setSelectedClassId('')
                  }}
                  className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                >
                  <option value="">全部年级</option>
                  {gradeOptions.map(option => (
                    <option key={option.id} value={option.id}>{option.name}</option>
                  ))}
                </select>
                <select
                  value={selectedClassId}
                  onChange={e => setSelectedClassId(e.target.value)}
                  className="w-full px-3 py-1.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20 disabled:opacity-50"
                  disabled={classOptions.length === 0}
                >
                  <option value="">全部班级</option>
                  {classOptions.map(option => (
                    <option key={option.id} value={option.id}>{option.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>当前范围 {filteredStudents.length} 人</span>
                <span>右侧分值取最新周报</span>
              </div>
            </div>
            <div className="overflow-y-auto max-h-[30rem] sm:max-h-[34rem] lg:max-h-[calc(100vh-10rem)]">
              {filteredStudents.length === 0 ? (
                <div className="p-4 text-center text-xs text-muted-foreground">暂无学生</div>
              ) : filteredStudents.map(s => (
                <button key={s.id} onClick={() => loadReport(s)}
                  className={cn('w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-secondary transition-colors border-b border-border/50 last:border-0',
                    selectedStudent?.id === s.id && 'bg-secondary/90'
                  )}>
                  <div className="w-8 h-8 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium flex-shrink-0">
                    {s.name[0]}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <div className="text-sm font-medium truncate">{s.name}</div>
                      {!!s.latest_report?.alert_count && (
                        <span className="rounded-full bg-health-amber/10 px-1.5 py-0.5 text-[10px] text-health-amber">
                          预警 {s.latest_report.alert_count}
                        </span>
                      )}
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {[s.grade_name, s.class_name].filter(Boolean).join(' · ') || '未分班'}
                    </div>
                    <div className="text-[10px] text-muted-foreground font-mono">{s.student_no}</div>
                  </div>
                  <div className="shrink-0 text-right">
                    {typeof s.latest_report?.overall_score === 'number' ? (
                      <>
                        <div className={cn(
                          'inline-flex min-w-[2.75rem] items-center justify-center rounded-full px-2 py-1 text-xs font-mono ring-1',
                          scoreBadgeClass(s.latest_report.overall_score)
                        )}>
                          {s.latest_report.overall_score}
                        </div>
                        <div className="mt-1 text-[10px] text-muted-foreground font-mono">
                          {fmtDate(s.latest_report.period_end)}
                        </div>
                      </>
                    ) : (
                      <div className="text-[10px] text-muted-foreground">待生成</div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Report content */}
        <div className="lg:col-span-3 space-y-4">
          {!selectedStudent ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center">
              <TrendingUp className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
              <p className="text-sm text-muted-foreground">请从左侧选择学生查看报告</p>
            </div>
          ) : loading ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center text-muted-foreground text-sm">加载中...</div>
          ) : !content ? (
            <div className="bg-card border border-border rounded-xl p-12 text-center">
              <p className="text-sm text-muted-foreground">该学生暂无营养报告，请先生成报告</p>
            </div>
          ) : (
            <>
              {/* Score card */}
              <div className="bg-card border border-border rounded-xl p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <h2 className="text-lg font-semibold">{content.student_name}</h2>
                    <p className="text-sm text-muted-foreground">{content.class_name} · {fmtDate(content.period_start)} — {fmtDate(content.period_end)}</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      就餐 <span className="font-mono font-medium text-foreground">{content.meal_days}</span> / {content.total_days} 天
                    </p>
                  </div>
                  <div className="text-center">
                    <div className={cn('text-4xl font-mono font-light', scoreColor(score))}>{score}</div>
                    <div className="text-xs text-muted-foreground mt-1">综合评分</div>
                  </div>
                </div>

                {/* Alerts */}
                {content.alerts.length > 0 && (
                  <div className="mt-4 space-y-1.5">
                    {content.alerts.map((alert, i) => (
                      <div key={i} className={cn('flex items-start gap-2 p-2.5 rounded-lg text-xs',
                        alert.type === 'excess' ? 'bg-health-red/5 border border-health-red/20' : 'bg-health-amber/5 border border-health-amber/20'
                      )}>
                        <AlertTriangle className={cn('w-3.5 h-3.5 mt-0.5 flex-shrink-0', alert.type === 'excess' ? 'text-health-red' : 'text-health-amber')} />
                        <span>{alert.message}</span>
                      </div>
                    ))}
                  </div>
                )}

                {content.alerts.length === 0 && (
                  <div className="mt-4 flex items-center gap-2 text-xs text-health-green">
                    <CheckCircle2 className="w-3.5 h-3.5" />营养摄入均衡，继续保持
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* Nutrient bar chart */}
                <div className="bg-card border border-border rounded-xl p-5">
                  <h3 className="text-sm font-medium mb-4">营养素摄入对比</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={nutrientChartData} layout="vertical" margin={{ left: 12, right: 12 }}>
                      <XAxis type="number" tick={{ fontSize: 10, fontFamily: 'DM Mono' }} />
                      <YAxis dataKey="name" type="category" width={60} tick={{ fontSize: 10 }} />
                      <Tooltip
                        contentStyle={{ fontSize: 11, fontFamily: 'DM Sans', border: '1px solid #e2e2dc', borderRadius: 6 }}
                        formatter={(v: any, name: string) => [v, name === 'avg' ? '实际' : '推荐']}
                      />
                      <Bar dataKey="avg" name="实际" fill="#16a34a" radius={[0, 2, 2, 0]} />
                      <Bar dataKey="rec" name="推荐" fill="#e2e2dc" radius={[0, 2, 2, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Radar chart */}
                <div className="bg-card border border-border rounded-xl p-5">
                  <h3 className="text-sm font-medium mb-4">营养均衡雷达图</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <RadarChart data={radarData}>
                      <PolarGrid stroke="#e2e2dc" />
                      <PolarAngleAxis dataKey="subject" tick={{ fontSize: 10 }} />
                      <Radar name="摄入比例%" dataKey="value" stroke="#16a34a" fill="#16a34a" fillOpacity={0.15} strokeWidth={1.5} />
                      <Tooltip contentStyle={{ fontSize: 11, fontFamily: 'DM Sans', border: '1px solid #e2e2dc', borderRadius: 6 }}
                        formatter={(v: any) => [`${v}%`]} />
                    </RadarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Top dishes & Suggestions */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="bg-card border border-border rounded-xl p-5">
                  <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                    <Star className="w-3.5 h-3.5 text-health-amber" />最常食用
                  </h3>
                  {content.top_dishes.length === 0 ? (
                    <p className="text-xs text-muted-foreground">暂无数据</p>
                  ) : (
                    <div className="space-y-2">
                      {content.top_dishes.map((d, i) => (
                        <div key={i} className="flex items-center gap-2">
                          <span className="text-xs font-mono text-muted-foreground w-4">{i + 1}</span>
                          <div className="flex-1">
                            <div className="flex items-center justify-between">
                              <span className="text-xs font-medium">{d.name}</span>
                              <span className="text-xs font-mono text-muted-foreground">{d.count}次</span>
                            </div>
                            <div className="h-1 bg-secondary rounded-full mt-1 overflow-hidden">
                              <div className="h-full bg-foreground/30 rounded-full"
                                style={{ width: `${(d.count / (content.top_dishes[0]?.count || 1)) * 100}%` }} />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="bg-card border border-border rounded-xl p-5">
                  <h3 className="text-sm font-medium mb-3">饮食建议</h3>
                  <ul className="space-y-2">
                    {content.suggestions.map((s, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
                        <span className="w-1 h-1 rounded-full bg-health-green mt-1.5 flex-shrink-0" />
                        {s}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
