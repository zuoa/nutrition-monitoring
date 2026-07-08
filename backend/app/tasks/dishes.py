import logging
import os
from datetime import date, datetime

from celery.exceptions import SoftTimeLimitExceeded
from celery_app import celery
from app import db
from app.models import Dish, TaskLog
from app.nutrition_metadata import NUTRITION_FIELD_KEYS
from app.services.dish_analyzer import DishAnalyzerService
from app.services.runtime_config import get_effective_config
from app.services.structured_description import compose_structured_description
from sqlalchemy import or_

logger = logging.getLogger(__name__)


def _active_dishes_without_nutrition() -> list[Dish]:
    return Dish.query.filter(
        Dish.is_active.is_(True),
        or_(*(getattr(Dish, field).is_(None) for field in NUTRITION_FIELD_KEYS)),
    ).order_by(Dish.id.asc()).all()


def _set_task_meta(task_log: TaskLog, **updates) -> None:
    task_log.meta = {
        **(task_log.meta or {}),
        **updates,
    }


@celery.task(
    name="app.tasks.dishes.batch_analyze_dish_nutrition",
    bind=True,
    soft_time_limit=1800,
    time_limit=3600,
)
def batch_analyze_dish_nutrition(self, task_log_id: int):
    from flask import current_app

    cfg = get_effective_config(current_app.config)
    task_log = TaskLog.query.get(task_log_id)
    if not task_log:
        logger.warning("Dish nutrition task log %s not found", task_log_id)
        return {"missing_task_log": True, "task_log_id": task_log_id}

    if not cfg.get("OPENAI_API_KEY", ""):
        task_log.status = "failed"
        task_log.error_count = 1
        task_log.error_message = "营养分析服务未配置 (OPENAI_API_KEY)"
        task_log.finished_at = datetime.utcnow()
        _set_task_meta(task_log, status_text="营养分析服务未配置")
        db.session.commit()
        return {"total": 0, "success": 0, "failed": 1}

    dishes = _active_dishes_without_nutrition()
    task_log.status = "running"
    task_log.total_count = len(dishes)
    task_log.success_count = 0
    task_log.error_count = 0
    _set_task_meta(
        task_log,
        status_text="正在批量分析菜品营养成分",
        celery_task_id=getattr(self.request, "id", None),
    )
    db.session.commit()

    if not dishes:
        task_log.status = "success"
        task_log.finished_at = datetime.utcnow()
        _set_task_meta(task_log, status_text="没有需要分析的菜品")
        db.session.commit()
        return {"total": 0, "success": 0, "failed": 0, "errors": []}

    analyzer = DishAnalyzerService(cfg)
    success_count = 0
    failed_count = 0
    errors: list[str] = []

    for index, dish in enumerate(dishes, start=1):
        _set_task_meta(
            task_log,
            current_dish_id=dish.id,
            current_dish_name=dish.name,
            processed_count=index - 1,
            status_text=f"正在分析 {dish.name}",
        )
        db.session.commit()

        try:
            weight = int(dish.weight) if dish.weight else 100
            result = analyzer.analyze_nutrition(dish.name, weight, dish.ingredients or "")

            for field in NUTRITION_FIELD_KEYS:
                setattr(dish, field, result.get(field))
            composed_description = compose_structured_description(
                result.get("description", ""),
                result.get("structured_description"),
            )
            if composed_description:
                dish.description = composed_description

            success_count += 1
            task_log.success_count = success_count
            logger.info("Analyzed nutrition for dish %s: %s", dish.id, dish.name)
        except Exception as e:
            failed_count += 1
            error_msg = f"{dish.name}: {str(e)}"
            errors.append(error_msg)
            task_log.error_count = failed_count
            task_log.error_message = "\n".join(errors[-20:])
            logger.error("Failed to analyze dish %s (%s): %s", dish.id, dish.name, e, exc_info=True)
            db.session.rollback()
            task_log = TaskLog.query.get(task_log_id)
            if not task_log:
                logger.warning("Dish nutrition task log %s disappeared during processing", task_log_id)
                return {"missing_task_log": True, "task_log_id": task_log_id}
        finally:
            task_log.total_count = len(dishes)
            task_log.success_count = success_count
            task_log.error_count = failed_count
            _set_task_meta(
                task_log,
                processed_count=index,
                current_dish_id=dish.id,
                current_dish_name=dish.name,
                status_text=f"已处理 {index}/{len(dishes)}",
            )
            db.session.commit()

    task_log.status = "success" if failed_count == 0 else ("partial" if success_count else "failed")
    task_log.finished_at = datetime.utcnow()
    _set_task_meta(
        task_log,
        processed_count=len(dishes),
        current_dish_id=None,
        current_dish_name="",
        status_text=f"批量分析完成，成功 {success_count} 个，失败 {failed_count} 个",
        errors=errors[:20],
    )
    db.session.commit()

    return {
        "total": len(dishes),
        "success": success_count,
        "failed": failed_count,
        "errors": errors[:20],
    }


def create_batch_nutrition_task_log(total_count: int) -> TaskLog:
    task_log = TaskLog(
        task_type="dish_nutrition_analysis",
        task_date=date.today(),
        status="pending",
        total_count=total_count,
        meta={
            "status_text": "任务已提交，等待执行",
            "processed_count": 0,
        },
    )
    db.session.add(task_log)
    db.session.commit()
    return task_log


def create_zip_import_task_log() -> TaskLog:
    task_log = TaskLog(
        task_type="dish_zip_import",
        task_date=date.today(),
        status="pending",
        meta={
            "status_text": "任务已提交，等待执行",
            "processed_count": 0,
            "total_count": 0,
        },
    )
    db.session.add(task_log)
    db.session.commit()
    return task_log


def _safe_delete(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError as e:
        logger.warning("Failed to delete uploaded zip %s: %s", path, e)


@celery.task(
    name="app.tasks.dishes.import_dishes_zip_task",
    bind=True,
    soft_time_limit=1800,
    time_limit=3600,
)
def import_dishes_zip_task(self, task_log_id: int, zip_path: str):
    """Background ZIP dish import. Heavy work (zip validation, Excel parse,
    image save) runs here so the HTTP request can return immediately.
    Progress is reported via task_log.meta; the uploaded zip is deleted on
    completion (success or failure)."""
    from flask import current_app

    from app.api.dishes import _execute_zip_import
    from app.services.embedding_jobs import trigger_local_embedding_rebuild

    task_log = TaskLog.query.get(task_log_id)
    if not task_log:
        logger.warning("Zip import task log %s not found", task_log_id)
        _safe_delete(zip_path)
        return {"missing_task_log": True, "task_log_id": task_log_id}

    task_log.status = "running"
    _set_task_meta(task_log, status_text="任务开始", celery_task_id=getattr(self.request, "id", None))
    db.session.commit()

    try:
        result = _execute_zip_import(zip_path, task_log)
        task_log = TaskLog.query.get(task_log_id) or task_log
        task_log.status = "success"
        task_log.success_count = result.get("images_imported", 0)
        task_log.error_count = result.get("images_skipped", 0)
        task_log.finished_at = datetime.utcnow()
        _set_task_meta(
            task_log,
            status_text=(
                f"导入完成：新增 {result['created_count']}，更新 {result['updated_count']}，"
                f"图片 {result['images_imported']} 张"
            ),
            created_count=result["created_count"],
            updated_count=result["updated_count"],
            images_imported=result["images_imported"],
            images_skipped=result["images_skipped"],
            dishes_with_images=result["dishes_with_images"],
            folders_unmatched=result["folders_unmatched"],
            warnings=result.get("warnings", []),
        )
        db.session.commit()
        return result
    except SoftTimeLimitExceeded:
        task_log = TaskLog.query.get(task_log_id) or task_log
        task_log.status = "failed"
        task_log.error_message = "导入超时"
        task_log.finished_at = datetime.utcnow()
        _set_task_meta(task_log, status_text="导入超时")
        db.session.commit()
        logger.error("Zip import task %s timed out", task_log_id)
        return {"failed": True, "reason": "timeout"}
    except Exception as e:
        task_log = TaskLog.query.get(task_log_id) or task_log
        task_log.status = "failed"
        task_log.error_message = str(e)
        task_log.finished_at = datetime.utcnow()
        _set_task_meta(task_log, status_text=f"导入失败: {str(e)}")
        db.session.commit()
        logger.error("Zip import task %s failed: %s", task_log_id, e, exc_info=True)
        return {"failed": True, "reason": str(e)}
    finally:
        _safe_delete(zip_path)
        try:
            trigger_local_embedding_rebuild(current_app.config, reason="zip_import")
        except Exception as e:
            logger.warning("Failed to trigger embedding rebuild after zip import: %s", e)
