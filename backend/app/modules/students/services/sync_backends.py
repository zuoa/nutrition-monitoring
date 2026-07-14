"""学生同步后端注册表与工厂。"""

from dataclasses import dataclass
from typing import Protocol

from app.modules.students.services.external_roster_sync_service import ExternalRosterSyncService
from app.modules.students.services.rest_student_provider import RestStudentListProvider


class StudentSyncBackend(Protocol):
    key: str
    label: str
    configured: bool
    mock: bool
    can_sync: bool

    def sync(self) -> dict:
        ...


@dataclass
class DingTalkStudentSyncBackend:
    config: dict
    key: str = "dingtalk"
    label: str = "钉钉家校通讯录"

    @property
    def mock(self) -> bool:
        return bool(self.config.get("DINGTALK_EDU_MOCK")) or not self.config.get("DINGTALK_APP_KEY") or not self.config.get("DINGTALK_APP_SECRET")

    @property
    def configured(self) -> bool:
        return not self.mock

    @property
    def can_sync(self) -> bool:
        # 本地开发保留原有 mock 同步能力。
        return True

    def sync(self) -> dict:
        from app.modules.students.services.dingtalk_edu import DingTalkEduService
        from app.modules.students.services.org_sync_service import OrgSyncService
        from app.modules.students.services.student_sync_service import StudentSyncService

        edu = DingTalkEduService(self.config)
        return {
            "org": OrgSyncService(edu).sync(),
            "students": StudentSyncService(edu).sync(),
        }


@dataclass
class RestStudentSyncBackend:
    config: dict
    key: str = "rest_student_list"
    label: str = "外部学生名单"
    mock: bool = False

    @property
    def configured(self) -> bool:
        return RestStudentListProvider(self.config).configured

    @property
    def can_sync(self) -> bool:
        return self.configured

    def sync(self) -> dict:
        provider = RestStudentListProvider(self.config)
        stats = ExternalRosterSyncService(
            provider,
            school_name=str(self.config.get("STUDENT_SYNC_SCHOOL_NAME") or "默认学校"),
            campus_name=str(self.config.get("STUDENT_SYNC_CAMPUS_NAME") or "默认校区"),
            stage_name=str(self.config.get("STUDENT_SYNC_STAGE_NAME") or "默认学段"),
            deactivate_missing=bool(self.config.get("STUDENT_SYNC_DEACTIVATE_MISSING")),
        ).sync()
        return {"students": stats}


_BACKENDS = {
    "dingtalk": DingTalkStudentSyncBackend,
    "rest_student_list": RestStudentSyncBackend,
}


def get_student_sync_backend(config: dict, provider_name: str | None = None) -> StudentSyncBackend:
    key = str(provider_name or config.get("STUDENT_SYNC_PROVIDER") or "dingtalk").strip().lower()
    backend_class = _BACKENDS.get(key)
    if not backend_class:
        supported = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"不支持的学生同步 Provider：{key}（可选：{supported}）")
    return backend_class(config)
