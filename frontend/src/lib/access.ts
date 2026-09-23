import type { Role } from '@/types'

export const PAGE_ROLES: Record<string, Role[]> = {
  '/dashboard': ['admin', 'teacher', 'grade_leader', 'parent'],
  '/students': ['admin', 'teacher', 'grade_leader'],
  '/reports': ['admin', 'teacher', 'grade_leader', 'parent'],
  '/menus': ['admin', 'canteen_manager'],
  '/dishes': ['admin', 'canteen_manager'],
  '/sample-capture': ['admin'],
  '/users': ['admin'],
  '/sports': ['admin'],
  '/admin': ['admin'],
  '/analysis': ['admin'],
  '/video-channels': ['admin'],
  '/consumption': ['admin'],
  '/matches': ['admin'],
  '/demo': ['admin'],
}

export const homeForRole = (role?: Role) => role === 'canteen_manager' ? '/menus' : '/dashboard'
export const canAccessPage = (role: Role, pathname: string) => PAGE_ROLES[pathname.replace(/\/$/, '')]?.includes(role) ?? false
