import os
import sys
import types
import unittest
import importlib.util
import io
import tempfile
from datetime import datetime, timedelta
from unittest import mock

import numpy as np

try:
    import cv2  # noqa: F401
except ModuleNotFoundError:
    sys.modules["cv2"] = types.SimpleNamespace(VideoCapture=object)

MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "app",
    "services",
    "video_analyzer.py",
)
SPEC = importlib.util.spec_from_file_location("video_analyzer", MODULE_PATH)
VIDEO_ANALYZER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VIDEO_ANALYZER)
if not hasattr(VIDEO_ANALYZER.cv2, "CAP_PROP_POS_MSEC"):
    VIDEO_ANALYZER.cv2.CAP_PROP_POS_MSEC = 0
if not hasattr(VIDEO_ANALYZER.cv2, "CAP_PROP_POS_FRAMES"):
    VIDEO_ANALYZER.cv2.CAP_PROP_POS_FRAMES = 1
if not hasattr(VIDEO_ANALYZER.cv2, "IMWRITE_JPEG_QUALITY"):
    VIDEO_ANALYZER.cv2.IMWRITE_JPEG_QUALITY = 1
if not hasattr(VIDEO_ANALYZER.cv2, "imwrite"):
    VIDEO_ANALYZER.cv2.imwrite = lambda *args, **kwargs: True

AnalyzerConfig = VIDEO_ANALYZER.AnalyzerConfig
BackgroundModel = VIDEO_ANALYZER.BackgroundModel
CandidateFrame = VIDEO_ANALYZER.CandidateFrame
ClosedEvent = VIDEO_ANALYZER.ClosedEvent
EventStateMachine = VIDEO_ANALYZER.EventStateMachine
EventWindow = VIDEO_ANALYZER.EventWindow
ForegroundAnalysis = VIDEO_ANALYZER.ForegroundAnalysis
FrameSampler = VIDEO_ANALYZER.FrameSampler
FrameScorer = VIDEO_ANALYZER.FrameScorer
FFmpegDecodeError = VIDEO_ANALYZER.FFmpegDecodeError
FFmpegSampledReader = VIDEO_ANALYZER.FFmpegSampledReader
MotionMeasure = VIDEO_ANALYZER.MotionMeasure
ResultWriter = VIDEO_ANALYZER.ResultWriter
VideoStreamInfo = VIDEO_ANALYZER.VideoStreamInfo
VideoAnalyzer = VIDEO_ANALYZER.VideoAnalyzer
mark_secondary_frames_by_quality = VIDEO_ANALYZER._mark_secondary_frames_by_quality


def make_config(**overrides):
    base = {
        "ROI_REGION": {"x": 0, "y": 0, "w": 60, "h": 60},
        "VIDEO_TIMEZONE": "Asia/Shanghai",
        "MOTION_RATIO_THRESHOLD": 0.10,
        "STABLE_FRAMES_ENTER": 2,
        "STABLE_FRAMES_EXIT": 2,
        "STABLE_SAMPLE_INTERVAL": 1,
        "FG_RATIO_THRESHOLD": 0.15,
        "FG_MIN_COMPONENT_AREA": 20,
        "PLATE_MIN_AREA_RATIO": 0.12,
        "PLATE_MAX_AREA_RATIO": 0.85,
        "PLATE_CENTER_MAX_RATIO": 0.95,
        "PLATE_EDGE_TOUCH_MAX_RATIO": 0.25,
        "QUICK_STABLE_FRAMES_MIN": 2,
        "STABLE_PRESENT_FRAMES_MIN": 1,
    }
    base.update(overrides)
    return AnalyzerConfig.from_mapping(base)


def textured_gray(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    pattern = np.indices((60, 60)).sum(axis=0) % 2
    texture = (pattern * 255).astype(np.uint8)
    noise = rng.integers(0, 24, size=(60, 60), dtype=np.uint8)
    return np.clip(texture + noise, 0, 255).astype(np.uint8)


def make_motion(score: float, seed: int) -> MotionMeasure:
    return MotionMeasure(
        motion_score=score,
        moving=score >= 0.10,
        changed_pixels=int(score * 1000),
        gray=textured_gray(seed),
    )


def make_foreground(present: bool, fg_ratio: float = 0.20) -> ForegroundAnalysis:
    mask = np.zeros((60, 60), dtype=np.uint8)
    if present:
        mask[12:48, 10:50] = 255
        fg_pixels = int(np.count_nonzero(mask))
        bbox = (10, 12, 40, 36)
        largest_area = fg_pixels
        largest_area_ratio = fg_pixels / float(mask.size)
        center_distance_ratio = 0.15
        edge_touch_ratio = 0.0
    else:
        fg_pixels = 0
        bbox = None
        largest_area = 0
        largest_area_ratio = 0.0
        center_distance_ratio = 1.0
        edge_touch_ratio = 1.0

    return ForegroundAnalysis(
        fg_mask=mask,
        fg_ratio=fg_ratio if present else 0.0,
        fg_pixels=fg_pixels,
        present=present,
        largest_bbox=bbox,
        largest_area=largest_area,
        largest_area_ratio=largest_area_ratio,
        center_distance_ratio=center_distance_ratio,
        edge_touch_ratio=edge_touch_ratio,
    )


def make_edge_foreground() -> ForegroundAnalysis:
    mask = np.zeros((60, 60), dtype=np.uint8)
    mask[8:44, 0:30] = 255
    fg_pixels = int(np.count_nonzero(mask))
    return ForegroundAnalysis(
        fg_mask=mask,
        fg_ratio=fg_pixels / float(mask.size),
        fg_pixels=fg_pixels,
        present=True,
        largest_bbox=(0, 8, 30, 36),
        largest_area=fg_pixels,
        largest_area_ratio=fg_pixels / float(mask.size),
        center_distance_ratio=0.55,
        edge_touch_ratio=0.85,
    )


def make_relaxed_foreground() -> ForegroundAnalysis:
    mask = np.zeros((60, 60), dtype=np.uint8)
    mask[10:46, 6:42] = 255
    fg_pixels = int(np.count_nonzero(mask))
    return ForegroundAnalysis(
        fg_mask=mask,
        fg_ratio=fg_pixels / float(mask.size),
        fg_pixels=fg_pixels,
        present=True,
        largest_bbox=(6, 10, 36, 36),
        largest_area=fg_pixels,
        largest_area_ratio=fg_pixels / float(mask.size),
        center_distance_ratio=0.38,
        edge_touch_ratio=0.32,
    )


def make_frame(seed: int) -> np.ndarray:
    gray = textured_gray(seed)
    return np.dstack([gray, gray, gray])


@unittest.skipUnless(
    hasattr(VIDEO_ANALYZER.cv2, "connectedComponentsWithStats"),
    "OpenCV image processing is not available",
)
class AnalyzerCpuOptimizationTests(unittest.TestCase):
    def test_background_model_skips_redundant_threshold_without_shadows(self):
        model = BackgroundModel(make_config(
            BG_DETECT_SHADOWS=False,
            MORPH_OPEN_KERNEL=1,
            MORPH_CLOSE_KERNEL=1,
        ))
        frame = np.zeros((12, 12, 3), dtype=np.uint8)

        with mock.patch.object(
            VIDEO_ANALYZER.cv2,
            "threshold",
            wraps=VIDEO_ANALYZER.cv2.threshold,
        ) as threshold:
            result = model.analyze(frame, mode="update")

        threshold.assert_not_called()
        self.assertEqual(result.fg_mask.size, 0)

    def test_component_measurement_sums_only_large_components(self):
        model = BackgroundModel.__new__(BackgroundModel)
        model.config = make_config(FG_MIN_COMPONENT_AREA=4)
        mask = np.zeros((12, 12), dtype=np.uint8)
        mask[0, 0:3] = 255
        mask[2:4, 2:5] = 255
        mask[8:10, 8:10] = 255

        stats = model._measure_components(mask)

        self.assertEqual(stats["fg_pixels"], 10)
        self.assertEqual(stats["largest_area"], 6)
        self.assertEqual(stats["largest_bbox"], (2, 2, 3, 2))


class EventStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.machine = EventStateMachine(self.config)
        self.scorer = FrameScorer(self.config)

    def test_emits_event_after_stable_plate_and_motion_exit(self):
        frames = [
            (0, make_motion(0.30, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.02, 2), make_foreground(True), make_frame(2)),
            (2, make_motion(0.01, 3), make_foreground(True), make_frame(3)),
            (3, make_motion(0.02, 4), make_foreground(True), make_frame(4)),
            (4, make_motion(0.18, 5), make_foreground(True), make_frame(5)),
            (5, make_motion(0.22, 6), make_foreground(True), make_frame(6)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = self.machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=self.scorer,
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertGreaterEqual(completed.window.candidate_count, 2)
        self.assertEqual(completed.window.start_frame_no, 1)
        self.assertLessEqual(completed.window.preferred_frame_no, completed.window.end_frame_no)

    def test_empty_candidate_pool_skips_output(self):
        frames = [
            (0, make_motion(0.02, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.01, 2), make_foreground(False), make_frame(2)),
            (2, make_motion(0.20, 3), make_foreground(False), make_frame(3)),
            (3, make_motion(0.22, 4), make_foreground(False), make_frame(4)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = self.machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=self.scorer,
            )

        self.assertIsNone(completed)

    def test_edge_foreground_still_keeps_event_candidate(self):
        frames = [
            (0, make_motion(0.02, 1), make_edge_foreground(), make_frame(1)),
            (1, make_motion(0.01, 2), make_edge_foreground(), make_frame(2)),
            (2, make_motion(0.20, 3), make_edge_foreground(), make_frame(3)),
            (3, make_motion(0.22, 4), make_edge_foreground(), make_frame(4)),
        ]

        completed = None
        sampled_flags = []
        for frame_no, motion, foreground, frame in frames:
            scan_frame, completed = self.machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=self.scorer,
            )
            sampled_flags.append(scan_frame.sampled)

        self.assertIsNotNone(completed)
        self.assertTrue(any(sampled_flags))

    def test_relaxed_plate_candidate_still_produces_event(self):
        frames = [
            (0, make_motion(0.25, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.02, 2), make_relaxed_foreground(), make_frame(2)),
            (2, make_motion(0.01, 3), make_relaxed_foreground(), make_frame(3)),
            (3, make_motion(0.18, 4), make_relaxed_foreground(), make_frame(4)),
            (4, make_motion(0.20, 5), make_relaxed_foreground(), make_frame(5)),
        ]

        completed = None
        sampled_flags = []
        for frame_no, motion, foreground, frame in frames:
            scan_frame, completed = self.machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=self.scorer,
            )
            sampled_flags.append(scan_frame.sampled)

        self.assertIsNotNone(completed)
        self.assertTrue(any(sampled_flags))

    def test_quick_stable_fallback_emits_short_event(self):
        config = make_config(STABLE_FRAMES_ENTER=5, QUICK_STABLE_FRAMES_MIN=2)
        machine = EventStateMachine(config)
        scorer = FrameScorer(config)
        frames = [
            (0, make_motion(0.28, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.02, 2), make_foreground(True), make_frame(2)),
            (2, make_motion(0.01, 3), make_foreground(True), make_frame(3)),
            (3, make_motion(0.21, 4), make_foreground(True), make_frame(4)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=scorer,
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertTrue(completed.window.quality_note.startswith("quick_stable_fallback"))
        self.assertEqual(completed.window.start_frame_no, 1)

    def test_legacy_single_candidate_fallback_emits_short_event(self):
        config = make_config(STABLE_FRAMES_ENTER=5, LEGACY_QUICK_STABLE_FRAMES_MIN=1)
        machine = EventStateMachine(config)
        scorer = FrameScorer(config)
        frames = [
            (0, make_motion(0.28, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.02, 2), make_foreground(True), make_frame(2)),
            (2, make_motion(0.21, 3), make_foreground(True), make_frame(3)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=scorer,
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertTrue(completed.window.quality_note.startswith("quick_stable_fallback"))
        self.assertEqual(completed.window.candidate_count, 1)

    def test_default_quick_fallback_emits_single_candidate_recall(self):
        config = make_config(STABLE_FRAMES_ENTER=5)
        machine = EventStateMachine(config)
        scorer = FrameScorer(config)
        frames = [
            (0, make_motion(0.28, 1), make_foreground(False), make_frame(1)),
            (1, make_motion(0.02, 2), make_foreground(True), make_frame(2)),
            (2, make_motion(0.21, 3), make_foreground(True), make_frame(3)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=scorer,
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertTrue(completed.window.quality_note.startswith("quick_stable_fallback"))
        self.assertEqual(completed.window.candidate_count, 1)

    def test_pre_stable_candidate_emits_when_foreground_leaves_before_stable(self):
        config = make_config(STABLE_FRAMES_ENTER=5, QUICK_STABLE_FRAMES_MIN=2)
        machine = EventStateMachine(config)
        scorer = FrameScorer(config)
        frames = [
            (0, make_motion(0.01, 1), make_foreground(True), make_frame(1)),
            (1, make_motion(0.01, 2), make_foreground(True), make_frame(2)),
            (2, make_motion(0.01, 3), make_foreground(False), make_frame(3)),
        ]

        completed = None
        for frame_no, motion, foreground, frame in frames:
            _, completed = machine.process_frame(
                frame_no=frame_no,
                ts=frame_no / 10.0,
                frame=frame,
                motion=motion,
                foreground=foreground,
                scorer=scorer,
            )

        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertTrue(completed.window.quality_note.startswith("legacy_pre_stable_fallback"))


class VideoAnalyzerTimeTests(unittest.TestCase):
    def test_same_second_primary_is_selected_by_quality_not_event_order(self):
        start = datetime(2026, 7, 13, 12, 0, 0)
        frames = [
            {
                "channel_id": "8",
                "captured_at": start + timedelta(milliseconds=100),
                "best_score": 0.7,
                "focus_score": 80,
                "low_quality": False,
            },
            {
                "channel_id": "8",
                "captured_at": start + timedelta(milliseconds=600),
                "best_score": 0.9,
                "focus_score": 120,
                "low_quality": False,
            },
            {
                "channel_id": "8",
                "captured_at": start + timedelta(seconds=1, milliseconds=100),
                "best_score": 0.5,
                "focus_score": 40,
                "low_quality": True,
            },
        ]

        mark_secondary_frames_by_quality(frames)

        self.assertEqual(frames[0]["captured_at"], start + timedelta(milliseconds=600))
        self.assertFalse(frames[0]["is_candidate"])
        self.assertTrue(frames[1]["is_candidate"])
        self.assertFalse(frames[2]["is_candidate"])

    def test_non_low_quality_frame_beats_higher_scored_relaxed_fallback(self):
        start = datetime(2026, 7, 13, 12, 0, 0)
        frames = [
            {
                "channel_id": "8",
                "captured_at": start + timedelta(milliseconds=100),
                "best_score": 1.0,
                "focus_score": 200,
                "low_quality": True,
            },
            {
                "channel_id": "8",
                "captured_at": start + timedelta(milliseconds=600),
                "best_score": 0.5,
                "focus_score": 50,
                "low_quality": False,
            },
        ]

        mark_secondary_frames_by_quality(frames)

        self.assertEqual(frames[0]["captured_at"], start + timedelta(milliseconds=600))
        self.assertFalse(frames[0]["is_candidate"])
        self.assertTrue(frames[1]["is_candidate"])

    def test_naive_video_time_uses_configured_timezone(self):
        analyzer = VideoAnalyzer({"VIDEO_TIMEZONE": "Asia/Shanghai"})
        normalized = analyzer._normalize_video_start_time(VIDEO_ANALYZER.datetime(2026, 3, 26, 12, 30, 0))

        self.assertEqual(str(normalized.tzinfo), "Asia/Shanghai")
        self.assertEqual(normalized.hour, 12)

    def test_frame_timestamp_prefers_video_position_msec(self):
        class FakeCapture:
            def get(self, prop):
                self.prop = prop
                return 3250.0

        ts = VideoAnalyzer._frame_timestamp_seconds(FakeCapture(), frame_no=100, video_fps=25.0)

        self.assertEqual(ts, 3.25)

    def test_frame_timestamp_subtracts_first_frame_position_baseline(self):
        class FakeCapture:
            def get(self, prop):
                return 653702.0

        ts = VideoAnalyzer._frame_timestamp_seconds(
            FakeCapture(),
            frame_no=0,
            video_fps=25.0,
            position_msec=653702.0,
            position_msec_base=653702.0,
        )

        self.assertEqual(ts, 0.0)

    def test_frame_timestamp_falls_back_to_frame_number_when_position_missing(self):
        class FakeCapture:
            def get(self, prop):
                return 0.0

        ts = VideoAnalyzer._frame_timestamp_seconds(FakeCapture(), frame_no=100, video_fps=25.0)

        self.assertEqual(ts, 4.0)

    def test_result_writer_uses_candidate_timestamp(self):
        original_imwrite = VIDEO_ANALYZER.cv2.imwrite
        VIDEO_ANALYZER.cv2.imwrite = lambda *args, **kwargs: True
        try:
            start = datetime(2026, 3, 26, 12, 30, 0)
            frame = np.zeros((4, 4, 3), dtype=np.uint8)
            candidate = CandidateFrame(
                frame_no=100,
                ts=3.25,
                frame=frame,
                fg_mask=np.zeros((4, 4), dtype=np.uint8),
                roi_gray=np.zeros((4, 4), dtype=np.uint8),
                motion_score=0.1,
                fg_ratio=0.2,
                changed_pixels=10,
                laplacian_score=1.0,
                tenengrad_score=1.0,
                local_clarity_score=1.0,
                high_frequency_ratio=0.1,
                completeness_raw=0.5,
                center_distance_ratio=0.1,
                edge_touch_ratio=0.0,
            )
            event = ClosedEvent(
                window=EventWindow(
                    core_start_frame_no=90,
                    core_end_frame_no=110,
                    start_frame_no=90,
                    end_frame_no=110,
                    preferred_frame_no=100,
                    peak_frame_no=95,
                    peak_motion_score=0.2,
                    candidate_count=1,
                    best_score=1.0,
                    low_quality=False,
                    quality_note="",
                ),
                best_candidate=candidate,
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                writer = ResultWriter(temp_dir, "8", start, "events.jsonl")
                result = writer.write(event)

            self.assertEqual(result["captured_at"], start + timedelta(seconds=3.25))
        finally:
            VIDEO_ANALYZER.cv2.imwrite = original_imwrite

    def test_result_writer_loads_selected_frame_from_source_video(self):
        original_video_capture = VIDEO_ANALYZER.cv2.VideoCapture
        original_imwrite = VIDEO_ANALYZER.cv2.imwrite
        loaded_frame = np.full((4, 4, 3), 127, dtype=np.uint8)
        writes = {}

        class FakeCapture:
            def __init__(self, path):
                self.path = path

            def isOpened(self):
                return True

            def set(self, prop, value):
                writes["seek"] = (prop, value)
                return True

            def read(self):
                return True, loaded_frame

            def release(self):
                writes["released"] = True

        def fake_imwrite(path, frame, options):
            writes["path"] = path
            writes["frame"] = frame
            writes["options"] = options
            return True

        VIDEO_ANALYZER.cv2.VideoCapture = FakeCapture
        VIDEO_ANALYZER.cv2.imwrite = fake_imwrite
        try:
            start = datetime(2026, 3, 26, 12, 30, 0)
            candidate = CandidateFrame(
                frame_no=42,
                ts=1.0,
                frame=None,
                fg_mask=np.zeros((4, 4), dtype=np.uint8),
                roi_gray=np.zeros((4, 4), dtype=np.uint8),
                motion_score=0.1,
                fg_ratio=0.2,
                changed_pixels=10,
                laplacian_score=1.0,
                tenengrad_score=1.0,
                local_clarity_score=1.0,
                high_frequency_ratio=0.1,
                completeness_raw=0.5,
                center_distance_ratio=0.1,
                edge_touch_ratio=0.0,
            )
            event = ClosedEvent(
                window=EventWindow(
                    core_start_frame_no=40,
                    core_end_frame_no=45,
                    start_frame_no=40,
                    end_frame_no=45,
                    preferred_frame_no=42,
                    peak_frame_no=42,
                    peak_motion_score=0.2,
                    candidate_count=1,
                    best_score=1.0,
                    low_quality=False,
                    quality_note="",
                ),
                best_candidate=candidate,
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                writer = ResultWriter(temp_dir, "8", start, "events.jsonl", video_path="/tmp/source.mp4")
                writer.write(event)

            self.assertEqual(writes["seek"], (VIDEO_ANALYZER.cv2.CAP_PROP_POS_FRAMES, 42))
            self.assertIs(writes["frame"], loaded_frame)
            self.assertTrue(writes["released"])
        finally:
            VIDEO_ANALYZER.cv2.VideoCapture = original_video_capture
            VIDEO_ANALYZER.cv2.imwrite = original_imwrite

    def test_result_writer_filename_always_includes_milliseconds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = ResultWriter(temp_dir, "ch01", datetime(2026, 4, 10, 12, 9, 34, 798000), "events.jsonl")

            self.assertEqual(
                writer._make_frame_filename(datetime(2026, 4, 10, 12, 9, 34, 798000)),
                "ch01_2026-04-10-12-09-34-798.jpg",
            )

    def test_video_analyzer_initializes_legacy_pipeline(self):
        analyzer = VideoAnalyzer({})

        self.assertEqual(analyzer.config.event_scan_fps, 12.0)
        self.assertEqual(analyzer.config.legacy_analysis_max_width, 960)
        self.assertEqual(analyzer.config.legacy_analysis_max_height, 540)
        self.assertEqual(analyzer.config.max_event_candidates, 120)
        self.assertEqual(analyzer.config.max_scan_history, 10000)
        self.assertEqual(analyzer.config.quality_max_dimension, 320)
        self.assertEqual(analyzer.cpu_threads, 2)
        self.assertEqual(analyzer.decode_backend, "opencv")

    def test_auto_decode_backend_falls_back_from_nvdec_to_cpu(self):
        analyzer = VideoAnalyzer({"VIDEO_EXTRACT_DECODE_BACKEND": "auto"})
        stream_info = VideoStreamInfo(fps=25.0, total_frames=250, width=1280, height=720)
        calls = []

        def fake_extract(*args, **kwargs):
            backend = args[5]
            calls.append(backend)
            if backend == "nvdec":
                raise FFmpegDecodeError("cuda unavailable", frames_read=0)
            return [{"extraction_strategy": backend}]

        with mock.patch.object(analyzer, "_probe_video_stream", return_value=stream_info), mock.patch.object(
            analyzer,
            "_extract_frames_with_backend",
            side_effect=fake_extract,
        ):
            frames = analyzer.extract_frames("video.mp4", "/tmp", datetime(2026, 1, 1), "8")

        self.assertEqual(calls, ["nvdec", "ffmpeg_cpu"])
        self.assertEqual(frames[0]["extraction_strategy"], "ffmpeg_cpu")

    def test_auto_decode_backend_does_not_switch_after_partial_decode(self):
        analyzer = VideoAnalyzer({"VIDEO_EXTRACT_DECODE_BACKEND": "auto"})
        stream_info = VideoStreamInfo(fps=25.0, total_frames=250, width=1280, height=720)

        with mock.patch.object(analyzer, "_probe_video_stream", return_value=stream_info), mock.patch.object(
            analyzer,
            "_extract_frames_with_backend",
            side_effect=FFmpegDecodeError("mid-stream failure", frames_read=5),
        ) as extract:
            with self.assertRaises(FFmpegDecodeError):
                analyzer.extract_frames("video.mp4", "/tmp", datetime(2026, 1, 1), "8")

        self.assertEqual(extract.call_count, 1)

    def test_bounded_crop_region_clamps_roi_to_video(self):
        self.assertEqual(
            VideoAnalyzer._bounded_crop_region(
                1920,
                1080,
                {"x": 1900, "y": 1000, "w": 300, "h": 300},
            ),
            (1900, 1000, 20, 80),
        )

    def test_parse_fractional_frame_rate(self):
        self.assertAlmostEqual(VideoAnalyzer._parse_frame_rate("30000/1001"), 29.97003, places=4)

    def test_probe_video_stream_prefers_ffprobe_metadata(self):
        analyzer = VideoAnalyzer({"FFMPEG_BIN": "/opt/ffmpeg/bin/ffmpeg"})
        completed = types.SimpleNamespace(
            returncode=0,
            stdout='{"streams":[{"avg_frame_rate":"25/1","nb_frames":"250","width":1920,"height":1080}]}',
            stderr="",
        )
        with mock.patch.object(VIDEO_ANALYZER.subprocess, "run", return_value=completed) as run:
            info = analyzer._probe_video_stream("video.mp4")

        self.assertEqual(info, VideoStreamInfo(fps=25.0, total_frames=250, width=1920, height=1080))
        self.assertEqual(run.call_args.args[0][0], "/opt/ffmpeg/bin/ffprobe")

    def test_channel_roi_overrides_global_roi(self):
        analyzer = VideoAnalyzer({
            "ROI_REGION": {"x": 0, "y": 0, "w": 60, "h": 60},
            "VIDEO_CHANNEL_ROI_REGIONS": {
                "8": {"x": 10, "y": 12, "w": 30, "h": 24},
            },
        })

        self.assertEqual(analyzer._resolve_roi_region("8"), {"x": 10, "y": 12, "w": 30, "h": 24})
        self.assertEqual(analyzer._resolve_roi_region("9"), {"x": 0, "y": 0, "w": 60, "h": 60})

    def test_legacy_analysis_scale_adjusts_component_area(self):
        config = AnalyzerConfig.from_mapping({
            "LEGACY_ANALYSIS_MAX_WIDTH": 1280,
            "LEGACY_ANALYSIS_MAX_HEIGHT": 720,
            "FG_MIN_COMPONENT_AREA": 1600,
        })

        effective, scale = config.for_legacy_analysis_scale(2560, 1440)

        self.assertEqual(scale, 0.5)
        self.assertEqual(effective.fg_min_component_area, 400)

    def test_scan_fps_scales_frame_thresholds(self):
        config = make_config(
            EVENT_SCAN_FPS=5.0,
            STABLE_FRAMES_ENTER=10,
            STABLE_FRAMES_EXIT=6,
            STABLE_SAMPLE_INTERVAL=4,
            LEGACY_QUICK_STABLE_FRAMES_MIN=5,
        )

        effective, frame_step, effective_fps = config.for_effective_scan_fps(25.0)

        self.assertEqual(frame_step, 5)
        self.assertEqual(effective_fps, 5.0)
        self.assertEqual(effective.stable_frames_enter, 2)
        self.assertEqual(effective.stable_frames_exit, 1)
        self.assertEqual(effective.stable_sample_interval, 1)
        self.assertEqual(effective.legacy_quick_stable_frames_min, 1)

    def test_scan_sampler_keeps_non_integer_target_fps(self):
        config = make_config(
            EVENT_SCAN_FPS=15.0,
            STABLE_FRAMES_ENTER=10,
            STABLE_FRAMES_EXIT=6,
            STABLE_SAMPLE_INTERVAL=5,
            LEGACY_QUICK_STABLE_FRAMES_MIN=5,
        )

        effective, frame_step, effective_fps = config.for_effective_scan_fps(25.0)

        self.assertEqual(frame_step, 2)
        self.assertEqual(effective_fps, 15.0)
        self.assertEqual(effective.stable_frames_enter, 6)
        self.assertEqual(effective.stable_frames_exit, 4)
        self.assertEqual(effective.stable_sample_interval, 3)
        self.assertEqual(effective.legacy_quick_stable_frames_min, 3)

        close_source_effective, close_source_step, close_source_fps = config.for_effective_scan_fps(20.0)
        self.assertEqual(close_source_step, 1)
        self.assertEqual(close_source_fps, 15.0)
        self.assertEqual(close_source_effective.stable_frames_enter, 8)

        sampler = FrameSampler(25.0, 15.0)
        sampled_frame_numbers = []
        frame_no = 0
        while frame_no < 25:
            sampled_frame_numbers.append(frame_no)
            frame_no += sampler.skip_count_after(frame_no) + 1

        self.assertEqual(
            sampled_frame_numbers,
            [0, 2, 3, 5, 7, 8, 10, 12, 13, 15, 17, 18, 20, 22, 23],
        )


class FFmpegSampledReaderTests(unittest.TestCase):
    class FakeProcess:
        def __init__(self, payload: bytes, stderr: bytes = b"", return_code: int = 0):
            self.stdout = io.BytesIO(payload)
            self.stderr = io.BytesIO(stderr)
            self.return_code = return_code
            self.terminated = False

        def wait(self, timeout=None):
            return self.return_code

        def poll(self):
            return self.return_code if self.stdout.tell() == len(self.stdout.getvalue()) else None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

    def test_reads_exact_bgr_frames_and_builds_nvdec_filter(self):
        process = self.FakeProcess(bytes(range(24)))
        with mock.patch.object(VIDEO_ANALYZER.subprocess, "Popen", return_value=process) as popen:
            reader = FFmpegSampledReader(
                "video.mp4",
                ffmpeg_bin="ffmpeg",
                backend="nvdec",
                source_fps=25.0,
                target_fps=12.0,
                crop_region=(10, 20, 4, 2),
                output_size=(4, 2),
            )
            ok, frame = reader.read()
            ended, empty = reader.read()
            reader.close()

        self.assertTrue(ok)
        self.assertEqual(frame.shape, (2, 4, 3))
        self.assertEqual(frame[0, 0].tolist(), [0, 1, 2])
        self.assertEqual(frame[1, 3].tolist(), [21, 22, 23])
        self.assertFalse(ended)
        self.assertIsNone(empty)
        command = popen.call_args.args[0]
        self.assertIn("cuda", command)
        filter_text = command[command.index("-vf") + 1]
        self.assertIn("fps=fps=12.00000000", filter_text)
        self.assertIn("hwdownload,format=nv12,crop=4:2:10:20:exact=1,format=bgr24", filter_text)
        self.assertEqual(command[command.index("-pix_fmt") + 1], "bgr24")

    def test_cpu_filter_crops_and_scales_before_bgr_conversion(self):
        process = self.FakeProcess(bytes(range(24)))
        with mock.patch.object(VIDEO_ANALYZER.subprocess, "Popen", return_value=process) as popen:
            reader = FFmpegSampledReader(
                "video.mp4",
                ffmpeg_bin="ffmpeg",
                backend="ffmpeg_cpu",
                source_fps=25.0,
                target_fps=12.0,
                crop_region=(3, 5, 8, 4),
                output_size=(4, 2),
            )
            ok, frame = reader.read()
            reader.close()

        self.assertTrue(ok)
        self.assertEqual(frame.shape, (2, 4, 3))
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("-threads") + 1], "2")
        filter_text = command[command.index("-vf") + 1]
        self.assertEqual(
            filter_text,
            "fps=fps=12.00000000:round=near,"
            "crop=8:4:3:5:exact=1,scale=4:2:flags=area,format=bgr24",
        )

    def test_reader_seeks_window_without_resetting_timestamp_origin(self):
        process = self.FakeProcess(bytes(range(24)))
        with mock.patch.object(VIDEO_ANALYZER.subprocess, "Popen", return_value=process) as popen:
            reader = FFmpegSampledReader(
                "video.mp4",
                ffmpeg_bin="ffmpeg",
                backend="ffmpeg_cpu",
                source_fps=25.0,
                target_fps=12.0,
                crop_region=(0, 0, 4, 2),
                output_size=(4, 2),
                start_offset_seconds=16960.0,
                duration_seconds=7200.0,
            )
            reader.read()
            reader.close()

        command = popen.call_args.args[0]
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertEqual(command[command.index("-ss") + 1], "16960.000000")
        self.assertEqual(command[command.index("-t") + 1], "7200.000000")

    def test_reports_startup_failure_with_stderr(self):
        process = self.FakeProcess(b"", b"No device available\n", return_code=1)
        with mock.patch.object(VIDEO_ANALYZER.subprocess, "Popen", return_value=process):
            reader = FFmpegSampledReader(
                "video.mp4",
                ffmpeg_bin="ffmpeg",
                backend="nvdec",
                source_fps=25.0,
                target_fps=12.0,
                crop_region=(0, 0, 4, 2),
                output_size=(4, 2),
            )
            with self.assertRaisesRegex(FFmpegDecodeError, "No device available") as raised:
                reader.read()
            reader.close()

        self.assertEqual(raised.exception.frames_read, 0)


@unittest.skipUnless(hasattr(VIDEO_ANALYZER.cv2, "GaussianBlur"), "OpenCV not available")
class FrameScorerTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.scorer = FrameScorer(self.config)

    def _candidate_from_gray(self, gray: np.ndarray, frame_no: int) -> CandidateFrame:
        blur = VIDEO_ANALYZER.cv2.GaussianBlur(gray, (9, 9), 0)
        del blur  # keep OpenCV dependency explicit for the skip guard
        fg_mask = np.zeros_like(gray)
        fg_mask[10:50, 10:50] = 255
        return CandidateFrame(
            frame_no=frame_no,
            ts=frame_no / 10.0,
            frame=np.dstack([gray, gray, gray]),
            fg_mask=fg_mask,
            roi_gray=gray,
            motion_score=0.01,
            fg_ratio=0.20,
            changed_pixels=int(np.count_nonzero(fg_mask)),
            laplacian_score=float(VIDEO_ANALYZER.cv2.Laplacian(gray, VIDEO_ANALYZER.cv2.CV_64F).var()),
            tenengrad_score=VIDEO_ANALYZER._compute_tenengrad(gray),
            local_clarity_score=VIDEO_ANALYZER._compute_local_clarity_floor(gray),
            high_frequency_ratio=VIDEO_ANALYZER._compute_high_frequency_ratio(gray),
            completeness_raw=0.9,
            center_distance_ratio=0.1,
            edge_touch_ratio=0.0,
        )

    def test_prefers_sharper_candidate(self):
        base = textured_gray(11)
        sharp = self._candidate_from_gray(base, 1)
        blurred_gray = VIDEO_ANALYZER.cv2.GaussianBlur(base, (13, 13), 0)
        blurred = self._candidate_from_gray(blurred_gray, 2)

        selection = self.scorer.choose_best([blurred, sharp])

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertEqual(selection.best_candidate.frame_no, 1)
        self.assertFalse(selection.low_quality)

    def test_falls_back_when_all_candidates_fail_filters(self):
        dark = np.zeros((60, 60), dtype=np.uint8)
        c1 = self._candidate_from_gray(dark, 1)
        c2 = self._candidate_from_gray(dark, 2)
        c1.exposure_outlier_ratio = 1.0
        c2.exposure_outlier_ratio = 1.0
        c1.temporal_diff_score = 50.0
        c2.temporal_diff_score = 50.0

        selection = self.scorer.choose_best([c1, c2])

        self.assertIsNotNone(selection)
        assert selection is not None
        self.assertTrue(selection.low_quality)
        self.assertEqual(selection.quality_note, "fallback_laplacian_only")


if __name__ == "__main__":
    unittest.main()
