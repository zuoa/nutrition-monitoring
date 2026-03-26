#!/usr/bin/env python3
"""
本地视频分析测试脚本
用于测试视频帧提取效果，不依赖数据库/Celery

用法:
    python test_video_analyzer.py <视频路径> [输出目录] [选项]

示例:
    python test_video_analyzer.py /path/to/video.mp4 ./test_output --channel 5
    python test_video_analyzer.py /path/to/video.mp4 ./test_output --motion-pixel-threshold 25 --fg-ratio-threshold 0.15
"""

import argparse
import csv
import cv2
import json
import os
import sys
from datetime import datetime, timezone

# 添加backend到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from app.services.video_analyzer import VideoAnalyzer


def detect_roi_auto(video_path, save_path=None):
    """根据首帧橙色结算区边框自动推导矩形 ROI。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"无法打开视频进行自动 ROI 检测: {video_path}")
        return None

    ret, frame = cap.read()
    cap.release()
    if not ret:
        print("自动 ROI 检测失败: 无法读取首帧")
        return None

    h, w = frame.shape[:2]
    total_pixels = h * w

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, (5, 100, 100), (15, 255, 255))
    mask2 = cv2.inRange(hsv, (170, 100, 100), (180, 255, 255))
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        print("自动 ROI 检测失败: 未找到橙色轮廓")
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) <= total_pixels * 0.01:
        print("自动 ROI 检测失败: 橙色区域过小")
        return None

    hull = cv2.convexHull(contour)
    epsilon = 0.015 * cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, epsilon, True)
    polygon = approx.reshape(-1, 2).tolist()

    x, y, roi_w, roi_h = cv2.boundingRect(contour)
    roi = {"x": int(x), "y": int(y), "w": int(roi_w), "h": int(roi_h)}
    print(f"自动 ROI: {roi}")

    if save_path:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump({"polygon": polygon, "source": "auto_orange_border"}, f, ensure_ascii=False, indent=2)
        print(f"自动 ROI 多边形已保存: {save_path}")

    return roi, polygon


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
            'object_ratio', 'plate_present', 'tray_present', 'event_window'
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
                int(sample.tray_present),
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
    parser.add_argument('--analysis-method', default='legacy', choices=['tray_selector', 'legacy'], help='分析实现 (默认: legacy)')
    parser.add_argument('--motion-pixel-threshold', '-d', type=int, default=25, help='帧差像素阈值 (默认: 25)')
    parser.add_argument('--motion-ratio-threshold', type=float, default=0.015, help='ROI运动像素占比阈值 (默认: 0.015)')
    parser.add_argument('--video-timezone', default='Asia/Shanghai', help='录像起始时间所属时区 (默认: Asia/Shanghai)')
    parser.add_argument('--stable-enter', type=int, default=8, help='进入稳定态连续静止帧数 (默认: 8)')
    parser.add_argument('--stable-exit', type=int, default=5, help='退出稳定态连续运动帧数 (默认: 5)')
    parser.add_argument('--bg-history', type=int, default=500, help='MOG2历史帧数 (默认: 500)')
    parser.add_argument('--bg-var-threshold', type=float, default=16.0, help='MOG2方差阈值 (默认: 16)')
    parser.add_argument('--bg-warmup-frames', type=int, default=500, help='背景预热帧数配置 (默认: 500)')
    parser.add_argument('--bg-empty-learning-rate', type=float, default=0.002, help='空台面稳定期背景更新学习率 (默认: 0.002)')
    parser.add_argument('--fg-ratio-threshold', type=float, default=0.15, help='前景像素占ROI阈值 (默认: 0.15)')
    parser.add_argument('--fg-min-component-area', type=int, default=1500, help='最小连通域面积 (默认: 1500)')
    parser.add_argument('--plate-min-area-ratio', type=float, default=0.12, help='判定餐盘的最小面积占比 (默认: 0.12)')
    parser.add_argument('--plate-max-area-ratio', type=float, default=0.85, help='判定餐盘的最大面积占比 (默认: 0.85)')
    parser.add_argument('--plate-center-max-ratio', type=float, default=0.95, help='判定餐盘的最大中心偏移比 (默认: 0.95)')
    parser.add_argument('--plate-edge-touch-max-ratio', type=float, default=0.25, help='判定餐盘的最大边缘接触比 (默认: 0.25)')
    parser.add_argument('--quick-stable-frames-min', type=int, default=2, help='短稳定窗口兜底所需最少静止帧数 (默认: 2)')
    parser.add_argument('--stable-present-frames-min', type=int, default=1, help='稳定期连续有盘最小帧数 (默认: 1)')
    parser.add_argument('--stable-sample-interval', type=int, default=3, help='稳定期采帧间隔 (默认: 3)')
    parser.add_argument('--blur-kernel-size', type=int, default=5, help='运动检测高斯模糊核大小 (默认: 5)')
    parser.add_argument('--morph-open-kernel', type=int, default=3, help='前景开运算核大小 (默认: 3)')
    parser.add_argument('--morph-close-kernel', type=int, default=7, help='前景闭运算核大小 (默认: 7)')
    parser.add_argument('--tray-orange-ratio-threshold', type=float, default=0.05, help='托盘橙色占比阈值 (默认: 0.05)')
    parser.add_argument('--tray-center-margin', type=float, default=0.15, help='托盘重心中心边距比例 (默认: 0.15)')
    parser.add_argument('--tray-motion-threshold', type=int, default=500, help='托盘静止判定运动像素阈值 (默认: 500)')
    parser.add_argument('--tray-window-size', type=int, default=20, help='托盘候选缓冲窗口大小 (默认: 20)')
    parser.add_argument('--tray-min-laplacian', type=float, default=50.0, help='托盘锁定最小清晰度阈值 (默认: 50)')
    parser.add_argument('--tray-roi-expand', type=int, default=0, help='托盘检测ROI外扩像素 (默认: 0)')
    parser.add_argument('--tray-leave-motion-threshold', type=int, default=1500, help='托盘离开高运动阈值 (默认: 1500)')
    parser.add_argument('--tray-leave-motion-frames', type=int, default=6, help='托盘离开连续高运动帧数 (默认: 6)')
    parser.add_argument('--tray-dedup-threshold', type=float, default=0.75, help='托盘去重直方图相似度阈值 (默认: 0.75)')
    parser.add_argument('--auto-roi', action='store_true', help='从首帧自动检测橙色结算区 ROI')
    parser.add_argument('--auto-roi-save', default=None, help='保存自动 ROI 多边形 JSON 的路径')
    parser.add_argument('--roi-x', type=int, help='ROI左上角X坐标')
    parser.add_argument('--roi-y', type=int, help='ROI左上角Y坐标')
    parser.add_argument('--roi-w', type=int, help='ROI宽度')
    parser.add_argument('--roi-h', type=int, help='ROI高度')
    parser.add_argument('--visualize', '-v', action='store_true', help='生成可视化标记图')
    parser.add_argument('--test-roi', action='store_true', help='只测试ROI区域，不运行完整分析')

    args = parser.parse_args()

    # 检查视频文件
    if not os.path.exists(args.video_path):
        print(f"错误: 视频文件不存在: {args.video_path}")
        sys.exit(1)

    # 构建ROI配置
    roi_region = None
    roi_polygon = None
    if args.roi_x is not None and args.roi_y is not None:
        roi_region = {
            'x': args.roi_x,
            'y': args.roi_y,
            'w': args.roi_w or 640,
            'h': args.roi_h or 480
        }
        print(f"ROI设置: {roi_region}")
    elif args.auto_roi:
        detected = detect_roi_auto(args.video_path, args.auto_roi_save)
        if detected is not None:
            roi_region, roi_polygon = detected

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

    # 构建配置
    config = {
        'ROI_REGION': roi_region,
        'ROI_POLYGON': roi_polygon,
        'VIDEO_TIMEZONE': args.video_timezone,
        'VIDEO_ANALYSIS_METHOD': args.analysis_method,
        'MOTION_PIXEL_DELTA_THRESHOLD': args.motion_pixel_threshold,
        'MOTION_RATIO_THRESHOLD': args.motion_ratio_threshold,
        'STABLE_FRAMES_ENTER': args.stable_enter,
        'STABLE_FRAMES_EXIT': args.stable_exit,
        'BG_HISTORY': args.bg_history,
        'BG_VAR_THRESHOLD': args.bg_var_threshold,
        'BG_WARMUP_FRAMES': args.bg_warmup_frames,
        'BG_EMPTY_LEARNING_RATE': args.bg_empty_learning_rate,
        'FG_RATIO_THRESHOLD': args.fg_ratio_threshold,
        'FG_MIN_COMPONENT_AREA': args.fg_min_component_area,
        'PLATE_MIN_AREA_RATIO': args.plate_min_area_ratio,
        'PLATE_MAX_AREA_RATIO': args.plate_max_area_ratio,
        'PLATE_CENTER_MAX_RATIO': args.plate_center_max_ratio,
        'PLATE_EDGE_TOUCH_MAX_RATIO': args.plate_edge_touch_max_ratio,
        'QUICK_STABLE_FRAMES_MIN': args.quick_stable_frames_min,
        'STABLE_PRESENT_FRAMES_MIN': args.stable_present_frames_min,
        'STABLE_SAMPLE_INTERVAL': args.stable_sample_interval,
        'BLUR_KERNEL_SIZE': args.blur_kernel_size,
        'MORPH_OPEN_KERNEL': args.morph_open_kernel,
        'MORPH_CLOSE_KERNEL': args.morph_close_kernel,
        'TRAY_ORANGE_RATIO_THRESHOLD': args.tray_orange_ratio_threshold,
        'TRAY_CENTER_MARGIN': args.tray_center_margin,
        'TRAY_MOTION_THRESHOLD': args.tray_motion_threshold,
        'TRAY_WINDOW_SIZE': args.tray_window_size,
        'TRAY_MIN_LAPLACIAN': args.tray_min_laplacian,
        'TRAY_ROI_EXPAND': args.tray_roi_expand,
        'TRAY_LEAVE_MOTION_THRESHOLD': args.tray_leave_motion_threshold,
        'TRAY_LEAVE_MOTION_FRAMES': args.tray_leave_motion_frames,
        'TRAY_DEDUP_THRESHOLD': args.tray_dedup_threshold,
    }

    visible_config = {
        'VIDEO_ANALYSIS_METHOD': config['VIDEO_ANALYSIS_METHOD'],
        'ROI_REGION': config['ROI_REGION'],
        'ROI_POLYGON': config['ROI_POLYGON'],
        'VIDEO_TIMEZONE': config['VIDEO_TIMEZONE'],
        'MOTION_PIXEL_DELTA_THRESHOLD': config['MOTION_PIXEL_DELTA_THRESHOLD'],
        'MOTION_RATIO_THRESHOLD': config['MOTION_RATIO_THRESHOLD'],
        'STABLE_FRAMES_ENTER': config['STABLE_FRAMES_ENTER'],
        'STABLE_FRAMES_EXIT': config['STABLE_FRAMES_EXIT'],
        'FG_RATIO_THRESHOLD': config['FG_RATIO_THRESHOLD'],
        'FG_MIN_COMPONENT_AREA': config['FG_MIN_COMPONENT_AREA'],
        'PLATE_MIN_AREA_RATIO': config['PLATE_MIN_AREA_RATIO'],
        'PLATE_MAX_AREA_RATIO': config['PLATE_MAX_AREA_RATIO'],
        'QUICK_STABLE_FRAMES_MIN': config['QUICK_STABLE_FRAMES_MIN'],
        'STABLE_PRESENT_FRAMES_MIN': config['STABLE_PRESENT_FRAMES_MIN'],
        'STABLE_SAMPLE_INTERVAL': config['STABLE_SAMPLE_INTERVAL'],
        'TRAY_ORANGE_RATIO_THRESHOLD': config['TRAY_ORANGE_RATIO_THRESHOLD'],
        'TRAY_MOTION_THRESHOLD': config['TRAY_MOTION_THRESHOLD'],
        'TRAY_MIN_LAPLACIAN': config['TRAY_MIN_LAPLACIAN'],
        'TRAY_LEAVE_MOTION_THRESHOLD': config['TRAY_LEAVE_MOTION_THRESHOLD'],
        'TRAY_LEAVE_MOTION_FRAMES': config['TRAY_LEAVE_MOTION_FRAMES'],
    }

    print("\n分析配置:")
    for k, v in visible_config.items():
        print(f"  {k}: {v}")
    print()

    # 创建分析器
    analyzer = VideoAnalyzer(config)
    print(f"自动结算区检测: {analyzer.auto_detect_settlement_roi}")

    # 运行分析
    print("开始分析视频...")
    video_start_time = datetime.now(timezone.utc)

    try:
        results = analyzer.extract_frames(
            args.video_path,
            args.output_dir,
            video_start_time,
            args.channel
        )
    except Exception as e:
        print(f"分析失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 输出结果
    print("\n" + "=" * 60)
    print(f"分析完成! 共检测到 {len(results)} 个事件")
    print("=" * 60)
    print(f"生效ROI: {analyzer.roi_region}")
    print(f"基线占比: {analyzer.object_ratio_baseline:.6f}")
    print(f"基线像素: {analyzer.object_pixels_baseline:.1f}")
    save_detected_roi_preview(args.video_path, args.output_dir, analyzer)
    save_scan_debug_csv(args.output_dir, analyzer)

    for i, r in enumerate(results):
        print(f"\n事件 {i+1}:")
        print(f"  时间戳: {r['captured_at'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  差分分数: {r['diff_score']:.2f}")
        print(f"  最优帧得分: {r.get('best_score', 0.0):.4f}")
        print(f"  低质量兜底: {r.get('low_quality', False)} {r.get('quality_note', '')}".rstrip())
        print(f"  通道: {r['channel_id']}")
        print(f"  图片路径: {r['image_path']}")

    # 可视化
    if args.visualize and results:
        print("\n生成可视化结果...")
        visualize_detection(args.video_path, args.output_dir, results, analyzer, video_start_time)

    print(f"\n所有结果已保存到: {args.output_dir}")


if __name__ == '__main__':
    main()
