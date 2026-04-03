from app.models import VideoSource, VideoSourceStatus


def list_video_sources() -> list[VideoSource]:
    return VideoSource.query.order_by(
        VideoSource.is_active.desc(),
        VideoSource.updated_at.desc(),
        VideoSource.id.desc(),
    ).all()


def get_video_source(video_source_id: int) -> VideoSource | None:
    return VideoSource.query.get(video_source_id)


def get_active_video_source() -> VideoSource | None:
    return VideoSource.query.filter_by(
        is_active=True,
        status=VideoSourceStatus.enabled.value,
    ).order_by(VideoSource.updated_at.desc(), VideoSource.id.desc()).first()
