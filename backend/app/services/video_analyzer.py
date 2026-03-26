import collections
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalyzerConfig:
    analysis_method: str
    roi_region: Optional[dict]
    roi_polygon: Optional[list[list[int]]]
    analysis_timezone: str
    motion_pixel_delta_threshold: int
    motion_ratio_threshold: float
    stable_frames_enter: int
    stable_frames_exit: int
    bg_history: int
    bg_var_threshold: float
    bg_detect_shadows: bool
    bg_warmup_frames: int
    bg_empty_learning_rate: float
    fg_ratio_threshold: float
    fg_min_component_area: int
    plate_min_area_ratio: float
    plate_max_area_ratio: float
    plate_center_max_ratio: float
    plate_edge_touch_max_ratio: float
    quick_stable_frames_min: int
    stable_present_frames_min: int
    stable_sample_interval: int
    blur_kernel_size: int
    morph_open_kernel: int
    morph_close_kernel: int
    score_clarity_weight: float
    score_completeness_weight: float
    event_record_filename: str
    tray_orange_ratio_threshold: float
    tray_center_margin: float
    tray_motion_threshold: int
    tray_window_size: int
    tray_min_laplacian: float
    tray_roi_expand: int
    tray_leave_motion_threshold: int
    tray_leave_motion_frames: int
    tray_dedup_threshold: float

    @classmethod
    def from_mapping(cls, config: dict) -> "AnalyzerConfig":
        return cls(
            analysis_method=str(
                config.get(
                    "VIDEO_ANALYSIS_METHOD",
                    config.get("ANALYSIS_METHOD", "legacy"),
                )
            ).strip().lower(),
            roi_region=config.get("ROI_REGION"),
            roi_polygon=config.get("ROI_POLYGON"),
            analysis_timezone=str(
                config.get(
                    "VIDEO_TIMEZONE",
                    config.get("APP_TIMEZONE", "Asia/Shanghai"),
                )
            ),
            motion_pixel_delta_threshold=int(
                config.get(
                    "MOTION_PIXEL_DELTA_THRESHOLD",
                    config.get("DIFF_THRESHOLD", 25),
                )
            ),
            motion_ratio_threshold=float(config.get("MOTION_RATIO_THRESHOLD", 0.015)),
            stable_frames_enter=int(config.get("STABLE_FRAMES_ENTER", 8)),
            stable_frames_exit=int(config.get("STABLE_FRAMES_EXIT", 5)),
            bg_history=int(config.get("BG_HISTORY", 500)),
            bg_var_threshold=float(config.get("BG_VAR_THRESHOLD", 16.0)),
            bg_detect_shadows=bool(config.get("BG_DETECT_SHADOWS", False)),
            bg_warmup_frames=int(config.get("BG_WARMUP_FRAMES", 500)),
            bg_empty_learning_rate=float(config.get("BG_EMPTY_LEARNING_RATE", 0.002)),
            fg_ratio_threshold=float(
                config.get(
                    "FG_RATIO_THRESHOLD",
                    config.get("OBJECT_ENTER_RATIO", 0.15),
                )
            ),
            fg_min_component_area=int(config.get("FG_MIN_COMPONENT_AREA", 1500)),
            plate_min_area_ratio=float(config.get("PLATE_MIN_AREA_RATIO", 0.12)),
            plate_max_area_ratio=float(config.get("PLATE_MAX_AREA_RATIO", 0.85)),
            plate_center_max_ratio=float(config.get("PLATE_CENTER_MAX_RATIO", 0.95)),
            plate_edge_touch_max_ratio=float(config.get("PLATE_EDGE_TOUCH_MAX_RATIO", 0.25)),
            quick_stable_frames_min=int(config.get("QUICK_STABLE_FRAMES_MIN", 2)),
            stable_present_frames_min=int(config.get("STABLE_PRESENT_FRAMES_MIN", 1)),
            stable_sample_interval=int(config.get("STABLE_SAMPLE_INTERVAL", 3)),
            blur_kernel_size=int(config.get("BLUR_KERNEL_SIZE", 5)),
            morph_open_kernel=int(config.get("MORPH_OPEN_KERNEL", 3)),
            morph_close_kernel=int(config.get("MORPH_CLOSE_KERNEL", 7)),
            score_clarity_weight=float(config.get("SCORE_CLARITY_WEIGHT", 0.6)),
            score_completeness_weight=float(config.get("SCORE_COMPLETENESS_WEIGHT", 0.4)),
            event_record_filename=str(config.get("EVENT_RECORD_FILENAME", "event_records.jsonl")),
            tray_orange_ratio_threshold=float(
                config.get(
                    "TRAY_ORANGE_RATIO_THRESHOLD",
                    config.get("ORANGE_RATIO_THRESHOLD", 0.05),
                )
            ),
            tray_center_margin=float(config.get("TRAY_CENTER_MARGIN", 0.15)),
            tray_motion_threshold=int(config.get("TRAY_MOTION_THRESHOLD", 500)),
            tray_window_size=int(config.get("TRAY_WINDOW_SIZE", 20)),
            tray_min_laplacian=float(config.get("TRAY_MIN_LAPLACIAN", 50.0)),
            tray_roi_expand=int(config.get("TRAY_ROI_EXPAND", 0)),
            tray_leave_motion_threshold=int(config.get("TRAY_LEAVE_MOTION_THRESHOLD", 1500)),
            tray_leave_motion_frames=int(config.get("TRAY_LEAVE_MOTION_FRAMES", 6)),
            tray_dedup_threshold=float(config.get("TRAY_DEDUP_THRESHOLD", 0.75)),
        )


@dataclass
class MotionMeasure:
    motion_score: float
    moving: bool
    changed_pixels: int
    gray: np.ndarray


@dataclass
class ForegroundAnalysis:
    fg_mask: np.ndarray
    fg_ratio: float
    fg_pixels: int
    present: bool
    largest_bbox: Optional[tuple[int, int, int, int]]
    largest_area: int
    largest_area_ratio: float
    center_distance_ratio: float
    edge_touch_ratio: float


@dataclass
class ScanFrame:
    frame_no: int
    ts: float
    motion_score: float
    fg_ratio: float
    tray_present: bool
    tray_score: float
    plate_present: bool
    plate_changed_pixels: int
    object_ratio: float
    state: str
    sampled: bool
    stable_frame_streak: int
    moving_frame_streak: int


@dataclass
class EventWindow:
    core_start_frame_no: int
    core_end_frame_no: int
    start_frame_no: int
    end_frame_no: int
    preferred_frame_no: int
    peak_frame_no: int
    peak_motion_score: float
    candidate_count: int
    best_score: float
    low_quality: bool
    quality_note: str


@dataclass
class CandidateFrame:
    frame_no: int
    ts: float
    frame: np.ndarray
    fg_mask: np.ndarray
    roi_gray: np.ndarray
    motion_score: float
    fg_ratio: float
    changed_pixels: int
    laplacian_score: float
    tenengrad_score: float
    local_clarity_score: float
    high_frequency_ratio: float
    completeness_raw: float
    center_distance_ratio: float
    edge_touch_ratio: float
    temporal_diff_score: float = 0.0
    exposure_outlier_ratio: float = 0.0
    score: float = 0.0
    clarity_score: float = 0.0
    clarity_norm: float = 0.0
    completeness_norm: float = 0.0


@dataclass
class ClosedEvent:
    window: EventWindow
    best_candidate: CandidateFrame


@dataclass
class SelectionResult:
    best_candidate: CandidateFrame
    low_quality: bool
    quality_note: str
    filtered_candidate_count: int


class MotionDetector:
    def __init__(self, config: AnalyzerConfig):
        self.pixel_delta_threshold = config.motion_pixel_delta_threshold
        self.motion_ratio_threshold = config.motion_ratio_threshold
        self.blur_kernel_size = _ensure_odd(config.blur_kernel_size)
        self.prev_gray: Optional[np.ndarray] = None

    def analyze(self, roi_frame: np.ndarray) -> MotionMeasure:
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (self.blur_kernel_size, self.blur_kernel_size), 0)

        if self.prev_gray is None:
            self.prev_gray = blurred
            return MotionMeasure(
                motion_score=0.0,
                moving=False,
                changed_pixels=0,
                gray=gray,
            )

        diff = cv2.absdiff(self.prev_gray, blurred)
        changed_mask = diff >= self.pixel_delta_threshold
        changed_pixels = int(np.count_nonzero(changed_mask))
        motion_score = changed_pixels / float(max(1, diff.size))

        self.prev_gray = blurred
        return MotionMeasure(
            motion_score=motion_score,
            moving=motion_score >= self.motion_ratio_threshold,
            changed_pixels=changed_pixels,
            gray=gray,
        )


class BackgroundModel:
    def __init__(self, config: AnalyzerConfig):
        self.config = config
        self.mog2 = cv2.createBackgroundSubtractorMOG2(
            history=config.bg_history,
            varThreshold=config.bg_var_threshold,
            detectShadows=config.bg_detect_shadows,
        )
        self.open_kernel = np.ones(
            (_ensure_odd(config.morph_open_kernel), _ensure_odd(config.morph_open_kernel)),
            dtype=np.uint8,
        )
        self.close_kernel = np.ones(
            (_ensure_odd(config.morph_close_kernel), _ensure_odd(config.morph_close_kernel)),
            dtype=np.uint8,
        )
        self.frames_seen = 0

    def analyze(self, roi_frame: np.ndarray, mode: str) -> ForegroundAnalysis:
        learning_rate = self._learning_rate_for_mode(mode)
        raw_mask = self.mog2.apply(roi_frame, learningRate=learning_rate)
        self.frames_seen += 1

        _, binary = cv2.threshold(raw_mask, 127, 255, cv2.THRESH_BINARY)
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, self.open_kernel)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, self.close_kernel)
        filtered_mask, stats = self._filter_components(cleaned)
        fg_pixels = int(cv2.countNonZero(filtered_mask))
        fg_ratio = fg_pixels / float(max(1, filtered_mask.size))
        largest_bbox = stats["largest_bbox"]
        largest_area = stats["largest_area"]
        largest_area_ratio = largest_area / float(max(1, filtered_mask.size))
        center_distance_ratio = stats["center_distance_ratio"]
        edge_touch_ratio = stats["edge_touch_ratio"]
        present = fg_ratio >= self.config.fg_ratio_threshold and largest_area >= self.config.fg_min_component_area

        return ForegroundAnalysis(
            fg_mask=filtered_mask,
            fg_ratio=fg_ratio,
            fg_pixels=fg_pixels,
            present=present,
            largest_bbox=largest_bbox,
            largest_area=largest_area,
            largest_area_ratio=largest_area_ratio,
            center_distance_ratio=center_distance_ratio,
            edge_touch_ratio=edge_touch_ratio,
        )

    def refresh_empty_scene(self, roi_frame: np.ndarray) -> None:
        self.mog2.apply(roi_frame, learningRate=self.config.bg_empty_learning_rate)
        self.frames_seen += 1

    def _learning_rate_for_mode(self, mode: str) -> float:
        if mode == "freeze":
            return 0.0
        if mode == "empty_refresh":
            return self.config.bg_empty_learning_rate
        return -1.0

    def _filter_components(self, mask: np.ndarray) -> tuple[np.ndarray, dict]:
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        filtered = np.zeros_like(mask)
        largest_bbox: Optional[tuple[int, int, int, int]] = None
        largest_area = 0

        for label in range(1, num_labels):
            area = int(stats[label, cv2.CC_STAT_AREA])
            if area < self.config.fg_min_component_area:
                continue

            filtered[labels == label] = 255
            if area <= largest_area:
                continue

            largest_area = area
            largest_bbox = (
                int(stats[label, cv2.CC_STAT_LEFT]),
                int(stats[label, cv2.CC_STAT_TOP]),
                int(stats[label, cv2.CC_STAT_WIDTH]),
                int(stats[label, cv2.CC_STAT_HEIGHT]),
            )

        center_distance_ratio = 1.0
        edge_touch_ratio = 1.0
        if largest_bbox is not None:
            center_distance_ratio = _bbox_center_distance_ratio(mask.shape[:2], largest_bbox)
            edge_touch_ratio = _bbox_edge_touch_ratio(mask.shape[:2], largest_bbox)

        return filtered, {
            "largest_bbox": largest_bbox,
            "largest_area": largest_area,
            "center_distance_ratio": center_distance_ratio,
            "edge_touch_ratio": edge_touch_ratio,
        }


class FrameScorer:
    def __init__(self, config: AnalyzerConfig):
        self.clarity_weight = config.score_clarity_weight
        self.completeness_weight = config.score_completeness_weight

    def choose_best(self, candidates: list[CandidateFrame]) -> Optional[SelectionResult]:
        if not candidates:
            return None

        self._compute_temporal_and_exposure_metrics(candidates)
        filtered = self._hard_filter_candidates(candidates)
        low_quality = False
        quality_note = ""

        ranked_pool = filtered
        if not ranked_pool:
            ranked_pool = self._hard_filter_candidates(
                candidates,
                motion_multiplier=2.5,
                exposure_threshold=0.08,
            )
            if ranked_pool:
                low_quality = True
                quality_note = "relaxed_filters"

        if not ranked_pool:
            fallback = max(candidates, key=lambda item: item.laplacian_score)
            fallback.clarity_score = 1.0
            fallback.clarity_norm = 1.0
            fallback.completeness_norm = 1.0
            fallback.score = 1.0
            return SelectionResult(
                best_candidate=fallback,
                low_quality=True,
                quality_note="fallback_laplacian_only",
                filtered_candidate_count=0,
            )

        ranked = self._score_candidates(ranked_pool)
        best = ranked[0]
        return SelectionResult(
            best_candidate=best,
            low_quality=low_quality,
            quality_note=quality_note,
            filtered_candidate_count=len(ranked_pool),
        )

    def _score_candidates(self, candidates: list[CandidateFrame]) -> list[CandidateFrame]:
        laplacian_values = [np.log1p(max(0.0, candidate.laplacian_score)) for candidate in candidates]
        tenengrad_values = [np.log1p(max(0.0, candidate.tenengrad_score)) for candidate in candidates]
        local_values = [np.log1p(max(0.0, candidate.local_clarity_score)) for candidate in candidates]
        frequency_values = [candidate.high_frequency_ratio for candidate in candidates]
        completeness_values = [candidate.completeness_raw for candidate in candidates]

        laplacian_norm = _normalize_scores(laplacian_values)
        tenengrad_norm = _normalize_scores(tenengrad_values)
        local_norm = _normalize_scores(local_values)
        frequency_norm = _normalize_scores(frequency_values)
        completeness_norm = _normalize_scores(completeness_values)

        ranked = []
        for idx, candidate in enumerate(candidates):
            candidate.clarity_score = (
                0.35 * laplacian_norm[idx]
                + 0.35 * tenengrad_norm[idx]
                + 0.20 * local_norm[idx]
                + 0.10 * frequency_norm[idx]
            )
            candidate.clarity_norm = candidate.clarity_score
            candidate.completeness_norm = completeness_norm[idx]
            candidate.score = (
                self.clarity_weight * candidate.clarity_score
                + self.completeness_weight * candidate.completeness_norm
            )
            ranked.append(candidate)

        ranked.sort(
            key=lambda item: (
                item.score,
                item.clarity_score,
                -item.edge_touch_ratio,
                -item.center_distance_ratio,
                -item.frame_no,
            ),
            reverse=True,
        )
        return ranked

    def _compute_temporal_and_exposure_metrics(self, candidates: list[CandidateFrame]) -> None:
        for idx, candidate in enumerate(candidates):
            neighbor_diffs = []
            if idx > 0:
                neighbor_diffs.append(_mean_abs_diff(candidate.roi_gray, candidates[idx - 1].roi_gray))
            if idx + 1 < len(candidates):
                neighbor_diffs.append(_mean_abs_diff(candidate.roi_gray, candidates[idx + 1].roi_gray))
            candidate.temporal_diff_score = float(np.mean(neighbor_diffs)) if neighbor_diffs else 0.0
            exposure_mask = (candidate.roi_gray <= 5) | (candidate.roi_gray >= 250)
            candidate.exposure_outlier_ratio = float(np.count_nonzero(exposure_mask)) / float(
                max(1, candidate.roi_gray.size)
            )

    def _hard_filter_candidates(
        self,
        candidates: list[CandidateFrame],
        motion_multiplier: float = 1.8,
        exposure_threshold: float = 0.05,
    ) -> list[CandidateFrame]:
        if not candidates:
            return []

        temporal_scores = np.array([candidate.temporal_diff_score for candidate in candidates], dtype=np.float64)
        baseline = float(np.median(temporal_scores))
        motion_threshold = max(baseline * motion_multiplier, baseline + 2.0)

        filtered = []
        for candidate in candidates:
            if candidate.temporal_diff_score > motion_threshold:
                continue
            if candidate.exposure_outlier_ratio > exposure_threshold:
                continue
            filtered.append(candidate)
        return filtered


class EventStateMachine:
    MOVING = "MOVING"
    STABLE = "STABLE"

    def __init__(self, config: AnalyzerConfig):
        self.config = config
        self.state = self.MOVING
        self.low_motion_streak = 0
        self.high_motion_streak = 0
        self.present_streak = 0
        self.stable_sample_counter = 0
        self.current_candidates: list[CandidateFrame] = []
        self.pre_stable_candidates: list[CandidateFrame] = []
        self.pre_stable_start_frame_no: Optional[int] = None
        self.current_event_start_frame_no: Optional[int] = None
        self.current_peak_motion_score = 0.0
        self.current_peak_frame_no = 0

    def current_bg_mode(self) -> str:
        return "update" if self.state == self.MOVING else "freeze"

    def process_frame(
        self,
        frame_no: int,
        ts: float,
        frame: np.ndarray,
        motion: MotionMeasure,
        foreground: ForegroundAnalysis,
        scorer: FrameScorer,
    ) -> tuple[ScanFrame, Optional[ClosedEvent]]:
        sampled = False
        completed_event: Optional[ClosedEvent] = None

        if self.state == self.MOVING:
            if motion.moving:
                completed_event = self._finalize_quick_event_if_ready(frame_no - 1, scorer)
                self.low_motion_streak = 0
                self.present_streak = 0
                self.pre_stable_candidates = []
                self.pre_stable_start_frame_no = None
                self.current_peak_motion_score = 0.0
                self.current_peak_frame_no = 0
                self._track_peak_motion(frame_no, motion.motion_score)
            else:
                self.low_motion_streak += 1
                sampled = self._collect_pre_stable_candidate(frame_no, ts, frame, motion, foreground)

            if self.low_motion_streak >= self.config.stable_frames_enter:
                self._enter_stable(frame_no)
                sampled = self._collect_candidate_if_needed(frame_no, ts, frame, motion, foreground)

        else:
            sampled = self._collect_candidate_if_needed(frame_no, ts, frame, motion, foreground)
            if motion.moving:
                self.high_motion_streak += 1
            else:
                self.high_motion_streak = 0

            if self.high_motion_streak >= self.config.stable_frames_exit:
                stable_end_frame = max(
                    self.current_event_start_frame_no or frame_no,
                    frame_no - self.config.stable_frames_exit,
                )
                completed_event = self._finalize_event(stable_end_frame, scorer)
                self._reset_after_finalize(frame_no, motion.motion_score)

        scan_frame = ScanFrame(
            frame_no=frame_no,
            ts=ts,
            motion_score=motion.motion_score,
            fg_ratio=foreground.fg_ratio,
            tray_present=foreground.present,
            tray_score=self._completeness_raw(foreground),
            plate_present=foreground.present,
            plate_changed_pixels=foreground.fg_pixels,
            object_ratio=foreground.fg_ratio,
            state=self.state,
            sampled=sampled,
            stable_frame_streak=self.low_motion_streak if self.state == self.MOVING else 0,
            moving_frame_streak=self.high_motion_streak if self.state == self.STABLE else 0,
        )
        return scan_frame, completed_event

    def flush(self, frame_no: int, scorer: FrameScorer) -> Optional[ClosedEvent]:
        if self.state == self.STABLE:
            completed_event = self._finalize_event(frame_no, scorer)
            self._reset_after_finalize(frame_no, 0.0)
            return completed_event
        return self._finalize_quick_event_if_ready(frame_no, scorer)

    def _enter_stable(self, frame_no: int) -> None:
        self.state = self.STABLE
        self.high_motion_streak = 0
        self.present_streak = 0
        self.stable_sample_counter = 0
        self.current_candidates = list(self.pre_stable_candidates)
        self.current_event_start_frame_no = (
            self.pre_stable_start_frame_no
            if self.pre_stable_start_frame_no is not None
            else max(0, frame_no - self.config.stable_frames_enter + 1)
        )
        self.pre_stable_candidates = []
        self.pre_stable_start_frame_no = None
        if self.current_peak_frame_no == 0:
            self.current_peak_frame_no = frame_no

    def _collect_candidate_if_needed(
        self,
        frame_no: int,
        ts: float,
        frame: np.ndarray,
        motion: MotionMeasure,
        foreground: ForegroundAnalysis,
    ) -> bool:
        if self.state != self.STABLE:
            return False

        if foreground.present:
            self.present_streak += 1
        else:
            self.present_streak = 0

        should_sample = (self.stable_sample_counter % self.config.stable_sample_interval) == 0
        self.stable_sample_counter += 1
        if (
            not should_sample
            or not foreground.present
            or self.present_streak < self.config.stable_present_frames_min
        ):
            return False

        self.current_candidates.append(
            self._make_candidate(frame_no, ts, frame, motion, foreground)
        )
        return True

    def _collect_pre_stable_candidate(
        self,
        frame_no: int,
        ts: float,
        frame: np.ndarray,
        motion: MotionMeasure,
        foreground: ForegroundAnalysis,
    ) -> bool:
        if not foreground.present:
            self.present_streak = 0
            self.pre_stable_candidates = []
            self.pre_stable_start_frame_no = None
            return False

        self.present_streak += 1
        if self.pre_stable_start_frame_no is None:
            self.pre_stable_start_frame_no = frame_no

        should_sample = (self.present_streak == 1) or ((self.present_streak - 1) % self.config.stable_sample_interval == 0)
        if not should_sample:
            return False

        self.pre_stable_candidates.append(
            self._make_candidate(frame_no, ts, frame, motion, foreground)
        )
        return True

    def _finalize_event(self, stable_end_frame: int, scorer: FrameScorer) -> Optional[ClosedEvent]:
        if not self.current_candidates or self.current_event_start_frame_no is None:
            return None

        selection = scorer.choose_best(self.current_candidates)
        if selection is None:
            return None
        best_candidate = selection.best_candidate

        peak_motion_score = max(self.current_peak_motion_score, best_candidate.motion_score)
        peak_frame_no = (
            self.current_peak_frame_no
            if self.current_peak_motion_score >= best_candidate.motion_score
            else best_candidate.frame_no
        )
        window = EventWindow(
            core_start_frame_no=self.current_event_start_frame_no,
            core_end_frame_no=stable_end_frame,
            start_frame_no=self.current_event_start_frame_no,
            end_frame_no=stable_end_frame,
            preferred_frame_no=best_candidate.frame_no,
            peak_frame_no=peak_frame_no,
            peak_motion_score=peak_motion_score,
            candidate_count=len(self.current_candidates),
            best_score=best_candidate.score,
            low_quality=selection.low_quality,
            quality_note=selection.quality_note,
        )
        return ClosedEvent(window=window, best_candidate=best_candidate)

    def _reset_after_finalize(self, frame_no: int, motion_score: float) -> None:
        self.state = self.MOVING
        self.low_motion_streak = 0
        self.high_motion_streak = 0
        self.present_streak = 0
        self.stable_sample_counter = 0
        self.current_candidates = []
        self.pre_stable_candidates = []
        self.pre_stable_start_frame_no = None
        self.current_event_start_frame_no = None
        self.current_peak_motion_score = motion_score
        self.current_peak_frame_no = frame_no if motion_score > 0 else 0

    def _finalize_quick_event_if_ready(self, end_frame_no: int, scorer: FrameScorer) -> Optional[ClosedEvent]:
        if self.state != self.MOVING:
            return None
        if self.low_motion_streak < self.config.quick_stable_frames_min:
            return None
        if not self.pre_stable_candidates or self.pre_stable_start_frame_no is None:
            return None

        selection = scorer.choose_best(self.pre_stable_candidates)
        if selection is None:
            return None

        best_candidate = selection.best_candidate
        peak_motion_score = max(self.current_peak_motion_score, best_candidate.motion_score)
        peak_frame_no = (
            self.current_peak_frame_no
            if self.current_peak_motion_score >= best_candidate.motion_score
            else best_candidate.frame_no
        )
        quality_note = (
            "quick_stable_fallback"
            if not selection.quality_note
            else f"quick_stable_fallback+{selection.quality_note}"
        )
        window = EventWindow(
            core_start_frame_no=self.pre_stable_start_frame_no,
            core_end_frame_no=end_frame_no,
            start_frame_no=self.pre_stable_start_frame_no,
            end_frame_no=end_frame_no,
            preferred_frame_no=best_candidate.frame_no,
            peak_frame_no=peak_frame_no,
            peak_motion_score=peak_motion_score,
            candidate_count=len(self.pre_stable_candidates),
            best_score=best_candidate.score,
            low_quality=True,
            quality_note=quality_note,
        )
        return ClosedEvent(window=window, best_candidate=best_candidate)

    def _track_peak_motion(self, frame_no: int, motion_score: float) -> None:
        if motion_score < self.current_peak_motion_score:
            return
        self.current_peak_motion_score = motion_score
        self.current_peak_frame_no = frame_no

    def _make_candidate(
        self,
        frame_no: int,
        ts: float,
        frame: np.ndarray,
        motion: MotionMeasure,
        foreground: ForegroundAnalysis,
    ) -> CandidateFrame:
        laplacian_score = _laplacian_variance(motion.gray)
        tenengrad_score = _compute_tenengrad(motion.gray)
        local_clarity_score = _compute_local_clarity_floor(motion.gray)
        high_frequency_ratio = _compute_high_frequency_ratio(motion.gray)
        return CandidateFrame(
            frame_no=frame_no,
            ts=ts,
            frame=frame.copy(),
            fg_mask=foreground.fg_mask.copy(),
            roi_gray=motion.gray.copy(),
            motion_score=motion.motion_score,
            fg_ratio=foreground.fg_ratio,
            changed_pixels=foreground.fg_pixels,
            laplacian_score=laplacian_score,
            tenengrad_score=tenengrad_score,
            local_clarity_score=local_clarity_score,
            high_frequency_ratio=high_frequency_ratio,
            completeness_raw=self._completeness_raw(foreground),
            center_distance_ratio=foreground.center_distance_ratio,
            edge_touch_ratio=foreground.edge_touch_ratio,
        )

    def _completeness_raw(self, foreground: ForegroundAnalysis) -> float:
        area_score = min(1.0, foreground.largest_area_ratio / max(self.config.fg_ratio_threshold, 1e-6))
        center_score = 1.0 - min(1.0, foreground.center_distance_ratio)
        containment_score = 1.0 - min(1.0, foreground.edge_touch_ratio * 2.0)
        return (area_score * 0.25) + (center_score * 0.35) + (containment_score * 0.40)


class ResultWriter:
    def __init__(self, output_dir: str, channel_id: str, video_start_time: datetime, writer_filename: str):
        self.output_dir = output_dir
        self.channel_id = channel_id
        self.video_start_time = video_start_time
        self.event_record_path = os.path.join(output_dir, writer_filename)
        os.makedirs(output_dir, exist_ok=True)

    def write(self, event: ClosedEvent, video_fps: float) -> dict:
        best = event.best_candidate
        seconds_offset = best.frame_no / video_fps if video_fps > 0 else 0.0
        captured_at = self.video_start_time + timedelta(seconds=seconds_offset)
        frame_filename = self._make_frame_filename(captured_at)
        frame_path = os.path.join(self.output_dir, frame_filename)
        cv2.imwrite(frame_path, best.frame, [cv2.IMWRITE_JPEG_QUALITY, 92])

        record = {
            "timestamp": captured_at.isoformat(),
            "image_path": frame_path,
            "candidate_frame_count": event.window.candidate_count,
            "best_score": round(best.score, 6),
            "frame_no": best.frame_no,
            "peak_frame_no": event.window.peak_frame_no,
            "diff_score": round(event.window.peak_motion_score, 6),
            "low_quality": event.window.low_quality,
            "quality_note": event.window.quality_note,
        }
        with open(self.event_record_path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

        return {
            "image_path": frame_path,
            "captured_at": captured_at,
            "diff_score": event.window.peak_motion_score,
            "channel_id": self.channel_id,
            "is_candidate": False,
            "frame_no": best.frame_no,
            "peak_frame_no": event.window.peak_frame_no,
            "plate_pixels": best.changed_pixels,
            "motion_score": best.motion_score,
            "tray_area_ratio": best.fg_ratio,
            "focus_score": best.laplacian_score,
            "outside_fg_ratio": best.edge_touch_ratio,
            "candidate_frame_count": event.window.candidate_count,
            "best_score": best.score,
            "low_quality": event.window.low_quality,
            "quality_note": event.window.quality_note,
        }

    def _make_frame_filename(self, captured_at: datetime) -> str:
        base_name = f"{self.channel_id}_{captured_at.strftime('%Y-%m-%d-%H-%M-%S')}"
        frame_path = os.path.join(self.output_dir, f"{base_name}.jpg")
        if not os.path.exists(frame_path):
            return f"{base_name}.jpg"
        return f"{base_name}-{captured_at.microsecond // 1000:03d}.jpg"


class TrayFrameSelector:
    """Closer port of frame.py to preserve its detection behavior."""

    IDLE = "IDLE"
    LOCKING = "LOCKING"
    LOCKED = "LOCKED"
    DONE = "DONE"

    ORANGE_LOWER = np.array([5, 80, 80], dtype=np.uint8)
    ORANGE_UPPER = np.array([25, 255, 255], dtype=np.uint8)
    SKIN_LOWER = np.array([0, 30, 60], dtype=np.uint8)
    SKIN_UPPER = np.array([25, 180, 255], dtype=np.uint8)

    def __init__(self, config: AnalyzerConfig, roi_region: Optional[dict], roi_polygon: Optional[list[list[int]]] = None):
        self.config = config
        self._rect_roi = _region_to_rect(roi_region)
        self._polygon = _polygon_to_array(roi_polygon)
        self._poly_mask: Optional[np.ndarray] = None
        self.state = self.IDLE
        self.stable_count = 0
        self.leave_motion_count = 0
        self.prev_gray_roi: Optional[np.ndarray] = None
        self.frame_buffer: collections.deque[tuple[int, float, np.ndarray, float]] = collections.deque(
            maxlen=max(1, config.tray_window_size)
        )
        self.current_start_frame_no: Optional[int] = None
        self.current_peak_motion_score = 0.0
        self.current_peak_frame_no = 0
        self.last_locked_roi_hsv: Optional[np.ndarray] = None

    def process_frame(self, frame_no: int, ts: float, frame: np.ndarray) -> tuple[ScanFrame, Optional[ClosedEvent]]:
        if self._rect_roi is None:
            self._init_roi(frame)

        laplacian_score = self._laplacian_score(frame)
        self.frame_buffer.append((frame_no, ts, frame.copy(), laplacian_score))

        roi_color = self._apply_roi(frame)
        if roi_color.size == 0:
            return self._make_scan_frame(frame_no, ts, 0.0, 0.0, 0, False, True), None

        orange_mask, orange_ratio, orange_pixels = self._orange_stats_from_roi(roi_color)
        roi_gray = cv2.cvtColor(roi_color, cv2.COLOR_BGR2GRAY)
        roi_gray = cv2.GaussianBlur(
            roi_gray,
            (max(1, _ensure_odd(self.config.blur_kernel_size)), max(1, _ensure_odd(self.config.blur_kernel_size))),
            0,
        )
        tray_present = self._detect_tray(orange_mask, orange_ratio)
        motion_pixels = self._motion_diff(roi_gray, color_roi=roi_color)
        motion_score = motion_pixels / float(max(1, roi_gray.size))
        is_still = motion_pixels < self.config.tray_motion_threshold
        sampled = True
        closed_event: Optional[ClosedEvent] = None

        if self.state == self.IDLE:
            if tray_present:
                self.state = self.LOCKING
                self.stable_count = 0
                self.frame_buffer.clear()
                self.current_start_frame_no = frame_no
                self.current_peak_motion_score = motion_score
                self.current_peak_frame_no = frame_no

        elif self.state == self.LOCKING:
            if not tray_present:
                self._reset()
            elif is_still:
                self.stable_count += 1
                if self.current_start_frame_no is None:
                    self.current_start_frame_no = frame_no
                self._track_peak_motion(frame_no, motion_score)
                if self.stable_count >= self.config.stable_frames_enter:
                    best_frame_no, best_ts, best_frame, best_score = self._best_frame()
                    if best_frame is not None and best_score >= self.config.tray_min_laplacian:
                        if self._is_same_tray(best_frame):
                            self.last_locked_roi_hsv = cv2.cvtColor(self._apply_roi(best_frame), cv2.COLOR_BGR2HSV)
                            self.state = self.DONE
                            self.leave_motion_count = 0
                        else:
                            self.state = self.LOCKED
                            self.last_locked_roi_hsv = cv2.cvtColor(self._apply_roi(best_frame), cv2.COLOR_BGR2HSV)
                            closed_event = self._build_closed_event(
                                frame_no=frame_no,
                                best_frame_no=best_frame_no,
                                best_ts=best_ts,
                                best_frame=best_frame,
                                best_score=best_score,
                                motion_score=motion_score,
                            )
            else:
                self.stable_count = 0
                self._track_peak_motion(frame_no, motion_score)

        elif self.state == self.LOCKED:
            self.state = self.DONE

        elif self.state == self.DONE:
            if not tray_present:
                self._reset()
            elif motion_pixels > self.config.tray_leave_motion_threshold:
                self.leave_motion_count += 1
                if self.leave_motion_count >= self.config.tray_leave_motion_frames:
                    self._reset()
            else:
                self.leave_motion_count = 0

        scan_frame = self._make_scan_frame(
            frame_no=frame_no,
            ts=ts,
            motion_score=motion_score,
            orange_ratio=orange_ratio,
            orange_pixels=orange_pixels,
            tray_present=tray_present,
            sampled=sampled,
        )
        return scan_frame, closed_event

    def _init_roi(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        expand = self.config.tray_roi_expand
        if self._polygon is not None:
            x, y, roi_w, roi_h = _poly_to_bbox(self._polygon)
            x = max(0, x - expand)
            y = max(0, y - expand)
            roi_w = min(width - x, roi_w + (expand * 2))
            roi_h = min(height - y, roi_h + (expand * 2))
            self._rect_roi = (x, y, roi_w, roi_h)
            self._poly_mask = _make_poly_mask(self._polygon, frame.shape)
            return

        if self._rect_roi is not None:
            x, y, roi_w, roi_h = self._rect_roi
            if roi_w > 0 and roi_h > 0:
                x = max(0, x - expand)
                y = max(0, y - expand)
                roi_w = min(width - x, roi_w + (expand * 2))
                roi_h = min(height - y, roi_h + (expand * 2))
                self._rect_roi = (x, y, roi_w, roi_h)
                return

        margin_x = int(width * 0.10)
        margin_y = int(height * 0.10)
        self._rect_roi = (
            margin_x,
            margin_y,
            max(1, width - (margin_x * 2)),
            max(1, height - (margin_y * 2)),
        )

    def _apply_roi(self, frame: np.ndarray) -> np.ndarray:
        if self._rect_roi is None:
            raise RuntimeError("ROI not initialized")
        x, y, roi_w, roi_h = self._rect_roi
        crop = frame[y:y + roi_h, x:x + roi_w].copy()
        if self._poly_mask is not None:
            mask_crop = self._poly_mask[y:y + roi_h, x:x + roi_w]
            crop[mask_crop == 0] = 0
        return crop

    def _orange_stats_from_roi(self, roi_frame: np.ndarray) -> tuple[np.ndarray, float, int]:
        hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
        orange_mask = cv2.inRange(hsv, self.ORANGE_LOWER, self.ORANGE_UPPER)
        orange_pixels = int(np.count_nonzero(orange_mask))
        total_pixels = int(np.count_nonzero(roi_frame.any(axis=2))) if self._poly_mask is not None else orange_mask.size
        orange_ratio = orange_pixels / float(max(1, total_pixels))
        return orange_mask, orange_ratio, orange_pixels

    def _detect_tray(self, orange_mask: np.ndarray, orange_ratio: float) -> bool:
        if orange_ratio < self.config.tray_orange_ratio_threshold:
            return False

        moments = cv2.moments(orange_mask)
        if moments["m00"] == 0:
            return False
        center_x = moments["m10"] / moments["m00"]
        center_y = moments["m01"] / moments["m00"]
        height, width = orange_mask.shape[:2]
        margin = self.config.tray_center_margin
        return (
            width * margin < center_x < width * (1.0 - margin)
            and height * margin < center_y < height * (1.0 - margin)
        )

    def _motion_diff(self, gray_roi: np.ndarray, color_roi: np.ndarray) -> int:
        if self.prev_gray_roi is None:
            self.prev_gray_roi = gray_roi.copy()
            return 0

        diff = cv2.absdiff(self.prev_gray_roi, gray_roi)
        _, thresh = cv2.threshold(diff, self.config.motion_pixel_delta_threshold, 255, cv2.THRESH_BINARY)
        skin_mask = self._make_skin_mask(color_roi)
        thresh[skin_mask > 0] = 0
        motion_pixels = int(np.count_nonzero(thresh))
        self.prev_gray_roi = gray_roi.copy()
        return motion_pixels

    def _make_skin_mask(self, color_roi: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(color_roi, cv2.COLOR_BGR2HSV)
        skin_mask = cv2.inRange(hsv, self.SKIN_LOWER, self.SKIN_UPPER)
        kernel = np.ones((7, 7), dtype=np.uint8)
        return cv2.dilate(skin_mask, kernel, iterations=1)

    def _laplacian_score(self, frame: np.ndarray) -> float:
        roi_frame = self._apply_roi(frame)
        gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _best_frame(self) -> tuple[int, float, Optional[np.ndarray], float]:
        if not self.frame_buffer:
            return 0, 0.0, None, 0.0
        frames = list(self.frame_buffer)
        tail = frames[-self.config.stable_frames_enter:]
        best_frame_no, best_ts, best_frame, best_score = max(tail, key=lambda item: item[3])
        return best_frame_no, best_ts, best_frame, float(best_score)

    def _food_mask(self, roi_bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
        orange_mask = cv2.inRange(hsv, self.ORANGE_LOWER, self.ORANGE_UPPER)
        dark_mask = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 255, 50]))
        return cv2.bitwise_not(cv2.bitwise_or(orange_mask, dark_mask))

    def _is_same_tray(self, frame: np.ndarray) -> bool:
        if self.last_locked_roi_hsv is None:
            return False

        current_roi = self._apply_roi(frame)
        current_hsv = cv2.cvtColor(current_roi, cv2.COLOR_BGR2HSV)
        current_food_mask = self._food_mask(current_roi)
        previous_food_mask = self._food_mask(cv2.cvtColor(self.last_locked_roi_hsv, cv2.COLOR_HSV2BGR))
        if np.count_nonzero(current_food_mask) < 500 or np.count_nonzero(previous_food_mask) < 500:
            return False

        current_hist = cv2.calcHist([current_hsv], [0, 1], current_food_mask, [32, 32], [0, 180, 0, 256])
        previous_hist = cv2.calcHist([self.last_locked_roi_hsv], [0, 1], previous_food_mask, [32, 32], [0, 180, 0, 256])
        cv2.normalize(current_hist, current_hist)
        cv2.normalize(previous_hist, previous_hist)
        score = cv2.compareHist(current_hist, previous_hist, cv2.HISTCMP_CORREL)
        return score > self.config.tray_dedup_threshold

    def _build_closed_event(
        self,
        frame_no: int,
        best_frame_no: int,
        best_ts: float,
        best_frame: np.ndarray,
        best_score: float,
        motion_score: float,
    ) -> ClosedEvent:
        best_roi = self._apply_roi(best_frame)
        orange_mask, orange_ratio, orange_pixels = self._orange_stats_from_roi(best_roi)
        roi_gray = cv2.cvtColor(best_roi, cv2.COLOR_BGR2GRAY)
        laplacian_score = _laplacian_variance(roi_gray)
        tenengrad_score = _compute_tenengrad(roi_gray)
        local_clarity_score = _compute_local_clarity_floor(roi_gray)
        high_frequency_ratio = _compute_high_frequency_ratio(roi_gray)
        coords = cv2.findNonZero(orange_mask)
        if coords is None:
            center_distance_ratio = 1.0
            edge_touch_ratio = 1.0
        else:
            bbox = cv2.boundingRect(coords)
            center_distance_ratio = _bbox_center_distance_ratio(orange_mask.shape[:2], bbox)
            edge_touch_ratio = _bbox_edge_touch_ratio(orange_mask.shape[:2], bbox)
        center_score = 1.0 - min(1.0, center_distance_ratio)
        completeness_raw = (orange_ratio * 0.6) + (center_score * 0.4)
        candidate = CandidateFrame(
            frame_no=best_frame_no,
            ts=best_ts,
            frame=best_frame.copy(),
            fg_mask=orange_mask.copy(),
            roi_gray=roi_gray.copy(),
            motion_score=motion_score,
            fg_ratio=orange_ratio,
            changed_pixels=orange_pixels,
            laplacian_score=laplacian_score,
            tenengrad_score=tenengrad_score,
            local_clarity_score=local_clarity_score,
            high_frequency_ratio=high_frequency_ratio,
            completeness_raw=completeness_raw,
            center_distance_ratio=center_distance_ratio,
            edge_touch_ratio=edge_touch_ratio,
            score=best_score,
            clarity_score=best_score,
            clarity_norm=1.0,
            completeness_norm=completeness_raw,
        )
        peak_motion_score = max(self.current_peak_motion_score, motion_score)
        peak_frame_no = self.current_peak_frame_no if self.current_peak_motion_score >= motion_score else best_frame_no
        start_frame_no = self.current_start_frame_no if self.current_start_frame_no is not None else best_frame_no
        window = EventWindow(
            core_start_frame_no=start_frame_no,
            core_end_frame_no=frame_no,
            start_frame_no=start_frame_no,
            end_frame_no=frame_no,
            preferred_frame_no=best_frame_no,
            peak_frame_no=peak_frame_no,
            peak_motion_score=peak_motion_score,
            candidate_count=len(self.frame_buffer),
            best_score=best_score,
            low_quality=False,
            quality_note="tray_selector",
        )
        return ClosedEvent(window=window, best_candidate=candidate)

    def _make_scan_frame(
        self,
        frame_no: int,
        ts: float,
        motion_score: float,
        orange_ratio: float,
        orange_pixels: int,
        tray_present: bool,
        sampled: bool,
    ) -> ScanFrame:
        return ScanFrame(
            frame_no=frame_no,
            ts=ts,
            motion_score=motion_score,
            fg_ratio=orange_ratio,
            tray_present=tray_present,
            tray_score=orange_ratio,
            plate_present=tray_present,
            plate_changed_pixels=orange_pixels,
            object_ratio=orange_ratio,
            state=self.state,
            sampled=sampled,
            stable_frame_streak=self.stable_count if self.state == self.LOCKING else 0,
            moving_frame_streak=self.leave_motion_count if self.state == self.DONE else 0,
        )

    def _track_peak_motion(self, frame_no: int, motion_score: float) -> None:
        if motion_score < self.current_peak_motion_score:
            return
        self.current_peak_motion_score = motion_score
        self.current_peak_frame_no = frame_no

    def _reset(self) -> None:
        self.state = self.IDLE
        self.stable_count = 0
        self.leave_motion_count = 0
        self.prev_gray_roi = None
        self.frame_buffer.clear()
        self.current_start_frame_no = None
        self.current_peak_motion_score = 0.0
        self.current_peak_frame_no = 0


class VideoAnalyzer:
    """Single-pass settlement frame extraction driven by ROI motion and background state."""

    def __init__(self, config: dict):
        self.config = AnalyzerConfig.from_mapping(config)
        self.roi_region = self.config.roi_region
        self.roi_polygon = self.config.roi_polygon
        self.analysis_timezone = ZoneInfo(self.config.analysis_timezone)
        self.auto_detect_settlement_roi = False
        self.last_scan_frames: list[ScanFrame] = []
        self.last_event_windows: list[EventWindow] = []
        self.object_ratio_baseline = 0.0
        self.object_pixels_baseline = 0.0

    def extract_frames(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
    ) -> list[dict]:
        if self.config.analysis_method == "legacy":
            return self._extract_frames_legacy(video_path, output_dir, video_start_time, channel_id)
        return self._extract_frames_tray_selector(video_path, output_dir, video_start_time, channel_id)

    def _extract_frames_legacy(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
    ) -> list[dict]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        max_frame_no = max(0, total_frames - 1)

        video_start_time = self._normalize_video_start_time(video_start_time)

        motion_detector = MotionDetector(self.config)
        background_model = BackgroundModel(self.config)
        scorer = FrameScorer(self.config)
        state_machine = EventStateMachine(self.config)
        writer = ResultWriter(output_dir, channel_id, video_start_time, self.config.event_record_filename)

        self.last_scan_frames = []
        self.last_event_windows = []
        results: list[dict] = []
        seen_seconds: set[int] = set()

        try:
            frame_no = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                roi_frame = self._apply_roi(frame)
                if roi_frame.size == 0:
                    frame_no += 1
                    continue

                ts = frame_no / video_fps if video_fps > 0 else 0.0
                motion = motion_detector.analyze(roi_frame)
                bg_mode = state_machine.current_bg_mode()
                foreground = background_model.analyze(roi_frame, mode=bg_mode)

                scan_frame, closed_event = state_machine.process_frame(
                    frame_no=frame_no,
                    ts=ts,
                    frame=frame,
                    motion=motion,
                    foreground=foreground,
                    scorer=scorer,
                )
                self.last_scan_frames.append(scan_frame)

                if bg_mode == "freeze" and not foreground.present:
                    background_model.refresh_empty_scene(roi_frame)

                if closed_event is not None:
                    self.last_event_windows.append(closed_event.window)
                    result = writer.write(closed_event, video_fps)
                    ts_key = int(ts)
                    result["is_candidate"] = ts_key in seen_seconds
                    seen_seconds.add(ts_key)
                    results.append(result)

                frame_no += 1

            final_event = state_machine.flush(max_frame_no, scorer)
            if final_event is not None:
                self.last_event_windows.append(final_event.window)
                result = writer.write(final_event, video_fps)
                ts_key = int(final_event.best_candidate.frame_no / video_fps) if video_fps > 0 else 0
                result["is_candidate"] = ts_key in seen_seconds
                seen_seconds.add(ts_key)
                results.append(result)
        finally:
            cap.release()

        self._update_baselines()
        logger.info("Extracted %s frames from %s", len(results), video_path)

        if not results:
            logger.warning("No settlement events detected in %s", video_path)
        return results

    def _extract_frames_tray_selector(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
    ) -> list[dict]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        video_start_time = self._normalize_video_start_time(video_start_time)
        selector = TrayFrameSelector(self.config, self.roi_region, self.roi_polygon)
        writer = ResultWriter(output_dir, channel_id, video_start_time, self.config.event_record_filename)

        self.last_scan_frames = []
        self.last_event_windows = []
        results: list[dict] = []
        seen_seconds: set[int] = set()

        try:
            frame_no = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                ts = frame_no / video_fps if video_fps > 0 else 0.0
                scan_frame, closed_event = selector.process_frame(frame_no, ts, frame)
                if selector._rect_roi is not None:
                    x, y, roi_w, roi_h = selector._rect_roi
                    self.roi_region = {"x": x, "y": y, "w": roi_w, "h": roi_h}
                if selector._polygon is not None:
                    self.roi_polygon = selector._polygon.astype(int).tolist()
                self.last_scan_frames.append(scan_frame)

                if closed_event is not None:
                    self.last_event_windows.append(closed_event.window)
                    result = writer.write(closed_event, video_fps)
                    ts_key = int(closed_event.best_candidate.frame_no / video_fps) if video_fps > 0 else 0
                    result["is_candidate"] = ts_key in seen_seconds
                    seen_seconds.add(ts_key)
                    results.append(result)

                frame_no += 1
        finally:
            cap.release()

        self._update_baselines()
        logger.info(
            "Extracted %s frames from %s using %s",
            len(results),
            video_path,
            self.config.analysis_method,
        )

        if not results:
            logger.warning("No settlement events detected in %s", video_path)
        return results

    def _update_baselines(self) -> None:
        if not self.last_scan_frames:
            self.object_ratio_baseline = 0.0
            self.object_pixels_baseline = 0.0
            return

        object_ratios = np.array([sample.object_ratio for sample in self.last_scan_frames], dtype=np.float64)
        pixel_counts = np.array([sample.plate_changed_pixels for sample in self.last_scan_frames], dtype=np.float64)
        self.object_ratio_baseline = float(np.percentile(object_ratios, 10))
        self.object_pixels_baseline = float(np.percentile(pixel_counts, 10))

    def _normalize_video_start_time(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.analysis_timezone)
        return value.astimezone(self.analysis_timezone)

    def _apply_roi(self, frame: np.ndarray) -> np.ndarray:
        roi_frame = _crop_frame_by_region(frame, self.roi_region)
        if roi_frame.size == 0:
            logger.warning("Invalid ROI_REGION %s; falling back to full frame", self.roi_region)
            return frame
        return roi_frame


def _crop_frame_by_region(frame: np.ndarray, roi_region: Optional[dict], expand: int = 0) -> np.ndarray:
    if not roi_region:
        return frame

    height, width = frame.shape[:2]
    x = max(0, min(int(roi_region.get("x", 0)), width))
    y = max(0, min(int(roi_region.get("y", 0)), height))
    roi_w = max(0, min(int(roi_region.get("w", width)), width - x))
    roi_h = max(0, min(int(roi_region.get("h", height)), height - y))
    if roi_w <= 0 or roi_h <= 0:
        return np.empty((0, 0, frame.shape[2]), dtype=frame.dtype)

    if expand > 0:
        x = max(0, x - expand)
        y = max(0, y - expand)
        roi_w = min(width - x, roi_w + (expand * 2))
        roi_h = min(height - y, roi_h + (expand * 2))
    return frame[y:y + roi_h, x:x + roi_w]


def _region_to_rect(roi_region: Optional[dict]) -> Optional[tuple[int, int, int, int]]:
    if not roi_region:
        return None
    return (
        int(roi_region.get("x", 0)),
        int(roi_region.get("y", 0)),
        int(roi_region.get("w", 0)),
        int(roi_region.get("h", 0)),
    )


def _polygon_to_array(roi_polygon: Optional[list[list[int]]]) -> Optional[np.ndarray]:
    if not roi_polygon:
        return None
    return np.array(roi_polygon, dtype=np.int32)


def _poly_to_bbox(poly: np.ndarray) -> tuple[int, int, int, int]:
    x, y, w, h = cv2.boundingRect(poly)
    return int(x), int(y), int(w), int(h)


def _make_poly_mask(poly: np.ndarray, frame_shape: tuple[int, ...]) -> np.ndarray:
    mask = np.zeros(frame_shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [poly], 255)
    return mask


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []

    values_array = np.array(values, dtype=np.float64)
    min_value = float(values_array.min())
    max_value = float(values_array.max())
    if abs(max_value - min_value) < 1e-9:
        return [1.0 for _ in values]
    return ((values_array - min_value) / (max_value - min_value)).tolist()


def _compute_tenengrad(gray: np.ndarray) -> float:
    if not hasattr(cv2, "Sobel"):
        grad_y, grad_x = np.gradient(gray.astype(np.float64))
        magnitude = np.hypot(grad_x, grad_y)
        return float(np.mean(magnitude))
    grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(grad_x, grad_y)
    return float(np.mean(magnitude))


def _compute_local_clarity_floor(gray: np.ndarray) -> float:
    height, width = gray.shape[:2]
    rows = np.array_split(np.arange(height), 3)
    cols = np.array_split(np.arange(width), 3)
    local_scores = []

    for row_idx in rows:
        for col_idx in cols:
            tile = gray[row_idx[0]:row_idx[-1] + 1, col_idx[0]:col_idx[-1] + 1]
            if tile.size == 0:
                continue
            local_scores.append(_laplacian_variance(tile))

    if not local_scores:
        return 0.0
    return min(local_scores)


def _compute_high_frequency_ratio(gray: np.ndarray) -> float:
    float_gray = gray.astype(np.float32)
    fft = np.fft.fft2(float_gray)
    shifted = np.fft.fftshift(fft)
    power = np.abs(shifted) ** 2
    total_energy = float(power.sum())
    if total_energy <= 0:
        return 0.0

    height, width = gray.shape[:2]
    cy, cx = height // 2, width // 2
    radius = max(1, int(min(height, width) * 0.12))
    y, x = np.ogrid[:height, :width]
    low_freq_mask = ((y - cy) ** 2 + (x - cx) ** 2) <= (radius * radius)
    high_freq_energy = float(power[~low_freq_mask].sum())
    return high_freq_energy / total_energy


def _mean_abs_diff(left: np.ndarray, right: np.ndarray) -> float:
    if hasattr(cv2, "absdiff"):
        diff = cv2.absdiff(left, right)
    else:
        diff = np.abs(left.astype(np.float32) - right.astype(np.float32))
    return float(np.mean(diff))


def _laplacian_variance(gray: np.ndarray) -> float:
    if hasattr(cv2, "Laplacian"):
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    gray64 = gray.astype(np.float64)
    lap = (
        -4.0 * gray64
        + np.roll(gray64, 1, axis=0)
        + np.roll(gray64, -1, axis=0)
        + np.roll(gray64, 1, axis=1)
        + np.roll(gray64, -1, axis=1)
    )
    return float(lap.var())


def _bbox_center_distance_ratio(frame_shape: tuple[int, int], bbox: tuple[int, int, int, int]) -> float:
    frame_h, frame_w = frame_shape
    x, y, w, h = bbox
    center_x = x + (w / 2.0)
    center_y = y + (h / 2.0)
    dx = (center_x - (frame_w / 2.0)) / max(1.0, frame_w / 2.0)
    dy = (center_y - (frame_h / 2.0)) / max(1.0, frame_h / 2.0)
    return float(np.sqrt((dx * dx) + (dy * dy)))


def _bbox_edge_touch_ratio(frame_shape: tuple[int, int], bbox: tuple[int, int, int, int]) -> float:
    frame_h, frame_w = frame_shape
    x, y, w, h = bbox
    margin_x = max(1, int(round(frame_w * 0.02)))
    margin_y = max(1, int(round(frame_h * 0.02)))
    touches = 0
    if x <= margin_x:
        touches += 1
    if y <= margin_y:
        touches += 1
    if (x + w) >= (frame_w - margin_x):
        touches += 1
    if (y + h) >= (frame_h - margin_y):
        touches += 1
    return touches / 4.0


def _ensure_odd(value: int) -> int:
    return value if value % 2 == 1 else value + 1
