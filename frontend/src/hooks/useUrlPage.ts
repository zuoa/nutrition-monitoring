import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

type PageUpdater = number | ((currentPage: number) => number)

const parsePage = (value: string | null) => {
  const parsed = Number.parseInt(value || '', 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1
}

export function useUrlPage(paramName = 'page') {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = parsePage(searchParams.get(paramName))

  const setPage = useCallback((updater: PageUpdater) => {
    const requestedPage = typeof updater === 'function' ? updater(page) : updater
    const nextPage = Math.max(1, Math.trunc(Number(requestedPage) || 1))
    const currentRawPage = searchParams.get(paramName)
    const urlAlreadyMatches = nextPage === 1 ? currentRawPage === null : currentRawPage === String(nextPage)
    if (nextPage === page && urlAlreadyMatches) return

    setSearchParams((currentParams) => {
      const nextParams = new URLSearchParams(currentParams)
      if (nextPage === 1) {
        nextParams.delete(paramName)
      } else {
        nextParams.set(paramName, String(nextPage))
      }
      return nextParams
    })
  }, [page, paramName, searchParams, setSearchParams])

  return [page, setPage] as const
}
