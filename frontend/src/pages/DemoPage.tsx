import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  Activity,
  AlertCircle,
  Brain,
  Camera,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Image as ImageIcon,
  Info,
  Loader2,
  MessageSquare,
  Play,
  RefreshCw,
  Send,
  Settings,
  Sparkles,
  Square,
  Target,
  Upload,
  Utensils,
  Video,
  VideoOff,
  X,
} from 'lucide-react'
import { demoApi } from '@/api/client'
import { cn, fmtDateTime } from '@/lib/utils'
import toast from 'react-hot-toast'

interface RecognizedDish {
  name: string
  confidence: number
}

interface MatchedDish {
  id: number
  name: string
  category?: string
  confidence?: number
  calories?: number
  protein?: number
  fat?: number
  carbohydrate?: number
  sodium?: number
  fiber?: number
  price?: number
}

interface NutritionData {
  total: {
    calories: number
    protein: number
    fat: number
    carbohydrate: number
    sodium: number
    fiber: number
    [key: string]: number
  }
  recommended?: {
    calories: number
    protein: number
    fat: number
    carbohydrate: number
    sodium: number
    fiber: number
    [key: string]: number
  }
  percentages?: Record<string, number>
}

interface Suggestion {
  type: 'warning' | 'info' | 'success' | 'suggestion'
  title: string
  message: string
}

interface AnalysisResult {
  image_base64?: string
  recognized_dishes: RecognizedDish[]
  matched_dishes: MatchedDish[]
  nutrition: NutritionData
  suggestions: Suggestion[]
  notes?: string
  analyzed_at?: string
}

interface ChatMessage {
  id: string
  role: 'assistant' | 'user' | 'system'
  content: string
  meta?: string
}

const NUTRITION_COLORS: Record<string, { rail: string; fill: string; text: string }> = {
  calories: { rail: 'bg-amber-100', fill: 'bg-amber-500', text: 'text-amber-700' },
  protein: { rail: 'bg-emerald-100', fill: 'bg-emerald-500', text: 'text-emerald-700' },
  fat: { rail: 'bg-rose-100', fill: 'bg-rose-500', text: 'text-rose-700' },
  carbohydrate: { rail: 'bg-sky-100', fill: 'bg-sky-500', text: 'text-sky-700' },
  sodium: { rail: 'bg-slate-200', fill: 'bg-slate-700', text: 'text-slate-700' },
  fiber: { rail: 'bg-lime-100', fill: 'bg-lime-500', text: 'text-lime-700' },
}

const NUTRITION_LABELS: Record<string, string> = {
  calories: '热量',
  protein: '蛋白质',
  fat: '脂肪',
  carbohydrate: '碳水',
  sodium: '钠',
  fiber: '纤维',
}

const NUTRITION_UNITS: Record<string, string> = {
  calories: 'kcal',
  protein: 'g',
  fat: 'g',
  carbohydrate: 'g',
  sodium: 'mg',
  fiber: 'g',
}

const QUICK_PROMPTS = [
  '总结这份餐盘的风险',
  '蛋白质够不够',
  '给出更均衡的调整建议',
]

function createMessage(role: ChatMessage['role'], content: string, meta?: string): ChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role,
    content,
    meta,
  }
}

function getNutritionPercent(result: AnalysisResult, key: string): number {
  const explicit = result.nutrition.percentages?.[key]
  if (typeof explicit === 'number') return explicit

  const value = result.nutrition.total[key]
  const recommended = result.nutrition.recommended?.[key]
  if (typeof value !== 'number' || !recommended) return 0

  return (value / recommended) * 100
}

function formatNutritionValue(key: string, value: number): string {
  const unit = NUTRITION_UNITS[key] ?? ''
  const precision = key === 'sodium' || key === 'calories' ? 0 : value < 10 ? 1 : 0
  return `${value.toFixed(precision)} ${unit}`.trim()
}

function getDominantNutrition(result: AnalysisResult) {
  return Object.entries(result.nutrition.total)
    .map(([key, value]) => ({
      key,
      label: NUTRITION_LABELS[key] ?? key,
      value,
      percentage: getNutritionPercent(result, key),
    }))
    .sort((a, b) => b.percentage - a.percentage)[0]
}

function getAverageConfidence(result: AnalysisResult): number | null {
  const matched = result.matched_dishes
    .map((dish) => dish.confidence)
    .filter((value): value is number => typeof value === 'number')
  const recognized = result.recognized_dishes
    .map((dish) => dish.confidence)
    .filter((value): value is number => typeof value === 'number')
  const list = matched.length > 0 ? matched : recognized
  if (list.length === 0) return null
  return list.reduce((sum, value) => sum + value, 0) / list.length
}

function getResultStatus(result: AnalysisResult | null) {
  if (!result) {
    return {
      label: '等待分析',
      description: 'Agent 将在拿到新截图后生成判断',
      badgeClass: 'border-border bg-secondary text-muted-foreground',
      dotClass: 'bg-muted-foreground/60',
    }
  }

  const warningCount = result.suggestions.filter((item) => item.type === 'warning').length
  const dominant = getDominantNutrition(result)
  if (result.matched_dishes.length === 0) {
    return {
      label: '待人工复核',
      description: '本次截图没有稳定匹配到菜品',
      badgeClass: 'border-amber-200 bg-amber-50 text-amber-700',
      dotClass: 'bg-amber-500',
    }
  }

  if (warningCount > 0 || (dominant && dominant.percentage >= 85)) {
    return {
      label: '需重点关注',
      description: '本次结果存在高负荷指标或明确风险提示',
      badgeClass: 'border-rose-200 bg-rose-50 text-rose-700',
      dotClass: 'bg-rose-500',
    }
  }

  return {
    label: '结构基本稳定',
    description: '识别结果完整，可继续让 Agent 深挖建议',
    badgeClass: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    dotClass: 'bg-emerald-500',
  }
}

function buildAutoSummary(result: AnalysisResult): string {
  const dishes = result.matched_dishes.slice(0, 4).map((dish) => dish.name)
  const dominant = getDominantNutrition(result)
  const leadingSuggestion = result.suggestions[0]?.message
  const parts = [
    dishes.length > 0
      ? `已识别 ${result.matched_dishes.length} 道菜，当前餐盘包含 ${dishes.join('、')}`
      : '当前截图没有稳定识别出菜品',
    dominant
      ? `${dominant.label}达到建议摄入的 ${dominant.percentage.toFixed(0)}%`
      : '营养占比数据已经同步',
  ]

  if (leadingSuggestion) {
    parts.push(`优先建议是 ${leadingSuggestion}`)
  }

  return `${parts.join('，')}。`
}

function explainNutrition(result: AnalysisResult, key: string): string {
  const label = NUTRITION_LABELS[key] ?? key
  const value = result.nutrition.total[key]
  if (typeof value !== 'number') {
    return `当前结果里没有 ${label} 的可用数据。`
  }

  const percentage = getNutritionPercent(result, key)
  let assessment = '处于观察区间'
  if (percentage >= 85) assessment = '偏高，需要优先关注'
  else if (percentage >= 60) assessment = '占比不低，建议继续控制'
  else if (percentage <= 25) assessment = '相对偏低，可以补强'

  return `${label}约 ${formatNutritionValue(key, value)}，相当于建议摄入的 ${percentage.toFixed(0)}%，当前判断为${assessment}。`
}

function buildSuggestionDigest(result: AnalysisResult): string {
  if (result.suggestions.length === 0) {
    const dominant = getDominantNutrition(result)
    if (!dominant) return '当前没有额外建议，建议继续观察连续样本。'
    if (dominant.percentage >= 85) {
      return `先控制 ${dominant.label} 负荷，再观察下一次截图的变化。`
    }
    return '当前结构没有明显异常，可以继续保持并结合后续样本判断。'
  }

  return result.suggestions
    .slice(0, 3)
    .map((item) => `${item.title}：${item.message}`)
    .join('；')
}

function buildAgentReply(input: string, result: AnalysisResult | null): string {
  const normalized = input.toLowerCase()

  if (!result) {
    return '我还没有拿到当前餐盘的分析结果。先上传图片、摄像头抓拍，或者从实时流里截图，我再根据结果继续判断。'
  }

  const dishNames = result.matched_dishes.map((dish) => dish.name)
  const dominant = getDominantNutrition(result)
  const warningCount = result.suggestions.filter((item) => item.type === 'warning').length

  if (normalized.includes('识别') || normalized.includes('菜品') || normalized.includes('有什么')) {
    if (dishNames.length === 0) {
      return '这张图里暂时没有稳定匹配到菜品，建议换一个角度再抓拍一次，或者补一张更清晰的截图。'
    }
    return `当前识别到 ${dishNames.length} 道菜：${dishNames.join('、')}。如果你要，我可以继续按热量、蛋白质或风险优先级拆开说明。`
  }

  if (normalized.includes('热量') || normalized.includes('卡路里')) {
    return `${explainNutrition(result, 'calories')} ${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('蛋白')) {
    return `${explainNutrition(result, 'protein')} 如果这是正餐，建议结合主菜和豆制品一起判断是否需要补强。`
  }

  if (normalized.includes('脂肪')) {
    return `${explainNutrition(result, 'fat')} 如果后续还有油炸或重油菜，优先从烹调方式上做控制。`
  }

  if (normalized.includes('碳水')) {
    return `${explainNutrition(result, 'carbohydrate')} 如果需要更稳的午后状态，可以把部分精制主食替换成粗粮。`
  }

  if (normalized.includes('钠') || normalized.includes('盐')) {
    return `${explainNutrition(result, 'sodium')} 如果连续多餐偏高，建议重点看卤味、汤汁和加工食品。`
  }

  if (normalized.includes('纤维') || normalized.includes('蔬菜')) {
    return `${explainNutrition(result, 'fiber')} 纤维主要看蔬菜、豆类和全谷物是否足够。`
  }

  if (normalized.includes('风险') || normalized.includes('注意')) {
    if (warningCount === 0 && dominant && dominant.percentage < 85) {
      return '当前结果里没有明显高风险指标，主要是常规结构优化问题。建议继续结合多次餐盘样本看趋势。'
    }
    return `本次需要优先关注的点有 ${warningCount || 1} 项。${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('建议') || normalized.includes('优化') || normalized.includes('怎么吃')) {
    return `我给出一个执行版建议：${buildSuggestionDigest(result)}`
  }

  if (normalized.includes('总结') || normalized.includes('概览') || normalized.includes('报告')) {
    return buildAutoSummary(result)
  }

  return `执行摘要：${buildAutoSummary(result)} 如果你想更具体一点，可以直接问我热量、蛋白质、风险点，或者让我要一个更均衡的调整方案。`
}

function Lightbulb({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5" />
      <path d="M9 18h6" />
      <path d="M10 22h4" />
    </svg>
  )
}

export default function DemoPage() {
  const [mode, setMode] = useState<'upload' | 'camera' | 'stream'>('upload')
  const [cameraHost, setCameraHost] = useState('')
  const [cameraPort, setCameraPort] = useState('80')
  const [cameraUsername, setCameraUsername] = useState('admin')
  const [cameraPassword, setCameraPassword] = useState('')
  const [channelId, setChannelId] = useState('1')
  const [capturedImage, setCapturedImage] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [capturing, setCapturing] = useState(false)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [showSettings, setShowSettings] = useState(false)

  const [streaming, setStreaming] = useState(false)
  const [streamUrl, setStreamUrl] = useState('')
  const [streamError, setStreamError] = useState<string | null>(null)

  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([
    createMessage('assistant', '我是营养分析 Agent。左侧输入画面后，我会在右侧持续输出判断，你也可以直接追问。', '系统就绪'),
  ])
  const [chatInput, setChatInput] = useState('')
  const [chatBusy, setChatBusy] = useState(false)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const peerConnectionRef = useRef<RTCPeerConnection | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chatViewportRef = useRef<HTMLDivElement>(null)
  const replyTimerRef = useRef<number | null>(null)
  const lastResultSignatureRef = useRef('')

  const stopWebRTCStream = useCallback(() => {
    const ws = wsRef.current
    if (ws) {
      ws.close()
      wsRef.current = null
    }

    if (peerConnectionRef.current) {
      peerConnectionRef.current.close()
      peerConnectionRef.current = null
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null
    }

    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }

    setStreaming(false)
  }, [])

  const startWebRTCStream = useCallback(async () => {
    const source = streamUrl.trim()
    if (!source) {
      setStreamError('请输入流名称，例如 test 或 camera1')
      return
    }

    setStreamError(null)
    setStreaming(true)

    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      })
      peerConnectionRef.current = pc

      const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const wsUrl = `${wsProtocol}://${window.location.host}/rtc/api/ws?src=${encodeURIComponent(source)}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      pc.ontrack = (event) => {
        if (!streamRef.current) {
          streamRef.current = new MediaStream()
          if (videoRef.current) {
            videoRef.current.srcObject = streamRef.current
          }
        }

        streamRef.current.addTrack(event.track)
      }

      pc.onicecandidate = (event) => {
        if (!event.candidate || ws.readyState !== WebSocket.OPEN) return

        ws.send(JSON.stringify({
          type: 'webrtc/candidate',
          value: event.candidate.candidate,
        }))
      }

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
          setStreamError('视频流连接断开，请重试')
          stopWebRTCStream()
        }
      }

      ws.onopen = async () => {
        pc.addTransceiver('video', { direction: 'recvonly' })
        pc.addTransceiver('audio', { direction: 'recvonly' })

        const offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        ws.send(JSON.stringify({
          type: 'webrtc/offer',
          value: pc.localDescription?.sdp,
        }))
      }

      ws.onmessage = async (event) => {
        try {
          const message = JSON.parse(event.data)

          if (message.type === 'webrtc/answer') {
            await pc.setRemoteDescription({
              type: 'answer',
              sdp: message.value,
            })
          } else if (message.type === 'webrtc/candidate') {
            await pc.addIceCandidate({
              candidate: message.value,
              sdpMid: '0',
            })
          }
        } catch (error) {
          console.error('Failed to parse WebRTC message:', error)
        }
      }

      ws.onerror = () => {
        setStreamError('WebSocket 连接失败')
        stopWebRTCStream()
      }

      ws.onclose = () => {
        if (pc.connectionState === 'new' || pc.connectionState === 'connecting') {
          setStreamError('实时流握手被关闭，请检查 go2rtc 流名称和服务状态')
          stopWebRTCStream()
        }
      }
    } catch (error) {
      console.error('WebRTC error:', error)
      setStreamError(error instanceof Error ? error.message : '连接失败')
      stopWebRTCStream()
    }
  }, [stopWebRTCStream, streamUrl])

  const captureFrameFromStream = useCallback(() => {
    if (!videoRef.current || !streaming) return

    const video = videoRef.current
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.drawImage(video, 0, 0)
    const base64 = canvas.toDataURL('image/jpeg', 0.9)
    setCapturedImage(base64)
    setResult(null)
    analyzeImage(base64)
  }, [streaming])

  useEffect(() => {
    return () => {
      stopWebRTCStream()
      if (replyTimerRef.current) {
        window.clearTimeout(replyTimerRef.current)
      }
    }
  }, [stopWebRTCStream])

  useEffect(() => {
    if (!chatViewportRef.current) return
    chatViewportRef.current.scrollTo({
      top: chatViewportRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [chatBusy, chatMessages])

  useEffect(() => {
    if (!result) return

    const signature = JSON.stringify({
      analyzed_at: result.analyzed_at,
      dishes: result.matched_dishes.map((dish) => dish.id || dish.name),
      suggestions: result.suggestions.map((item) => `${item.type}-${item.title}`),
    })

    if (signature === lastResultSignatureRef.current) return
    lastResultSignatureRef.current = signature

    setChatMessages((prev) => [
      ...prev,
      createMessage('assistant', buildAutoSummary(result), '新结果已载入'),
    ])
  }, [result])

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return

    const reader = new FileReader()
    reader.onload = async (loadEvent) => {
      const base64 = loadEvent.target?.result as string
      setCapturedImage(base64)
      setResult(null)
      await analyzeImage(base64)
    }
    reader.readAsDataURL(file)
  }

  const captureFromCamera = async () => {
    if (!cameraHost) {
      toast.error('请先配置摄像头 IP 地址')
      return
    }

    setCapturing(true)
    try {
      const response = await demoApi.capture({
        channel_id: channelId,
        host: cameraHost,
        port: parseInt(cameraPort, 10) || 80,
        username: cameraUsername,
        password: cameraPassword,
      })

      const base64 = `data:${response.data.data.content_type};base64,${response.data.data.image_base64}`
      setCapturedImage(base64)
      setResult(null)
      await analyzeImage(response.data.data.image_base64)
    } catch (error) {
      toast.error('抓拍失败，请检查摄像头配置')
    } finally {
      setCapturing(false)
    }
  }

  const analyzeImage = async (base64: string) => {
    const pureBase64 = base64.includes(',') ? base64.split(',')[1] : base64

    setAnalyzing(true)
    try {
      const response = await demoApi.quickAnalyze(pureBase64)
      setResult(response.data.data as AnalysisResult)
    } catch (error) {
      toast.error('分析失败，请重试')
    } finally {
      setAnalyzing(false)
    }
  }

  const reanalyze = () => {
    if (capturedImage) {
      analyzeImage(capturedImage)
    }
  }

  const clearAll = () => {
    setCapturedImage(null)
    setResult(null)
    setStreamError(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const submitChat = (content: string) => {
    const trimmed = content.trim()
    if (!trimmed || chatBusy) return

    setChatMessages((prev) => [...prev, createMessage('user', trimmed, '实时提问')])
    setChatBusy(true)

    if (replyTimerRef.current) {
      window.clearTimeout(replyTimerRef.current)
    }

    replyTimerRef.current = window.setTimeout(() => {
      setChatMessages((prev) => [
        ...prev,
        createMessage('assistant', buildAgentReply(trimmed, result), 'Agent 回复'),
      ])
      setChatBusy(false)
      replyTimerRef.current = null
    }, 420)
  }

  const handleChatSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const current = chatInput
    setChatInput('')
    submitChat(current)
  }

  const status = getResultStatus(result)
  const averageConfidence = result ? getAverageConfidence(result) : null
  const warningCount = result?.suggestions.filter((item) => item.type === 'warning').length ?? 0
  const dominantNutrition = result ? getDominantNutrition(result) : null
  const sourceText = mode === 'stream'
    ? streaming
      ? `实时流 ${streamUrl || '未命名'} 在线`
      : '等待连接实时流'
    : mode === 'camera'
      ? cameraHost
        ? `摄像头 ${cameraHost}:${cameraPort}`
        : '等待填写摄像头地址'
      : capturedImage
        ? '上传样本已载入'
        : '等待上传图片'

  const insightCards = result
    ? [
        {
          title: 'Agent 判断',
          value: status.label,
          detail: status.description,
          icon: Brain,
        },
        {
          title: '主要负荷',
          value: dominantNutrition
            ? `${dominantNutrition.label} ${dominantNutrition.percentage.toFixed(0)}%`
            : '等待计算',
          detail: dominantNutrition
            ? `${formatNutritionValue(dominantNutrition.key, dominantNutrition.value)}`
            : '暂无营养分布',
          icon: Activity,
        },
        {
          title: '匹配质量',
          value: averageConfidence !== null ? `${(averageConfidence * 100).toFixed(0)}%` : '—',
          detail: result.matched_dishes.length > 0
            ? `已锁定 ${result.matched_dishes.length} 道菜`
            : '未形成稳定匹配',
          icon: Target,
        },
      ]
    : []

  return (
    <div className="min-h-full bg-[radial-gradient(circle_at_top_left,rgba(47,175,127,0.12),transparent_28%),radial-gradient(circle_at_85%_20%,rgba(15,23,42,0.06),transparent_26%),linear-gradient(180deg,rgba(255,255,255,0.92),rgba(248,250,252,0.96))] p-4 sm:p-6">
      <div className="mx-auto max-w-[1600px] space-y-5">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={handleFileSelect}
          className="hidden"
        />

        <section className="relative overflow-hidden rounded-[28px] border border-border/80 bg-card/90 px-5 py-5 shadow-[0_30px_100px_rgba(15,23,42,0.06)] backdrop-blur sm:px-6">
          <div className="absolute inset-0 bg-[linear-gradient(115deg,transparent_0%,rgba(255,255,255,0.7)_20%,transparent_42%),linear-gradient(0deg,rgba(255,255,255,0.25),rgba(255,255,255,0.25))]" />
          <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-foreground/10 bg-foreground text-background shadow-[0_14px_40px_rgba(15,23,42,0.14)]">
                  <Brain className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.3em] text-muted-foreground">
                    Agent Console
                  </div>
                  <h1 className="text-2xl font-semibold tracking-tight text-foreground">智能演示工作台</h1>
                </div>
              </div>
              <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
                把演示页改成一个真正的分析席位。左侧持续接入实时画面与截图，右侧同步输出报告、建议和对话，不再像单次 AI 营销演示。
              </p>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl border border-border/80 bg-background/70 px-4 py-3">
                <div className="text-[11px] font-mono uppercase tracking-[0.24em] text-muted-foreground">Source</div>
                <div className="mt-2 text-sm font-medium text-foreground">{sourceText}</div>
              </div>
              <div className="rounded-2xl border border-border/80 bg-background/70 px-4 py-3">
                <div className="text-[11px] font-mono uppercase tracking-[0.24em] text-muted-foreground">Status</div>
                <div className="mt-2 flex items-center gap-2 text-sm font-medium text-foreground">
                  <span className={cn('h-2.5 w-2.5 rounded-full', status.dotClass)} />
                  {status.label}
                </div>
              </div>
              <div className="rounded-2xl border border-border/80 bg-background/70 px-4 py-3">
                <div className="text-[11px] font-mono uppercase tracking-[0.24em] text-muted-foreground">Updated</div>
                <div className="mt-2 text-sm font-medium text-foreground">
                  {result?.analyzed_at ? fmtDateTime(result.analyzed_at) : '等待首个结果'}
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(420px,0.85fr)]">
          <section className="space-y-5">
            <div className="rounded-[28px] border border-border/80 bg-card/90 p-5 shadow-[0_24px_80px_rgba(15,23,42,0.05)] backdrop-blur">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Input Routing</div>
                  <h2 className="mt-2 text-lg font-semibold text-foreground">采集模式与连接控制</h2>
                </div>
                <div className="flex flex-wrap gap-2">
                  {[
                    { id: 'upload', label: '上传图片', icon: ImageIcon },
                    { id: 'camera', label: '摄像头抓拍', icon: Camera },
                    { id: 'stream', label: '实时预览', icon: Video },
                  ].map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      onClick={() => {
                        if (id !== 'stream') stopWebRTCStream()
                        setMode(id as 'upload' | 'camera' | 'stream')
                      }}
                      className={cn(
                        'inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm transition-all',
                        mode === id
                          ? 'border-foreground bg-foreground text-background shadow-[0_12px_30px_rgba(15,23,42,0.14)]'
                          : 'border-border bg-background text-muted-foreground hover:border-foreground/20 hover:text-foreground',
                      )}
                    >
                      <Icon className="h-4 w-4" />
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mt-5 rounded-[24px] border border-border/70 bg-background/80 p-4">
                {mode === 'upload' && (
                  <div
                    onClick={() => fileInputRef.current?.click()}
                    className="group relative flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-[22px] border border-dashed border-border bg-card px-6 text-center transition-colors hover:border-foreground/30 hover:bg-secondary/50"
                  >
                    <div className="flex h-16 w-16 items-center justify-center rounded-full border border-border bg-secondary text-foreground transition-transform duration-200 group-hover:scale-105">
                      <Upload className="h-7 w-7" />
                    </div>
                    <div className="mt-4 text-base font-medium text-foreground">拖入餐盘图片，或点击上传</div>
                    <div className="mt-1 text-sm text-muted-foreground">支持 JPG、PNG，上传后自动触发分析</div>
                  </div>
                )}

                {mode === 'camera' && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium text-foreground">摄像头配置</div>
                        <div className="text-xs text-muted-foreground">用于单次抓拍并立即分析</div>
                      </div>
                      <button
                        onClick={() => setShowSettings((value) => !value)}
                        className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
                      >
                        <Settings className="h-3.5 w-3.5" />
                        高级参数
                      </button>
                    </div>

                    <div className="grid gap-3 md:grid-cols-2">
                      <label className="block">
                        <div className="mb-1.5 text-xs font-medium text-muted-foreground">IP 地址</div>
                        <input
                          type="text"
                          value={cameraHost}
                          onChange={(event) => setCameraHost(event.target.value)}
                          placeholder="192.168.1.100"
                          className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                        />
                      </label>
                      <label className="block">
                        <div className="mb-1.5 text-xs font-medium text-muted-foreground">端口</div>
                        <input
                          type="text"
                          value={cameraPort}
                          onChange={(event) => setCameraPort(event.target.value)}
                          placeholder="80"
                          className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                        />
                      </label>
                    </div>

                    {showSettings && (
                      <div className="grid gap-3 md:grid-cols-2">
                        <label className="block">
                          <div className="mb-1.5 text-xs font-medium text-muted-foreground">通道</div>
                          <input
                            type="text"
                            value={channelId}
                            onChange={(event) => setChannelId(event.target.value)}
                            className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                          />
                        </label>
                        <label className="block">
                          <div className="mb-1.5 text-xs font-medium text-muted-foreground">用户名</div>
                          <input
                            type="text"
                            value={cameraUsername}
                            onChange={(event) => setCameraUsername(event.target.value)}
                            className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                          />
                        </label>
                        <label className="block md:col-span-2">
                          <div className="mb-1.5 text-xs font-medium text-muted-foreground">密码</div>
                          <input
                            type="password"
                            value={cameraPassword}
                            onChange={(event) => setCameraPassword(event.target.value)}
                            className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                          />
                        </label>
                      </div>
                    )}

                    <button
                      onClick={captureFromCamera}
                      disabled={capturing || !cameraHost}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-foreground px-4 py-3 text-sm font-medium text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {capturing ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          抓拍中
                        </>
                      ) : (
                        <>
                          <Camera className="h-4 w-4" />
                          抓拍并送入 Agent
                        </>
                      )}
                    </button>
                  </div>
                )}

                {mode === 'stream' && (
                  <div className="space-y-4">
                    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_160px]">
                      <label className="block">
                        <div className="mb-1.5 text-xs font-medium text-muted-foreground">流名称</div>
                        <input
                          type="text"
                          value={streamUrl}
                          onChange={(event) => setStreamUrl(event.target.value)}
                          placeholder="camera1"
                          className="w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm outline-none transition focus:border-foreground/20"
                        />
                      </label>
                      <div className="rounded-2xl border border-border bg-card px-4 py-3">
                        <div className="text-xs font-medium text-muted-foreground">链路状态</div>
                        <div className="mt-2 flex items-center gap-2 text-sm font-medium text-foreground">
                          <span className={cn('h-2.5 w-2.5 rounded-full', streaming ? 'bg-emerald-500' : 'bg-muted-foreground/50')} />
                          {streaming ? '在线' : '待连接'}
                        </div>
                      </div>
                    </div>

                    {streamError && (
                      <div className="flex items-start gap-2 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
                        {streamError}
                      </div>
                    )}

                    <div className="flex flex-wrap gap-2">
                      {!streaming ? (
                        <button
                          onClick={startWebRTCStream}
                          className="inline-flex items-center gap-2 rounded-2xl bg-foreground px-4 py-3 text-sm font-medium text-background transition hover:bg-foreground/90"
                        >
                          <Play className="h-4 w-4" />
                          建立实时预览
                        </button>
                      ) : (
                        <>
                          <button
                            onClick={stopWebRTCStream}
                            className="inline-flex items-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm font-medium text-foreground transition hover:bg-secondary"
                          >
                            <Square className="h-4 w-4" />
                            停止预览
                          </button>
                          <button
                            onClick={captureFrameFromStream}
                            disabled={analyzing}
                            className="inline-flex items-center gap-2 rounded-2xl bg-foreground px-4 py-3 text-sm font-medium text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {analyzing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Camera className="h-4 w-4" />}
                            截图并分析
                          </button>
                        </>
                      )}
                    </div>

                    <div className="rounded-2xl border border-border bg-card px-4 py-3 text-xs leading-6 text-muted-foreground">
                      go2rtc 已接入时，在这里填写流名称即可。预览建立后可以直接截图，把当前帧送到右侧报告和 Agent 对话区。
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-[28px] border border-border/80 bg-card/90 p-5 shadow-[0_24px_80px_rgba(15,23,42,0.05)] backdrop-blur">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Visual Deck</div>
                  <h2 className="mt-2 text-lg font-semibold text-foreground">实时预览与截图画面</h2>
                </div>
                <div className={cn('inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs', status.badgeClass)}>
                  <span className={cn('h-2 w-2 rounded-full', status.dotClass)} />
                  {status.label}
                </div>
              </div>

              <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                <div className="relative overflow-hidden rounded-[24px] border border-border bg-[#0f172a]">
                  <div className="absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.04)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.04)_1px,transparent_1px)] bg-[size:24px_24px]" />
                  <div className="absolute left-4 top-4 z-10 inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/35 px-3 py-1.5 text-xs text-white/80 backdrop-blur">
                    <span className={cn('h-2 w-2 rounded-full', streaming ? 'bg-emerald-400' : capturedImage ? 'bg-sky-400' : 'bg-white/40')} />
                    {mode === 'stream' ? 'Live feed' : 'Capture frame'}
                  </div>

                  {mode === 'stream' ? (
                    <div className="relative aspect-[16/10]">
                      <video
                        ref={videoRef}
                        autoPlay
                        playsInline
                        muted
                        className="h-full w-full object-cover"
                      />
                      {!streaming && (
                        <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60">
                          <div className="text-center text-white">
                            <VideoOff className="mx-auto h-12 w-12 opacity-50" />
                            <p className="mt-3 text-sm text-white/70">建立连接后在这里显示实时画面</p>
                          </div>
                        </div>
                      )}
                    </div>
                  ) : capturedImage ? (
                    <div className="aspect-[16/10]">
                      <img src={capturedImage} alt="Captured preview" className="h-full w-full object-cover" />
                    </div>
                  ) : (
                    <div className="flex aspect-[16/10] items-center justify-center bg-slate-950/40 px-8">
                      <div className="text-center text-white">
                        <Camera className="mx-auto h-12 w-12 opacity-50" />
                        <p className="mt-3 text-sm text-white/75">当前还没有可展示的餐盘画面</p>
                        <p className="mt-1 text-xs text-white/45">左侧上传、抓拍或接入实时流后自动更新</p>
                      </div>
                    </div>
                  )}

                  {analyzing && (
                    <div className="absolute inset-0 flex items-center justify-center bg-slate-950/55 backdrop-blur-sm">
                      <div className="text-center text-white">
                        <Loader2 className="mx-auto h-10 w-10 animate-spin" />
                        <p className="mt-3 text-sm font-medium">Agent 正在解析截图</p>
                        <p className="mt-1 text-xs text-white/60">识别菜品、计算营养、生成建议</p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="space-y-4">
                  <div className="overflow-hidden rounded-[24px] border border-border bg-background">
                    <div className="flex items-center justify-between border-b border-border px-4 py-3">
                      <div>
                        <div className="text-sm font-medium text-foreground">最新截图</div>
                        <div className="text-xs text-muted-foreground">用于本轮报告与问答上下文</div>
                      </div>
                      {capturedImage && (
                        <button
                          onClick={clearAll}
                          className="rounded-full border border-border p-2 text-muted-foreground transition-colors hover:text-foreground"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      )}
                    </div>

                    {capturedImage ? (
                      <img src={capturedImage} alt="Snapshot" className="aspect-[4/3] w-full object-cover" />
                    ) : (
                      <div className="flex aspect-[4/3] items-center justify-center bg-secondary/70 px-6 text-center">
                        <div>
                          <ImageIcon className="mx-auto h-10 w-10 text-muted-foreground/60" />
                          <p className="mt-3 text-sm text-muted-foreground">截图会固定在这里，方便对照报告</p>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="rounded-[24px] border border-border bg-background p-4">
                    <div className="text-[11px] font-mono uppercase tracking-[0.24em] text-muted-foreground">Control</div>
                    <div className="mt-3 space-y-2">
                      <button
                        onClick={reanalyze}
                        disabled={!capturedImage || analyzing}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm font-medium text-foreground transition hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <RefreshCw className={cn('h-4 w-4', analyzing && 'animate-spin')} />
                        重新分析当前截图
                      </button>
                      <button
                        onClick={() => fileInputRef.current?.click()}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm font-medium text-foreground transition hover:bg-secondary"
                      >
                        <Upload className="h-4 w-4" />
                        更换输入样本
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="space-y-5">
            <div className="rounded-[28px] border border-border/80 bg-card/90 p-5 shadow-[0_24px_80px_rgba(15,23,42,0.05)] backdrop-blur">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Analysis Report</div>
                  <h2 className="mt-2 text-lg font-semibold text-foreground">分析结果与建议</h2>
                </div>
                <div className="rounded-2xl border border-border bg-background px-4 py-3 text-sm text-muted-foreground">
                  {result?.analyzed_at ? `分析时间 ${fmtDateTime(result.analyzed_at)}` : '等待首个分析结果'}
                </div>
              </div>

              {!result && (
                <div className="mt-5 rounded-[24px] border border-dashed border-border bg-background/70 px-8 py-12 text-center">
                  <Sparkles className="mx-auto h-10 w-10 text-muted-foreground/50" />
                  <h3 className="mt-4 text-lg font-semibold text-foreground">报告区待命中</h3>
                  <p className="mx-auto mt-2 max-w-md text-sm leading-7 text-muted-foreground">
                    左侧一旦产生新截图，右侧会立即刷新识别结果、营养占比、Agent 判断和执行建议。
                  </p>
                </div>
              )}

              {result && (
                <div className="mt-5 space-y-4">
                  <div className="grid gap-3 md:grid-cols-3">
                    {insightCards.map(({ title, value, detail, icon: Icon }) => (
                      <div key={title} className="rounded-[22px] border border-border bg-background px-4 py-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="text-[11px] font-mono uppercase tracking-[0.2em] text-muted-foreground">{title}</div>
                            <div className="mt-3 text-lg font-semibold text-foreground">{value}</div>
                            <div className="mt-1 text-sm text-muted-foreground">{detail}</div>
                          </div>
                          <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-border bg-card">
                            <Icon className="h-4 w-4 text-foreground" />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
                    <div className="space-y-4">
                      <div className="rounded-[24px] border border-border bg-background p-4">
                        <div className="flex items-center gap-2">
                          <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-card">
                            <Utensils className="h-4 w-4 text-foreground" />
                          </div>
                          <div>
                            <div className="text-sm font-medium text-foreground">识别结果</div>
                            <div className="text-xs text-muted-foreground">本轮稳定匹配到的菜品</div>
                          </div>
                        </div>

                        {result.matched_dishes.length > 0 ? (
                          <div className="mt-4 flex flex-wrap gap-2">
                            {result.matched_dishes.map((dish) => (
                              <div
                                key={`${dish.id}-${dish.name}`}
                                className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-2 text-sm text-foreground"
                              >
                                <span>{dish.name}</span>
                                {typeof dish.confidence === 'number' && (
                                  <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-mono text-muted-foreground">
                                    {(dish.confidence * 100).toFixed(0)}%
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="mt-4 rounded-2xl border border-dashed border-border bg-card px-4 py-6 text-sm text-muted-foreground">
                            当前没有稳定匹配结果，建议调整光线、角度或食物摆放后再试。
                          </div>
                        )}
                      </div>

                      <div className="rounded-[24px] border border-border bg-background p-4">
                        <div className="flex items-center gap-2">
                          <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-card">
                            <Activity className="h-4 w-4 text-foreground" />
                          </div>
                          <div>
                            <div className="text-sm font-medium text-foreground">营养占比</div>
                            <div className="text-xs text-muted-foreground">相对推荐摄入的即时压力</div>
                          </div>
                        </div>

                        <div className="mt-4 grid gap-3 sm:grid-cols-2">
                          {Object.entries(result.nutrition.total).map(([key, value]) => {
                            const percentage = getNutritionPercent(result, key)
                            const colors = NUTRITION_COLORS[key] ?? NUTRITION_COLORS.calories
                            return (
                              <div key={key} className="rounded-2xl border border-border bg-card px-4 py-3">
                                <div className="flex items-baseline justify-between gap-3">
                                  <span className="text-sm text-muted-foreground">{NUTRITION_LABELS[key] ?? key}</span>
                                  <span className={cn('text-sm font-semibold', colors.text)}>
                                    {formatNutritionValue(key, value)}
                                  </span>
                                </div>
                                <div className={cn('mt-3 h-2 overflow-hidden rounded-full', colors.rail)}>
                                  <div
                                    className={cn('h-full rounded-full transition-all duration-500', colors.fill)}
                                    style={{ width: `${Math.min(percentage, 100)}%` }}
                                  />
                                </div>
                                <div className="mt-2 text-[11px] font-mono text-muted-foreground">
                                  {percentage.toFixed(0)}% of daily reference
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    </div>

                    <div className="space-y-4">
                      <div className="rounded-[24px] border border-border bg-background p-4">
                        <div className="flex items-center gap-2">
                          <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-card">
                            <Lightbulb className="h-4 w-4 text-foreground" />
                          </div>
                          <div>
                            <div className="text-sm font-medium text-foreground">执行建议</div>
                            <div className="text-xs text-muted-foreground">按优先级输出</div>
                          </div>
                        </div>

                        <div className="mt-4 space-y-3">
                          {result.suggestions.length > 0 ? (
                            result.suggestions.map((item, index) => {
                              const tone = {
                                warning: 'border-rose-200 bg-rose-50 text-rose-700',
                                info: 'border-sky-200 bg-sky-50 text-sky-700',
                                success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
                                suggestion: 'border-amber-200 bg-amber-50 text-amber-700',
                              }[item.type]
                              const Icon = {
                                warning: AlertCircle,
                                info: Info,
                                success: CheckCircle2,
                                suggestion: Sparkles,
                              }[item.type]

                              return (
                                <div key={`${item.title}-${index}`} className={cn('rounded-2xl border px-4 py-3', tone)}>
                                  <div className="flex items-start gap-3">
                                    <Icon className="mt-0.5 h-4 w-4 flex-shrink-0" />
                                    <div>
                                      <div className="text-sm font-medium">{item.title}</div>
                                      <div className="mt-1 text-sm opacity-90">{item.message}</div>
                                    </div>
                                  </div>
                                </div>
                              )
                            })
                          ) : (
                            <div className="rounded-2xl border border-dashed border-border bg-card px-4 py-6 text-sm text-muted-foreground">
                              当前没有明确建议，说明本轮结果更接近观察样本。
                            </div>
                          )}
                        </div>
                      </div>

                      <div className="rounded-[24px] border border-border bg-background p-4">
                        <div className="flex items-center gap-2">
                          <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-card">
                            <Clock3 className="h-4 w-4 text-foreground" />
                          </div>
                          <div>
                            <div className="text-sm font-medium text-foreground">报告摘要</div>
                            <div className="text-xs text-muted-foreground">Agent 的即时结论</div>
                          </div>
                        </div>

                        <div className="mt-4 space-y-3 text-sm leading-7 text-muted-foreground">
                          <p>{buildAutoSummary(result)}</p>
                          {result.notes && (
                            <div className="rounded-2xl border border-border bg-card px-4 py-3 text-foreground">
                              {result.notes}
                            </div>
                          )}
                          <div className="grid gap-2 text-xs text-muted-foreground">
                            <div className="flex items-center justify-between rounded-2xl border border-border bg-card px-3 py-2">
                              <span>预警条目</span>
                              <span className="font-mono text-foreground">{warningCount}</span>
                            </div>
                            <div className="flex items-center justify-between rounded-2xl border border-border bg-card px-3 py-2">
                              <span>识别菜品</span>
                              <span className="font-mono text-foreground">{result.matched_dishes.length}</span>
                            </div>
                            <div className="flex items-center justify-between rounded-2xl border border-border bg-card px-3 py-2">
                              <span>对话上下文</span>
                              <span className="font-mono text-foreground">已接入当前截图</span>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {result.matched_dishes.length > 0 && (
                    <div className="rounded-[24px] border border-border bg-background p-4">
                      <div className="flex items-center gap-2">
                        <div className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-card">
                          <ChevronRight className="h-4 w-4 text-foreground" />
                        </div>
                        <div>
                          <div className="text-sm font-medium text-foreground">菜品细项</div>
                          <div className="text-xs text-muted-foreground">逐项查看热量与营养概况</div>
                        </div>
                      </div>

                      <div className="mt-4 space-y-2">
                        {result.matched_dishes.map((dish) => (
                          <div key={`${dish.id}-${dish.name}-detail`} className="flex items-center justify-between rounded-2xl border border-border bg-card px-4 py-3">
                            <div className="min-w-0">
                              <div className="truncate text-sm font-medium text-foreground">{dish.name}</div>
                              <div className="mt-1 text-xs text-muted-foreground">
                                {dish.category || '未分类'}
                                {typeof dish.protein === 'number' && ` · 蛋白质 ${dish.protein}g`}
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-sm font-semibold text-foreground">{dish.calories ?? 0} kcal</div>
                              <div className="text-xs text-muted-foreground">
                                {typeof dish.fat === 'number' ? `脂肪 ${dish.fat}g` : '营养数据已接入'}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="rounded-[28px] border border-border/80 bg-card/90 p-5 shadow-[0_24px_80px_rgba(15,23,42,0.05)] backdrop-blur">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <div className="text-[11px] font-mono uppercase tracking-[0.28em] text-muted-foreground">Live Dialogue</div>
                  <h2 className="mt-2 text-lg font-semibold text-foreground">营养 Agent 对话</h2>
                </div>
                <div className="flex flex-wrap gap-2">
                  {QUICK_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      onClick={() => submitChat(prompt)}
                      disabled={chatBusy}
                      className="rounded-full border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground transition hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>

              <div
                ref={chatViewportRef}
                className="mt-5 h-[360px] space-y-3 overflow-y-auto rounded-[24px] border border-border bg-background p-4"
              >
                {chatMessages.map((message) => (
                  <div
                    key={message.id}
                    className={cn(
                      'max-w-[92%] rounded-2xl px-4 py-3 text-sm leading-7',
                      message.role === 'user' && 'ml-auto bg-foreground text-background',
                      message.role === 'assistant' && 'border border-border bg-card text-foreground',
                      message.role === 'system' && 'border border-amber-200 bg-amber-50 text-amber-700',
                    )}
                  >
                    <div className="mb-1 flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em] opacity-70">
                      {message.role === 'user' ? <Send className="h-3 w-3" /> : <MessageSquare className="h-3 w-3" />}
                      {message.meta || (message.role === 'user' ? 'User' : 'Agent')}
                    </div>
                    <div>{message.content}</div>
                  </div>
                ))}

                {chatBusy && (
                  <div className="max-w-[92%] rounded-2xl border border-border bg-card px-4 py-3 text-sm text-foreground">
                    <div className="mb-1 flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.18em] text-muted-foreground">
                      <MessageSquare className="h-3 w-3" />
                      Agent 正在组织回复
                    </div>
                    <div className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      基于当前截图和报告生成回答
                    </div>
                  </div>
                )}
              </div>

              <form onSubmit={handleChatSubmit} className="mt-4 rounded-[24px] border border-border bg-background p-3">
                <div className="flex gap-3">
                  <input
                    value={chatInput}
                    onChange={(event) => setChatInput(event.target.value)}
                    placeholder="直接问：风险在哪里？蛋白质够吗？怎么优化？"
                    className="h-12 flex-1 rounded-2xl border border-border bg-card px-4 text-sm outline-none transition focus:border-foreground/20"
                  />
                  <button
                    type="submit"
                    disabled={!chatInput.trim() || chatBusy}
                    className="inline-flex h-12 items-center justify-center gap-2 rounded-2xl bg-foreground px-4 text-sm font-medium text-background transition hover:bg-foreground/90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <Send className="h-4 w-4" />
                    发送
                  </button>
                </div>
              </form>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
