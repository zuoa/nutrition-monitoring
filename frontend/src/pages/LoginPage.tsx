import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Leaf, Loader2, User, QrCode, RefreshCw } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { authApi } from '@/api/client'
import toast from 'react-hot-toast'

type LoginTab = 'password' | 'dingtalk'

declare global {
  interface Window {
    DTFrameLogin: (config: {
      id: string
      width?: number
      height?: number
    }, params: {
      redirect_uri: string
      client_id: string
      scope: string
      response_type: string
      state?: string
      prompt?: string
    }, callback: (result: { authCode?: string; code?: string; errorCode?: number; errorMessage?: string }) => void) => void
  }
}

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<LoginTab>('password')
  const [loading, setLoading] = useState(false)
  const qrContainerRef = useRef<HTMLDivElement>(null)

  // Form states
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [captchaId, setCaptchaId] = useState('')
  const [captchaImage, setCaptchaImage] = useState('')
  const [captchaCode, setCaptchaCode] = useState('')

  useEffect(() => {
    if (user) {
      navigate('/dashboard', { replace: true })
      return
    }

    // Check for DingTalk authCode in URL (callback)
    const params = new URLSearchParams(window.location.search)
    const authCode = params.get('authCode') || params.get('code')
    if (authCode) {
      handleDingTalkCallback(authCode)
    }
  }, [user])

  // Load captcha when switching to password tab
  useEffect(() => {
    if (activeTab === 'password') {
      loadCaptcha()
    }
  }, [activeTab])

  // Initialize DingTalk QR code when tab switches
  useEffect(() => {
    if (activeTab === 'dingtalk' && qrContainerRef.current && window.DTFrameLogin) {
      const appId = import.meta.env.VITE_DINGTALK_APP_ID || ''
      const redirectUri = window.location.origin + '/login'

      window.DTFrameLogin(
        {
          id: 'dingtalk-qr-container',
          width: 300,
          height: 300,
        },
        {
          redirect_uri: redirectUri,
          client_id: appId,
          scope: 'openid',
          response_type: 'code',
          state: 'STATE',
          prompt: 'consent',
        },
        (result) => {
          if (result.errorCode) {
            toast.error(`钉钉登录失败: ${result.errorMessage}`)
            return
          }
          const code = result.authCode || result.code
          if (code) {
            handleDingTalkCallback(code)
          }
        }
      )
    }
  }, [activeTab])

  const loadCaptcha = async () => {
    try {
      const res = await authApi.getCaptcha()
      setCaptchaId(res.data.data.captcha_id)
      setCaptchaImage(res.data.data.captcha_image)
      setCaptchaCode('')
    } catch {
      // Silent fail
    }
  }

  const handleDingTalkCallback = async (authCode: string) => {
    setLoading(true)
    try {
      const res = await authApi.loginDingTalk(authCode)
      const { token, user: userData } = res.data.data
      login(token, userData)
      navigate('/dashboard', { replace: true })
    } catch {
      toast.error('登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) {
      toast.error('请输入账号和密码')
      return
    }
    if (!captchaCode.trim()) {
      toast.error('请输入验证码')
      return
    }
    setLoading(true)
    try {
      const res = await authApi.login({
        username: username.trim(),
        password,
        captcha_id: captchaId,
        captcha_code: captchaCode,
      })
      const { token, user: userData } = res.data.data
      login(token, userData)
      navigate('/dashboard', { replace: true })
    } catch (err: any) {
      // Refresh captcha on error
      loadCaptcha()
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 bg-primary flex-col justify-between p-12">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary-foreground/10 flex items-center justify-center">
            <Leaf className="w-4 h-4 text-primary-foreground" />
          </div>
          <span className="text-primary-foreground font-semibold">NutriTrack</span>
        </div>

        <div>
          <h1 className="text-4xl font-semibold text-primary-foreground leading-tight mb-4">
            基于视觉识别的<br />学生营养健康<br />监测平台
          </h1>
          <p className="text-primary-foreground/50 text-sm leading-relaxed max-w-sm">
            利用 AI 视觉技术自动识别食堂菜品，结合消费记录精准追踪每位学生的营养摄入状况，赋能家校协同健康管理。
          </p>
        </div>

        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '识别准确率', value: '≥92%' },
            { label: '匹配成功率', value: '≥95%' },
            { label: '分析完成时间', value: '<23:00' },
          ].map(({ label, value }) => (
            <div key={label} className="border border-primary-foreground/10 rounded-lg p-4">
              <div className="text-xl font-mono text-primary-foreground">{value}</div>
              <div className="text-xs text-primary-foreground/40 mt-1">{label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-8">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
              <Leaf className="w-4 h-4 text-primary-foreground" />
            </div>
            <span className="font-semibold">营养健康监测平台</span>
          </div>

          <h2 className="text-2xl font-semibold mb-6">欢迎登录</h2>

          {/* Tabs */}
          <div className="flex border-b mb-6">
            <button
              onClick={() => setActiveTab('password')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'password'
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <User className="w-4 h-4" />
              账号密码登录
            </button>
            <button
              onClick={() => setActiveTab('dingtalk')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'dingtalk'
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              <QrCode className="w-4 h-4" />
              钉钉扫码登录
            </button>
          </div>

          {/* Tab Content */}
          {activeTab === 'password' ? (
            <form onSubmit={handlePasswordLogin} className="space-y-4">
              <div>
                <label className="text-sm font-medium mb-1.5 block">账号</label>
                <input
                  type="text"
                  placeholder="请输入账号"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 disabled:opacity-50"
                />
              </div>
              <div>
                <label className="text-sm font-medium mb-1.5 block">密码</label>
                <input
                  type="password"
                  placeholder="请输入密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 disabled:opacity-50"
                />
              </div>
              <div>
                <label className="text-sm font-medium mb-1.5 block">验证码</label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="请输入验证码"
                    value={captchaCode}
                    onChange={(e) => setCaptchaCode(e.target.value)}
                    disabled={loading}
                    maxLength={4}
                    className="flex-1 h-10 px-3 rounded-md border border-input bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 disabled:opacity-50 uppercase"
                  />
                  {captchaImage ? (
                    <div
                      onClick={loadCaptcha}
                      className="w-28 h-10 cursor-pointer rounded-md overflow-hidden border border-input"
                      title="点击刷新验证码"
                    >
                      <img
                        src={captchaImage}
                        alt="验证码"
                        className="w-full h-full object-cover"
                      />
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={loadCaptcha}
                      className="w-28 h-10 flex items-center justify-center rounded-md border border-input bg-muted text-muted-foreground hover:bg-muted/80"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
              <button
                type="submit"
                disabled={loading}
                className="w-full h-10 bg-primary text-primary-foreground text-sm font-medium rounded-md flex items-center justify-center hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : null}
                登录
              </button>
            </form>
          ) : (
            <div className="flex flex-col items-center">
              <div
                id="dingtalk-qr-container"
                ref={qrContainerRef}
                className="w-[300px] h-[300px] flex items-center justify-center bg-muted rounded-lg"
              >
                {!window.DTFrameLogin && (
                  <p className="text-sm text-muted-foreground">加载中...</p>
                )}
              </div>
              <p className="mt-4 text-sm text-muted-foreground text-center">
                请使用钉钉扫描二维码登录
              </p>
            </div>
          )}

          <p className="mt-8 text-xs text-muted-foreground text-center">
            仅限已授权学校工作人员使用
          </p>
        </div>
      </div>
    </div>
  )
}
