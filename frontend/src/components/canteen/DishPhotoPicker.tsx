import { useRef, type ChangeEventHandler } from 'react'
import { Camera, ImagePlus, X } from 'lucide-react'
import type { DishSampleImage } from '@/types'

export type PhotoDraft = { id: string; file: File; previewUrl: string; edited?: boolean }

type Props = {
  pending: PhotoDraft[]
  existing?: DishSampleImage[]
  disabled?: boolean
  onSelect: ChangeEventHandler<HTMLInputElement>
  onRemove: (id: string) => void
  onDelete?: (id: number) => void
  deletingId?: number | null
  onCropPending?: (id: string) => void
  onCropExisting?: (photo: DishSampleImage) => void
}

export function DishPhotoPicker({ pending, existing = [], disabled, onSelect, onRemove, onDelete, deletingId, onCropPending, onCropExisting }: Props) {
  const cameraRef = useRef<HTMLInputElement>(null)
  const albumRef = useRef<HTMLInputElement>(null)
  const full = pending.length + existing.length >= 12

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium">菜品照片</span>
        <span className="text-xs text-muted-foreground">{pending.length + existing.length} / 12 张</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <button type="button" onClick={() => cameraRef.current?.click()} disabled={disabled || full} className="canteen-button border border-primary/25 bg-primary/10 text-primary">
          <Camera className="h-5 w-5" />拍照
        </button>
        <button type="button" onClick={() => albumRef.current?.click()} disabled={disabled || full} className="canteen-button border border-border bg-card">
          <ImagePlus className="h-5 w-5" />从相册选
        </button>
        <input ref={cameraRef} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={onSelect} disabled={disabled || full} className="hidden" aria-label="拍摄菜品照片" />
        <input ref={albumRef} type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={onSelect} disabled={disabled || full} className="hidden" aria-label="选择菜品照片" />
      </div>
      {(existing.length > 0 || pending.length > 0) && (
        <div className="grid grid-cols-3 gap-3">
          {existing.map((photo) => (
            <div key={photo.id} className="relative overflow-hidden rounded-xl border border-border bg-secondary">
              {photo.image_url && <img src={photo.image_url} alt="已上传的菜品照片" className="aspect-square w-full object-cover" loading="lazy" />}
              <span className="block px-2 py-1.5 text-xs text-muted-foreground">已上传</span>
              {onCropExisting && <button type="button" onClick={() => onCropExisting(photo)} disabled={disabled || !photo.image_url} className="min-h-11 w-full border-t border-border text-sm">裁剪</button>}
              {onDelete && <button type="button" onClick={() => { if (window.confirm('删除这张菜品照片？')) onDelete(photo.id) }} disabled={disabled || deletingId != null} aria-label={`删除照片 ${photo.id}`} className="canteen-icon-button absolute right-0 top-0 bg-white/95 text-foreground"><X className="h-4 w-4" /></button>}
            </div>
          ))}
          {pending.map((photo, index) => (
            <div key={photo.id} className="relative overflow-hidden rounded-xl border border-primary/30 bg-primary/5">
              <img src={photo.previewUrl} alt={`待上传照片 ${index + 1}`} className="aspect-square w-full object-cover" />
              <span className="block px-2 py-1.5 text-xs text-primary">待保存</span>
              {onCropPending && <button type="button" onClick={() => onCropPending(photo.id)} disabled={disabled} className="min-h-11 w-full border-t border-border text-sm">裁剪</button>}
              <button type="button" onClick={() => onRemove(photo.id)} disabled={disabled} aria-label={`移除待上传照片 ${index + 1}`} className="canteen-icon-button absolute right-0 top-0 bg-white/95 text-foreground"><X className="h-4 w-4" /></button>
            </div>
          ))}
        </div>
      )}
      <p className="text-xs leading-relaxed text-muted-foreground">拍清楚菜品全貌，尽量避免反光。支持 JPG、PNG、WebP。</p>
    </div>
  )
}
