import logging
import math
import os
import re
import statistics
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import cv2
import numpy as np


logger = logging.getLogger(__name__)

_RAPIDOCR_ENGINE = None
_RAPIDOCR_LOAD_LOCK = threading.Lock()
_RAPIDOCR_RUN_LOCK = threading.Lock()

_DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\D{1,4}(?P<month>\d{1,2})\D{1,4}(?P<day>\d{1,2})"
)
_COMPACT_DATE_PATTERN = re.compile(r"(?<!\d)(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})(?!\d)")
_TIME_PATTERN = re.compile(
    r"(?=(?<!\d)(?P<hour>[0-2]?\d)\D{1,3}(?P<minute>[0-5]\d)\D{1,3}(?P<second>[0-5]\d)(?!\d))"
)
_COMPACT_TIME_PATTERN = re.compile(r"(?=(?<!\d)(?P<hour>[0-2]\d)(?P<minute>[0-5]\d)(?P<second>[0-5]\d)(?!\d))")


@dataclass(frozen=True)
class OCRTimestampObservation:
    sample_offset_seconds: float
    timestamp: datetime | None
    text: str
    confidence: float
    date_from_ocr: bool
    error: str | None = None

    def to_metadata(self, reported_start: datetime) -> dict:
        media_start = (
            self.timestamp - timedelta(seconds=self.sample_offset_seconds)
            if self.timestamp is not None
            else None
        )
        return {
            "sample_offset_seconds": round(self.sample_offset_seconds, 3),
            "recognized_timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "candidate_media_start": media_start.isoformat() if media_start else None,
            "candidate_offset_seconds": (
                round((media_start - reported_start).total_seconds(), 3)
                if media_start is not None
                else None
            ),
            "ocr_text": self.text[:160],
            "ocr_confidence": round(self.confidence, 4),
            "date_from_ocr": self.date_from_ocr,
            "error": self.error,
        }


@dataclass(frozen=True)
class OSDTimeCalibrationResult:
    reported_start: datetime
    media_start: datetime
    status: str
    confidence: float = 0.0
    offset_seconds: float = 0.0
    sample_count: int = 0
    valid_sample_count: int = 0
    inlier_sample_count: int = 0
    observations: tuple[OCRTimestampObservation, ...] = field(default_factory=tuple)
    error: str | None = None

    @property
    def calibrated(self) -> bool:
        return self.status == "calibrated"

    def to_metadata(self) -> dict:
        return {
            "osd_time_calibration_status": self.status,
            "osd_time_calibrated": self.calibrated,
            "osd_time_reported_start": self.reported_start.isoformat(),
            "osd_time_media_start": self.media_start.isoformat(),
            "osd_time_offset_seconds": round(self.offset_seconds, 3),
            "osd_time_confidence": round(self.confidence, 4),
            "osd_time_sample_count": self.sample_count,
            "osd_time_valid_sample_count": self.valid_sample_count,
            "osd_time_inlier_sample_count": self.inlier_sample_count,
            "osd_time_samples": [item.to_metadata(self.reported_start) for item in self.observations],
            "osd_time_error": self.error,
            "timestamp_origin": self.media_start.isoformat(),
            "timestamp_origin_basis": "osd_ocr" if self.calibrated else "reported_recording_start",
        }


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _as_float(value: object, default: float, minimum: float | None = None) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = default
    if not math.isfinite(resolved):
        resolved = default
    return max(minimum, resolved) if minimum is not None else resolved


def _as_int(value: object, default: int, minimum: int = 1) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, resolved)


def _normalize_ocr_text(value: str) -> str:
    return " ".join(
        str(value or "")
        .replace("：", ":")
        .replace("－", "-")
        .replace("—", "-")
        .replace("／", "/")
        .split()
    )


def _valid_date_parts(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
        return True
    except ValueError:
        return False


def _valid_time_parts(hour: int, minute: int, second: int) -> bool:
    return 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59


def parse_osd_timestamp(text: str, expected_at: datetime) -> tuple[datetime | None, bool]:
    """Parse an OSD timestamp, falling back to the expected date when needed."""
    normalized = _normalize_ocr_text(text)
    date_candidates: list[tuple[int, int, int, int]] = []
    for pattern in (_DATE_PATTERN, _COMPACT_DATE_PATTERN):
        for match in pattern.finditer(normalized):
            parts = tuple(int(match.group(name)) for name in ("year", "month", "day"))
            if _valid_date_parts(*parts):
                date_candidates.append((*parts, match.end()))

    time_candidates: list[tuple[int, int, int, int]] = []
    for pattern in (_TIME_PATTERN, _COMPACT_TIME_PATTERN):
        for match in pattern.finditer(normalized):
            parts = tuple(int(match.group(name)) for name in ("hour", "minute", "second"))
            if _valid_time_parts(*parts):
                time_candidates.append((*parts, match.start()))

    if not time_candidates:
        return None, False

    combined: list[tuple[datetime, bool]] = []
    for year, month, day, date_end in date_candidates:
        for hour, minute, second, time_start in time_candidates:
            if time_start < date_end:
                continue
            combined.append((expected_at.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=second, microsecond=0), True))

    if not combined:
        expected_date = expected_at.date()
        for hour, minute, second, _ in time_candidates:
            for day_delta in (-1, 0, 1):
                candidate_date = expected_date + timedelta(days=day_delta)
                combined.append((expected_at.replace(year=candidate_date.year, month=candidate_date.month, day=candidate_date.day, hour=hour, minute=minute, second=second, microsecond=0), False))

    if not combined:
        return None, False
    return min(combined, key=lambda item: abs((item[0] - expected_at).total_seconds()))


class OSDTimeCalibrator:
    def __init__(
        self,
        config: dict,
        *,
        frame_loader: Callable[[str, float], np.ndarray] | None = None,
        ocr_reader: Callable[[np.ndarray], tuple[str, float]] | None = None,
    ):
        self.config = config
        self.enabled = _as_bool(config.get("VIDEO_OSD_TIME_CALIBRATION_ENABLED"), False)
        self.ffmpeg_bin = str(config.get("FFMPEG_BIN") or "ffmpeg")
        self.tesseract_bin = str(config.get("VIDEO_OSD_OCR_BIN") or "tesseract")
        self.sample_offsets = self._parse_sample_offsets(config.get("VIDEO_OSD_OCR_SAMPLE_OFFSETS", "0,1,2,4"))
        self.roi = self._parse_roi(config.get("VIDEO_OSD_OCR_ROI", "0,0.68,1,1"))
        self.min_samples = _as_int(config.get("VIDEO_OSD_OCR_MIN_SAMPLES"), 3)
        self.max_offset_seconds = _as_float(config.get("VIDEO_OSD_OCR_MAX_OFFSET_SECONDS"), 600.0, 0.0)
        self.max_spread_seconds = _as_float(config.get("VIDEO_OSD_OCR_MAX_SPREAD_SECONDS"), 1.25, 0.0)
        self.min_confidence = _as_float(config.get("VIDEO_OSD_OCR_MIN_CONFIDENCE"), 0.70, 0.0)
        self.frame_timeout_seconds = _as_float(config.get("VIDEO_OSD_OCR_FRAME_TIMEOUT_SECONDS"), 20.0, 1.0)
        self.ocr_timeout_seconds = _as_float(config.get("VIDEO_OSD_OCR_TIMEOUT_SECONDS"), 6.0, 1.0)
        timezone_name = str(config.get("VIDEO_TIMEZONE") or config.get("APP_TIMEZONE") or "Asia/Shanghai")
        self.timezone = ZoneInfo(timezone_name)
        self.frame_loader = frame_loader or self._load_frame
        self.ocr_reader = ocr_reader or self._read_timestamp_text
        self._rapidocr_timestamp_boxes: list[tuple[int, int, int, int]] = []

    @staticmethod
    def _parse_sample_offsets(value: object) -> tuple[float, ...]:
        if isinstance(value, (list, tuple)):
            raw_values = value
        else:
            raw_values = str(value or "").split(",")
        offsets = []
        for item in raw_values:
            try:
                offset = max(0.0, float(item))
            except (TypeError, ValueError):
                continue
            if math.isfinite(offset) and offset not in offsets:
                offsets.append(offset)
        return tuple(sorted(offsets)) or (0.0, 1.0, 2.0, 4.0)

    @staticmethod
    def _parse_roi(value: object) -> tuple[float, float, float, float]:
        if isinstance(value, dict):
            x1 = value.get("x", value.get("x1", 0))
            y1 = value.get("y", value.get("y1", 0.68))
            x2 = float(x1) + float(value["w"]) if "w" in value else value.get("x2", 1)
            y2 = float(y1) + float(value["h"]) if "h" in value else value.get("y2", 1)
            raw = (x1, y1, x2, y2)
        else:
            raw = tuple(str(value or "0,0.68,1,1").split(","))
        try:
            x1, y1, x2, y2 = (float(item) for item in raw)
        except (TypeError, ValueError):
            return 0.0, 0.68, 1.0, 1.0
        x1, y1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
        x2, y2 = max(x1, min(1.0, x2)), max(y1, min(1.0, y2))
        return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else (0.0, 0.68, 1.0, 1.0)

    def calibrate(self, video_path: str, reported_start: datetime) -> OSDTimeCalibrationResult:
        reported_start = self._normalize_datetime(reported_start)
        if not self.enabled:
            return OSDTimeCalibrationResult(reported_start, reported_start, "disabled")
        if not os.path.isfile(video_path):
            return OSDTimeCalibrationResult(reported_start, reported_start, "video_missing", error=video_path)

        observations = []
        for sample_offset in self.sample_offsets:
            expected_at = reported_start + timedelta(seconds=sample_offset)
            try:
                frame = self.frame_loader(video_path, sample_offset)
                text, confidence = self.ocr_reader(frame)
                timestamp, date_from_ocr = parse_osd_timestamp(text, expected_at)
                observations.append(OCRTimestampObservation(sample_offset, timestamp, text, confidence, date_from_ocr))
            except Exception as exc:
                observations.append(OCRTimestampObservation(sample_offset, None, "", 0.0, False, f"{type(exc).__name__}: {exc}"[:300]))
            interim_deltas = []
            for observation in observations:
                if observation.timestamp is None:
                    continue
                candidate_start = observation.timestamp - timedelta(seconds=observation.sample_offset_seconds)
                delta = (candidate_start - reported_start).total_seconds()
                if abs(delta) <= self.max_offset_seconds:
                    interim_deltas.append(delta)
            if (
                len(interim_deltas) >= self.min_samples
                and max(interim_deltas) - min(interim_deltas) <= self.max_spread_seconds
            ):
                break

        valid = []
        for observation in observations:
            if observation.timestamp is None:
                continue
            candidate_start = observation.timestamp - timedelta(seconds=observation.sample_offset_seconds)
            delta = (candidate_start - reported_start).total_seconds()
            if abs(delta) <= self.max_offset_seconds:
                valid.append((observation, delta))

        if len(valid) < self.min_samples:
            return OSDTimeCalibrationResult(
                reported_start,
                reported_start,
                "insufficient_samples",
                sample_count=len(observations),
                valid_sample_count=len(valid),
                observations=tuple(observations),
            )

        median_delta = statistics.median(delta for _, delta in valid)
        inliers = [(item, delta) for item, delta in valid if abs(delta - median_delta) <= self.max_spread_seconds]
        if len(inliers) < self.min_samples:
            return OSDTimeCalibrationResult(
                reported_start,
                reported_start,
                "inconsistent_samples",
                sample_count=len(observations),
                valid_sample_count=len(valid),
                inlier_sample_count=len(inliers),
                observations=tuple(observations),
            )

        final_delta = float(statistics.median(delta for _, delta in inliers))
        spread = max(delta for _, delta in inliers) - min(delta for _, delta in inliers)
        consistency = max(0.0, 1.0 - spread / max(1.0, self.max_spread_seconds * 2.0))
        sample_ratio = len(inliers) / max(1, len(observations))
        ocr_confidence = statistics.fmean(max(0.0, min(1.0, item.confidence)) for item, _ in inliers)
        confidence = min(1.0, 0.55 * consistency + 0.25 * sample_ratio + 0.20 * ocr_confidence)
        if confidence < self.min_confidence:
            return OSDTimeCalibrationResult(
                reported_start,
                reported_start,
                "low_confidence",
                confidence=confidence,
                sample_count=len(observations),
                valid_sample_count=len(valid),
                inlier_sample_count=len(inliers),
                observations=tuple(observations),
            )

        media_start = reported_start + timedelta(seconds=final_delta)
        logger.info(
            "OSD time calibration succeeded for %s: reported=%s media=%s offset=%.3fs confidence=%.3f samples=%s/%s",
            os.path.basename(video_path),
            reported_start.isoformat(),
            media_start.isoformat(),
            final_delta,
            confidence,
            len(inliers),
            len(observations),
        )
        return OSDTimeCalibrationResult(
            reported_start,
            media_start,
            "calibrated",
            confidence=confidence,
            offset_seconds=final_delta,
            sample_count=len(observations),
            valid_sample_count=len(valid),
            inlier_sample_count=len(inliers),
            observations=tuple(observations),
        )

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    def _load_frame(self, video_path: str, offset_seconds: float) -> np.ndarray:
        command = [
            self.ffmpeg_bin,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-i",
            video_path,
        ]
        if offset_seconds > 0:
            command.extend(["-ss", f"{offset_seconds:.3f}"])
        command.extend(["-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.frame_timeout_seconds, check=False)
        if completed.returncode != 0 or not completed.stdout:
            error = completed.stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(error or f"ffmpeg exited with {completed.returncode}")
        frame = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("ffmpeg returned an undecodable frame")
        return frame

    def _crop_and_preprocess(self, frame: np.ndarray) -> list[np.ndarray]:
        crop = self._crop_frame(frame)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        scale = 2.0 if gray.shape[1] >= 1280 else 3.0
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return [enhanced, cv2.bitwise_not(binary)]

    def _crop_frame(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self.roi
        crop = frame[int(height * y1):max(int(height * y1) + 1, int(height * y2)), int(width * x1):max(int(width * x1) + 1, int(width * x2))]
        if crop.size == 0:
            crop = frame
        return crop

    def _read_timestamp_text(self, frame: np.ndarray) -> tuple[str, float]:
        try:
            text, confidence = self._read_with_rapidocr(self._crop_frame(frame))
            parsed, _ = parse_osd_timestamp(text, datetime.now(self.timezone))
            if parsed is not None:
                return text, confidence
        except Exception as exc:
            logger.warning("RapidOCR OSD timestamp recognition failed, using Tesseract fallback: %s", exc)

        best_text = ""
        best_confidence = 0.0
        for image in self._crop_and_preprocess(frame):
            encoded, payload = cv2.imencode(".png", image)
            if not encoded:
                continue
            command = [
                self.tesseract_bin,
                "stdin",
                "stdout",
                "-l",
                "eng",
                "--psm",
                "6",
                "-c",
                "tessedit_char_whitelist=0123456789-/:. ",
                "tsv",
            ]
            completed = subprocess.run(command, input=payload.tobytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.ocr_timeout_seconds, check=False)
            if completed.returncode != 0:
                continue
            text, confidence = self._parse_tesseract_tsv(completed.stdout.decode("utf-8", errors="replace"))
            parsed, _ = parse_osd_timestamp(text, datetime.now(self.timezone))
            score = confidence + (1.0 if parsed is not None else 0.0)
            best_score = best_confidence + (1.0 if parse_osd_timestamp(best_text, datetime.now(self.timezone))[0] is not None else 0.0)
            if score > best_score:
                best_text, best_confidence = text, confidence
        return best_text, best_confidence

    def _read_with_rapidocr(self, image: np.ndarray) -> tuple[str, float]:
        global _RAPIDOCR_ENGINE

        if _RAPIDOCR_ENGINE is None:
            with _RAPIDOCR_LOAD_LOCK:
                if _RAPIDOCR_ENGINE is None:
                    from rapidocr import RapidOCR

                    _RAPIDOCR_ENGINE = RapidOCR()
        with _RAPIDOCR_RUN_LOCK:
            if self._rapidocr_timestamp_boxes:
                texts = []
                scores = []
                height, width = image.shape[:2]
                for x1, y1, x2, y2 in self._rapidocr_timestamp_boxes:
                    crop = image[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
                    if crop.size == 0:
                        continue
                    item_result = _RAPIDOCR_ENGINE(crop, use_det=False, use_cls=False, use_rec=True)
                    texts.extend(tuple(getattr(item_result, "txts", ()) or ()))
                    scores.extend(tuple(getattr(item_result, "scores", ()) or ()))
                result = None
            else:
                result = _RAPIDOCR_ENGINE(image)
                texts = list(tuple(getattr(result, "txts", ()) or ())) if result is not None else []
                scores = list(tuple(getattr(result, "scores", ()) or ())) if result is not None else []
                raw_boxes = getattr(result, "boxes", None) if result is not None else None
                boxes = tuple(raw_boxes) if raw_boxes is not None else ()
                timestamp_boxes = []
                now = datetime.now(self.timezone)
                for text, box in zip(texts, boxes):
                    normalized = _normalize_ocr_text(str(text or ""))
                    has_date = bool(_DATE_PATTERN.search(normalized) or _COMPACT_DATE_PATTERN.search(normalized))
                    parsed_time, _ = parse_osd_timestamp(normalized, now)
                    if not has_date and parsed_time is None:
                        continue
                    coordinates = np.asarray(box, dtype=float)
                    if coordinates.size < 4:
                        continue
                    x1, y1 = np.floor(coordinates.min(axis=0)).astype(int)
                    x2, y2 = np.ceil(coordinates.max(axis=0)).astype(int)
                    padding = 8
                    timestamp_boxes.append((x1 - padding, y1 - padding, x2 + padding, y2 + padding))
                self._rapidocr_timestamp_boxes = timestamp_boxes
        normalized_texts = [str(item or "").strip() for item in texts if str(item or "").strip()]
        valid_scores = [float(item) for item in scores if math.isfinite(float(item))]
        return " ".join(normalized_texts), statistics.fmean(valid_scores) if valid_scores else 0.0

    @staticmethod
    def _parse_tesseract_tsv(value: str) -> tuple[str, float]:
        words = []
        confidences = []
        for line in str(value or "").splitlines()[1:]:
            columns = line.split("\t")
            if len(columns) < 12:
                continue
            text = columns[11].strip()
            if not text:
                continue
            try:
                confidence = float(columns[10])
            except ValueError:
                confidence = -1.0
            words.append(text)
            if confidence >= 0:
                confidences.append(confidence / 100.0)
        return " ".join(words), statistics.fmean(confidences) if confidences else 0.0
