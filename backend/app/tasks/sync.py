import logging
from datetime import datetime, timezone
from celery_app import celery
from app import db
from app.models import User, RoleEnum

logger = logging.getLogger(__name__)
ROOT_DEPARTMENT_ID = "1"
ROOT_DEPARTMENT_NAME = "根部门"


@celery.task(name="app.tasks.sync.sync_dingtalk_org")
def sync_dingtalk_org():
    from flask import current_app
    from app.services.dingtalk import DingTalkService

    cfg = current_app.config
    dt = DingTalkService(cfg)

    try:
        logger.info(
            "Starting DingTalk org sync: app_key_configured=%s app_secret_configured=%s agent_id_configured=%s corp_id_configured=%s",
            bool(cfg.get("DINGTALK_APP_KEY")),
            bool(cfg.get("DINGTALK_APP_SECRET")),
            bool(cfg.get("DINGTALK_AGENT_ID")),
            bool(cfg.get("DINGTALK_CORP_ID")),
        )
        raw_depts = dt.get_department_list()
        logger.info("DingTalk department/list returned %s departments", len(raw_depts) if isinstance(raw_depts, list) else "invalid")
        depts = _normalize_departments(raw_depts)
        logger.info("DingTalk org sync will scan %s departments including root", len(depts))
        synced = 0
        created = 0
        updated = 0

        for dept in depts:
            dept_id = str(dept.get("id", ""))
            dept_name = dept.get("name", "")
            offset = 0
            dept_synced = 0
            logger.info("Syncing DingTalk department %s (%s)", dept_id, dept_name or "-")

            while True:
                data = dt.get_department_users(int(dept_id), offset=offset)
                _ensure_dingtalk_success(data, f"获取部门 {dept_id} 用户")
                users_data = data.get("userlist", [])
                has_more = data.get("hasMore", False)
                logger.info(
                    "DingTalk department %s page offset=%s returned users=%s has_more=%s errcode=%s errmsg=%s",
                    dept_id,
                    offset,
                    len(users_data) if isinstance(users_data, list) else "invalid",
                    has_more,
                    data.get("errcode"),
                    data.get("errmsg"),
                )

                for ud in users_data:
                    action = _upsert_user(ud, dept_id, dept_name)
                    if action == "created":
                        created += 1
                    elif action == "updated":
                        updated += 1
                    synced += 1
                    dept_synced += 1

                if not has_more:
                    break
                offset += max(len(users_data), 100)

            logger.info("DingTalk department %s sync finished: %s users", dept_id, dept_synced)

        db.session.commit()
        logger.info("DingTalk org sync complete: %s users, created=%s, updated=%s", synced, created, updated)
        return synced

    except Exception as e:
        db.session.rollback()
        logger.error(f"DingTalk org sync failed: {e}", exc_info=True)
        raise


def _normalize_departments(depts) -> list[dict]:
    result = [{"id": ROOT_DEPARTMENT_ID, "name": ROOT_DEPARTMENT_NAME}]
    seen = {ROOT_DEPARTMENT_ID}

    if not isinstance(depts, list):
        return result

    for dept in depts:
        if not isinstance(dept, dict):
            continue
        dept_id = str(dept.get("id") or "").strip()
        if not dept_id or dept_id in seen:
            continue
        seen.add(dept_id)
        result.append({
            "id": dept_id,
            "name": str(dept.get("name") or ""),
        })
    return result


def _ensure_dingtalk_success(data: dict, action: str):
    if not isinstance(data, dict):
        logger.error("%s failed: invalid response format: %r", action, data)
        raise RuntimeError(f"{action}失败：钉钉返回格式无效")
    errcode = data.get("errcode", 0)
    if errcode not in (0, "0", None):
        logger.error("%s failed: errcode=%s errmsg=%s response=%s", action, data.get("errcode"), data.get("errmsg"), data)
        raise RuntimeError(f"{action}失败：{data}")


def _upsert_user(ud: dict, dept_id: str, dept_name: str):
    dingtalk_user_id = ud.get("userid", "")
    if not dingtalk_user_id:
        logger.warning("Skip DingTalk user without userid in department %s: %s", dept_id, ud)
        return "skipped"

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
        return "updated"
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
        return "created"


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
