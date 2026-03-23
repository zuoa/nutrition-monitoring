import { Outlet, NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Utensils, CalendarDays, Video, FileUp,
  GitMerge, BarChart3, Settings, LogOut, Leaf, ChevronRight,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/dashboard', icon: LayoutDashboard, label: '概览', roles: ['admin', 'teacher', 'grade_leader', 'canteen_manager', 'parent'] },
  { to: '/dishes', icon: Utensils, label: '菜品管理', roles: ['admin', 'canteen_manager'] },
  { to: '/menus', icon: CalendarDays, label: '菜单管理', roles: ['admin', 'canteen_manager'] },
  { to: '/analysis', icon: Video, label: '视频分析', roles: ['admin'] },
  { to: '/consumption', icon: FileUp, label: '消费导入', roles: ['admin'] },
  { to: '/matches', icon: GitMerge, label: '匹配管理', roles: ['admin'] },
  { to: '/reports', icon: BarChart3, label: '营养报告', roles: ['admin', 'teacher', 'grade_leader', 'parent'] },
  { to: '/admin', icon: Settings, label: '系统管理', roles: ['admin'] },
]

export function AppLayout() {
  const { user, logout, hasRole } = useAuth()
  const location = useLocation()

  const visibleItems = NAV_ITEMS.filter(item => item.roles.some(r => hasRole(r)))

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 border-r border-border bg-card flex flex-col">
        {/* Logo */}
        <div className="h-14 flex items-center px-5 border-b border-border gap-2.5">
          <div className="w-7 h-7 rounded-md bg-foreground flex items-center justify-center">
            <Leaf className="w-3.5 h-3.5 text-background" />
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
                    ? 'bg-foreground text-background font-medium'
                    : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={cn('w-4 h-4 flex-shrink-0', isActive ? 'text-background' : '')} />
                  <span className="flex-1">{label}</span>
                  {isActive && <ChevronRight className="w-3 h-3 text-background/60" />}
                </>
              )}
            </NavLink>
          ))}
        </nav>

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

      {/* Main */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="h-14 border-b border-border bg-card px-6 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground font-mono">
            <span>营养健康监测平台</span>
            <ChevronRight className="w-3 h-3" />
            <span className="text-foreground">{getPageTitle(location.pathname)}</span>
          </div>
          <div className="text-xs font-mono text-muted-foreground">
            {new Date().toLocaleDateString('zh-CN')}
          </div>
        </header>

        {/* Page content */}
        <div className="flex-1 overflow-auto">
          <div className="page-enter">
            <Outlet />
          </div>
        </div>
      </main>
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
