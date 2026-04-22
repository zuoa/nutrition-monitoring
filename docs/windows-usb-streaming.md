# Windows USB 摄像头接入智能演示

当前智能演示的“实时流”模式已经从 `go2rtc` 切换为 `MediaMTX + WHEP`。推荐链路如下：

`USB 摄像头 -> FFmpeg -> RTSP 发布到 MediaMTX -> Demo 页面预览并截图分析`

## 1. 启动 MediaMTX

仓库根目录已经提供 [mediamtx.yml](/Users/yujian/Code/py/nutrition-monitoring/mediamtx.yml:1)。

开发环境可以直接启动整套容器：

```bash
make up
```

如果浏览器和 `MediaMTX` 不在同一台机器，编辑 `mediamtx.yml` 的 `webrtcAdditionalHosts`，加入浏览器访问这台服务时使用的 LAN IP 或域名。

## 2. 找到 Windows 摄像头设备名

先用 FFmpeg 列出 DirectShow 设备：

```bash
ffmpeg -list_devices true -f dshow -i dummy
```

记下摄像头名称，例如 `USB Camera`。

## 3. 把 USB 摄像头发布到 MediaMTX

下面示例把摄像头发布成 `camera1` 这一路流：

```bash
ffmpeg ^
  -f dshow ^
  -rtbufsize 512M ^
  -i video="USB Camera" ^
  -an ^
  -c:v libx264 ^
  -preset ultrafast ^
  -tune zerolatency ^
  -pix_fmt yuv420p ^
  -profile:v baseline ^
  -bf 0 ^
  -g 30 ^
  -f rtsp ^
  -rtsp_transport tcp ^
  rtsp://localhost:8554/camera1
```

说明：

- `-profile:v baseline` 和 `-bf 0` 用来避免 WebRTC 常见的 H.264 兼容问题
- `-rtsp_transport tcp` 与当前 [mediamtx.yml](/Users/yujian/Code/py/nutrition-monitoring/mediamtx.yml:1) 的 `rtspTransports: [tcp]` 保持一致
- 如果你想换流名，把结尾的 `camera1` 改成别的名字即可

## 4. 在智能演示里预览

1. 打开前端“智能演示”页面
2. 选择“实时流”
3. 在“流名称”里填写 `camera1`
4. 点击“建立预览”
5. 画面出现后，点击“截图并分析”

## 5. 常见问题

- 页面能打开但预览失败：先确认 FFmpeg 推流命令仍在运行，且流名和页面里填写的一致
- 同机可看，局域网其他电脑不能看：补充 `mediamtx.yml` 里的 `webrtcAdditionalHosts`
- 画面延迟或失败明显：优先检查 Windows 防火墙是否拦截了 `8189/tcp` 和 `8189/udp`
