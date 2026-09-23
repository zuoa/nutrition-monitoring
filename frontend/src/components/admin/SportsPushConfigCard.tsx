import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import { sportsApi } from '@/api/client'

export default function SportsPushConfigCard() {
  const [schoolIds, setSchoolIds] = useState('')
  const [secret, setSecret] = useState('')
  const [configured, setConfigured] = useState(false)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setLoading(true)
    setFailed(false)
    sportsApi.config().then(({ data }) => {
      if (!active) return
      setSchoolIds(data.data.school_ids.join('\n'))
      setConfigured(data.data.check_string_configured)
    }).catch(() => { if (active) setFailed(true) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [retry])

  const clearSecret = async () => {
    if (!window.confirm('清除签名密钥后将恢复环境变量配置；若环境变量未配置密钥，推送接口将停用（503）。确定清除？')) return
    setSaving(true)
    try {
      await sportsApi.saveConfig({ check_string: '' })
      setConfigured(false)
      setSecret('')
      toast.success('已清除签名密钥')
    } catch { /* API interceptor displays the error. */ } finally { setSaving(false) }
  }

  return <section className="rounded-xl border border-border bg-card p-5 space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="text-sm font-semibold">体育推送 API</h3><p className="text-xs text-muted-foreground mt-1">配置第三方体育设备的学校编号和签名密钥，保存后立即生效。</p></div>
      <Link to="/sports" className="text-sm text-primary underline underline-offset-4">查看体育数据</Link>
    </div>
    {failed ? <p role="alert" className="text-sm text-destructive">配置加载失败。<button className="underline ml-2" onClick={() => setRetry(v => v + 1)}>重新加载</button></p> : null}
    <form onSubmit={async event => {
      event.preventDefault()
      setSaving(true)
      try {
        await sportsApi.saveConfig({
          school_ids: schoolIds.split('\n').map(item => item.trim()).filter(Boolean),
          ...(secret.trim() ? { check_string: secret.trim() } : {}),
        })
        if (secret.trim()) setConfigured(true)
        setSecret('')
        toast.success('体育推送配置已保存')
      } catch { /* API interceptor displays the error. */ } finally { setSaving(false) }
    }}>
      <fieldset disabled={loading || failed || saving} className="space-y-4 disabled:opacity-60">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm">学校编号（school_id）
            <textarea rows={2} className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2" value={schoolIds} onChange={e => setSchoolIds(e.target.value)} placeholder={'每行一个编号，例如\nschool-1'} />
            <span className="block mt-1 text-xs text-muted-foreground">每行一个学校编号；留空允许所有签名有效的学校。</span>
          </label>
          <label className="text-sm">签名密钥（check_string）
            <input type="password" autoComplete="new-password" maxLength={1024} required={!configured} className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2" value={secret} onChange={e => setSecret(e.target.value)} placeholder={configured ? '已配置，留空保持原密钥' : '输入与推送方约定的密钥'} />
            <span className="block mt-1 text-xs text-muted-foreground">{configured ? '密钥已配置，不显示已保存的密钥；保存时首尾空格自动去除。' : '尚未配置密钥，暂不能接收推送。'}</span>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground">{saving ? '保存中…' : loading ? '加载中…' : '保存体育推送配置'}</button>
          {configured ? <button type="button" onClick={clearSecret} className="rounded-lg border border-destructive/40 px-4 py-2 text-sm text-destructive hover:bg-destructive/10">清除签名密钥</button> : null}
        </div>
        <p className="text-xs text-muted-foreground break-all">成绩接口：/api/v1/external/sports/results<br />附件接口：/api/v1/external/sports/files</p>
      </fieldset>
    </form>
  </section>
}
