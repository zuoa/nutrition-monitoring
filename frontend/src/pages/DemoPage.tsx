import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Camera, Upload, Zap, Brain, Target, AlertCircle, CheckCircle2, Info,
  Sparkles, RefreshCw, ChevronRight, Utensils, Activity, Heart, X,
  Play, Square, Settings, Image as ImageIcon, Loader2
} from 'lucide-react'
import { demoApi, dishApi } from '@/api/client'
import { cn } from '@/lib/utils'
import { useAuth } from '@/contexts/AuthContext'
import type { Dish } from '@/types'
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

// Nutrition bar colors
const NUTRITION_COLORS: Record<string, { bg: string; fill: string; text: string }> = {
  calories: { bg: 'bg-orange-100', fill: 'bg-orange-500', text: 'text-orange-600' },
  protein: { bg: 'bg-red-100', fill: 'bg-red-500', text: 'text-red-600' },
  fat: { bg: 'bg-yellow-100', fill: 'bg-yellow-500', text: 'text-yellow-600' },
  carbohydrate: { bg: 'bg-blue-100', fill: 'bg-blue-500', text: 'text-blue-600' },
  sodium: { bg: 'bg-purple-100', fill: 'bg-purple-500', text: 'text-purple-600' },
  fiber: { bg: 'bg-green-100', fill: 'bg-green-500', text: 'text-green-600' },
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

export default function DemoPage() {
  const [mode, setMode] = useState<'upload' | 'camera'>('upload')
  const [cameraUrl, setCameraUrl] = useState('')
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

  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  const { hasRole } = useAuth()
  const isAdmin = hasRole('admin')

  // Handle file upload
  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    // Read and display image
    const reader = new FileReader()
    reader.onload = async (event) => {
      const base64 = event.target?.result as string
      setCapturedImage(base64)
      setResult(null)

      // Auto-analyze
      await analyzeImage(base64)
    }
    reader.readAsDataURL(file)
  }

  // Capture from camera
  const captureFromCamera = async () => {
    if (!cameraHost) {
      toast.error('请先配置摄像头IP地址')
      return
    }

    setCapturing(true)
    try {
      const res = await demoApi.capture({
        channel_id: channelId,
        host: cameraHost,
        port: parseInt(cameraPort) || 80,
        username: cameraUsername,
        password: cameraPassword,
      })

      const base64 = `data:${res.data.data.content_type};base64,${res.data.data.image_base64}`
      setCapturedImage(base64)
      setResult(null)

      // Auto-analyze
      await analyzeImage(res.data.data.image_base64)
    } catch (err) {
      toast.error('抓拍失败，请检查摄像头配置')
    } finally {
      setCapturing(false)
    }
  }

  // Analyze image
  const analyzeImage = async (base64: string) => {
    // Remove data URL prefix
    const pureBase64 = base64.includes(',') ? base64.split(',')[1] : base64

    setAnalyzing(true)
    try {
      const res = await demoApi.quickAnalyze(pureBase64)
      setResult(res.data.data as AnalysisResult)
    } catch (err) {
      toast.error('分析失败，请重试')
    } finally {
      setAnalyzing(false)
    }
  }

  // Re-analyze current image
  const reanalyze = () => {
    if (capturedImage) {
      analyzeImage(capturedImage)
    }
  }

  // Clear everything
  const clearAll = () => {
    setCapturedImage(null)
    setResult(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-slate-50 p-4 sm:p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center shadow-lg shadow-violet-500/25">
              <Brain className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-gray-900">智能营养分析</h1>
              <p className="text-sm text-gray-500">AI 驱动的实时菜品识别与营养建议</p>
            </div>
          </div>
        </div>

        {/* Main Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Left: Camera/Upload Section */}
          <div className="lg:col-span-5 space-y-4">
            {/* Mode Tabs */}
            <div className="flex gap-2 p-1 bg-gray-100 rounded-lg w-fit">
              <button
                onClick={() => setMode('upload')}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 text-sm rounded-md transition-all',
                  mode === 'upload'
                    ? 'bg-white shadow-sm text-gray-900 font-medium'
                    : 'text-gray-600 hover:text-gray-900'
                )}
              >
                <ImageIcon className="w-4 h-4" />
                上传图片
              </button>
              <button
                onClick={() => setMode('camera')}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 text-sm rounded-md transition-all',
                  mode === 'camera'
                    ? 'bg-white shadow-sm text-gray-900 font-medium'
                    : 'text-gray-600 hover:text-gray-900'
                )}
              >
                <Camera className="w-4 h-4" />
                摄像头抓拍
              </button>
            </div>

            {/* Camera Settings */}
            {mode === 'camera' && (
              <div className="bg-white rounded-xl border border-gray-200 p-4 space-y-3">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-medium text-gray-700">摄像头配置</h3>
                  <button
                    onClick={() => setShowSettings(!showSettings)}
                    className="p-1.5 hover:bg-gray-100 rounded-md"
                  >
                    <Settings className="w-4 h-4 text-gray-500" />
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">IP 地址</label>
                    <input
                      type="text"
                      value={cameraHost}
                      onChange={(e) => setCameraHost(e.target.value)}
                      placeholder="192.168.1.100"
                      className="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-violet-500/20 focus:border-violet-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">端口</label>
                    <input
                      type="text"
                      value={cameraPort}
                      onChange={(e) => setCameraPort(e.target.value)}
                      placeholder="80"
                      className="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-violet-500/20 focus:border-violet-500"
                    />
                  </div>
                </div>

                {showSettings && (
                  <div className="space-y-3 pt-2 border-t border-gray-100">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs text-gray-500 mb-1">通道</label>
                        <input
                          type="text"
                          value={channelId}
                          onChange={(e) => setChannelId(e.target.value)}
                          className="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-gray-500 mb-1">用户名</label>
                        <input
                          type="text"
                          value={cameraUsername}
                          onChange={(e) => setCameraUsername(e.target.value)}
                          className="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">密码</label>
                      <input
                        type="password"
                        value={cameraPassword}
                        onChange={(e) => setCameraPassword(e.target.value)}
                        className="w-full px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg"
                      />
                    </div>
                  </div>
                )}

                <button
                  onClick={captureFromCamera}
                  disabled={capturing || !cameraHost}
                  className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-gradient-to-r from-violet-500 to-purple-600 text-white rounded-xl font-medium shadow-lg shadow-violet-500/25 hover:shadow-violet-500/40 transition-all disabled:opacity-50 disabled:shadow-none"
                >
                  {capturing ? (
                    <>
                      <Loader2 className="w-5 h-5 animate-spin" />
                      抓拍中...
                    </>
                  ) : (
                    <>
                      <Camera className="w-5 h-5" />
                      抓拍并分析
                    </>
                  )}
                </button>
              </div>
            )}

            {/* Upload Area */}
            {mode === 'upload' && (
              <div
                onClick={() => fileInputRef.current?.click()}
                className="relative bg-white rounded-xl border-2 border-dashed border-gray-200 hover:border-violet-400 p-8 text-center cursor-pointer transition-all group"
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  onChange={handleFileSelect}
                  className="hidden"
                />
                <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-violet-50 flex items-center justify-center group-hover:bg-violet-100 transition-colors">
                  <Upload className="w-8 h-8 text-violet-500" />
                </div>
                <p className="text-gray-700 font-medium mb-1">点击或拖拽上传图片</p>
                <p className="text-sm text-gray-400">支持 JPG、PNG 格式</p>
              </div>
            )}

            {/* Image Preview */}
            {capturedImage && (
              <div className="relative bg-white rounded-xl border border-gray-200 overflow-hidden">
                <img
                  src={capturedImage}
                  alt="Captured"
                  className="w-full aspect-[4/3] object-cover"
                />
                {/* Overlay when analyzing */}
                {analyzing && (
                  <div className="absolute inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center">
                    <div className="text-center text-white">
                      <Loader2 className="w-10 h-10 animate-spin mx-auto mb-3" />
                      <p className="font-medium">AI 分析中...</p>
                      <p className="text-sm text-white/70">正在识别菜品并计算营养</p>
                    </div>
                  </div>
                )}
                {/* Actions */}
                <div className="absolute bottom-3 right-3 flex gap-2">
                  <button
                    onClick={clearAll}
                    className="p-2 bg-black/50 hover:bg-black/70 text-white rounded-lg backdrop-blur-sm transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              </div>
            )}

            {/* Quick Actions */}
            {capturedImage && !analyzing && (
              <div className="flex gap-2">
                <button
                  onClick={reanalyze}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  <RefreshCw className="w-4 h-4" />
                  重新分析
                </button>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  <Upload className="w-4 h-4" />
                  更换图片
                </button>
              </div>
            )}
          </div>

          {/* Right: Results Section */}
          <div className="lg:col-span-7 space-y-4">
            {/* Empty State */}
            {!capturedImage && !result && (
              <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
                <div className="w-20 h-20 mx-auto mb-4 rounded-full bg-gradient-to-br from-violet-100 to-purple-100 flex items-center justify-center">
                  <Sparkles className="w-10 h-10 text-violet-400" />
                </div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">准备就绪</h3>
                <p className="text-gray-500 max-w-sm mx-auto">
                  上传餐盘图片或通过摄像头抓拍，AI 将自动识别菜品并分析营养成分
                </p>
              </div>
            )}

            {/* Analysis Results */}
            {result && (
              <>
                {/* Recognized Dishes */}
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center">
                      <Target className="w-4 h-4 text-blue-500" />
                    </div>
                    <h3 className="font-semibold text-gray-900">识别结果</h3>
                    {result.recognized_dishes.length > 0 && (
                      <span className="ml-auto text-sm text-gray-500">
                        共 {result.matched_dishes.length} 道菜品
                      </span>
                    )}
                  </div>

                  {result.matched_dishes.length > 0 ? (
                    <div className="flex flex-wrap gap-2">
                      {result.matched_dishes.map((dish, idx) => (
                        <div
                          key={idx}
                          className="flex items-center gap-2 px-3 py-2 bg-gradient-to-r from-blue-50 to-indigo-50 rounded-lg border border-blue-100"
                        >
                          <Utensils className="w-4 h-4 text-blue-500" />
                          <span className="font-medium text-gray-900">{dish.name}</span>
                          {dish.confidence && (
                            <span className="text-xs text-blue-500 font-mono">
                              {(dish.confidence * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-6 text-gray-400">
                      <AlertCircle className="w-8 h-8 mx-auto mb-2" />
                      <p>未能识别出菜品</p>
                    </div>
                  )}
                </div>

                {/* Nutrition Summary */}
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-green-50 flex items-center justify-center">
                      <Activity className="w-4 h-4 text-green-500" />
                    </div>
                    <h3 className="font-semibold text-gray-900">营养成分</h3>
                    <span className="ml-auto text-xs text-gray-400">占每日推荐</span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                    {Object.entries(result.nutrition.total).map(([key, value]) => {
                      const recommended = result.nutrition.recommended?.[key] || 1
                      const percentage = result.nutrition.percentages?.[key] || Math.min((value / recommended) * 100, 100)
                      const colors = NUTRITION_COLORS[key]
                      const label = NUTRITION_LABELS[key]
                      const unit = NUTRITION_UNITS[key]

                      return (
                        <div key={key} className="relative">
                          <div className="flex items-baseline justify-between mb-1.5">
                            <span className="text-sm text-gray-600">{label}</span>
                            <span className={cn('text-sm font-semibold', colors.text)}>
                              {value.toFixed(0)} <span className="text-xs font-normal">{unit}</span>
                            </span>
                          </div>
                          <div className={cn('h-2 rounded-full overflow-hidden', colors.bg)}>
                            <div
                              className={cn('h-full rounded-full transition-all duration-500', colors.fill)}
                              style={{ width: `${Math.min(percentage, 100)}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-gray-400 mt-1 block">
                            {percentage.toFixed(0)}% 每日推荐
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* AI Suggestions */}
                <div className="bg-white rounded-xl border border-gray-200 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <div className="w-8 h-8 rounded-lg bg-amber-50 flex items-center justify-center">
                      <Sparkles className="w-4 h-4 text-amber-500" />
                    </div>
                    <h3 className="font-semibold text-gray-900">智能建议</h3>
                  </div>

                  <div className="space-y-3">
                    {result.suggestions.map((suggestion, idx) => {
                      const icons = {
                        warning: AlertCircle,
                        info: Info,
                        success: CheckCircle2,
                        suggestion: Lightbulb,
                      }
                      const Icon = icons[suggestion.type] || Info
                      const colors = {
                        warning: 'bg-amber-50 border-amber-200 text-amber-700',
                        info: 'bg-blue-50 border-blue-200 text-blue-700',
                        success: 'bg-green-50 border-green-200 text-green-700',
                        suggestion: 'bg-purple-50 border-purple-200 text-purple-700',
                      }
                      const iconColors = {
                        warning: 'text-amber-500',
                        info: 'text-blue-500',
                        success: 'text-green-500',
                        suggestion: 'text-purple-500',
                      }

                      return (
                        <div
                          key={idx}
                          className={cn(
                            'flex gap-3 p-3 rounded-lg border',
                            colors[suggestion.type]
                          )}
                        >
                          <Icon className={cn('w-5 h-5 flex-shrink-0 mt-0.5', iconColors[suggestion.type])} />
                          <div>
                            <p className="font-medium">{suggestion.title}</p>
                            <p className="text-sm opacity-80 mt-0.5">{suggestion.message}</p>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* Dish Details */}
                {result.matched_dishes.length > 0 && (
                  <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                    <div className="px-5 py-4 border-b border-gray-100">
                      <h3 className="font-semibold text-gray-900">菜品详情</h3>
                    </div>
                    <div className="divide-y divide-gray-100">
                      {result.matched_dishes.map((dish, idx) => (
                        <div key={idx} className="px-5 py-3 flex items-center justify-between hover:bg-gray-50">
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-lg bg-gray-100 flex items-center justify-center">
                              <Utensils className="w-5 h-5 text-gray-400" />
                            </div>
                            <div>
                              <p className="font-medium text-gray-900">{dish.name}</p>
                              {dish.category && (
                                <p className="text-xs text-gray-400">{dish.category}</p>
                              )}
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="text-sm font-medium text-gray-900">
                              {dish.calories || 0} kcal
                            </p>
                            {dish.protein !== undefined && (
                              <p className="text-xs text-gray-400">
                                蛋白质 {dish.protein}g
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Lightbulb icon component
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
