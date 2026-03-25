import cv2
import numpy as np
import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class VideoAnalyzer:
    """Extracts dish-placement frames from cashier-counter video."""

    def __init__(self, config: dict):
        self.extract_fps = float(config.get("EXTRACT_FPS", 2))
        self.diff_threshold = int(config.get("DIFF_THRESHOLD", 30))
        self.min_event_duration_s = float(config.get("MIN_EVENT_DURATION_S", 0.5))
        self.stable_frame_offset_s = float(config.get("STABLE_FRAME_OFFSET_S", 1.0))
        self.min_interval_s = float(config.get("MIN_INTERVAL_S", 3.0))
        self.alert_no_event_minutes = int(config.get("ALERT_NO_EVENT_MINUTES", 30))
        # ROI: {x, y, w, h} in pixels; None means full frame
        self.roi_region: Optional[dict] = config.get("ROI_REGION")

    def extract_frames(
        self,
        video_path: str,
        output_dir: str,
        video_start_time: datetime,
        channel_id: str,
    ) -> list[dict]:
        """
        Extract cashier event frames from video.
        Returns list of {image_path, captured_at, diff_score, channel_id}
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        os.makedirs(output_dir, exist_ok=True)

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25
        sample_interval = max(1, int(video_fps / self.extract_fps))

        prev_gray: Optional[np.ndarray] = None
        events = []  # {frame_no, timestamp, diff_score}
        last_event_ts = -999
        event_start_ts = None
        event_peak_diff = 0
        event_peak_frame = None
        frame_no = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                current_ts = frame_no / video_fps  # seconds from video start

                if frame_no % sample_interval == 0:
                    roi_frame = self._apply_roi(frame)
                    gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)

                    if prev_gray is not None:
                        diff = cv2.absdiff(prev_gray, gray)
                        mean_diff = float(np.mean(diff))

                        if mean_diff > self.diff_threshold:
                            if event_start_ts is None:
                                event_start_ts = current_ts
                            if mean_diff > event_peak_diff:
                                event_peak_diff = mean_diff
                                event_peak_frame = (frame_no, current_ts, frame.copy())
                        else:
                            if (
                                event_start_ts is not None
                                and (current_ts - event_start_ts) >= self.min_event_duration_s
                                and (current_ts - last_event_ts) >= self.min_interval_s
                            ):
                                events.append({
                                    "frame_no": event_peak_frame[0],
                                    "video_ts": event_peak_frame[1] + self.stable_frame_offset_s,
                                    "diff_score": event_peak_diff,
                                    "frame": event_peak_frame[2],
                                })
                                last_event_ts = current_ts

                            event_start_ts = None
                            event_peak_diff = 0
                            event_peak_frame = None

                    prev_gray = gray

                frame_no += 1
        finally:
            cap.release()

        # Save extracted frames and compute timestamps
        results = []
        seen_seconds = {}

        for ev in events:
            if video_start_time.tzinfo is None:
                video_start_time = video_start_time.replace(tzinfo=timezone.utc)
            seconds_offset = ev["video_ts"]
            from datetime import timedelta
            frame_time = video_start_time + timedelta(seconds=seconds_offset)

            ts_key = int(seconds_offset)
            is_candidate = ts_key in seen_seconds
            seen_seconds[ts_key] = True

            # Save frame as JPEG with format: 通道号_年-月-日-时-分-秒.jpg
            frame_filename = f"{channel_id}_{frame_time.strftime('%Y-%m-%d-%H-%M-%S')}.jpg"
            frame_path = os.path.join(output_dir, frame_filename)
            # Handle duplicate filenames by appending milliseconds if needed
            if os.path.exists(frame_path):
                frame_filename = f"{channel_id}_{frame_time.strftime('%Y-%m-%d-%H-%M-%S')}-{frame_time.microsecond // 1000:03d}.jpg"
                frame_path = os.path.join(output_dir, frame_filename)
            cv2.imwrite(frame_path, ev["frame"], [cv2.IMWRITE_JPEG_QUALITY, 85])

            results.append({
                "image_path": frame_path,
                "captured_at": frame_time,
                "diff_score": ev["diff_score"],
                "channel_id": channel_id,
                "is_candidate": is_candidate,
            })

        logger.info(f"Extracted {len(results)} frames from {video_path}")

        # Check for alert: no events in 30+ minute windows
        if video_start_time and len(results) == 0:
            logger.warning(f"No events detected in {video_path} — possible camera issue")

        return results

    def _apply_roi(self, frame: np.ndarray) -> np.ndarray:
        if not self.roi_region:
            return frame
        r = self.roi_region
        x, y, w, h = r.get("x", 0), r.get("y", 0), r.get("w", frame.shape[1]), r.get("h", frame.shape[0])
        x = max(0, min(x, frame.shape[1]))
        y = max(0, min(y, frame.shape[0]))
        w = min(w, frame.shape[1] - x)
        h = min(h, frame.shape[0] - y)
        return frame[y:y + h, x:x + w]
