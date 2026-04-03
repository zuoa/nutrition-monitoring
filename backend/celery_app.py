from celery import Celery
from celery.schedules import crontab
from config import get_config


def make_celery(app=None):
    cfg = get_config()
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
            "app.tasks.local_models",
        ],
    )

    celery.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="Asia/Shanghai",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=300,
        task_time_limit=600,
        beat_schedule={
            "video-source-sync-dispatcher": {
                "task": "app.tasks.video.schedule_video_source_sync",
                "schedule": crontab(),
                "args": [],
            },
            "weekly-report": {
                "task": "app.tasks.reports.generate_all_reports",
                "schedule": crontab(hour=7, minute=30, day_of_week=1),
                "args": ["personal_weekly"],
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
            "check-nutrition-alerts": {
                "task": "app.tasks.nutrition.check_all_alerts",
                "schedule": crontab(hour=8, minute=0),
                "args": [],
            },
        },
    )

    if app:
        class ContextTask(celery.Task):
            def __call__(self, *args, **kwargs):
                with app.app_context():
                    return self.run(*args, **kwargs)
        celery.Task = ContextTask

    return celery


celery = make_celery()
