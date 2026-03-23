import { createContext, useContext, useEffect, useState } from 'react'

export type ThemeId = 'green' | 'mono' | 'red'

export const THEMES: { id: ThemeId; label: string; color: string; bg: string }[] = [
  { id: 'green', label: '青绿', color: '#2FAF7F', bg: '#EAF8F2' },
  { id: 'mono', label: '墨灰', color: '#1a1a1a', bg: '#f5f5f5' },
  { id: 'red',  label: '赤红', color: '#D63031', bg: '#FEF0F0' },
]

const ThemeContext = createContext<{
  theme: ThemeId
  setTheme: (t: ThemeId) => void
}>({ theme: 'green', setTheme: () => {} })

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    return (localStorage.getItem('app-theme') as ThemeId) ?? 'green'
  })

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('app-theme', theme)
  }, [theme])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setThemeState }}>
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => useContext(ThemeContext)
