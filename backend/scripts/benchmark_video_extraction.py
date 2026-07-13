#!/usr/bin/env python3
"""Benchmark extraction backends and verify event recall against OpenCV.

Run inside the backend container so the FFmpeg build, NVIDIA runtime, and CPU
limits match production. Input videos are never modified.
"""

import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.video_analyzer import VideoAnalyzer  # noqa: E402


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".dav"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Video files or directories containing videos")
    parser.add_argument("--backends", default="nvdec,ffmpeg_cpu")
    parser.add_argument("--sizes", default="1280x720,960x540,640x360")
    parser.add_argument("--scan-fps", type=float, default=12.0)
    parser.add_argument("--channel-id", default="benchmark")
    parser.add_argument("--event-tolerance", type=float, default=1.0)
    parser.add_argument("--target-seconds", type=float, default=30.0)
    parser.add_argument("--output", default="video-extraction-benchmark.json")
    return parser.parse_args()


def discover_videos(inputs: list[str]) -> list[Path]:
    videos: list[Path] = []
    for raw_path in inputs:
        path = Path(raw_path).expanduser().resolve()
        if path.is_file():
            videos.append(path)
        elif path.is_dir():
            videos.extend(
                item for item in sorted(path.rglob("*"))
                if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS
            )
    unique = list(dict.fromkeys(videos))
    if not unique:
        raise SystemExit("No video files found")
    return unique


def parse_sizes(value: str) -> list[tuple[int, int]]:
    result = []
    for item in value.split(","):
        width_text, height_text = item.strip().lower().split("x", 1)
        result.append((max(1, int(width_text)), max(1, int(height_text))))
    return result


def event_offsets(frames: list[dict], start_at: datetime) -> list[float]:
    return sorted((frame["captured_at"] - start_at).total_seconds() for frame in frames)


def event_recall(baseline: list[float], candidate: list[float], tolerance: float) -> float:
    if not baseline:
        return 1.0
    remaining = list(candidate)
    matched = 0
    for expected in baseline:
        options = [
            (abs(actual - expected), index)
            for index, actual in enumerate(remaining)
            if abs(actual - expected) <= tolerance
        ]
        if not options:
            continue
        _, best_index = min(options)
        remaining.pop(best_index)
        matched += 1
    return matched / len(baseline)


def run_variant(
    video: Path,
    backend: str,
    size: tuple[int, int],
    args: argparse.Namespace,
    start_at: datetime,
) -> dict:
    progress: dict = {}

    def capture_progress(value: dict) -> None:
        progress.update(value or {})

    config = {
        "VIDEO_EXTRACT_DECODE_BACKEND": backend,
        "EVENT_SCAN_FPS": args.scan_fps,
        "LEGACY_ANALYSIS_MAX_WIDTH": size[0],
        "LEGACY_ANALYSIS_MAX_HEIGHT": size[1],
        "VIDEO_TIMEZONE": "Asia/Shanghai",
    }
    with tempfile.TemporaryDirectory(prefix="video-extract-benchmark-") as output_dir:
        started = time.perf_counter()
        frames = VideoAnalyzer(config).extract_frames(
            str(video),
            output_dir,
            start_at,
            args.channel_id,
            progress_callback=capture_progress,
        )
        elapsed = time.perf_counter() - started
    effective_strategies = {
        str(frame.get("decoder_strategy") or frame.get("extraction_strategy") or "")
        for frame in frames
    }
    effective_strategies.add(str(progress.get("extract_strategy") or ""))
    return {
        "video": str(video),
        "requested_backend": backend,
        "size": f"{size[0]}x{size[1]}",
        "elapsed_seconds": round(elapsed, 3),
        "event_count": len(frames),
        "event_offsets": event_offsets(frames, start_at),
        "effective_strategies": sorted(effective_strategies - {""}),
        "progress": progress,
    }


def percentile95(values: list[float]) -> float:
    if len(values) <= 1:
        return values[0] if values else 0.0
    ordered = sorted(values)
    rank = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return ordered[rank]


def summarize(results: list[dict], target_seconds: float) -> tuple[list[dict], dict | None]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for result in results:
        if result.get("baseline") or result.get("error"):
            continue
        groups.setdefault((result["requested_backend"], result["size"]), []).append(result)

    summaries = []
    for (backend, size), items in groups.items():
        elapsed_values = [float(item["elapsed_seconds"]) for item in items]
        recalls = [float(item["event_recall"]) for item in items]
        effective = sorted({strategy for item in items for strategy in item["effective_strategies"]})
        summary = {
            "backend": backend,
            "size": size,
            "video_count": len(items),
            "mean_seconds": round(statistics.fmean(elapsed_values), 3),
            "p95_seconds": round(percentile95(elapsed_values), 3),
            "minimum_event_recall": round(min(recalls), 6),
            "effective_strategies": effective,
        }
        summary["accepted"] = (
            summary["p95_seconds"] <= target_seconds
            and summary["minimum_event_recall"] >= 1.0
            and (backend != "nvdec" or effective == ["ffmpeg_nvdec"])
        )
        summaries.append(summary)

    def sort_key(item: dict) -> tuple[int, int]:
        width, height = (int(part) for part in item["size"].split("x", 1))
        backend_priority = {"nvdec": 0, "ffmpeg_cpu": 1}.get(item["backend"], 2)
        return backend_priority, -(width * height)

    summaries.sort(key=sort_key)
    recommendation = next((item for item in summaries if item["accepted"]), None)
    return summaries, recommendation


def main() -> int:
    args = parse_args()
    videos = discover_videos(args.inputs)
    backends = [item.strip() for item in args.backends.split(",") if item.strip()]
    sizes = parse_sizes(args.sizes)
    start_at = datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    results: list[dict] = []
    baselines: dict[str, list[float]] = {}

    for video in videos:
        try:
            baseline = run_variant(video, "opencv", (1280, 720), args, start_at)
            baseline["baseline"] = True
            baselines[str(video)] = baseline["event_offsets"]
            results.append(baseline)
        except Exception as exc:
            results.append({"video": str(video), "baseline": True, "error": str(exc)})
            continue

        for backend in backends:
            for size in sizes:
                try:
                    result = run_variant(video, backend, size, args, start_at)
                    result["event_recall"] = event_recall(
                        baselines[str(video)],
                        result["event_offsets"],
                        args.event_tolerance,
                    )
                    results.append(result)
                except Exception as exc:
                    results.append({
                        "video": str(video),
                        "requested_backend": backend,
                        "size": f"{size[0]}x{size[1]}",
                        "error": str(exc),
                    })

    summaries, recommendation = summarize(results, args.target_seconds)
    report = {
        "target_seconds": args.target_seconds,
        "event_tolerance_seconds": args.event_tolerance,
        "results": results,
        "summaries": summaries,
        "recommendation": recommendation,
    }
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "recommendation": recommendation}, ensure_ascii=False))
    return 0 if recommendation is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
