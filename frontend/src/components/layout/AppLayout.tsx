import { Outlet, NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Utensils, CalendarDays, Video, FileUp,
  GitMerge, BarChart3, Settings, LogOut, Leaf, ChevronRight, Menu, X, Palette,
} from 'lucide-react'
import { Suspense, useState } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme, THEMES } from '@/contexts/ThemeContext'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/dashboard', icon: LayoutDashboard, label: '概览', shortLabel: '概览', roles: ['admin', 'teacher', 'grade_leader', 'canteen_manager', 'parent'] },
  { to: '/dishes', icon: Utensils, label: '菜品管理', shortLabel: '菜品', roles: ['admin', 'canteen_manager'] },
  { to: '/menus', icon: CalendarDays, label: '菜单管理', shortLabel: '菜单', roles: ['admin', 'canteen_manager'] },
  { to: '/analysis', icon: Video, label: '视频分析', shortLabel: '视频', roles: ['admin'] },
  { to: '/consumption', icon: FileUp, label: '消费导入', shortLabel: '消费', roles: ['admin'] },
  { to: '/matches', icon: GitMerge, label: '匹配管理', shortLabel: '匹配', roles: ['admin'] },
  { to: '/reports', icon: BarChart3, label: '营养报告', shortLabel: '报告', roles: ['admin', 'teacher', 'grade_leader', 'parent'] },
  { to: '/admin', icon: Settings, label: '系统管理', shortLabel: '设置', roles: ['admin'] },
]

export function AppLayout() {
  const { user, logout, hasRole } = useAuth()
  const { theme, setTheme } = useTheme()
  const location = useLocation()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [showThemePicker, setShowThemePicker] = useState(false)

  const visibleItems = NAV_ITEMS.filter(item => item.roles.some(r => hasRole(r)))

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar - Desktop */}
      <aside className="hidden lg:flex w-56 flex-shrink-0 border-r border-border bg-card flex-col">
        {/* Logo */}
        <div className="h-14 flex items-center px-5 border-b border-border gap-2.5">
          <div className="w-7 h-7 rounded-md bg-primary flex items-center justify-center">
            <Leaf className="w-3.5 h-3.5 text-primary-foreground" />
          </div>
          <div>
            <div className="text-sm font-semibold text-foreground leading-none">营养监测</div>
            <div className="text-[10px] text-muted-foreground mt-0.5">NutriTrack</div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-0.5">
          {visibleItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors duration-100 group',
                  isActive
                    ? 'bg-primary text-primary-foreground font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-4 h-4 flex-shrink-0', isActive ? 'text-primary-foreground' : '')} />
                  <span className="flex-1">{label}</span>
                  {isActive && <ChevronRight className="w-3 h-3 text-primary-foreground/60" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Theme Picker */}
        <div className="px-3 pb-2 relative">
          <button
            onClick={() => setShowThemePicker(v => !v)}
            className="flex items-center gap-2 w-full px-2 py-1.5 rounded-md text-xs text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
          >
            <Palette className="w-3.5 h-3.5" />
            <span>主题配色</span>
            <span className="ml-auto w-3 h-3 rounded-full border border-border" style={{ background: THEMES.find(t => t.id === theme)?.color }} />
          </button>
          {showThemePicker && (
            <div className="absolute bottom-full left-3 right-3 mb-1 bg-card border border-border rounded-lg p-2 shadow-lg space-y-1">
              {THEMES.map(t => (
                <button
                  key={t.id}
                  onClick={() => { setTheme(t.id); setShowThemePicker(false) }}
                  className={cn(
                    'flex items-center gap-2.5 w-full px-2.5 py-2 rounded-md text-xs transition-colors',
                    theme === t.id ? 'bg-primary/10 text-primary font-medium' : 'hover:bg-secondary text-foreground'
                  )}
                >
                  <span className="w-4 h-4 rounded-full flex-shrink-0 border border-border/50" style={{ background: t.color }} />
                  {t.label}
                  {theme === t.id && <span className="ml-auto text-primary">✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* User */}
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2.5 px-2 py-1.5 rounded-md hover:bg-secondary group cursor-pointer" onClick={logout}>
            <div className="w-7 h-7 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium text-foreground flex-shrink-0">
              {user?.name?.[0] ?? '?'}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-foreground truncate">{user?.name}</div>
              <div className="text-[10px] text-muted-foreground">{roleLabel(user?.role)}</div>
            </div>
            <LogOut className="w-3.5 h-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>
        </div>
      </aside>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <div className="lg:hidden fixed inset-0 z-50 bg-background">
          <div className="flex flex-col h-full">
            <div className="h-14 flex items-center justify-between px-4 border-b border-border">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 rounded-md bg-primary flex items-center justify-center">
                  <Leaf className="w-3.5 h-3.5 text-primary-foreground" />
                </div>
                <span className="text-sm font-semibold">营养监测</span>
              </div>
              <button onClick={() => setMobileMenuOpen(false)} className="p-2 hover:bg-secondary rounded-md">
                <X className="w-5 h-5" />
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto py-4 px-3 space-y-1">
              {visibleItems.map(({ to, icon: Icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  onClick={() => setMobileMenuOpen(false)}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-3 px-4 py-3 rounded-lg text-sm transition-colors',
                      isActive
                        ? 'bg-primary text-primary-foreground font-medium'
                        : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon className={cn('w-5 h-5 flex-shrink-0', isActive ? 'text-primary-foreground' : '')} />
                      <span>{label}</span>
                    </>
                  )}
                </NavLink>
              ))}
            </nav>
            <div className="border-t border-border p-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-full bg-foreground/10 flex items-center justify-center text-sm font-medium">
                    {user?.name?.[0] ?? '?'}
                  </div>
                  <div>
                    <div className="text-sm font-medium">{user?.name}</div>
                    <div className="text-xs text-muted-foreground">{roleLabel(user?.role)}</div>
                  </div>
                </div>
                <button onClick={logout} className="p-2 hover:bg-secondary rounded-md">
                  <LogOut className="w-5 h-5 text-muted-foreground" />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-14 border-b border-border bg-card px-4 lg:px-6 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2">
            {/* Mobile menu button */}
            <button
              onClick={() => setMobileMenuOpen(true)}
              className="lg:hidden p-2 -ml-2 hover:bg-secondary rounded-md"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono">
              <span className="hidden sm:inline">营养健康监测平台</span>
              <span className="sm:hidden">NutriTrack</span>
              <ChevronRight className="w-3 h-3" />
              <span className="text-foreground">{getPageTitle(location.pathname)}</span>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-xs font-mono text-muted-foreground hidden sm:block">
              {new Date().toLocaleDateString('zh-CN')}
            </div>
            {/* Mobile user avatar */}
            <div className="lg:hidden w-7 h-7 rounded-full bg-foreground/10 flex items-center justify-center text-xs font-medium">
              {user?.name?.[0] ?? '?'}
            </div>
          </div>
        </header>

        {/* Page content */}
        <div className="flex-1 overflow-auto">
          <div className="page-enter pb-20 lg:pb-0">
            <Suspense fallback={<PageFallback />}>
              <Outlet />
            </Suspense>
          </div>
        </div>
      </main>

      {/* Bottom Navigation - Mobile */}
      <nav className="lg:hidden fixed bottom-0 left-0 right-0 bg-card/95 backdrop-blur-md border-t border-border z-40 shadow-[0_-2px_12px_rgba(0,0,0,0.08)]">
        <div className="flex items-center justify-around">
          {visibleItems.slice(0, 5).map(({ to, icon: Icon, shortLabel }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex flex-col items-center gap-0.5 py-2 px-3 text-xs transition-colors',
                  isActive ? 'text-primary' : 'text-muted-foreground'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <div className={cn(
                    'p-1.5 rounded-lg transition-colors',
                    isActive ? 'bg-primary/15' : ''
                  )}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <span>{shortLabel}</span>
                </>
              )}
            </NavLink>
          ))}
        </div>
        {/* Safe area for iOS */}
        <div className="h-safe-area-inset-bottom bg-card/95" />
      </nav>
    </div>
  )
}

function PageFallback() {
  return (
    <div className="flex items-center justify-center min-h-[40vh] text-sm text-muted-foreground font-mono">
      Loading...
    </div>
  )
}

function roleLabel(role?: string): string {
  const map: Record<string, string> = {
    admin: '系统管理员', teacher: '班主任', grade_leader: '年级组长',
    parent: '家长', canteen_manager: '食堂管理员',
  }
  return map[role ?? ''] ?? role ?? ''
}

function getPageTitle(pathname: string): string {
  const map: Record<string, string> = {
    '/dashboard': '概览', '/dishes': '菜品管理', '/menus': '菜单管理',
    '/analysis': '视频分析', '/consumption': '消费导入', '/matches': '匹配管理',
    '/reports': '营养报告', '/admin': '系统管理',
  }
  return map[pathname] ?? pathname.replace('/', '')
}
