import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, RotateCcw, Save, Search } from 'lucide-react'
import { format, addDays, isSameDay, isToday, startOfWeek, subDays } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import toast from 'react-hot-toast'

import { adminApi, dishApi, menuApi } from '@/api/client'
import { DEFAULT_MEAL_SLOTS } from '@/components/admin/adminPageShared'
import { cn } from '@/lib/utils'
import type { Dish, MealDishIds, MealSlot } from '@/types'

const DISH_PAGE_SIZE = 100

const DOT_CLASS_NAMES = [
  'bg-amber-400',
  'bg-orange-400',
  'bg-rose-400',
  'bg-indigo-400',
  'bg-emerald-400',
  'bg-sky-400',
  'bg-violet-400',
  'bg-pink-400',
]

const getSlotDotClassName = (index: number) => DOT_CLASS_NAMES[index % DOT_CLASS_NAMES.length]

type MealSelectionState = Record<string, Set<number>>

const createEmptyMealDishIds = (slots: MealSlot[]): MealDishIds => {
  const next: MealDishIds = {}
  slots.forEach((slot) => {
    next[slot.key] = []
  })
  return next
}

const createEmptyMealSelections = (slots: MealSlot[]): MealSelectionState => {
  const next: MealSelectionState = {}
  slots.forEach((slot) => {
    next[slot.key] = new Set<number>()
  })
  return next
}

const normalizeMealDishIds = (
  slots: MealSlot[],
  value?: Partial<Record<string, number[]>> | null,
): MealDishIds => {
  const next = createEmptyMealDishIds(slots)

  slots.forEach((slot) => {
    const ids = Array.isArray(value?.[slot.key]) ? value?.[slot.key] || [] : []
    next[slot.key] = ids
  })

  return next
}

const toMealSelections = (slots: MealSlot[], mealDishIds: MealDishIds): MealSelectionState => {
  const next: MealSelectionState = {}
  slots.forEach((slot) => {
    next[slot.key] = new Set(mealDishIds[slot.key] || [])
  })
  return next
}

const serializeMealSelections = (slots: MealSlot[], value: MealSelectionState): MealDishIds => {
  const next: MealDishIds = {}
  slots.forEach((slot) => {
    next[slot.key] = Array.from(value[slot.key] || [])
  })
  return next
}

const countUniqueSelectedDishes = (slots: MealSlot[], value: MealSelectionState) => {
  const selectedIds = new Set<number>()
  slots.forEach((slot) => {
    value[slot.key]?.forEach((dishId) => selectedIds.add(dishId))
  })
  return selectedIds.size
}

export default function MenusPage() {
  const [selectedDate, setSelectedDate] = useState(new Date())
  const [allDishes, setAllDishes] = useState<Dish[]>([])
  const [mealSlots, setMealSlots] = useState<MealSlot[]>(DEFAULT_MEAL_SLOTS)
  const [selectedByMeal, setSelectedByMeal] = useState<MealSelectionState>(createEmptyMealSelections(DEFAULT_MEAL_SLOTS))
  const [activeMeal, setActiveMeal] = useState<string>(DEFAULT_MEAL_SLOTS[0]?.key || '')
  const [isDefault, setIsDefault] = useState(true)
  const [loading, setLoading] = useState(false)
  const [loadingDishes, setLoadingDishes] = useState(false)
  const [loadingSlots, setLoadingSlots] = useState(true)
  const [saving, setSaving] = useState(false)
  const [search, setSearch] = useState('')
  const [weekStart, setWeekStart] = useState(startOfWeek(new Date(), { weekStartsOn: 1 }))

  useEffect(() => {
    void loadMealSlots()
    void loadAllDishes()
  }, [])

  useEffect(() => {
    void loadMenu()
  }, [selectedDate, mealSlots])

  const loadMealSlots = async () => {
    setLoadingSlots(true)
    try {
      const res = await adminApi.config()
      const slots = Array.isArray(res.data.data.meal_slots) && res.data.data.meal_slots.length > 0
        ? res.data.data.meal_slots
        : DEFAULT_MEAL_SLOTS
      setMealSlots(slots)
      setSelectedByMeal(createEmptyMealSelections(slots))
      setActiveMeal(slots[0]?.key || '')
    } finally {
      setLoadingSlots(false)
    }
  }

  const loadAllDishes = async () => {
    setLoadingDishes(true)
    try {
      const items: Dish[] = []
      let page = 1
      let totalPages = 1

      do {
        const res = await dishApi.list({ active_only: 'true', page, page_size: DISH_PAGE_SIZE })
        const data = res.data.data
        items.push(...(data.items || []))
        totalPages = Math.max(1, Number(data.total_pages || 1))
        page += 1
      } while (page <= totalPages)

      setAllDishes(items)
    } finally {
      setLoadingDishes(false)
    }
  }

  const loadMenu = async () => {
    setLoading(true)
    const dateStr = format(selectedDate, 'yyyy-MM-dd')
    try {
      const res = await menuApi.get(dateStr)
      const menu = res.data.data
      const normalizedMealDishIds = normalizeMealDishIds(mealSlots, menu.meal_dish_ids)
      setSelectedByMeal(toMealSelections(mealSlots, normalizedMealDishIds))
      setIsDefault(Boolean(menu.is_default))
    } finally {
      setLoading(false)
    }
  }

  const toggleDish = (dishId: number) => {
    setSelectedByMeal((prev) => {
      const next = {
        ...prev,
        [activeMeal]: new Set(prev[activeMeal]),
      }
      if (next[activeMeal].has(dishId)) next[activeMeal].delete(dishId)
      else next[activeMeal].add(dishId)
      return next
    })
    setIsDefault(false)
  }

  const normalizedSearch = search.trim().toLowerCase()
  const visibleDishes = normalizedSearch
    ? allDishes.filter((dish) =>
      dish.name.toLowerCase().includes(normalizedSearch) ||
      dish.category.toLowerCase().includes(normalizedSearch),
    )
    : allDishes

  const selectAll = () => {
    setSelectedByMeal((prev) => {
      const next = {
        ...prev,
        [activeMeal]: new Set(prev[activeMeal]),
      }
      visibleDishes.forEach((dish) => next[activeMeal].add(dish.id))
      return next
    })
    setIsDefault(false)
  }

  const clearAll = () => {
    setSelectedByMeal((prev) => {
      const next = {
        ...prev,
        [activeMeal]: new Set(prev[activeMeal]),
      }
      if (!normalizedSearch) {
        next[activeMeal].clear()
        return next
      }
      visibleDishes.forEach((dish) => next[activeMeal].delete(dish.id))
      return next
    })
    setIsDefault(false)
  }

  const save = async () => {
    setSaving(true)
    const dateStr = format(selectedDate, 'yyyy-MM-dd')
    try {
      await menuApi.upsert(dateStr, {
        meal_dish_ids: serializeMealSelections(mealSlots, selectedByMeal),
      })
      toast.success(`${dateStr} 菜单已保存`)
      void loadMenu()
    } finally {
      setSaving(false)
    }
  }

  const weekDays = Array.from({ length: 7 }, (_, index) => addDays(weekStart, index))
  const byCategory = visibleDishes.reduce<Record<string, Dish[]>>((acc, dish) => {
    if (!acc[dish.category]) acc[dish.category] = []
    acc[dish.category].push(dish)
    return acc
  }, {})
  const dishById = new Map(allDishes.map((dish) => [dish.id, dish]))
  const currentMealSelection = selectedByMeal[activeMeal] || new Set<number>()
  const activeMealMeta = mealSlots.find((item) => item.key === activeMeal) || mealSlots[0]
  const activeMealDotClassName = activeMealMeta
    ? getSlotDotClassName(mealSlots.findIndex((slot) => slot.key === activeMealMeta.key))
    : DOT_CLASS_NAMES[0]
  const currentMealSelectedDishes = Array.from(currentMealSelection)
    .map((dishId) => dishById.get(dishId))
    .filter((dish): dish is Dish => Boolean(dish))
  const selectedVisibleCount = visibleDishes.filter((dish) => currentMealSelection.has(dish.id)).length
  const totalSelectedCount = countUniqueSelectedDishes(mealSlots, selectedByMeal)

  return (
    <div className="p-4 sm:p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">菜单管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            为每日分别配置
            {mealSlots.map((slot) => slot.label).join('、')}
          </p>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 mb-5">
        <div className="flex items-center justify-between mb-3">
          <button onClick={() => setWeekStart((value) => subDays(value, 7))} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm font-medium">
            {format(weekStart, 'yyyy年M月', { locale: zhCN })}
          </span>
          <button onClick={() => setWeekStart((value) => addDays(value, 7))} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {weekDays.map((day) => {
            const selected = isSameDay(day, selectedDate)
            const today = isToday(day)
            return (
              <button
                key={day.toString()}
                onClick={() => setSelectedDate(day)}
                className={cn(
                  'flex flex-col items-center py-2 rounded-lg transition-colors',
                  selected ? 'bg-primary text-primary-foreground' : 'hover:bg-secondary',
                  !selected && today ? 'border border-foreground/30' : '',
                )}
              >
                <span className="text-[10px] uppercase tracking-wider opacity-60 mb-1">
                  {format(day, 'EEE', { locale: zhCN })}
                </span>
                <span className="text-sm font-mono">{format(day, 'd')}</span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div>
            <h2 className="text-sm font-medium">{format(selectedDate, 'yyyy年M月d日', { locale: zhCN })} 菜单</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {isDefault
                ? '当前未配置菜单，视频分析会停止并生成告警'
                : `共 ${mealSlots.length} 餐合计已选 ${totalSelectedCount} 个去重菜品`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={clearAll} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1.5 rounded-md hover:bg-secondary transition-colors">
              <RotateCcw className="w-3 h-3" />
              {normalizedSearch ? `清空${activeMealMeta?.label || ''}搜索结果` : `清空${activeMealMeta?.label || ''}`}
            </button>
            <button onClick={selectAll} className="text-xs text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-md hover:bg-secondary transition-colors">
              {normalizedSearch ? `全选${activeMealMeta?.label || ''}搜索结果` : `全选${activeMealMeta?.label || ''}`}
            </button>
            <button
              onClick={save}
              disabled={saving || loading || loadingDishes || loadingSlots}
              className="flex items-center gap-1.5 text-sm bg-primary text-primary-foreground px-4 py-1.5 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              {saving ? '保存中...' : '保存菜单'}
            </button>
          </div>
        </div>

        {loading || loadingDishes || loadingSlots ? (
          <div className="p-12 text-center text-sm text-muted-foreground">加载中...</div>
        ) : (
          <div className="p-4 space-y-5">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {mealSlots.map((slot, index) => {
                const selected = slot.key === activeMeal
                const count = selectedByMeal[slot.key]?.size || 0
                const dotClassName = getSlotDotClassName(index)
                return (
                  <button
                    key={slot.key}
                    onClick={() => setActiveMeal(slot.key)}
                    className={cn(
                      'rounded-xl border p-3 text-left transition-all',
                      selected
                        ? 'border-primary bg-primary/5 shadow-sm'
                        : 'border-border hover:border-primary/30 hover:bg-secondary/40',
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={cn('w-2.5 h-2.5 rounded-full', dotClassName)} />
                        <span className="text-sm font-medium">{slot.label}</span>
                      </div>
                      <span className="text-xs font-mono text-muted-foreground">{count} 项</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{slot.start}-{slot.end}</p>
                  </button>
                )
              })}
            </div>

            <div className="rounded-xl border border-border bg-secondary/20 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className={cn('w-2.5 h-2.5 rounded-full', activeMealDotClassName)} />
                    <span className="text-sm font-medium">当前编辑：{activeMealMeta?.label || ''}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {currentMealSelection.size === 0
                      ? `${activeMealMeta?.label || ''}暂未配置菜品`
                      : `${activeMealMeta?.label || ''}已选 ${currentMealSelection.size} 个菜品`}
                  </p>
                  {currentMealSelectedDishes.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {currentMealSelectedDishes.map((dish) => (
                        <span
                          key={dish.id}
                          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1 text-xs text-foreground shadow-sm"
                        >
                          <span className={cn('h-2 w-2 rounded-full', activeMealDotClassName)} />
                          <span className="truncate">{dish.name}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="text-right text-xs text-muted-foreground">
                  <div>日期 {format(selectedDate, 'MM-dd', { locale: zhCN })}</div>
                  <div>{activeMealMeta?.start}-{activeMealMeta?.end}</div>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:max-w-xs">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`搜索${activeMealMeta?.label || ''}菜品名称或分类...`}
                  className="w-full rounded-lg border border-border bg-card py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                当前显示 {visibleDishes.length} / {allDishes.length} 个菜品
                {normalizedSearch ? `，${activeMealMeta?.label || ''}已选 ${selectedVisibleCount} 个搜索结果` : ''}
              </p>
            </div>

            {visibleDishes.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                没有匹配的菜品
              </div>
            ) : Object.entries(byCategory).map(([category, dishes]) => (
              <div key={category}>
                <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
                  <span className={cn('w-2 h-2 rounded-full',
                    category === '主食' ? 'bg-amber-400' :
                    category === '荤菜' ? 'bg-red-400' :
                    category === '素菜' ? 'bg-green-400' :
                    category === '汤' ? 'bg-blue-400' : 'bg-gray-400',
                  )} />
                  {category}
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                  {dishes.map((dish) => {
                    const selected = currentMealSelection.has(dish.id)
                    return (
                      <button
                        key={dish.id}
                        onClick={() => toggleDish(dish.id)}
                        className={cn(
                          'flex items-center gap-2 p-2.5 rounded-lg border text-left transition-all',
                          selected
                            ? 'border-primary/30 bg-primary/5'
                            : 'border-border hover:border-primary/20',
                        )}
                      >
                        <div className={cn(
                          'w-4 h-4 rounded flex-shrink-0 border transition-colors flex items-center justify-center',
                          selected ? 'bg-primary border-primary' : 'border-border',
                        )}>
                          {selected && (
                            <svg className="w-2.5 h-2.5 text-background" viewBox="0 0 10 10">
                              <path d="M2 5l2.5 2.5L8 3" stroke="currentColor" strokeWidth="1.5" fill="none" />
                            </svg>
                          )}
                        </div>
                        <div className="min-w-0">
                          <div className="text-xs font-medium truncate">{dish.name}</div>
                          <div className="text-[10px] text-muted-foreground font-mono">¥{dish.price.toFixed(2)}</div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
