import { useEffect, useId, useRef, useState, type ComponentProps, type Dispatch, type SetStateAction } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { NUTRITION_FIELDS, type NutritionKey } from '@/lib/nutrition'
import { cn } from '@/lib/utils'
import type { MealSlot } from '@/types'
import { CanteenSheet } from './CanteenSheet'
import { DishPhotoPicker } from './DishPhotoPicker'

export type CanteenDishForm = {
  name: string
  description: string
  ingredients: string
  price: string
  category: string
  weight: string
  meal_slots: string[]
  resident_meal_slots: string[]
} & Record<NutritionKey, string>

type Props = {
  editing: boolean
  form: CanteenDishForm
  setForm: Dispatch<SetStateAction<CanteenDishForm>>
  mealSlots: MealSlot[]
  saving: boolean
  analyzing: boolean
  onAnalyze: () => void
  onSave: () => void
  onClose: () => void
  photos: ComponentProps<typeof DishPhotoPicker>
  onDescription: (value: string) => void
  focusPhotos: boolean
  suspended: boolean
}

export function MobileDishEditor({ editing, form, setForm, mealSlots, saving, analyzing, onAnalyze, onSave, onClose, photos, onDescription, focusPhotos, suspended }: Props) {
  const formId = useId()
  const photoId = useId()
  const photoRef = useRef<HTMLDivElement>(null)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const busy = saving || analyzing || photos.deletingId != null
  useEffect(() => { if (focusPhotos) photoRef.current?.scrollIntoView({ block: 'start' }) }, [focusPhotos])

  return (
    <CanteenSheet open suspended={suspended} onClose={onClose} busy={busy} title={editing ? '编辑菜品' : '新增菜品'} description="填好菜名、分类和价格，再添加清晰照片。"
      footer={<button type="submit" form={formId} disabled={busy} className="canteen-button w-full bg-primary text-primary-foreground">{saving && <Loader2 className="h-5 w-5 animate-spin" />}{saving ? '保存中…' : '保存菜品'}</button>}>
      <form id={formId} onSubmit={(event) => { event.preventDefault(); if (!busy) onSave() }} className="space-y-5">
        <fieldset disabled={busy} className="space-y-5">
          <label className="block text-sm font-medium">菜品名称 <span className="text-destructive">*</span>
            <input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} required autoComplete="off" placeholder="例如：番茄炒蛋" className="canteen-input mt-2" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-sm font-medium">分类 <span className="text-destructive">*</span>
              <select value={form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value }))} className="canteen-input mt-2">
                {['主食', '荤菜', '素菜', '汤', '其他'].map((category) => <option key={category}>{category}</option>)}
              </select>
            </label>
            <label className="block text-sm font-medium">价格（元） <span className="text-destructive">*</span>
              <input value={form.price} onChange={(event) => setForm((current) => ({ ...current, price: event.target.value }))} type="number" min="0" step="0.01" inputMode="decimal" required placeholder="0.00" className="canteen-input mt-2" />
            </label>
          </div>
          <div id={photoId} ref={photoRef} className="scroll-mt-4 rounded-2xl border border-border p-4">
            <DishPhotoPicker {...photos} disabled={busy} />
          </div>
          <details open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)} className="rounded-2xl border border-border">
            <summary className="cursor-pointer px-4 py-4 text-sm font-medium">营养与更多设置</summary>
            <div className="space-y-5 border-t border-border p-4">
              <label className="block text-sm font-medium">分析用重量（克）
                <input value={form.weight} onChange={(event) => setForm((current) => ({ ...current, weight: event.target.value }))} type="number" min="1" inputMode="decimal" className="canteen-input mt-2" />
              </label>
              <label className="block text-sm font-medium">主要食材（选填）
                <textarea value={form.ingredients} onChange={(event) => setForm((current) => ({ ...current, ingredients: event.target.value }))} rows={2} placeholder="例如：鸡蛋、番茄" className="canteen-input mt-2" />
              </label>
              <button type="button" onClick={onAnalyze} disabled={busy || !form.name.trim()} className="canteen-button w-full border border-border text-primary"><Sparkles className="h-4 w-4" />{analyzing ? '正在分析…' : '自动分析营养'}</button>
              <p className="text-xs text-muted-foreground">以下营养数值按每 100 克计算。</p>
              <div className="grid grid-cols-2 gap-3">
                {NUTRITION_FIELDS.map(({ key, label, unit }) => <label key={key} className="block text-xs text-muted-foreground">{label}（{unit}）
                  <input value={form[key]} onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))} type="number" min="0" step="any" inputMode="decimal" className="canteen-input mt-1" />
                </label>)}
              </div>
              {([
                { field: 'resident_meal_slots', title: '每天默认供应', hint: '新日期菜单会默认选中，当天仍可移除。' },
                { field: 'meal_slots', title: '适用餐次', hint: '指定这道菜可以在哪些餐次供应。' },
              ] as const).map(({ field, title, hint }) => <div key={field}>
                <p className="text-sm font-medium">{title}</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{hint}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {mealSlots.map((meal) => <label key={meal.key} className={cn('flex min-h-12 cursor-pointer items-center gap-2 rounded-xl border px-3 text-sm', form[field].includes(meal.key) ? 'border-primary/30 bg-primary/10 text-primary' : 'border-border')}>
                    <input type="checkbox" checked={form[field].includes(meal.key)} onChange={(event) => setForm((current) => ({ ...current, [field]: event.target.checked ? [...current[field], meal.key] : current[field].filter((key) => key !== meal.key) }))} className="h-4 w-4 accent-primary" />{meal.label}
                  </label>)}
                </div>
              </div>)}
              <label className="block text-sm font-medium">菜品描述（选填）
                <textarea value={form.description} onChange={(event) => onDescription(event.target.value)} rows={4} className="canteen-input mt-2" />
              </label>
            </div>
          </details>
        </fieldset>
      </form>
    </CanteenSheet>
  )
}
