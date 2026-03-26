import os
import sys
import types
import unittest
import importlib.util

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

AnalyzerConfig = VIDEO_ANALYZER.AnalyzerConfig
CandidateFrame = VIDEO_ANALYZER.CandidateFrame
EventStateMachine = VIDEO_ANALYZER.EventStateMachine
ForegroundAnalysis = VIDEO_ANALYZER.ForegroundAnalysis
FrameScorer = VIDEO_ANALYZER.FrameScorer
MotionMeasure = VIDEO_ANALYZER.MotionMeasure
VideoAnalyzer = VIDEO_ANALYZER.VideoAnalyzer


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


class VideoAnalyzerTimeTests(unittest.TestCase):
    def test_naive_video_time_uses_configured_timezone(self):
        analyzer = VideoAnalyzer({"VIDEO_TIMEZONE": "Asia/Shanghai"})
        normalized = analyzer._normalize_video_start_time(VIDEO_ANALYZER.datetime(2026, 3, 26, 12, 30, 0))

        self.assertEqual(str(normalized.tzinfo), "Asia/Shanghai")
        self.assertEqual(normalized.hour, 12)


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
