import { useMemo, useState } from 'react'
import { Building2, Check, ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, GraduationCap, Layers3, Network, Search, School, Users, X } from 'lucide-react'
import type { SchoolNode } from '@/components/students/adminTypes'
import { cn } from '@/lib/utils'

export type ReportScope = 'personal' | 'class' | 'grade' | 'campus'
type OrganizationKind = 'school' | 'campus' | 'stage' | 'grade' | 'class'

export type ReportOrganizationNode = {
  key: string
  id: number
  kind: OrganizationKind
  name: string
  path: string[]
  ancestors: string[]
  studentCount: number
  children: ReportOrganizationNode[]
}

const NODE_ICONS = { school: School, campus: Building2, stage: Layers3, grade: GraduationCap, class: Users }

export function buildReportOrganization(tree: SchoolNode[], role: string | undefined, classIds: string[] = [], gradeIds: string[] = []) {
  const allowedClasses = new Set(classIds.map(Number))
  const allowedGrades = new Set(gradeIds.map(Number))
  const makeNode = (kind: OrganizationKind, source: { id: number; name: string }, parents: { key: string; name: string }[], children: ReportOrganizationNode[], studentCount = 0): ReportOrganizationNode => ({
    key: `${kind}-${source.id}`, id: source.id, kind, name: source.name,
    path: [...parents.map(parent => parent.name), source.name],
    ancestors: parents.map(parent => parent.key),
    studentCount: children.length ? children.reduce((sum, child) => sum + child.studentCount, 0) : studentCount,
    children,
  })
  const parent = (kind: OrganizationKind, node: { id: number; name: string }) => ({ key: `${kind}-${node.id}`, name: node.name })
  return tree.flatMap(school => {
    const schoolPath = [parent('school', school)]
    const campuses = school.campuses.flatMap(campus => {
      const campusPath = [...schoolPath, parent('campus', campus)]
      const stages = campus.stages.flatMap(stage => {
        const stagePath = [...campusPath, parent('stage', stage)]
        const grades = stage.grades.flatMap(grade => {
          const gradePath = [...stagePath, parent('grade', grade)]
          const classes = grade.classes
            .filter(item => role === 'admin' || (role === 'teacher' && allowedClasses.has(item.id)) || (role === 'grade_leader' && allowedGrades.has(grade.id)))
            .map(item => makeNode('class', item, gradePath, [], item.student_count || 0))
          return role === 'admin' || classes.length || (role === 'grade_leader' && allowedGrades.has(grade.id))
            ? [makeNode('grade', grade, stagePath, classes)] : []
        })
        return role === 'admin' || grades.length ? [makeNode('stage', stage, campusPath, grades)] : []
      })
      return role === 'admin' || stages.length ? [makeNode('campus', campus, schoolPath, stages)] : []
    })
    return role === 'admin' || campuses.length ? [makeNode('school', school, [], campuses)] : []
  })
}

export function flattenReportOrganization(nodes: ReportOrganizationNode[]): ReportOrganizationNode[] {
  return nodes.flatMap(node => [node, ...flattenReportOrganization(node.children)])
}

function filterNodes(nodes: ReportOrganizationNode[], search: string): ReportOrganizationNode[] {
  return nodes.flatMap(node => {
    if (node.name.toLowerCase().includes(search)) return [node]
    const children = filterNodes(node.children, search)
    return children.length ? [{ ...node, children }] : []
  })
}

function limitToScope(nodes: ReportOrganizationNode[], scope: ReportScope): ReportOrganizationNode[] {
  return nodes.map(node => ({ ...node, children: node.kind === scope ? [] : limitToScope(node.children, scope) }))
}

function OrganizationBranch({ nodes, scope, selectedKey, expanded, searching, onToggle, onSelect, depth = 0 }: {
  nodes: ReportOrganizationNode[]
  scope: ReportScope
  selectedKey: string | null
  expanded: Record<string, boolean>
  searching: boolean
  onToggle: (key: string, current: boolean) => void
  onSelect: (node: ReportOrganizationNode) => void
  depth?: number
}) {
  return <ul className={cn(depth > 0 && 'ml-[15px] border-l border-border/70 pl-2')}>
    {nodes.map(node => {
      const Icon = NODE_ICONS[node.kind]
      const open = searching || (expanded[node.key] ?? node.kind !== 'grade')
      const selectable = scope === 'personal' || node.kind === scope
      const selected = selectedKey === node.key
      return <li key={node.key} className="mt-0.5">
        <div className={cn('flex min-h-9 items-center rounded-lg transition-colors hover:bg-secondary/70', selected && 'bg-primary/10 text-primary')}>
          {node.children.length > 0 ? <button type="button" aria-label={`${open ? '收起' : '展开'}${node.name}`} aria-expanded={open}
            onClick={() => onToggle(node.key, open)} className="flex h-9 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button> : <span className="w-7 shrink-0" />}
          <button type="button" aria-current={selected ? 'true' : undefined} aria-label={`${selectable ? '选择' : open ? '收起' : '展开'}${node.name}`}
            aria-expanded={!selectable && node.children.length ? open : undefined}
            onClick={() => selectable ? onSelect(node) : onToggle(node.key, open)}
            title={node.path.join(' / ')}
            className={cn('flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-lg py-1.5 pr-2 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', selected ? 'font-semibold' : 'text-foreground/80')}>
            <Icon className={cn('h-3.5 w-3.5 shrink-0', selected ? 'text-primary' : 'text-muted-foreground')} />
            <span className="min-w-0 flex-1 truncate">{node.name}</span>
            {selected ? <Check className="h-3.5 w-3.5 shrink-0" /> : <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{node.studentCount}</span>}
          </button>
        </div>
        {open && node.children.length > 0 ? <OrganizationBranch nodes={node.children} scope={scope} selectedKey={selectedKey} expanded={expanded} searching={searching} onToggle={onToggle} onSelect={onSelect} depth={depth + 1} /> : null}
      </li>
    })}
  </ul>
}

export default function ReportOrganizationTree({ nodes, scope, selectedKey, loading, error, onSelect, onRetry }: {
  nodes: ReportOrganizationNode[]
  scope: ReportScope
  selectedKey: string | null
  loading: boolean
  error: boolean
  onSelect: (node: ReportOrganizationNode | null) => void
  onRetry: () => void
}) {
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [expandAll, setExpandAll] = useState(false)
  const normalizedSearch = search.trim().toLowerCase()
  const scopedNodes = useMemo(() => scope === 'personal' ? nodes : limitToScope(nodes, scope), [nodes, scope])
  const filteredNodes = useMemo(() => normalizedSearch ? filterNodes(scopedNodes, normalizedSearch) : scopedNodes, [scopedNodes, normalizedSearch])
  const expandedNodes = useMemo(() => {
    const selected = flattenReportOrganization(nodes).find(node => node.key === selectedKey)
    return { ...Object.fromEntries((selected?.ancestors || []).map(key => [key, true])), ...expanded }
  }, [nodes, selectedKey, expanded])
  const toggleAll = () => {
    const next = !expandAll
    setExpandAll(next)
    setExpanded(Object.fromEntries(flattenReportOrganization(nodes).map(node => [node.key, next])))
  }
  return <section aria-label="组织选择" className="lg:flex lg:min-h-0 lg:flex-[1_1_45%] lg:flex-col">
    <div className="flex shrink-0 items-center justify-between px-4 pb-3 pt-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><Network className="h-4 w-4 text-primary" />学生与组织</h2>
      <button type="button" onClick={toggleAll} aria-label={expandAll ? '收起全部组织' : '展开全部组织'} title={expandAll ? '收起全部' : '展开全部'}
        className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        {expandAll ? <ChevronsDownUp className="h-3.5 w-3.5" /> : <ChevronsUpDown className="h-3.5 w-3.5" />}
      </button>
    </div>
    <div className="relative mx-3 mb-3 shrink-0">
      <Search className="pointer-events-none absolute left-3 top-3 h-3.5 w-3.5 text-muted-foreground" />
      <input aria-label="搜索组织" placeholder="搜索组织名称" value={search} onChange={event => setSearch(event.target.value)}
        className="h-10 w-full rounded-lg border border-border bg-background/70 pl-9 pr-8 text-xs outline-none focus:border-primary/50 focus:ring-2 focus:ring-primary/15" />
      {search ? <button type="button" aria-label="清空组织搜索" onClick={() => setSearch('')} className="absolute right-1 top-1 flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground"><X className="h-3.5 w-3.5" /></button> : null}
    </div>
    <nav aria-label="学生组织" className="max-h-72 overflow-y-auto px-2 pb-3 lg:min-h-0 lg:max-h-none lg:flex-1">
      {scope === 'personal' && !normalizedSearch ? <button type="button" onClick={() => onSelect(null)} aria-current={!selectedKey ? 'true' : undefined}
        className={cn('mb-2 flex min-h-10 w-full items-center gap-2 rounded-lg px-3 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary', !selectedKey ? 'bg-primary/10 font-semibold text-primary' : 'hover:bg-secondary')}>
        <Users className="h-4 w-4" /><span className="flex-1">全部学生</span>
        <span className="font-mono text-[10px]">{nodes.reduce((sum, node) => sum + node.studentCount, 0)}</span>
      </button> : null}
      {loading ? <p role="status" className="p-4 text-center text-xs text-muted-foreground">正在加载组织…</p>
        : error ? <p className="p-4 text-center text-xs text-muted-foreground">组织加载失败 <button type="button" onClick={onRetry} className="ml-1 text-primary underline">重试</button></p>
          : filteredNodes.length ? <OrganizationBranch nodes={filteredNodes} scope={scope} selectedKey={selectedKey} expanded={expandedNodes} searching={!!normalizedSearch}
            onToggle={(key, current) => setExpanded(previous => ({ ...previous, [key]: !current }))}
            onSelect={node => {
              setExpanded(previous => ({ ...previous, ...Object.fromEntries([...node.ancestors, node.key].map(key => [key, true])) }))
              onSelect(node)
            }} />
            : <p className="px-3 py-5 text-center text-xs text-muted-foreground">{normalizedSearch ? '未找到匹配的组织，试试其他名称' : '暂无可查看的组织'}</p>}
    </nav>
  </section>
}
