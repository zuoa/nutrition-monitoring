"""student org module: 5-level org tables + student refactor + guardians

Revision ID: 20260707_0009
Revises: 20260608_0008
Create Date: 2026-07-07 00:00:09

重构学生管理为独立模块：
- 新建 5 级组织表 schools/campuses/stages/grades/classes + guardians
- students：旧字符串 class_id/grade_id 改名为 legacy_*；新增整型外键 class_id 及
  钉钉/本地字段
- 数据迁移：自动建默认组织树，按历史 class/grade 编码（取 students 与
  users.managed_*_ids 的并集，覆盖「空班级」）回填外键；把 users.managed_class_ids
  /managed_grade_ids 由旧字符串映射为新整型主键；并把历史 class/grade 报告的
  target_id 由旧字符串编码改写为新整型主键字符串
"""
import json
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from migrations.helpers import (
    add_column_if_not_exists,
    column_exists,
    column_type,
    create_foreign_key_if_not_exists,
    create_index_if_not_exists,
    drop_column_if_exists,
    drop_constraint_if_exists,
    drop_index_if_exists,
    drop_table_if_exists,
    table_exists,
)


revision = "20260707_0009"
down_revision = "20260608_0008"
branch_labels = None
depends_on = None


# create_type=False：类型由 upgrade() 里的 .create(checkfirst=True) 显式建一次，
# 避免 op.create_table 再通过 _on_table_create 重复 CREATE TYPE 报 DuplicateObject。
stage_type_enum = postgresql.ENUM(
    "kindergarten", "primary", "junior", "senior", "other",
    name="stagetypeenum",
    create_type=False,
)
student_source_enum = postgresql.ENUM(
    "dingtalk", "local", "csv",
    name="studentsourceenum",
    create_type=False,
)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _ensure_default_org(bind):
    """确保存在默认 学校/校区/学段/年级，返回各级 id。"""
    if not bind.execute(sa.text("SELECT 1 FROM schools LIMIT 1")).fetchone():
        bind.execute(sa.text(
            "INSERT INTO schools (name, sort_order, is_active, created_at, updated_at) "
            "VALUES ('默认学校', 0, true, now(), now())"
        ))
    school_id = bind.execute(sa.text("SELECT id FROM schools ORDER BY id LIMIT 1")).scalar()

    if not bind.execute(sa.text("SELECT 1 FROM campuses LIMIT 1")).fetchone():
        bind.execute(sa.text(
            "INSERT INTO campuses (school_id, name, sort_order, is_active, created_at, updated_at) "
            "VALUES (:sid, '默认校区', 0, true, now(), now())"
        ), {"sid": school_id})
    campus_id = bind.execute(sa.text("SELECT id FROM campuses ORDER BY id LIMIT 1")).scalar()

    if not bind.execute(sa.text("SELECT 1 FROM stages LIMIT 1")).fetchone():
        bind.execute(sa.text(
            "INSERT INTO stages (campus_id, name, stage_type, sort_order, is_active, created_at, updated_at) "
            "VALUES (:cid, '默认学段', 'other', 0, true, now(), now())"
        ), {"cid": campus_id})
    stage_id = bind.execute(sa.text("SELECT id FROM stages ORDER BY id LIMIT 1")).scalar()

    if not bind.execute(sa.text("SELECT 1 FROM grades LIMIT 1")).fetchone():
        bind.execute(sa.text(
            "INSERT INTO grades (stage_id, name, sort_order, is_active, created_at, updated_at) "
            "VALUES (:sid, '默认年级', 0, true, now(), now())"
        ), {"sid": stage_id})
    default_grade_id = bind.execute(sa.text("SELECT id FROM grades ORDER BY id LIMIT 1")).scalar()

    return school_id, campus_id, stage_id, default_grade_id


def _migrate_student_org(bind):
    """根据历史 class/grade 编码构建组织树并回填外键。

    历史编码取自 students 与 users.managed_*_ids 的**并集**：若只从 students 取，
    「空班级（当前无在校生）」不会建出 Class 行，负责该班的教师其 managed_class_ids
    在 remap 时被静默丢弃 → 报告查看/推送整体失效；同时这些学生 legacy 编码若仅出现在
    作用域里也会回填不到 class_id。取并集可一并解决。
    """
    school_id, campus_id, stage_id, default_grade_id = _ensure_default_org(bind)

    # 0) 采集所有出现过的历史编码：students（带代表性名称）+ users.managed_*_ids
    student_grade_rows = bind.execute(sa.text(
        "SELECT legacy_grade_code, MAX(grade_name) AS grade_name FROM students "
        "WHERE legacy_grade_code IS NOT NULL AND legacy_grade_code <> '' "
        "GROUP BY legacy_grade_code"
    )).fetchall()
    grade_name_by_code = {code: name for code, name in student_grade_rows}

    student_class_rows = bind.execute(sa.text(
        "SELECT legacy_class_code, MAX(class_name) AS class_name, "
        "MAX(legacy_grade_code) AS grade_code FROM students "
        "WHERE legacy_class_code IS NOT NULL AND legacy_class_code <> '' "
        "GROUP BY legacy_class_code"
    )).fetchall()
    class_name_by_code = {code: name for code, name, _code in student_class_rows}
    class_grade_by_code = {code: gcode for code, _name, gcode in student_class_rows}

    user_rows = bind.execute(sa.text(
        "SELECT id, managed_class_ids, managed_grade_ids FROM users "
        "WHERE managed_class_ids IS NOT NULL OR managed_grade_ids IS NOT NULL"
    )).fetchall()
    user_class_codes = set()
    user_grade_codes = set()
    for _uid, mcids, mgids in user_rows:
        for c in _as_list(mcids):
            if isinstance(c, str) and c:
                user_class_codes.add(c)
        for g in _as_list(mgids):
            if isinstance(g, str) and g:
                user_grade_codes.add(g)

    all_grade_codes = set(grade_name_by_code) | user_grade_codes
    all_class_codes = set(class_name_by_code) | user_class_codes

    # 1) grade_map：含仅出现在教师作用域里的年级
    grade_map = {}
    for code in all_grade_codes:
        gid = bind.execute(sa.text(
            "INSERT INTO grades (stage_id, name, sort_order, is_active, created_at, updated_at) "
            "VALUES (:sid, :name, 0, true, now(), now()) RETURNING id"
        ), {"sid": stage_id, "name": grade_name_by_code.get(code) or code}).scalar()
        grade_map[code] = gid

    # 2) class_map：含仅出现在教师作用域里的「空班级」；缺年级信息则挂默认年级
    class_map = {}
    for code in all_class_codes:
        grade_id = grade_map.get(class_grade_by_code.get(code)) or default_grade_id
        cid = bind.execute(sa.text(
            "INSERT INTO classes (grade_id, name, sort_order, is_active, created_at, updated_at) "
            "VALUES (:gid, :name, 0, true, now(), now()) RETURNING id"
        ), {"gid": grade_id, "name": class_name_by_code.get(code) or code}).scalar()
        class_map[code] = cid

    # 3) 回填 students.class_id（覆盖全部学生历史编码；空班级无学生，UPDATE 自然 0 行）
    for code, cid in class_map.items():
        bind.execute(sa.text(
            "UPDATE students SET class_id = :cid WHERE legacy_class_code = :code"
        ), {"cid": cid, "code": code})

    # 4) 改写历史 class/grade 报告的 target_id（旧字符串编码 → 新整型主键字符串）。
    #    重构后端点按整型主键查询 reports，若不改写历史报告会全部查不到。
    for code, cid in class_map.items():
        bind.execute(sa.text(
            "UPDATE reports SET target_id = :new "
            "WHERE target_id = :old AND report_type = 'class_weekly'"
        ), {"new": str(cid), "old": code})
    for code, gid in grade_map.items():
        bind.execute(sa.text(
            "UPDATE reports SET target_id = :new "
            "WHERE target_id = :old AND report_type = 'grade_monthly'"
        ), {"new": str(gid), "old": code})

    # 5) users.managed_class_ids / managed_grade_ids 由旧字符串映射为新主键
    for uid, mcids, mgids in user_rows:
        new_mcids = [class_map[c] for c in _as_list(mcids) if isinstance(c, str) and c in class_map]
        new_mgids = [grade_map[g] for g in _as_list(mgids) if isinstance(g, str) and g in grade_map]
        bind.execute(sa.text(
            "UPDATE users SET managed_class_ids = :c, managed_grade_ids = :g WHERE id = :id"
        ), {"c": json.dumps(new_mcids), "g": json.dumps(new_mgids), "id": uid})


def upgrade():
    bind = op.get_bind()
    stage_type_enum.create(bind, checkfirst=True)
    student_source_enum.create(bind, checkfirst=True)

    # --- 组织 5 级表 ---
    if not table_exists("schools"):
        op.create_table(
            "schools",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_node_id", sa.String(length=64), nullable=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("code", sa.String(length=64), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_node_id", name="uq_schools_dingtalk_node_id"),
        )
    create_index_if_not_exists("ix_schools_dingtalk_node_id", "schools", ["dingtalk_node_id"], unique=False)

    if not table_exists("campuses"):
        op.create_table(
            "campuses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_node_id", sa.String(length=64), nullable=True),
            sa.Column("school_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["school_id"], ["schools.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_node_id", name="uq_campuses_dingtalk_node_id"),
        )
    create_index_if_not_exists("ix_campuses_dingtalk_node_id", "campuses", ["dingtalk_node_id"], unique=False)
    create_index_if_not_exists("ix_campuses_school_id", "campuses", ["school_id"], unique=False)

    if not table_exists("stages"):
        op.create_table(
            "stages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_node_id", sa.String(length=64), nullable=True),
            sa.Column("campus_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("stage_type", stage_type_enum, nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["campus_id"], ["campuses.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_node_id", name="uq_stages_dingtalk_node_id"),
        )
    create_index_if_not_exists("ix_stages_dingtalk_node_id", "stages", ["dingtalk_node_id"], unique=False)
    create_index_if_not_exists("ix_stages_campus_id", "stages", ["campus_id"], unique=False)

    if not table_exists("grades"):
        op.create_table(
            "grades",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_node_id", sa.String(length=64), nullable=True),
            sa.Column("stage_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["stage_id"], ["stages.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_node_id", name="uq_grades_dingtalk_node_id"),
        )
    create_index_if_not_exists("ix_grades_dingtalk_node_id", "grades", ["dingtalk_node_id"], unique=False)
    create_index_if_not_exists("ix_grades_stage_id", "grades", ["stage_id"], unique=False)

    if not table_exists("classes"):
        op.create_table(
            "classes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("dingtalk_node_id", sa.String(length=64), nullable=True),
            sa.Column("grade_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["grade_id"], ["grades.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dingtalk_node_id", name="uq_classes_dingtalk_node_id"),
        )
    create_index_if_not_exists("ix_classes_dingtalk_node_id", "classes", ["dingtalk_node_id"], unique=False)
    create_index_if_not_exists("ix_classes_grade_id", "classes", ["grade_id"], unique=False)

    # --- students 改造 ---
    # 旧字符串列改名（保留为 legacy_*），并放宽为可空，供后续 DingTalk 同步写入 NULL
    student_org_backfill_needed = False
    class_id_type = column_type("students", "class_id")
    legacy_class_id_exists = column_exists("students", "class_id") and not isinstance(class_id_type, sa.Integer)
    if legacy_class_id_exists and not column_exists("students", "legacy_class_code"):
        op.alter_column("students", "class_id", new_column_name="legacy_class_code")
        student_org_backfill_needed = True
    if column_exists("students", "grade_id") and not column_exists("students", "legacy_grade_code"):
        op.alter_column("students", "grade_id", new_column_name="legacy_grade_code")
        student_org_backfill_needed = True
    if column_exists("students", "legacy_class_code"):
        op.alter_column("students", "legacy_class_code", nullable=True)
    if column_exists("students", "legacy_grade_code"):
        op.alter_column("students", "legacy_grade_code", nullable=True)

    class_id_missing_before_add = not column_exists("students", "class_id")
    add_column_if_not_exists("students", sa.Column("class_id", sa.Integer(), nullable=True))
    if class_id_missing_before_add and column_exists("students", "legacy_class_code"):
        student_org_backfill_needed = True
    create_foreign_key_if_not_exists("fk_students_class_id_classes", "students", "classes", ["class_id"], ["id"])
    create_index_if_not_exists("ix_students_class_id", "students", ["class_id"], unique=False)

    add_column_if_not_exists("students", sa.Column("dingtalk_user_id", sa.String(length=64), nullable=True))
    create_index_if_not_exists("ix_students_dingtalk_user_id", "students", ["dingtalk_user_id"], unique=False)

    add_column_if_not_exists("students", sa.Column("gender", sa.String(length=16), nullable=True))
    add_column_if_not_exists("students", sa.Column("source", student_source_enum, nullable=False, server_default="local"))
    add_column_if_not_exists("students", sa.Column("is_locally_disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    add_column_if_not_exists("students", sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True))
    add_column_if_not_exists("students", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    # --- 数据迁移：构建默认组织树 + 回填外键 + 转换用户作用域 ---
    if student_org_backfill_needed and column_exists("students", "legacy_class_code") and column_exists("students", "class_id"):
        _migrate_student_org(bind)

    # --- guardians ---
    if not table_exists("guardians"):
        op.create_table(
            "guardians",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("student_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("relation", sa.String(length=32), nullable=True),
            sa.Column("dingtalk_user_id", sa.String(length=64), nullable=True),
            sa.Column("phone", sa.String(length=32), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["student_id"], ["students.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    create_index_if_not_exists("ix_guardians_student_id", "guardians", ["student_id"], unique=False)
    create_index_if_not_exists("ix_guardians_dingtalk_user_id", "guardians", ["dingtalk_user_id"], unique=False)
    create_index_if_not_exists("ix_guardians_user_id", "guardians", ["user_id"], unique=False)


def downgrade():
    # students 回退
    drop_index_if_exists("ix_students_dingtalk_user_id", table_name="students")
    drop_index_if_exists("ix_students_class_id", table_name="students")
    drop_constraint_if_exists("fk_students_class_id_classes", "students", type_="foreignkey")
    drop_column_if_exists("students", "updated_at")
    drop_column_if_exists("students", "sync_at")
    drop_column_if_exists("students", "is_locally_disabled")
    drop_column_if_exists("students", "source")
    drop_column_if_exists("students", "gender")
    drop_column_if_exists("students", "dingtalk_user_id")
    drop_column_if_exists("students", "class_id")
    if column_exists("students", "legacy_grade_code"):
        op.alter_column("students", "legacy_grade_code", nullable=True)
    if column_exists("students", "legacy_class_code"):
        op.alter_column("students", "legacy_class_code", nullable=False)
        op.alter_column("students", "legacy_class_code", new_column_name="class_id")
    if column_exists("students", "legacy_grade_code"):
        op.alter_column("students", "legacy_grade_code", new_column_name="grade_id")

    drop_index_if_exists("ix_guardians_user_id", table_name="guardians")
    drop_index_if_exists("ix_guardians_dingtalk_user_id", table_name="guardians")
    drop_index_if_exists("ix_guardians_student_id", table_name="guardians")
    drop_table_if_exists("guardians")

    drop_index_if_exists("ix_classes_grade_id", table_name="classes")
    drop_index_if_exists("ix_classes_dingtalk_node_id", table_name="classes")
    drop_table_if_exists("classes")
    drop_index_if_exists("ix_grades_stage_id", table_name="grades")
    drop_index_if_exists("ix_grades_dingtalk_node_id", table_name="grades")
    drop_table_if_exists("grades")
    drop_index_if_exists("ix_stages_campus_id", table_name="stages")
    drop_index_if_exists("ix_stages_dingtalk_node_id", table_name="stages")
    drop_table_if_exists("stages")
    drop_index_if_exists("ix_campuses_school_id", table_name="campuses")
    drop_index_if_exists("ix_campuses_dingtalk_node_id", table_name="campuses")
    drop_table_if_exists("campuses")
    drop_index_if_exists("ix_schools_dingtalk_node_id", table_name="schools")
    drop_table_if_exists("schools")

    student_source_enum.drop(op.get_bind(), checkfirst=True)
    stage_type_enum.drop(op.get_bind(), checkfirst=True)
