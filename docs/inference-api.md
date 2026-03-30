# Inference API

本文档描述当前 YOLO detector + embedding retrieval 链路的接口边界、调用方式和部署约束。

## 服务边界

当前链路拆成三层：

- `flask-api`
  - 对外业务 API
  - 可以接受 `image_id`
  - 负责鉴权、查库、解析候选菜
  - 负责把图片文件转发给内部推理服务
- `detector-api`
  - 内部 YOLO 检测服务
  - 只负责返回检测框
  - 不访问数据库
  - 不接受 `image_id`
- `retrieval-api`
  - 内部 embedding / recall / rerank 服务
  - 不访问数据库
  - 不接受 `image_id`

重要约束：

- `detector-api` 和 `retrieval-api` 是跨机可部署的解耦服务
- 两个内部服务只接受 `multipart/form-data` 的 `image_file`
- `image_id` 只能由业务 API 使用，不能透传给推理服务

## 对外业务接口

路径：

```http
POST /api/v1/analysis/pipeline
Authorization: Bearer <token>
```

支持模式：

- `detect`
- `embed`
- `full`

输入来源三选一：

- `image_id`
- `image_path`
- `image_file`

### 1. detect

```bash
curl -X POST http://localhost:5000/api/v1/analysis/pipeline \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "detect",
    "image_id": 123,
    "max_regions": 3
  }'
```

示例响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "mode": "detect",
    "source": "image_id",
    "backend": "yolo",
    "regions": [
      {
        "index": 1,
        "bbox": { "x1": 10, "y1": 20, "x2": 110, "y2": 180 },
        "score": 0.92,
        "class_id": 0,
        "class_name": "food_region",
        "source": "yolo"
      }
    ],
    "model_version": "best.pt",
    "timings_ms": {
      "detect": 48,
      "total": 48
    }
  }
}
```

### 2. embed

```bash
curl -X POST http://localhost:5000/api/v1/analysis/pipeline \
  -H "Authorization: Bearer <token>" \
  -F 'mode=embed' \
  -F 'image_file=@/tmp/meal.jpg' \
  -F 'bboxes=[{"x1":10,"y1":20,"x2":110,"y2":180}]'
```

示例响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "mode": "embed",
    "source": "upload",
    "embeddings": [
      {
        "index": 1,
        "bbox": { "x1": 10, "y1": 20, "x2": 110, "y2": 180 },
        "vector": [0.01, 0.12, 0.03],
        "dim": 3,
        "source": "provided_bbox"
      }
    ],
    "model_version": "qwen3_vl_embedding+reranker",
    "timings_ms": {
      "embed": 120,
      "total": 120
    }
  }
}
```

### 3. full

```bash
curl -X POST http://localhost:5000/api/v1/analysis/pipeline \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "full",
    "image_id": 123,
    "candidate_dish_ids": [1, 2, 3],
    "max_regions": 3
  }'
```

示例响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "mode": "full",
    "source": "image_id",
    "detector_backend": "yolo",
    "regions": [
      {
        "index": 1,
        "bbox": { "x1": 10, "y1": 20, "x2": 110, "y2": 180 },
        "score": 0.92,
        "class_id": 0,
        "class_name": "food_region",
        "source": "yolo"
      }
    ],
    "recognized_dishes": [
      {
        "name": "红烧肉",
        "confidence": 0.88
      }
    ],
    "region_results": [
      {
        "index": 1,
        "bbox": { "x1": 10, "y1": 20, "x2": 110, "y2": 180 },
        "embedding_dim": 1024,
        "recall_hits": [],
        "reranked_hits": []
      }
    ],
    "raw_response": {
      "mode": "local_embedding",
      "regions": []
    },
    "model_version": "qwen3_vl_embedding+reranker",
    "notes": "full_image local embedding 模式，区域数 1",
    "timings_ms": {
      "retrieve": 236,
      "total": 236
    }
  }
}
```

## 内部 detector-api

路径：

```http
POST /v1/detect
Authorization: Bearer <internal-token>
Content-Type: multipart/form-data
```

请求参数：

- `image_file`: 必填
- `conf_threshold`: 可选
- `iou_threshold`: 可选
- `max_regions`: 可选

示例：

```bash
curl -X POST http://detector-api:5000/v1/detect \
  -H "Authorization: Bearer <internal-token>" \
  -F 'image_file=@/tmp/meal.jpg' \
  -F 'max_regions=3'
```

说明：

- 不支持 `image_id`
- 不支持 `image_path`
- 不依赖数据库

## 内部 retrieval-api

### embed

路径：

```http
POST /v1/embed
Authorization: Bearer <internal-token>
Content-Type: multipart/form-data
```

请求参数：

- `image_file`: 必填
- `bboxes`: 可选，JSON 数组字符串
- `instruction`: 可选

### full / retrieve

路径：

```http
POST /v1/full
POST /v1/retrieve
```

请求参数：

- `image_file`: 必填
- `regions`: 必填，JSON 数组字符串
- `candidate_dishes`: 必填，JSON 数组字符串

示例：

```bash
curl -X POST http://retrieval-api:5000/v1/full \
  -H "Authorization: Bearer <internal-token>" \
  -F 'image_file=@/tmp/meal.jpg' \
  -F 'regions=[{"x1":10,"y1":20,"x2":110,"y2":180}]' \
  -F 'candidate_dishes=[{"id":1,"name":"红烧肉","description":""}]'
```

### 模型与索引健康检查

```bash
curl http://retrieval-api:5000/health
curl -H "Authorization: Bearer <internal-token>" http://retrieval-api:5000/health/models
curl -X POST -H "Authorization: Bearer <internal-token>" http://retrieval-api:5000/v1/index/reload
```

## 部署约束

- `detector-api` 和 `retrieval-api` 通过 GitHub Actions 打包并推送 GHCR
- compose 只使用 `image:` 拉取，不在服务器本地 `build`
- 开发环境下两个 inference 服务挂在 `inference` profile 下，默认 `docker compose up` 不启动；需要 GPU 推理时使用 `docker compose --profile inference up`
- 两个内部服务默认通过内网地址通信：
  - `http://detector-api:5000`
  - `http://retrieval-api:5000`
- 同机部署时可通过以下环境变量精确绑卡：
  - `DETECTOR_GPU_DEVICE`
  - `RETRIEVAL_GPU_DEVICE`
  例如 `DETECTOR_GPU_DEVICE=0`、`RETRIEVAL_GPU_DEVICE=1`
- 业务服务通过以下环境变量访问：
  - `DETECTOR_API_BASE_URL`
  - `RETRIEVAL_API_BASE_URL`
  - `INFERENCE_API_TOKEN`
  - `INFERENCE_API_TIMEOUT`

## 设计原则

- 推理服务不依赖业务数据库
- 推理服务不依赖业务主键
- 业务语义由 `flask-api` 承担
- 推理服务只处理图片和推理参数
