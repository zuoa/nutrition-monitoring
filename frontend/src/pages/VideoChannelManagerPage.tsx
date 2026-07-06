import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent, type SyntheticEvent } from 'react'
import toast from 'react-hot-toast'
import {
  Camera,
  Check,
  ChevronDown,
  ChevronRight,
  Eraser,
  Image as ImageIcon,
  Loader2,
  MapPin,
  Play,
  Plus,
  RefreshCw,
  Save,
  SquareDashedMousePointer,
  StopCircle,
  Video,
  X,
} from 'lucide-react'

import { adminApi } from '@/api/client'
import VideoSourceManagerPanel from '@/components/admin/VideoSourceManagerPanel'
import { cn, fmtDateTime } from '@/lib/utils'
import type {
  HikvisionPluginPreviewConfig,
  RoiRegion,
  VideoChannelSnapshot,
  VideoSourceChannel,
  VideoSourceChannelsResponse,
  VideoSourceSummary,
} from '@/types'

type SourceTreeNode = {
  source: VideoSourceSummary
  supportsSnapshot: boolean
  channels: VideoSourceChannel[]
}

type SelectedChannel = {
  sourceId: number
  sourceName: string
  sourceType: string
  supportsSnapshot: boolean
  channel: VideoSourceChannel
}

type ImageSize = {
  naturalWidth: number
  naturalHeight: number
  displayWidth: number
  displayHeight: number
}

type DragState = {
  mode: 'draw' | 'move' | 'resize'
  corner?: 'nw' | 'ne' | 'sw' | 'se'
  startX: number
  startY: number
  initialRoi: RoiRegion | null
}

type HikvisionWebVideoCtrl = {
  I_CheckPluginInstall?: () => number
  I_CheckPluginVersion?: () => Promise<boolean> | boolean
  I_InitPlugin: ((
    options: {
      bWndFull?: boolean
      iWndowType?: number
      bDebugMode?: boolean
      cbInitPluginComplete?: () => void
      cbInitPlugin?: () => void
      cbInitPluginError?: () => void
      iTopHeight?: number
    },
  ) => Promise<unknown> | void) & ((
    width: number,
    height: number,
    options: {
      bWndFull?: boolean
      iWndowType?: number
      bDebugMode?: boolean
      cbInitPluginComplete?: () => void
      cbInitPlugin?: () => void
      cbInitPluginError?: () => void
      iTopHeight?: number
    },
  ) => Promise<unknown> | void)
  I_InsertOBJECTPlugin: (elementId: string) => Promise<unknown> | number
  I_Login: (
    host: string,
    protocol: number,
    port: number,
    username: string,
    password: string,
    callbacks: {
      success?: () => void
      error?: (_status: unknown, error: unknown) => void
    },
  ) => Promise<unknown> | void
  I_StartRealPlay: (
    deviceIdentify: string,
    options: {
      iStreamType: number
      iChannelID: number
      bZeroChannel?: boolean
      iWndIndex?: number
      iPort?: number
      success?: () => void
      error?: (_status: unknown, error: unknown) => void
    },
  ) => Promise<unknown> | void
  I_Stop?: (options?: { iWndIndex?: number }) => Promise<unknown> | void
  I_Logout?: (deviceIdentify: string) => Promise<unknown> | void
  I_DestroyPlugin?: () => Promise<unknown> | void
  I_Resize?: (width: number, height: number) => Promise<unknown> | void
}

declare global {
  interface Window {
    WebVideoCtrl?: HikvisionWebVideoCtrl
  }
}

const HIKVISION_WEBVIDEOCTRL_SRC = '/hikvision/webVideoCtrl.js'

function loadHikvisionWebVideoCtrl() {
  if (window.WebVideoCtrl) return Promise.resolve(window.WebVideoCtrl)

  return new Promise<HikvisionWebVideoCtrl>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${HIKVISION_WEBVIDEOCTRL_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', () => {
        if (window.WebVideoCtrl) resolve(window.WebVideoCtrl)
        else reject(new Error('海康 webVideoCtrl.js 已加载，但没有暴露 WebVideoCtrl'))
      }, { once: true })
      existing.addEventListener('error', () => reject(new Error('海康 webVideoCtrl.js 加载失败')), { once: true })
      return
    }

    const script = document.createElement('script')
    script.src = HIKVISION_WEBVIDEOCTRL_SRC
    script.async = true
    script.onload = () => {
      if (window.WebVideoCtrl) resolve(window.WebVideoCtrl)
      else reject(new Error('海康 webVideoCtrl.js 已加载，但没有暴露 WebVideoCtrl'))
    }
    script.onerror = () => reject(new Error(`未找到 ${HIKVISION_WEBVIDEOCTRL_SRC}，请把海康 WebComponentsKit 的 webVideoCtrl.js 放到 frontend/public/hikvision/`))
    document.head.appendChild(script)
  })
}

function initHikvisionPlugin(webVideoCtrl: HikvisionWebVideoCtrl, elementId: string, width: number, height: number) {
  return new Promise<void>((resolve, reject) => {
    const installStatus = webVideoCtrl.I_CheckPluginInstall?.()
    if (installStatus === -1 || installStatus === -2) {
      reject(new Error('未检测到海康 WebComponents 插件，请先安装 WebComponentsKit/VideoWebPlugin 后刷新页面'))
      return
    }

    let insertStarted = false
    const insertOnce = () => {
      if (insertStarted) return
      insertStarted = true
      insertHikvisionPluginObject(webVideoCtrl, elementId).then(resolve).catch(reject)
    }
    const options = {
      bWndFull: true,
      iWndowType: 1,
      cbInitPluginComplete: insertOnce,
      cbInitPlugin: insertOnce,
      cbInitPluginError: () => reject(new Error('海康插件初始化失败，请确认插件已安装并正在运行')),
      iTopHeight: 0,
    }

    const initResult = webVideoCtrl.I_InitPlugin.length >= 3
      ? webVideoCtrl.I_InitPlugin(width, height, options)
      : webVideoCtrl.I_InitPlugin(options)
    if (initResult && typeof (initResult as Promise<unknown>).then === 'function') {
      ;(initResult as Promise<unknown>)
        .then(insertOnce)
        .catch(() => reject(new Error('海康插件初始化失败，请确认插件已安装并正在运行')))
    }
  })
}

async function insertHikvisionPluginObject(webVideoCtrl: HikvisionWebVideoCtrl, elementId: string) {
  const inserted = await webVideoCtrl.I_InsertOBJECTPlugin(elementId)
  if (inserted === -1) {
    throw new Error('海康插件窗口创建失败，请确认浏览器允许加载本地插件')
  }
}

function loginHikvisionDevice(webVideoCtrl: HikvisionWebVideoCtrl, config: HikvisionPluginPreviewConfig) {
  return new Promise<void>((resolve, reject) => {
    const result = webVideoCtrl.I_Login(config.host, config.protocol || 1, config.port, config.username, config.password, {
      success: () => resolve(),
      error: (_status, error) => reject(new Error(`海康设备登录失败: ${String(error || '未知错误')}`)),
    })
    if (result && typeof (result as Promise<unknown>).then === 'function') {
      ;(result as Promise<unknown>).then(() => resolve()).catch((error) => reject(new Error(`海康设备登录失败: ${String(error || '未知错误')}`)))
    }
  })
}

function startHikvisionRealPlay(webVideoCtrl: HikvisionWebVideoCtrl, config: HikvisionPluginPreviewConfig) {
  return new Promise<void>((resolve, reject) => {
    const deviceIdentify = `${config.host}_${config.port}`
    const result = webVideoCtrl.I_StartRealPlay(deviceIdentify, {
      iStreamType: config.stream_type || 1,
      iChannelID: Number(config.channel_id) || 1,
      iPort: config.rtsp_port || 554,
      bZeroChannel: false,
      success: () => resolve(),
      error: (_status, error) => reject(new Error(`实时预览启动失败: ${String(error || '未知错误')}`)),
    })
    if (result && typeof (result as Promise<unknown>).then === 'function') {
      ;(result as Promise<unknown>).then(() => resolve()).catch((error) => reject(new Error(`实时预览启动失败: ${String(error || '未知错误')}`)))
    }
  })
}

function normalizeRoi(value?: RoiRegion | null): RoiRegion | null {
  if (!value) return null
  const x = Math.round(Number(value.x))
  const y = Math.round(Number(value.y))
  const w = Math.round(Number(value.w))
  const h = Math.round(Number(value.h))
  if (x < 0 || y < 0 || w <= 0 || h <= 0) return null
  return { x, y, w, h }
}

function formatChannelId(channelId?: string | null) {
  const value = String(channelId ?? '').trim()
  return value || '—'
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

function waitForAnimationFrame() {
  return new Promise<void>((resolve) => {
    window.requestAnimationFrame(() => resolve())
  })
}

function clampRoi(roi: RoiRegion, width: number, height: number): RoiRegion {
  const x = clamp(Math.round(roi.x), 0, Math.max(0, width - 1))
  const y = clamp(Math.round(roi.y), 0, Math.max(0, height - 1))
  const maxW = Math.max(1, width - x)
  const maxH = Math.max(1, height - y)
  return {
    x,
    y,
    w: clamp(Math.round(roi.w), 1, maxW),
    h: clamp(Math.round(roi.h), 1, maxH),
  }
}

function makeRoiFromPoints(x1: number, y1: number, x2: number, y2: number, width: number, height: number): RoiRegion {
  const left = clamp(Math.min(x1, x2), 0, width)
  const top = clamp(Math.min(y1, y2), 0, height)
  const right = clamp(Math.max(x1, x2), 0, width)
  const bottom = clamp(Math.max(y1, y2), 0, height)
  return clampRoi({
    x: left,
    y: top,
    w: Math.max(1, right - left),
    h: Math.max(1, bottom - top),
  }, width, height)
}

function pointInRoi(x: number, y: number, roi: RoiRegion) {
  return x >= roi.x && x <= roi.x + roi.w && y >= roi.y && y <= roi.y + roi.h
}

function nearestCorner(x: number, y: number, roi: RoiRegion, threshold: number) {
  const corners = [
    { key: 'nw' as const, x: roi.x, y: roi.y },
    { key: 'ne' as const, x: roi.x + roi.w, y: roi.y },
    { key: 'sw' as const, x: roi.x, y: roi.y + roi.h },
    { key: 'se' as const, x: roi.x + roi.w, y: roi.y + roi.h },
  ]
  return corners.find((corner) => Math.hypot(corner.x - x, corner.y - y) <= threshold)?.key || null
}

function RoiEditor({
  imageUrl,
  roi,
  onChange,
  disabled,
}: {
  imageUrl: string
  roi: RoiRegion | null
  onChange: (roi: RoiRegion | null) => void
  disabled?: boolean
}) {
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [imageSize, setImageSize] = useState<ImageSize | null>(null)
  const dragRef = useRef<DragState | null>(null)

  const updateImageSize = () => {
    const image = imageRef.current
    if (!image || !image.naturalWidth || !image.naturalHeight) return
    const rect = image.getBoundingClientRect()
    setImageSize({
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
      displayWidth: rect.width,
      displayHeight: rect.height,
    })
  }

  useEffect(() => {
    updateImageSize()
    window.addEventListener('resize', updateImageSize)
    return () => window.removeEventListener('resize', updateImageSize)
  }, [imageUrl])

  const toImagePoint = (event: PointerEvent<HTMLDivElement>) => {
    if (!imageSize) return null
    const rect = event.currentTarget.getBoundingClientRect()
    return {
      x: clamp(((event.clientX - rect.left) / rect.width) * imageSize.naturalWidth, 0, imageSize.naturalWidth),
      y: clamp(((event.clientY - rect.top) / rect.height) * imageSize.naturalHeight, 0, imageSize.naturalHeight),
    }
  }

  const updateRoiFromDrag = (point: { x: number; y: number }) => {
    const drag = dragRef.current
    if (!drag || !imageSize) return
    const { naturalWidth, naturalHeight } = imageSize

    if (drag.mode === 'draw') {
      onChange(makeRoiFromPoints(drag.startX, drag.startY, point.x, point.y, naturalWidth, naturalHeight))
      return
    }

    if (!drag.initialRoi) return

    if (drag.mode === 'move') {
      const dx = point.x - drag.startX
      const dy = point.y - drag.startY
      onChange(clampRoi({
        ...drag.initialRoi,
        x: drag.initialRoi.x + dx,
        y: drag.initialRoi.y + dy,
      }, naturalWidth, naturalHeight))
      return
    }

    const current = drag.initialRoi
    const left = drag.corner === 'nw' || drag.corner === 'sw' ? point.x : current.x
    const right = drag.corner === 'ne' || drag.corner === 'se' ? point.x : current.x + current.w
    const top = drag.corner === 'nw' || drag.corner === 'ne' ? point.y : current.y
    const bottom = drag.corner === 'sw' || drag.corner === 'se' ? point.y : current.y + current.h
    onChange(makeRoiFromPoints(left, top, right, bottom, naturalWidth, naturalHeight))
  }

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (disabled || !imageSize) return
    const point = toImagePoint(event)
    if (!point) return
    event.currentTarget.setPointerCapture(event.pointerId)

    const activeRoi = normalizeRoi(roi)
    if (activeRoi) {
      const threshold = Math.max(12, imageSize.naturalWidth / Math.max(24, imageSize.displayWidth / 12))
      const corner = nearestCorner(point.x, point.y, activeRoi, threshold)
      if (corner) {
        dragRef.current = { mode: 'resize', corner, startX: point.x, startY: point.y, initialRoi: activeRoi }
        return
      }
      if (pointInRoi(point.x, point.y, activeRoi)) {
        dragRef.current = { mode: 'move', startX: point.x, startY: point.y, initialRoi: activeRoi }
        return
      }
    }

    dragRef.current = { mode: 'draw', startX: point.x, startY: point.y, initialRoi: null }
    onChange(makeRoiFromPoints(point.x, point.y, point.x + 1, point.y + 1, imageSize.naturalWidth, imageSize.naturalHeight))
  }

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    const point = toImagePoint(event)
    if (point) updateRoiFromDrag(point)
  }

  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragRef.current = null
  }

  const box = imageSize && roi
    ? {
        left: `${(roi.x / imageSize.naturalWidth) * 100}%`,
        top: `${(roi.y / imageSize.naturalHeight) * 100}%`,
        width: `${(roi.w / imageSize.naturalWidth) * 100}%`,
        height: `${(roi.h / imageSize.naturalHeight) * 100}%`,
      }
    : null

  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-black">
      <img
        ref={imageRef}
        src={imageUrl}
        alt="通道抓拍预览"
        onLoad={updateImageSize}
        className="block max-h-[62vh] w-full select-none object-contain"
        draggable={false}
      />
      <div
        className={cn(
          'absolute inset-0 touch-none',
          disabled ? 'cursor-not-allowed' : 'cursor-crosshair',
        )}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        {box && (
          <div
            className="absolute border-2 border-health-green bg-health-green/15 shadow-[0_0_0_9999px_rgba(0,0,0,0.32)]"
            style={box}
          >
            {(['nw', 'ne', 'sw', 'se'] as const).map((corner) => (
              <span
                key={corner}
                className={cn(
                  'absolute h-3 w-3 rounded-sm border border-white bg-health-green shadow',
                  corner.includes('n') ? '-top-1.5' : '-bottom-1.5',
                  corner.includes('w') ? '-left-1.5' : '-right-1.5',
                )}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function VideoChannelManagerPage() {
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [tree, setTree] = useState<SourceTreeNode[]>([])
  const [expandedSourceIds, setExpandedSourceIds] = useState<Set<number>>(new Set())
  const [selected, setSelected] = useState<SelectedChannel | null>(null)
  const [snapshot, setSnapshot] = useState<VideoChannelSnapshot | null>(null)
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null)
  const [roiDraft, setRoiDraft] = useState<RoiRegion | null>(null)
  const [aliasDraft, setAliasDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [livePreviewOpen, setLivePreviewOpen] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewDevice, setPreviewDevice] = useState('')
  const [saving, setSaving] = useState(false)
  const [savingAlias, setSavingAlias] = useState(false)
  const pluginContainerRef = useRef<HTMLDivElement | null>(null)
  const pluginDeviceHostRef = useRef('')

  const snapshotUrl = useMemo(() => {
    if (!snapshot?.image_base64) return ''
    return `data:${snapshot.content_type || 'image/jpeg'};base64,${snapshot.image_base64}`
  }, [snapshot])

  const loadTree = async (preferredSelection?: SelectedChannel | null) => {
    setLoading(true)
    try {
      const sourcesRes = await adminApi.listVideoSources()
      const sources = (sourcesRes.data.data.items || []) as VideoSourceSummary[]
      const persistedSources = sources.filter((source): source is VideoSourceSummary & { id: number } => source.id !== null)
      const channelResults = await Promise.all(
        persistedSources.map(async (source) => {
          const res = await adminApi.listVideoSourceChannels(source.id)
          const payload = res.data.data as VideoSourceChannelsResponse
          return {
            source: payload.source,
            supportsSnapshot: Boolean(payload.supports_snapshot),
            channels: payload.channels || [],
          }
        }),
      )

      setTree(channelResults)
      setExpandedSourceIds(new Set(channelResults.map((item) => Number(item.source.id)).filter(Number.isFinite)))

      const nextSelected = preferredSelection
        ? findMatchingSelection(channelResults, preferredSelection)
        : selected
          ? findMatchingSelection(channelResults, selected)
          : firstSelection(channelResults)

      setSelected(nextSelected)
      setRoiDraft(normalizeRoi(nextSelected?.channel.roi_region))
      setAliasDraft(nextSelected?.channel.location_alias || '')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadTree()
  }, [])

  const waitForPreviewContainer = async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (pluginContainerRef.current) return pluginContainerRef.current
      await waitForAnimationFrame()
    }
    return pluginContainerRef.current
  }

  const stopLivePreview = useCallback((options?: { close?: boolean }) => {
    window.WebVideoCtrl?.I_Stop?.()
    if (pluginDeviceHostRef.current) {
      window.WebVideoCtrl?.I_Logout?.(pluginDeviceHostRef.current)
      pluginDeviceHostRef.current = ''
    }
    setPreviewing(false)
    setPreviewLoading(false)
    setPreviewDevice('')
    if (options?.close) setLivePreviewOpen(false)
  }, [])

  useEffect(() => () => stopLivePreview(), [stopLivePreview])

  const selectChannel = (sourceNode: SourceTreeNode, channel: VideoSourceChannel) => {
    if (sourceNode.source.id === null) return
    stopLivePreview({ close: true })
    const nextSelected: SelectedChannel = {
      sourceId: sourceNode.source.id,
      sourceName: sourceNode.source.name,
      sourceType: sourceNode.source.source_type,
      supportsSnapshot: sourceNode.supportsSnapshot && Boolean(channel.supports_snapshot),
      channel,
    }
    setSelected(nextSelected)
    setSnapshot(null)
    setImageSize(null)
    setPreviewError(null)
    setRoiDraft(normalizeRoi(channel.roi_region))
    setAliasDraft(channel.location_alias || '')
  }

  const toggleSource = (sourceId: number) => {
    setExpandedSourceIds((prev) => {
      const next = new Set(prev)
      if (next.has(sourceId)) next.delete(sourceId)
      else next.add(sourceId)
      return next
    })
  }

  const captureSnapshot = async () => {
    if (!selected) return
    setCapturing(true)
    try {
      const res = await adminApi.captureVideoSourceChannelSnapshot(selected.sourceId, selected.channel.channel_id)
      const payload = res.data.data as VideoChannelSnapshot
      setSnapshot(payload)
      setImageSize(null)
      toast.success('抓拍完成')
    } finally {
      setCapturing(false)
    }
  }

  const startLivePreview = async () => {
    if (!selected) return
    stopLivePreview()
    setLivePreviewOpen(true)
    setPreviewLoading(true)
    setPreviewError(null)
    try {
      const container = pluginContainerRef.current || await waitForPreviewContainer()
      if (!container) {
        throw new Error('插件容器未准备好')
      }
      const webVideoCtrl = await loadHikvisionWebVideoCtrl()
      const res = await adminApi.getVideoSourceChannelPluginPreviewConfig(selected.sourceId, selected.channel.channel_id)
      const config = res.data.data as HikvisionPluginPreviewConfig
      const rect = container.getBoundingClientRect()
      await initHikvisionPlugin(webVideoCtrl, container.id, Math.max(320, Math.round(rect.width)), Math.max(240, Math.round(rect.height)))
      await loginHikvisionDevice(webVideoCtrl, config)
      await startHikvisionRealPlay(webVideoCtrl, config)
      pluginDeviceHostRef.current = config.host
      setPreviewDevice(`${config.host}:${config.port} · 通道 ${config.channel_id}`)
      setPreviewing(true)
    } catch (error) {
      const message = error instanceof Error ? error.message : '实时预览启动失败'
      setPreviewError(message)
      toast.error(message)
      stopLivePreview()
    } finally {
      setPreviewLoading(false)
    }
  }

  const saveRoi = async () => {
    if (!selected) return
    setSaving(true)
    try {
      const res = await adminApi.updateVideoSourceChannelRoi(selected.sourceId, selected.channel.channel_id, {
        roi_region: roiDraft,
        image_width: imageSize?.width,
        image_height: imageSize?.height,
      })
      const updatedChannel = res.data.data.channel as VideoSourceChannel
      const nextSelected = {
        ...selected,
        channel: updatedChannel || { ...selected.channel, roi_region: roiDraft },
      }
      setSelected(nextSelected)
      toast.success(roiDraft ? 'ROI 已保存' : 'ROI 已清空')
      await loadTree(nextSelected)
    } finally {
      setSaving(false)
    }
  }

  const saveLocationAlias = async () => {
    if (!selected) return
    setSavingAlias(true)
    try {
      const res = await adminApi.updateVideoSourceChannelLocationAlias(selected.sourceId, selected.channel.channel_id, {
        location_alias: aliasDraft.trim(),
      })
      const updatedChannel = res.data.data.channel as VideoSourceChannel
      const nextSelected = {
        ...selected,
        channel: updatedChannel || { ...selected.channel, location_alias: aliasDraft.trim() },
      }
      setSelected(nextSelected)
      setAliasDraft(nextSelected.channel.location_alias || '')
      toast.success(nextSelected.channel.location_alias ? '地点别名已保存' : '地点别名已清空')
      await loadTree(nextSelected)
    } finally {
      setSavingAlias(false)
    }
  }

  const handleImageLoad = (event: SyntheticEvent<HTMLImageElement>) => {
    setImageSize({
      width: event.currentTarget.naturalWidth,
      height: event.currentTarget.naturalHeight,
    })
  }

  const handleSourceSaved = async () => {
    await loadTree(selected)
  }

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col bg-background">
      <header className="border-b border-border bg-card px-4 py-3 sm:px-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-base font-semibold">通道管理</h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              设备接入、通道筛选、抓拍预览、ROI 与地点别名统一配置。
            </p>
          </div>
          <button
            onClick={() => setSourceDialogOpen(true)}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 sm:w-auto"
          >
            <Plus className="h-4 w-4" />
            管理视频源
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside className="flex flex-col border-b border-border bg-card lg:w-80 lg:flex-shrink-0 lg:border-b-0 lg:border-r">
            <div className="flex h-14 items-center justify-between border-b border-border px-4">
              <div>
                <h1 className="text-sm font-semibold">视频通道管理</h1>
                <p className="text-[11px] text-muted-foreground">海康 NVR/IPC 抓拍与 ROI</p>
              </div>
              <button
                onClick={() => void loadTree()}
                disabled={loading}
                className="rounded-md border border-border p-2 text-muted-foreground transition hover:bg-secondary disabled:opacity-50"
                title="刷新"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              </button>
            </div>

            <div className="max-h-72 overflow-auto p-3 lg:min-h-0 lg:flex-1 lg:max-h-none">
              {tree.length === 0 && !loading ? (
                <div className="rounded-lg border border-dashed border-border bg-secondary/30 px-4 py-6 text-sm text-muted-foreground">
                  暂无视频源。请先在设备接入中创建视频源（启用状态将自动激活）。
                  <button
                    onClick={() => setSourceDialogOpen(true)}
                    className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-border bg-background px-3 py-1.5 text-xs text-foreground transition hover:bg-secondary"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    管理视频源
                  </button>
                </div>
              ) : (
                <div className="space-y-2">
                  {tree.map((sourceNode) => {
                    const sourceId = Number(sourceNode.source.id)
                    const expanded = expandedSourceIds.has(sourceId)
                    return (
                      <div key={sourceNode.source.id ?? sourceNode.source.name} className="rounded-lg border border-border bg-background">
                        <button
                          onClick={() => toggleSource(sourceId)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left"
                        >
                          {expanded ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
                          <Video className="h-4 w-4 text-muted-foreground" />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium">{sourceNode.source.name}</div>
                            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                              <span>{sourceNode.source.source_type === 'hikvision_camera' ? '海康 NVR/IPC' : sourceNode.source.source_type}</span>
                              {sourceNode.source.is_active
                                ? <span className="text-health-green">当前激活</span>
                                : <span className="text-health-amber">未激活</span>}
                            </div>
                          </div>
                        </button>
                        {expanded && (
                          <div className="border-t border-border px-2 py-2">
                            {sourceNode.channels.length === 0 ? (
                              <div className="px-3 py-2 text-xs text-muted-foreground">未发现通道</div>
                            ) : sourceNode.channels.map((channel) => {
                              const active = selected?.sourceId === sourceNode.source.id && selected.channel.channel_id === channel.channel_id
                              return (
                                <button
                                  key={channel.channel_id}
                                  onClick={() => selectChannel(sourceNode, channel)}
                                  className={cn(
                                    'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left transition',
                                    active ? 'bg-primary text-primary-foreground' : 'hover:bg-secondary',
                                  )}
                                >
                                  <Camera className="h-4 w-4 flex-shrink-0" />
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm">{channel.name || `通道 ${channel.channel_id}`}</div>
                                    <div className={cn('text-[11px]', active ? 'text-primary-foreground/75' : 'text-muted-foreground')}>
                                      {formatChannelId(channel.channel_id)}
                                      {channel.roi_region ? ' · ROI 已配置' : ' · 未配置 ROI'}
                                      {channel.location_alias ? ` · ${channel.location_alias}` : ''}
                                    </div>
                                  </div>
                                  {channel.roi_region && <Check className="h-4 w-4 flex-shrink-0 text-health-green" />}
                                </button>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </aside>

          <main className="min-w-0 flex-1 overflow-auto p-4 sm:p-6">
            {!selected ? (
              <div className="flex min-h-[420px] items-center justify-center rounded-xl border border-dashed border-border bg-card">
                <div className="text-center">
                  <ImageIcon className="mx-auto h-8 w-8 text-muted-foreground" />
                  <div className="mt-3 text-sm font-medium">选择一个通道开始配置</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    左侧选择通道后可抓拍画面并绘制 ROI；没有通道时先接入设备。
                  </div>
                  {tree.length === 0 && !loading && (
                    <button
                      onClick={() => setSourceDialogOpen(true)}
                      className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground transition hover:bg-primary/90"
                    >
                      <Plus className="h-4 w-4" />
                      管理视频源
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <section className="rounded-xl border border-border bg-card p-4">
                  <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base font-semibold">{selected.channel.name || `通道 ${selected.channel.channel_id}`}</h2>
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{formatChannelId(selected.channel.channel_id)}</span>
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">{selected.sourceName}</span>
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {selected.channel.host || '未记录地址'}
                        {selected.channel.port ? `:${selected.channel.port}` : ''}
                        {' · '}
                        {selected.supportsSnapshot ? '支持抓拍预览' : '当前类型不支持抓拍'}
                        {selected.channel.location_alias ? ` · 地点别名：${selected.channel.location_alias}` : ''}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => void captureSnapshot()}
                        disabled={!selected.supportsSnapshot || capturing}
                        className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                      >
                        {capturing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                        {capturing ? '抓拍中' : '抓拍预览'}
                      </button>
                      <button
                        onClick={() => void startLivePreview()}
                        disabled={previewLoading}
                        className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary disabled:opacity-50"
                      >
                        {previewLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        {previewing ? '重连实时预览' : '实时预览'}
                      </button>
                      {previewing && (
                        <button
                          onClick={() => {
                            setPreviewError(null)
                            stopLivePreview({ close: true })
                          }}
                          className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary"
                        >
                          <StopCircle className="h-4 w-4" />
                          停止预览
                        </button>
                      )}
                      <button
                        onClick={() => setRoiDraft(null)}
                        disabled={!snapshotUrl || saving}
                        className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary disabled:opacity-50"
                      >
                        <Eraser className="h-4 w-4" />
                        清空 ROI
                      </button>
                      <button
                        onClick={() => void saveRoi()}
                        disabled={saving || (!snapshotUrl && Boolean(roiDraft))}
                        className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary disabled:opacity-50"
                      >
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                        保存 ROI
                      </button>
                    </div>
                  </div>
                </section>

                {livePreviewOpen && (
                  <section className="rounded-xl border border-border bg-card p-4">
                    <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <h3 className="text-sm font-medium">实时画面</h3>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {previewing ? `插件预览在线${previewDevice ? ` · ${previewDevice}` : ''}` : '未连接'}
                        </div>
                      </div>
                      {previewError && <div className="text-xs text-destructive">{previewError}</div>}
                    </div>
                    <div className="relative overflow-hidden rounded-lg border border-border bg-black">
                      <div
                        id="hikvision-plugin-preview"
                        ref={pluginContainerRef}
                        className="aspect-video w-full bg-black"
                      />
                      {!previewing && (
                        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/70 text-xs text-white/70">
                          {previewLoading ? '正在启动海康插件...' : '点击“实时预览”后显示插件窗口'}
                        </div>
                      )}
                    </div>
                  </section>
                )}

                <section className="rounded-xl border border-border bg-card p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
                    <label className="min-w-0 flex-1 space-y-1">
                      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                        <MapPin className="h-3.5 w-3.5" />
                        消费记录地点别名
                      </div>
                      <input
                        value={aliasDraft}
                        onChange={(event) => setAliasDraft(event.target.value)}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                        placeholder="例如 一楼结算台 / 早餐窗口 1"
                      />
                    </label>
                    <button
                      onClick={() => void saveLocationAlias()}
                      disabled={savingAlias || aliasDraft.trim() === (selected.channel.location_alias || '')}
                      className="inline-flex items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-sm transition hover:bg-secondary disabled:opacity-50"
                    >
                      {savingAlias ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      保存别名
                    </button>
                  </div>
                </section>

                <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                  <div className="rounded-xl border border-border bg-card p-4">
                    {snapshotUrl ? (
                      <div>
                        <RoiEditor
                          imageUrl={snapshotUrl}
                          roi={roiDraft}
                          onChange={setRoiDraft}
                          disabled={saving}
                        />
                        <img
                          src={snapshotUrl}
                          alt=""
                          className="hidden"
                          onLoad={handleImageLoad}
                        />
                      </div>
                    ) : (
                      <div className="flex min-h-[460px] items-center justify-center rounded-lg border border-dashed border-border bg-secondary/30">
                        <div className="text-center">
                          <SquareDashedMousePointer className="mx-auto h-8 w-8 text-muted-foreground" />
                          <div className="mt-3 text-sm font-medium">还没有抓拍画面</div>
                          <div className="mt-1 text-xs text-muted-foreground">点击“抓拍预览”后，在图片上拖拽绘制矩形 ROI；实时预览仅用于查看画面。</div>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="space-y-4">
                    <div className="rounded-xl border border-border bg-card p-4">
                      <h3 className="text-sm font-medium">ROI 坐标</h3>
                      <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                        {(['x', 'y', 'w', 'h'] as const).map((key) => (
                          <div key={key} className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                            <div className="font-mono text-[11px] uppercase text-muted-foreground">{key}</div>
                            <div className="mt-1 font-mono text-sm">{roiDraft ? roiDraft[key] : '—'}</div>
                          </div>
                        ))}
                      </div>
                      <div className="mt-3 text-xs text-muted-foreground">
                        坐标基于原始抓拍图片像素。拖拽空白区域可重新绘制，拖拽框内可移动，拖拽四角可缩放。
                      </div>
                    </div>

                    <div className="rounded-xl border border-border bg-card p-4">
                      <h3 className="text-sm font-medium">预览状态</h3>
                      <div className="mt-3 space-y-2 text-xs text-muted-foreground">
                        <div>最近抓拍：{snapshot?.captured_at ? fmtDateTime(snapshot.captured_at) : '—'}</div>
                        <div>图片尺寸：{imageSize ? `${imageSize.width} × ${imageSize.height}` : '—'}</div>
                        <div>已保存 ROI：{selected.channel.roi_region ? '是' : '否'}</div>
                      </div>
                    </div>
                  </div>
                </section>
              </div>
            )}
          </main>
        </div>

      {sourceDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 sm:p-6">
          <div className="flex max-h-[calc(100vh-2rem)] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
            <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-4">
              <div>
                <h2 className="text-base font-semibold">管理视频源</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">新增、编辑、激活、校验设备，并为 NVR/IPC 选择需要同步的通道。</p>
              </div>
              <button
                onClick={() => setSourceDialogOpen(false)}
                className="rounded-md border border-border p-2 text-muted-foreground transition hover:bg-secondary"
                title="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="overflow-auto p-5">
              <VideoSourceManagerPanel
                variant="manager"
                onRefreshConfig={handleSourceSaved}
                onSaved={handleSourceSaved}
                onCancel={() => setSourceDialogOpen(false)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function firstSelection(nodes: SourceTreeNode[]): SelectedChannel | null {
  for (const node of nodes) {
    if (node.source.id === null) continue
    const channel = node.channels[0]
    if (!channel) continue
    return {
      sourceId: node.source.id,
      sourceName: node.source.name,
      sourceType: node.source.source_type,
      supportsSnapshot: node.supportsSnapshot && Boolean(channel.supports_snapshot),
      channel,
    }
  }
  return null
}

function findMatchingSelection(nodes: SourceTreeNode[], selected: SelectedChannel): SelectedChannel | null {
  for (const node of nodes) {
    if (node.source.id !== selected.sourceId) continue
    const channel = node.channels.find((item) => item.channel_id === selected.channel.channel_id)
    if (!channel || node.source.id === null) return null
    return {
      sourceId: node.source.id,
      sourceName: node.source.name,
      sourceType: node.source.source_type,
      supportsSnapshot: node.supportsSnapshot && Boolean(channel.supports_snapshot),
      channel,
    }
  }
  return firstSelection(nodes)
}
