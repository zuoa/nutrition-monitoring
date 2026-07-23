from app.models.user import User, RoleEnum
from app.models.department import Department
from app.models.student import Student, StudentSourceEnum, EnrollmentStatusEnum  # noqa: F401  (re-exported from module)
from app.modules.students.models.organization import (
    School,
    Campus,
    Stage,
    StageTypeEnum,
    Grade,
    Class,
)
from app.modules.students.models.guardian import Guardian
from app.models.dish import Dish, CategoryEnum
from app.models.dish_image import DishSampleImage, EmbeddingStatusEnum
from app.models.menu import DailyMenu
from app.models.image import CapturedImage, ImageStatusEnum
from app.models.region_candidate import CapturedImageRegion, RegionRecognitionStatusEnum, RegionReviewStatusEnum
from app.models.recognition import DishRecognition
from app.models.consumption import ConsumptionRecord, ConsumptionSyncState, TimeCalibrationSample
from app.models.match import MatchResult, MatchStatusEnum
from app.models.nutrition_log import NutritionLog
from app.models.report import Report, ReportPushLog, ReportTypeEnum
from app.models.task_log import TaskLog
from app.models.video_source import (
    VideoSource,
    VideoSourceStatus,
    VideoSourceType,
    VideoSourceValidationStatus,
)
from app.models.video_recording_job import VideoRecordingJob

__all__ = [
    "User", "RoleEnum",
    "Department",
    "Student", "StudentSourceEnum",
    "School", "Campus", "Stage", "StageTypeEnum", "Grade", "Class",
    "Guardian",
    "Dish", "CategoryEnum",
    "DishSampleImage", "EmbeddingStatusEnum",
    "DailyMenu",
    "CapturedImage", "ImageStatusEnum",
    "CapturedImageRegion", "RegionRecognitionStatusEnum", "RegionReviewStatusEnum",
    "DishRecognition",
    "ConsumptionRecord", "ConsumptionSyncState", "TimeCalibrationSample",
    "MatchResult", "MatchStatusEnum",
    "NutritionLog",
    "Report", "ReportPushLog", "ReportTypeEnum",
    "TaskLog",
    "VideoSource", "VideoSourceStatus", "VideoSourceType", "VideoSourceValidationStatus",
    "VideoRecordingJob",
]
