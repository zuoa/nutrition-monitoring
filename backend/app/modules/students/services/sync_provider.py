"""学生同步 Provider 的稳定边界。

外部系统的请求方式和字段名只出现在 Provider 中；通用入库服务只接收
``StudentRosterEntry``，因此新增客户接口时不需要修改学生领域逻辑。
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StudentRosterEntry:
    external_id: str
    student_no: str
    name: str
    gender: str = ""
    grade_name: str = ""
    class_name: str = ""

    # 学籍号属于核心学生属性；身份证和宿舍数据当前仅在适配层归一，
    # 入库服务按最小化采集原则不持久化后两类字段。
    registration_no: str = ""
    identity_number: str = ""
    dorm_building: str = ""
    dorm_floor: str = ""
    dorm_room: str = ""


class StudentRosterProvider(Protocol):
    key: str
    label: str

    @property
    def configured(self) -> bool:
        ...

    def fetch_students(self) -> list[StudentRosterEntry]:
        ...
