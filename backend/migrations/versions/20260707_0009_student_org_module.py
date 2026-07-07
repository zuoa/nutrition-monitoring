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


revision = "20260707_0009"
down_revision = "20260608_0008"
branch_labels = None
depends_on = None


stage_type_enum = sa.Enum(
    "kindergarten", "primary", "junior", "senior", "other",
    name="stagetypeenum",
)
student_source_enum = sa.Enum(
    "dingtalk", "local", "csv",
    name="studentsourceenum",
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
    op.create_index("ix_schools_dingtalk_node_id", "schools", ["dingtalk_node_id"], unique=False)

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
    op.create_index("ix_campuses_dingtalk_node_id", "campuses", ["dingtalk_node_id"], unique=False)
    op.create_index("ix_campuses_school_id", "campuses", ["school_id"], unique=False)

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
    op.create_index("ix_stages_dingtalk_node_id", "stages", ["dingtalk_node_id"], unique=False)
    op.create_index("ix_stages_campus_id", "stages", ["campus_id"], unique=False)

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
    op.create_index("ix_grades_dingtalk_node_id", "grades", ["dingtalk_node_id"], unique=False)
    op.create_index("ix_grades_stage_id", "grades", ["stage_id"], unique=False)

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
    op.create_index("ix_classes_dingtalk_node_id", "classes", ["dingtalk_node_id"], unique=False)
    op.create_index("ix_classes_grade_id", "classes", ["grade_id"], unique=False)

    # --- students 改造 ---
    # 旧字符串列改名（保留为 legacy_*），并放宽为可空，供后续 DingTalk 同步写入 NULL
    op.alter_column("students", "class_id", new_column_name="legacy_class_code")
    op.alter_column("students", "grade_id", new_column_name="legacy_grade_code")
    op.alter_column("students", "legacy_class_code", nullable=True)
    op.alter_column("students", "legacy_grade_code", nullable=True)

    op.add_column("students", sa.Column("class_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_students_class_id_classes", "students", "classes", ["class_id"], ["id"])
    op.create_index("ix_students_class_id", "students", ["class_id"], unique=False)

    op.add_column("students", sa.Column("dingtalk_user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_students_dingtalk_user_id", "students", ["dingtalk_user_id"], unique=False)

    op.add_column("students", sa.Column("gender", sa.String(length=16), nullable=True))
    op.add_column("students", sa.Column("source", student_source_enum, nullable=False, server_default="local"))
    op.add_column("students", sa.Column("is_locally_disabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("students", sa.Column("sync_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("students", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    # --- 数据迁移：构建默认组织树 + 回填外键 + 转换用户作用域 ---
    _migrate_student_org(bind)

    # --- guardians ---
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
    op.create_index("ix_guardians_student_id", "guardians", ["student_id"], unique=False)
    op.create_index("ix_guardians_dingtalk_user_id", "guardians", ["dingtalk_user_id"], unique=False)
    op.create_index("ix_guardians_user_id", "guardians", ["user_id"], unique=False)


def downgrade():
    # students 回退
    op.drop_index("ix_students_dingtalk_user_id", table_name="students")
    op.drop_index("ix_students_class_id", table_name="students")
    op.drop_constraint("fk_students_class_id_classes", "students", type_="foreignkey")
    op.drop_column("students", "updated_at")
    op.drop_column("students", "sync_at")
    op.drop_column("students", "is_locally_disabled")
    op.drop_column("students", "source")
    op.drop_column("students", "gender")
    op.drop_column("students", "dingtalk_user_id")
    op.drop_column("students", "class_id")
    op.alter_column("students", "legacy_grade_code", nullable=True)
    op.alter_column("students", "legacy_class_code", nullable=False)
    op.alter_column("students", "legacy_class_code", new_column_name="class_id")
    op.alter_column("students", "legacy_grade_code", new_column_name="grade_id")

    op.drop_index("ix_guardians_user_id", table_name="guardians")
    op.drop_index("ix_guardians_dingtalk_user_id", table_name="guardians")
    op.drop_index("ix_guardians_student_id", table_name="guardians")
    op.drop_table("guardians")

    op.drop_index("ix_classes_grade_id", table_name="classes")
    op.drop_index("ix_classes_dingtalk_node_id", table_name="classes")
    op.drop_table("classes")
    op.drop_index("ix_grades_stage_id", table_name="grades")
    op.drop_index("ix_grades_dingtalk_node_id", table_name="grades")
    op.drop_table("grades")
    op.drop_index("ix_stages_campus_id", table_name="stages")
    op.drop_index("ix_stages_dingtalk_node_id", table_name="stages")
    op.drop_table("stages")
    op.drop_index("ix_campuses_school_id", table_name="campuses")
    op.drop_index("ix_campuses_dingtalk_node_id", table_name="campuses")
    op.drop_table("campuses")
    op.drop_index("ix_schools_dingtalk_node_id", table_name="schools")
    op.drop_table("schools")

    student_source_enum.drop(op.get_bind(), checkfirst=True)
    stage_type_enum.drop(op.get_bind(), checkfirst=True)
