import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, RotateCcw, Save, Search } from 'lucide-react'
import { format, addDays, isSameDay, isToday, startOfWeek, subDays } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import toast from 'react-hot-toast'

import { dishApi, menuApi } from '@/api/client'
import { cn } from '@/lib/utils'
import type { Dish, MealDishIds, MealSlotKey } from '@/types'

const DISH_PAGE_SIZE = 100
const MEAL_SLOTS: Array<{
  key: MealSlotKey
  label: string
  description: string
  dotClassName: string
}> = [
  { key: 'breakfast', label: '早餐', description: '早间供应', dotClassName: 'bg-amber-400' },
  { key: 'lunch', label: '午餐', description: '中午正餐', dotClassName: 'bg-orange-400' },
  { key: 'dinner', label: '晚餐', description: '傍晚正餐', dotClassName: 'bg-rose-400' },
  { key: 'late_night', label: '宵夜', description: '夜间加餐', dotClassName: 'bg-indigo-400' },
]

type MealSelectionState = Record<MealSlotKey, Set<number>>

const createEmptyMealDishIds = (): MealDishIds => ({
  breakfast: [],
  lunch: [],
  dinner: [],
  late_night: [],
})

const createEmptyMealSelections = (): MealSelectionState => ({
  breakfast: new Set<number>(),
  lunch: new Set<number>(),
  dinner: new Set<number>(),
  late_night: new Set<number>(),
})

const normalizeMealDishIds = (
  value?: Partial<Record<MealSlotKey, number[]>> | null,
): MealDishIds => {
  const next = createEmptyMealDishIds()

  MEAL_SLOTS.forEach(({ key }) => {
    const ids = Array.isArray(value?.[key]) ? value?.[key] || [] : []
    next[key] = ids
  })

  return next
}

const toMealSelections = (mealDishIds: MealDishIds): MealSelectionState => ({
  breakfast: new Set(mealDishIds.breakfast || []),
  lunch: new Set(mealDishIds.lunch || []),
  dinner: new Set(mealDishIds.dinner || []),
  late_night: new Set(mealDishIds.late_night || []),
})

const serializeMealSelections = (value: MealSelectionState): MealDishIds => ({
  breakfast: Array.from(value.breakfast),
  lunch: Array.from(value.lunch),
  dinner: Array.from(value.dinner),
  late_night: Array.from(value.late_night),
})

const countUniqueSelectedDishes = (value: MealSelectionState) => {
  const selectedIds = new Set<number>()
  MEAL_SLOTS.forEach(({ key }) => {
    value[key].forEach((dishId) => selectedIds.add(dishId))
  })
  return selectedIds.size
}

export default function MenusPage() {
  const [selectedDate, setSelectedDate] = useState(new Date())
  const [allDishes, setAllDishes] = useState<Dish[]>([])
  const [selectedByMeal, setSelectedByMeal] = useState<MealSelectionState>(createEmptyMealSelections())
  const [activeMeal, setActiveMeal] = useState<MealSlotKey>('breakfast')
  const [isDefault, setIsDefault] = useState(true)
  const [loading, setLoading] = useState(false)
  const [loadingDishes, setLoadingDishes] = useState(false)
  const [saving, setSaving] = useState(false)
  const [search, setSearch] = useState('')
  const [weekStart, setWeekStart] = useState(startOfWeek(new Date(), { weekStartsOn: 1 }))

  useEffect(() => {
    void loadAllDishes()
  }, [])

  useEffect(() => {
    void loadMenu()
  }, [selectedDate])

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
      const normalizedMealDishIds = normalizeMealDishIds(menu.meal_dish_ids)
      setSelectedByMeal(toMealSelections(normalizedMealDishIds))
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
        meal_dish_ids: serializeMealSelections(selectedByMeal),
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
  const currentMealSelection = selectedByMeal[activeMeal]
  const activeMealMeta = MEAL_SLOTS.find((item) => item.key === activeMeal) || MEAL_SLOTS[0]
  const selectedVisibleCount = visibleDishes.filter((dish) => currentMealSelection.has(dish.id)).length
  const totalSelectedCount = countUniqueSelectedDishes(selectedByMeal)

  return (
    <div className="p-4 sm:p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">菜单管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">为每日分别配置早餐、午餐、晚餐、宵夜</p>
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
                ? '当前未配置餐次，系统会回退为全部启用菜品'
                : `四餐合计已选 ${totalSelectedCount} 个去重菜品`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={clearAll} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1.5 rounded-md hover:bg-secondary transition-colors">
              <RotateCcw className="w-3 h-3" />
              {normalizedSearch ? `清空${activeMealMeta.label}搜索结果` : `清空${activeMealMeta.label}`}
            </button>
            <button onClick={selectAll} className="text-xs text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-md hover:bg-secondary transition-colors">
              {normalizedSearch ? `全选${activeMealMeta.label}搜索结果` : `全选${activeMealMeta.label}`}
            </button>
            <button
              onClick={save}
              disabled={saving || loading || loadingDishes}
              className="flex items-center gap-1.5 text-sm bg-primary text-primary-foreground px-4 py-1.5 rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              {saving ? '保存中...' : '保存菜单'}
            </button>
          </div>
        </div>

        {loading || loadingDishes ? (
          <div className="p-12 text-center text-sm text-muted-foreground">加载中...</div>
        ) : (
          <div className="p-4 space-y-5">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {MEAL_SLOTS.map((slot) => {
                const selected = slot.key === activeMeal
                const count = selectedByMeal[slot.key].size
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
                        <span className={cn('w-2.5 h-2.5 rounded-full', slot.dotClassName)} />
                        <span className="text-sm font-medium">{slot.label}</span>
                      </div>
                      <span className="text-xs font-mono text-muted-foreground">{count} 项</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{slot.description}</p>
                  </button>
                )
              })}
            </div>

            <div className="rounded-xl border border-border bg-secondary/20 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className={cn('w-2.5 h-2.5 rounded-full', activeMealMeta.dotClassName)} />
                    <span className="text-sm font-medium">当前编辑：{activeMealMeta.label}</span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">
                    {currentMealSelection.size === 0
                      ? `${activeMealMeta.label}暂未配置菜品`
                      : `${activeMealMeta.label}已选 ${currentMealSelection.size} 个菜品`}
                  </p>
                </div>
                <div className="text-right text-xs text-muted-foreground">
                  <div>日期 {format(selectedDate, 'MM-dd', { locale: zhCN })}</div>
                  <div>{activeMealMeta.description}</div>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="relative w-full sm:max-w-xs">
                <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={`搜索${activeMealMeta.label}菜品名称或分类...`}
                  className="w-full rounded-lg border border-border bg-card py-2 pl-8 pr-3 text-sm focus:outline-none focus:ring-1 focus:ring-foreground/20"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                当前显示 {visibleDishes.length} / {allDishes.length} 个菜品
                {normalizedSearch ? `，${activeMealMeta.label}已选 ${selectedVisibleCount} 个搜索结果` : ''}
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
