import json
import logging
import math
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import cv2
import numpy as np

logger = logging.getLogger(__name__)


SUPPORTED_DECODE_BACKENDS = {"opencv", "ffmpeg_cpu", "nvdec", "auto"}


class FFmpegDecodeError(RuntimeError):
    """Raised when an FFmpeg sampled-frame reader cannot complete decoding."""

    def __init__(self, message: str, *, frames_read: int = 0):
        super().__init__(message)
        self.frames_read = frames_read


@dataclass(frozen=True)
class VideoStreamInfo:
    fps: float
    total_frames: int
    width: int
    height: int


class FFmpegSampledReader:
    """Stream sampled BGR frames from FFmpeg without materializing files.

    NVDEC keeps compressed-frame decoding on the GPU. The fps filter runs before
    hwdownload, so only sampled frames cross back to host memory. Cropping,
    scaling, and BGR conversion are performed inside FFmpeg. Keeping color here
    preserves the foreground-model semantics of the original OpenCV pipeline;
    motion detection converts its own input to grayscale.
    """

    def __init__(
        self,
        video_path: str,
        *,
        ffmpeg_bin: str,
        backend: str,
        source_fps: float,
        target_fps: float,
        crop_region: tuple[int, int, int, int],
        output_size: tuple[int, int],
        cpu_threads: int = 2,
        start_offset_seconds: float = 0.0,
        duration_seconds: Optional[float] = None,
    ):
        if backend not in {"ffmpeg_cpu", "nvdec"}:
            raise ValueError(f"Unsupported FFmpeg backend: {backend}")
        self.video_path = video_path
        self.backend = backend
        self.source_fps = max(0.001, float(source_fps))
        self.target_fps = max(0.001, min(float(target_fps), self.source_fps))
        self.output_width = max(1, int(output_size[0]))
        self.output_height = max(1, int(output_size[1]))
        self.frame_size = self.output_width * self.output_height * 3
        self.frames_read = 0
        self.start_offset_seconds = max(0.0, float(start_offset_seconds or 0.0))
        self.duration_seconds = (
            max(0.001, float(duration_seconds))
            if duration_seconds is not None
            else None
        )
        self._stderr_tail: list[str] = []
        self._stderr_lock = threading.Lock()

        x, y, width, height = crop_region
        filters = [f"fps=fps={self.target_fps:.8f}:round=near"]
        if backend == "nvdec":
            filters.extend(["hwdownload", "format=nv12"])
        filters.append(
            f"crop={max(1, width)}:{max(1, height)}:{max(0, x)}:{max(0, y)}:exact=1"
        )
        if (width, height) != (self.output_width, self.output_height):
            filters.append(f"scale={self.output_width}:{self.output_height}:flags=area")
        filters.append("format=bgr24")

        command = [ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "warning"]
        if backend == "nvdec":
            command.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
        else:
            command.extend(["-threads", str(max(1, int(cpu_threads)))])
        if self.start_offset_seconds > 0:
            command.extend(["-ss", f"{self.start_offset_seconds:.6f}"])
        command.extend([
            "-i",
            video_path,
        ])
        if self.duration_seconds is not None:
            command.extend(["-t", f"{self.duration_seconds:.6f}"])
        command.extend([
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            ",".join(filters),
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ])
        self.command = command
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=max(self.frame_size * 2, 1024 * 1024),
            )
        except FileNotFoundError as exc:
            raise FFmpegDecodeError(f"FFmpeg executable not found: {ffmpeg_bin}") from exc
        except OSError as exc:
            raise FFmpegDecodeError(f"Cannot start FFmpeg decoder: {exc}") from exc

        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        payload = self._read_exact(self.frame_size)
        if len(payload) == self.frame_size:
            self.frames_read += 1
            frame = np.frombuffer(payload, dtype=np.uint8).reshape(
                self.output_height,
                self.output_width,
                3,
            )
            return True, frame

        return_code = self.process.wait()
        self._stderr_thread.join(timeout=1.0)
        if payload or return_code != 0:
            detail = self.stderr_text or f"exit code {return_code}"
            raise FFmpegDecodeError(
                f"FFmpeg {self.backend} decode failed: {detail}",
                frames_read=self.frames_read,
            )
        return False, None

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        self._stderr_thread.join(timeout=1.0)

    @property
    def stderr_text(self) -> str:
        with self._stderr_lock:
            return " | ".join(self._stderr_tail[-8:])

    def _read_exact(self, size: int) -> bytes:
        assert self.process.stdout is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self.process.stdout.read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw_line in iter(self.process.stderr.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            with self._stderr_lock:
                self._stderr_tail.append(line)
                self._stderr_tail = self._stderr_tail[-20:]


@dataclass
class FrameSampler:
    source_fps: float
    target_fps: float

    def __post_init__(self) -> None:
        if self.source_fps <= 0 or self.target_fps <= 0 or not np.isfinite(self.source_fps) or not np.isfinite(self.target_fps):
            self.effective_fps = self.source_fps
            self.interval = 1.0
        else:
            self.effective_fps = min(self.target_fps, self.source_fps)
            self.interval = max(1.0, self.source_fps / self.effective_fps)
        self.nominal_frame_step = max(1, int(math.floor(self.interval + 0.5)))
        self._next_sample_position = 0.0

    def skip_count_after(self, current_frame_no: int) -> int:
        if self.interval <= 1.0:
            return 0

        self._next_sample_position += self.interval
        next_frame_no = max(
            current_frame_no + 1,
            int(math.floor(self._next_sample_position + 0.5)),
        )
        return max(0, next_frame_no - current_frame_no - 1)


@dataclass(frozen=True)
class AnalyzerConfig:
    event_scan_fps: float
    roi_region: Optional[dict]
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
    legacy_analysis_max_width: int
    legacy_analysis_max_height: int
    legacy_quick_stable_frames_min: int
    legacy_min_event_gap_seconds: float
    max_event_candidates: int
    max_scan_history: int
    quality_max_dimension: int
    candidate_sample_fps: float
    min_decode_completion_ratio: float

    @classmethod
    def from_mapping(cls, config: dict) -> "AnalyzerConfig":
        return cls(
            event_scan_fps=float(config.get("EVENT_SCAN_FPS", 12.0)),
            roi_region=config.get("ROI_REGION"),
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
            stable_frames_enter=int(config.get("STABLE_FRAMES_ENTER", 5)),
            stable_frames_exit=int(config.get("STABLE_FRAMES_EXIT", 3)),
            bg_history=int(config.get("BG_HISTORY", 500)),
            bg_var_threshold=float(config.get("BG_VAR_THRESHOLD", 16.0)),
            bg_detect_shadows=bool(config.get("BG_DETECT_SHADOWS", False)),
            bg_warmup_frames=int(config.get("BG_WARMUP_FRAMES", 500)),
            bg_empty_learning_rate=float(config.get("BG_EMPTY_LEARNING_RATE", 0.002)),
            fg_ratio_threshold=float(
                config.get(
                    "FG_RATIO_THRESHOLD",
                    config.get("OBJECT_ENTER_RATIO", 0.10),
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
            legacy_analysis_max_width=int(config.get("LEGACY_ANALYSIS_MAX_WIDTH", 960)),
            legacy_analysis_max_height=int(config.get("LEGACY_ANALYSIS_MAX_HEIGHT", 540)),
            legacy_quick_stable_frames_min=int(config.get("LEGACY_QUICK_STABLE_FRAMES_MIN", 1)),
            legacy_min_event_gap_seconds=float(config.get("LEGACY_MIN_EVENT_GAP_SECONDS", 0.8)),
            max_event_candidates=max(8, int(config.get("VIDEO_ANALYSIS_MAX_EVENT_CANDIDATES", 120))),
            max_scan_history=max(100, int(config.get("VIDEO_ANALYSIS_MAX_SCAN_HISTORY", 10000))),
            quality_max_dimension=max(64, int(config.get("VIDEO_ANALYSIS_QUALITY_MAX_DIMENSION", 320))),
            candidate_sample_fps=max(0.0, float(config.get("VIDEO_ANALYSIS_CANDIDATE_FPS", 0.0))),
            min_decode_completion_ratio=max(
                0.0,
                min(1.0, float(config.get("VIDEO_EXTRACT_MIN_DECODE_COMPLETION_RATIO", 0.5))),
            ),
        )

    def for_effective_scan_fps(self, source_fps: float) -> tuple["AnalyzerConfig", int, float]:
        """Return config adjusted for sampled scanning.

        Frame-count thresholds are tuned against source video frames. When we scan
        only every Nth frame, streak counters operate in sampled frames, so keep
        the same wall-clock duration by scaling those thresholds down.
        """
        if source_fps <= 0 or self.event_scan_fps <= 0:
            return self, 1, source_fps

        sampler = FrameSampler(source_fps, self.event_scan_fps)
        if sampler.interval <= 1.0:
            return self, 1, sampler.effective_fps

        ratio = sampler.effective_fps / source_fps

        def scaled(value: int, minimum: int = 1) -> int:
            return max(minimum, int(round(value * ratio)))

        return replace(
            self,
            stable_frames_enter=scaled(self.stable_frames_enter),
            stable_frames_exit=scaled(self.stable_frames_exit),
            quick_stable_frames_min=scaled(self.quick_stable_frames_min),
            legacy_quick_stable_frames_min=scaled(self.legacy_quick_stable_frames_min),
            stable_present_frames_min=scaled(self.stable_present_frames_min),
            stable_sample_interval=scaled(self.stable_sample_interval),
        ), sampler.nominal_frame_step, sampler.effective_fps

    def for_legacy_analysis_scale(self, source_width: int, source_height: int) -> tuple["AnalyzerConfig", float]:
        if source_width <= 0 or source_height <= 0:
            return self, 1.0

        scale = 1.0
        if self.legacy_analysis_max_width > 0:
            scale = min(scale, self.legacy_analysis_max_width / float(source_width))
        if self.legacy_analysis_max_height > 0:
            scale = min(scale, self.legacy_analysis_max_height / float(source_height))
        scale = max(0.05, min(1.0, scale))
        if scale >= 0.999:
            return self, 1.0

        area_scale = scale * scale
        return replace(
            self,
            fg_min_component_area=max(1, int(round(self.fg_min_component_area * area_scale))),
        ), scale


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
    object_present: bool
    object_score: float
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
    frame: Optional[np.ndarray]
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
        gray = (
            roi_frame
            if roi_frame.ndim == 2
            else cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
        )
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
        self.detect_shadows = config.bg_detect_shadows
        self.mog2 = cv2.createBackgroundSubtractorMOG2(
            history=config.bg_history,
            varThreshold=config.bg_var_threshold,
            detectShadows=self.detect_shadows,
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
        self.mog2_seconds = 0.0
        self.threshold_seconds = 0.0
        self.morphology_seconds = 0.0
        self.component_seconds = 0.0

    def analyze(self, roi_frame: np.ndarray, mode: str) -> ForegroundAnalysis:
        learning_rate = self._learning_rate_for_mode(mode)
        started_at = time.perf_counter()
        raw_mask = self.mog2.apply(roi_frame, learningRate=learning_rate)
        self.mog2_seconds += time.perf_counter() - started_at
        self.frames_seen += 1

        started_at = time.perf_counter()
        if self.detect_shadows:
            _, binary = cv2.threshold(raw_mask, 127, 255, cv2.THRESH_BINARY)
        else:
            # MOG2 emits a binary 0/255 mask when shadow detection is disabled.
            binary = raw_mask
        self.threshold_seconds += time.perf_counter() - started_at

        started_at = time.perf_counter()
        cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, self.open_kernel)
        cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, self.close_kernel)
        self.morphology_seconds += time.perf_counter() - started_at

        started_at = time.perf_counter()
        stats = self._measure_components(cleaned)
        self.component_seconds += time.perf_counter() - started_at
        fg_pixels = stats["fg_pixels"]
        fg_ratio = fg_pixels / float(max(1, cleaned.size))
        largest_bbox = stats["largest_bbox"]
        largest_area = stats["largest_area"]
        largest_area_ratio = largest_area / float(max(1, cleaned.size))
        center_distance_ratio = stats["center_distance_ratio"]
        edge_touch_ratio = stats["edge_touch_ratio"]
        present = fg_ratio >= self.config.fg_ratio_threshold and largest_area >= self.config.fg_min_component_area

        return ForegroundAnalysis(
            # Downstream selection uses only aggregate component statistics.
            fg_mask=np.empty((0, 0), dtype=np.uint8),
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
        started_at = time.perf_counter()
        self.mog2.apply(roi_frame, learningRate=self.config.bg_empty_learning_rate)
        self.mog2_seconds += time.perf_counter() - started_at
        self.frames_seen += 1

    def _learning_rate_for_mode(self, mode: str) -> float:
        if mode == "freeze":
            return 0.0
        if mode == "empty_refresh":
            return self.config.bg_empty_learning_rate
        return -1.0

    def _measure_components(self, mask: np.ndarray) -> dict:
        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        largest_bbox: Optional[tuple[int, int, int, int]] = None
        largest_area = 0
        fg_pixels = 0

        component_stats = stats[1:num_labels]
        if component_stats.size:
            component_areas = component_stats[:, cv2.CC_STAT_AREA]
            valid_stats = component_stats[component_areas >= self.config.fg_min_component_area]
        else:
            valid_stats = component_stats

        if valid_stats.size:
            fg_pixels = int(valid_stats[:, cv2.CC_STAT_AREA].sum(dtype=np.int64))
            largest = valid_stats[int(np.argmax(valid_stats[:, cv2.CC_STAT_AREA]))]
            largest_area = int(largest[cv2.CC_STAT_AREA])
            largest_bbox = (
                int(largest[cv2.CC_STAT_LEFT]),
                int(largest[cv2.CC_STAT_TOP]),
                int(largest[cv2.CC_STAT_WIDTH]),
                int(largest[cv2.CC_STAT_HEIGHT]),
            )

        center_distance_ratio = 1.0
        edge_touch_ratio = 1.0
        if largest_bbox is not None:
            center_distance_ratio = _bbox_center_distance_ratio(mask.shape[:2], largest_bbox)
            edge_touch_ratio = _bbox_edge_touch_ratio(mask.shape[:2], largest_bbox)

        return {
            "fg_pixels": fg_pixels,
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
        self.last_candidate_ts: Optional[float] = None

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
                self.last_candidate_ts = None
                self.current_peak_motion_score = 0.0
                self.current_peak_frame_no = 0
                self._track_peak_motion(frame_no, motion.motion_score)
            else:
                self.low_motion_streak += 1
                if not foreground.present and self.pre_stable_candidates:
                    completed_event = self._finalize_pre_stable_candidate(frame_no, scorer)
                    self.present_streak = 0
                    self.pre_stable_candidates = []
                    self.pre_stable_start_frame_no = None
                    self.last_candidate_ts = None
                else:
                    sampled = self._collect_pre_stable_candidate(frame_no, ts, frame, motion, foreground)

            if self.low_motion_streak >= self.config.stable_frames_enter:
                self._enter_stable(frame_no)
                sampled = self._collect_candidate_if_needed(frame_no, ts, frame, motion, foreground)

        else:
            sampled = self._collect_candidate_if_needed(frame_no, ts, frame, motion, foreground)
            if not foreground.present:
                completed_event = self._finalize_event(frame_no, scorer)
                self._reset_after_finalize(frame_no, motion.motion_score)
            if motion.moving:
                self.high_motion_streak += 1
            else:
                self.high_motion_streak = 0

            if completed_event is None and self.high_motion_streak >= self.config.stable_frames_exit:
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
            object_present=foreground.present,
            object_score=self._completeness_raw(foreground),
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
        return self._finalize_quick_event_if_ready(frame_no, scorer) or self._finalize_pre_stable_candidate(frame_no, scorer)

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
            or not self._candidate_sampling_due(ts)
        ):
            return False

        self._append_candidate(
            self.current_candidates,
            self._make_candidate(frame_no, ts, frame, motion, foreground),
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
            self.last_candidate_ts = None
            return False

        self.present_streak += 1
        if self.pre_stable_start_frame_no is None:
            self.pre_stable_start_frame_no = frame_no

        should_sample = (self.present_streak == 1) or ((self.present_streak - 1) % self.config.stable_sample_interval == 0)
        if not should_sample or not self._candidate_sampling_due(ts):
            return False

        self._append_candidate(
            self.pre_stable_candidates,
            self._make_candidate(frame_no, ts, frame, motion, foreground),
        )
        return True

    def _append_candidate(self, candidates: list[CandidateFrame], candidate: CandidateFrame) -> None:
        """Bound memory for long stable scenes while retaining time coverage."""
        candidates.append(candidate)
        if len(candidates) <= self.config.max_event_candidates:
            return
        compacted = candidates[::2]
        if compacted[-1] is not candidates[-1]:
            compacted.append(candidates[-1])
        candidates[:] = compacted

    def _candidate_sampling_due(self, ts: float) -> bool:
        fps = self.config.candidate_sample_fps
        if fps <= 0:
            return True
        if self.last_candidate_ts is not None and ts - self.last_candidate_ts < (1.0 / fps):
            return False
        self.last_candidate_ts = ts
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
        self.last_candidate_ts = None

    def _finalize_quick_event_if_ready(self, end_frame_no: int, scorer: FrameScorer) -> Optional[ClosedEvent]:
        if self.state != self.MOVING:
            return None
        if self.low_motion_streak < self.config.legacy_quick_stable_frames_min:
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

    def _finalize_pre_stable_candidate(self, end_frame_no: int, scorer: FrameScorer) -> Optional[ClosedEvent]:
        if self.state != self.MOVING:
            return None
        if len(self.pre_stable_candidates) < max(1, self.config.legacy_quick_stable_frames_min):
            return None
        if self.pre_stable_start_frame_no is None:
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
            "legacy_pre_stable_fallback"
            if not selection.quality_note
            else f"legacy_pre_stable_fallback+{selection.quality_note}"
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
        quality_gray = _resize_gray_for_quality(motion.gray, self.config.quality_max_dimension)
        laplacian_score = _laplacian_variance(quality_gray)
        tenengrad_score = _compute_tenengrad(quality_gray)
        local_clarity_score = _compute_local_clarity_floor(quality_gray)
        high_frequency_ratio = _compute_high_frequency_ratio(quality_gray)
        return CandidateFrame(
            frame_no=frame_no,
            ts=ts,
            frame=None,
            # No downstream selector reads the stored foreground mask.
            fg_mask=np.empty((0, 0), dtype=np.uint8),
            roi_gray=quality_gray.copy(),
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
    def __init__(
        self,
        output_dir: str,
        channel_id: str,
        video_start_time: datetime,
        writer_filename: str,
        video_path: Optional[str] = None,
        ffmpeg_bin: str = "ffmpeg",
        seek_by_timestamp: bool = False,
    ):
        self.output_dir = output_dir
        self.channel_id = channel_id
        self.video_start_time = video_start_time
        self.video_path = video_path
        self.ffmpeg_bin = ffmpeg_bin
        self.seek_by_timestamp = seek_by_timestamp
        self.event_record_path = os.path.join(output_dir, writer_filename)
        os.makedirs(output_dir, exist_ok=True)

    def write(self, event: ClosedEvent, video_fps: float = 0.0) -> dict:
        best = event.best_candidate
        seconds_offset = best.ts
        captured_at = self.video_start_time + timedelta(seconds=seconds_offset)
        frame_filename = self._make_frame_filename(captured_at)
        frame_path = os.path.join(self.output_dir, frame_filename)
        frame = self._load_best_frame(best)
        cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        start_offset = event.window.start_frame_no
        end_offset = event.window.end_frame_no
        best_offset_from_start = best.frame_no - event.window.start_frame_no
        peak_offset_from_best = event.window.peak_frame_no - best.frame_no
        best_offset_seconds_from_start = best_offset_from_start / video_fps if video_fps > 0 else None
        window_span_seconds = max(0, end_offset - start_offset) / video_fps if video_fps > 0 else None

        record = {
            "timestamp": captured_at.isoformat(),
            "source_start": self.video_start_time.isoformat(),
            "source_offset_seconds": round(seconds_offset, 3),
            "image_path": frame_path,
            "candidate_frame_count": event.window.candidate_count,
            "best_score": round(best.score, 6),
            "best_ts": round(best.ts, 3),
            "frame_no": best.frame_no,
            "start_frame_no": event.window.start_frame_no,
            "end_frame_no": event.window.end_frame_no,
            "peak_frame_no": event.window.peak_frame_no,
            "best_offset_frames_from_start": best_offset_from_start,
            "peak_offset_frames_from_best": peak_offset_from_best,
            "window_frame_span": max(0, end_offset - start_offset),
            "best_offset_seconds_from_start": round(best_offset_seconds_from_start, 3) if best_offset_seconds_from_start is not None else None,
            "window_span_seconds": round(window_span_seconds, 3) if window_span_seconds is not None else None,
            "diff_score": round(event.window.peak_motion_score, 6),
            "low_quality": event.window.low_quality,
            "quality_note": event.window.quality_note,
        }
        with open(self.event_record_path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

        return {
            "image_path": frame_path,
            "captured_at": captured_at,
            "source_start": self.video_start_time,
            "source_offset_seconds": seconds_offset,
            "diff_score": event.window.peak_motion_score,
            "channel_id": self.channel_id,
            "is_candidate": False,
            "frame_no": best.frame_no,
            "start_frame_no": event.window.start_frame_no,
            "end_frame_no": event.window.end_frame_no,
            "peak_frame_no": event.window.peak_frame_no,
            "best_ts": best.ts,
            "best_offset_frames_from_start": best_offset_from_start,
            "peak_offset_frames_from_best": peak_offset_from_best,
            "window_frame_span": max(0, end_offset - start_offset),
            "best_offset_seconds_from_start": best_offset_seconds_from_start,
            "window_span_seconds": window_span_seconds,
            "plate_pixels": best.changed_pixels,
            "motion_score": best.motion_score,
            "object_area_ratio": best.fg_ratio,
            "focus_score": best.laplacian_score,
            "outside_fg_ratio": best.edge_touch_ratio,
            "candidate_frame_count": event.window.candidate_count,
            "best_score": best.score,
            "low_quality": event.window.low_quality,
            "quality_note": event.window.quality_note,
        }

    def _make_frame_filename(self, captured_at: datetime) -> str:
        base_name = f"{self.channel_id}_{captured_at.strftime('%Y-%m-%d-%H-%M-%S')}-{captured_at.microsecond // 1000:03d}"
        filename = f"{base_name}.jpg"
        if not os.path.exists(os.path.join(self.output_dir, filename)):
            return filename

        for idx in range(1, 1000):
            filename = f"{base_name}-{idx:03d}.jpg"
            if not os.path.exists(os.path.join(self.output_dir, filename)):
                return filename

        return f"{base_name}-{uuid.uuid4().hex}.jpg"

    def _load_best_frame(self, best: CandidateFrame) -> np.ndarray:
        if best.frame is not None:
            return best.frame
        if not self.video_path:
            raise ValueError(f"Selected frame {best.frame_no} has no in-memory frame and no source video path")

        if self.seek_by_timestamp:
            try:
                return self._load_frame_with_ffmpeg(best.ts)
            except Exception as exc:
                logger.warning(
                    "Timestamp frame seek failed for %s at %.3fs, falling back to OpenCV frame seek: %s",
                    self.video_path,
                    best.ts,
                    exc,
                )

        cap = cv2.VideoCapture(self.video_path)
        try:
            if not cap.isOpened():
                raise ValueError(f"Cannot open video to load selected frame: {self.video_path}")
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, best.frame_no))
            ret, frame = cap.read()
            if not ret or frame is None:
                raise ValueError(f"Cannot load selected frame {best.frame_no} from {self.video_path}")
            return frame
        finally:
            cap.release()

    def _load_frame_with_ffmpeg(self, timestamp_seconds: float) -> np.ndarray:
        command = [
            self.ffmpeg_bin,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, timestamp_seconds):.6f}",
            "-i",
            self.video_path,
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "pipe:1",
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(detail or f"ffmpeg exited with {completed.returncode}")
        frame = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("ffmpeg returned an undecodable candidate image")
        return frame


class VideoAnalyzer:
    """Single-pass settlement frame extraction driven by ROI motion and background state."""

    def __init__(self, config: dict):
        self.config = AnalyzerConfig.from_mapping(config)
        self.roi_region = self.config.roi_region
        self.channel_roi_regions = _normalize_channel_roi_regions(config.get("VIDEO_CHANNEL_ROI_REGIONS"))
        self.analysis_timezone = ZoneInfo(self.config.analysis_timezone)
        self.auto_detect_settlement_roi = False
        self.last_scan_frames: list[ScanFrame] = []
        self.last_event_windows: list[EventWindow] = []
        self.object_ratio_baseline = 0.0
        self.object_pixels_baseline = 0.0
        requested_backend = str(config.get("VIDEO_EXTRACT_DECODE_BACKEND", "opencv")).strip().lower()
        self.decode_backend = requested_backend if requested_backend in SUPPORTED_DECODE_BACKENDS else "opencv"
        self.ffmpeg_bin = str(config.get("FFMPEG_BIN") or "ffmpeg")
        try:
            self.cpu_threads = max(1, int(config.get("VIDEO_EXTRACT_CPU_THREADS_PER_JOB", 2)))
        except (TypeError, ValueError):
            self.cpu_threads = 2

    def extract_frames(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        *,
        start_offset_seconds: float = 0.0,
        duration_seconds: Optional[float] = None,
    ) -> list[dict]:
        return self._extract_frames_legacy(
            video_path,
            output_dir,
            video_start_time,
            channel_id,
            progress_callback,
            start_offset_seconds=start_offset_seconds,
            duration_seconds=duration_seconds,
        )

    def _extract_frames_legacy(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        *,
        start_offset_seconds: float = 0.0,
        duration_seconds: Optional[float] = None,
    ) -> list[dict]:
        stream_info = self._probe_video_stream(video_path)
        backend_order = {
            "auto": ["nvdec", "ffmpeg_cpu", "opencv"],
            "nvdec": ["nvdec", "ffmpeg_cpu", "opencv"],
            "ffmpeg_cpu": ["ffmpeg_cpu", "opencv"],
            "opencv": ["opencv"],
        }[self.decode_backend]
        fallback_errors: list[str] = []

        for backend in backend_order:
            try:
                return self._extract_frames_with_backend(
                    video_path,
                    output_dir,
                    video_start_time,
                    channel_id,
                    stream_info,
                    backend,
                    progress_callback,
                    fallback_reason=" | ".join(fallback_errors[-2:]) or None,
                    start_offset_seconds=start_offset_seconds,
                    duration_seconds=duration_seconds,
                )
            except FFmpegDecodeError as exc:
                # Switching decoders after analysis has started can duplicate
                # already-written events. Let the outer repair strategy handle
                # mid-stream failures, but transparently recover startup errors.
                if exc.frames_read > 0:
                    raise
                error_text = f"{backend}: {exc}"
                fallback_errors.append(error_text)
                logger.warning("Video decode backend %s unavailable for %s: %s", backend, video_path, exc)
                if progress_callback is not None:
                    progress_callback({
                        "extract_strategy": self._strategy_name(backend),
                        "decode_backend": backend,
                        "recovery_status": "decode_fallback",
                        "decode_fallback_reason": error_text,
                    })

        raise RuntimeError("No video decode backend succeeded: " + " | ".join(fallback_errors))

    def _extract_frames_with_backend(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
        stream_info: VideoStreamInfo,
        backend: str,
        progress_callback: Optional[Callable[[dict], None]],
        fallback_reason: Optional[str],
        start_offset_seconds: float = 0.0,
        duration_seconds: Optional[float] = None,
    ) -> list[dict]:
        start_offset_seconds = max(0.0, float(start_offset_seconds or 0.0))
        duration_seconds = (
            max(0.001, float(duration_seconds))
            if duration_seconds is not None
            else None
        )
        cap = None
        reader = None
        resolved_roi = self._resolve_roi_region(channel_id)
        original_roi = self.roi_region
        base_config = self.config
        video_fps = stream_info.fps
        total_frames = stream_info.total_frames
        frame_width = stream_info.width
        frame_height = stream_info.height
        effective_config, frame_step, effective_scan_fps = base_config.for_effective_scan_fps(video_fps)
        crop_x, crop_y, crop_width, crop_height = self._bounded_crop_region(
            frame_width,
            frame_height,
            resolved_roi,
        )
        effective_config, analysis_scale = effective_config.for_legacy_analysis_scale(
            crop_width,
            crop_height,
        )
        analysis_width = max(1, int(round(crop_width * analysis_scale)))
        analysis_height = max(1, int(round(crop_height * analysis_scale)))
        self.config = effective_config
        self.roi_region = resolved_roi if backend == "opencv" else None

        try:
            if backend == "opencv":
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    raise ValueError(f"Cannot open video: {video_path}")
                if start_offset_seconds > 0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, start_offset_seconds * 1000.0)
            else:
                reader = FFmpegSampledReader(
                    video_path,
                    ffmpeg_bin=self.ffmpeg_bin,
                    backend=backend,
                    source_fps=video_fps,
                    target_fps=effective_scan_fps,
                    crop_region=(crop_x, crop_y, crop_width, crop_height),
                    output_size=(analysis_width, analysis_height),
                    cpu_threads=self.cpu_threads,
                    start_offset_seconds=start_offset_seconds,
                    duration_seconds=duration_seconds,
                )
        except Exception:
            if cap is not None:
                cap.release()
            self.config = base_config
            self.roi_region = original_roi
            raise

        video_start_time = self._normalize_video_start_time(video_start_time)
        motion_detector = MotionDetector(effective_config)
        background_model = BackgroundModel(effective_config)
        scorer = FrameScorer(effective_config)
        state_machine = EventStateMachine(effective_config)
        frame_sampler = FrameSampler(video_fps, effective_scan_fps)
        strategy_name = self._strategy_name(backend)
        writer = ResultWriter(
            output_dir,
            channel_id,
            video_start_time,
            effective_config.event_record_filename,
            video_path=video_path,
            ffmpeg_bin=self.ffmpeg_bin,
            seek_by_timestamp=backend != "opencv",
        )

        self.last_scan_frames = []
        self.last_event_windows = []
        results: list[dict] = []
        last_written_event_ts: Optional[float] = None
        first_frame_pos_msec: Optional[float] = None
        first_window_frame_no = max(0, int(round(start_offset_seconds * video_fps)))
        if total_frames > 0:
            available_frames = max(0, total_frames - first_window_frame_no)
            requested_frames = (
                max(1, int(math.ceil(duration_seconds * video_fps)))
                if duration_seconds is not None
                else available_frames
            )
            window_total_frames = min(available_frames, requested_frames)
        else:
            window_total_frames = 0
        progress_interval = self._progress_interval(window_total_frames, frame_step, video_fps)
        next_progress_frame = first_window_frame_no
        last_consumed_frame_no = -1
        sample_index = 0
        started_at = time.perf_counter()
        decode_seconds = 0.0
        analysis_seconds = 0.0
        motion_seconds = 0.0
        state_machine_seconds = 0.0
        candidate_write_seconds = 0.0

        def progress_metrics() -> dict:
            elapsed = max(0.0, time.perf_counter() - started_at)
            processed_samples = len(self.last_scan_frames)
            video_seconds = (
                max(0.0, (last_consumed_frame_no - first_window_frame_no) / video_fps)
                if video_fps > 0 and last_consumed_frame_no >= 0
                else 0.0
            )
            return {
                "extract_strategy": strategy_name,
                "decode_backend": backend,
                "decode_fallback_reason": fallback_reason,
                "analysis_width": analysis_width,
                "analysis_height": analysis_height,
                "elapsed_seconds": round(elapsed, 3),
                "processing_fps": round(processed_samples / elapsed, 3) if elapsed > 0 else 0.0,
                "realtime_factor": round(video_seconds / elapsed, 3) if elapsed > 0 else 0.0,
                "stage_timings": {
                    "decode_seconds": round(decode_seconds, 3),
                    "analysis_seconds": round(analysis_seconds, 3),
                    "motion_seconds": round(motion_seconds, 3),
                    "mog2_seconds": round(background_model.mog2_seconds, 3),
                    "threshold_seconds": round(background_model.threshold_seconds, 3),
                    "morphology_seconds": round(background_model.morphology_seconds, 3),
                    "component_seconds": round(background_model.component_seconds, 3),
                    "state_machine_seconds": round(state_machine_seconds, 3),
                    "candidate_write_seconds": round(candidate_write_seconds, 3),
                },
            }

        self._report_progress(
            progress_callback,
            frame_no=0,
            total_frames=window_total_frames,
            extracted_count=0,
            frame_step=frame_step,
            effective_scan_fps=effective_scan_fps,
            extra=progress_metrics(),
        )

        try:
            frame_no = first_window_frame_no
            while True:
                read_started = time.perf_counter()
                if backend == "opencv":
                    assert cap is not None
                    ret, frame = cap.read()
                else:
                    assert reader is not None
                    ret, frame = reader.read()
                decode_seconds += time.perf_counter() - read_started
                if not ret:
                    break
                assert frame is not None
                if (
                    duration_seconds is not None
                    and video_fps > 0
                    and (frame_no - first_window_frame_no) / video_fps >= duration_seconds
                ):
                    break
                last_consumed_frame_no = frame_no

                if backend == "opencv":
                    roi_frame = self._apply_roi(frame)
                    if roi_frame.size == 0:
                        frame_no += 1
                        continue
                    analysis_frame = _resize_frame_for_analysis(roi_frame, analysis_scale)
                    assert cap is not None
                    pos_msec = self._video_position_msec(cap)
                    if first_frame_pos_msec is None and np.isfinite(pos_msec):
                        first_frame_pos_msec = pos_msec
                    ts = self._frame_timestamp_seconds(
                        cap,
                        max(0, frame_no - first_window_frame_no),
                        video_fps,
                        position_msec=pos_msec,
                        position_msec_base=first_frame_pos_msec,
                    ) + start_offset_seconds
                else:
                    analysis_frame = frame
                    relative_ts = (
                        sample_index / effective_scan_fps
                        if effective_scan_fps > 0
                        else max(0, frame_no - first_window_frame_no) / video_fps
                    )
                    ts = start_offset_seconds + relative_ts
                    sample_index += 1

                if duration_seconds is not None and ts >= start_offset_seconds + duration_seconds:
                    break

                analysis_started = time.perf_counter()
                motion_started = time.perf_counter()
                motion = motion_detector.analyze(analysis_frame)
                motion_seconds += time.perf_counter() - motion_started
                bg_mode = state_machine.current_bg_mode()
                foreground = background_model.analyze(analysis_frame, mode=bg_mode)

                state_machine_started = time.perf_counter()
                scan_frame, closed_event = state_machine.process_frame(
                    frame_no=frame_no,
                    ts=ts,
                    frame=frame,
                    motion=motion,
                    foreground=foreground,
                    scorer=scorer,
                )
                state_machine_seconds += time.perf_counter() - state_machine_started
                analysis_seconds += time.perf_counter() - analysis_started
                self.last_scan_frames.append(scan_frame)
                if len(self.last_scan_frames) > effective_config.max_scan_history:
                    self.last_scan_frames = self.last_scan_frames[::2]

                if bg_mode == "freeze" and not foreground.present:
                    background_model.refresh_empty_scene(analysis_frame)

                if closed_event is not None:
                    self.last_event_windows.append(closed_event.window)
                    if self._should_write_legacy_event(closed_event, last_written_event_ts):
                        write_started = time.perf_counter()
                        result = writer.write(closed_event, video_fps)
                        candidate_write_seconds += time.perf_counter() - write_started
                        result["decoder_strategy"] = strategy_name
                        result["decode_backend"] = backend
                        results.append(result)
                        last_written_event_ts = closed_event.best_candidate.ts

                if frame_no >= next_progress_frame:
                    self._report_progress(
                        progress_callback,
                        frame_no=max(0, frame_no - first_window_frame_no),
                        total_frames=window_total_frames,
                        extracted_count=len(results),
                        frame_step=frame_step,
                        effective_scan_fps=effective_scan_fps,
                        extra=progress_metrics(),
                    )
                    next_progress_frame = frame_no + progress_interval

                planned_skip = frame_sampler.skip_count_after(frame_no)
                skipped = (
                    self._skip_frames(cap, planned_skip)
                    if backend == "opencv"
                    else planned_skip
                )
                last_consumed_frame_no = frame_no + skipped
                frame_no += skipped + 1

            final_event = state_machine.flush(max(0, last_consumed_frame_no), scorer)
            if final_event is not None:
                self.last_event_windows.append(final_event.window)
                if self._should_write_legacy_event(final_event, last_written_event_ts):
                    write_started = time.perf_counter()
                    result = writer.write(final_event, video_fps)
                    candidate_write_seconds += time.perf_counter() - write_started
                    result["decoder_strategy"] = strategy_name
                    result["decode_backend"] = backend
                    results.append(result)
        finally:
            if cap is not None:
                cap.release()
            if reader is not None:
                reader.close()
            self.config = base_config
            self.roi_region = original_roi

        consumed_window_frames = max(0, last_consumed_frame_no - first_window_frame_no + 1)
        completion_ratio = (
            consumed_window_frames / float(window_total_frames)
            if window_total_frames > 0 and last_consumed_frame_no >= 0
            else 1.0
        )
        if window_total_frames > 0 and completion_ratio < effective_config.min_decode_completion_ratio:
            raise RuntimeError(
                "Video decode ended prematurely: "
                f"consumed={consumed_window_frames} total={window_total_frames} "
                f"ratio={completion_ratio:.3f}"
            )

        _mark_secondary_frames_by_quality(results)
        self._update_baselines()
        self._report_progress(
            progress_callback,
            frame_no=max(0, last_consumed_frame_no - first_window_frame_no),
            total_frames=window_total_frames,
            extracted_count=len(results),
            frame_step=frame_step,
            effective_scan_fps=effective_scan_fps,
            extra=progress_metrics(),
        )
        logger.info(
            "Extracted %s frames from %s backend=%s elapsed=%.3fs decode=%.3fs analysis=%.3fs "
            "motion=%.3fs mog2=%.3fs morphology=%.3fs components=%.3fs state=%.3fs write=%.3fs",
            len(results),
            video_path,
            backend,
            time.perf_counter() - started_at,
            decode_seconds,
            analysis_seconds,
            motion_seconds,
            background_model.mog2_seconds,
            background_model.morphology_seconds,
            background_model.component_seconds,
            state_machine_seconds,
            candidate_write_seconds,
        )

        if not results:
            logger.warning("No settlement events detected in %s", video_path)
        return results

    @staticmethod
    def _strategy_name(backend: str) -> str:
        return "ffmpeg_nvdec" if backend == "nvdec" else backend

    def _probe_video_stream(self, video_path: str) -> VideoStreamInfo:
        ffmpeg_dir = os.path.dirname(self.ffmpeg_bin)
        ffprobe_bin = os.path.join(ffmpeg_dir, "ffprobe") if ffmpeg_dir else "ffprobe"
        probe_command = [
            ffprobe_bin,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate,nb_frames,width,height,duration",
            "-of",
            "json",
            video_path,
        ]
        try:
            completed = subprocess.run(
                probe_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
                text=True,
            )
            payload = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
            streams = payload.get("streams") or []
            if streams:
                stream = streams[0]
                fps = self._parse_frame_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
                raw_total = str(stream.get("nb_frames") or "").strip()
                total_frames = int(raw_total) if raw_total.isdigit() else 0
                if total_frames <= 0:
                    duration = float(stream.get("duration") or 0.0)
                    total_frames = int(round(duration * fps)) if duration > 0 and fps > 0 else 0
                if fps > 0 and width > 0 and height > 0:
                    return VideoStreamInfo(
                        fps=fps,
                        total_frames=total_frames,
                        width=width,
                        height=height,
                    )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError):
            pass

        cap = cv2.VideoCapture(video_path)
        try:
            if not cap.isOpened():
                raise ValueError(f"Cannot open video: {video_path}")
            return VideoStreamInfo(
                fps=float(cap.get(cv2.CAP_PROP_FPS) or 25.0),
                total_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
                width=max(1, int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)),
                height=max(1, int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)),
            )
        finally:
            cap.release()

    @staticmethod
    def _parse_frame_rate(value: object) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else 0.0
        return float(text)

    @staticmethod
    def _bounded_crop_region(
        frame_width: int,
        frame_height: int,
        roi_region: Optional[dict],
    ) -> tuple[int, int, int, int]:
        if not roi_region:
            return 0, 0, max(1, frame_width), max(1, frame_height)
        x = max(0, min(int(roi_region.get("x", 0) or 0), max(0, frame_width - 1)))
        y = max(0, min(int(roi_region.get("y", 0) or 0), max(0, frame_height - 1)))
        width = max(1, min(int(roi_region.get("w", frame_width - x) or frame_width - x), frame_width - x))
        height = max(1, min(int(roi_region.get("h", frame_height - y) or frame_height - y), frame_height - y))
        return x, y, width, height

    @staticmethod
    def _skip_frames(cap, skip_count: int) -> int:
        skipped = 0
        for _ in range(max(0, skip_count)):
            if not cap.grab():
                break
            skipped += 1
        return skipped

    @staticmethod
    def _video_position_msec(cap) -> float:
        pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
        try:
            return float(pos_msec)
        except (TypeError, ValueError):
            return float("nan")

    @staticmethod
    def _frame_timestamp_seconds(
        cap,
        frame_no: int,
        video_fps: float,
        position_msec: Optional[float] = None,
        position_msec_base: Optional[float] = None,
    ) -> float:
        pos_msec_value = (
            VideoAnalyzer._video_position_msec(cap)
            if position_msec is None
            else position_msec
        )
        if position_msec_base is not None and np.isfinite(position_msec_base):
            pos_msec_value -= position_msec_base
        if np.isfinite(pos_msec_value) and (pos_msec_value > 0 or frame_no == 0):
            return max(0.0, pos_msec_value / 1000.0)
        return frame_no / video_fps if video_fps > 0 else 0.0

    @staticmethod
    def _progress_interval(total_frames: int, frame_step: int, video_fps: float) -> int:
        if total_frames > 0:
            return max(frame_step, total_frames // 100)
        return max(frame_step, int(max(video_fps, 1.0) * 10))

    @staticmethod
    def _report_progress(
        progress_callback: Optional[Callable[[dict], None]],
        *,
        frame_no: int,
        total_frames: int,
        extracted_count: int,
        frame_step: int,
        effective_scan_fps: float,
        extra: Optional[dict] = None,
    ) -> None:
        if progress_callback is None:
            return
        progress = {
            "frame_no": max(0, frame_no),
            "total_frames": max(0, total_frames),
            "progress_percent": (
                min(100.0, round(((max(0, frame_no) + 1) / total_frames) * 100.0, 1))
                if total_frames > 0
                else None
            ),
            "extracted_count": extracted_count,
            "frame_step": frame_step,
            "effective_scan_fps": round(effective_scan_fps, 3) if effective_scan_fps > 0 else 0.0,
        }
        if extra:
            progress.update(extra)
        progress_callback(progress)

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

    def _resolve_roi_region(self, channel_id: str) -> Optional[dict]:
        normalized_channel_id = str(channel_id or "").strip()
        if normalized_channel_id and normalized_channel_id in self.channel_roi_regions:
            return self.channel_roi_regions[normalized_channel_id]
        return self.config.roi_region

    def _legacy_analysis_source_size(self, frame_width: int, frame_height: int) -> tuple[int, int]:
        if self.roi_region:
            roi_w = int(self.roi_region.get("w", frame_width) or frame_width)
            roi_h = int(self.roi_region.get("h", frame_height) or frame_height)
            return max(1, roi_w), max(1, roi_h)
        return max(1, frame_width), max(1, frame_height)

    def _should_write_legacy_event(self, event: ClosedEvent, last_written_ts: Optional[float]) -> bool:
        if last_written_ts is None:
            return True
        min_gap = self.config.legacy_min_event_gap_seconds
        if min_gap <= 0:
            return True
        return (event.best_candidate.ts - last_written_ts) >= min_gap


def _crop_frame_by_region(frame: np.ndarray, roi_region: Optional[dict], expand: int = 0) -> np.ndarray:
    if not roi_region:
        return frame

    height, width = frame.shape[:2]
    x = max(0, min(int(roi_region.get("x", 0)), width))
    y = max(0, min(int(roi_region.get("y", 0)), height))
    roi_w = max(0, min(int(roi_region.get("w", width)), width - x))
    roi_h = max(0, min(int(roi_region.get("h", height)), height - y))
    if roi_w <= 0 or roi_h <= 0:
        shape = (0, 0) if frame.ndim == 2 else (0, 0, frame.shape[2])
        return np.empty(shape, dtype=frame.dtype)

    if expand > 0:
        x = max(0, x - expand)
        y = max(0, y - expand)
        roi_w = min(width - x, roi_w + (expand * 2))
        roi_h = min(height - y, roi_h + (expand * 2))
    return frame[y:y + roi_h, x:x + roi_w]


def _normalize_channel_roi_regions(value: object) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, int]] = {}
    for channel_id, roi_value in value.items():
        if not isinstance(roi_value, dict):
            continue
        try:
            x = int(round(float(roi_value.get("x"))))
            y = int(round(float(roi_value.get("y"))))
            w = int(round(float(roi_value.get("w"))))
            h = int(round(float(roi_value.get("h"))))
        except (TypeError, ValueError):
            continue
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            continue
        normalized_channel_id = str(channel_id or "").strip()
        if normalized_channel_id:
            result[normalized_channel_id] = {"x": x, "y": y, "w": w, "h": h}
    return result


def _mark_secondary_frames_by_quality(frames: list[dict]) -> None:
    """Keep one best primary frame per channel/second and mark the rest as backups.

    Event completion order is not a quality signal. Grouping after the full
    recording has been scanned lets a later, clearer event become the primary
    frame instead of permanently treating the first event as the winner.
    """
    groups: dict[tuple[str, object], list[tuple[int, dict]]] = {}
    for index, frame in enumerate(frames):
        captured_at = frame.get("captured_at")
        if isinstance(captured_at, datetime):
            second_key: object = captured_at.replace(microsecond=0)
        else:
            second_key = int(float(frame.get("best_ts") or 0.0))
        key = (str(frame.get("channel_id") or ""), second_key)
        groups.setdefault(key, []).append((index, frame))

    ranked_frames: list[dict] = []
    for grouped_frames in groups.values():
        ranked_group = sorted(
            grouped_frames,
            key=lambda item: _frame_quality_key(item[1]),
            reverse=True,
        )
        for rank, (_, frame) in enumerate(ranked_group):
            frame["is_candidate"] = rank > 0
            ranked_frames.append(frame)

    # Video tasks persist frames in this order. Keeping backups quality-ranked
    # makes their database IDs a stable fallback order without a schema change.
    frames[:] = ranked_frames


def _frame_quality_key(frame: dict) -> tuple[float, float, float, float, float]:
    def number(name: str, default: float = 0.0) -> float:
        try:
            value = float(frame.get(name, default))
        except (TypeError, ValueError):
            return default
        return value if np.isfinite(value) else default

    return (
        0.0 if frame.get("low_quality") else 1.0,
        number("best_score"),
        number("focus_score"),
        number("object_area_ratio"),
        -number("outside_fg_ratio", 1.0),
    )


def _resize_frame_for_analysis(frame: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 0.999:
        return frame
    height, width = frame.shape[:2]
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)


def _resize_gray_for_quality(gray: np.ndarray, max_dimension: int) -> np.ndarray:
    height, width = gray.shape[:2]
    largest = max(height, width)
    if largest <= max_dimension:
        return gray
    scale = max_dimension / float(largest)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    return cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_AREA)


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
