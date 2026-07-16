from celery import Celery
from celery.schedules import crontab
from config import get_config


def make_celery(app=None):
    cfg = get_config()
    visibility_timeout = max(3600, int(getattr(cfg, "CELERY_VISIBILITY_TIMEOUT", 86400)))
    celery = Celery(
        "nutrition_monitoring",
        broker=cfg.CELERY_BROKER_URL,
        backend=cfg.CELERY_RESULT_BACKEND,
        include=[
            "app.tasks.video",
            "app.tasks.recognition",
            "app.tasks.region_proposal",
            "app.tasks.matching",
            "app.tasks.nutrition",
            "app.tasks.reports",
            "app.tasks.sync",
            "app.tasks.embeddings",
            "app.tasks.dishes",
            "app.tasks.local_models",
            "app.tasks.menu_reminders",
            "app.tasks.system_notifications",
            "app.tasks.ztk_consumption",
            "app.modules.students.tasks",
        ],
    )

    beat_schedule = {
        "video-source-sync-dispatcher": {
            "task": "app.tasks.video.schedule_video_source_sync",
            "schedule": crontab(),
            "args": [],
        },
        "weekly-report": {
            "task": "app.tasks.reports.dispatch_scheduled_weekly_reports",
            "schedule": crontab(),
            "args": [],
        },
        "monthly-report": {
            "task": "app.tasks.reports.generate_all_reports",
            "schedule": crontab(hour=7, minute=30, day_of_month=1),
            "args": ["school_monthly"],
        },
        "dingtalk-org-sync": {
            "task": "app.tasks.sync.sync_dingtalk_org",
            "schedule": crontab(hour=2, minute=0),
            "args": [],
        },
        "student-sync": {
            "task": "app.modules.students.tasks.sync_students",
            "schedule": crontab(hour=2, minute=30),
            "args": [],
        },
        "check-nutrition-alerts": {
            "task": "app.tasks.nutrition.check_all_alerts",
            "schedule": crontab(hour=8, minute=0),
            "args": [],
        },
        "menu-sample-reminder-dispatcher": {
            "task": "app.tasks.menu_reminders.check_menu_sample_reminders",
            "schedule": crontab(),
            "args": [],
        },
        "system-runtime-notification-dispatcher": {
            "task": "app.tasks.system_notifications.dispatch_daily_system_runtime_notification",
            "schedule": crontab(),
            "args": [],
        },
        "recover-stale-recognition-images": {
            "task": "app.tasks.recognition.requeue_stale_recognition_images",
            "schedule": crontab(),
            "args": [],
            "options": {"queue": "maintenance"},
        },
        "dispatch-pending-recognition-images": {
            "task": "app.tasks.recognition.dispatch_pending_recognition_images",
            "schedule": crontab(),
            "args": [],
            "options": {"queue": "maintenance"},
        },
        "recover-stale-video-recording-jobs": {
            "task": "app.tasks.video.recover_stale_video_recording_jobs",
            "schedule": crontab(),
            "args": [],
            "options": {"queue": "maintenance"},
        },
    }
    # Register the ZTK sync beat unconditionally; the task body reads the
    # runtime enable flag and interval, then skips until the configured interval
    # has elapsed. This keeps page-saved changes effective without restarting
    # Celery Beat.
    beat_schedule["ztk-consumption-sync"] = {
        "task": "app.tasks.ztk_consumption.sync_ztk_consumption",
        "schedule": crontab(),
        "args": [],
    }

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        broker_transport_options={"visibility_timeout": visibility_timeout},
        result_backend_transport_options={"visibility_timeout": visibility_timeout},
        visibility_timeout=visibility_timeout,
        broker_connection_retry_on_startup=True,
        task_soft_time_limit=300,
        task_time_limit=600,
        task_routes={
            "app.tasks.video.sync_video_source_media": {"queue": "video"},
            "app.tasks.video.process_manual_video_upload": {"queue": "video-extract"},
            "app.tasks.video.download_video_recording_job": {"queue": "video-download"},
            "app.tasks.video.extract_video_recording_job": {"queue": "video-extract"},
            "app.tasks.video.recover_stale_video_recording_jobs": {"queue": "maintenance"},
            "app.tasks.recognition.*": {"queue": "recognition"},
            "app.tasks.region_proposal.*": {"queue": "recognition"},
            "app.tasks.embeddings.*": {"queue": "maintenance"},
            "app.tasks.local_models.*": {"queue": "maintenance"},
            "app.tasks.matching.*": {"queue": "matching"},
            "app.tasks.nutrition.*": {"queue": "matching"},
            "app.tasks.ztk_consumption.*": {"queue": "maintenance"},
            "app.tasks.reports.*": {"queue": "maintenance"},
            "app.tasks.sync.*": {"queue": "maintenance"},
            "app.modules.students.tasks.*": {"queue": "maintenance"},
            "app.tasks.menu_reminders.*": {"queue": "maintenance"},
            "app.tasks.system_notifications.*": {"queue": "maintenance"},
        },
        beat_schedule=beat_schedule,
    )

    if app:
        class ContextTask(celery.Task):
            def __call__(self, *args, **kwargs):
                with app.app_context():
                    return self.run(*args, **kwargs)
        celery.Task = ContextTask

    return celery


celery = make_celery()
