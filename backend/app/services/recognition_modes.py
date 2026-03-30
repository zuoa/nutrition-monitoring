LOCAL_RECOGNITION_MODE = "local_embedding"
LEGACY_LOCAL_RECOGNITION_MODE = "yolo_embedding_local"
LOCAL_RECOGNITION_MODES = {
    LOCAL_RECOGNITION_MODE,
    LEGACY_LOCAL_RECOGNITION_MODE,
}


def is_local_recognition_mode(mode: str | None) -> bool:
    return str(mode or "").strip() in LOCAL_RECOGNITION_MODES


def normalize_recognition_mode(mode: str | None) -> str:
    raw_mode = str(mode or "").strip()
    if raw_mode in LOCAL_RECOGNITION_MODES:
        return LOCAL_RECOGNITION_MODE
    return raw_mode
