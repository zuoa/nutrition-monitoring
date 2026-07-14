"""Local organization maintenance with guarded soft-archive semantics."""

from app import db
from app.modules.students.models.organization import (
    School,
    Campus,
    Stage,
    Grade,
    Class,
    StageTypeEnum,
)
from app.modules.students.models.student import Student, EnrollmentStatusEnum


class OrganizationError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


MODEL_BY_KIND = {
    "schools": School,
    "campuses": Campus,
    "stages": Stage,
    "grades": Grade,
    "classes": Class,
}

PARENT_BY_KIND = {
    "campuses": ("school_id", School),
    "stages": ("campus_id", Campus),
    "grades": ("stage_id", Stage),
    "classes": ("grade_id", Grade),
}

CHILD_BY_KIND = {
    "schools": (Campus, "school_id", "校区"),
    "campuses": (Stage, "campus_id", "学段"),
    "stages": (Grade, "stage_id", "年级"),
    "grades": (Class, "grade_id", "班级"),
}


def create_node(kind: str, data: dict):
    model = _model(kind)
    name = _required_name(data)
    values = _validated_values(kind, data, creating=True)
    _ensure_unique_name(kind, name, values)
    node = model(name=name, is_active=True, **values)
    db.session.add(node)
    db.session.commit()
    return node


def update_node(kind: str, node_id: int, data: dict):
    node = _get(kind, node_id)
    name = str(data.get("name", node.name)).strip()
    if not name:
        raise OrganizationError("名称不能为空")
    values = _validated_values(kind, data, creating=False)
    effective = {
        field: values.get(field, getattr(node, field))
        for field, _ in [PARENT_BY_KIND[kind]]
    } if kind in PARENT_BY_KIND else {}
    _ensure_unique_name(kind, name, effective, exclude_id=node.id)
    node.name = name
    for field, value in values.items():
        setattr(node, field, value)
    if "sort_order" in data:
        node.sort_order = _int_value(data["sort_order"], "排序")
    db.session.commit()
    return node


def set_archived(kind: str, node_id: int, archived: bool):
    node = _get(kind, node_id)
    if archived:
        _validate_archive(kind, node)
        node.is_active = False
    else:
        _validate_restore(kind, node)
        node.is_active = True
    db.session.commit()
    return node


def _validated_values(kind: str, data: dict, creating: bool) -> dict:
    values = {}
    if kind in PARENT_BY_KIND:
        field, parent_model = PARENT_BY_KIND[kind]
        if creating or field in data:
            parent_id = _int_value(data.get(field), "上级组织")
            parent = db.session.get(parent_model, parent_id)
            if not parent or not parent.is_active:
                raise OrganizationError("上级组织不存在或已归档")
            values[field] = parent_id
    if kind == "schools" and "code" in data:
        values["code"] = str(data.get("code") or "").strip() or None
    if kind == "stages" and "stage_type" in data:
        raw = str(data.get("stage_type") or "other")
        try:
            values["stage_type"] = StageTypeEnum(raw)
        except ValueError as exc:
            raise OrganizationError("学段类型无效") from exc
    if creating or "sort_order" in data:
        values["sort_order"] = _int_value(data.get("sort_order", 0), "排序")
    return values


def _ensure_unique_name(kind: str, name: str, values: dict, exclude_id: int | None = None):
    model = _model(kind)
    query = model.query.filter(model.name == name)
    if kind in PARENT_BY_KIND:
        parent_field, _ = PARENT_BY_KIND[kind]
        query = query.filter(getattr(model, parent_field) == values[parent_field])
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    duplicate = query.first()
    if duplicate:
        state = "已归档" if not duplicate.is_active else "已存在"
        raise OrganizationError(f"同级组织中{state}同名节点：{name}", 409)


def _validate_archive(kind: str, node):
    child_spec = CHILD_BY_KIND.get(kind)
    if child_spec:
        child_model, parent_field, label = child_spec
        if child_model.query.filter_by(**{parent_field: node.id, "is_active": True}).first():
            raise OrganizationError(f"请先归档或移走该节点下的{label}", 409)
    elif kind == "classes":
        in_school = Student.query.filter_by(
            class_id=node.id,
            enrollment_status=EnrollmentStatusEnum.enrolled,
        ).first()
        if in_school:
            raise OrganizationError("班级仍有在校学生，请先调班或标记毕业", 409)


def _validate_restore(kind: str, node):
    if kind not in PARENT_BY_KIND:
        return
    field, parent_model = PARENT_BY_KIND[kind]
    parent = db.session.get(parent_model, getattr(node, field))
    if not parent or not parent.is_active:
        raise OrganizationError("请先恢复上级组织", 409)


def _model(kind: str):
    model = MODEL_BY_KIND.get(kind)
    if not model:
        raise OrganizationError("组织类型无效", 404)
    return model


def _get(kind: str, node_id: int):
    node = db.session.get(_model(kind), node_id)
    if not node:
        raise OrganizationError("组织节点不存在", 404)
    return node


def _required_name(data: dict) -> str:
    name = str(data.get("name") or "").strip()
    if not name:
        raise OrganizationError("名称不能为空")
    return name


def _int_value(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise OrganizationError(f"{label}无效") from exc
