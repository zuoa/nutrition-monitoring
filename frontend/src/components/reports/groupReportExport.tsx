import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import html2canvas from 'html2canvas'
import type { GroupReportContent, Report } from '@/types'
import GroupReportCard from './GroupReportCard'

/** Render a separate, complete report so screen size and pagination cannot crop the PNG. */
export async function renderGroupReportPng(content: GroupReportContent, report: Report): Promise<Blob> {
  const container = document.createElement('div')
  container.setAttribute('aria-hidden', 'true')
  container.style.cssText = 'position:fixed;left:-10000px;top:0;width:960px;pointer-events:none;'
  document.body.appendChild(container)
  const root = createRoot(container)
  try {
    flushSync(() => root.render(<GroupReportCard content={content} report={report} exportMode />))
    await document.fonts.ready
    const canvas = await html2canvas(container, {
      backgroundColor: '#ffffff',
      scale: 2,
      windowWidth: 1280,
      windowHeight: 900,
      scrollX: 0,
      scrollY: 0,
      logging: false,
    })
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => {
      if (blob) resolve(blob)
      else reject(new Error('报告图片生成失败'))
    }, 'image/png'))
  } finally {
    root.unmount()
    container.remove()
  }
}
