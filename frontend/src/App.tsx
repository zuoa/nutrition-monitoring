import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { AppLayout } from '@/components/layout/AppLayout'

const LoginPage = lazy(() => import('@/pages/LoginPage'))
const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const DishesPage = lazy(() => import('@/pages/DishesPage'))
const MenusPage = lazy(() => import('@/pages/MenusPage'))
const AnalysisPage = lazy(() => import('@/pages/AnalysisPage'))
const ConsumptionPage = lazy(() => import('@/pages/ConsumptionPage'))
const MatchesPage = lazy(() => import('@/pages/MatchesPage'))
const ReportsPage = lazy(() => import('@/pages/ReportsPage'))
const AdminPage = lazy(() => import('@/pages/AdminPage'))

function RouteFallback() {
  return (
    <div className="flex items-center justify-center h-screen text-muted-foreground text-sm font-mono">
      Loading...
    </div>
  )
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <div className="flex items-center justify-center h-screen text-muted-foreground text-sm font-mono">Loading...</div>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <Suspense fallback={<RouteFallback />}>
            <LoginPage />
          </Suspense>
        }
      />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="dishes" element={<DishesPage />} />
        <Route path="menus" element={<MenusPage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="consumption" element={<ConsumptionPage />} />
        <Route path="matches" element={<MatchesPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  )
}
