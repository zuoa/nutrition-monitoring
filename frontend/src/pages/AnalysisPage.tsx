import { useEffect, useState, useRef } from 'react'
import { Play, RefreshCw, CheckCircle2, X, ChevronLeft, ChevronRight, Eye, Upload, FolderOpen, Sparkles } from 'lucide-react'
import { adminApi, analysisApi, dishApi } from '@/api/client'
import { fmtDateTime, cn, isLocalRecognitionMode } from '@/lib/utils'
import { useAuth } from '@/contexts/AuthContext'
import type { TaskLog, CapturedImage, Dish, ImageRegionProposal } from '@/types'
import toast from 'react-hot-toast'

const MIN_PREVIEW_SCALE = 1
const MAX_PREVIEW_SCALE = 4
const MIN_ANNOTATION_SCALE = 1
const MAX_ANNOTATION_SCALE = 4
const MIN_ANNOTATION_EDGE = 24

const STATUS_STYLE: Record<string, string> = {
  running: 'text-health-blue',
  success: 'text-health-green',
  failed: 'text-health-red',
  partial: 'text-health-amber',
  pending: 'text-muted-foreground',
  identified: 'text-health-blue',
  matched: 'text-health-green',
  error: 'text-health-red',
}

const STATUS_LABEL: Record<string, string> = {
  running: '运行中', success: '完成', failed: '失败', partial: '部分成功',
  pending: '待处理', identified: '已识别', matched: '已匹配', error: '错误',
}

const TASK_TYPE_LABEL: Record<string, string> = {
  nvr_download: 'NVR 下载',
  ai_recognition: 'AI 识别',
  report_gen: '报告生成',
  manual_upload: '手动上传',
  region_proposal: '菜区提议',
}

interface AnnotationBox {
  x1: number
  y1: number
  x2: number
  y2: number
  width: number
  height: number
}

interface ImageLayout {
  left: number
  top: number
  width: number
  height: number
  naturalWidth: number
  naturalHeight: number
}

interface AnnotationViewport {
  scale: number
  offsetX: number
  offsetY: number
}

const normalizeAnnotationBox = (x1: number, y1: number, x2: number, y2: number): AnnotationBox => {
  const left = Math.round(Math.min(x1, x2))
  const top = Math.round(Math.min(y1, y2))
  const right = Math.round(Math.max(x1, x2))
  const bottom = Math.round(Math.max(y1, y2))
  return {
    x1: left,
    y1: top,
    x2: right,
    y2: bottom,
    width: right - left,
    height: bottom - top,
  }
}

const resolveImageUrl = (img: Pick<CapturedImage, 'image_url' | 'image_path'>) => {
  if (img.image_url) return img.image_url
  if (!img.image_path) return ''

  const normalizedPath = img.image_path.replace(/\\/g, '/')
  if (normalizedPath.startsWith('http://') || normalizedPath.startsWith('https://') || normalizedPath.startsWith('/images/')) {
    return normalizedPath
  }
  const marker = '/data/images/'
  const markerIndex = normalizedPath.indexOf(marker)
  if (markerIndex >= 0) {
    return `/images/${normalizedPath.slice(markerIndex + marker.length)}`
  }
  return normalizedPath
}

export default function AnalysisPage() {
  const [tab, setTab] = useState<'tasks' | 'images'>('tasks')
  const [tasks, setTasks] = useState<TaskLog[]>([])
  const [images, setImages] = useState<CapturedImage[]>([])
  const [imagesTotal, setImagesTotal] = useState(0)
  const [imagePage, setImagePage] = useState(1)
  const [statusFilter, setStatusFilter] = useState('')
  const [loading, setLoading] = useState(false)
  const [today] = useState(new Date().toISOString().split('T')[0])
  const [reviewModal, setReviewModal] = useState<CapturedImage | null>(null)
  const [allDishes, setAllDishes] = useState<Dish[]>([])
  const [reviewDishIds, setReviewDishIds] = useState<number[]>([])
  const [saving, setSaving] = useState(false)
  const [recognizing, setRecognizing] = useState(false)
  const [describing, setDescribing] = useState(false)
  const [dishDescription, setDishDescription] = useState<string | null>(null)
  const [previewImageUrl, setPreviewImageUrl] = useState<string | null>(null)
  const [previewScale, setPreviewScale] = useState(1)
  const [annotationMode, setAnnotationMode] = useState(false)
  const [annotationTool, setAnnotationTool] = useState<'draw' | 'pan'>('draw')
  const [annotationDishId, setAnnotationDishId] = useState<number | ''>('')
  const [annotationDishKeyword, setAnnotationDishKeyword] = useState('')
  const [annotationDishOptions, setAnnotationDishOptions] = useState<Dish[]>([])
  const [annotationDishLoading, setAnnotationDishLoading] = useState(false)
  const [annotationDishDropdownOpen, setAnnotationDishDropdownOpen] = useState(false)
  const [annotationSelectedDish, setAnnotationSelectedDish] = useState<Dish | null>(null)
  const [annotationBox, setAnnotationBox] = useState<AnnotationBox | null>(null)
  const [annotationSaving, setAnnotationSaving] = useState(false)
  const [proposalLoading, setProposalLoading] = useState(false)
  const [proposalBackend, setProposalBackend] = useState<string | null>(null)
  const [proposalRegions, setProposalRegions] = useState<ImageRegionProposal[]>([])
  const [proposalTask, setProposalTask] = useState<TaskLog | null>(null)
  const [imageLayout, setImageLayout] = useState<ImageLayout | null>(null)
  const [annotationViewport, setAnnotationViewport] = useState<AnnotationViewport>({
    scale: MIN_ANNOTATION_SCALE,
    offsetX: 0,
    offsetY: 0,
  })
  const [recognitionMode, setRecognitionMode] = useState('')

  const { hasRole } = useAuth()
  const isAdmin = hasRole('admin')
  const localRecognitionModeEnabled = isLocalRecognitionMode(recognitionMode)

  // Task detail modal state
  const [taskDetailModal, setTaskDetailModal] = useState<TaskLog | null>(null)
  const [taskImages, setTaskImages] = useState<CapturedImage[]>([])
  const [taskImagesLoading, setTaskImagesLoading] = useState(false)

  // Upload video modal state
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadDate, setUploadDate] = useState(today)
  const [uploadTime, setUploadTime] = useState('12:00:00')
  const [uploadChannel, setUploadChannel] = useState('manual')
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const reviewImageFrameRef = useRef<HTMLDivElement>(null)
  const reviewImageElementRef = useRef<HTMLImageElement>(null)
  const annotationSurfaceRef = useRef<HTMLDivElement>(null)
  const annotationDishPickerRef = useRef<HTMLDivElement>(null)
  const annotationDragRef = useRef<{ startX: number; startY: number } | null>(null)
  const annotationPanRef = useRef<{ startX: number; startY: number; originX: number; originY: number } | null>(null)
  const activeReviewImageIdRef = useRef<number | null>(null)

  const loadTasks = async () => {
    setLoading(true)
    try {
      const res = await analysisApi.tasks({ task_type: 'manual_upload', page_size: 20 })
      setTasks(res.data.data.items)
    } finally { setLoading(false) }
  }

  const loadImages = async () => {
    setLoading(true)
    try {
      const params: Record<string, any> = { page: imagePage, page_size: 20 }
      if (statusFilter) params.status = statusFilter
      // 不再限制为今日，显示所有日期的图片
      const res = await analysisApi.images(params)
      setImages(res.data.data.items)
      setImagesTotal(res.data.data.total)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    if (tab === 'tasks') loadTasks()
    else loadImages()
  }, [tab, imagePage, statusFilter])

  useEffect(() => {
    activeReviewImageIdRef.current = reviewModal?.id ?? null
  }, [reviewModal?.id])

  useEffect(() => {
    dishApi.list({ active_only: 'true', page_size: 100 }).then(res => setAllDishes(res.data.data.items))
  }, [])
  useEffect(() => {
    adminApi.config().then((res) => {
      setRecognitionMode(String(res.data.data.dish_recognition_mode || ''))
    }).catch(() => {})
  }, [])
  useEffect(() => {
    if (!localRecognitionModeEnabled && annotationMode) {
      setAnnotationMode(false)
      clearAnnotation()
      resetAnnotationViewport()
      setAnnotationTool('draw')
      setAnnotationDishDropdownOpen(false)
      setProposalRegions([])
      setProposalBackend(null)
    }
  }, [annotationMode, localRecognitionModeEnabled])

  const triggerAnalysis = async () => {
    await analysisApi.triggerAnalysis(today)
    toast.success('已触发今日视频分析任务')
    loadTasks()
  }

  const retryTask = async (id: number) => {
    await analysisApi.retryTask(id)
    toast.success('已提交重试任务')
    loadTasks()
  }

  const openReview = (img: CapturedImage) => {
    setReviewModal(img)
    const current = img.recognitions?.filter(r => !r.is_low_confidence).map(r => r.dish_id).filter(Boolean) as number[]
    setReviewDishIds(current || [])
    setDishDescription(null)  // Reset description when opening new image
    setPreviewImageUrl(null)
    setPreviewScale(1)
    setAnnotationMode(false)
    setAnnotationTool('draw')
    setAnnotationDishId('')
    setAnnotationDishKeyword('')
    setAnnotationDishOptions([])
    setAnnotationDishLoading(false)
    setAnnotationDishDropdownOpen(false)
    setAnnotationSelectedDish(null)
    setAnnotationBox(null)
    setProposalLoading(false)
    setProposalBackend(null)
    setProposalRegions([])
    setProposalTask(null)
    setImageLayout(null)
    annotationDragRef.current = null
    annotationPanRef.current = null
    setAnnotationViewport({
      scale: MIN_ANNOTATION_SCALE,
      offsetX: 0,
      offsetY: 0,
    })
  }

  const openPreview = (imageUrl: string) => {
    if (!imageUrl) return
    setPreviewImageUrl(imageUrl)
    setPreviewScale(1)
  }

  const closePreview = () => {
    setPreviewImageUrl(null)
    setPreviewScale(1)
  }

  const updateReviewImageLayout = () => {
    const frame = reviewImageFrameRef.current
    const image = reviewImageElementRef.current
    if (!frame || !image || !image.naturalWidth || !image.naturalHeight) {
      setImageLayout(null)
      return
    }

    const frameWidth = frame.clientWidth
    const frameHeight = frame.clientHeight
    if (!frameWidth || !frameHeight) {
      setImageLayout(null)
      return
    }

    const scale = Math.min(frameWidth / image.naturalWidth, frameHeight / image.naturalHeight)
    const width = image.naturalWidth * scale
    const height = image.naturalHeight * scale

    setImageLayout({
      left: (frameWidth - width) / 2,
      top: (frameHeight - height) / 2,
      width,
      height,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
    })
  }

  const clearAnnotation = () => {
    setAnnotationBox(null)
    annotationDragRef.current = null
  }

  const clampAnnotationViewport = (
    nextViewport: AnnotationViewport,
    layout: ImageLayout | null = imageLayout,
  ): AnnotationViewport => {
    if (!layout || nextViewport.scale <= MIN_ANNOTATION_SCALE) {
      return {
        scale: MIN_ANNOTATION_SCALE,
        offsetX: 0,
        offsetY: 0,
      }
    }

    const minOffsetX = layout.width - layout.width * nextViewport.scale
    const minOffsetY = layout.height - layout.height * nextViewport.scale

    return {
      scale: nextViewport.scale,
      offsetX: Math.min(0, Math.max(minOffsetX, nextViewport.offsetX)),
      offsetY: Math.min(0, Math.max(minOffsetY, nextViewport.offsetY)),
    }
  }

  const resetAnnotationViewport = () => {
    annotationPanRef.current = null
    setAnnotationViewport({
      scale: MIN_ANNOTATION_SCALE,
      offsetX: 0,
      offsetY: 0,
    })
  }

  const applyProposal = (proposal: ImageRegionProposal) => {
    const { x1, y1, x2, y2 } = proposal.bbox
    setAnnotationBox(normalizeAnnotationBox(x1, y1, x2, y2))
  }

  const clearSelectedProposal = (proposal: ImageRegionProposal) => {
    setAnnotationBox((current) => {
      if (!current) return current
      const selected = (
        current.x1 === proposal.bbox.x1 &&
        current.y1 === proposal.bbox.y1 &&
        current.x2 === proposal.bbox.x2 &&
        current.y2 === proposal.bbox.y2
      )
      return selected ? null : current
    })
  }

  const generateAnnotationProposals = async () => {
    if (!reviewModal) return
    const requestedImageId = reviewModal.id
    setProposalLoading(true)
    try {
      const res = await analysisApi.proposeImageRegions(
        requestedImageId,
        {},
      )
      if (activeReviewImageIdRef.current !== requestedImageId) {
        return
      }
      setProposalRegions([])
      setProposalBackend(null)
      setProposalTask((res.data.data?.task || null) as TaskLog | null)
      toast.success('已提交菜区提议任务')
    } catch {
      if (activeReviewImageIdRef.current === requestedImageId) {
        setProposalLoading(false)
        setProposalTask(null)
      }
    }
  }

  useEffect(() => {
    if (!reviewModal || !proposalTask || proposalTask.status !== 'running') return

    const imageId = reviewModal.id
    let cancelled = false
    let timer: number | null = null

    const pollTask = async () => {
      try {
        const res = await analysisApi.task(proposalTask.id)
        if (cancelled || activeReviewImageIdRef.current !== imageId) return

        const nextTask = res.data.data as TaskLog
        const taskImageId = Number(nextTask.meta?.image_id || 0)
        if (taskImageId && taskImageId !== imageId) {
          setProposalLoading(false)
          return
        }
        setProposalTask(nextTask)

        if (nextTask.status === 'running') {
          timer = window.setTimeout(pollTask, 2000)
          return
        }

        setProposalLoading(false)

        if (nextTask.status === 'success') {
          const proposals = Array.isArray(nextTask.meta?.proposals)
            ? (nextTask.meta?.proposals as ImageRegionProposal[])
            : []
          setProposalRegions(proposals)
          setProposalBackend(typeof nextTask.meta?.backend === 'string' ? nextTask.meta.backend : null)
          toast.success(
            proposals.length > 0
              ? `已生成 ${proposals.length} 个菜区提议`
              : '未检测到明显菜区，可继续手动框选',
          )
          return
        }

        toast.error(nextTask.error_message || String(nextTask.meta?.status_text || '生成菜区提议失败'))
      } catch {
        if (cancelled || activeReviewImageIdRef.current !== imageId) return
        timer = window.setTimeout(pollTask, 3000)
      }
    }

    timer = window.setTimeout(pollTask, 1500)

    return () => {
      cancelled = true
      if (timer !== null) {
        window.clearTimeout(timer)
      }
    }
  }, [reviewModal?.id, proposalTask?.id, proposalTask?.status])

  const getNaturalPoint = (clientX: number, clientY: number) => {
    const surface = annotationSurfaceRef.current
    if (!surface || !imageLayout) return null

    const surfaceRect = surface.getBoundingClientRect()
    const viewport = clampAnnotationViewport(annotationViewport)
    const localX = clientX - surfaceRect.left
    const localY = clientY - surfaceRect.top
    const contentX = (localX - viewport.offsetX) / viewport.scale
    const contentY = (localY - viewport.offsetY) / viewport.scale

    const clampedX = Math.max(0, Math.min(imageLayout.width, contentX))
    const clampedY = Math.max(0, Math.min(imageLayout.height, contentY))

    return {
      x: (clampedX / imageLayout.width) * imageLayout.naturalWidth,
      y: (clampedY / imageLayout.height) * imageLayout.naturalHeight,
    }
  }

  const zoomAnnotationAtPoint = (nextScale: number, clientX?: number, clientY?: number) => {
    if (!imageLayout || !annotationSurfaceRef.current) return

    const normalizedScale = Math.min(
      MAX_ANNOTATION_SCALE,
      Math.max(MIN_ANNOTATION_SCALE, Number(nextScale.toFixed(2))),
    )
    const rect = annotationSurfaceRef.current.getBoundingClientRect()
    const anchorX = clientX === undefined ? rect.width / 2 : Math.max(0, Math.min(rect.width, clientX - rect.left))
    const anchorY = clientY === undefined ? rect.height / 2 : Math.max(0, Math.min(rect.height, clientY - rect.top))

    setAnnotationViewport((current) => {
      if (normalizedScale === current.scale) return current

      const contentX = (anchorX - current.offsetX) / current.scale
      const contentY = (anchorY - current.offsetY) / current.scale
      return clampAnnotationViewport({
        scale: normalizedScale,
        offsetX: anchorX - contentX * normalizedScale,
        offsetY: anchorY - contentY * normalizedScale,
      })
    })
  }

  const handleAnnotationPointerDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!annotationMode || !imageLayout || event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()

    if (annotationTool === 'pan') {
      if (annotationViewport.scale <= MIN_ANNOTATION_SCALE) return
      annotationPanRef.current = {
        startX: event.clientX,
        startY: event.clientY,
        originX: annotationViewport.offsetX,
        originY: annotationViewport.offsetY,
      }
      return
    }

    const point = getNaturalPoint(event.clientX, event.clientY)
    if (!point) return

    annotationDragRef.current = { startX: point.x, startY: point.y }
    setAnnotationBox(normalizeAnnotationBox(point.x, point.y, point.x, point.y))
  }

  const saveAnnotation = async () => {
    if (!reviewModal || !annotationBox || !annotationDishId) {
      toast.error('请先框选区域并选择菜品')
      return
    }
    if (annotationBox.width < MIN_ANNOTATION_EDGE || annotationBox.height < MIN_ANNOTATION_EDGE) {
      toast.error(`标注区域至少需要 ${MIN_ANNOTATION_EDGE}px × ${MIN_ANNOTATION_EDGE}px`)
      return
    }

    setAnnotationSaving(true)
    try {
      const res = await analysisApi.annotateImage(reviewModal.id, {
        dish_id: Number(annotationDishId),
        bbox: {
          x1: annotationBox.x1,
          y1: annotationBox.y1,
          x2: annotationBox.x2,
          y2: annotationBox.y2,
        },
      })
      toast.success(res.data.data?.message || `${res.data.data?.dish?.name || '目标菜品'} 标注已保存为样图`)
      setAnnotationBox(null)
      setAnnotationDishDropdownOpen(false)
    } finally {
      setAnnotationSaving(false)
    }
  }

  const handlePreviewWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()

    const delta = event.deltaY > 0 ? -0.2 : 0.2
    setPreviewScale((current) => {
      const next = current + delta
      return Math.min(MAX_PREVIEW_SCALE, Math.max(MIN_PREVIEW_SCALE, Number(next.toFixed(2))))
    })
  }

  useEffect(() => {
    if (!reviewModal || !annotationMode) return

    const handleMove = (event: MouseEvent) => {
      const pan = annotationPanRef.current
      if (pan) {
        const deltaX = event.clientX - pan.startX
        const deltaY = event.clientY - pan.startY
        setAnnotationViewport((current) => clampAnnotationViewport({
          scale: current.scale,
          offsetX: pan.originX + deltaX,
          offsetY: pan.originY + deltaY,
        }))
        return
      }

      const drag = annotationDragRef.current
      if (!drag) return
      const point = getNaturalPoint(event.clientX, event.clientY)
      if (!point) return
      setAnnotationBox(normalizeAnnotationBox(drag.startX, drag.startY, point.x, point.y))
    }

    const handleUp = () => {
      if (annotationPanRef.current) {
        annotationPanRef.current = null
      }

      const drag = annotationDragRef.current
      if (!drag) return
      annotationDragRef.current = null
      setAnnotationBox((current) => {
        if (!current) return null
        if (current.width < MIN_ANNOTATION_EDGE || current.height < MIN_ANNOTATION_EDGE) {
          return null
        }
        return current
      })
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (annotationDishDropdownOpen) {
          setAnnotationDishDropdownOpen(false)
          return
        }
        if (annotationBox) {
          clearAnnotation()
          return
        }
        if (annotationViewport.scale > MIN_ANNOTATION_SCALE) {
          resetAnnotationViewport()
        }
      }
    }

    const handleResize = () => updateReviewImageLayout()

    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('resize', handleResize)
    }
  }, [reviewModal, annotationMode, imageLayout, annotationDishDropdownOpen, annotationBox, annotationViewport.scale])

  useEffect(() => {
    if (!annotationMode) {
      setAnnotationDishDropdownOpen(false)
      return
    }

    const keyword = annotationDishKeyword.trim()
    if (!keyword) {
      setAnnotationDishOptions(allDishes.slice(0, 20))
      setAnnotationDishLoading(false)
      return
    }

    let cancelled = false
    setAnnotationDishLoading(true)
    const timer = window.setTimeout(async () => {
      try {
        const res = await dishApi.list({
          active_only: 'true',
          page: 1,
          page_size: 20,
          search: keyword,
        })
        if (!cancelled) {
          setAnnotationDishOptions(res.data.data.items)
        }
      } finally {
        if (!cancelled) {
          setAnnotationDishLoading(false)
        }
      }
    }, 250)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [annotationMode, annotationDishKeyword, allDishes])

  useEffect(() => {
    if (!annotationMode) return

    const handlePointerDown = (event: MouseEvent) => {
      if (!annotationDishPickerRef.current?.contains(event.target as Node)) {
        setAnnotationDishDropdownOpen(false)
      }
    }

    window.addEventListener('mousedown', handlePointerDown)
    return () => window.removeEventListener('mousedown', handlePointerDown)
  }, [annotationMode])

  const saveReview = async () => {
    if (!reviewModal) return
    setSaving(true)
    try {
      await analysisApi.reviewImage(reviewModal.id, reviewDishIds)
      toast.success('已保存人工复核结果')
      setReviewModal(null)
      loadImages()
    } finally { setSaving(false) }
  }

  const mergeImage = (updated: CapturedImage) => {
    setImages(prev => prev.map(img => img.id === updated.id ? updated : img))
    setTaskImages(prev => prev.map(img => img.id === updated.id ? updated : img))
    setReviewModal(prev => prev && prev.id === updated.id ? updated : prev)
  }

  const triggerSingleRecognition = async () => {
    if (!reviewModal) return
    setRecognizing(true)
    try {
      const res = await analysisApi.recognizeImage(reviewModal.id)
      const updated = res.data.data as CapturedImage
      mergeImage(updated)
      toast.success('已提交图片识别任务')
    } finally {
      setRecognizing(false)
    }
  }

  const generateDishDescription = async () => {
    if (!reviewModal) return
    setDescribing(true)
    try {
      const res = await analysisApi.describeImage(reviewModal.id)
      setDishDescription(res.data.data.description)
      toast.success('已生成菜品描述')
    } catch {
      // Error handled by interceptor
    } finally {
      setDescribing(false)
    }
  }

  // Open task detail modal and load associated images
  const openTaskDetail = async (task: TaskLog) => {
    setTaskDetailModal(task)
    setTaskImagesLoading(true)
    try {
      if (task.task_type === 'region_proposal' && task.meta?.image_id) {
        const res = await analysisApi.getImage(Number(task.meta.image_id))
        setTaskImages([res.data.data as CapturedImage])
      } else {
        const params: Record<string, any> = { page: 1, page_size: 100 }
        if (task.task_date) params.date = task.task_date
        const res = await analysisApi.images(params)
        setTaskImages(res.data.data.items)
      }
    } catch (err) {
      toast.error('加载任务图片失败')
    } finally {
      setTaskImagesLoading(false)
    }
  }

  const totalImagePages = Math.ceil(imagesTotal / 20)

  // Handle file selection
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      // Validate file type
      const allowedTypes = ['video/mp4', 'video/avi', 'video/quicktime', 'video/x-matroska', 'video/x-ms-wmv']
      const allowedExts = ['.mp4', '.avi', '.mov', '.mkv', '.wmv']
      const fileExt = '.' + file.name.split('.').pop()?.toLowerCase()

      if (!allowedTypes.includes(file.type) && !allowedExts.includes(fileExt)) {
        toast.error('不支持的文件格式，请上传 MP4/AVI/MOV/MKV/WMV 格式视频')
        return
      }

      // Parse filename for channel and time info
      // Format: {channel}_{YYYY-MM-DD-HH-MM-SS}.ext, e.g., 5_2026-03-25-11-35-12.mp4
      const baseName = file.name.replace(/\.[^/.]+$/, '')
      const parts = baseName.split('_')

      if (parts.length >= 2) {
        const channelId = parts[0]
        const timeStr = parts[1]

        // Set channel
        if (channelId) {
          setUploadChannel(channelId)
        }

        // Parse time: YYYY-MM-DD-HH-MM-SS
        const timeMatch = timeStr.match(/^(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})$/)
        if (timeMatch) {
          const [, year, month, day, hour, minute, second] = timeMatch
          setUploadDate(`${year}-${month}-${day}`)
          setUploadTime(`${hour}:${minute}:${second}`)
        }
      }

      setUploadFile(file)
    }
  }

  // Handle video upload
  const handleUpload = async () => {
    if (!uploadFile) {
      toast.error('请选择视频文件')
      return
    }
    if (!uploadDate || !uploadTime) {
      toast.error('请填写录像起始时间')
      return
    }

    const videoStartTime = `${uploadDate}T${uploadTime}`

    setUploading(true)
    try {
      const res = await analysisApi.uploadVideo(uploadFile, videoStartTime, uploadChannel)
      toast.success(res.data.data.message || '视频上传成功')
      setUploadModalOpen(false)
      setUploadFile(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      loadTasks()
    } catch (err) {
      // Error handled by interceptor
    } finally {
      setUploading(false)
    }
  }

  const hasManualRecognition = reviewModal?.recognitions?.some(r => r.is_manual) ?? false
  const canRerunRecognition = reviewModal ? ['pending', 'error', 'identified', 'matched'].includes(reviewModal.status) : false
  const hasRecognitionResult = (reviewModal?.recognitions?.length ?? 0) > 0
  const selectedAnnotationDish = typeof annotationDishId === 'number'
    ? annotationSelectedDish
      ?? annotationDishOptions.find(dish => dish.id === annotationDishId)
      ?? allDishes.find(dish => dish.id === annotationDishId)
      ?? null
    : null
  const annotationBoxStyle = annotationBox && imageLayout ? {
    left: `${(annotationBox.x1 / imageLayout.naturalWidth) * imageLayout.width}px`,
    top: `${(annotationBox.y1 / imageLayout.naturalHeight) * imageLayout.height}px`,
    width: `${(annotationBox.width / imageLayout.naturalWidth) * imageLayout.width}px`,
    height: `${(annotationBox.height / imageLayout.naturalHeight) * imageLayout.height}px`,
  } : undefined
  const proposalOverlays = imageLayout ? proposalRegions.map((proposal) => {
    const bbox = proposal.bbox
    const width = Math.max(0, bbox.x2 - bbox.x1)
    const height = Math.max(0, bbox.y2 - bbox.y1)
    return {
      proposal,
      selected: annotationBox
        ? annotationBox.x1 === bbox.x1 && annotationBox.y1 === bbox.y1 && annotationBox.x2 === bbox.x2 && annotationBox.y2 === bbox.y2
        : false,
      style: {
        left: `${(bbox.x1 / imageLayout.naturalWidth) * imageLayout.width}px`,
        top: `${(bbox.y1 / imageLayout.naturalHeight) * imageLayout.height}px`,
        width: `${(width / imageLayout.naturalWidth) * imageLayout.width}px`,
        height: `${(height / imageLayout.naturalHeight) * imageLayout.height}px`,
      },
    }
  }) : []

  return (
    <div className="p-4 sm:p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold">视频分析</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {localRecognitionModeEnabled ? '手动上传录像 · 图片复核标注' : '手动上传录像 · 图片复核'}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => tab === 'tasks' ? loadTasks() : loadImages()} className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg hover:bg-secondary transition-colors">
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />刷新
          </button>
          <button onClick={() => setUploadModalOpen(true)} className="flex items-center gap-2 bg-secondary text-foreground text-sm px-4 py-2 rounded-lg hover:bg-secondary/80 transition-colors">
            <Upload className="w-3.5 h-3.5" />上传录像
          </button>
          <button onClick={triggerAnalysis} className="flex items-center gap-2 bg-primary text-primary-foreground text-sm px-4 py-2 rounded-lg hover:bg-primary/90 transition-colors">
            <Play className="w-3.5 h-3.5" />触发今日分析
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-fit mb-5">
        {(['tasks', 'images'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn('px-4 py-1.5 text-sm rounded-md transition-colors', tab === t ? 'bg-background shadow-sm font-medium' : 'text-muted-foreground hover:text-foreground')}>
            {t === 'tasks' ? '分析任务' : '采集图片'}
          </button>
        ))}
      </div>

      {tab === 'tasks' ? (
        <div className="space-y-3">
          <div className="text-xs text-muted-foreground">
            这里只显示手动上传任务。系统自动任务和全部任务请到“系统管理”中的“全部任务”查看。
          </div>
          <div className="bg-card border border-border rounded-xl overflow-x-auto">
          <table className="data-table min-w-[768px]">
            <thead><tr><th>任务类型</th><th>日期</th><th>状态</th><th>总数</th><th>成功</th><th>低置信</th><th>失败</th><th>耗时</th><th></th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={9} className="text-center py-12 text-muted-foreground">加载中...</td></tr>}
              {!loading && tasks.length === 0 && <tr><td colSpan={9} className="text-center py-12 text-muted-foreground">暂无任务记录</td></tr>}
              {tasks.map(t => {
                const duration = t.started_at && t.finished_at
                  ? `${Math.round((new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()) / 1000)}s`
                  : t.status === 'running' ? '运行中' : '—'
                return (
                  <tr key={t.id} className="cursor-pointer hover:bg-secondary/50" onClick={() => openTaskDetail(t)}>
                    <td><span className="font-mono text-xs">{TASK_TYPE_LABEL[t.task_type] || t.task_type}</span></td>
                    <td><span className="font-mono text-xs">{t.task_date || '—'}</span></td>
                    <td><span className={cn('text-xs font-medium', STATUS_STYLE[t.status])}>{STATUS_LABEL[t.status] || t.status}</span></td>
                    <td><span className="font-mono">{t.total_count}</span></td>
                    <td><span className="font-mono text-health-green">{t.success_count}</span></td>
                    <td><span className="font-mono text-health-amber">{t.low_confidence_count}</span></td>
                    <td><span className="font-mono text-health-red">{t.error_count}</span></td>
                    <td><span className="font-mono text-xs text-muted-foreground">{duration}</span></td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {['failed', 'partial'].includes(t.status) && (
                        <button onClick={() => retryTask(t.id)} className="text-xs text-health-blue hover:underline">重试</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        </div>
      ) : (
        <>
          {/* Image filters */}
          <div className="flex gap-1.5 mb-4">
            {['', 'pending', 'identified', 'matched', 'error'].map(s => (
              <button key={s} onClick={() => { setStatusFilter(s); setImagePage(1) }}
                className={cn('px-3 py-1.5 text-xs rounded-md transition-colors', statusFilter === s ? 'bg-primary text-primary-foreground' : 'bg-secondary text-muted-foreground hover:text-foreground')}>
                {s === '' ? '全部' : STATUS_LABEL[s]}
              </button>
            ))}
          </div>

          {/* Image grid */}
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
            {loading && Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="aspect-video bg-secondary rounded-lg animate-pulse" />
            ))}
            {!loading && images.map(img => (
              <div key={img.id} onClick={() => openReview(img)}
                className="group relative aspect-video bg-secondary rounded-lg overflow-hidden cursor-pointer border border-border hover:border-foreground/30 transition-all">
                <img
                  src={resolveImageUrl(img)}
                  alt={`Captured at ${img.captured_at}`}
                  className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
                  loading="lazy"
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = 'none'
                  }}
                />
                <div className="absolute inset-0 flex items-center justify-center">
                  <Eye className="w-6 h-6 text-muted-foreground/50 group-hover:text-muted-foreground transition-colors" />
                </div>
                {/* Status badge */}
                <div className={cn('absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium',
                  img.status === 'matched' ? 'bg-health-green/20 text-health-green' :
                  img.status === 'identified' ? 'bg-health-blue/20 text-health-blue' :
                  img.status === 'error' ? 'bg-health-red/20 text-health-red' :
                  'bg-secondary text-muted-foreground')}>
                  {STATUS_LABEL[img.status]}
                </div>
                {/* Channel badge */}
                <div className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-foreground/60 text-background">
                  CH{img.channel_id}
                </div>
                {/* Time */}
                <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-2 pt-6">
                  <span className="text-[10px] font-mono text-white/80">{fmtDateTime(img.captured_at)}</span>
                </div>
                {/* Dish tags */}
                {img.recognitions && img.recognitions.length > 0 && (
                  <div className="absolute bottom-6 left-1.5 right-1.5 flex flex-wrap gap-0.5">
                    {img.recognitions.slice(0, 2).map((r, i) => (
                      <span key={i} className={cn('px-1 py-0.5 rounded text-[9px]', r.is_low_confidence ? 'bg-health-amber/20 text-health-amber' : 'bg-foreground/60 text-background')}>
                        {r.dish_name_raw}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalImagePages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4">
              <button onClick={() => setImagePage(p => Math.max(1, p - 1))} disabled={imagePage <= 1} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40"><ChevronLeft className="w-4 h-4" /></button>
              <span className="text-xs font-mono">{imagePage} / {totalImagePages}</span>
              <button onClick={() => setImagePage(p => Math.min(totalImagePages, p + 1))} disabled={imagePage >= totalImagePages} className="p-1.5 rounded-md hover:bg-secondary disabled:opacity-40"><ChevronRight className="w-4 h-4" /></button>
            </div>
          )}
        </>
      )}

      {/* Task Detail Modal */}
      {taskDetailModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-4xl shadow-xl animate-fade-in max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div>
                <h3 className="font-medium text-sm">任务详情</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {TASK_TYPE_LABEL[taskDetailModal.task_type] || taskDetailModal.task_type}
                  {' '}· {taskDetailModal.task_date || '—'} · {STATUS_LABEL[taskDetailModal.status]}
                </p>
              </div>
              <button onClick={() => setTaskDetailModal(null)} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {taskImagesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <RefreshCw className="w-6 h-6 animate-spin text-muted-foreground" />
                </div>
              ) : taskImages.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground">
                  <FolderOpen className="w-12 h-12 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">该任务暂无采集图片</p>
                </div>
              ) : (
                <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                  {taskImages.map(img => (
                    <div key={img.id} onClick={() => openReview(img)}
                      className="group relative aspect-video bg-secondary rounded-lg overflow-hidden cursor-pointer border border-border hover:border-foreground/30 transition-all">
                      <img
                        src={resolveImageUrl(img)}
                        alt={`Captured at ${img.captured_at}`}
                        className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
                        loading="lazy"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none'
                        }}
                      />
                      <div className="absolute inset-0 flex items-center justify-center">
                        <Eye className="w-6 h-6 text-muted-foreground/50 group-hover:text-muted-foreground transition-colors" />
                      </div>
                      {/* Status badge */}
                      <div className={cn('absolute top-1.5 right-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium',
                        img.status === 'matched' ? 'bg-health-green/20 text-health-green' :
                        img.status === 'identified' ? 'bg-health-blue/20 text-health-blue' :
                        img.status === 'error' ? 'bg-health-red/20 text-health-red' :
                        'bg-secondary text-muted-foreground')}>
                        {STATUS_LABEL[img.status]}
                      </div>
                      {/* Channel badge */}
                      <div className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-foreground/60 text-background">
                        CH{img.channel_id}
                      </div>
                      {/* Time */}
                      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-2 pt-6">
                        <span className="text-[10px] font-mono text-white/80">{fmtDateTime(img.captured_at)}</span>
                      </div>
                      {/* Dish tags */}
                      {img.recognitions && img.recognitions.length > 0 && (
                        <div className="absolute bottom-6 left-1.5 right-1.5 flex flex-wrap gap-0.5">
                          {img.recognitions.slice(0, 2).map((r, i) => (
                            <span key={i} className={cn('px-1 py-0.5 rounded text-[9px]', r.is_low_confidence ? 'bg-health-amber/20 text-health-amber' : 'bg-foreground/60 text-background')}>
                              {r.dish_name_raw}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="flex items-center justify-between p-4 border-t border-border">
              <span className="text-xs text-muted-foreground">
                共 {taskImages.length} 张图片
                {taskDetailModal.task_date && ` · 日期: ${taskDetailModal.task_date}`}
              </span>
              <button onClick={() => setTaskDetailModal(null)} className="px-4 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors">关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* Review modal */}
      {reviewModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-5xl shadow-xl animate-fade-in max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-border">
              <div>
                <h3 className="font-medium text-sm">人工复核 — {fmtDateTime(reviewModal.captured_at)}</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  状态:
                  {' '}
                  <span className={cn('font-medium', STATUS_STYLE[reviewModal.status])}>
                    {STATUS_LABEL[reviewModal.status]}
                  </span>
                </p>
              </div>
              <div className="flex items-center gap-2">
                {isAdmin && localRecognitionModeEnabled && (
                  <button
                    onClick={() => {
                      setAnnotationMode(current => {
                        const next = !current
                        if (!next) {
                          clearAnnotation()
                          resetAnnotationViewport()
                          setAnnotationTool('draw')
                          setAnnotationDishDropdownOpen(false)
                          setProposalRegions([])
                          setProposalBackend(null)
                        }
                        return next
                      })
                    }}
                    className={cn(
                      'px-3 py-1.5 text-xs rounded-lg transition-colors',
                      annotationMode ? 'bg-primary text-primary-foreground' : 'bg-secondary hover:bg-secondary/80',
                    )}
                  >
                    {annotationMode ? '退出标注' : '标注'}
                  </button>
                )}
                {isAdmin && (
                  <button
                    onClick={generateDishDescription}
                    disabled={describing}
                    className="px-3 py-1.5 text-xs bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50 flex items-center gap-1"
                  >
                    <Sparkles className="w-3 h-3" />
                    {describing ? '生成中...' : '生成菜品描述'}
                  </button>
                )}
                {!hasManualRecognition && canRerunRecognition && !reviewModal.is_candidate && (
                  <button
                    onClick={triggerSingleRecognition}
                    disabled={recognizing}
                    className="px-3 py-1.5 text-xs bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50"
                  >
                    {recognizing ? '提交中...' : hasRecognitionResult ? '重新识别这张图片' : '发起 AI 识别'}
                  </button>
                )}
                <button onClick={() => setReviewModal(null)} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.9fr)]">
                <div className="space-y-4">
                  <div className="rounded-xl border border-border bg-card p-3">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs font-medium text-foreground">采集图详情</p>
                        <p className="text-[11px] text-muted-foreground">
                          {localRecognitionModeEnabled
                            ? (annotationMode
                              ? '可先缩放、拖动画面，再切回框选模式裁出单个菜。保存时只会使用裁剪后的区域做 embedding。'
                              : '可查看原图、放大预览，或切换到标注模式裁出单个菜。')
                            : '可查看原图与放大预览。'}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => openPreview(resolveImageUrl(reviewModal))}
                        className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-secondary transition-colors"
                      >
                        <Eye className="w-3 h-3" />
                        查看原图
                      </button>
                    </div>
                    <div
                      ref={reviewImageFrameRef}
                      className={cn(
                        'relative aspect-video w-full overflow-hidden rounded-lg bg-secondary/80',
                        annotationMode ? 'select-none' : 'cursor-zoom-in',
                      )}
                      onClick={() => {
                        if (!annotationMode) openPreview(resolveImageUrl(reviewModal))
                      }}
                    >
                      <img
                        ref={reviewImageElementRef}
                        src={resolveImageUrl(reviewModal)}
                        alt="Captured"
                        className={cn('h-full w-full object-contain', annotationMode && 'opacity-0')}
                        onLoad={updateReviewImageLayout}
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none'
                          setImageLayout(null)
                        }}
                      />
                      {annotationMode && imageLayout && (
                        <div
                          ref={annotationSurfaceRef}
                          className={cn(
                            'absolute overflow-hidden border border-dashed border-primary/60 bg-primary/5',
                            annotationTool === 'pan' ? (annotationViewport.scale > MIN_ANNOTATION_SCALE ? 'cursor-grab' : 'cursor-default') : 'cursor-crosshair',
                          )}
                          style={{
                            left: imageLayout.left,
                            top: imageLayout.top,
                            width: imageLayout.width,
                            height: imageLayout.height,
                          }}
                          onWheel={(event) => {
                            event.preventDefault()
                            event.stopPropagation()
                            const delta = event.deltaY > 0 ? -0.2 : 0.2
                            zoomAnnotationAtPoint(annotationViewport.scale + delta, event.clientX, event.clientY)
                          }}
                          onMouseDown={handleAnnotationPointerDown}
                          >
                          <div
                            className="absolute inset-0 origin-top-left"
                            style={{
                              transform: `translate(${annotationViewport.offsetX}px, ${annotationViewport.offsetY}px) scale(${annotationViewport.scale})`,
                            }}
                          >
                            <img
                              src={resolveImageUrl(reviewModal)}
                              alt="Captured annotation"
                              className="h-full w-full select-none object-fill"
                              draggable={false}
                            />
                            {!annotationBox && (
                              <div className="absolute left-3 top-3 rounded-full bg-black/65 px-2.5 py-1 text-[11px] text-white">
                                {annotationTool === 'pan' ? '拖动画面查看细节，滚轮缩放，Esc 可重置视图' : '拖动鼠标框选单个菜，或先生成智能提议'}
                              </div>
                            )}
                            {proposalOverlays.map(({ proposal, selected, style }) => (
                              <button
                                key={`${proposal.index}-${proposal.bbox.x1}-${proposal.bbox.y1}-${proposal.bbox.x2}-${proposal.bbox.y2}`}
                                type="button"
                                className={cn(
                                  'absolute border-2 border-dashed transition-colors',
                                  selected ? 'border-primary bg-primary/15' : 'border-health-amber/80 bg-health-amber/10 hover:bg-health-amber/15',
                                )}
                                style={style}
                                onMouseDown={(event) => {
                                  event.preventDefault()
                                  event.stopPropagation()
                                }}
                                onClick={(event) => {
                                  event.preventDefault()
                                  event.stopPropagation()
                                  if (selected) {
                                    clearSelectedProposal(proposal)
                                    return
                                  }
                                  applyProposal(proposal)
                                }}
                                title={`${proposal.label || 'dish region'} ${(proposal.score * 100).toFixed(1)}%`}
                              >
                                <span className={cn(
                                  'absolute left-2 top-2 rounded-full px-2 py-0.5 text-[10px] font-medium',
                                  selected ? 'bg-primary text-primary-foreground' : 'bg-health-amber text-black',
                                )}>
                                  提议 {proposal.index}
                                </span>
                              </button>
                            ))}
                            {annotationBox && annotationBoxStyle && (
                              <div
                                className="absolute border-2 border-primary bg-primary/12 shadow-[0_0_0_1px_rgba(255,255,255,0.45)]"
                                style={annotationBoxStyle}
                              >
                                <div className="absolute left-2 top-2 rounded-full bg-primary px-2 py-0.5 text-[10px] font-medium text-primary-foreground">
                                  {annotationBox.width} × {annotationBox.height}px
                                </div>
                                <button
                                  type="button"
                                  onMouseDown={(event) => {
                                    event.preventDefault()
                                    event.stopPropagation()
                                  }}
                                  onClick={(event) => {
                                    event.preventDefault()
                                    event.stopPropagation()
                                    clearAnnotation()
                                  }}
                                  className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-white/90 text-foreground shadow-sm transition-colors hover:bg-white"
                                  aria-label="取消当前标注框"
                                >
                                  <X className="w-3 h-3" />
                                </button>
                              </div>
                            )}
                          </div>
                          {annotationBox && annotationBoxStyle && (
                            <div
                              className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center"
                            >
                              <div className="rounded-full bg-black/70 px-2.5 py-1 text-[11px] text-white">
                                点击框右上角可取消当前标注
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    {localRecognitionModeEnabled && annotationMode && (
                      <div className="mt-3 rounded-lg border border-primary/10 bg-primary/[0.03] p-3">
                        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">标注工具</label>
                            <div className="flex flex-wrap items-center gap-2">
                              <div className="inline-flex rounded-lg border border-border bg-background p-1">
                                <button
                                  type="button"
                                  onClick={() => setAnnotationTool('draw')}
                                  className={cn(
                                    'rounded-md px-3 py-1.5 text-xs transition-colors',
                                    annotationTool === 'draw' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
                                  )}
                                >
                                  框选
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setAnnotationTool('pan')}
                                  className={cn(
                                    'rounded-md px-3 py-1.5 text-xs transition-colors',
                                    annotationTool === 'pan' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
                                  )}
                                >
                                  移动
                                </button>
                              </div>
                              <div className="inline-flex items-center rounded-lg border border-border bg-background">
                                <button
                                  type="button"
                                  onClick={() => zoomAnnotationAtPoint(annotationViewport.scale - 0.2)}
                                  disabled={annotationViewport.scale <= MIN_ANNOTATION_SCALE}
                                  className="px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary disabled:opacity-50"
                                >
                                  -
                                </button>
                                <span className="min-w-[72px] px-2 text-center text-xs font-medium">
                                  {Math.round(annotationViewport.scale * 100)}%
                                </span>
                                <button
                                  type="button"
                                  onClick={() => zoomAnnotationAtPoint(annotationViewport.scale + 0.2)}
                                  disabled={annotationViewport.scale >= MAX_ANNOTATION_SCALE}
                                  className="px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary disabled:opacity-50"
                                >
                                  +
                                </button>
                              </div>
                              <button
                                type="button"
                                onClick={resetAnnotationViewport}
                                className="px-3 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors"
                              >
                                重置视图
                              </button>
                            </div>
                            <p className="mt-1 text-[11px] text-muted-foreground">
                              滚轮可缩放图片；切到“移动”后可拖动画面；Esc 可关闭搜索、取消框选或重置视图。
                            </p>
                          </div>
                        </div>
                        <div className="mt-3 flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={generateAnnotationProposals}
                            disabled={proposalLoading}
                            className="whitespace-nowrap px-3 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors disabled:opacity-50"
                          >
                            {proposalLoading ? '生成中...' : '智能提议'}
                          </button>
                          <p className="text-[11px] text-muted-foreground">
                            自动提议使用当前检测服务生成候选框。
                          </p>
                        </div>
                        <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
                          <div>
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">关联菜品</label>
                            <div ref={annotationDishPickerRef} className="relative">
                              <input
                                value={annotationDishKeyword}
                                onChange={(event) => {
                                  setAnnotationDishKeyword(event.target.value)
                                  setAnnotationDishId('')
                                  setAnnotationSelectedDish(null)
                                  setAnnotationDishDropdownOpen(true)
                                }}
                                onFocus={() => setAnnotationDishDropdownOpen(true)}
                                placeholder="输入菜品名称模糊搜索"
                                className="w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-foreground/20"
                              />
                              {annotationDishDropdownOpen && (
                                <div className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-border bg-background shadow-lg">
                                  {annotationDishLoading ? (
                                    <div className="px-3 py-2 text-sm text-muted-foreground">搜索中...</div>
                                  ) : annotationDishOptions.length > 0 ? (
                                    annotationDishOptions.map((dish) => {
                                      const selected = annotationDishId === dish.id
                                      return (
                                        <button
                                          key={dish.id}
                                          type="button"
                                          onMouseDown={(event) => {
                                            event.preventDefault()
                                          }}
                                          onClick={() => {
                                            setAnnotationDishId(dish.id)
                                            setAnnotationSelectedDish(dish)
                                            setAnnotationDishKeyword(dish.name)
                                            setAnnotationDishDropdownOpen(false)
                                          }}
                                          className={cn(
                                            'flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors',
                                            selected ? 'bg-primary/8 text-foreground' : 'hover:bg-secondary',
                                          )}
                                        >
                                          <span>{dish.name}</span>
                                          <span className="text-[11px] text-muted-foreground">{dish.category}</span>
                                        </button>
                                      )
                                    })
                                  ) : (
                                    <div className="px-3 py-2 text-sm text-muted-foreground">没有匹配到菜品</div>
                                  )}
                                </div>
                              )}
                            </div>
                            <p className="mt-1 text-[11px] text-muted-foreground">
                              当前会从原始采集图中裁剪所选区域，新增到该菜品的样图库。
                            </p>
                          </div>
                          <div className="flex gap-2">
                            <button
                              type="button"
                              onClick={clearAnnotation}
                              className="px-3 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors"
                            >
                              清除框选
                            </button>
                            <button
                              type="button"
                              onClick={saveAnnotation}
                              disabled={annotationSaving || !annotationBox || !annotationDishId}
                              className="px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50"
                            >
                              {annotationSaving ? '保存中...' : '保存为样图'}
                            </button>
                          </div>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                          <span>最小框选尺寸: {MIN_ANNOTATION_EDGE}px</span>
                          {proposalTask && (
                            <span>
                              提议任务 #{proposalTask.id}: {String(proposalTask.meta?.status_text || STATUS_LABEL[proposalTask.status] || proposalTask.status)}
                            </span>
                          )}
                          {proposalBackend && <span>提议来源: {proposalBackend}</span>}
                          {proposalRegions.length > 0 && <span>候选框: {proposalRegions.length} 个</span>}
                          {selectedAnnotationDish && <span>当前菜品: {selectedAnnotationDish.name}</span>}
                          {annotationBox && <span>坐标: ({annotationBox.x1}, {annotationBox.y1}) → ({annotationBox.x2}, {annotationBox.y2})</span>}
                        </div>
                        {proposalRegions.length > 0 && (
                          <div className="mt-3 flex flex-wrap gap-2">
                            {proposalRegions.map((proposal) => {
                              const selected = annotationBox
                                ? annotationBox.x1 === proposal.bbox.x1 && annotationBox.y1 === proposal.bbox.y1 && annotationBox.x2 === proposal.bbox.x2 && annotationBox.y2 === proposal.bbox.y2
                                : false
                              return (
                                <button
                                  key={`proposal-chip-${proposal.index}-${proposal.bbox.x1}-${proposal.bbox.y1}`}
                                  type="button"
                                  onClick={() => applyProposal(proposal)}
                                  className={cn(
                                    'rounded-full border px-3 py-1 text-[11px] transition-colors',
                                    selected ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-background hover:bg-secondary',
                                  )}
                                >
                                  提议 {proposal.index}
                                  {' · '}
                                  {(proposal.score * 100).toFixed(0)}%
                                  {proposal.label ? ` · ${proposal.label}` : ''}
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {dishDescription && (
                    <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg">
                      <p className="text-xs font-medium text-blue-700 mb-1.5 flex items-center gap-1">
                        <Sparkles className="w-3 h-3" />
                        菜品视觉描述
                      </p>
                      <p className="text-xs text-blue-600 leading-relaxed whitespace-pre-wrap">{dishDescription}</p>
                    </div>
                  )}
                </div>

                <div className="space-y-4">
                  <div className="rounded-xl border border-border bg-card p-4">
                    <p className="text-xs font-medium text-muted-foreground mb-2">AI 识别结果</p>
                    {reviewModal.recognitions && reviewModal.recognitions.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {reviewModal.recognitions.map((r, i) => (
                          <span key={i} className={cn('px-2 py-1 rounded-full text-xs', r.is_low_confidence ? 'bg-health-amber/10 text-health-amber border border-health-amber/20' : 'bg-health-green/10 text-health-green border border-health-green/20')}>
                            {r.dish_name_raw} <span className="opacity-60">({(r.confidence * 100).toFixed(0)}%)</span>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">当前暂无识别结果。</p>
                    )}
                  </div>

                  <div className="rounded-xl border border-border bg-card p-4">
                    <p className="text-xs font-medium text-muted-foreground mb-2">手动修正（选择实际菜品）</p>
                    <div className="grid grid-cols-2 gap-1.5 max-h-[320px] overflow-y-auto pr-1">
                      {allDishes.map(dish => {
                        const sel = reviewDishIds.includes(dish.id)
                        return (
                          <button key={dish.id} onClick={() => setReviewDishIds(prev => sel ? prev.filter(id => id !== dish.id) : [...prev, dish.id])}
                            className={cn('px-2 py-1.5 rounded text-xs text-left border transition-colors', sel ? 'border-primary/30 bg-primary/5 font-medium' : 'border-border hover:border-primary/20')}>
                            {dish.name}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div className="flex gap-3 p-4 border-t border-border">
              <button onClick={() => setReviewModal(null)} className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg">取消</button>
              <button onClick={saveReview} disabled={saving} className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg disabled:opacity-50">
                {saving ? '保存中...' : '确认修正'}
              </button>
            </div>
          </div>
        </div>
      )}

      {previewImageUrl && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={closePreview}
        >
          <button
            type="button"
            onClick={closePreview}
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white transition-colors hover:bg-white/20"
          >
            <X className="w-5 h-5" />
          </button>
          <div
            className="relative flex max-h-[92vh] max-w-[92vw] items-center justify-center overflow-hidden rounded-xl"
            onClick={(e) => e.stopPropagation()}
            onWheel={handlePreviewWheel}
          >
            <div className="absolute left-4 top-4 z-10 rounded-full bg-black/55 px-3 py-1 text-xs font-mono text-white">
              {previewScale.toFixed(1)}x
            </div>
            <div className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full bg-black/55 px-3 py-1 text-xs text-white/90">
              滚轮缩放
            </div>
            <img
              src={previewImageUrl}
              alt="Preview"
              className="max-h-[92vh] max-w-[92vw] rounded-xl bg-white object-contain shadow-2xl transition-transform duration-100"
              style={{ transform: `scale(${previewScale})` }}
            />
          </div>
        </div>
      )}

      {/* Upload Video Modal */}
      {uploadModalOpen && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white border border-border rounded-xl w-full max-w-lg shadow-xl animate-fade-in max-h-[90vh] flex flex-col">
            <div className="flex items-center justify-between p-5 border-b border-border">
              <h3 className="font-medium">上传录像文件</h3>
              <button onClick={() => setUploadModalOpen(false)} className="p-1 hover:bg-secondary rounded-md"><X className="w-4 h-4" /></button>
            </div>
            <div className="p-5 space-y-4 overflow-y-auto max-h-[70vh]">
              {/* File Upload */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-2">视频文件</label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".mp4,.avi,.mov,.mkv,.wmv,video/*"
                  onChange={handleFileSelect}
                  className="block w-full text-sm text-muted-foreground file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-secondary file:text-foreground hover:file:bg-secondary/80 cursor-pointer border border-border rounded-lg px-3 py-2 bg-background"
                />
                <p className="text-[10px] text-muted-foreground mt-1">支持格式: MP4, AVI, MOV, MKV, WMV</p>
                {uploadFile && (
                  <p className="text-xs text-health-green mt-1 flex items-center gap-1">
                    <CheckCircle2 className="w-3 h-3" />{uploadFile.name} ({(uploadFile.size / 1024 / 1024).toFixed(1)} MB)
                  </p>
                )}
              </div>

              {/* Video Start Time */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-2">录像起始时间</label>
                <div className="flex gap-2">
                  <input
                    type="date"
                    value={uploadDate}
                    onChange={(e) => setUploadDate(e.target.value)}
                    className="flex-1 px-3 py-2 text-sm bg-background rounded-lg border border-border focus:outline-none focus:ring-1 focus:ring-foreground/20"
                  />
                  <input
                    type="time"
                    step="1"
                    value={uploadTime}
                    onChange={(e) => setUploadTime(e.target.value)}
                    className="w-32 px-3 py-2 text-sm bg-background rounded-lg border border-border focus:outline-none focus:ring-1 focus:ring-foreground/20"
                  />
                </div>
                <p className="text-[10px] text-muted-foreground mt-1">请准确填写录像开始时间，用于计算帧时间戳</p>
              </div>

              {/* Channel ID */}
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-2">通道/摄像头编号</label>
                <input
                  type="text"
                  value={uploadChannel}
                  onChange={(e) => setUploadChannel(e.target.value)}
                  placeholder="例如: camera_01"
                  className="w-full px-3 py-2 text-sm bg-background rounded-lg border border-border focus:outline-none focus:ring-1 focus:ring-foreground/20"
                />
              </div>
            </div>
            <div className="flex gap-3 p-5 border-t border-border">
              <button onClick={() => setUploadModalOpen(false)} className="flex-1 px-4 py-2 text-sm bg-secondary rounded-lg hover:bg-secondary/80 transition-colors">取消</button>
              <button
                onClick={handleUpload}
                disabled={uploading || !uploadFile}
                className="flex-1 px-4 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {uploading ? '上传中...' : '开始处理'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
