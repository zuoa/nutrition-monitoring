import { useState } from 'react'
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { REPORT_NUTRITION_FIELDS } from '@/lib/nutrition'
import type { GroupReportAnalysis, GroupReportContent, NutrientData, Report } from '@/types'
import './report.css'

type Tone = 'good' | 'attention' | 'warning' | 'unknown'
const MACROS = [
  { key: 'carbohydrate', label: '碳水化合物', lower: 50, upper: 65 },
  { key: 'fat', label: '脂肪', lower: 20, upper: 30 },
  { key: 'protein', label: '蛋白质', lower: 10, upper: 15 },
] as const
const PAGE_SIZE = 8
const number = (value: number | null | undefined, digits = 1) => value == null || !Number.isFinite(value)
  ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits })
const percent = (value: number | null | undefined) => value == null ? '—' : `${number(value)}%`
const between = (value: number | null | undefined, lower: number | null | undefined, upper?: number | null): Tone => {
  if (value == null || lower == null) return 'unknown'
  if (value >= lower && (upper == null || value <= upper)) return 'good'
  return upper != null && value > upper ? 'warning' : 'attention'
}
const toneLabel = { good: '参考范围内', attention: '需关注', warning: '需关注', unknown: '待评价' }
const ratioTone = (value: number | null | undefined, lower: number, upper: number): Tone => {
  const tone = between(value, lower, upper)
  return tone === 'warning' ? 'attention' : tone
}

function conclusion(analysis?: GroupReportAnalysis) {
  if (!analysis) return { tone: 'unknown', title: '历史报告待补充结构分析', description: '重新生成本周期报告后，可查看餐次覆盖、供能结构与菜品贡献。现有营养记录见下方明细。' }
  const { energy_percentages: ratios, avg_nutrients: avg, reference: ref } = analysis
  if (!ratios) return { tone: 'unknown', title: '营养数据不足，暂不评价供能结构', description: '补充午晚餐识别记录和菜品营养信息后，可分析三大营养素供能比例。' }
  const concerns = [
    ratios.fat > 30 ? `脂肪供能 ${percent(ratios.fat)}，高于建议上限 30%` : ratios.fat < 20 ? `脂肪供能 ${percent(ratios.fat)}，低于建议下限 20%` : '',
    ratios.carbohydrate < 50 ? '碳水供能偏低' : ratios.carbohydrate > 65 ? '碳水供能偏高' : '',
    ratios.protein < 10 ? '蛋白质供能偏低' : ratios.protein > 15 ? '蛋白质供能偏高' : '',
    avg.fiber != null && ref.fiber_lower != null && avg.fiber < ref.fiber_lower ? '膳食纤维偏低' : '',
  ].filter(Boolean)
  const energyTone = between(avg.calories, ref.calories_lower, ref.calories_upper)
  const energy = energyTone === 'good' ? '双餐供能在参考范围内' : energyTone === 'unknown' ? '双餐供能待评价' : '双餐供能需调整'
  return {
    tone: concerns.length || energyTone === 'attention' || energyTone === 'warning' ? 'attention' : 'good',
    title: `${energy}，${concerns.length ? '供能结构需调整' : '供能比例较均衡'}`,
    description: concerns.length ? `${concerns.join('；')}。结合下方菜品贡献与调整建议，优化餐食搭配。` : '继续保持主食、鱼肉蛋豆与蔬果的合理搭配，并结合餐次覆盖情况持续观察。',
  }
}

function recommendations(analysis?: GroupReportAnalysis, suggestions: string[] = []) {
  if (!analysis?.energy_percentages) return suggestions.map(text => {
    const [label, ...rest] = text.split('｜')
    return { label: rest.length ? label : '建议', text: rest.length ? rest.join('｜') : text, owner: '' }
  })
  const { energy_percentages: ratios, avg_nutrients: avg, reference: ref } = analysis
  const dishes = analysis.fat_contributors.items.slice(0, 3).map(item => item.name).join(' / ')
  return [
    { label: '食堂', text: ratios.fat > 30
      ? `优先复核${dishes ? `「${dishes}」等菜品的` : '脂肪贡献较高菜品的'}用油与份量，增加蒸、炖、烩菜式，目标脂肪供能比 20–30%。`
      : ratios.fat < 20 ? '合理搭配鱼、蛋、豆制品及适量植物油，目标脂肪供能比 20–30%。' : '保持适量用油与多样烹调方式，持续观察脂肪供能结构。', owner: '食堂' },
    { label: '主食', text: ratios.carbohydrate < 50 ? `增加全谷物、杂粮与薯类搭配，碳水供能比由 ${percent(ratios.carbohydrate)} 向 50–65% 调整。`
      : ratios.carbohydrate > 65 ? '调整主食与鱼肉蛋豆、蔬菜的搭配，目标碳水供能比 50–65%。' : '保持主食供给，适当增加全谷物和杂粮的种类。', owner: '食堂' },
    { label: '蔬果', text: avg.fiber != null && ref.fiber_lower != null && avg.fiber < ref.fiber_lower
      ? `增加绿叶菜、水果与豆类供给，双餐膳食纤维由 ${number(avg.fiber)}g 向 ≥ ${number(ref.fiber_lower)}g 调整。`
      : '增加蔬菜、水果与豆类的品种，持续关注膳食纤维供给。', owner: '食堂、班主任' },
  ]
}

function Indicator({ label, value, reference, tone }: { label: string; value: string; reference: string; tone: Tone }) {
  return <div className={`nutrition-indicator tone-${tone}`}>
    <span className="nutrition-indicator-label">{label}</span><strong>{value}</strong>
    <span className="nutrition-indicator-reference">{reference}</span><span className="nutrition-status">{toneLabel[tone]}</span>
  </div>
}

export default function GroupReportCard({ content, report, exportMode = false }: { content: GroupReportContent; report: Report | null; exportMode?: boolean }) {
  const [cohortPage, setCohortPage] = useState(0)
  const analysis = content.group_analysis
  const coverage = analysis?.coverage
  const ratios = analysis?.energy_percentages
  const ref = analysis?.reference
  const avg = analysis?.avg_nutrients
  const summary = conclusion(analysis)
  const advice = recommendations(analysis, content.suggestions)
  const monthly = report?.report_type.endsWith('_monthly') ?? false
  const period = monthly ? '本月' : '本周'
  const scopeLabel = { class: '班级', grade: '年级', campus: '校区' }[content.scope_type]
  const scopeEnglish = { class: 'CLASS', grade: 'GRADE', campus: 'CAMPUS' }[content.scope_type]
  const cohorts = content.cohorts || []
  const pageCount = Math.max(1, Math.ceil(cohorts.length / PAGE_SIZE))
  const safePage = Math.min(cohortPage, pageCount - 1)
  const contributors = analysis?.fat_contributors.items || []
  const daily = analysis?.daily_series || (content.daily_series || []).map(day => ({ date: day.date, calories: day.nutrients.calories ?? null, sample_count: day.sample_counts.calories || 0 }))
  const hasDaily = daily.some(day => day.calories != null)
  const hasBand = ref?.calories_lower != null && ref.calories_upper != null
  const trendValues = daily.flatMap(day => day.calories == null ? [] : [day.calories])
  const maxEnergy = Math.max(...trendValues, ref?.calories_upper || 0, 1)
  const minEnergy = Math.min(...trendValues, ref?.calories_lower ?? maxEnergy)
  const chartDomain = [Math.max(0, Math.floor(minEnergy * .75 / 100) * 100), Math.ceil(maxEnergy * 1.2 / 100) * 100]
  const trendChart = <LineChart width={exportMode ? 878 : undefined} height={exportMode ? 200 : undefined} data={daily} margin={{ top: 24, right: 20, bottom: 8, left: 2 }} accessibilityLayer>
    <CartesianGrid stroke="#eaf0ed" vertical={false} />
    <XAxis dataKey="date" tickFormatter={value => monthly ? String(value).slice(5).replace('-', '/') : ['日', '一', '二', '三', '四', '五', '六'][new Date(`${value}T12:00:00`).getDay()]} axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#60756b' }} minTickGap={22} dy={8} />
    <YAxis domain={chartDomain} tick={{ fontSize: 10, fill: '#60756b' }} axisLine={false} tickLine={false} width={42} tickCount={4} />
    {hasBand ? <ReferenceArea y1={ref.calories_lower!} y2={ref.calories_upper!} fill="#def3eb" fillOpacity={1} /> : null}
    {hasBand ? [ref.calories_lower!, ref.calories_upper!].map(bound => <ReferenceLine key={bound} y={bound} stroke="#80b9a5" strokeDasharray="3 4" label={{ value: number(bound, 0), position: 'insideTopLeft', fill: '#10765e', fontSize: 10 }} />) : null}
    <Tooltip contentStyle={{ border: '1px solid #dce6e1', borderRadius: 6, fontSize: 12 }} labelFormatter={value => `${value} · 双餐日均`} formatter={value => [`${number(Number(value))} kcal`, '供能']} />
    <Line type="linear" dataKey="calories" stroke="#10765e" strokeWidth={2.5} dot={{ r: 3, fill: '#10765e', strokeWidth: 0 }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false} />
  </LineChart>

  return <article className="nutrition-report" aria-label={`${content.scope_name}${scopeLabel}营养${monthly ? '月报' : '周报'}`}>
    <header className="nutrition-header">
      <div><p className="nutrition-eyebrow">{scopeEnglish} NUTRITION {monthly ? 'MONTHLY' : 'WEEKLY'}</p><h2>{scopeLabel}在校膳食营养{monthly ? '月报' : '周报'}</h2></div>
      <div className="nutrition-header-meta"><p>{content.period_start.replace(/-/g, '.')} – {content.period_end.slice(5).replace('-', '.')}</p><p>{content.scope_name} · {number(content.students_with_data, 0)} / {number(content.student_count, 0)} 人</p></div>
    </header>
    <p className="nutrition-basis">餐次覆盖率 {percent(coverage?.meal_coverage_rate)} · 识别匹配率 {percent(coverage?.recognition_match_rate)} · 口径：午+晚餐双餐日均供给（WS/T 554），非全天 DRI</p>
    <section className={`nutrition-conclusion tone-${summary.tone}`} aria-label="本期核心结论">
      <h3>{period}核心结论：{summary.title}</h3><p>{summary.description}</p>
    </section>
    <div className="nutrition-indicators">
      <Indicator label="餐次覆盖率" value={percent(coverage?.meal_coverage_rate)} reference="目标 ≥ 90%" tone={between(coverage?.meal_coverage_rate, 90)} />
      <Indicator label="脂肪供能比" value={percent(ratios?.fat)} reference="建议 20–30%" tone={ratioTone(ratios?.fat, 20, 30)} />
      <Indicator label="碳水供能比" value={percent(ratios?.carbohydrate)} reference="建议 50–65%" tone={ratioTone(ratios?.carbohydrate, 50, 65)} />
      <Indicator label="膳食纤维" value={avg?.fiber == null ? '—' : `${number(avg.fiber)}g`} reference={ref?.fiber_lower == null ? '参考量待补充' : `双餐参考 ≥ ${number(ref.fiber_lower)}g`} tone={between(avg?.fiber, ref?.fiber_lower)} />
    </div>
    <section className="nutrition-section">
      <h3>供能比结构 · 红绿灯</h3><p className="nutrition-caption">绿色区间为建议比例，竖线为本期值；三项使用同一组完整营养样本。{analysis ? `覆盖 ${analysis.macro_sample_count} 人。` : ''}</p>
      <div className="nutrition-ratios">{MACROS.map(field => {
        const value = ratios?.[field.key]
        const tone = field.key === 'protein' ? between(value, field.lower, field.upper) : ratioTone(value, field.lower, field.upper)
        const status = value == null ? '待评价' : value < field.lower ? '偏低' : value > field.upper ? '偏高' : '范围内'
        return <div className={`nutrition-ratio tone-${tone}`} key={field.key}>
          <div className="nutrition-row"><span>{field.label}</span><span><strong>{percent(value)}</strong><span className="nutrition-muted">（建议 {field.lower}–{field.upper}%）</span><small>{status}</small></span></div>
          <div className="nutrition-ratio-track" role="img" aria-label={`${field.label}${percent(value)}，建议${field.lower}至${field.upper}%，${status}`}>
            <span className="nutrition-ratio-range" style={{ left: `${field.lower}%`, width: `${field.upper - field.lower}%` }} />
            {value != null ? <span className="nutrition-ratio-marker" style={{ left: `${Math.min(100, Math.max(0, value))}%` }} /> : null}
          </div>
        </div>
      })}</div>
    </section>
    <section className="nutrition-section">
      <h3>{period}每日供能趋势</h3><p className="nutrition-caption">{hasBand ? `浅绿带 = 午+晚双餐供能参考区间 ${number(ref.calories_lower, 0)}–${number(ref.calories_upper, 0)} kcal；` : ''}{analysis ? '已记录午晚餐折算双餐日均，空缺日期不连线。' : '历史记录日均供给，尚未按午晚双餐折算。'}</p>
      {hasDaily ? <div className="nutrition-energy-chart" role="img" aria-label={`${period}每日供能趋势，单位千卡${hasBand ? `，双餐参考${number(ref.calories_lower, 0)}至${number(ref.calories_upper, 0)}` : ''}`}>
        {exportMode ? trendChart : <ResponsiveContainer width="100%" height="100%">{trendChart}</ResponsiveContainer>}
      </div> : <p className="nutrition-empty">暂无有效的每日供能记录，补充午晚餐识别后查看趋势。</p>}
    </section>
    <section className="nutrition-section">
      <h3>{content.scope_type === 'class' ? '本班概况' : '各班级看板'}（餐次覆盖率 / 达标率 / 脂肪供能比）</h3><p className="nutrition-caption">按比例比较班级；达标率仅统计年龄、性别与营养数据齐全的可评价学生。</p>
      <div className="nutrition-table-scroll"><table className="nutrition-table"><thead><tr><th scope="col">班级</th><th scope="col">餐次覆盖率</th><th scope="col">达标率</th><th scope="col">脂肪供能比</th></tr></thead><tbody>
        {(cohorts.length ? exportMode ? cohorts : cohorts.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE) : [{ name: content.scope_name, analysis }]).map(cohort => {
          const item = cohort.analysis
          return <tr key={cohort.name}><th scope="row">{cohort.name.split(' / ').slice(-1)[0]}</th>
            <td className={`tone-${between(item?.coverage.meal_coverage_rate, 90)}`}>{percent(item?.coverage.meal_coverage_rate)}</td>
            <td title={item ? `${item.passed_students} / ${item.assessed_students} 位可评价学生` : '待补充结构分析'}>{percent(item?.compliance_rate)}{item?.compliance_rate != null ? <small>{item.passed_students} / {item.assessed_students} 人</small> : null}</td>
            <td className={`tone-${ratioTone(item?.energy_percentages?.fat, 20, 30)}`}>{percent(item?.energy_percentages?.fat)}</td></tr>
        })}
      </tbody></table></div>
      {!exportMode && pageCount > 1 ? <nav className="nutrition-pager" aria-label="班级看板分页"><span>共 {cohorts.length} 个班级</span><button type="button" disabled={!safePage} onClick={() => setCohortPage(safePage - 1)}>上一页</button><span>{safePage + 1} / {pageCount}</span><button type="button" disabled={safePage + 1 >= pageCount} onClick={() => setCohortPage(safePage + 1)}>下一页</button></nav> : null}
    </section>
    <section className="nutrition-section">
      <h3>脂肪贡献 TOP 5 菜品（食堂调整参考）</h3><p className="nutrition-caption">占有效识别菜品脂肪总量的比例，用于定位优先复核的菜品。{analysis ? `来自 ${analysis.fat_contributors.image_count} 张营养信息完整的午晚餐图像。` : ''}</p>
      {contributors.length ? <div className="nutrition-contributors">{contributors.map((dish, index) => <div className={`nutrition-contributor tone-${ratios && ratios.fat > 30 ? index < 3 ? 'attention' : 'warning' : 'good'}`} key={dish.name}>
        <div className="nutrition-row"><span>{dish.name}</span><strong>{percent(dish.share)}</strong></div><div className="nutrition-contributor-track" role="img" aria-label={`${dish.name}占已识别菜品脂肪${percent(dish.share)}`}><span style={{ width: `${dish.share}%` }} /></div>
      </div>)}</div> : <p className="nutrition-empty">暂无可归因的菜品记录。需要午晚餐匹配图像、有效菜品识别及完整脂肪数据。</p>}
    </section>
    <section className="nutrition-section">
      <h3>基于{period}数据的调整建议</h3><div className="nutrition-advice">{advice.length ? advice.map((item, index) => <p key={`${item.label}-${index}`}><strong>{item.label}：</strong>{item.text}{item.owner ? <span className="nutrition-owner">责任：{item.owner}</span> : null}</p>) : <p>补充午晚餐记录后，可查看针对供能结构的调整建议。</p>}</div>
    </section>
    <section className="nutrition-linkage" aria-label="吃动体质联动规划">
      <h3>吃—动—体质联动 <span>规划中</span></h3><div><p>在校双餐日均供给 <strong>{number(avg?.calories, 0)} kcal</strong></p><p>估算消耗 <strong>— kcal</strong></p><p>体测维度 <strong>BMI / 肺活量 / 50m</strong></p></div>
    </section>
    <details className="nutrition-details" open={exportMode || undefined}><summary>营养供给明细与统计口径</summary>
      <p className="nutrition-caption">{analysis?.supply_note || '历史报告明细保留原始在校膳食统计口径。'}</p>
      <table className="nutrition-table"><thead><tr><th scope="col">营养指标</th><th scope="col">原始记录日均</th><th scope="col">有效人数</th></tr></thead><tbody>{REPORT_NUTRITION_FIELDS.map(field => {
        const key = field.key as keyof NutrientData
        return <tr key={key}><th scope="row">{field.label}</th><td>{number(content.avg_nutrients[key])} {field.unit}</td><td>{number(content.nutrient_sample_counts?.[key], 0)} 人</td></tr>
      })}</tbody></table>
      {analysis ? <div className="nutrition-methods"><p>{analysis.coverage_note}</p><p>识别匹配率 = 有实际营养识别记录的午晚餐次 ÷ 已记录午晚餐次。预计营养不计入识别分子。</p><p>{analysis.compliance_note}</p><p>餐次覆盖率 90% 为本系统观察目标。</p><p>已记录 {coverage?.recorded_meals} / 应记录 {coverage?.expected_meals} 餐；识别匹配 {coverage?.matched_meals} 餐；另有预计就餐 {coverage?.estimated_meals} 餐。</p></div> : null}
    </details>
    <footer className="nutrition-sources"><p>口径：午+晚双餐日均供给（WS/T 554），不比较全天 DRI。供能参考按适用年龄、性别参考值的 65–75% 计算，膳食纤维按 75% 折算。{analysis?.supply_note}</p><p>人数覆盖：{number(content.students_with_data, 0)} / {number(content.student_count, 0)} 人（{percent(content.data_coverage_rate)}）；餐次覆盖率与人数覆盖率分别统计。</p>
      {content.references?.length ? <p className="nutrition-reference-links">参考依据：{content.references.map(source => <a key={source.id} href={source.url} target="_blank" rel="noopener noreferrer">{source.title} · {source.version}</a>)}</p> : null}
    </footer>
  </article>
}
