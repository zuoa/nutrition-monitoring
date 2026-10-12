import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { addDays, format, isToday } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { CalendarDays, Camera, Check, ChevronLeft, ChevronRight, Copy, Loader2, Plus, Search, Utensils, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { dishApi } from '@/api/client'
import { cn } from '@/lib/utils'
import type { Dish, DishSampleImage, MealSlot } from '@/types'
import { CanteenSheet } from './CanteenSheet'
import { DishPhotoPicker, type PhotoDraft } from './DishPhotoPicker'

type PhotoTarget = { dish: Dish; meal: string; addAfterUpload: boolean }

type Props = {
  date: Date
  dishes: Dish[]
  mealSlots: MealSlot[]
  selectedByMeal: Record<string, Set<number>>
  activeMeal: string
  busy: boolean
  error: boolean
  dirty: boolean
  saving: boolean
  copying: boolean
  onDate: (date: Date) => void
  onMeal: (meal: string) => void
  onToggle: (id: number) => void
  onCopy: () => void
  onSave: () => void
  onCreate: (name: string) => void
  onRetry: () => void
  onPhotoSaved: (id: number, count: number, images: DishSampleImage[], meal?: string) => void
}

const coverForDish = (dish: Dish) => dish.sample_images?.find((image) => image.is_cover)?.image_url || dish.sample_images?.[0]?.image_url || dish.image_url

function DishThumbnail({ dish }: { dish: Dish }) {
  const cover = coverForDish(dish)
  return (
    <div className="flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-secondary text-muted-foreground">
      {cover ? <img src={cover} alt="" loading="lazy" className="h-full w-full object-cover" /> : <Utensils className="h-6 w-6" />}
    </div>
  )
}

function MenuPhotoCapture({ target, onClose, onSaved }: { target: PhotoTarget; onClose: () => void; onSaved: Props['onPhotoSaved'] }) {
  const [pending, setPending] = useState<PhotoDraft[]>([])
  const [uploading, setUploading] = useState(false)
  const [failed, setFailed] = useState(false)
  const pendingRef = useRef(pending)
  useEffect(() => { pendingRef.current = pending }, [pending])
  useEffect(() => () => pendingRef.current.forEach((photo) => URL.revokeObjectURL(photo.previewUrl)), [])

  const selectPhotos = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (!files.length) return
    if (files.some((file) => !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) {
      toast.error('请选择 JPG、PNG 或 WebP 照片')
      return
    }
    if ((target.dish.sample_image_count || 0) + pending.length + files.length > 12) {
      toast.error('每道菜最多 12 张照片，请减少选择数量')
      return
    }
    setPending((current) => [...current, ...files.map((file) => ({ id: crypto.randomUUID(), file, previewUrl: URL.createObjectURL(file) }))])
    setFailed(false)
  }

  const upload = async () => {
    if (uploading || !pending.length) return
    setUploading(true)
    setFailed(false)
    try {
      const response = await dishApi.uploadImages(target.dish.id, pending.map((photo) => photo.file))
      const data = response.data.data
      onSaved(target.dish.id, data.sample_image_count, data.images, target.addAfterUpload ? target.meal : undefined)
      toast.success(target.addAfterUpload ? '照片已上传，菜品已加入菜单' : '照片已上传')
      onClose()
    } catch {
      setFailed(true)
    } finally {
      setUploading(false)
    }
  }

  return (
    <CanteenSheet open onClose={() => { if (!pending.length || window.confirm('照片还未上传，放弃这些照片？')) onClose() }} busy={uploading}
      title={`给「${target.dish.name}」补照片`} description={target.addAfterUpload ? '拍照或从相册选择，上传后自动加入当前餐次。' : '上传后直接回到菜单，已选菜品会保留。'}
      footer={<button onClick={() => void upload()} disabled={uploading || !pending.length} className="canteen-button w-full bg-primary text-primary-foreground">{uploading && <Loader2 className="h-5 w-5 animate-spin" />}{uploading ? '正在上传…' : failed ? '重新上传' : target.addAfterUpload ? '上传并加入菜单' : '上传照片'}</button>}>
      <DishPhotoPicker pending={pending} existing={target.dish.sample_images} disabled={uploading} onSelect={selectPhotos}
        onRemove={(id) => setPending((current) => { const photo = current.find((item) => item.id === id); if (photo) URL.revokeObjectURL(photo.previewUrl); return current.filter((item) => item.id !== id) })} />
      {failed && <p role="alert" className="mt-4 text-sm text-destructive">上传未完成，照片已保留，请重试。</p>}
    </CanteenSheet>
  )
}

export function MobileMenu(props: Props) {
  const { date, dishes, mealSlots, selectedByMeal, activeMeal, busy, error, dirty, saving, copying, onDate, onMeal, onToggle, onCopy, onSave, onCreate, onRetry, onPhotoSaved } = props
  const [adding, setAdding] = useState(false)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [photoTarget, setPhotoTarget] = useState<PhotoTarget | null>(null)
  const selected = selectedByMeal[activeMeal] || new Set<number>()
  const dishById = new Map(dishes.map((dish) => [dish.id, dish]))
  const selectedDishes = Array.from(selected).map((id) => dishById.get(id)).filter((dish): dish is Dish => Boolean(dish))
  const unavailableIds = Array.from(selected).filter((id) => !dishById.has(id))
  const meal = mealSlots.find((slot) => slot.key === activeMeal)
  const normalizedSearch = search.trim().toLowerCase()
  const visibleDishes = dishes.filter((dish) => (!category || dish.category === category) && (!normalizedSearch || dish.name.toLowerCase().includes(normalizedSearch)))
  const categories = Array.from(new Set(dishes.map((dish) => dish.category)))
  const totalCount = Object.values(selectedByMeal).reduce((count, ids) => count + ids.size, 0)
  const missingCount = dishes.filter((dish) => !dish.sample_image_count && Object.values(selectedByMeal).some((ids) => ids.has(dish.id))).length
  const disabled = busy || saving || copying

  const toggle = (dish: Dish) => {
    if (!selected.has(dish.id) && !dish.sample_image_count) {
      setPhotoTarget({ dish, meal: activeMeal, addAfterUpload: true })
    } else onToggle(dish.id)
  }

  return (
    <div className="canteen-mobile mx-auto max-w-2xl px-4 pb-28 pt-5">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{isToday(date) ? '今日菜单' : '每日菜单'}</h1>
          <p className="mt-1 text-sm text-muted-foreground">选好每餐菜品，保存即可。</p>
        </div>
        {!isToday(date) && <button onClick={() => onDate(new Date())} disabled={disabled} className="canteen-button border border-border bg-card px-4 text-sm">回到今天</button>}
      </div>

      <div className="mb-4 flex items-center justify-between rounded-2xl border border-border bg-card p-1.5">
        <button onClick={() => onDate(addDays(date, -1))} disabled={disabled} aria-label="前一天" className="canteen-icon-button"><ChevronLeft className="h-5 w-5" /></button>
        <label className="relative flex min-h-12 items-center justify-center gap-2 rounded-lg px-2 font-medium focus-within:ring-2 focus-within:ring-primary">
          <CalendarDays className="h-4 w-4 text-muted-foreground" />
          {format(date, 'M月d日 EEEE', { locale: zhCN })}
          <input type="date" value={format(date, 'yyyy-MM-dd')} onChange={(event) => { if (event.target.value) onDate(new Date(`${event.target.value}T00:00:00`)) }} disabled={disabled} aria-label="选择菜单日期" className="absolute inset-0 h-full w-full cursor-pointer opacity-0" />
        </label>
        <button onClick={() => onDate(addDays(date, 1))} disabled={disabled} aria-label="后一天" className="canteen-icon-button"><ChevronRight className="h-5 w-5" /></button>
      </div>

      <div className="mb-5 flex gap-1 overflow-x-auto rounded-2xl bg-secondary p-1" aria-label="选择餐次">
        {mealSlots.map((slot) => <button key={slot.key} onClick={() => onMeal(slot.key)} disabled={disabled} aria-pressed={activeMeal === slot.key} className={cn('flex min-h-12 min-w-[3.5rem] flex-1 items-center justify-center gap-1 whitespace-nowrap rounded-xl px-2 text-sm font-medium transition-colors', activeMeal === slot.key ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground')}>
          {slot.label}<span className={cn('text-xs', activeMeal === slot.key ? 'text-primary' : 'text-muted-foreground')}>{busy ? '—' : selectedByMeal[slot.key]?.size || 0}</span>
        </button>)}
      </div>

      {busy ? (
        <div className="rounded-2xl border border-border bg-card p-6 text-center" role={error ? 'alert' : 'status'}>
          {error ? <><p className="text-sm">菜单暂时没有加载成功</p><button onClick={onRetry} className="canteen-button mx-auto mt-3 border border-border px-5">重新加载</button></> : <><Loader2 className="mx-auto h-6 w-6 animate-spin text-primary" /><p className="mt-3 text-sm text-muted-foreground">正在加载菜单…</p></>}
        </div>
      ) : (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="font-semibold">{meal?.label} <span className="ml-1 text-sm font-normal text-muted-foreground">{selected.size} 道菜</span></h2>
            <button onClick={onCopy} disabled={disabled} className="canteen-button gap-1.5 px-2 text-sm text-primary"><Copy className="h-4 w-4" />{copying ? '沿用中…' : '沿用昨天全天'}</button>
          </div>
          {selected.size === 0 ? (
            <div className="rounded-2xl border border-dashed border-border bg-card px-5 py-8 text-center">
              <Utensils className="mx-auto mb-3 h-8 w-8 text-primary/60" />
              <p className="font-medium">{meal?.label}还没有安排菜品</p>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">添加今天要做的菜，也可以沿用昨天的菜单。</p>
              <button onClick={() => setAdding(true)} disabled={disabled} className="canteen-button mx-auto mt-5 bg-primary px-6 text-primary-foreground"><Plus className="h-5 w-5" />添加菜品</button>
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-border bg-card">
              {selectedDishes.map((dish) => (
                <article key={dish.id} className="flex items-center gap-3 border-b border-border/70 p-3 last:border-b-0">
                  <DishThumbnail dish={dish} />
                  <div className="min-w-0 flex-1">
                    <h3 className="break-words font-medium">{dish.name}</h3>
                    <p className="mt-1 text-xs text-muted-foreground">{dish.category} · ¥{dish.price.toFixed(2)}{dish.resident_meal_slots?.includes(activeMeal) ? ' · 常驻' : ''}</p>
                    {!dish.sample_image_count && <button onClick={() => setPhotoTarget({ dish, meal: activeMeal, addAfterUpload: false })} disabled={disabled} className="mt-1 inline-flex min-h-11 items-center gap-1 text-sm text-amber-700"><Camera className="h-4 w-4" />补拍照片后可保存</button>}
                  </div>
                  <button onClick={() => onToggle(dish.id)} disabled={disabled} aria-label={`移除${dish.name}`} className="canteen-icon-button shrink-0 text-muted-foreground"><X className="h-5 w-5" /></button>
                </article>
              ))}
              {unavailableIds.map((id) => <div key={id} className="flex items-center justify-between gap-2 p-3 text-sm text-destructive"><span>菜品 #{id} 已停用或不存在</span><button onClick={() => onToggle(id)} disabled={disabled} className="canteen-button px-3">移除</button></div>)}
            </div>
          )}
          {selected.size > 0 && <button onClick={() => setAdding(true)} disabled={disabled} className="canteen-button mt-3 w-full border border-primary/25 bg-primary/5 text-primary"><Plus className="h-5 w-5" />添加菜品</button>}
          {missingCount > 0 && <p role="alert" className="mt-4 text-sm leading-relaxed text-amber-700">全天菜单有 {missingCount} 道菜缺照片，补拍或移除后即可保存。</p>}
        </>
      )}

      <div className="canteen-save-bar fixed inset-x-0 z-30 border-t border-border bg-card px-4 py-3">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-4" aria-live="polite">
          <div className="min-w-0">
            <p className="text-sm font-medium">{saving ? '正在保存…' : busy ? '正在加载' : dirty ? '有修改待保存' : '菜单已保存'}</p>
            <p className="mt-1 whitespace-nowrap text-xs text-muted-foreground">{format(date, 'M/d')}{busy ? ' · 请稍候' : ` · 全天 ${totalCount} 道`}</p>
          </div>
          <button onClick={onSave} disabled={disabled || !dirty || missingCount > 0} className="canteen-button min-w-36 bg-primary px-6 text-primary-foreground">{saving ? <Loader2 className="h-5 w-5 animate-spin" /> : !dirty && !busy ? <Check className="h-5 w-5" /> : null}{saving ? '保存中…' : !dirty && !busy ? '已保存' : '保存全天菜单'}</button>
        </div>
      </div>

      <CanteenSheet open={adding && !photoTarget} onClose={() => setAdding(false)} title={`为${meal?.label || ''}添加菜品`} description="点选菜品；缺照片的菜可以直接补拍。"
        footer={<button onClick={() => setAdding(false)} className="canteen-button w-full bg-primary text-primary-foreground">完成选菜 · 已选 {selected.size} 道</button>}>
        <div className="relative mb-3">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索菜名" aria-label="搜索菜名" className="canteen-input !pl-10" />
        </div>
        <div className="mb-3 flex gap-2 overflow-x-auto pb-1" aria-label="菜品分类">
          {['', ...categories].map((item) => <button key={item} onClick={() => setCategory(item)} aria-pressed={category === item} className={cn('canteen-button shrink-0 rounded-full px-4 text-sm', category === item ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground')}>{item || '全部'}</button>)}
        </div>
        <div className="divide-y divide-border">
          {visibleDishes.map((dish) => {
            const checked = selected.has(dish.id)
            return <button key={dish.id} onClick={() => toggle(dish)} aria-pressed={checked} aria-label={`${checked ? '取消' : '添加'}${dish.name}`} className="flex w-full items-center gap-3 py-3 text-left">
              <DishThumbnail dish={dish} />
              <span className="min-w-0 flex-1"><span className="block break-words font-medium">{dish.name}</span><span className="mt-1 block text-xs text-muted-foreground">{dish.category} · ¥{dish.price.toFixed(2)}</span>{!dish.sample_image_count && <span className="mt-1 inline-flex items-center gap-1 text-xs text-amber-700"><Camera className="h-3.5 w-3.5" />先补拍，再添加</span>}</span>
              <span className={cn('flex h-8 w-8 shrink-0 items-center justify-center rounded-full', checked ? 'bg-primary text-primary-foreground' : 'bg-secondary text-primary')}>{checked ? <Check className="h-5 w-5" /> : <Plus className="h-5 w-5" />}</span>
            </button>
          })}
        </div>
        {visibleDishes.length === 0 && <div className="py-6 text-center"><p className="text-sm text-muted-foreground">{search ? '没有找到这道菜' : '暂无可选菜品'}</p><button onClick={() => onCreate(search.trim())} className="canteen-button mx-auto mt-3 border border-border px-5"><Plus className="h-4 w-4" />新增菜品</button></div>}
      </CanteenSheet>
      {photoTarget && <MenuPhotoCapture target={photoTarget} onClose={() => setPhotoTarget(null)} onSaved={onPhotoSaved} />}
    </div>
  )
}
