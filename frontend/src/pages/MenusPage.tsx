import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Save, RotateCcw } from 'lucide-react'
import { menuApi, dishApi } from '@/api/client'
import { cn } from '@/lib/utils'
import type { Dish, DishCategory } from '@/types'
import toast from 'react-hot-toast'
import { format, addDays, subDays, startOfWeek, isToday, isSameDay } from 'date-fns'
import { zhCN } from 'date-fns/locale'

const CATEGORY_COLORS: Record<string, string> = {
  '主食': 'border-amber-300 bg-amber-50',
  '荤菜': 'border-red-200 bg-red-50',
  '素菜': 'border-green-200 bg-green-50',
  '汤': 'border-blue-200 bg-blue-50',
  '其他': 'border-gray-200 bg-gray-50',
}

export default function MenusPage() {
  const [selectedDate, setSelectedDate] = useState(new Date())
  const [allDishes, setAllDishes] = useState<Dish[]>([])
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isDefault, setIsDefault] = useState(true)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [weekStart, setWeekStart] = useState(startOfWeek(new Date(), { weekStartsOn: 1 }))

  useEffect(() => {
    dishApi.list({ active_only: 'true', page_size: 100 }).then(res => {
      setAllDishes(res.data.data.items)
    })
  }, [])

  useEffect(() => { loadMenu() }, [selectedDate])

  const loadMenu = async () => {
    setLoading(true)
    const dateStr = format(selectedDate, 'yyyy-MM-dd')
    try {
      const res = await menuApi.get(dateStr)
      const menu = res.data.data
      setSelectedIds(new Set(menu.dish_ids || []))
      setIsDefault(menu.is_default)
    } finally {
      setLoading(false)
    }
  }

  const toggleDish = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
    setIsDefault(false)
  }

  const selectAll = () => { setSelectedIds(new Set(allDishes.map(d => d.id))); setIsDefault(false) }
  const clearAll = () => { setSelectedIds(new Set()); setIsDefault(false) }

  const save = async () => {
    setSaving(true)
    const dateStr = format(selectedDate, 'yyyy-MM-dd')
    try {
      await menuApi.upsert(dateStr, { dish_ids: Array.from(selectedIds) })
      toast.success(`${dateStr} 菜单已保存`)
      loadMenu()
    } finally {
      setSaving(false)
    }
  }

  const weekDays = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i))

  const byCategory = allDishes.reduce<Record<string, Dish[]>>((acc, d) => {
    if (!acc[d.category]) acc[d.category] = []
    acc[d.category].push(d)
    return acc
  }, {})

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-display">菜单管理</h1>
          <p className="text-sm text-muted-foreground mt-0.5">为每日配置供应菜品</p>
        </div>
      </div>

      {/* Week calendar */}
      <div className="bg-card border border-border rounded-xl p-4 mb-5">
        <div className="flex items-center justify-between mb-3">
          <button onClick={() => setWeekStart(s => subDays(s, 7))} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
            <ChevronLeft className="w-4 h-4" />
          </button>
          <span className="text-sm font-medium">
            {format(weekStart, 'yyyy年M月', { locale: zhCN })}
          </span>
          <button onClick={() => setWeekStart(s => addDays(s, 7))} className="p-1.5 hover:bg-secondary rounded-md transition-colors">
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
        <div className="grid grid-cols-7 gap-1.5">
          {weekDays.map(day => {
            const isSelected = isSameDay(day, selectedDate)
            const isTodayDay = isToday(day)
            return (
              <button
                key={day.toString()}
                onClick={() => setSelectedDate(day)}
                className={cn(
                  'flex flex-col items-center py-2 rounded-lg transition-colors',
                  isSelected ? 'bg-foreground text-background' : 'hover:bg-secondary',
                  !isSelected && isTodayDay ? 'border border-foreground/30' : ''
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

      {/* Menu editor */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div>
            <h2 className="text-sm font-medium">{format(selectedDate, 'yyyy年M月d日', { locale: zhCN })} 菜单</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {isDefault ? '使用默认（全部菜品）' : `已选 ${selectedIds.size} 个菜品`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={clearAll} className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2 py-1.5 rounded-md hover:bg-secondary transition-colors">
              <RotateCcw className="w-3 h-3" />清空
            </button>
            <button onClick={selectAll} className="text-xs text-muted-foreground hover:text-foreground px-3 py-1.5 rounded-md hover:bg-secondary transition-colors">
              全选
            </button>
            <button
              onClick={save}
              disabled={saving || loading}
              className="flex items-center gap-1.5 text-sm bg-foreground text-background px-4 py-1.5 rounded-lg hover:bg-foreground/90 transition-colors disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              {saving ? '保存中...' : '保存菜单'}
            </button>
          </div>
        </div>

        {loading ? (
          <div className="p-12 text-center text-sm text-muted-foreground">加载中...</div>
        ) : (
          <div className="p-4 space-y-5">
            {Object.entries(byCategory).map(([cat, dishes]) => (
              <div key={cat}>
                <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-2">
                  <span className={cn('w-2 h-2 rounded-full',
                    cat === '主食' ? 'bg-amber-400' :
                    cat === '荤菜' ? 'bg-red-400' :
                    cat === '素菜' ? 'bg-green-400' :
                    cat === '汤' ? 'bg-blue-400' : 'bg-gray-400'
                  )} />
                  {cat}
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                  {dishes.map(dish => {
                    const selected = selectedIds.has(dish.id)
                    return (
                      <button
                        key={dish.id}
                        onClick={() => toggleDish(dish.id)}
                        className={cn(
                          'flex items-center gap-2 p-2.5 rounded-lg border text-left transition-all',
                          selected
                            ? 'border-foreground/30 bg-foreground/5'
                            : 'border-border hover:border-foreground/20'
                        )}
                      >
                        <div className={cn(
                          'w-4 h-4 rounded flex-shrink-0 border transition-colors flex items-center justify-center',
                          selected ? 'bg-foreground border-foreground' : 'border-border'
                        )}>
                          {selected && <svg className="w-2.5 h-2.5 text-background" viewBox="0 0 10 10"><path d="M2 5l2.5 2.5L8 3" stroke="currentColor" strokeWidth="1.5" fill="none" /></svg>}
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
