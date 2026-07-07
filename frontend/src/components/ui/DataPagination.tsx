import { FormEvent, useEffect, useId, useState } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { cn } from '@/lib/utils'

interface DataPaginationProps {
  page: number
  totalPages: number
  totalItems?: number
  disabled?: boolean
  className?: string
  ariaLabel?: string
  onPageChange: (page: number) => void
}

const clampPage = (value: number, totalPages: number) =>
  Math.min(Math.max(1, value), Math.max(1, totalPages))

export function DataPagination({
  page,
  totalPages,
  totalItems,
  disabled = false,
  className,
  ariaLabel = '表格分页',
  onPageChange,
}: DataPaginationProps) {
  const safeTotalPages = Math.max(1, totalPages)
  const safePage = clampPage(page, safeTotalPages)
  const pageInputId = useId()
  const [jumpValue, setJumpValue] = useState(String(safePage))

  useEffect(() => {
    setJumpValue(String(safePage))
  }, [safePage])

  const goToPage = (nextPage: number) => {
    const targetPage = clampPage(nextPage, safeTotalPages)
    setJumpValue(String(targetPage))
    if (targetPage !== page) {
      onPageChange(targetPage)
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const parsed = Number.parseInt(jumpValue, 10)
    if (Number.isNaN(parsed)) {
      setJumpValue(String(safePage))
      return
    }
    goToPage(parsed)
  }

  const isFirstPage = safePage <= 1
  const isLastPage = safePage >= safeTotalPages
  const buttonClass = 'inline-flex h-9 w-9 items-center justify-center rounded-md border border-border bg-background text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40'

  return (
    <nav
      aria-label={ariaLabel}
      className={cn(
        'flex flex-col gap-3 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between',
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {typeof totalItems === 'number' && (
          <span>共 {totalItems} 条</span>
        )}
        <span className="font-mono">
          第 {safePage} / {safeTotalPages} 页
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => goToPage(1)}
            disabled={disabled || isFirstPage}
            className={buttonClass}
            aria-label="首页"
            title="首页"
          >
            <ChevronsLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => goToPage(safePage - 1)}
            disabled={disabled || isFirstPage}
            className={buttonClass}
            aria-label="上一页"
            title="上一页"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => goToPage(safePage + 1)}
            disabled={disabled || isLastPage}
            className={buttonClass}
            aria-label="下一页"
            title="下一页"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => goToPage(safeTotalPages)}
            disabled={disabled || isLastPage}
            className={buttonClass}
            aria-label="尾页"
            title="尾页"
          >
            <ChevronsRight className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex items-center gap-1.5">
          <label htmlFor={pageInputId}>跳至</label>
          <input
            id={pageInputId}
            value={jumpValue}
            onChange={(event) => setJumpValue(event.target.value.replace(/[^\d]/g, ''))}
            disabled={disabled}
            inputMode="numeric"
            pattern="[0-9]*"
            className="h-9 w-16 rounded-md border border-border bg-background px-2 text-center font-mono text-xs text-foreground outline-none transition-colors focus:border-foreground/30 focus:ring-1 focus:ring-foreground/20 disabled:opacity-50"
            aria-label="输入页码"
          />
          <span>页</span>
          <button
            type="submit"
            disabled={disabled}
            className="h-9 rounded-md border border-border bg-background px-3 text-xs text-foreground transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-40"
          >
            跳转
          </button>
        </form>
      </div>
    </nav>
  )
}
