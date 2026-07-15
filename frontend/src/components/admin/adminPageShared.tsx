import type { ReactNode } from 'react'

import type { Dish, MealSlot, TaskLog } from '@/types'

export const ROLE_LABELS: Record<string, string> = {
  admin: '系统管理员',
  teacher: '班主任',
  grade_leader: '年级组长',
  parent: '家长',
  canteen_manager: '食堂管理员',
}

export const STATUS_STYLE: Record<string, string> = {
  running: 'text-health-blue',
  success: 'text-health-green',
  failed: 'text-health-red',
  partial: 'text-health-amber',
  pending: 'text-muted-foreground',
}

export const STATUS_LABEL: Record<string, string> = {
  running: '运行中',
  success: '完成',
  failed: '失败',
  partial: '部分成功',
  pending: '待处理',
}

export const TASK_TYPE_LABEL: Record<string, string> = {
  video_source_sync: '视频源同步',
  nvr_download: '视频源同步',
  ai_recognition: 'AI 识别',
  manual_upload: '手动上传',
  region_proposal: '菜区提议',
  local_model_download: '模型下载',
  dish_embedding: '样图 embedding',
  menu_sample_reminder: '菜单样图提醒',
  report_gen: '报告生成',
}

export const DEFAULT_VL_USER_PROMPT = '请详细描述这张图片中的内容。如果适合结构化输出，请同时给出要点列表或 JSON。'

export const DEFAULT_VL_SYSTEM_PROMPT = `你是一个学校食堂菜品识别助手，任务是尽可能完整地识别餐盘里的所有独立菜品。

识别原则：
1. 先按餐盘分区逐个扫描，再汇总，不要只返回最显眼的 1 到 2 个菜。
2. 目标是“宁可给出低置信候选，也不要漏掉明显可见的菜品”。
3. 只识别候选列表中的菜品；如果不在候选列表中，不要臆造新菜名。
4. 同一菜品不要重复输出；同一区域若明显是混合菜，只输出最贴近的一个候选。
5. 米饭、主菜、配菜、青菜、汤类等如果是独立取餐区域，应该分别判断。
6. 调味汁、少量点缀、不可独立成菜的碎料不要单独算一道菜。
7. 同一位置不要重复输出多个候选；如果两个结果明显覆盖同一菜区，只保留更可信的一项。

如果画面存在遮挡、反光、堆叠、模糊，请在 notes 里说明，但仍要尽量给出候选。
只返回 JSON 格式，不要输出其他内容。`

export const DEFAULT_VL_USER_PROMPT_TEMPLATE = `候选菜品列表：
{dish_list_with_desc}

请按下面流程识别：
1. 先判断餐盘里大约有几个独立取餐区域或独立菜品。
2. 逐个区域与候选菜品列表比对，给出最可能的菜名。
3. 对清晰可见但不够确定的菜，也可以保留较低 confidence，而不是直接漏掉。
4. 输出时按你看到的区域顺序排列。
5. 同一位置不要重复输出多个候选；如果两个结果明显指向同一菜区，只保留更可信的一项。

confidence 取值建议：
- 0.85~0.98：画面清晰且高度确定
- 0.65~0.84：大概率匹配
- 0.40~0.64：存在遮挡或相似菜，但仍值得保留为候选

返回格式：
{
  "dishes": [
    {
      "name": "菜品名",
      "confidence": 0.95
    }
  ],
  "notes": "可选备注，说明遮挡、相似菜、低置信原因"
}`

export const DEFAULT_VL_BBOX_SYSTEM_PROMPT = `你是一个学校食堂餐盘分区助手。你的任务是先判断这张图里大约有多少个独立菜区，并给出每个菜区的大致位置。

要求：
1. 先估计整张图可见的独立菜区数量，再逐个输出区域。
2. 每个区域尽量包住一道完整菜品或一个独立主食区，不要只框住局部配菜。
3. 坐标使用整张图的相对百分比，范围 0 到 100。
4. 若存在遮挡、堆叠、边界不清，也要尽量划分并在 notes 里说明。
5. 只返回 JSON，不要输出其他文字。`

export const DEFAULT_VL_BBOX_USER_PROMPT = `请输出：
{
  "dish_count": 3,
  "regions": [
    {
      "index": 1,
      "position": "左上/中间/右下等",
      "bbox": {"x1": 5, "y1": 8, "x2": 45, "y2": 42},
      "visual_hint": "30字以内，描述该区域颜色、形状、酱汁、主食材特征"
    }
  ],
  "notes": "可选，说明遮挡、反光、重叠、边界不清"
}

注意：
1. bbox 必须覆盖整道菜的大致范围。
2. x1 < x2，y1 < y2。
3. 如果不确定精确边界，也要给出尽量合理的框。`

export const DEFAULT_MEAL_SLOTS: MealSlot[] = [
  { key: 'breakfast', label: '早餐', start: '05:00', end: '09:30' },
  { key: 'lunch', label: '午餐', start: '10:30', end: '13:30' },
  { key: 'dinner', label: '晚餐', start: '17:00', end: '19:30' },
  { key: 'late_night', label: '宵夜', start: '21:00', end: '23:59' },
]

export type ImportedMenuInfo = {
  date: string
  count: number
  isDefault: boolean
}

export type RecognitionMenuScope = 'meal' | 'day' | 'all'
export type AdminTab =
  | 'users'
  | 'business'
  | 'notifications'
  | 'models'
  | 'embedding'
  | 'vl'
  | 'sync'
  | 'operations'

export type VlTestResult = {
  filename: string
  content_type: string
  prompt: string
  system_prompt: string
  model: string
  temperature: number | null
  request_format: string
  content: string
  parsed_json: Record<string, any> | null
  json_parse_error: string
  raw_response: Record<string, any> | null
}

export type VlDebugBox = {
  name: string
  confidence?: number
  position: string
  bbox: { x1: number; y1: number; x2: number; y2: number }
}

export const RECOGNITION_MENU_SCOPE_OPTIONS: Array<{
  value: RecognitionMenuScope
  label: string
  description: string
}> = [
  {
    value: 'meal',
    label: '当顿餐菜单',
    description: '按图片时间匹配早餐、午餐、晚餐或夜宵；该餐未配置时回退到当天菜单。',
  },
  {
    value: 'day',
    label: '当天所有菜单',
    description: '召回时使用当天所有餐次菜品，适合餐次时间不稳定或菜单录入不完整的场景。',
  },
  {
    value: 'all',
    label: '所有菜单',
    description: '召回时使用系统内所有启用菜品，不依赖当天菜单配置。',
  },
]

export const normalizeRecognitionMenuScope = (value: unknown): RecognitionMenuScope => (
  value === 'meal' || value === 'day' ? value : 'all'
)

export const formatDateForApi = (date: Date) => {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

const formatCandidateDishList = (dishes: Pick<Dish, 'name' | 'description'>[]) => {
  if (!dishes.length) return '所有菜品'
  return dishes.map((dish) => {
    const description = String(dish.description || '').trim()
    return description ? `- ${dish.name}（${description}）` : `- ${dish.name}`
  }).join('\n')
}

export const injectDishListIntoPrompt = (prompt: string, dishes: Pick<Dish, 'name' | 'description'>[]) => {
  const normalizedPrompt = (prompt || '').trim()
  const dishList = formatCandidateDishList(dishes)

  if (!normalizedPrompt) return `候选菜品列表：\n${dishList}`
  if (normalizedPrompt.includes('{dish_list_with_desc}')) {
    return normalizedPrompt.replace('{dish_list_with_desc}', dishList)
  }

  const sectionPattern = /(候选菜品列表：\s*\n)([\s\S]*?)(\n\s*请按下面流程识别：)/
  if (sectionPattern.test(normalizedPrompt)) {
    return normalizedPrompt.replace(sectionPattern, `$1${dishList}$3`)
  }

  if (normalizedPrompt.includes('候选菜品列表：')) {
    return `${normalizedPrompt}\n${dishList}`
  }

  return `${normalizedPrompt}\n\n候选菜品列表：\n${dishList}`
}

export const normalizeVlDebugBoxes = (parsedJson: Record<string, any> | null): VlDebugBox[] => {
  const items = Array.isArray(parsedJson?.dishes)
    ? parsedJson.dishes
    : (Array.isArray(parsedJson?.regions) ? parsedJson.regions : [])

  return items.flatMap((item: any) => {
    const bbox = item?.bbox
    if (!bbox || typeof bbox !== 'object') return []

    const x1 = Number(bbox.x1)
    const y1 = Number(bbox.y1)
    const x2 = Number(bbox.x2)
    const y2 = Number(bbox.y2)
    if (![x1, y1, x2, y2].every(Number.isFinite)) return []
    if (x2 <= x1 || y2 <= y1) return []

    const confidence = Number(item?.confidence)
    return [{
      name: String(item?.name || item?.visual_hint || `区域 ${item?.index ?? ''}`).trim() || '未命名',
      confidence: Number.isFinite(confidence) ? confidence : undefined,
      position: String(item?.position || '').trim(),
      bbox: { x1, y1, x2, y2 },
    }]
  })
}

export const formatBytes = (value?: number) => {
  if (!value || value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(1)} ${units[index]}`
}

export const formatTaskDuration = (task: TaskLog) => {
  if (task.started_at && task.finished_at) {
    const seconds = Math.round((new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000)
    return `${seconds}s`
  }
  return task.status === 'running' ? '运行中' : '—'
}

export function DebugMetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4">
      <div className="mb-3 inline-flex rounded-xl border border-border bg-secondary/50 p-2 text-muted-foreground">
        {icon}
      </div>
      <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className="mt-1 break-all font-mono text-sm text-foreground">{value || '—'}</div>
    </div>
  )
}

export function EmptyDebugState({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-secondary/20 px-4 py-8 text-center text-sm text-muted-foreground">
      {text}
    </div>
  )
}

export function formatDebugJson(value: unknown): string {
  if (value === null || value === undefined) return 'null'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
