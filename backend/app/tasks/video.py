import json
import logging
import os
from datetime import datetime, date, timedelta
from celery_app import celery
from app import db
from app.models import CapturedImage, TaskLog, ImageStatusEnum

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.video.download_nvr_videos", bind=True, max_retries=2)
def download_nvr_videos(self, date_str: str = None):
    """Download NVR recordings and extract cashier frames for a given date."""
    from flask import current_app
    from app.services.video_analyzer import VideoAnalyzer

    cfg = current_app.config
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    task_log = TaskLog(task_type="nvr_download", task_date=target_date)
    db.session.add(task_log)
    db.session.commit()

    try:
        nvr = _make_video_source(cfg)
        analyzer = VideoAnalyzer(cfg)

        meal_windows = json.loads(cfg.get("NVR_MEAL_WINDOWS", "[]"))
        channel_ids = cfg.get("NVR_CHANNEL_IDS", ["1"])
        storage_path = cfg.get("NVR_LOCAL_STORAGE_PATH", "/data/nvr_cache")
        image_path = cfg.get("IMAGE_STORAGE_PATH", "/data/images")

        total_images = 0

        for channel_id in channel_ids:
            for window in meal_windows:
                start_dt = datetime.strptime(
                    f"{target_date} {window['start']}", "%Y-%m-%d %H:%M"
                )
                end_dt = datetime.strptime(
                    f"{target_date} {window['end']}", "%Y-%m-%d %H:%M"
                )

                recordings = nvr.list_recordings(channel_id, start_dt, end_dt)
                if not recordings:
                    logger.warning(f"No recordings for channel {channel_id} {start_dt}-{end_dt}")
                    continue

                for rec in recordings:
                    video_filename = rec.get("filename", f"{channel_id}_{int(start_dt.timestamp())}.mp4")
                    video_save_path = os.path.join(storage_path, str(target_date), video_filename)

                    # Download
                    resume_offset = os.path.getsize(video_save_path) if os.path.exists(video_save_path) else 0
                    ok = nvr.download_recording(
                        rec.get("download_url", ""), video_save_path, resume_offset
                    )
                    if not ok:
                        logger.error(f"Failed to download {video_filename}")
                        continue

                    # Extract frames
                    video_start = rec.get("start_time")
                    if isinstance(video_start, str):
                        video_start = datetime.fromisoformat(video_start)
                    else:
                        video_start = start_dt

                    output_dir = os.path.join(image_path, str(target_date), channel_id)
                    try:
                        frames = analyzer.extract_frames(
                            video_save_path, output_dir, video_start, channel_id
                        )
                    except Exception as e:
                        logger.error(f"Frame extraction failed for {video_filename}: {e}")
                        continue

                    for frame in frames:
                        img = CapturedImage(
                            capture_date=target_date,
                            channel_id=frame["channel_id"],
                            captured_at=frame["captured_at"],
                            image_path=frame["image_path"],
                            status=ImageStatusEnum.pending,
                            source_video=video_filename,
                            diff_score=frame.get("diff_score"),
                            is_candidate=frame.get("is_candidate", False),
                        )
                        db.session.add(img)
                        total_images += 1

                    db.session.commit()

        task_log.status = "success"
        task_log.total_count = total_images
        task_log.success_count = total_images
        task_log.finished_at = datetime.utcnow()
        db.session.commit()

        # Trigger recognition
        from app.tasks.recognition import run_recognition_batch
        run_recognition_batch.delay(str(target_date))

        logger.info(f"NVR download complete for {target_date}: {total_images} images")

    except Exception as e:
        logger.error(f"NVR download task failed: {e}", exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        db.session.commit()

        # Alert admin via DingTalk
        _send_admin_alert(f"NVR下载任务失败（{target_date}）: {str(e)[:200]}")
        raise self.retry(exc=e, countdown=300)


def _make_video_source(cfg):
    """Return NVRService or HikvisionCameraService based on VIDEO_SOURCE_MODE config."""
    mode = cfg.get("VIDEO_SOURCE_MODE", "nvr")
    if mode == "hikvision_camera":
        from app.services.hikvision_camera import HikvisionCameraService
        return HikvisionCameraService(cfg)
    from app.services.nvr import NVRService
    return NVRService(cfg)


def _send_admin_alert(message: str):
    try:
        from flask import current_app
        from app.services.dingtalk import DingTalkService
        from app.models import User, RoleEnum
        cfg = current_app.config
        dt = DingTalkService(cfg)
        admins = User.query.filter_by(role=RoleEnum.admin, is_active=True).all()
        for admin in admins:
            dt.send_work_notification(
                [admin.dingtalk_user_id],
                {"msgtype": "text", "text": {"content": f"[营养监测系统告警] {message}"}},
            )
    except Exception as e:
        logger.error(f"Failed to send admin alert: {e}")
