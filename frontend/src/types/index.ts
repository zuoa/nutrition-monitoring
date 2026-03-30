// ─── Users & Auth ─────────────────────────────────────────────────────────────
export type Role = 'admin' | 'teacher' | 'grade_leader' | 'parent' | 'canteen_manager'

export interface User {
  id: number
  dingtalk_user_id: string
  name: string
  role: Role
  dept_id?: string
  dept_name?: string
  managed_class_ids?: string[]
  managed_grade_ids?: string[]
  student_ids?: number[]
  is_active: boolean
  sync_at?: string
}

// ─── Dishes ───────────────────────────────────────────────────────────────────
export type DishCategory = '主食' | '荤菜' | '素菜' | '汤' | '其他'
export type EmbeddingStatus = 'pending' | 'processing' | 'ready' | 'failed'

export interface DishSampleImage {
  id: number
  dish_id: number
  image_path?: string
  image_url?: string
  original_filename?: string
  sort_order: number
  is_cover: boolean
  is_active: boolean
  embedding_status: EmbeddingStatus
  embedding_model?: string
  embedding_version?: string
  embedding_updated_at?: string
  error_message?: string
  created_at?: string
  updated_at?: string
}

export interface Dish {
  id: number
  name: string
  description?: string
  ingredients?: string  // 配菜描述，用于营养成分分析
  image_url?: string
  price: number
  category: DishCategory
  weight?: number
  calories?: number
  protein?: number
  fat?: number
  carbohydrate?: number
  sodium?: number
  fiber?: number
  is_active: boolean
  sample_image_count?: number
  sample_images?: DishSampleImage[]
  created_at?: string
  updated_at?: string
}

// ─── Menus ────────────────────────────────────────────────────────────────────
export interface DailyMenu {
  id?: number
  menu_date: string
  dish_ids: number[]
  dishes?: Dish[]
  is_default: boolean
  updated_at?: string
}

// ─── Images & Recognition ─────────────────────────────────────────────────────
export type ImageStatus = 'pending' | 'identified' | 'matched' | 'error'

export interface CapturedImage {
  id: number
  capture_date: string
  channel_id: string
  captured_at: string
  image_path: string
  image_url?: string
  status: ImageStatus
  source_video?: string
  diff_score?: number
  is_candidate: boolean
  recognitions?: DishRecognition[]
}

export interface ImageRegionProposal {
  index: number
  bbox: { x1: number; y1: number; x2: number; y2: number }
  score: number
  label?: string
  source?: string
}

export interface DishRecognition {
  id: number
  image_id: number
  dish_id?: number
  dish_name_raw: string
  confidence: number
  is_low_confidence: boolean
  is_manual: boolean
  model_version?: string
}

// ─── Consumption & Matching ───────────────────────────────────────────────────
export type MatchStatus = 'matched' | 'time_matched_only' | 'unmatched_image' | 'unmatched_record' | 'confirmed'

export interface ConsumptionRecord {
  id: number
  student_id?: number
  student_no?: string
  student_name?: string
  transaction_time: string
  amount: number
  transaction_id: string
  import_batch?: string
}

export interface MatchResult {
  id: number
  consumption_record_id?: number
  image_id?: number
  student_id?: number
  status: MatchStatus
  time_diff_seconds?: number
  price_diff?: number
  is_manual: boolean
  match_date?: string
  consumption_record?: ConsumptionRecord
  student?: Student
}

// ─── Students ─────────────────────────────────────────────────────────────────
export interface Student {
  id: number
  student_no: string
  name: string
  class_id: string
  class_name?: string
  grade_id?: string
  grade_name?: string
  card_no?: string
  is_active: boolean
}

// ─── Reports ──────────────────────────────────────────────────────────────────
export type ReportType = 'personal_weekly' | 'personal_monthly' | 'class_weekly' | 'grade_monthly' | 'school_monthly'

export interface NutrientData {
  calories: number
  protein: number
  fat: number
  carbohydrate: number
  sodium: number
  fiber: number
}

export interface ReportAlert {
  type: 'deficiency' | 'excess' | 'no_meal' | 'diversity'
  nutrient?: string
  ratio?: number
  message: string
}

export interface PersonalReportContent {
  student_id: number
  student_name: string
  class_name?: string
  period_start: string
  period_end: string
  meal_days: number
  total_days: number
  avg_nutrients: NutrientData
  recommended_nutrients: NutrientData
  top_dishes: { name: string; count: number }[]
  alerts: ReportAlert[]
  overall_score: number
  suggestions: string[]
}

export interface ClassReportContent {
  class_id: string
  period_start: string
  period_end: string
  student_count: number
  avg_nutrients: NutrientData
  recommended_nutrients: NutrientData
  flagged_students: { name_masked: string; alerts: string[]; score: number }[]
  class_avg_score: number
}

export interface Report {
  id: number
  report_type: ReportType
  target_id: string
  period_start: string
  period_end: string
  summary?: string
  push_status: string
  pushed_at?: string
  created_at?: string
  content?: PersonalReportContent | ClassReportContent
}

// ─── Task Logs ────────────────────────────────────────────────────────────────
export interface TaskLog {
  id: number
  task_type: string
  task_date?: string
  status: 'running' | 'success' | 'failed' | 'partial'
  total_count: number
  success_count: number
  low_confidence_count: number
  error_count: number
  error_message?: string
  meta?: Record<string, any>
  started_at?: string
  finished_at?: string
}

// ─── API Response ─────────────────────────────────────────────────────────────
export interface ApiResponse<T> {
  code: number
  data: T
  message: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export interface DailySummary {
  date: string
  total_images: number
  pending: number
  identified: number
  matched: number
  error: number
  low_confidence_recognitions: number
}
