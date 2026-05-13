import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from '@/contexts/AuthContext'
import { AppLayout } from '@/components/layout/AppLayout'

const LoginPage = lazy(() => import('@/pages/LoginPage'))
const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const DishesPage = lazy(() => import('@/pages/DishesPage'))
const MenusPage = lazy(() => import('@/pages/MenusPage'))
const SampleCapturePage = lazy(() => import('@/pages/SampleCapturePage'))
const AnalysisPage = lazy(() => import('@/pages/AnalysisPage'))
const VideoChannelManagerPage = lazy(() => import('@/pages/VideoChannelManagerPage'))
const ConsumptionPage = lazy(() => import('@/pages/ConsumptionPage'))
const MatchesPage = lazy(() => import('@/pages/MatchesPage'))
const ReportsPage = lazy(() => import('@/pages/ReportsPage'))
const AdminPage = lazy(() => import('@/pages/AdminPage'))
const DemoPage = lazy(() => import('@/pages/DemoPage'))

function RouteFallback() {
  return (
    <div className="flex items-center justify-center h-screen text-muted-foreground text-sm font-mono">
      Loading...
    </div>
  )
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="flex items-center justify-center h-screen text-muted-foreground text-sm font-mono">Loading...</div>
  if (!user) {
    const redirect = encodeURIComponent(`${location.pathname}${location.search}${location.hash}`)
    return <Navigate to={`/login?redirect=${redirect}`} replace />
  }
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
        <Route path="sample-capture" element={<SampleCapturePage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="video-channels" element={<VideoChannelManagerPage />} />
        <Route path="consumption" element={<ConsumptionPage />} />
        <Route path="matches" element={<MatchesPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="demo" element={<DemoPage />} />
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
