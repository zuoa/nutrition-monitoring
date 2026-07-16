#!/usr/bin/env python3
"""
本地视频分析测试脚本
用于测试视频帧提取效果，不依赖数据库/Celery

用法:
    python test_video_analyzer.py <视频路径> [输出目录] [选项]

示例:
    python test_video_analyzer.py /path/to/video.mp4 ./test_output --channel 5
    python test_video_analyzer.py /path/to/video.mp4 ./test_output --preset recall --event-scan-fps 15
    python test_video_analyzer.py /path/to/video.mp4 ./test_output --start-time "2026-05-14 08:30:00"
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 添加backend到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

try:
    import cv2
except ModuleNotFoundError:
    cv2 = None


PRESETS = {
    "balanced": {
        "FG_RATIO_THRESHOLD": 0.10,
        "LEGACY_QUICK_STABLE_FRAMES_MIN": 1,
        "LEGACY_MIN_EVENT_GAP_SECONDS": 0.8,
        "LEGACY_ANALYSIS_MAX_WIDTH": 960,
        "LEGACY_ANALYSIS_MAX_HEIGHT": 540,
    },
    "recall": {
        "FG_RATIO_THRESHOLD": 0.07,
        "LEGACY_QUICK_STABLE_FRAMES_MIN": 1,
        "LEGACY_MIN_EVENT_GAP_SECONDS": 0.8,
        "LEGACY_ANALYSIS_MAX_WIDTH": 1280,
        "LEGACY_ANALYSIS_MAX_HEIGHT": 720,
    },
    "fast": {
        "FG_RATIO_THRESHOLD": 0.12,
        "LEGACY_QUICK_STABLE_FRAMES_MIN": 2,
        "LEGACY_MIN_EVENT_GAP_SECONDS": 1.0,
        "LEGACY_ANALYSIS_MAX_WIDTH": 960,
        "LEGACY_ANALYSIS_MAX_HEIGHT": 540,
    },
}


def parse_video_start_time(value, timezone_name):
    """解析录像起始时间；无时区输入按配置的视频时区解释。"""
    if not value:
        return datetime.now(timezone.utc)

    text = value.strip()
    if not text:
        return datetime.now(timezone.utc)

    normalized = text[:-1] + '+00:00' if text.endswith('Z') else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None

    if parsed is None:
        formats = (
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y-%m-%d-%H-%M-%S',
            '%Y%m%d%H%M%S',
            '%Y-%m-%d',
            '%Y/%m/%d',
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if parsed is None:
        raise ValueError(
            '无法解析 --video-start-time，请使用类似 "2026-05-14 08:30:00"、'
            '"2026-05-14T08:30:00+08:00" 或 "20260514083000" 的格式'
        )

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f'无效的 --video-timezone: {timezone_name}') from exc

    return parsed


def draw_info(frame, text, y_pos=30, color=(0, 255, 0)):
    """在帧上绘制文字信息"""
    cv2.putText(frame, text, (10, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
    return frame


def draw_roi(frame, roi_region, color=(0, 255, 255)):
    """在帧上绘制ROI区域"""
    if not roi_region:
        return frame
    h, w = frame.shape[:2]
    x = max(0, min(roi_region.get('x', 0), w))
    y = max(0, min(roi_region.get('y', 0), h))
    roi_w = min(roi_region.get('w', w), w - x)
    roi_h = min(roi_region.get('h', h), h - y)
    cv2.rectangle(frame, (x, y), (x + roi_w, y + roi_h), color, 2)
    cv2.putText(frame, "ROI", (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    return frame


def visualize_detection(video_path, output_dir, events, analyzer, video_start_time):
    """可视化检测结果，保存带标记的帧"""
    vis_dir = os.path.join(output_dir, 'visualized')
    os.makedirs(vis_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"无法打开视频: {video_path}")
        return

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    for i, event in enumerate(events):
        # 定位到事件帧
        frame_no = event['frame_no']
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, frame = cap.read()

        if not ret:
            print(f"  警告: 无法读取帧 {frame_no}")
            continue

        # 绘制信息
        timestamp = event['captured_at'].strftime('%H:%M:%S')
        frame = draw_info(frame, f"Event {i+1}/{len(events)} - {timestamp}", 30, (0, 255, 0))
        frame = draw_info(frame, f"Diff Score: {event['diff_score']:.1f}", 60, (0, 255, 0))
        frame = draw_roi(frame, analyzer.roi_region)

        # 保存可视化结果
        vis_path = os.path.join(vis_dir, f"event_{i+1:03d}_{timestamp.replace(':', '-')}.jpg")
        cv2.imwrite(vis_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        print(f"  ✓ 保存可视化: {vis_path}")

    cap.release()


def save_detected_roi_preview(video_path, output_dir, analyzer):
    """保存检测到的结算区ROI预览图"""
    if not analyzer.roi_region:
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return

    ret, frame = cap.read()
    cap.release()
    if not ret:
        return

    preview = draw_roi(frame.copy(), analyzer.roi_region, (0, 165, 255))
    preview_path = os.path.join(output_dir, 'detected_roi_preview.jpg')
    cv2.imwrite(preview_path, preview, [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(f"结算区预览图: {preview_path}")


def save_scan_debug_csv(output_dir, analyzer):
    """导出扫描时序指标，便于分析为何没有切分"""
    scan_frames = getattr(analyzer, 'last_scan_frames', None) or []
    event_windows = getattr(analyzer, 'last_event_windows', None) or []
    if not scan_frames:
        return

    event_ranges = [
        (idx + 1, event.start_frame_no, event.end_frame_no)
        for idx, event in enumerate(event_windows)
    ]

    csv_path = os.path.join(output_dir, 'scan_debug.csv')
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'frame_no', 'ts', 'motion_score', 'fg_ratio', 'plate_changed_pixels',
            'object_ratio', 'plate_present', 'object_present', 'state',
            'stable_frame_streak', 'moving_frame_streak', 'event_window'
        ])
        for sample in scan_frames:
            event_id = ''
            for idx, start_frame, end_frame in event_ranges:
                if start_frame <= sample.frame_no <= end_frame:
                    event_id = idx
                    break
            writer.writerow([
                sample.frame_no,
                f'{sample.ts:.3f}',
                f'{sample.motion_score:.4f}',
                f'{sample.fg_ratio:.6f}',
                sample.plate_changed_pixels,
                f'{sample.object_ratio:.6f}',
                int(sample.plate_present),
                int(sample.object_present),
                sample.state,
                sample.stable_frame_streak,
                sample.moving_frame_streak,
                event_id,
            ])

    print(f"扫描调试CSV: {csv_path}")


def preview_video_info(video_path):
    """预览视频基本信息"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"错误: 无法打开视频 {video_path}")
        return None

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = frame_count / fps if fps > 0 else 0

    cap.release()

    print("=" * 60)
    print("视频信息:")
    print(f"  路径: {video_path}")
    print(f"  分辨率: {width}x{height}")
    print(f"  FPS: {fps:.2f}")
    print(f"  总帧数: {frame_count}")
    print(f"  时长: {duration:.1f}秒 ({duration/60:.1f}分钟)")
    print("=" * 60)

    return {'fps': fps, 'width': width, 'height': height}


def test_single_frame(video_path, roi_region=None, frame_offset_sec=1.0):
    """测试单帧，用于ROI调试"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    target_frame = int(frame_offset_sec * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("无法读取测试帧")
        return

    # 保存原图
    test_dir = './test_roi'
    os.makedirs(test_dir, exist_ok=True)
    cv2.imwrite(os.path.join(test_dir, 'original.jpg'), frame)

    # 绘制ROI
    frame_with_roi = draw_roi(frame.copy(), roi_region)
    cv2.imwrite(os.path.join(test_dir, 'with_roi.jpg'), frame_with_roi)

    # 提取并保存ROI区域
    if roi_region:
        h, w = frame.shape[:2]
        x = max(0, min(roi_region.get('x', 0), w))
        y = max(0, min(roi_region.get('y', 0), h))
        roi_w = min(roi_region.get('w', w), w - x)
        roi_h = min(roi_region.get('h', h), h - y)
        roi_frame = frame[y:y+roi_h, x:x+roi_w]
        cv2.imwrite(os.path.join(test_dir, 'roi_only.jpg'), roi_frame)
        print(f"\nROI测试图已保存到 {test_dir}/")
        print(f"  ROI区域: x={x}, y={y}, w={roi_w}, h={roi_h}")


def main():
    parser = argparse.ArgumentParser(description='本地测试视频分析效果')
    parser.add_argument('video_path', help='视频文件路径')
    parser.add_argument('output_dir', nargs='?', default='./test_output', help='输出目录 (默认: ./test_output)')
    parser.add_argument('--channel', '-c', default='test', help='通道ID (默认: test)')
    parser.add_argument('--preset', choices=sorted(PRESETS), default='balanced', help='预设: balanced均衡, recall优先召回, fast优先速度 (默认: balanced)')
    parser.add_argument('--event-scan-fps', type=float, default=15.0, help='事件扫描帧率 (默认: 15.0)')
    parser.add_argument('--fg-ratio-threshold', type=float, default=None, help='前景像素占ROI阈值；越低召回越高 (默认跟随 preset)')
    parser.add_argument('--legacy-analysis-max-width', type=int, default=None, help='legacy分析帧最大宽度，0表示不缩放 (默认跟随 preset)')
    parser.add_argument('--legacy-analysis-max-height', type=int, default=None, help='legacy分析帧最大高度，0表示不缩放 (默认跟随 preset)')
    parser.add_argument('--legacy-quick-stable-frames-min', type=int, default=None, help='短稳定兜底最少候选帧数；越低召回越高但误报更多 (默认跟随 preset)')
    parser.add_argument('--legacy-min-event-gap-seconds', type=float, default=None, help='连续输出事件的最小间隔秒数，0表示不限制 (默认跟随 preset)')
    parser.add_argument('--video-timezone', default='Asia/Shanghai', help='录像起始时间所属时区 (默认: Asia/Shanghai)')
    parser.add_argument(
        '--video-start-time',
        '--start-time',
        default=None,
        help='录像起始时间；支持 "2026-05-14 08:30:00"、"2026-05-14T08:30:00+08:00"、"20260514083000"；无时区时按 --video-timezone 解释',
    )
    parser.add_argument('--roi-x', type=int, help='ROI左上角X坐标')
    parser.add_argument('--roi-y', type=int, help='ROI左上角Y坐标')
    parser.add_argument('--roi-w', type=int, help='ROI宽度')
    parser.add_argument('--roi-h', type=int, help='ROI高度')
    parser.add_argument('--visualize', '-v', action='store_true', help='生成可视化标记图')
    parser.add_argument('--test-roi', action='store_true', help='只测试ROI区域，不运行完整分析')

    args = parser.parse_args()

    if cv2 is None:
        print("错误: 当前 Python 环境未安装 OpenCV，请先安装 backend/requirements.txt")
        print("示例: python3 -m pip install -r backend/requirements.txt")
        sys.exit(1)

    from app.services.video_analyzer import VideoAnalyzer

    # 检查视频文件
    if not os.path.exists(args.video_path):
        print(f"错误: 视频文件不存在: {args.video_path}")
        sys.exit(1)

    try:
        video_start_time = parse_video_start_time(args.video_start_time, args.video_timezone)
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)

    # 构建ROI配置
    roi_region = None
    if args.roi_x is not None and args.roi_y is not None:
        roi_region = {
            'x': args.roi_x,
            'y': args.roi_y,
            'w': args.roi_w or 640,
            'h': args.roi_h or 480
        }
        print(f"ROI设置: {roi_region}")

    # 如果只测试ROI
    if args.test_roi:
        test_single_frame(args.video_path, roi_region)
        return

    # 预览视频信息
    info = preview_video_info(args.video_path)
    if not info:
        sys.exit(1)

    # 如果指定了ROI但没有给宽高，默认使用视频中心区域
    if roi_region and (args.roi_w is None or args.roi_h is None):
        roi_region['w'] = int(info['width'] * 0.6)
        roi_region['h'] = int(info['height'] * 0.6)
        roi_region['x'] = (info['width'] - roi_region['w']) // 2
        roi_region['y'] = (info['height'] - roi_region['h']) // 2
        print(f"自动调整ROI到中心: {roi_region}")

    preset_config = dict(PRESETS[args.preset])
    if args.fg_ratio_threshold is not None:
        preset_config['FG_RATIO_THRESHOLD'] = args.fg_ratio_threshold
    if args.legacy_analysis_max_width is not None:
        preset_config['LEGACY_ANALYSIS_MAX_WIDTH'] = args.legacy_analysis_max_width
    if args.legacy_analysis_max_height is not None:
        preset_config['LEGACY_ANALYSIS_MAX_HEIGHT'] = args.legacy_analysis_max_height
    if args.legacy_quick_stable_frames_min is not None:
        preset_config['LEGACY_QUICK_STABLE_FRAMES_MIN'] = args.legacy_quick_stable_frames_min
    if args.legacy_min_event_gap_seconds is not None:
        preset_config['LEGACY_MIN_EVENT_GAP_SECONDS'] = args.legacy_min_event_gap_seconds

    config = {
        'ROI_REGION': roi_region,
        'VIDEO_TIMEZONE': args.video_timezone,
        'EVENT_SCAN_FPS': args.event_scan_fps,
        **preset_config,
    }

    visible_config = {
        'MODE': 'legacy',
        'PRESET': args.preset,
        'ROI_REGION': config['ROI_REGION'],
        'VIDEO_TIMEZONE': config['VIDEO_TIMEZONE'],
        'VIDEO_START_TIME': video_start_time.isoformat(),
        'EVENT_SCAN_FPS': config['EVENT_SCAN_FPS'],
        'LEGACY_ANALYSIS_MAX_WIDTH': config['LEGACY_ANALYSIS_MAX_WIDTH'],
        'LEGACY_ANALYSIS_MAX_HEIGHT': config['LEGACY_ANALYSIS_MAX_HEIGHT'],
        'LEGACY_QUICK_STABLE_FRAMES_MIN': config['LEGACY_QUICK_STABLE_FRAMES_MIN'],
        'LEGACY_MIN_EVENT_GAP_SECONDS': config['LEGACY_MIN_EVENT_GAP_SECONDS'],
        'FG_RATIO_THRESHOLD': config['FG_RATIO_THRESHOLD'],
    }

    print("\n分析配置:")
    for k, v in visible_config.items():
        print(f"  {k}: {v}")
    print()

    def run_once(output_dir):
        print("\n" + "=" * 60)
        print("开始分析视频: legacy")
        print(f"输出目录: {output_dir}")
        print("=" * 60)

        analyzer = VideoAnalyzer(config)
        start = time.perf_counter()

        try:
            results = analyzer.extract_frames(
                args.video_path,
                output_dir,
                video_start_time,
                args.channel
            )
        except Exception as e:
            print(f"分析失败: {e}")
            import traceback
            traceback.print_exc()
            raise

        elapsed = time.perf_counter() - start
        print("\n" + "=" * 60)
        print(f"分析完成: legacy，共检测到 {len(results)} 个事件，耗时 {elapsed:.2f} 秒")
        print("=" * 60)
        print(f"生效ROI: {analyzer.roi_region}")
        print(f"基线占比: {analyzer.object_ratio_baseline:.6f}")
        print(f"基线像素: {analyzer.object_pixels_baseline:.1f}")
        save_detected_roi_preview(args.video_path, output_dir, analyzer)
        save_scan_debug_csv(output_dir, analyzer)

        for i, r in enumerate(results):
            print(f"\n事件 {i+1}:")
            print(f"  时间戳: {r['captured_at'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  帧号: best={r.get('frame_no')} start={r.get('start_frame_no')} end={r.get('end_frame_no')} peak={r.get('peak_frame_no')}")
            print(f"  窗口跨度: {r.get('window_span_seconds') or 0:.2f}s / {r.get('window_frame_span', 0)}帧")
            print(f"  best 距窗口开始: {r.get('best_offset_seconds_from_start') or 0:.2f}s / {r.get('best_offset_frames_from_start', 0)}帧")
            print(f"  差分分数: {r['diff_score']:.2f}")
            print(f"  最优帧得分: {r.get('best_score', 0.0):.4f}")
            print(f"  低质量兜底: {r.get('low_quality', False)} {r.get('quality_note', '')}".rstrip())
            print(f"  通道: {r['channel_id']}")
            print(f"  图片路径: {r['image_path']}")

        if args.visualize and results:
            print("\n生成可视化结果...")
            visualize_detection(args.video_path, output_dir, results, analyzer, video_start_time)

        print(f"\n所有结果已保存到: {output_dir}")
        return {
            'method': 'legacy',
            'output_dir': output_dir,
            'event_count': len(results),
            'elapsed': elapsed,
        }

    try:
        run_once(args.output_dir)
    except Exception:
        sys.exit(1)


if __name__ == '__main__':
    main()
