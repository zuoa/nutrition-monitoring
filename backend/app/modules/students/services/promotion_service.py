"""Transactional class promotion/graduation preview and apply service."""

import hashlib
import json

from app import db
from app.modules.students.models.organization import Class
from app.modules.students.models.student import Student, EnrollmentStatusEnum


class PromotionError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


VALID_ACTIONS = {"promote", "graduate", "skip"}


def preview_promotion(payload: dict) -> dict:
    return _build_preview(payload, lock=False)


def apply_promotion(payload: dict, expected_token: str) -> dict:
    if not expected_token:
        raise PromotionError("缺少升年级预览令牌")
    preview = _build_preview(payload, lock=True)
    if preview["preview_token"] != expected_token:
        db.session.rollback()
        raise PromotionError("学生数据已发生变化，请重新预览后再执行", 409)

    promoted = graduated = skipped = 0
    for row in preview["students"]:
        student = db.session.get(Student, row["student_id"])
        if row["action"] == "promote":
            student.class_id = row["target_class_id"]
            student.enrollment_status = EnrollmentStatusEnum.enrolled
            student.is_active = not student.is_locally_disabled
            promoted += 1
        elif row["action"] == "graduate":
            student.enrollment_status = EnrollmentStatusEnum.graduated
            student.is_active = False
            graduated += 1
        else:
            skipped += 1
    db.session.commit()
    return {
        "promoted": promoted,
        "graduated": graduated,
        "skipped": skipped,
        "total": len(preview["students"]),
    }


def _build_preview(payload: dict, lock: bool) -> dict:
    mappings = _normalize_mappings(payload.get("mappings"))
    overrides = _normalize_overrides(payload.get("overrides"))
    source_ids = [item["source_class_id"] for item in mappings]

    source_classes = {
        item.id: item
        for item in Class.query.filter(Class.id.in_(source_ids)).all()
    }
    if len(source_classes) != len(source_ids):
        raise PromotionError("部分来源班级不存在")

    target_ids = {
        item["target_class_id"]
        for item in [*mappings, *overrides.values()]
        if item["action"] == "promote"
    }
    target_classes = {
        item.id: item
        for item in Class.query.filter(Class.id.in_(target_ids), Class.is_active.is_(True)).all()
    } if target_ids else {}
    if len(target_classes) != len(target_ids):
        raise PromotionError("部分目标班级不存在或已归档")

    query = Student.query.filter(
        Student.class_id.in_(source_ids),
        Student.enrollment_status == EnrollmentStatusEnum.enrolled,
    ).order_by(Student.class_id, Student.student_no, Student.id)
    if lock:
        query = query.with_for_update()
    students = query.all()
    student_ids = {student.id for student in students}
    unknown_overrides = set(overrides) - student_ids
    if unknown_overrides:
        raise PromotionError("个别调整中包含不属于所选来源班级的学生")

    mapping_by_source = {item["source_class_id"]: item for item in mappings}
    rows = []
    snapshot = []
    for student in students:
        decision = overrides.get(student.id) or mapping_by_source[student.class_id]
        action = decision["action"]
        target_id = decision.get("target_class_id") if action == "promote" else None
        if target_id == student.class_id:
            raise PromotionError(f"学生 {student.name} 的目标班级不能与来源班级相同")
        target = target_classes.get(target_id)
        source = source_classes[student.class_id]
        rows.append({
            "student_id": student.id,
            "student_no": student.student_no,
            "student_name": student.name,
            "source_class_id": source.id,
            "source_class_name": source.name,
            "action": action,
            "target_class_id": target_id,
            "target_class_name": target.name if target else None,
            "is_locally_disabled": student.is_locally_disabled,
        })
        snapshot.append({
            "student_id": student.id,
            "class_id": student.class_id,
            "status": student.enrollment_status.value,
            "disabled": student.is_locally_disabled,
            "action": action,
            "target_class_id": target_id,
        })

    token_payload = {
        "mappings": mappings,
        "overrides": [overrides[key] for key in sorted(overrides)],
        "students": snapshot,
    }
    token = hashlib.sha256(
        json.dumps(token_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "preview_token": token,
        "summary": {
            "promoted": sum(row["action"] == "promote" for row in rows),
            "graduated": sum(row["action"] == "graduate" for row in rows),
            "skipped": sum(row["action"] == "skip" for row in rows),
            "total": len(rows),
        },
        "students": rows,
    }


def _normalize_mappings(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise PromotionError("请至少选择一个来源班级")
    result = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise PromotionError("班级映射格式无效")
        source_id = _positive_int(item.get("source_class_id"), "来源班级")
        if source_id in seen:
            raise PromotionError("来源班级不能重复")
        seen.add(source_id)
        result.append(_normalize_decision(item, source_class_id=source_id))
    return result


def _normalize_overrides(raw) -> dict[int, dict]:
    if raw in (None, []):
        return {}
    if not isinstance(raw, list):
        raise PromotionError("个别调整格式无效")
    result = {}
    for item in raw:
        if not isinstance(item, dict):
            raise PromotionError("个别调整格式无效")
        student_id = _positive_int(item.get("student_id"), "学生")
        if student_id in result:
            raise PromotionError("同一学生不能重复调整")
        result[student_id] = _normalize_decision(item, student_id=student_id)
    return result


def _normalize_decision(item: dict, **identity) -> dict:
    action = str(item.get("action") or "").strip()
    if action not in VALID_ACTIONS:
        raise PromotionError("处理方式无效")
    result = {**identity, "action": action}
    if action == "promote":
        result["target_class_id"] = _positive_int(item.get("target_class_id"), "目标班级")
    return result


def _positive_int(value, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{label}无效") from exc
    if result <= 0:
        raise PromotionError(f"{label}无效")
    return result
