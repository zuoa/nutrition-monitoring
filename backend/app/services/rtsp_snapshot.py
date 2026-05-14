import os
import subprocess
import tempfile
from urllib.parse import quote


def build_rtsp_url(
    *,
    host: str,
    username: str,
    password: str,
    stream_id: str,
    rtsp_port: int = 554,
) -> str:
    normalized_host = str(host or "").strip()
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_host:
        raise ValueError("RTSP 抓拍缺少设备地址")
    if not normalized_stream_id:
        raise ValueError("RTSP 抓拍缺少码流通道")

    credentials = ""
    if username or password:
        credentials = f"{quote(str(username or ''), safe='')}:{quote(str(password or ''), safe='')}@"
    return f"rtsp://{credentials}{normalized_host}:{int(rtsp_port or 554)}/Streaming/channels/{normalized_stream_id}"


def capture_rtsp_snapshot(
    rtsp_url: str,
    *,
    ffmpeg_bin: str = "ffmpeg",
    timeout: int = 20,
) -> bytes:
    normalized_url = str(rtsp_url or "").strip()
    if not normalized_url:
        raise ValueError("RTSP 抓拍地址不能为空")

    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as output_file:
            output_path = output_file.name

        cmd = [
            ffmpeg_bin or "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-rtsp_transport",
            "tcp",
            "-i",
            normalized_url,
            "-vframes",
            "1",
            "-q:v",
            "2",
            output_path,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"ffmpeg RTSP 抓拍失败: {detail or f'exit code {result.returncode}'}")
        with open(output_path, "rb") as snapshot_file:
            content = snapshot_file.read()
        if not content:
            raise ValueError("ffmpeg RTSP 抓拍未生成图片")
        return content
    except FileNotFoundError as exc:
        raise ValueError("未找到 ffmpeg，请先在后端运行环境安装 ffmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"ffmpeg RTSP 抓拍超时（{timeout} 秒）") from exc
    finally:
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
