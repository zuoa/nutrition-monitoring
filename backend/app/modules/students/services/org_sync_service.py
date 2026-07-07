"""把钉钉家校通讯录节点树同步到本地 5 级组织表。

按 classic 结构以「深度」映射：depth 0=学校, 1=校区, 2=学段, 3=年级, 4=班级。
对每级按 ``dingtalk_node_id`` upsert；本轮未出现在钉钉的节点（且非本地手工创建）
置 ``is_active=False``。本地手工创建（dingtalk_node_id 为空）的节点永不被停用。
"""
import logging
from collections import deque
from datetime import datetime, timezone

from app import db
from app.modules.students.models.organization import (
    Campus,
    Class,
    Grade,
    School,
    Stage,
    StageTypeEnum,
)

logger = logging.getLogger(__name__)

# 深度 → (模型, 父级 FK 字段, 父级模型)
_LEVEL_MAP = {
    1: (Campus, "school_id", School),
    2: (Stage, "campus_id", Campus),
    3: (Grade, "stage_id", Stage),
    4: (Class, "grade_id", Grade),
}

# 学段名称 → 枚举（尽力推断）
_STAGE_TYPE_BY_NAME = {
    "幼儿园": StageTypeEnum.kindergarten,
    "小学": StageTypeEnum.primary,
    "初中": StageTypeEnum.junior,
    "高中": StageTypeEnum.senior,
}


def _utcnow():
    return datetime.now(timezone.utc)


def _infer_stage_type(name: str):
    for key, val in _STAGE_TYPE_BY_NAME.items():
        if key in (name or ""):
            return val
    return None


class OrgSyncService:
    def __init__(self, edu_service):
        self.edu = edu_service

    def sync(self) -> dict:
        now = _utcnow()
        stats = {"school": 0, "campus": 0, "stage": 0, "grade": 0, "class": 0}
        seen = {1: set(), 2: set(), 3: set(), 4: set()}  # 各深度见到的 node_id

        root = self.edu.get_school_root()
        if not root:
            logger.warning("钉钉家校通讯录未返回学校根节点，跳过组织同步")
            return stats
        school = self._upsert_school(root["node_id"], root["name"], now)
        stats["school"] = 1

        # BFS：以 (node_id, depth) 入队，school 为 depth 0
        queue = deque([(root["node_id"], 0)])
        next_sort = {}
        while queue:
            node_id, depth = queue.popleft()
            try:
                children = self.edu.get_node_children(node_id) or []
            except Exception as exc:
                logger.warning("获取节点 %s 子级失败（depth=%s）：%s", node_id, depth, exc)
                continue
            for idx, child in enumerate(children):
                child_id = str(child.get("node_id") or "").strip()
                if not child_id:
                    continue
                child_depth = depth + 1
                # 深度超过 4 的节点（custom 结构里班级之下不应再有）按班级处理
                eff_depth = child_depth if child_depth <= 4 else 4
                self._upsert_node(eff_depth, child_id, child.get("name", ""),
                                  node_id, school.id, idx, now, seen)
                queue.append((child_id, child_depth))

        self._deactivate_missing(seen, now)
        db.session.commit()
        for key in ("campus", "stage", "grade", "class"):
            stats[key] = len(seen[{"campus": 1, "stage": 2, "grade": 3, "class": 4}[key]])
        logger.info("组织同步完成：%s", stats)
        return stats

    def _upsert_school(self, node_id: str, name: str, now) -> School:
        school = School.query.filter_by(dingtalk_node_id=node_id).first()
        if not school:
            school = School(dingtalk_node_id=node_id)
            db.session.add(school)
        school.name = name or school.name or "学校"
        school.is_active = True
        school.sync_at = now
        db.session.flush()
        return school

    def _upsert_node(self, depth, node_id, name, parent_node_id, school_id, idx, now, seen):
        model, fk_field, parent_model = _LEVEL_MAP[depth]
        # 找父级本地行（按 dingtalk_node_id）
        parent_row = parent_model.query.filter_by(dingtalk_node_id=parent_node_id).first()
        if not parent_row:
            logger.warning("找不到父级节点 dingtalk_node_id=%s（depth=%s），跳过 %s", parent_node_id, depth, node_id)
            return
        row = model.query.filter_by(dingtalk_node_id=node_id).first()
        if not row:
            row = model(dingtalk_node_id=node_id)
            db.session.add(row)
        row.name = name or row.name
        setattr(row, fk_field, parent_row.id)
        row.sort_order = idx
        row.is_active = True
        row.sync_at = now
        if depth == 2 and isinstance(row, Stage):
            row.stage_type = row.stage_type or _infer_stage_type(name)
        db.session.flush()
        seen[depth].add(node_id)

    def _deactivate_missing(self, seen, now):
        for depth, model in [(1, Campus), (2, Stage), (3, Grade), (4, Class)]:
            present = seen.get(depth, set())
            if not present:
                continue
            model.query.filter(
                model.dingtalk_node_id.isnot(None),
                ~model.dingtalk_node_id.in_(present),
            ).update({model.is_active: False, model.sync_at: now}, synchronize_session=False)
