import { useCallback, useEffect, useRef } from 'react'
import type { PersonalReportContent, ReportFormat } from '@/types'

/** The isolated document is also the server's PNG template. Scripts are disabled. */
export default function ReportPreview({ content, format = 'detailed' }: { content: PersonalReportContent; format?: ReportFormat }) {
  const observer = useRef<ResizeObserver | null>(null)
  const onLoad = useCallback((event: React.SyntheticEvent<HTMLIFrameElement>) => {
    const frame = event.currentTarget
    const body = frame.contentDocument?.body
    observer.current?.disconnect()
    if (!body) return
    const resize = () => { frame.style.height = `${Math.ceil(body.getBoundingClientRect().height) + 2}px` }
    observer.current = new ResizeObserver(resize)
    observer.current.observe(body)
    resize()
  }, [])
  useEffect(() => () => observer.current?.disconnect(), [])

  const html = format === 'brief' ? content.brief_presentation_html : content.presentation_html
  if (!html) {
    return <p className="rounded-xl border border-border bg-card p-8 text-sm text-muted-foreground">报告预览暂不可用，请刷新后重试。</p>
  }
  return <iframe
    title={`${content.student_name}的营养报告`}
    srcDoc={html}
    sandbox="allow-same-origin"
    onLoad={onLoad}
    className="block w-full rounded-lg border-0 bg-white"
    style={{ height: 1200 }}
  />
}
