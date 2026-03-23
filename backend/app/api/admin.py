import logging
from flask import Blueprint, request
from app import db
from app.models import User, Student, RoleEnum
from app.utils.jwt_utils import login_required, role_required, api_ok, api_error
from app.utils.pagination import paginate, paginated_response

bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)


@bp.route("/users", methods=["GET"])
@role_required("admin")
def list_users():
    q = User.query.order_by(User.name)
    if role := request.args.get("role"):
        q = q.filter(User.role == role)
    if request.args.get("active_only") != "false":
        q = q.filter(User.is_active == True)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([u.to_dict() for u in items], total, page, page_size))


@bp.route("/users/<int:user_id>", methods=["PUT"])
@role_required("admin")
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json() or {}

    for field in ["role", "managed_class_ids", "managed_grade_ids", "student_ids", "is_active"]:
        if field in data:
            if field == "role":
                try:
                    setattr(user, field, RoleEnum(data[field]))
                except ValueError:
                    return api_error(f"无效角色：{data[field]}")
            else:
                setattr(user, field, data[field])

    db.session.commit()
    return api_ok(user.to_dict())


@bp.route("/students", methods=["GET"])
@role_required("admin", "teacher", "grade_leader")
def list_students():
    q = Student.query.filter_by(is_active=True)
    user = request.current_user

    # Scope filter
    if user.role.value == "teacher":
        class_ids = user.managed_class_ids or []
        q = q.filter(Student.class_id.in_(class_ids))
    elif user.role.value == "grade_leader":
        grade_ids = user.managed_grade_ids or []
        q = q.filter(Student.grade_id.in_(grade_ids))

    if class_id := request.args.get("class_id"):
        q = q.filter(Student.class_id == class_id)
    if grade_id := request.args.get("grade_id"):
        q = q.filter(Student.grade_id == grade_id)
    if search := request.args.get("search"):
        q = q.filter(db.or_(
            Student.name.ilike(f"%{search}%"),
            Student.student_no.ilike(f"%{search}%"),
        ))

    q = q.order_by(Student.class_id, Student.name)
    items, total, page, page_size = paginate(q)
    return api_ok(paginated_response([s.to_dict() for s in items], total, page, page_size))


@bp.route("/students/<int:student_id>", methods=["PUT"])
@role_required("admin")
def update_student(student_id):
    student = Student.query.get_or_404(student_id)
    data = request.get_json() or {}
    for field in ["student_no", "name", "class_id", "class_name", "grade_id", "grade_name", "card_no", "is_active"]:
        if field in data:
            setattr(student, field, data[field])
    db.session.commit()
    return api_ok(student.to_dict())


@bp.route("/config", methods=["GET"])
@role_required("admin")
def get_config():
    from flask import current_app
    cfg = current_app.config
    # Only expose safe, non-secret config
    return api_ok({
        "nvr_host": cfg.get("NVR_HOST", ""),
        "nvr_port": cfg.get("NVR_PORT", 8080),
        "nvr_channel_ids": cfg.get("NVR_CHANNEL_IDS", []),
        "nvr_meal_windows": cfg.get("NVR_MEAL_WINDOWS", "[]"),
        "extract_fps": cfg.get("EXTRACT_FPS", 2),
        "diff_threshold": cfg.get("DIFF_THRESHOLD", 30),
        "min_event_duration_s": cfg.get("MIN_EVENT_DURATION_S", 0.5),
        "stable_frame_offset_s": cfg.get("STABLE_FRAME_OFFSET_S", 1.0),
        "min_interval_s": cfg.get("MIN_INTERVAL_S", 3.0),
        "time_offset_tolerance": cfg.get("TIME_OFFSET_TOLERANCE", 1),
        "price_tolerance": cfg.get("PRICE_TOLERANCE", 0.5),
        "qwen_model": cfg.get("QWEN_MODEL", "qwen-vl-max"),
        "qwen_max_qps": cfg.get("QWEN_MAX_QPS", 10),
    })
