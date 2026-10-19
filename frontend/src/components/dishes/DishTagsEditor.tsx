import { useId } from 'react'

export interface DishTags {
  food_groups: string[]
  cooking_method: string
  vegetable_types: string[]
}

const FOOD_GROUPS = ['谷薯', '畜肉', '禽肉', '鱼虾', '蛋', '豆', '蔬菜', '水果', '奶', '坚果']
const COOKING_METHODS = ['蒸', '煮', '炖', '炒', '煎', '炸', '烤', '凉拌', '其他']
const VEGETABLE_TYPES = ['深色蔬菜']

export function suggestedDishTags(data: Partial<DishTags>): Partial<DishTags> {
  return {
    ...(Array.isArray(data.food_groups) ? { food_groups: data.food_groups.filter(tag => FOOD_GROUPS.includes(tag)) } : {}),
    ...(Array.isArray(data.vegetable_types) ? { vegetable_types: data.vegetable_types.filter(tag => VEGETABLE_TYPES.includes(tag)) } : {}),
    ...(typeof data.cooking_method === 'string' && COOKING_METHODS.includes(data.cooking_method) ? { cooking_method: data.cooking_method } : {}),
  }
}

export function DishTagsEditor({ value, onChange }: { value: DishTags; onChange: (patch: Partial<DishTags>) => void }) {
  const methodId = useId()
  return <div className="space-y-4 rounded-xl border border-border bg-secondary/20 p-4">
    <div><p className="text-sm font-medium">选餐特征标签</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">AI 分析可提供建议，请核对后保存。保存的标签用于报告中的食物选择特征。</p></div>
    {([
      { field: 'food_groups', label: '食物组（可多选）', options: FOOD_GROUPS },
      { field: 'vegetable_types', label: '蔬菜标签（选填）', options: VEGETABLE_TYPES },
    ] as const).map(({ field, label, options }) => <fieldset key={field}>
      <legend className="text-xs font-medium text-muted-foreground">{label}</legend>
      <div className="mt-2 flex flex-wrap gap-2">{options.map(tag => <label key={tag} className="flex min-h-10 cursor-pointer items-center gap-2 rounded-lg border border-border bg-background px-3 text-sm has-[:checked]:border-primary/40 has-[:checked]:bg-primary/10">
        <input type="checkbox" checked={value[field].includes(tag)} onChange={event => onChange({ [field]: event.target.checked ? [...value[field], tag] : value[field].filter(item => item !== tag) })} className="h-4 w-4 accent-primary" />{tag}
      </label>)}</div>
    </fieldset>)}
    <div><label htmlFor={methodId} className="text-xs font-medium text-muted-foreground">主要烹饪方式（选填）</label>
      <select id={methodId} value={value.cooking_method} onChange={event => onChange({ cooking_method: event.target.value })} className="mt-2 min-h-10 w-full rounded-lg border border-border bg-background px-3 text-sm">
        <option value="">未标注</option>{COOKING_METHODS.map(method => <option key={method}>{method}</option>)}
      </select>
    </div>
  </div>
}
