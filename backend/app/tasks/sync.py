import logging
from datetime import datetime, timezone
from celery_app import celery
from app import db
from app.models import User, RoleEnum

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.sync.sync_dingtalk_org")
def sync_dingtalk_org():
    from flask import current_app
    from app.services.dingtalk import DingTalkService

    cfg = current_app.config
    dt = DingTalkService(cfg)

    try:
        depts = dt.get_department_list()
        synced = 0

        for dept in depts:
            dept_id = str(dept.get("id", ""))
            dept_name = dept.get("name", "")
            offset = 0

            while True:
                data = dt.get_department_users(int(dept_id), offset=offset)
                users_data = data.get("userlist", [])
                has_more = data.get("hasMore", False)

                for ud in users_data:
                    _upsert_user(ud, dept_id, dept_name)
                    synced += 1

                if not has_more:
                    break
                offset += len(users_data)

        db.session.commit()
        logger.info(f"DingTalk org sync complete: {synced} users")
        return synced

    except Exception as e:
        db.session.rollback()
        logger.error(f"DingTalk org sync failed: {e}", exc_info=True)
        raise


def _upsert_user(ud: dict, dept_id: str, dept_name: str):
    dingtalk_user_id = ud.get("userid", "")
    if not dingtalk_user_id:
        return

    user = User.query.filter_by(dingtalk_user_id=dingtalk_user_id).first()
    now = datetime.now(timezone.utc)

    # Infer role from job_number / title
    role = _infer_role(ud)

    if user:
        user.name = ud.get("name", user.name)
        user.dept_id = dept_id
        user.dept_name = dept_name
        user.sync_at = now
        if not user.is_active:
            user.is_active = True
    else:
        user = User(
            dingtalk_user_id=dingtalk_user_id,
            name=ud.get("name", ""),
            role=role,
            dept_id=dept_id,
            dept_name=dept_name,
            is_active=True,
            sync_at=now,
        )
        db.session.add(user)


def _infer_role(ud: dict) -> RoleEnum:
    title = (ud.get("title") or "").lower()
    job_number = (ud.get("job_number") or "").lower()
    manager = ud.get("is_leader_in_dept")

    if "年级" in title or "grade" in job_number:
        return RoleEnum.grade_leader
    if "班主任" in title or "teacher" in job_number or "teacher" in title:
        return RoleEnum.teacher
    if "食堂" in title or "canteen" in job_number:
        return RoleEnum.canteen_manager
    if manager:
        return RoleEnum.grade_leader
    return RoleEnum.teacher
