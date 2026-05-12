import type { Dispatch, SetStateAction } from 'react'
import { Bot, Braces, FileJson, ImageUp, SendHorizontal, X } from 'lucide-react'
import type { DropzoneInputProps, DropzoneRootProps } from 'react-dropzone'

import {
  DebugMetricCard,
  EmptyDebugState,
  formatDebugJson,
  type ImportedMenuInfo,
  type VlDebugBox,
  type VlTestResult,
} from '@/components/admin/adminPageShared'
import { cn } from '@/lib/utils'

export default function VlDebugTab({
  config,
  imageFile,
  imagePreviewUrl,
  debugBoxes,
  systemPrompt,
  setSystemPrompt,
  userPrompt,
  setUserPrompt,
  temperature,
  setTemperature,
  importedMenuInfo,
  promptSupportsDishList,
  defaultsLoading,
  loading,
  result,
  getRootProps,
  getInputProps,
  isDragActive,
  onClearImage,
  onSubmit,
  onLoadDefaults,
  onApplyBboxDefaults,
  onImportTodayMenu,
}: {
  config: Record<string, any>
  imageFile: File | null
  imagePreviewUrl: string
  debugBoxes: VlDebugBox[]
  systemPrompt: string
  setSystemPrompt: Dispatch<SetStateAction<string>>
  userPrompt: string
  setUserPrompt: Dispatch<SetStateAction<string>>
  temperature: string
  setTemperature: Dispatch<SetStateAction<string>>
  importedMenuInfo: ImportedMenuInfo | null
  promptSupportsDishList: boolean
  defaultsLoading: boolean
  loading: boolean
  result: VlTestResult | null
  getRootProps: <T extends DropzoneRootProps>(props?: T) => T
  getInputProps: <T extends DropzoneInputProps>(props?: T) => T
  isDragActive: boolean
  onClearImage: () => void
  onSubmit: () => void | Promise<void>
  onLoadDefaults: () => void | Promise<void>
  onApplyBboxDefaults: (temperature?: string) => void
  onImportTodayMenu: () => void | Promise<void>
}) {
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,460px)_minmax(0,1fr)]">
      <div className="space-y-4">
        <div className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border bg-[linear-gradient(135deg,rgba(16,185,129,0.08),rgba(15,23,42,0.02))] px-5 py-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 rounded-xl border border-border bg-background p-2.5">
                <Bot className="h-4 w-4 text-primary" />
              </div>
              <div>
                <h2 className="text-sm font-medium">视觉模型调试工作台</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  上传单张图片，自定义系统提示词和用户提示词，直接查看 VL 模型原始返回。
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                    model: {String(config.qwen_model || '未配置')}
                  </span>
                  <span className="rounded-full border border-border bg-background px-2.5 py-1 font-mono text-muted-foreground">
                    mode: remote-vl
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4 p-5">
            <div
              {...getRootProps()}
              className={cn(
                'group rounded-2xl border border-dashed p-4 transition-colors',
                isDragActive ? 'border-primary bg-primary/5' : 'border-border bg-secondary/30 hover:border-primary/30 hover:bg-secondary/60',
              )}
            >
              <input {...getInputProps()} />
              {imagePreviewUrl ? (
                <div className="space-y-3">
                  <div className="overflow-hidden rounded-xl border border-border bg-background">
                    <div className="flex justify-center bg-secondary/20 p-2">
                      <div className="relative inline-block">
                        <img src={imagePreviewUrl} alt="VL test preview" className="block max-h-[280px] max-w-full" />
                        {debugBoxes.length > 0 && (
                          <div className="pointer-events-none absolute inset-0">
                            {debugBoxes.map((item, index) => (
                              <div
                                key={`vl-debug-box-${index}-${item.name}-${item.bbox.x1}-${item.bbox.y1}`}
                                className="absolute rounded-lg border-2 border-emerald-500/90 bg-emerald-500/10"
                                style={{
                                  left: `${item.bbox.x1}%`,
                                  top: `${item.bbox.y1}%`,
                                  width: `${item.bbox.x2 - item.bbox.x1}%`,
                                  height: `${item.bbox.y2 - item.bbox.y1}%`,
                                }}
                              >
                                <div className="absolute left-0 top-0 -translate-y-full rounded-md bg-emerald-600 px-2 py-1 text-[10px] leading-none text-white shadow-sm">
                                  {item.name}
                                  {item.confidence !== undefined ? ` ${(item.confidence * 100).toFixed(0)}%` : ''}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation()
                            onClearImage()
                          }}
                          className="absolute right-2 top-2 inline-flex h-8 w-8 items-center justify-center rounded-full border border-border bg-background/90 text-muted-foreground transition-colors hover:text-foreground"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{imageFile?.name}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {imageFile ? `${(imageFile.size / 1024 / 1024).toFixed(2)} MB` : ''}
                      </div>
                      {debugBoxes.length > 0 && (
                        <div className="mt-1 text-[11px] text-emerald-700">
                          已解析 {debugBoxes.length} 个 bbox，可在图片上直接查看框选区域。
                        </div>
                      )}
                    </div>
                    <div className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-mono text-muted-foreground">
                      单图测试
                    </div>
                  </div>
                </div>
              ) : (
                <div className="flex min-h-[220px] flex-col items-center justify-center text-center">
                  <div className="mb-4 rounded-2xl border border-border bg-background p-4">
                    <ImageUp className="h-7 w-7 text-primary" />
                  </div>
                  <div className="text-sm font-medium">拖拽图片到这里，或点击选择文件</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    支持 JPG、PNG、WEBP、BMP。建议使用原图，便于复现线上响应。
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-3">
              <div>
                <div className="mb-1.5 text-xs font-medium text-foreground">系统提示词</div>
                <textarea
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                  rows={4}
                  placeholder="可选。为空时不附带 system message。"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                />
              </div>
              <div>
                <div className="mb-1.5 text-xs font-medium text-foreground">Temperature</div>
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.1"
                  value={temperature}
                  onChange={(event) => setTemperature(event.target.value)}
                  className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm font-mono outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                />
                <div className="mt-1 text-[11px] text-muted-foreground">调试范围 0 到 1，值越高随机性越强。</div>
              </div>
              <div>
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <div className="text-xs font-medium text-foreground">用户提示词</div>
                  <button
                    type="button"
                    onClick={onImportTodayMenu}
                    disabled={defaultsLoading || !promptSupportsDishList}
                    className="rounded-lg border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                  >
                    {defaultsLoading ? '导入中...' : '导入今日菜单'}
                  </button>
                </div>
                <textarea
                  value={userPrompt}
                  onChange={(event) => setUserPrompt(event.target.value)}
                  rows={8}
                  placeholder="输入要发给 VL 模型的用户提示词"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary/40"
                />
                {importedMenuInfo && (
                  <div className="mt-1 text-[11px] text-muted-foreground">
                    已导入 {importedMenuInfo.date} 菜单，候选菜品 {importedMenuInfo.count} 道。
                    {importedMenuInfo.isDefault ? ' 当前日期未单独配置菜单，正式视频分析会停止并生成告警。' : ''}
                  </div>
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={onSubmit}
                disabled={loading}
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                <SendHorizontal className={cn('h-4 w-4', loading && 'animate-pulse')} />
                {loading ? '请求模型中...' : '发送测试请求'}
              </button>
              <button
                type="button"
                onClick={onLoadDefaults}
                disabled={defaultsLoading}
                className="rounded-xl border border-border bg-background px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                {defaultsLoading ? '加载中...' : '识别预设'}
              </button>
              <button
                type="button"
                onClick={() => onApplyBboxDefaults(temperature)}
                className="rounded-xl border border-border bg-background px-4 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
              >
                BBox 预设
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <DebugMetricCard
            icon={<Bot className="h-4 w-4" />}
            label="模型"
            value={result?.model || String(config.qwen_model || '—')}
          />
          <DebugMetricCard
            icon={<SendHorizontal className="h-4 w-4" />}
            label="Temperature"
            value={String(result?.temperature ?? (temperature || '—'))}
          />
          <DebugMetricCard
            icon={<Braces className="h-4 w-4" />}
            label="请求格式"
            value={result?.request_format || '—'}
          />
          <DebugMetricCard
            icon={<ImageUp className="h-4 w-4" />}
            label="文件"
            value={result?.filename || imageFile?.name || '未选择'}
          />
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <FileJson className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">解析文本</h3>
          </div>
          {result ? (
            <pre className="max-h-[260px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-secondary/40 p-4 text-sm leading-6 text-foreground">
              {result.content || '模型未返回可提取文本'}
            </pre>
          ) : (
            <EmptyDebugState text="发起测试后，这里会显示从原始响应中提取出的文本内容。" />
          )}
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <Braces className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">解析后的 JSON</h3>
          </div>
          {result?.parsed_json ? (
            <pre className="max-h-[320px] overflow-auto rounded-xl bg-secondary/40 p-4 text-xs leading-6 text-foreground">
              {formatDebugJson(result.parsed_json)}
            </pre>
          ) : (
            <EmptyDebugState text={result?.json_parse_error || '未识别到可解析的 JSON 结果。'} />
          )}
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <ImageUp className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">BBox 结果</h3>
          </div>
          {debugBoxes.length > 0 ? (
            <div className="space-y-2">
              {debugBoxes.map((item, index) => (
                <div key={`vl-debug-box-row-${index}-${item.name}`} className="rounded-xl bg-secondary/40 px-3 py-2.5 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-700">
                      {item.name}
                    </span>
                    {item.position && (
                      <span className="text-muted-foreground">位置 {item.position}</span>
                    )}
                    {item.confidence !== undefined && (
                      <span className="text-muted-foreground">置信度 {(item.confidence * 100).toFixed(0)}%</span>
                    )}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                    ({item.bbox.x1}, {item.bbox.y1}) - ({item.bbox.x2}, {item.bbox.y2})
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyDebugState text="当前解析结果里没有可视化的 bbox。可让提示词返回 dishes[].bbox 后再测试。" />
          )}
        </div>

        <div className="rounded-2xl border border-border bg-card p-5">
          <div className="mb-3 flex items-center gap-2">
            <FileJson className="h-4 w-4 text-muted-foreground" />
            <h3 className="text-sm font-medium">原始响应</h3>
          </div>
          {result ? (
            <pre className="max-h-[520px] overflow-auto rounded-xl bg-[linear-gradient(180deg,rgba(15,23,42,0.96),rgba(15,23,42,0.88))] p-4 text-xs leading-6 text-slate-100">
              {formatDebugJson(result.raw_response)}
            </pre>
          ) : (
            <EmptyDebugState text="还没有请求记录。上传图片并发送测试请求后，这里会展示服务端返回的完整 JSON。" />
          )}
        </div>
      </div>
    </div>
  )
}
