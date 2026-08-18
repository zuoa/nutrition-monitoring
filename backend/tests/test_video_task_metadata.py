import os
import sys
import tempfile
import threading
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from flask import Flask


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

if "flask_migrate" not in sys.modules:
    flask_migrate = types.ModuleType("flask_migrate")

    class _Migrate:
        def init_app(self, *args, **kwargs):
            return None

    flask_migrate.Migrate = _Migrate
    sys.modules["flask_migrate"] = flask_migrate

if "pythonjsonlogger" not in sys.modules:
    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger = types.ModuleType("jsonlogger")

    class _JsonFormatter:
        def __init__(self, *args, **kwargs):
            pass

    jsonlogger.JsonFormatter = _JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger
    sys.modules["pythonjsonlogger"] = pythonjsonlogger

if "redis" not in sys.modules:
    redis = types.ModuleType("redis")
    redis.from_url = lambda *args, **kwargs: object()
    sys.modules["redis"] = redis

if "celery" not in sys.modules:
    celery_module = types.ModuleType("celery")
    schedules_module = types.ModuleType("celery.schedules")

    class _FakeTaskWrapper:
        def __init__(self, fn):
            self.run = fn
            self.delay = lambda *args, **kwargs: None

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

    class _FakeCelery:
        def __init__(self, *args, **kwargs):
            self.conf = {}

        def task(self, *args, **kwargs):
            def decorator(fn):
                return _FakeTaskWrapper(fn)
            return decorator

        def __getattr__(self, name):
            if name == "conf":
                return self.conf
            if name == "Task":
                return object
            raise AttributeError(name)

    def _fake_crontab(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}

    celery_module.Celery = _FakeCelery
    schedules_module.crontab = _fake_crontab
    sys.modules["celery"] = celery_module
    sys.modules["celery.schedules"] = schedules_module

from app import db  # noqa: E402
import app.models  # noqa: F401,E402
from app.models import CapturedImage, CategoryEnum, DailyMenu, Dish, TaskLog, VideoRecordingJob, VideoSource  # noqa: E402
from app.services.video_sources.manager import VideoSourceManager  # noqa: E402
from app.tasks.video import (  # noqa: E402
    _build_recording_filename,
    _claim_recording_job_execution,
    _cleanup_expired_video_recordings,
    _dispatch_available_video_recording_jobs,
    _prepare_recording_job_dispatch,
    _publish_prepared_recording_job,
    _refresh_distributed_sync_task,
    _trim_downloaded_recording_to_window,
    download_video_recording_job,
    extract_video_recording_job,
    mark_sync_task_failed,
    process_manual_video_upload,
    recover_stale_video_recording_jobs,
    retry_failed_video_recording_jobs,
    retry_video_source_sync_task,
    sync_video_source_media,
)


class _FakeVideoSource:
    def list_recordings(self, channel_id, start, end):
        return [{
            "filename": f"{channel_id}_{int(start.timestamp())}.mp4",
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "download_url": "http://example.com/video.mp4",
            "size": 128,
        }]

    def download_recording(self, download_url, save_path, resume_offset=0):
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, "wb") as handle:
            handle.write(b"fake-video")
        return True


class _OrderingVideoSource(_FakeVideoSource):
    def __init__(self, events, before_download=None):
        self.events = events
        self.before_download = before_download
        self.download_count = 0

    def download_recording(self, download_url, save_path, resume_offset=0):
        self.download_count += 1
        if self.before_download is not None:
            self.before_download(self.download_count)
        self.events.append(("download", os.path.basename(save_path)))
        return super().download_recording(download_url, save_path, resume_offset)


class VideoTaskMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SECRET_KEY="test-secret",
            JWT_ALGORITHM="HS256",
            JWT_ACCESS_TOKEN_EXPIRES=timedelta(hours=1),
            IMAGE_STORAGE_PATH="/tmp/nutrition-monitoring-test-images",
            VIDEO_EXTRACT_USE_SUBPROCESS=False,
            MEAL_SLOTS=[
                {"key": "breakfast", "label": "早餐", "start": "07:00", "end": "09:00"},
                {"key": "lunch", "label": "午餐", "start": "11:30", "end": "13:00"},
                {"key": "dinner", "label": "晚餐", "start": "17:30", "end": "19:00"},
            ],
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        db.drop_all()
        cls.app_context.pop()

    def setUp(self):
        self.app.config["VIDEO_RECORDING_JOB_STALE_SECONDS"] = 7200
        self.app.config["VIDEO_RECORDING_JOB_MAX_RECOVERIES"] = 5
        self.app.config["VIDEO_DOWNLOAD_MAX_IN_FLIGHT"] = 4
        self.app.config["VIDEO_EXTRACT_MAX_IN_FLIGHT"] = 4
        self.app.config["VIDEO_RECORDING_DISPATCH_LEASE_SECONDS"] = 300
        self.app.config["VIDEO_RECORDING_QUEUED_LEASE_SECONDS"] = 21600
        self.app.config["VIDEO_RECORDING_HEARTBEAT_LEASE_SECONDS"] = 1800
        self.app.config["VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS"] = 20
        db.session.query(CapturedImage).delete()
        db.session.query(VideoRecordingJob).delete()
        db.session.query(TaskLog).delete()
        db.session.query(DailyMenu).delete()
        db.session.query(Dish).delete()
        db.session.query(VideoSource).delete()
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.expunge_all()

    def test_build_recording_filename_uses_formatted_local_time(self):
        filename = _build_recording_filename(
            "nvr",
            "8",
            datetime(2026, 4, 3, 3, 30, tzinfo=ZoneInfo("UTC")),
            {"VIDEO_TIMEZONE": "Asia/Shanghai"},
        )

        self.assertEqual(filename, "nvr_ch8_2026-04-03_11-30-00.mp4")

    def test_trim_recording_transcodes_for_frame_accurate_start(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, "recording.mp4")
            with open(video_path, "wb") as handle:
                handle.write(b"source-video")

            commands = []

            def fake_run(command, cfg):
                commands.append(command)
                with open(command[-1], "wb") as handle:
                    handle.write(b"accurately-trimmed-video")

            with mock.patch("app.tasks.video._run_ffmpeg_command", side_effect=fake_run):
                result = _trim_downloaded_recording_to_window(
                    {"VIDEO_TIMEZONE": "Asia/Shanghai", "FFMPEG_BIN": "ffmpeg"},
                    video_path,
                    datetime(2026, 8, 18, 11, 29, 47, tzinfo=ZoneInfo("Asia/Shanghai")),
                    datetime(2026, 8, 18, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
                    datetime(2026, 8, 18, 11, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                    datetime(2026, 8, 18, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                )

            command = commands[0]
            self.assertTrue(result["trimmed"])
            self.assertEqual(result["strategy"], "accurate_transcode")
            self.assertEqual(result["offset_seconds"], 13.0)
            self.assertEqual(result["duration_seconds"], 3600.0)
            self.assertEqual(command[command.index("-ss") + 1], "13.000")
            self.assertEqual(command[command.index("-t") + 1], "3600.000")
            self.assertEqual(command[command.index("-c:v") + 1], "libx264")
            self.assertNotIn("copy", command)
            with open(video_path, "rb") as handle:
                self.assertEqual(handle.read(), b"accurately-trimmed-video")

    def test_manual_hikvision_ps_upload_uses_ffmpeg_recovery_pipeline(self):
        task = TaskLog(
            task_type="manual_upload",
            task_date=date(2026, 7, 15),
            status="pending",
            meta={"source_video": "8_1752552000.ps"},
        )
        db.session.add(task)
        db.session.commit()

        with mock.patch("app.tasks.video._extract_frames_for_recording", return_value=[]) as extract_mock:
            result = process_manual_video_upload.run(
                types.SimpleNamespace(),
                task.id,
                "/tmp/8_1752552000.ps",
                "/tmp/nutrition-monitoring-test-images/2026-07-15/8",
                "2026-07-15T12:00:00",
                "8",
                "8_1752552000.ps",
            )

        analysis_cfg = extract_mock.call_args.args[0]
        self.assertEqual(analysis_cfg["VIDEO_EXTRACT_DECODE_BACKEND"], "ffmpeg_cpu")
        self.assertEqual(extract_mock.call_args.args[1], "/tmp/8_1752552000.ps")
        self.assertEqual(result["frames_extracted"], 0)
        db.session.refresh(task)
        self.assertEqual(task.status, "success")

    def _create_recording_job(
        self,
        *,
        filename: str,
        task_date: date = date(2026, 7, 15),
        task_id: str = "download-current",
        last_progress_at: datetime | None = None,
        recovery_count: int = 0,
        status: str = "pending",
        stage: str = "queued_download",
        published_at: datetime | None = None,
        next_dispatch_at: datetime | None = None,
        lease_expires_at: datetime | None = None,
    ) -> VideoRecordingJob:
        task = TaskLog(
            task_type="video_source_sync",
            task_date=task_date,
            status="running",
        )
        db.session.add(task)
        db.session.flush()
        job = VideoRecordingJob(
            task_log_id=task.id,
            channel_id="1",
            filename=filename,
            video_path=f"/tmp/{filename}",
            output_dir=f"/tmp/{filename}-frames",
            download_url="http://example.com/video.mp4",
            status=status,
            stage=stage,
            download_task_id=task_id,
            last_progress_at=last_progress_at,
            dispatch_attempt_count=0,
            recovery_count=recovery_count,
            published_at=published_at,
            next_dispatch_at=next_dispatch_at,
            lease_expires_at=lease_expires_at,
            details={"recovery_count": recovery_count},
        )
        db.session.add(job)
        db.session.commit()
        return job

    def test_recording_job_execution_token_allows_only_one_claim(self):
        job = self._create_recording_job(filename="single-claim.mp4")

        first = _claim_recording_job_execution(job.id, "download", "download-current")
        second = _claim_recording_job_execution(job.id, "download", "download-current")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(current.download_attempt_count, 1)
        self.assertGreater(current.lease_expires_at.replace(tzinfo=timezone.utc), datetime.now(timezone.utc))

    def test_duplicate_parent_delivery_cannot_reclaim_running_sync(self):
        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 7, 15),
            status="running",
        )
        db.session.add(task)
        db.session.commit()

        result = sync_video_source_media.run(
            types.SimpleNamespace(request=types.SimpleNamespace(retries=0)),
            "2026-07-15",
            task.id,
        )

        self.assertEqual(result["reason"], "reserved_task_already_claimed")
        self.assertEqual(result["task_id"], task.id)
        self.assertEqual(task.status, "running")

    def test_parent_retry_uses_nonduplicating_arguments_and_resets_inline_state(self):
        class RetryScheduled(Exception):
            pass

        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 7, 15),
            status="pending",
            meta={
                "recordings": [{"filename": "old.mp4", "download_status": "success"}],
                "empty_windows": [{"channel_id": "1"}],
                "image_ids": [99],
                "primary_count": 1,
                "candidate_count": 2,
            },
        )
        db.session.add(task)
        db.session.commit()
        retry_kwargs = {}

        def retry(**kwargs):
            retry_kwargs.update(kwargs)
            return RetryScheduled()

        request_task = types.SimpleNamespace(
            request=types.SimpleNamespace(retries=0),
            max_retries=2,
            retry=retry,
        )
        with (
            mock.patch.object(
                VideoSourceManager,
                "get_active_runtime_source",
                side_effect=RuntimeError("source unavailable"),
            ),
            self.assertRaises(RetryScheduled),
        ):
            sync_video_source_media.run(request_task, "2026-07-15", task.id)

        self.assertEqual(retry_kwargs["args"], ("2026-07-15", task.id))
        self.assertEqual(retry_kwargs["kwargs"], {})
        db.session.refresh(task)
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.meta["recordings"], [])
        self.assertEqual(task.meta["empty_windows"], [])
        self.assertEqual(task.meta["image_ids"], [])
        self.assertEqual(task.meta["primary_count"], 0)
        self.assertEqual(task.meta["candidate_count"], 0)

    def test_parent_retry_resumes_existing_distributed_children(self):
        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 7, 15),
            status="pending",
        )
        db.session.add(task)
        db.session.flush()
        child = VideoRecordingJob(
            task_log_id=task.id,
            channel_id="1",
            filename="already-persisted.mp4",
            video_path="/tmp/already-persisted.mp4",
            output_dir="/tmp/already-persisted-frames",
            download_url="http://example.com/video.mp4",
            status="pending",
            stage="awaiting_download",
        )
        db.session.add(child)
        db.session.commit()

        with (
            mock.patch(
                "app.tasks.video._dispatch_available_video_recording_jobs",
                return_value={"queued": [child.id], "published": [child.id], "publish_failed": []},
            ) as dispatch_mock,
            mock.patch("app.tasks.video._refresh_distributed_sync_task") as refresh_mock,
            mock.patch("app.tasks.video._make_video_source") as source_mock,
        ):
            result = sync_video_source_media.run(
                types.SimpleNamespace(request=types.SimpleNamespace(retries=1)),
                "2026-07-15",
                task.id,
            )

        self.assertTrue(result["resumed"])
        self.assertEqual(result["recording_jobs"], 1)
        self.assertEqual(VideoRecordingJob.query.filter_by(task_log_id=task.id).count(), 1)
        dispatch_mock.assert_called_once()
        refresh_mock.assert_called_once_with(task.id)
        source_mock.assert_not_called()

    def test_second_manual_retry_treats_first_transition_as_idempotent(self):
        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 7, 15),
            status="failed",
            finished_at=datetime.now(timezone.utc),
        )
        db.session.add(task)
        db.session.commit()

        with mock.patch.object(
            sync_video_source_media,
            "apply_async",
            create=True,
        ) as publish_mock:
            first = retry_video_source_sync_task(
                task.id,
                {"VIDEO_DISTRIBUTED_PIPELINE": True},
            )
            second = retry_video_source_sync_task(
                task.id,
                {"VIDEO_DISTRIBUTED_PIPELINE": True},
            )

        self.assertIsNone(first["conflict"])
        self.assertEqual(second["conflict"].id, task.id)
        self.assertEqual(TaskLog.query.get(task.id).status, "pending")
        publish_mock.assert_called_once()

    def test_superseded_download_message_is_skipped(self):
        job = self._create_recording_job(filename="superseded.mp4", task_id="download-current")
        request_task = types.SimpleNamespace(request=types.SimpleNamespace(id="download-old"))

        result = download_video_recording_job.run(request_task, job.id)

        self.assertEqual(result["reason"], "superseded")
        self.assertEqual(VideoRecordingJob.query.get(job.id).status, "pending")

    def test_published_queued_job_is_not_recovered_before_lease_expiry(self):
        now = datetime.now(timezone.utc)
        job = self._create_recording_job(
            filename="patiently-queued.mp4",
            task_id="download-old",
            last_progress_at=now - timedelta(hours=2),
            published_at=now - timedelta(hours=2),
            lease_expires_at=now + timedelta(hours=4),
        )

        result = recover_stale_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["requeued"], [])
        self.assertEqual(current.download_task_id, "download-old")
        self.assertEqual(current.recovery_count, 0)

    def test_unconfirmed_publish_is_retried_with_same_execution_token(self):
        now = datetime.now(timezone.utc)
        job = self._create_recording_job(
            filename="outbox-republish.mp4",
            task_id="download-same-token",
            last_progress_at=now - timedelta(minutes=10),
            next_dispatch_at=now - timedelta(seconds=1),
            lease_expires_at=now - timedelta(seconds=1),
        )

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            return_value=types.SimpleNamespace(id="published"),
        ) as publish:
            result = recover_stale_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["requeued"], [])
        self.assertEqual(result["published"], [job.id])
        self.assertEqual(current.download_task_id, "download-same-token")
        self.assertEqual(current.recovery_count, 0)
        self.assertIsNotNone(current.published_at)
        publish.assert_called_once()

    def test_expired_published_lease_rotates_execution_token(self):
        now = datetime.now(timezone.utc)
        job = self._create_recording_job(
            filename="recover-once.mp4",
            task_id="download-old",
            last_progress_at=now - timedelta(hours=7),
            published_at=now - timedelta(hours=7),
            lease_expires_at=now - timedelta(seconds=1),
        )

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            return_value=types.SimpleNamespace(id="published"),
        ) as publish:
            first = recover_stale_video_recording_jobs()
            second = recover_stale_video_recording_jobs()

        recovered = VideoRecordingJob.query.get(job.id)
        self.assertEqual(first["requeued"], [job.id])
        self.assertEqual(second["requeued"], [])
        self.assertNotEqual(recovered.download_task_id, "download-old")
        self.assertEqual(recovered.recovery_count, 1)
        self.assertEqual(recovered.details["recovery_count"], 1)
        publish.assert_called_once()

        stale_request = types.SimpleNamespace(request=types.SimpleNamespace(id="download-old"))
        self.assertEqual(
            download_video_recording_job.run(stale_request, job.id)["reason"],
            "superseded",
        )

    def test_running_download_is_not_recovered_before_task_time_limit(self):
        job = self._create_recording_job(
            filename="still-downloading.mp4",
            last_progress_at=datetime.now(timezone.utc) - timedelta(minutes=11),
            published_at=datetime.now(timezone.utc) - timedelta(minutes=11),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            status="downloading",
            stage="downloading",
        )
        self.app.config["VIDEO_RECORDING_JOB_STALE_SECONDS"] = 600

        result = recover_stale_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["requeued"], [])
        self.assertEqual(current.status, "downloading")
        self.assertEqual(current.download_task_id, "download-current")

    def test_download_completion_hands_off_to_independent_extract_task(self):
        job = self._create_recording_job(
            filename="async-phase-handoff.mp4",
            published_at=datetime.now(timezone.utc),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
        request_task = types.SimpleNamespace(
            request=types.SimpleNamespace(id="download-current", retries=0),
            max_retries=2,
        )

        try:
            with (
                mock.patch(
                    "app.tasks.video._runtime_source_for_recording_job",
                    return_value={"config": {}},
                ),
                mock.patch("app.tasks.video._make_video_source", return_value=_FakeVideoSource()),
                mock.patch(
                    "app.tasks.video._trim_downloaded_recording_to_window",
                    return_value={"trimmed": False},
                ),
                mock.patch.object(
                    extract_video_recording_job,
                    "apply_async",
                    create=True,
                    return_value=types.SimpleNamespace(id="extract-published"),
                ) as publish_extract,
            ):
                result = download_video_recording_job.run(request_task, job.id)
        finally:
            if os.path.exists(job.video_path):
                os.remove(job.video_path)

        current = VideoRecordingJob.query.get(job.id)
        self.assertTrue(result["downloaded"])
        self.assertTrue(result["extract_queued"])
        self.assertEqual(current.status, "queued_for_extract")
        self.assertEqual(current.stage, "queued_extract")
        self.assertIsNotNone(current.extract_task_id)
        publish_extract.assert_called_once()
        self.assertEqual(publish_extract.call_args.kwargs["queue"], "video-extract")

    def test_stale_recording_recovery_stops_at_configured_limit(self):
        now = datetime.now(timezone.utc)
        job = self._create_recording_job(
            filename="recovery-exhausted.mp4",
            last_progress_at=now - timedelta(hours=7),
            recovery_count=2,
            published_at=now - timedelta(hours=7),
            lease_expires_at=now - timedelta(seconds=1),
        )
        self.app.config["VIDEO_RECORDING_JOB_MAX_RECOVERIES"] = 2

        result = recover_stale_video_recording_jobs()

        exhausted = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["exhausted"], [job.id])
        self.assertEqual(exhausted.status, "failed")
        self.assertEqual(exhausted.error_code, "recovery_limit_exceeded")

    def test_phase_changed_recovery_waits_for_download_capacity(self):
        self.app.config["VIDEO_DOWNLOAD_MAX_IN_FLIGHT"] = 1
        now = datetime.now(timezone.utc)
        self._create_recording_job(
            filename="download-capacity-holder.mp4",
            status="downloading",
            stage="downloading",
            published_at=now,
            lease_expires_at=now + timedelta(hours=1),
        )
        expired_extract = self._create_recording_job(
            filename="lost-local-extract.mp4",
            task_date=date(2026, 7, 16),
            status="extracting",
            stage="extracting",
            published_at=now - timedelta(hours=4),
            lease_expires_at=now - timedelta(seconds=1),
        )
        expired_extract.extract_task_id = "extract-old"
        db.session.commit()
        if os.path.exists(expired_extract.video_path):
            os.remove(expired_extract.video_path)

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
        ) as publish_download:
            result = recover_stale_video_recording_jobs()

        current = VideoRecordingJob.query.get(expired_extract.id)
        self.assertEqual(result["requeued"], [expired_extract.id])
        self.assertEqual(current.status, "pending")
        self.assertEqual(current.stage, "awaiting_download")
        self.assertEqual(current.recovery_count, 1)
        self.assertIsNone(current.download_task_id)
        self.assertIsNone(current.extract_task_id)
        publish_download.assert_not_called()

    def test_dispatcher_limits_download_jobs_in_flight(self):
        self.app.config["VIDEO_DOWNLOAD_MAX_IN_FLIGHT"] = 2
        task = TaskLog(
            task_type="video_source_sync",
            task_date=date(2026, 7, 15),
            status="running",
        )
        db.session.add(task)
        db.session.flush()
        jobs = []
        for index in range(5):
            job = VideoRecordingJob(
                task_log_id=task.id,
                channel_id="1",
                filename=f"bounded-{index}.mp4",
                video_path=f"/tmp/bounded-{index}.mp4",
                output_dir=f"/tmp/bounded-{index}-frames",
                download_url="http://example.com/video.mp4",
                status="pending",
                stage="awaiting_download",
            )
            db.session.add(job)
            jobs.append(job)
        db.session.commit()

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            return_value=types.SimpleNamespace(id="published"),
        ) as publish:
            result = _dispatch_available_video_recording_jobs()

        stages = [VideoRecordingJob.query.get(job.id).stage for job in jobs]
        self.assertEqual(result["queued"], [jobs[0].id, jobs[1].id])
        self.assertEqual(stages.count("queued_download"), 2)
        self.assertEqual(stages.count("awaiting_download"), 3)
        self.assertEqual(publish.call_count, 2)

    def test_dispatcher_recovers_process_exit_after_outbox_commit(self):
        now = datetime.now(timezone.utc)
        job = self._create_recording_job(
            filename="publisher-crash.mp4",
            stage="awaiting_download",
            task_id="unused",
            last_progress_at=now,
        )
        task_id = _prepare_recording_job_dispatch(job, "download", cfg=self.app.config)

        with (
            mock.patch.object(
                download_video_recording_job,
                "apply_async",
                create=True,
                side_effect=SystemExit("publisher stopped"),
            ),
            self.assertRaises(SystemExit),
        ):
            _publish_prepared_recording_job(
                job.id,
                "download",
                task_id,
                cfg=self.app.config,
            )

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(current.download_task_id, task_id)
        self.assertIsNone(current.published_at)
        current.next_dispatch_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.session.commit()

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            return_value=types.SimpleNamespace(id="published"),
        ) as publish:
            result = recover_stale_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["requeued"], [])
        self.assertEqual(current.download_task_id, task_id)
        self.assertIsNotNone(current.published_at)
        publish.assert_called_once()

    def test_transient_broker_failure_stays_durable_for_retry(self):
        job = self._create_recording_job(
            filename="broker-temporary.mp4",
            stage="awaiting_download",
        )

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            side_effect=RuntimeError("broker unavailable"),
        ):
            result = _dispatch_available_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(result["publish_failed"], [job.id])
        self.assertEqual(current.status, "pending")
        self.assertEqual(current.stage, "queued_download")
        self.assertEqual(current.dispatch_attempt_count, 1)
        self.assertIsNone(current.published_at)
        self.assertIsNotNone(current.next_dispatch_at)
        self.assertEqual(current.task_log.status, "running")

    def test_broker_failure_becomes_terminal_after_dispatch_budget(self):
        self.app.config["VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS"] = 1
        job = self._create_recording_job(
            filename="broker-exhausted.mp4",
            stage="awaiting_download",
        )

        with mock.patch.object(
            download_video_recording_job,
            "apply_async",
            create=True,
            side_effect=RuntimeError("broker unavailable"),
        ):
            _dispatch_available_video_recording_jobs()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(current.status, "failed")
        self.assertEqual(current.stage, "dispatch_failed")
        self.assertEqual(current.error_code, "download_dispatch_attempts_exhausted")
        self.assertEqual(current.details["failed_task_kind"], "download")
        self.assertEqual(current.task_log.status, "failed")

    def test_manual_retry_preserves_extract_phase_after_dispatch_failure(self):
        self.app.config["VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS"] = 1
        job = self._create_recording_job(
            filename="extract-dispatch-retry.mp4",
            status="queued_for_extract",
            stage="awaiting_extract",
        )
        job.download_finished_at = datetime.now(timezone.utc)
        with open(job.video_path, "wb") as handle:
            handle.write(b"downloaded-video")
        db.session.commit()

        try:
            with mock.patch.object(
                extract_video_recording_job,
                "apply_async",
                create=True,
                side_effect=RuntimeError("broker unavailable"),
            ):
                _dispatch_available_video_recording_jobs()

            failed = VideoRecordingJob.query.get(job.id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.extract_attempt_count, 0)
            self.assertEqual(failed.details["failed_task_kind"], "extract")

            self.app.config["VIDEO_RECORDING_DISPATCH_MAX_ATTEMPTS"] = 20
            with (
                mock.patch.object(
                    extract_video_recording_job,
                    "apply_async",
                    create=True,
                    return_value=types.SimpleNamespace(id="extract-published"),
                ) as publish_extract,
                mock.patch.object(
                    download_video_recording_job,
                    "apply_async",
                    create=True,
                ) as publish_download,
            ):
                retried_count = retry_failed_video_recording_jobs(job.task_log_id)

            current = VideoRecordingJob.query.get(job.id)
            self.assertEqual(retried_count, 1)
            self.assertEqual(current.status, "queued_for_extract")
            self.assertEqual(current.stage, "queued_extract")
            self.assertEqual(current.extract_attempt_count, 0)
            self.assertNotIn("failed_task_kind", current.details)
            publish_extract.assert_called_once()
            publish_download.assert_not_called()
        finally:
            if os.path.exists(job.video_path):
                os.remove(job.video_path)

    def test_manual_parent_failure_cancels_all_active_recording_jobs(self):
        job = self._create_recording_job(
            filename="cancel-with-parent.mp4",
            published_at=datetime.now(timezone.utc),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
        task = job.task_log

        mark_sync_task_failed(task, "用户取消任务")
        db.session.commit()

        current = VideoRecordingJob.query.get(job.id)
        self.assertEqual(task.status, "failed")
        self.assertEqual(current.status, "cancelled")
        self.assertEqual(current.error_code, "parent_task_terminated")
        self.assertIsNone(current.lease_expires_at)
        self.assertEqual(task.meta["active_recording_count"], 0)
        self.assertEqual(task.meta["recordings"][0]["download_status"], "cancelled")

    def test_parent_aggregation_reports_partial_only_after_all_jobs_finish(self):
        first = self._create_recording_job(filename="partial-success.mp4")
        task = first.task_log
        first.status = "success"
        first.stage = "complete"
        first.finished_at = datetime.now(timezone.utc)
        second = VideoRecordingJob(
            task_log_id=task.id,
            channel_id="1",
            filename="partial-failed.mp4",
            video_path="/tmp/partial-failed.mp4",
            output_dir="/tmp/partial-failed-frames",
            download_url="http://example.com/video.mp4",
            status="failed",
            stage="failed",
            finished_at=datetime.now(timezone.utc),
        )
        db.session.add(second)
        db.session.commit()

        _refresh_distributed_sync_task(task.id)

        db.session.refresh(task)
        self.assertEqual(task.status, "partial")
        self.assertEqual(task.meta["completed_recording_count"], 1)
        self.assertEqual(task.meta["failed_recording_count"], 1)
        self.assertIsNotNone(task.finished_at)

    def test_cleanup_expired_video_recordings_deletes_old_date_dirs(self):
        cfg = {"VIDEO_TIMEZONE": "Asia/Shanghai"}
        with tempfile.TemporaryDirectory() as tmpdir:
            for dirname in ("2026-05-10", "2026-05-11", "2026-05-12", "2026-05-14"):
                os.makedirs(os.path.join(tmpdir, dirname), exist_ok=True)
                with open(os.path.join(tmpdir, dirname, "recording.mp4"), "wb") as handle:
                    handle.write(b"video")
            old_file = os.path.join(tmpdir, "old-orphan.mp4")
            with open(old_file, "wb") as handle:
                handle.write(b"video")
            old_mtime = datetime(2026, 5, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(old_file, (old_mtime, old_mtime))

            summary = _cleanup_expired_video_recordings(
                tmpdir,
                3,
                cfg,
                now=datetime(2026, 5, 14, 21, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
                keep_dates={date(2026, 5, 10)},
            )

            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-10")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "2026-05-11")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-12")))
            self.assertTrue(os.path.isdir(os.path.join(tmpdir, "2026-05-14")))
            self.assertFalse(os.path.exists(old_file))
            self.assertEqual(summary["cutoff_date"], "2026-05-12")
            self.assertIn("2026-05-11", summary["deleted_dirs"])
            self.assertIn("old-orphan.mp4", summary["deleted_files"])

    def _create_menu(self, menu_date):
        dish = Dish(name="红烧肉", price=12.0, category=CategoryEnum.meat, is_active=True)
        db.session.add(dish)
        db.session.flush()
        db.session.add(DailyMenu(
            menu_date=menu_date,
            meal_dish_ids={
                "breakfast": [],
                "lunch": [dish.id],
                "dinner": [],
                "late_night": [],
            },
            is_default=False,
        ))
        db.session.commit()

    def test_sync_video_source_task_records_recordings_in_task_meta(self):
        self._create_menu(date(2026, 4, 3))
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "21:30",
                "local_storage_path": "/tmp/nutrition-monitoring-test-videos",
                "retention_days": 3,
            },
        })

        fake_video_analyzer = types.ModuleType("app.services.video_analyzer")

        class FakeVideoAnalyzer:
            def __init__(self, config):
                self.config = config

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                if progress_callback is not None:
                    progress_callback({
                        "frame_no": 50,
                        "total_frames": 100,
                        "progress_percent": 50.0,
                        "extracted_count": 1,
                        "frame_step": 2,
                        "effective_scan_fps": 15.0,
                        "extract_strategy": "ffmpeg_nvdec",
                        "decode_backend": "nvdec",
                        "analysis_width": 960,
                        "analysis_height": 540,
                        "elapsed_seconds": 12.5,
                        "realtime_factor": 24.0,
                        "stage_timings": {
                            "decode_seconds": 2.0,
                            "analysis_seconds": 10.0,
                            "candidate_write_seconds": 0.5,
                        },
                    })
                return [
                    {
                        "channel_id": channel_id,
                        "captured_at": video_start_time,
                        "image_path": os.path.join(output_dir, "frame-1.jpg"),
                        "is_candidate": False,
                    },
                    {
                        "channel_id": channel_id,
                        "captured_at": video_start_time,
                        "image_path": os.path.join(output_dir, "frame-2.jpg"),
                        "is_candidate": True,
                    },
                ]

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        fake_recognition = types.ModuleType("app.tasks.recognition")

        fake_recognition.enqueue_recognition_images = lambda image_ids, **kwargs: types.SimpleNamespace(
            id=900,
            total_count=len(image_ids),
        )
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            with mock.patch("app.tasks.video._make_video_source", return_value=_FakeVideoSource()):
                sync_video_source_media.run(
                    types.SimpleNamespace(retry=lambda *args, **kwargs: None),
                    "2026-04-03",
                )
        finally:
            if original_video_analyzer is None:
                sys.modules.pop("app.services.video_analyzer", None)
            else:
                sys.modules["app.services.video_analyzer"] = original_video_analyzer

            if original_recognition is None:
                sys.modules.pop("app.tasks.recognition", None)
            else:
                sys.modules["app.tasks.recognition"] = original_recognition

        task = TaskLog.query.filter_by(task_type="video_source_sync").one()
        self.assertEqual(task.status, "success")
        self.assertEqual(task.total_count, 6)
        self.assertEqual(task.success_count, 6)
        self.assertEqual(task.meta["recording_count"], 3)
        self.assertEqual(task.meta["primary_count"], 3)
        self.assertEqual(task.meta["candidate_count"], 3)
        self.assertEqual(len(task.meta["recordings"]), 3)
        self.assertTrue(all(item["channel_id"] == "8" for item in task.meta["recordings"]))
        self.assertTrue(all(item["download_status"] == "success" for item in task.meta["recordings"]))
        self.assertTrue(all(item["frame_count"] == 2 for item in task.meta["recordings"]))
        self.assertTrue(all(len(item["image_ids"]) == 2 for item in task.meta["recordings"]))
        self.assertTrue(all(item["progress_percent"] == 100.0 for item in task.meta["recordings"]))
        self.assertTrue(all(item["effective_scan_fps"] == 15.0 for item in task.meta["recordings"]))
        self.assertTrue(all(item["decode_backend"] == "nvdec" for item in task.meta["recordings"]))
        self.assertTrue(all(item["analysis_width"] == 960 for item in task.meta["recordings"]))
        self.assertTrue(all(item["realtime_factor"] == 24.0 for item in task.meta["recordings"]))
        self.assertEqual(len(task.meta["image_ids"]), 6)

    def test_sync_video_source_uses_ffmpeg_fallback_after_analyzer_failure(self):
        self._create_menu(date(2026, 4, 3))
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "21:30",
                "local_storage_path": "/tmp/nutrition-monitoring-test-videos",
                "retention_days": 3,
            },
        })

        fake_video_analyzer = types.ModuleType("app.services.video_analyzer")

        class FakeVideoAnalyzer:
            def __init__(self, config):
                self.config = config

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                raise RuntimeError("extract stuck")

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        recognition_calls = []
        fake_recognition = types.ModuleType("app.tasks.recognition")

        def enqueue_recognition_images(image_ids, **kwargs):
            recognition_calls.append((list(image_ids), kwargs))
            return types.SimpleNamespace(id=900 + len(recognition_calls), total_count=len(image_ids))

        fake_recognition.enqueue_recognition_images = enqueue_recognition_images
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            def fallback_extract(cfg, video_path, output_dir, video_start, channel_id, progress_callback=None, cancel_event=None):
                return [{
                    "channel_id": channel_id,
                    "captured_at": video_start,
                    "image_path": os.path.join(output_dir, "fallback.jpg"),
                    "is_candidate": False,
                    "extraction_strategy": "ffmpeg_interval_fallback",
                }]

            with (
                mock.patch("app.tasks.video._make_video_source", return_value=_FakeVideoSource()),
                mock.patch("app.tasks.video._repair_video_for_extract", side_effect=RuntimeError("repair failed")),
                mock.patch("app.tasks.video._extract_frames_with_ffmpeg_fallback", side_effect=fallback_extract),
            ):
                sync_video_source_media.run(
                    types.SimpleNamespace(retry=lambda *args, **kwargs: None),
                    "2026-04-03",
                )
        finally:
            if original_video_analyzer is None:
                sys.modules.pop("app.services.video_analyzer", None)
            else:
                sys.modules["app.services.video_analyzer"] = original_video_analyzer

            if original_recognition is None:
                sys.modules.pop("app.tasks.recognition", None)
            else:
                sys.modules["app.tasks.recognition"] = original_recognition

        task = TaskLog.query.filter_by(task_type="video_source_sync").one()
        self.assertEqual(task.status, "success")
        self.assertEqual(task.total_count, 3)
        self.assertEqual(task.success_count, 3)
        self.assertEqual(task.error_count, 0)
        self.assertEqual(task.meta["recording_count"], 3)
        self.assertTrue(all(item["download_status"] == "success" for item in task.meta["recordings"]))
        self.assertTrue(all(item["fallback_used"] for item in task.meta["recordings"]))
        self.assertEqual(len(recognition_calls), 3)
        self.assertTrue(all(len(image_ids) == 1 for image_ids, _kwargs in recognition_calls))
        self.assertTrue(all(kwargs["target_date"] == date(2026, 4, 3) for _image_ids, kwargs in recognition_calls))

    def test_sync_video_source_starts_extracting_before_all_recordings_are_downloaded(self):
        self._create_menu(date(2026, 4, 3))
        manager = VideoSourceManager(self.app.config)
        manager.create_source({
            "name": "食堂主 NVR",
            "source_type": "nvr",
            "status": "enabled",
            "is_active": True,
            "config": {
                "host": "192.168.1.10",
                "port": 8080,
                "username": "admin",
                "password": "secret-1",
                "channel_ids": ["8"],
                "meal_windows": [{"start": "11:30", "end": "13:00"}],
                "download_trigger_time": "21:30",
                "local_storage_path": "/tmp/nutrition-monitoring-test-videos",
                "retention_days": 3,
            },
        })

        events = []
        first_extract_started = threading.Event()
        pipeline_failures = []

        def before_download(download_count):
            if download_count == 2 and not first_extract_started.wait(timeout=2.0):
                pipeline_failures.append("second download started before first extraction")

        fake_video_analyzer = types.ModuleType("app.services.video_analyzer")

        class FakeVideoAnalyzer:
            def __init__(self, config):
                self.config = config

            def extract_frames(self, video_path, output_dir, video_start_time, channel_id, progress_callback=None):
                events.append(("extract", os.path.basename(video_path)))
                first_extract_started.set()
                return [{
                    "channel_id": channel_id,
                    "captured_at": video_start_time,
                    "image_path": os.path.join(output_dir, "frame-1.jpg"),
                    "is_candidate": False,
                }]

        fake_video_analyzer.VideoAnalyzer = FakeVideoAnalyzer
        original_video_analyzer = sys.modules.get("app.services.video_analyzer")
        sys.modules["app.services.video_analyzer"] = fake_video_analyzer

        fake_recognition = types.ModuleType("app.tasks.recognition")

        fake_recognition.enqueue_recognition_images = lambda image_ids, **kwargs: types.SimpleNamespace(
            id=900,
            total_count=len(image_ids),
        )
        original_recognition = sys.modules.get("app.tasks.recognition")
        sys.modules["app.tasks.recognition"] = fake_recognition

        try:
            from unittest import mock

            with mock.patch("app.tasks.video._make_video_source", return_value=_OrderingVideoSource(events, before_download)):
                sync_video_source_media.run(
                    types.SimpleNamespace(retry=lambda *args, **kwargs: None),
                    "2026-04-03",
                )
        finally:
            if original_video_analyzer is None:
                sys.modules.pop("app.services.video_analyzer", None)
            else:
                sys.modules["app.services.video_analyzer"] = original_video_analyzer

            if original_recognition is None:
                sys.modules.pop("app.tasks.recognition", None)
            else:
                sys.modules["app.tasks.recognition"] = original_recognition

        self.assertEqual(len(events), 6)
        self.assertEqual(pipeline_failures, [])
        event_types = [event[0] for event in events]
        self.assertEqual(event_types.count("download"), 3)
        self.assertEqual(event_types.count("extract"), 3)
        self.assertLess(
            event_types.index("extract"),
            max(index for index, event_type in enumerate(event_types) if event_type == "download"),
        )


if __name__ == "__main__":
    unittest.main()
