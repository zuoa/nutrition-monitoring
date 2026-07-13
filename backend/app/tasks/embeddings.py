import logging
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, date

import numpy as np

from celery_app import celery
from app import db
from app.models import Dish, DishSampleImage, EmbeddingStatusEnum, TaskLog
from app.services.inference_client import InferenceServiceError, make_retrieval_client
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def _build_active_sample_images() -> list[DishSampleImage]:
    return DishSampleImage.query.join(Dish).filter(
        Dish.is_active.is_(True),
        DishSampleImage.is_active.is_(True),
    ).order_by(DishSampleImage.dish_id.asc(), DishSampleImage.sort_order.asc()).all()


def _build_embedding_input_hash(config: dict, image_path: str, embedding_instruction: str | None) -> str:
    hasher = hashlib.sha256()
    hasher.update(b"qwen3-vl-sample-embedding-v1\0")
    hasher.update(str(embedding_instruction or "").encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(config.get("LOCAL_QWEN3_VL_EMBEDDING_REPO_ID", "") or "").encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(config.get("LOCAL_QWEN3_VL_EMBEDDING_MODEL_PATH", "") or "").encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(config.get("LOCAL_QWEN3_VL_EMBEDDING_PRECISION", "auto") or "auto").encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(str(config.get("LOCAL_EMBEDDING_MAX_PIXELS", "") or "").encode("utf-8"))
    hasher.update(b"\0")

    with open(image_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _coerce_cached_vector(value) -> np.ndarray | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        vector = np.asarray(value, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if vector.size == 0:
        return None
    return vector


def _get_cached_sample_vector(image: DishSampleImage, input_hash: str) -> np.ndarray | None:
    if image.embedding_status != EmbeddingStatusEnum.ready:
        return None
    if getattr(image, "embedding_input_hash", None) != input_hash:
        return None
    return _coerce_cached_vector(getattr(image, "embedding_vector", None))


def _build_sample_metadata_record(image: DishSampleImage, *, upload_sample: bool = False) -> dict:
    return {
        "image_id": image.id,
        "dish_id": image.dish_id,
        "dish_name": image.dish.name if image.dish else "",
        "original_filename": image.original_filename,
        "relative_image_path": f"dish_{image.dish_id}/sample_{image.id}{os.path.splitext(image.image_path)[1].lower() or '.jpg'}",
        "_source_image_path": image.image_path,
        "_upload_sample": upload_sample,
    }


def _upload_remote_index(
    config: dict,
    *,
    metadata: list[dict],
    matrix: np.ndarray,
) -> dict:
    client = make_retrieval_client(config)

    def _post_index(*, force_all_samples: bool) -> dict:
        upload_items = [
            item for item in metadata
            if force_all_samples or bool(item.get("_upload_sample"))
        ]
        matrix_tmp = ""
        metadata_tmp = ""
        samples_tmp = ""
        public_metadata = [
            {key: value for key, value in item.items() if not key.startswith("_")}
            for item in metadata
        ]
        try:
            with tempfile.NamedTemporaryFile(suffix=".npy", delete=False) as tmp:
                np.save(tmp, matrix.astype(np.float32))
                matrix_tmp = tmp.name
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
                json.dump(public_metadata, tmp, ensure_ascii=False, indent=2)
                metadata_tmp = tmp.name

            if upload_items:
                with tempfile.TemporaryDirectory() as sample_dir:
                    for item in upload_items:
                        source_path = str(item.get("_source_image_path") or "").strip()
                        relative_image_path = str(item.get("relative_image_path") or "").strip()
                        if not source_path or not os.path.exists(source_path):
                            raise FileNotFoundError(f"样图文件不存在: {relative_image_path or source_path}")
                        if not relative_image_path:
                            raise ValueError("metadata 缺少 relative_image_path")
                        dest_path = os.path.join(sample_dir, relative_image_path)
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        shutil.copy2(source_path, dest_path)

                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                        samples_tmp = tmp.name
                    with zipfile.ZipFile(samples_tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for root, _, files in os.walk(sample_dir):
                            for filename in files:
                                file_path = os.path.join(root, filename)
                                arcname = os.path.relpath(file_path, sample_dir)
                                archive.write(file_path, arcname)

            return client.post_form_files(
                "/v1/index/upload",
                data={"reuse_existing_samples": "1"},
                file_paths={
                    "matrix_file": matrix_tmp,
                    "metadata_file": metadata_tmp,
                    **({"samples_archive": samples_tmp} if samples_tmp else {}),
                },
            )
        finally:
            for path in (matrix_tmp, metadata_tmp, samples_tmp):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

    try:
        return _post_index(force_all_samples=False)
    except InferenceServiceError as e:
        if metadata and "samples_archive 缺少文件" in str(e):
            logger.warning("Remote sample cache is incomplete; retrying full sample index upload")
            return _post_index(force_all_samples=True)
        raise


def _rebuild_sample_embeddings_remote(config: dict, task_log: TaskLog) -> dict:
    client = make_retrieval_client(config)
    embedding_instruction = str(config.get("LOCAL_QWEN3_VL_EMBEDDING_INSTRUCTION", "") or "").strip() or None
    images = _build_active_sample_images()

    if not images:
        remote_result = _upload_remote_index(config, metadata=[], matrix=np.empty((0, 0), dtype=np.float32))
        task_log.status = "success"
        task_log.total_count = 0
        task_log.success_count = 0
        task_log.error_count = 0
        task_log.finished_at = datetime.utcnow()
        task_log.meta = {
            "execution_target": "retrieval-api",
            "index_dir": remote_result.get("index_dir"),
            "index_ready": remote_result.get("index_ready"),
        }
        db.session.commit()
        return {
            "total": 0,
            "ready": 0,
            "failed": 0,
            "index_ready": bool(remote_result.get("index_ready")),
        }

    records: list[dict] = []
    vectors: list[np.ndarray] = []
    failed = 0
    reused = 0
    generated = 0
    model_version = ""
    image_hashes: dict[int, str] = {}
    images_to_embed: list[DishSampleImage] = []

    for image in images:
        try:
            if not image.image_path or not os.path.exists(image.image_path):
                raise FileNotFoundError("样图文件不存在")
            input_hash = _build_embedding_input_hash(config, image.image_path, embedding_instruction)
            image_hashes[image.id] = input_hash
            cached_vector = _get_cached_sample_vector(image, input_hash)
            if cached_vector is not None:
                vectors.append(cached_vector)
                records.append(_build_sample_metadata_record(image, upload_sample=False))
                reused += 1
                if image.embedding_version and not model_version:
                    model_version = image.embedding_version
            else:
                images_to_embed.append(image)
        except Exception as e:
            failed += 1
            image.embedding_status = EmbeddingStatusEnum.failed
            image.error_message = str(e)[:255]
            logger.error("Failed to prepare sample image %s for embedding: %s", image.id, e)

    for image in images_to_embed:
        image.embedding_status = EmbeddingStatusEnum.processing
        image.error_message = None
    db.session.commit()

    for idx, image in enumerate(images_to_embed, start=1):
        try:
            response = client.post_file(
                "/v1/embed",
                image_path=image.image_path,
                data={"instruction": embedding_instruction} if embedding_instruction else None,
            )
            embeddings = response.get("embeddings") or []
            if not embeddings:
                raise ValueError("retrieval-api 未返回 embedding")
            vector = np.asarray(embeddings[0].get("vector") or [], dtype=np.float32).reshape(-1)
            if vector.size == 0:
                raise ValueError("retrieval-api 返回了空 embedding")

            model_version = str(response.get("model_version") or model_version or "retrieval-api")
            vectors.append(vector.astype(np.float32))
            records.append(_build_sample_metadata_record(image, upload_sample=True))
            image.embedding_status = EmbeddingStatusEnum.ready
            image.embedding_model = "retrieval-api"
            image.embedding_version = model_version
            image.embedding_input_hash = image_hashes.get(image.id)
            image.embedding_vector = vector.astype(float).tolist()
            image.error_message = None
            image.embedding_updated_at = datetime.utcnow()
            generated += 1
        except Exception as e:
            failed += 1
            image.embedding_status = EmbeddingStatusEnum.failed
            image.embedding_vector = None
            image.error_message = str(e)[:255]
            logger.error("Failed to build remote embedding for sample image %s: %s", image.id, e)
        finally:
            task_log.status = "running"
            task_log.total_count = len(images)
            task_log.success_count = len(records)
            task_log.error_count = failed
            task_log.meta = {
                "execution_target": "retrieval-api",
                "status_text": f"正在生成 embedding ({idx}/{len(images_to_embed)})",
                "processed": reused + idx,
                "reused_count": reused,
                "generated_count": generated,
                "model_version": model_version,
            }
            db.session.commit()

    matrix = np.vstack(vectors).astype(np.float32) if vectors else np.empty((0, 0), dtype=np.float32)
    remote_result = _upload_remote_index(config, metadata=records, matrix=matrix)

    task_log.status = "success" if failed == 0 else "partial"
    task_log.total_count = len(images)
    task_log.success_count = len(records)
    task_log.error_count = failed
    task_log.finished_at = datetime.utcnow()
    task_log.meta = {
        "execution_target": "retrieval-api",
        "status_text": "样图 embedding 重建完成",
        "model_version": model_version,
        "index_dir": remote_result.get("index_dir"),
        "index_ready": remote_result.get("index_ready"),
        "embedding_count": remote_result.get("embedding_count"),
        "sample_image_root": remote_result.get("sample_image_root"),
        "reused_count": reused,
        "generated_count": generated,
    }
    db.session.commit()
    return {
        "total": len(images),
        "ready": len(records),
        "failed": failed,
        "reused": reused,
        "generated": generated,
        "index_ready": bool(remote_result.get("index_ready")),
        "embedding_count": int(remote_result.get("embedding_count") or 0),
        "metadata_count": len(records),
    }


@celery.task(
    name="app.tasks.embeddings.rebuild_sample_embeddings",
    bind=True,
    max_retries=1,
    # 全量重建耗时随样图数量线性增长（1130 张冷启动约 400s），
    # 远超 celery 默认 300s 软超时；此处给足余量，避免超时砸在
    # 末尾 _upload_remote_index 上传阶段导致索引写不进去。
    soft_time_limit=1800,
    time_limit=2400,
)
def rebuild_sample_embeddings(self):
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    task_log = TaskLog(task_type="dish_embedding", task_date=date.today())
    db.session.add(task_log)
    db.session.commit()

    try:
        result = _rebuild_sample_embeddings_remote(cfg, task_log)
        return result
    except Exception as e:
        logger.error("Failed to rebuild sample embeddings: %s", e, exc_info=True)
        task_log.status = "failed"
        task_log.error_message = str(e)[:255]
        task_log.finished_at = datetime.utcnow()
        existing_meta = dict(task_log.meta or {})
        existing_meta.setdefault("status_text", "样图 embedding 重建失败")
        task_log.meta = existing_meta
        db.session.commit()
        raise
