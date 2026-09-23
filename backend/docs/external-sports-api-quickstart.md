# 体育数据推送 API 接口说明（简版）

服务器：`http://192.168.11.50:5080`



用于第三方向本系统推送学生运动成绩，可选上传抓拍人脸和运动视频。对接前由系统管理员提供实际域名、学校编号 `school_id` 和签名密钥 `check_string`。

## 1. 接口与鉴权

基础地址：`服务器HOST/api/v1/external/sports`

| 接口 | 请求方式 | 路径 | 请求类型 |
| --- | --- | --- | --- |
| 推送运动成绩 | POST | `/results` | `application/json` |
| 上传图片或视频（可选） | POST | `/files` | `multipart/form-data` |

两个接口均无需登录或 JWT，必须携带以下请求头：

| 请求头 | 说明 |
| --- | --- |
| `timestamp` | 当前发送时间，13 位毫秒时间戳字符串 |
| `sign` | `MD5(check_string + timestamp)`，32 位小写十六进制字符串 |

签名拼接不加分隔符，使用 UTF-8 编码。请求时间与服务器时间默认允许相差 300 秒，每次重试都要重新生成时间戳和签名。

## 2. 推送运动成绩

请求示例：

```json
{
  "version": "v1.1",
  "school_id": "school-1",
  "product_type": 1,
  "sport_type": 1,
  "mode": 1,
  "sport_result": [
    {
      "person_id": "000123",
      "start_time": 1789430400123,
      "score": 120,
      "all_time": 60
    }
  ]
}
```

以上表示学号 `000123` 的学生进行一次跳绳练习，运动时长 60 秒，成绩 120 个。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `version` | string | 是 | 固定为 `v1.1` |
| `school_id` | string | 是 | 外部学校编号，非空，最多 128 字符 |
| `product_type` | integer | 是 | `1` AI运动吧；`2` AI体测吧；`3` AI操场吧 |
| `sport_type` | integer | 是 | 运动项目编号，正整数；如 `1` 跳绳、`2` 跳远、`8` 坐位体前屈、`11` 50m跑 |
| `mode` | integer | 是 | `1` 练习；`2` 测试 |
| `sport_result` | object[] | 是 | 运动结果数组，默认每次 1～1000 条 |
| `sport_result[].person_id` | string | 是 | 学生学号，最多 64 字符，保留前导零，不含首尾空格；未识别时传 `""` |
| `sport_result[].start_time` | integer | 是 | 运动发生时间，13 位毫秒时间戳 |
| `sport_result[].score` | number | 是 | 成绩：跳绳为个、跳远及坐位体前屈为 cm、50m跑为秒；允许负成绩 |
| `sport_result[].all_time` | number | 否 | 运动总时长，单位秒，非负数 |
| `sport_result[].video_file_id` | string | 否 | 视频文件 ID，最多 256 字符，与上传接口的 `file_id` 一致 |
| `sport_result[].face_file_id` | string | 否 | 人脸图片文件 ID，最多 256 字符，与上传接口的 `file_id` 一致 |

数值字段请传 JSON 数字，不要传字符串、布尔值、`null` 或 `NaN`。不用的可选字段直接省略。请求体默认最大 4 MiB。

可直接改用的 Python 示例（需安装 `requests`，并在环境变量 `SPORTS_PUSH_CHECK_STRING` 中设置约定密钥）：

```python
import hashlib
import os
import time

import requests

base_url = "服务器HOST/api/v1/external/sports"
timestamp = str(int(time.time() * 1000))
sign = hashlib.md5(
    (os.environ["SPORTS_PUSH_CHECK_STRING"] + timestamp).encode("utf-8")
).hexdigest()

response = requests.post(
    base_url + "/results",
    headers={"timestamp": timestamp, "sign": sign},
    json={
        "version": "v1.1",
        "school_id": "school-1",
        "product_type": 1,
        "sport_type": 1,
        "mode": 1,
        "sport_result": [
            {"person_id": "000123", "start_time": 1789430400123, "score": 120}
        ],
    },
    timeout=30,
)
response.raise_for_status()
print(response.json())
```

## 3. 上传文件（可选）

向 `服务器HOST/api/v1/external/sports/files` 提交以下表单字段，并携带第 1 节的签名请求头。使用客户端的 multipart 上传功能自动生成带 boundary 的 `Content-Type`。

| 表单字段 | 必填 | 说明 |
| --- | --- | --- |
| `version` | 是 | 固定为 `v1.0`，注意与成绩接口版本不同 |
| `file_id` | 是 | 全局唯一的非空文件 ID，最多 256 字符 |
| `file_type` | 是 | `1`：JPG 图片；`2`：MP4 视频 |
| `file` | 是 | 非空文件，实际内容须与声明类型一致，默认最大 100 MiB |

文件与成绩可按任意顺序上传，通过文件 ID 关联。同一 `file_id` 上传相同类型和内容会返回成功，类型或内容不同返回 `409`。

## 4. 返回结果与重试

成功：HTTP `200`，表示数据已保存。

```json
{"code": 0, "message": "成功"}
```

失败示例：HTTP `401`。

```json
{"code": 401, "message": "签名校验失败"}
```

| HTTP 状态码 / 错误 code | 说明 |
| --- | --- |
| 400 | 参数、版本或数据格式错误，整批不保存 |
| 401 | 签名或请求时间戳无效 |
| 403 | 学校编号未获授权 |
| 409 | 文件 ID 已存在且类型或内容不同 |
| 413 | 请求或文件超过大小限制 |
| 415 | 请求 Content-Type 不符合要求 |
| 500 | 保存失败，可稍后重试 |
| 503 | 服务端未配置签名密钥，需联系管理员 |

超时或 `500` 可间隔递增重试；`4xx` 先修正请求。历史成绩补传保留原始 `start_time`，请求头使用当前时间。

对于非空学号，`school_id + product_type + sport_type + mode + person_id + start_time` 相同视为同一次运动，重复推送会覆盖原记录，补传时需带齐要保留的可选字段。未识别人员仅对相同上下文且完整结果相同的数据去重。学号尚未建档也会保存成绩；多学校共用系统时需保证学号全局唯一。

完整运动项目、扩展指标及部署配置见 [详细接口文档](external-sports-api.md)。
