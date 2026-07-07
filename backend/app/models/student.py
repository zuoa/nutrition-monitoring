# 学生模型已迁移至独立模块 app.modules.students；此处仅做向后兼容的重导出，
# 避免历史代码 `from app.models.student import Student` 失效。
from app.modules.students.models.student import Student, StudentSourceEnum  # noqa: F401
