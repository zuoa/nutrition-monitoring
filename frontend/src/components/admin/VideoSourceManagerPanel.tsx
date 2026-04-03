import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'

import { adminApi } from '@/api/client'
import { fmtDateTime } from '@/lib/utils'
import type {
  VideoSourceDetail,
  VideoSourceSummary,
  VideoSourceType,
} from '@/types'

type Props = {
  activeSummary?: VideoSourceSummary | null
  onRefreshConfig?: () => Promise<void> | void
}

type NVRForm = {
  host: string
  port: string
  username: string
  password: string
  passwordConfigured: boolean
  channelIdsText: string
  downloadTriggerTime: string
  localStoragePath: string
  retentionDays: string
}

type HikvisionChannelForm = {
  channel_id: string
  name: string
  selected: boolean
}

type HikvisionForm = {
  host: string
  port: string
  username: string
  password: string
  passwordConfigured: boolean
  deviceName: string
  deviceModel: string
  deviceSerialNumber: string
  channels: HikvisionChannelForm[]
}

type FormState = {
  id: number | null
  name: string
  sourceType: VideoSourceType
  status: 'enabled' | 'disabled'
  nvr: NVRForm
  hikvision: HikvisionForm
}

const emptyHikvision = (): HikvisionForm => ({
  host: '',
  port: '80',
  username: 'admin',
  password: '',
  passwordConfigured: false,
  deviceName: '',
  deviceModel: '',
  deviceSerialNumber: '',
  channels: [],
})

const emptyForm = (): FormState => ({
  id: null,
  name: '',
  sourceType: 'nvr',
  status: 'enabled',
  nvr: {
    host: '',
    port: '8080',
    username: 'admin',
    password: '',
    passwordConfigured: false,
    channelIdsText: '1',
    downloadTriggerTime: '21:30',
    localStoragePath: '/data/nvr_cache',
    retentionDays: '3',
  },
  hikvision: emptyHikvision(),
})

function detailToForm(detail: VideoSourceDetail): FormState {
  const form = emptyForm()
  form.id = detail.id
  form.name = detail.name
  form.sourceType = detail.source_type
  form.status = detail.status

  if (detail.source_type === 'nvr') {
    form.nvr = {
      host: String(detail.config.host || ''),
      port: String(detail.config.port ?? 8080),
      username: String(detail.config.username || 'admin'),
      password: '',
      passwordConfigured: Boolean(detail.config.password_configured),
      channelIdsText: Array.isArray(detail.config.channel_ids) ? detail.config.channel_ids.join(',') : '1',
      downloadTriggerTime: String(detail.config.download_trigger_time || '21:30'),
      localStoragePath: String(detail.config.local_storage_path || '/data/nvr_cache'),
      retentionDays: String(detail.config.retention_days ?? 3),
    }
    return form
  }

  const channels = Array.isArray(detail.config.channels) && detail.config.channels.length > 0
    ? detail.config.channels
    : (detail.config.cameras || []).map((camera) => ({
        channel_id: String(camera.channel_id || ''),
        name: String(camera.name || ''),
        selected: true,
      }))
  form.hikvision = {
    host: String(detail.config.host || detail.config.cameras?.[0]?.host || ''),
    port: String(detail.config.port ?? detail.config.cameras?.[0]?.port ?? 80),
    username: String(detail.config.username || 'admin'),
    password: '',
    passwordConfigured: Boolean(detail.config.password_configured),
    deviceName: String(detail.config.device_name || ''),
    deviceModel: String(detail.config.device_model || ''),
    deviceSerialNumber: String(detail.config.device_serial_number || ''),
    channels: channels.map((channel) => ({
      channel_id: String(channel.channel_id || ''),
      name: String(channel.name || ''),
      selected: Boolean(channel.selected ?? true),
    })),
  }
  return form
}

function buildPayload(form: FormState) {
  if (form.sourceType === 'nvr') {
    return {
      name: form.name.trim(),
      source_type: form.sourceType,
      status: form.status,
      config: {
        host: form.nvr.host.trim(),
        port: Number(form.nvr.port || 8080),
        username: form.nvr.username.trim(),
        password: form.nvr.password,
        channel_ids: form.nvr.channelIdsText,
        download_trigger_time: form.nvr.downloadTriggerTime.trim(),
        local_storage_path: form.nvr.localStoragePath.trim(),
        retention_days: Number(form.nvr.retentionDays || 3),
      },
    }
  }

  return {
    name: form.name.trim(),
    source_type: form.sourceType,
    status: form.status,
    config: {
      host: form.hikvision.host.trim(),
      port: Number(form.hikvision.port || 80),
      username: form.hikvision.username.trim(),
      password: form.hikvision.password,
      selected_channel_ids: form.hikvision.channels
        .filter((channel) => channel.selected && channel.channel_id.trim())
        .map((channel) => channel.channel_id.trim()),
    },
  }
}

export default function VideoSourceManagerPanel({ activeSummary, onRefreshConfig }: Props) {
  const [sources, setSources] = useState<VideoSourceSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [validatingId, setValidatingId] = useState<number | null>(null)
  const [activatingId, setActivatingId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<FormState>(emptyForm())

  const loadSources = async () => {
    setLoading(true)
    try {
      const res = await adminApi.listVideoSources()
      setSources(res.data.data.items || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSources()
  }, [])

  const refreshAll = async () => {
    await loadSources()
    await Promise.resolve(onRefreshConfig?.())
  }

  const resetForm = () => {
    setEditingId(null)
    setForm(emptyForm())
  }

  const loadDetail = async (id: number) => {
    const res = await adminApi.getVideoSource(id)
    const detail = res.data.data as VideoSourceDetail
    setEditingId(id)
    setForm(detailToForm(detail))
  }

  const submit = async () => {
    const payload = buildPayload(form)

    setSaving(true)
    try {
      if (editingId) {
        await adminApi.updateVideoSource(editingId, payload)
        toast.success('视频源已更新')
      } else {
        await adminApi.createVideoSource(payload)
        toast.success('视频源已创建')
      }
      resetForm()
      await refreshAll()
    } finally {
      setSaving(false)
    }
  }

  const activate = async (id: number) => {
    setActivatingId(id)
    try {
      await adminApi.activateVideoSource(id)
      toast.success('视频源已激活')
      await refreshAll()
    } finally {
      setActivatingId(null)
    }
  }

  const validate = async (id: number) => {
    setValidatingId(id)
    try {
      const res = await adminApi.validateVideoSource(id)
      toast.success(res.data.data.message || '视频源校验完成')
      await refreshAll()
    } finally {
      setValidatingId(null)
    }
  }

  const remove = async (id: number) => {
    if (!window.confirm('确定删除这个视频源吗？')) return
    await adminApi.deleteVideoSource(id)
    toast.success('视频源已删除')
    if (editingId === id) resetForm()
    await refreshAll()
  }

  const updateNVR = (patch: Partial<NVRForm>) => {
    setForm((prev) => ({ ...prev, nvr: { ...prev.nvr, ...patch } }))
  }

  const updateHikvision = (patch: Partial<HikvisionForm>) => {
    setForm((prev) => ({ ...prev, hikvision: { ...prev.hikvision, ...patch } }))
  }

  const toggleHikvisionChannel = (channelId: string, selected: boolean) => {
    setForm((prev) => ({
      ...prev,
      hikvision: {
        ...prev.hikvision,
        channels: prev.hikvision.channels.map((channel) => (
          channel.channel_id === channelId ? { ...channel, selected } : channel
        )),
      },
    }))
  }

  const discoverHikvision = async () => {
    setDiscovering(true)
    try {
      const res = await adminApi.discoverHikvisionVideoSource({
        video_source_id: editingId ?? undefined,
        config: {
          host: form.hikvision.host.trim(),
          port: Number(form.hikvision.port || 80),
          username: form.hikvision.username.trim(),
          password: form.hikvision.password,
          selected_channel_ids: form.hikvision.channels
            .filter((channel) => channel.selected && channel.channel_id.trim())
            .map((channel) => channel.channel_id.trim()),
        },
      })
      const payload = res.data.data || {}
      const channels = Array.isArray(payload.channels) ? payload.channels : []
      setForm((prev) => ({
        ...prev,
        hikvision: {
          ...prev.hikvision,
          host: String(payload.config?.host || prev.hikvision.host),
          port: String(payload.config?.port ?? prev.hikvision.port),
          username: String(payload.username || prev.hikvision.username || 'admin'),
          passwordConfigured: Boolean(payload.password_configured),
          deviceName: String(payload.device?.device_name || ''),
          deviceModel: String(payload.device?.model || ''),
          deviceSerialNumber: String(payload.device?.serial_number || ''),
          channels: channels.map((channel: HikvisionChannelForm) => ({
            channel_id: String(channel.channel_id || ''),
            name: String(channel.name || ''),
            selected: Boolean(channel.selected),
          })),
        },
      }))
      toast.success(`已探测到 ${channels.length || 0} 个可用通道`)
    } catch (error: any) {
      toast.error(error?.response?.data?.message || '海康设备探测失败')
    } finally {
      setDiscovering(false)
    }
  }

  const onSourceTypeChange = (nextType: VideoSourceType) => {
    setForm((prev) => ({
      ...prev,
      sourceType: nextType,
    }))
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h2 className="text-sm font-medium">视频源管理</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              当前激活：
              {' '}
              {activeSummary
                ? `${activeSummary.name} · ${activeSummary.source_type}`
                : '未配置'}
            </p>
          </div>
          <button
            onClick={() => void loadSources()}
            className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground transition hover:bg-secondary"
          >
            {loading ? '刷新中...' : '刷新视频源'}
          </button>
        </div>

        <div className="mt-4 grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(380px,420px)]">
          <div className="space-y-3">
            {sources.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border bg-secondary/30 px-4 py-6 text-sm text-muted-foreground">
                暂无视频源。请先创建并激活一个视频源，系统才会执行同步和抓拍。
              </div>
            ) : (
              sources.map((source) => (
                <div key={source.id ?? source.name} className="rounded-lg border border-border bg-secondary/40 p-4">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="text-sm font-medium">{source.name}</div>
                        {source.is_active && (
                          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-700">
                            当前激活
                          </span>
                        )}
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                          {source.source_type}
                        </span>
                        <span className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
                          {source.status}
                        </span>
                      </div>
                      <div className="mt-2 text-[11px] text-muted-foreground">
                        校验状态：
                        {' '}
                        {source.last_validation_status}
                        {source.last_validated_at ? ` · ${fmtDateTime(source.last_validated_at)}` : ''}
                      </div>
                      {source.last_validation_error && (
                        <div className="mt-1 text-[11px] text-health-red break-words">{source.last_validation_error}</div>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {source.id !== null && (
                        <>
                          <button
                            onClick={() => void loadDetail(source.id as number)}
                            className="rounded-lg border border-border px-3 py-1.5 text-xs transition hover:bg-background"
                          >
                            编辑
                          </button>
                          <button
                            onClick={() => void validate(source.id as number)}
                            disabled={validatingId === source.id}
                            className="rounded-lg border border-border px-3 py-1.5 text-xs transition hover:bg-background disabled:opacity-50"
                          >
                            {validatingId === source.id ? '校验中...' : '校验'}
                          </button>
                          <button
                            onClick={() => void activate(source.id as number)}
                            disabled={source.is_active || activatingId === source.id || source.status !== 'enabled'}
                            className="rounded-lg bg-primary px-3 py-1.5 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                          >
                            {activatingId === source.id ? '切换中...' : '激活'}
                          </button>
                          <button
                            onClick={() => void remove(source.id as number)}
                            disabled={source.is_active}
                            className="rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-700 transition hover:bg-rose-50 disabled:opacity-50"
                          >
                            删除
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="rounded-lg border border-border bg-background p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium">{editingId ? '编辑视频源' : '新建视频源'}</div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  密码留空表示保持原值不变。海康直连改为设备级录入，通道通过探测生成。
                </div>
              </div>
              {editingId && (
                <button
                  onClick={resetForm}
                  className="rounded-lg border border-border px-3 py-1.5 text-xs transition hover:bg-secondary"
                >
                  新建
                </button>
              )}
            </div>

            <div className="mt-4 space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1">
                  <div className="text-xs text-muted-foreground">名称</div>
                  <input
                    value={form.name}
                    onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="例如 食堂主 NVR"
                  />
                </label>
                <label className="space-y-1">
                  <div className="text-xs text-muted-foreground">状态</div>
                  <select
                    value={form.status}
                    onChange={(event) => setForm((prev) => ({ ...prev, status: event.target.value as 'enabled' | 'disabled' }))}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  >
                    <option value="enabled">enabled</option>
                    <option value="disabled">disabled</option>
                  </select>
                </label>
              </div>

              <label className="space-y-1">
                <div className="text-xs text-muted-foreground">类型</div>
                <select
                  value={form.sourceType}
                  onChange={(event) => onSourceTypeChange(event.target.value as VideoSourceType)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                >
                  <option value="nvr">nvr</option>
                  <option value="hikvision_camera">hikvision_camera</option>
                </select>
              </label>

              {form.sourceType === 'nvr' ? (
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Host</div>
                      <input value={form.nvr.host} onChange={(event) => updateNVR({ host: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Port</div>
                      <input value={form.nvr.port} onChange={(event) => updateNVR({ port: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Username</div>
                      <input value={form.nvr.username} onChange={(event) => updateNVR({ username: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">
                        Password
                        {form.nvr.passwordConfigured ? '（已配置）' : ''}
                      </div>
                      <input type="password" value={form.nvr.password} onChange={(event) => updateNVR({ password: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1 sm:col-span-2">
                      <div className="text-xs text-muted-foreground">Channel IDs</div>
                      <input value={form.nvr.channelIdsText} onChange={(event) => updateNVR({ channelIdsText: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" placeholder="1,2" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Trigger Time</div>
                      <input value={form.nvr.downloadTriggerTime} onChange={(event) => updateNVR({ downloadTriggerTime: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" placeholder="21:30" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Retention Days</div>
                      <input value={form.nvr.retentionDays} onChange={(event) => updateNVR({ retentionDays: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1 sm:col-span-2">
                      <div className="text-xs text-muted-foreground">Local Storage Path</div>
                      <input value={form.nvr.localStoragePath} onChange={(event) => updateNVR({ localStoragePath: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <div className="sm:col-span-2 rounded-lg border border-border bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
                      同步查询时间段已改为系统配置统一管理，请到“系统配置”页调整早餐 / 午餐 / 晚餐时段。
                    </div>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Host</div>
                      <input value={form.hikvision.host} onChange={(event) => updateHikvision({ host: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" placeholder="192.168.1.88" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Port</div>
                      <input value={form.hikvision.port} onChange={(event) => updateHikvision({ port: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" placeholder="80" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">Username</div>
                      <input value={form.hikvision.username} onChange={(event) => updateHikvision({ username: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                    <label className="space-y-1">
                      <div className="text-xs text-muted-foreground">
                        Password
                        {form.hikvision.passwordConfigured ? '（已配置）' : ''}
                      </div>
                      <input type="password" value={form.hikvision.password} onChange={(event) => updateHikvision({ password: event.target.value })} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm" />
                    </label>
                  </div>

                  <div className="rounded-lg border border-border bg-secondary/30 p-3">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div>
                        <div className="text-sm font-medium">设备探测</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          仅录入设备连接信息。保存时后端会自动探测并生成通道配置；如果设备有多个可用通道，可先探测后勾选。
                        </div>
                      </div>
                      <button
                        onClick={() => void discoverHikvision()}
                        disabled={discovering}
                        className="rounded-lg border border-border px-3 py-1.5 text-xs transition hover:bg-background disabled:opacity-50"
                      >
                        {discovering ? '探测中...' : '探测设备'}
                      </button>
                    </div>

                    {(form.hikvision.deviceName || form.hikvision.deviceModel || form.hikvision.deviceSerialNumber) && (
                      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                        <div>设备名称：{form.hikvision.deviceName || '—'}</div>
                        <div>型号：{form.hikvision.deviceModel || '—'}</div>
                        <div>序列号：{form.hikvision.deviceSerialNumber || '—'}</div>
                      </div>
                    )}

                    <div className="mt-3 space-y-2">
                      <div className="text-xs text-muted-foreground">可用通道</div>
                      {form.hikvision.channels.length > 0 ? (
                        form.hikvision.channels.map((channel) => (
                          <label key={channel.channel_id} className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2 text-sm">
                            <div>
                              <div className="font-medium">{channel.name || `通道 ${channel.channel_id}`}</div>
                              <div className="text-xs text-muted-foreground">设备内部通道号：{channel.channel_id}</div>
                            </div>
                            <input
                              type="checkbox"
                              checked={channel.selected}
                              onChange={(event) => toggleHikvisionChannel(channel.channel_id, event.target.checked)}
                              className="h-4 w-4 rounded border-border"
                            />
                          </label>
                        ))
                      ) : (
                        <div className="rounded-lg border border-dashed border-border bg-background px-3 py-3 text-xs text-muted-foreground">
                          尚未探测。单通道 IPC 可以直接保存，后端会默认使用主通道；多通道设备建议先探测后再选择。
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={submit}
                  disabled={saving}
                  className="rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground transition hover:bg-primary/90 disabled:opacity-50"
                >
                  {saving ? '保存中...' : editingId ? '保存修改' : '创建视频源'}
                </button>
                <button
                  onClick={resetForm}
                  className="rounded-lg border border-border px-4 py-2 text-sm transition hover:bg-secondary"
                >
                  重置
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
