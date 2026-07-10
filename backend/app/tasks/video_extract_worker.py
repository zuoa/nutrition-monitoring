import json
import sys
import traceback
from datetime import datetime

import cv2

from app.services.video_analyzer import VideoAnalyzer


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _emit(message: dict) -> None:
    print(json.dumps(message, ensure_ascii=False, default=_json_default), flush=True)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        cfg = payload["cfg"]
        video_path = payload["video_path"]
        output_dir = payload["output_dir"]
        video_start = datetime.fromisoformat(payload["video_start"])
        channel_id = payload["channel_id"]
        try:
            cv2.setNumThreads(max(1, int(cfg.get("VIDEO_EXTRACT_CPU_THREADS_PER_JOB", 1))))
        except (AttributeError, TypeError, ValueError):
            pass

        def progress_callback(progress: dict) -> None:
            _emit({"type": "progress", "progress": progress or {}})

        frames = VideoAnalyzer(cfg).extract_frames(
            video_path,
            output_dir,
            video_start,
            channel_id,
            progress_callback=progress_callback,
        )
        _emit({"type": "result", "frames": frames})
        return 0
    except BaseException as exc:
        _emit({
            "type": "error",
            "error": str(exc) or exc.__class__.__name__,
            "traceback": traceback.format_exc(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
