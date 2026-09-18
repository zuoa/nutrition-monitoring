# 外部学生运动数据推送接口

第三方向本系统推送运动结果，以及可选的抓拍人脸和运动视频。接口采用运动数据协议 `v1.1`、文件上传协议 `v1.0`。`person_id` 就是学生学号，对应 `students.student_no`，必须以字符串传递，保留前导零。

## 接入配置

在部署环境设置：

```dotenv
SPORTS_PUSH_CHECK_STRING=替换为双方约定的随机密钥
SPORTS_PUSH_ALLOWED_SCHOOL_IDS=school-1
SPORTS_PUSH_TIMESTAMP_TOLERANCE_SECONDS=300
```

然后将以下信息提供给第三方：

| 用途 | 方法与地址 |
| --- | --- |
| 运动数据接收 | `POST https://你的域名/api/v1/external/sports/results` |
| 文件接收（可选） | `POST https://你的域名/api/v1/external/sports/files` |
| 校验密钥 | 双方约定的 `check_string`，两个接口共用 |

这些回调无需用户登录或 JWT，使用协议签名鉴权。`SPORTS_PUSH_CHECK_STRING` 为空时两个接口返回 HTTP 503，不接收数据。密钥仅配置于服务端部署环境。

`SPORTS_PUSH_ALLOWED_SCHOOL_IDS` 是逗号分隔的外部学校 ID 白名单；为空时接受所有签名有效的学校。外部 `school_id` 会原样保存，不当作本系统 `schools.id`。当前学生表的学号全局唯一，学生关联沿用这一约束；多学校共用实例时需保证学号不重复。

### 数据库和文件目录

新增迁移 `20260915_0027`，创建 `sport_records`、`sport_files`。按项目惯例执行 `make migrate`；Compose 启动时的 `flask bootstrap-db` 也会应用迁移。

两个 Compose 文件已透传推送配置，并为 API 服务挂载 `${DATA_DIR:-./data}/sports:/data/sports`。媒体存放在独立目录，不挂载到公开的 `/images/` 或 `/videos/` URL。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `SPORTS_PUSH_CHECK_STRING` | 空 | 签名密钥，空值禁用 |
| `SPORTS_PUSH_ALLOWED_SCHOOL_IDS` | 空 | 可选学校 ID 白名单 |
| `SPORTS_PUSH_TIMESTAMP_TOLERANCE_SECONDS` | `300` | 请求时间戳与服务器时间允许的偏差秒数；`0` 关闭时效检查 |
| `SPORTS_PUSH_MAX_BATCH_SIZE` | `1000` | 每次最多接收的运动结果条数 |
| `SPORTS_PUSH_MAX_REQUEST_BYTES` | `4194304` | 运动数据请求上限，默认 4 MiB |
| `SPORTS_PUSH_MAX_FILE_BYTES` | `104857600` | 单文件上限，默认 100 MiB |
| `SPORTS_PUSH_FILE_STORAGE_PATH` | `/data/sports` | 本地运行可调整；Compose 固定使用容器内 `/data/sports` |

## 签名

两个接口均必须带请求头：

```text
timestamp: 当前时间的13位毫秒时间戳
sign: MD5(check_string + timestamp) 的32位小写十六进制字符串
```

拼接时没有分隔符，按 UTF-8 编码计算 MD5。签名按提供的协议实现，只覆盖密钥和请求时间戳，因此部署时使用 HTTPS。请求时间戳是**发送时间**，运动 `start_time` 是**运动发生时间**；历史补推可以保留原始 `start_time`，每次发送重新计算请求头。

示例（依赖 `requests`，从环境读取密钥）：

```python
import hashlib
import os
import time

import requests

base_url = "https://你的域名/api/v1/external/sports"


def signed_headers():
    timestamp = str(int(time.time() * 1000))
    sign = hashlib.md5(
        (os.environ["SPORTS_PUSH_CHECK_STRING"] + timestamp).encode("utf-8")
    ).hexdigest()
    return {"timestamp": timestamp, "sign": sign}


response = requests.post(
    base_url + "/results",
    headers=signed_headers(),
    json={
        "version": "v1.1",
        "school_id": "school-1",
        "product_type": 1,
        "sport_type": 1,
        "mode": 1,
        "sport_result": [{
            "person_id": "000123",
            "start_time": 1789430400123,
            "all_time": 60,
            "score": 120,
            "interrupt_count": 2,
            "sub_count_list": [
                {"sub_time": 30, "sub_count": 65},
                {"sub_time": 60, "sub_count": 55},
            ],
        }],
    },
    timeout=30,
)
response.raise_for_status()
assert response.json()["code"] == 0
```

## 运动结果字段

请求使用 `Content-Type: application/json`。

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `version` | string | 必填，`v1.1` |
| `school_id` | string | 必填，非空，最多 128 字符 |
| `product_type` | number | 必填整数，1：AI运动吧，2：AI体测吧，3：AI操场吧 |
| `sport_type` | number | 必填正整数，允许未来新增项目 |
| `mode` | number | 必填整数，1：练习，2：测试 |
| `sport_result` | object[] | 必填非空数组，每项为一次运动结果 |

每项运动结果：

| 字段 | 类型 | 要求与单位 |
| --- | --- | --- |
| `person_id` | string | 必填学号，最多 64 字符；未识别为 `""` |
| `start_time` | number | 必填，13 位整数毫秒时间戳 |
| `score` | number | 必填，成绩单位由运动类型决定；允许坐位体前屈等负成绩 |
| `all_time` | number | 可选，运动总时间，秒 |
| `interrupt_count` | number | 可选，中断次数，非负整数 |
| `jump_speed` | number | 可选，平均速度，m/s |
| `jump_height` | number | 可选，腾空高度，cm |
| `jump_angle` | number | 可选，起跳角度 |
| `arm_angle` | number | 可选，摆臂振幅 |
| `sub_count_list` | object[] | 可选；每项必须包含非负 `sub_time`、非负整数 `sub_count` |
| `reaction_time` | number | 可选，反应时间，ms |
| `weight` | number | 可选，体重，kg |
| `height` | number | 可选，身高，cm |
| `video_file_id` | string | 可选，视频文件 ID，最多 256 字符 |
| `face_file_id` | string | 可选，抓拍人脸文件 ID，最多 256 字符 |

可选数值字段传入时必须是有限非负数，不接收 `null`、字符串、布尔值或 `NaN`。暂未使用的可选字段可以直接省略。扩展指标保留在 `raw_result`，方便后续画像使用。

成绩单位映射：

| 项目编号 | 项目 | 成绩单位 |
| --- | --- | --- |
| 1、4、5、6、7、16、17、18、23 | 跳绳、引体向上、开合跳、仰卧起坐、高抬腿、排球、深蹲、象限跳、左右跳 | 个 |
| 2、3、8 | 跳远、摸高、坐位体前屈 | cm |
| 9 | 肺活量 | ml |
| 10 | BMI | kg/m² |
| 11、12、13、14、15、20、21 | 50m、100m、50m×8、800m、1000m、足球、篮球 | s |
| 19、22 | 阳光跑、实心球 | m |

未知运动编号仍接收，`score_unit` 留空，不猜测单位。

## 文件上传

使用带 boundary 的标准 `multipart/form-data`。原文中的 `application/form-data` 不是标准文件上传媒体类型；使用 HTTP 客户端的 multipart 功能自动生成请求头。

| 表单字段 | 要求 |
| --- | --- |
| `version` | 必填，`v1.0`，与运动数据版本不同 |
| `file_id` | 必填非空字符串，最多 256 字符 |
| `file_type` | 必填，`1`：jpg，`2`：mp4 |
| `file` | 必填，非空文件，内容头需符合声明类型 |

```python
with open("exercise.mp4", "rb") as source:
    response = requests.post(
        base_url + "/files",
        headers=signed_headers(),
        data={"version": "v1.0", "file_id": "video-1", "file_type": "2"},
        files={"file": ("exercise.mp4", source, "video/mp4")},
        timeout=120,
    )
response.raise_for_status()
assert response.json()["code"] == 0
```

文件和运动结果可以按任意顺序推送，通过 `video_file_id` / `face_file_id` 与 `sport_files.file_id` 关联。相同 `file_id` 和相同内容重复上传返回成功；相同 ID 对应不同内容返回 HTTP 409。文件 ID 在此接入内全局唯一，因为文件上传协议没有 `school_id` 字段。

## 回执、重试与数据关联

只有数据库事务提交成功（文件上传还包括文件写入完成）才返回 HTTP 200：

```json
{"code": 0, "message": "成功"}
```

错误统一返回非零 `code`、字符串 `message`，HTTP 状态码与 `code` 一致：

| 状态码 | 含义 |
| --- | --- |
| 400 | 数据格式、版本或字段校验失败，整批不入库 |
| 401 | 签名无效、请求时间戳格式错误或超过时效窗口 |
| 403 | 学校 ID 不在白名单 |
| 409 | 文件 ID 已存在且内容不同 |
| 413 | 请求、文件过大 |
| 415 | Content-Type 不符合接口要求 |
| 500 | 数据库或文件写入失败，可重试 |
| 503 | 服务端尚未配置校验密钥 |

超时或 5xx 可退避重试，重试时重新生成请求头；4xx 需修正请求后重试。

- **已识别学生去重**：使用 `school_id + product_type + sport_type + mode + person_id + start_time` 确定一次运动。相同键重推更新原记录，后到的完整结果覆盖先到结果；补传修正时请带齐需要保留的可选字段。数据库唯一约束与原子 upsert 处理并发重试。
- **协议限制**：文档没有运动事件 ID 或修订序号，因此不能区分同一学生同一毫秒的两场同类运动，也不能判断迟到数据是否为旧版成绩。后续若提供稳定事件 ID / 修订序号，可扩展去重与更新规则。
- **未识别人员**：`person_id=""` 仍保存，不加入个人画像；仅对同一上下文、完整结果相同的匿名记录去重。同一时刻成绩不同的匿名结果分别保留。协议无法区分同一时刻成绩和所有指标完全相同的多名匿名人员。
- **未建档学号**：保留记录，不创建虚构学生。`SportRecord.student` 按学号动态关联，名单后到时即可关联；后续联合膳食数据时可通过 `sport_records.person_id = students.student_no` 再关联 `nutrition_logs.student_id`。
- **统计时间**：`start_time` 保留原始毫秒时间戳，后续按日统计时转换为学校所在地时区（例如 Asia/Shanghai）；`received_at` 是接收时间，不用于替代运动时间。
- **数据用途**：保存运动成绩和指标，作为后续运动＋膳食画像的数据基础；现有营养报告尚未加入运动分析，也没有根据成绩推算热量消耗。

## 开发验证

```bash
cd backend
python -m pytest tests/test_external_sports.py -q
```

默认运行 SQLite 契约、入库和迁移测试。设置 `SPORTS_TEST_DATABASE_URL` 指向测试 PostgreSQL 可同时运行并发结果推送、并发文件上传与事务回滚测试；测试使用独立临时 schema，结束后自动删除。
