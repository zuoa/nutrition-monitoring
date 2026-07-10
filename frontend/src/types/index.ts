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

export interface Department {
  id: number
  dingtalk_dept_id: string
  name: string
  parent_dingtalk_dept_id?: string | null
  sort_order: number
  is_active: boolean
  sync_at?: string
  user_count?: number
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
  cholesterol?: number
  carbohydrate?: number
  added_sugar?: number
  sodium?: number
  fiber?: number
  calcium?: number
  iron?: number
  zinc?: number
  vitamin_a?: number
  vitamin_c?: number
  vitamin_d?: number
  is_active: boolean
  sample_image_count?: number
  sample_images?: DishSampleImage[]
  created_at?: string
  updated_at?: string
}

// ─── Menus ────────────────────────────────────────────────────────────────────
export type MealSlotKey = string
export interface MealSlot {
  key: string
  label: string
  start: string
  end: string
}
export type MealDishIds = Record<string, number[]>

export interface DailyMenu {
  id?: number
  menu_date: string
  meal_dish_ids: MealDishIds
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
  recognition_price_total?: number | null
  match_summary?: {
    is_matched: boolean
    match_count: number
    statuses: MatchStatus[]
    latest_status?: MatchStatus | null
    latest_match_id?: number | null
  }
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
  dish_price?: number | null
  confidence: number
  is_low_confidence: boolean
  is_manual: boolean
  position?: string
  bbox?: { x1: number; y1: number; x2: number; y2: number } | null
  notes?: string
  model_version?: string
}

export type RegionRecognitionStatus = 'recognized' | 'low_confidence' | 'unrecognized'
export type RegionReviewStatus = 'pending' | 'bound' | 'ignored'

export interface CapturedImageRegion {
  id: number
  image_id: number
  region_index: number
  bbox: { x1: number; y1: number; x2: number; y2: number }
  bbox_source: string
  detector_source?: string
  image_url?: string
  recognition_status: RegionRecognitionStatus
  suggested_dish_id?: number | null
  suggested_dish_name?: string | null
  suggested_confidence?: number | null
  review_status: RegionReviewStatus
  dish_sample_image_id?: number | null
  model_version?: string
  image?: CapturedImage
  suggested_dish?: Pick<Dish, 'id' | 'name' | 'category' | 'sample_image_count'>
  created_at?: string
  updated_at?: string
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
  channel_id?: string
  import_batch?: string
  created_at?: string
}

export interface MatchResult {
  id: number
  consumption_record_id?: number
  image_id?: number
  student_id?: number
  status: MatchStatus
  time_diff_seconds?: number
  price_diff?: number
  image_price_total?: number
  is_manual: boolean
  match_date?: string
  consumption_record?: ConsumptionRecord
  image?: CapturedImage
  student?: Student
}

// ─── Students ─────────────────────────────────────────────────────────────────
export interface StudentLatestReportSummary {
  report_id: number
  overall_score?: number
  alert_count: number
  period_start?: string
  period_end?: string
  summary?: string
  created_at?: string
}

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
  latest_report?: StudentLatestReportSummary | null
}

// ─── Reports ──────────────────────────────────────────────────────────────────
export type ReportType = 'personal_weekly' | 'personal_monthly' | 'class_weekly' | 'grade_monthly' | 'school_monthly'

export interface NutrientData {
  calories: number | null
  protein: number | null
  fat: number | null
  cholesterol: number | null
  carbohydrate: number | null
  added_sugar: number | null
  sodium: number | null
  fiber: number | null
  calcium: number | null
  iron: number | null
  zinc: number | null
  vitamin_a: number | null
  vitamin_c: number | null
  vitamin_d: number | null
}

export type NutrientSampleCounts = Partial<Record<keyof NutrientData, number>>

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
  nutrient_sample_counts?: NutrientSampleCounts
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
  nutrient_sample_counts?: NutrientSampleCounts
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
  status: 'pending' | 'running' | 'success' | 'failed' | 'partial'
  total_count: number
  success_count: number
  low_confidence_count: number
  error_count: number
  error_message?: string
  meta?: Record<string, any>
  started_at?: string
  finished_at?: string
}

// ─── Video Sources ───────────────────────────────────────────────────────────
export type VideoSourceType = 'nvr' | 'hikvision_camera'
export type VideoSourceStatus = 'enabled' | 'disabled'

export interface VideoMealWindow {
  start: string
  end: string
}

export interface VideoSourceCameraConfig {
  channel_id: string
  name: string
  selected?: boolean
  host: string
  port: number
  username?: string
  password?: string
  password_configured?: boolean
  supports_snapshot?: boolean
  location_alias?: string
}

export interface VideoSourceSummary {
  id: number | null
  name: string
  source_type: VideoSourceType
  is_active: boolean
  status: VideoSourceStatus
  last_validation_status: 'unknown' | 'success' | 'failed' | string
  last_validation_error?: string | null
  last_validated_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  persisted: boolean
}

export interface VideoSourceDetail extends VideoSourceSummary {
  config: {
    host?: string
    port?: number
    username?: string
    password?: string
    password_configured?: boolean
    device_name?: string
    device_model?: string
    device_serial_number?: string
    channel_ids?: string[]
    selected_channel_ids?: string[]
    download_trigger_time?: string
    local_storage_path?: string
    retention_days?: number
    cameras?: VideoSourceCameraConfig[]
    channels?: VideoSourceCameraConfig[]
  }
}

export interface RoiRegion {
  x: number
  y: number
  w: number
  h: number
}

export interface VideoSourceChannel {
  channel_id: string
  name: string
  host?: string
  port?: number
  supports_snapshot?: boolean
  roi_region?: RoiRegion | null
  location_alias?: string
}

export interface VideoSourceChannelsResponse {
  source: VideoSourceSummary
  supports_snapshot: boolean
  channels: VideoSourceChannel[]
}

export interface VideoChannelBindingSuggestionChannel {
  source_id: number
  source_name: string
  source_type: VideoSourceType | string
  channel_id: string
  channel_name: string
  location_alias?: string
  hit_count: number
  sample_count: number
  hit_rate: number
  price_match_count: number
  price_match_rate: number
  avg_time_diff_seconds: number
  avg_price_diff: number
  score: number
}

export interface VideoChannelBindingSuggestionEvidence {
  consumption_record_id: number
  transaction_id: string
  transaction_time: string
  amount: number
  image_id: number
  channel_id: string
  captured_at: string
  time_diff_seconds: number
  price_diff: number
  image_price_total: number
}

export type VideoChannelBindingSuggestionStatus = 'suggested' | 'conflict' | 'low_confidence' | 'sample_insufficient'

export interface VideoChannelBindingSuggestion {
  id: string
  location: string
  record_count: number
  matched_record_count: number
  status: VideoChannelBindingSuggestionStatus
  reason: string
  confidence: number
  can_apply: boolean
  recommended_channel?: VideoChannelBindingSuggestionChannel | null
  top_channels: VideoChannelBindingSuggestionChannel[]
  evidence: VideoChannelBindingSuggestionEvidence[]
}

export interface VideoChannelBindingSuggestionsResponse {
  days: number
  min_samples: number
  window_start: string
  window_end: string
  generated_at: string
  channel_count: number
  items: VideoChannelBindingSuggestion[]
}

export interface VideoChannelSnapshot {
  image_base64: string
  content_type: string
  captured_at: string
  channel_id: string
}

export interface HikvisionPluginPreviewConfig {
  host: string
  port: number
  rtsp_port?: number
  username: string
  password: string
  channel_id: string
  source_type: VideoSourceType
  protocol: number
  stream_type: number
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
  start_date?: string
  end_date?: string
  total_images: number
  pending: number
  identified: number
  matched: number
  error: number
  low_confidence_recognitions: number
  image_analysis_task_count?: number
  image_analysis_processed_images?: number
  image_analysis_duration_seconds?: number
  image_analysis_avg_seconds?: number | null
}
