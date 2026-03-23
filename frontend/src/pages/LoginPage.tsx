import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Leaf, Loader2 } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { authApi } from '@/api/client'
import toast from 'react-hot-toast'

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const [devMode, setDevMode] = useState(false)

  useEffect(() => {
    if (user) { navigate('/dashboard', { replace: true }); return }

    // Check for DingTalk authCode in URL
    const params = new URLSearchParams(window.location.search)
    const authCode = params.get('authCode') || params.get('code')
    if (authCode) {
      handleLogin(authCode)
    }
  }, [user])

  const handleLogin = async (authCode: string) => {
    setLoading(true)
    try {
      const res = await authApi.login(authCode)
      const { token, user: userData } = res.data.data
      login(token, userData)
      navigate('/dashboard', { replace: true })
    } catch {
      toast.error('登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  // Dev: simulate login with demo token
  const handleDevLogin = async () => {
    setLoading(true)
    try {
      const res = await authApi.login('dev-demo-code')
      const { token, user: userData } = res.data.data
      login(token, userData)
      navigate('/dashboard', { replace: true })
    } catch {
      // Fallback: inject a mock user for UI preview
      login('dev-token', {
        id: 1, dingtalk_user_id: 'dev-001', name: '管理员',
        role: 'admin', is_active: true,
      })
      navigate('/dashboard', { replace: true })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel */}
      <div className="hidden lg:flex lg:w-1/2 bg-foreground flex-col justify-between p-12">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-background/10 flex items-center justify-center">
            <Leaf className="w-4 h-4 text-background" />
          </div>
          <span className="text-background font-semibold">NutriTrack</span>
        </div>

        <div>
          <h1 className="text-4xl font-display text-background leading-tight mb-4">
            基于视觉识别的<br />学生营养健康<br />监测平台
          </h1>
          <p className="text-background/50 text-sm leading-relaxed max-w-sm">
            利用 AI 视觉技术自动识别食堂菜品，结合消费记录精准追踪每位学生的营养摄入状况，赋能家校协同健康管理。
          </p>
        </div>

        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '识别准确率', value: '≥92%' },
            { label: '匹配成功率', value: '≥95%' },
            { label: '分析完成时间', value: '<23:00' },
          ].map(({ label, value }) => (
            <div key={label} className="border border-background/10 rounded-lg p-4">
              <div className="text-xl font-mono text-background">{value}</div>
              <div className="text-xs text-background/40 mt-1">{label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="lg:hidden flex items-center gap-2 mb-8">
            <Leaf className="w-5 h-5" />
            <span className="font-semibold">营养健康监测平台</span>
          </div>

          <h2 className="text-2xl font-display mb-2">欢迎登录</h2>
          <p className="text-muted-foreground text-sm mb-8">
            使用钉钉账号进行身份验证
          </p>

          <button
            onClick={handleDevLogin}
            disabled={loading}
            className="w-full h-11 bg-foreground text-background text-sm font-medium rounded-lg flex items-center justify-center gap-2 hover:bg-foreground/90 transition-colors disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <DingTalkIcon />
                钉钉扫码登录
              </>
            )}
          </button>

          <div className="mt-4 text-center">
            <button
              onClick={() => setDevMode(!devMode)}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              开发调试入口
            </button>
          </div>

          {devMode && (
            <div className="mt-4 p-3 bg-secondary rounded-lg text-xs font-mono text-muted-foreground">
              <p>DingTalk authCode 将通过 URL 参数传入:</p>
              <p className="mt-1 text-foreground">?authCode=xxx</p>
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

function DingTalkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z" />
    </svg>
  )
}
