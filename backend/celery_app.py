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
            "app.tasks.dishes",
            "app.tasks.local_models",
            "app.tasks.menu_reminders",
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
        "dingtalk-school-sync": {
            "task": "app.modules.students.tasks.sync_dingtalk_school",
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
        worker_prefetch_multiplier=1,
        task_soft_time_limit=300,
        task_time_limit=600,
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
