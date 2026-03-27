import axios, { AxiosError } from 'axios'
import toast from 'react-hot-toast'

const BASE = '/api'

export const client = axios.create({
  baseURL: BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// Attach token
client.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Handle errors globally
client.interceptors.response.use(
  (res) => res,
  (err: AxiosError<{ message?: string }>) => {
    const msg = err.response?.data?.message || err.message || '请求失败'
    if (err.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_user')
      window.location.href = '/login'
    } else if (err.response?.status !== 404) {
      toast.error(msg)
    }
    return Promise.reject(err)
  },
)

// ─── Auth ─────────────────────────────────────────────────────────────────────
export const authApi = {
  getCaptcha: () => client.get<any>('/auth/captcha'),
  login: (data: { username: string; password: string; captcha_id: string; captcha_code: string }) =>
    client.post<any>('/auth/login', data),
  loginDingTalk: (authCode: string) =>
    client.post<any>('/auth/dingtalk-login', { authCode }),
  me: () => client.get<any>('/auth/me'),
  refresh: () => client.post<any>('/auth/refresh'),
}

// ─── Dishes ───────────────────────────────────────────────────────────────────
export const dishApi = {
  list: (params?: Record<string, any>) =>
    client.get<any>('/v1/dishes/', { params }),
  get: (id: number) => client.get<any>(`/v1/dishes/${id}`),
  create: (data: Record<string, any>) => client.post<any>('/v1/dishes/', data),
  update: (id: number, data: Record<string, any>) =>
    client.put<any>(`/v1/dishes/${id}`, data),
  delete: (id: number) => client.delete<any>(`/v1/dishes/${id}`),
  categories: () => client.get<any>('/v1/dishes/categories'),
  analyzePreview: (dish_name: string, weight: number, ingredients?: string) =>
    client.post<any>('/v1/dishes/analyze-nutrition-preview', { dish_name, weight, ingredients }),
  analyze: (id: number, weight: number) =>
    client.post<any>(`/v1/dishes/${id}/analyze-nutrition`, { weight }),
}

// ─── Menus ────────────────────────────────────────────────────────────────────
export const menuApi = {
  get: (date: string) => client.get<any>(`/v1/menus/${date}`),
  upsert: (date: string, data: { dish_ids: number[] }) =>
    client.put<any>(`/v1/menus/${date}`, data),
  list: (start: string, end: string) =>
    client.get<any>('/v1/menus/', { params: { start, end } }),
}

// ─── Analysis ─────────────────────────────────────────────────────────────────
export const analysisApi = {
  tasks: (params?: Record<string, any>) =>
    client.get<any>('/v1/analysis/tasks', { params }),
  retryTask: (id: number) =>
    client.post<any>(`/v1/analysis/tasks/${id}/retry`),
  triggerAnalysis: (date?: string) =>
    client.post<any>('/v1/analysis/tasks/trigger', { date }),
  uploadVideo: (file: File, videoStartTime: string, channelId?: string) => {
    const fd = new FormData()
    fd.append('video_file', file)
    fd.append('video_start_time', videoStartTime)
    if (channelId) fd.append('channel_id', channelId)
    return client.post<any>('/v1/analysis/upload-video', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000, // 5 minutes for large video files
    })
  },
  images: (params?: Record<string, any>) =>
    client.get<any>('/v1/analysis/images', { params }),
  getImage: (id: number) => client.get<any>(`/v1/analysis/images/${id}`),
  recognizeImage: (id: number) =>
    client.post<any>(`/v1/analysis/images/${id}/recognize`),
  describeImage: (id: number) =>
    client.post<any>(`/v1/analysis/images/${id}/describe`),
  reviewImage: (id: number, dish_ids: number[]) =>
    client.put<any>(`/v1/analysis/images/${id}/review`, { dish_ids }),
  summary: (date?: string) =>
    client.get<any>('/v1/analysis/summary', { params: { date } }),
}

// ─── Consumption ──────────────────────────────────────────────────────────────
export const consumptionApi = {
  preview: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<any>('/v1/consumption/preview', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  import: (file: File, fieldMapping?: Record<string, string>) => {
    const fd = new FormData()
    fd.append('file', file)
    if (fieldMapping) fd.append('field_mapping', JSON.stringify(fieldMapping))
    return client.post<any>('/v1/consumption/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  records: (params?: Record<string, any>) =>
    client.get<any>('/v1/consumption/records', { params }),
  matches: (params?: Record<string, any>) =>
    client.get<any>('/v1/consumption/matches', { params }),
  confirmMatch: (id: number, image_id?: number) =>
    client.put<any>(`/v1/consumption/matches/${id}/confirm`, { image_id }),
  rematch: (date?: string) =>
    client.post<any>('/v1/consumption/matches/rematch', { date }),
}

// ─── Reports ──────────────────────────────────────────────────────────────────
export const reportApi = {
  studentLatest: (studentId: number) =>
    client.get<any>(`/v1/reports/student/${studentId}/latest`),
  studentReports: (studentId: number, params?: Record<string, any>) =>
    client.get<any>(`/v1/reports/student/${studentId}`, { params }),
  classReports: (classId: string, params?: Record<string, any>) =>
    client.get<any>(`/v1/reports/class/${classId}`, { params }),
  get: (id: number) => client.get<any>(`/v1/reports/${id}`),
  push: (id: number) => client.post<any>(`/v1/reports/${id}/push`),
  generate: (type: string, period_start?: string, period_end?: string) =>
    client.post<any>('/v1/reports/generate', { type, period_start, period_end }),
  alerts: () => client.get<any>('/v1/reports/alerts'),
}

// ─── Admin ────────────────────────────────────────────────────────────────────
export const adminApi = {
  users: (params?: Record<string, any>) =>
    client.get<any>('/v1/admin/users', { params }),
  updateUser: (id: number, data: Record<string, any>) =>
    client.put<any>(`/v1/admin/users/${id}`, data),
  students: (params?: Record<string, any>) =>
    client.get<any>('/v1/admin/students', { params }),
  config: () => client.get<any>('/v1/admin/config'),
}

// ─── Sync ─────────────────────────────────────────────────────────────────────
export const syncApi = {
  status: () => client.get<any>('/v1/sync/dingtalk/status'),
  trigger: () => client.post<any>('/v1/sync/dingtalk/trigger'),
  importStudents: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return client.post<any>('/v1/sync/students/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}
