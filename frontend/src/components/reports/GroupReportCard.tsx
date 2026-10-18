import { useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { NUTRITION_FIELDS } from '@/lib/nutrition'
import type { GroupReportContent, NutrientData, Report } from '@/types'
import './report.css'

const COLORS = ['#126448', '#417CB2', '#D69A28', '#78988A']
const TREND_FIELDS = [
  { key: 'calories', label: '能量', unit: 'kcal', color: COLORS[0] },
  { key: 'protein', label: '蛋白质', unit: 'g', color: COLORS[1] },
  { key: 'calcium', label: '钙', unit: 'mg', color: COLORS[2] },
] as const
const chartMargin = { top: 12, right: 16, bottom: 0, left: 0 }
const axisStyle = { fontSize: 11, fill: '#667D72' }
const tooltipStyle = { border: '1px solid #DFE8E2', borderRadius: 8, fontSize: 12 }
const tooltipValue = (value: number | string) => typeof value === 'number' ? value.toLocaleString('zh-CN', { maximumFractionDigits: 1 }) : value

function EmptyChart({ children }: { children: React.ReactNode }) {
  return <p className="nutrition-empty">{children}</p>
}

export default function GroupReportCard({ content }: { content: GroupReportContent; report: Report | null }) {
  const [cohortPage, setCohortPage] = useState(0)
  const [nutrient, setNutrient] = useState<keyof NutrientData>('protein')
  const scopeLabel = { class: '班级', grade: '年级', campus: '校区' }[content.scope_type]
  const daily = (content.daily_series || []).map(day => ({ date: day.date.slice(5), ...day.nutrients, samples: day.sample_counts }))
  const slots = [...(content.meal_slots || []), { key: 'unknown', label: '未归类' }]
  const meals = slots.map(slot => ({ name: slot.label, count: content.meal_distribution?.[slot.key] || 0 }))
  const coverage = NUTRITION_FIELDS.map(field => ({
    name: field.label, count: content.nutrient_sample_counts?.[field.key as keyof NutrientData] || 0,
  }))
  const cohorts = content.cohorts || []
  const pageCount = Math.max(1, Math.ceil(cohorts.length / 8))
  const safePage = Math.min(cohortPage, pageCount - 1)
  const selected = NUTRITION_FIELDS.find(field => field.key === nutrient)!
  const cohortData = cohorts.slice(safePage * 8, safePage * 8 + 8).map(cohort => ({
    name: cohort.name, value: cohort.nutrients[nutrient] ?? null,
    sample: cohort.sample_counts[nutrient] || 0, meal_count: cohort.meal_count,
  }))
  const refs = content.references || []

  return <article className="nutrition-report">
    <header className="nutrition-header">
      <div className="nutrition-eyebrow">CAMPUS NUTRITION <span>GROUP / WEEKLY</span></div>
      <h2>{scopeLabel}在校膳食<br />营养记录报告</h2>
      <p>看见群体的饮食记录，让下一餐更有依据。</p>
      <div className="nutrition-meta"><strong>{content.scope_name}</strong><span>{content.period_start} — {content.period_end}</span><span>群体统计 · 不含个人明细</span></div>
    </header>
    {content.report_version !== 2 ? <p className="nutrition-notice">历史口径报告。请重新生成以查看新版记录、趋势和参考依据；旧评分不作为个人全天摄入评价。</p> : null}
    <div className="nutrition-stats">
      {[
        { label: '范围人数', value: content.student_count, unit: '人' },
        { label: '有营养记录', value: content.students_with_data, unit: '人' },
        { label: '人数覆盖率', value: content.data_coverage_rate, unit: '%' },
        { label: '消费记录餐次', value: content.meal_count ?? '—', unit: '次' },
      ].map(item => <div key={item.label}><span>{item.label}</span><strong>{item.value}<small>{item.unit}</small></strong></div>)}
    </div>
    <div className="nutrition-chart-pair">
      <section>
        <h3>记录覆盖到多少人</h3><p className="nutrition-caption">营养记录人数 / 范围人数 · 数据覆盖程度</p>
        <div className="nutrition-coverage"><span style={{ width: `${content.data_coverage_rate}%` }} /></div>
        <div className="nutrition-legend"><span><i />有数据 {content.students_with_data} 人</span><span><i className="gray" />无数据 {content.student_count - content.students_with_data} 人</span></div>
        <p className="nutrition-caption">无记录不等于未用餐，也不代表营养不足。</p>
      </section>
      <section><h3>餐次记录分布</h3><p className="nutrition-caption">消费记录按学生、日期、餐段去重；未归类单列</p>
        {meals.some(item => item.count > 0) ? <div className="nutrition-chart" role="img" aria-label={meals.map(item => `${item.name}${item.count}次`).join('，')}>
          <ResponsiveContainer width="100%" height="100%"><BarChart data={meals} margin={chartMargin} accessibilityLayer>
            <CartesianGrid stroke="#E5ECE7" vertical={false} /><XAxis dataKey="name" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis allowDecimals={false} tick={axisStyle} width={36} axisLine={false} tickLine={false} /><Tooltip formatter={tooltipValue} contentStyle={tooltipStyle} />
            <Bar dataKey="count" name="消费记录（次）" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false}>
              {meals.map((item, i) => <Cell key={item.name} fill={COLORS[i % COLORS.length]} />)}
            </Bar>
          </BarChart></ResponsiveContainer>
        </div> : <EmptyChart>暂无可归类的消费记录</EmptyChart>}
      </section>
    </div>
    <section className="nutrition-section"><h3>群体营养，随记录变化</h3><p className="nutrition-caption">每日有数据学生的营养估算均值；缺失日期断开显示，不代表全天摄入。</p>
      <div className="nutrition-trends">{TREND_FIELDS.map(field => <div className="nutrition-trend" key={field.key}>
        <h4>{field.label}<span>{field.unit} / 有记录日</span></h4>
        {daily.some(day => day[field.key] != null) ? <div className="nutrition-chart" role="img" aria-label={`${field.label}群体日趋势，单位${field.unit}`}>
          <ResponsiveContainer width="100%" height="100%"><LineChart data={daily} margin={chartMargin} accessibilityLayer>
            <CartesianGrid stroke="#DFE8E2" vertical={false} /><XAxis dataKey="date" minTickGap={24} tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis tick={axisStyle} width={42} axisLine={false} tickLine={false} domain={[0, 'auto']} />
            <Tooltip contentStyle={tooltipStyle} formatter={tooltipValue} labelFormatter={(label, payload) => `${label} · ${payload[0]?.payload?.samples?.[field.key] || 0} 人有效样本`} />
            <Line type="linear" dataKey={field.key} name={`${field.label}（${field.unit}）`} stroke={field.color} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />
          </LineChart></ResponsiveContainer>
        </div> : <EmptyChart>暂无有效{field.label}记录</EmptyChart>}
      </div>)}</div>
    </section>
    <div className="nutrition-chart-pair nutrition-section">
      <section><h3>各项营养数据覆盖</h3><p className="nutrition-caption">每项有效样本人数；同一学生在每项中只计一次</p>
        {content.students_with_data > 0 ? <div style={{ height: 430 }} role="img" aria-label="各营养指标有效样本人数条形图">
          <ResponsiveContainer width="100%" height="100%"><BarChart layout="vertical" data={coverage} margin={{ top: 8, right: 20, left: 0, bottom: 0 }} accessibilityLayer>
            <CartesianGrid stroke="#E5ECE7" horizontal={false} /><XAxis type="number" domain={[0, content.student_count || 1]} tick={axisStyle} allowDecimals={false} axisLine={false} tickLine={false} />
            <YAxis dataKey="name" type="category" tick={axisStyle} width={85} axisLine={false} tickLine={false} />
            <Tooltip formatter={tooltipValue} contentStyle={tooltipStyle} /><Bar dataKey="count" name="有效样本（人）" fill={COLORS[0]} radius={[0, 3, 3, 0]} maxBarSize={10} isAnimationActive={false} />
          </BarChart></ResponsiveContainer>
        </div> : <EmptyChart>暂无有效营养数据</EmptyChart>}
      </section>
      <section><h3>完整营养估算</h3><p className="nutrition-caption">先按学生计算周期日均，再按学生等权汇总</p>
        <table className="nutrition-table"><thead><tr><th>营养指标</th><th>日均估算</th><th>样本人数</th></tr></thead><tbody>{NUTRITION_FIELDS.map(field => {
          const key = field.key as keyof NutrientData
          const value = content.avg_nutrients[key]
          const sample = content.nutrient_sample_counts?.[key] || 0
          return <tr key={key}><td>{field.label}<small>{field.unit}</small></td><td>{sample && value != null ? tooltipValue(value) : '—'}</td><td>{sample} 人</td></tr>
        })}</tbody></table>
      </section>
    </div>
    {cohorts.length > 1 ? <section className="nutrition-section">
      <div className="nutrition-section-head"><div><h3>按班级查看记录分布</h3><p className="nutrition-caption">按组织顺序展示，不作健康排名；不同餐次覆盖的数值应结合记录范围阅读。</p></div>
        <select aria-label="选择班级分布营养指标" value={nutrient} onChange={event => setNutrient(event.target.value as keyof NutrientData)}>
          {NUTRITION_FIELDS.map(field => <option key={field.key} value={field.key}>{field.label}（{field.unit}）</option>)}
        </select>
      </div>
      <div style={{ height: Math.max(180, cohortData.length * 42) }} role="img" aria-label={`各班级${selected.label}日均估算，单位${selected.unit}`}>
        <ResponsiveContainer width="100%" height="100%"><BarChart data={cohortData} layout="vertical" margin={{ top: 8, right: 30, bottom: 0, left: 0 }} accessibilityLayer>
          <CartesianGrid stroke="#E5ECE7" horizontal={false} /><XAxis type="number" tick={axisStyle} unit={` ${selected.unit}`} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="name" width={100} tick={axisStyle} axisLine={false} tickLine={false} />
          <Tooltip formatter={tooltipValue} contentStyle={tooltipStyle} labelFormatter={(label, payload) => `${label} · ${payload[0]?.payload?.sample || 0} 人有效样本`} />
          <Bar dataKey="value" name={`${selected.label}（${selected.unit}）`} fill={COLORS[1]} maxBarSize={16} radius={[0, 3, 3, 0]} isAnimationActive={false} />
        </BarChart></ResponsiveContainer>
      </div>
      <table className="nutrition-table"><thead><tr><th>班级</th><th>有效样本</th><th>早 / 午 / 晚 / 其他餐次</th></tr></thead><tbody>{cohorts.slice(safePage * 8, safePage * 8 + 8).map(cohort => <tr key={cohort.name}>
        <td>{cohort.name}</td><td>{cohort.sample_counts[nutrient] || 0} / {cohort.student_count} 人</td><td>{['breakfast', 'lunch', 'dinner'].map(slot => cohort.meal_distribution[slot] || 0).join(' / ')} / {Object.entries(cohort.meal_distribution).filter(([slot]) => !['breakfast', 'lunch', 'dinner'].includes(slot)).reduce((sum, [, count]) => sum + count, 0)}</td>
      </tr>)}</tbody></table>
      {pageCount > 1 ? <div className="nutrition-pager"><button disabled={!safePage} onClick={() => setCohortPage(safePage - 1)}>上一组</button><span>{safePage + 1} / {pageCount}</span><button disabled={safePage + 1 >= pageCount} onClick={() => setCohortPage(safePage + 1)}>下一组</button></div> : null}
    </section> : null}
    {content.reference_table?.length ? <section className="nutrition-section nutrition-reference"><h3>不同年龄的全天供给参考</h3><p className="nutrition-caption">WS/T 554—2017 表1 · 独立参考，不与未知年龄学生的在校记录判断达标。</p>
      <div className="nutrition-trends">{TREND_FIELDS.map(field => {
        const rows = content.reference_table!.filter(row => row.sex === '男').map(row => ({
          name: row.age_group, 男生: row[field.key], 女生: content.reference_table!.find(other => other.age_group === row.age_group && other.sex === '女')![field.key],
        }))
        return <div key={field.key} className="nutrition-trend"><h4>{field.label}<span>{field.unit} / 天</span></h4><div className="nutrition-chart" role="img" aria-label={`各年龄组${field.label}全天供给参考`}>
          <ResponsiveContainer width="100%" height="100%"><BarChart data={rows} margin={chartMargin} accessibilityLayer>
            <CartesianGrid stroke="#DFE8E2" vertical={false} /><XAxis dataKey="name" tick={{ ...axisStyle, fontSize: 9 }} interval={0} axisLine={false} tickLine={false} />
            <YAxis tick={axisStyle} width={40} axisLine={false} tickLine={false} /><Tooltip formatter={tooltipValue} contentStyle={tooltipStyle} />
            <Bar dataKey="男生" fill={COLORS[1]} radius={[3, 3, 0, 0]} isAnimationActive={false} /><Bar dataKey="女生" fill={COLORS[2]} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          </BarChart></ResponsiveContainer>
        </div></div>
      })}</div><div className="nutrition-legend"><span><i className="blue" />男生</span><span><i className="gold" />女生</span></div>
    </section> : null}
    <section className="nutrition-section"><h3>下一餐的行动建议</h3><div className="nutrition-advice">{content.suggestions.map(suggestion => <p key={suggestion}>{suggestion}</p>)}</div><p className="nutrition-notice">{content.limitations || '仅反映已记录膳食，不代表全天实际摄入。'}</p></section>
    <footer className="nutrition-sources"><h4>依据与阅读说明</h4>{refs.map(ref => <p key={ref.id}><span>{ref.kind}</span> · <a href={ref.url} target="_blank" rel="noopener noreferrer">{ref.title}</a><br />{ref.publisher} · {ref.version} · {ref.use}</p>)}<p>数值核验 {content.reference_checked_at || '—'} · 各图注明单位与有效样本，数据覆盖不表示营养达标。</p></footer>
  </article>
}
