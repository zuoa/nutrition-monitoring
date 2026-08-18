"""Resolve the source-DB clock offset for a given moment in time.

Matching needs the clock skew between the card-system database and the local
platform at the *moment a transaction happened*, not a single global value:
the source clock drifts, so ``time_calibration_samples`` holds one measurement
per minute. Resolution order for a transaction time:

1. The sample taken in the same minute as the transaction;
2. The sample nearest in time to the transaction;
3. The manually configured ``TIME_OFFSET_CALIBRATION`` fallback (only when no
   samples exist at all).

Samples store ``source_time - local_time``. A source timestamp is converted to
the local/video clock by applying the inverse value. The manual fallback is
already configured as a direct adjustment, so it is not inverted.
"""

import bisect
import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_SYSTEM = "ztk_plus"


@dataclass(frozen=True)
class TimeOffsetResolution:
    """The adjustment selected for one transaction moment."""

    # Direct adjustment applied to a source-system transaction timestamp.
    offset_seconds: float
    method: str
    sample: object | None = None
    sample_distance_seconds: float | None = None
    # Raw measured skew: source_time - local_time. Manual fallback has no
    # measurement, so this remains None.
    measured_offset_seconds: float | None = None


class TimeOffsetResolver:
    """In-memory offset lookup over a window of calibration samples."""

    def __init__(self, samples, *, fallback_offset: float = 0.0, tz: ZoneInfo | None = None):
        self.fallback_offset = float(fallback_offset or 0.0)
        self.tz = tz or ZoneInfo("Asia/Shanghai")
        # Multiple samples can land in the same minute (task retries); keep the
        # most recent one per minute bucket.
        self._by_minute: dict[datetime, object] = {}
        minute_ids: dict[datetime, tuple] = {}
        sorted_samples = sorted(samples, key=lambda s: s.source_time)
        for sample in sorted_samples:
            minute = sample.source_time.replace(second=0, microsecond=0)
            created_at = sample.created_at or datetime.min
            # SQLite may return a naive created_at while PostgreSQL returns an
            # aware one. The wall-clock value is sufficient for ordering
            # retries within a single minute and keeps the comparison stable.
            recency = (created_at.replace(tzinfo=None), sample.id or 0)
            if minute not in minute_ids or recency > minute_ids[minute]:
                minute_ids[minute] = recency
                self._by_minute[minute] = sample
        self._samples = sorted_samples
        self._times = [sample.source_time for sample in sorted_samples]

    @classmethod
    def for_time_range(
        cls,
        start: datetime | None,
        end: datetime | None,
        *,
        fallback_offset: float = 0.0,
        source_system: str = DEFAULT_SOURCE_SYSTEM,
        tz: ZoneInfo | None = None,
    ) -> "TimeOffsetResolver":
        """Load the samples covering [start, end] plus the nearest sample on
        each side, so nearest-in-time lookup is exact even at the edges.

        ``start``/``end`` are naive datetimes on the source clock (or tz-aware,
        which are converted). ``None`` bounds are open-ended.
        """
        from app.models import TimeCalibrationSample

        tz = tz or ZoneInfo("Asia/Shanghai")
        start_naive = cls._as_source_naive(start, tz)
        end_naive = cls._as_source_naive(end, tz)

        query = TimeCalibrationSample.query.filter_by(source_system=source_system)
        window_query = query
        if start_naive is not None:
            window_query = window_query.filter(TimeCalibrationSample.source_time >= start_naive)
        if end_naive is not None:
            window_query = window_query.filter(TimeCalibrationSample.source_time <= end_naive)
        samples = list(window_query.all())

        if start_naive is not None:
            before = (
                query.filter(TimeCalibrationSample.source_time < start_naive)
                .order_by(TimeCalibrationSample.source_time.desc())
                .first()
            )
            if before:
                samples.append(before)
        if end_naive is not None:
            after = (
                query.filter(TimeCalibrationSample.source_time > end_naive)
                .order_by(TimeCalibrationSample.source_time.asc())
                .first()
            )
            if after:
                samples.append(after)

        return cls(samples, fallback_offset=fallback_offset, tz=tz)

    @classmethod
    def _as_source_naive(cls, value: datetime | None, tz: ZoneInfo) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(tz).replace(tzinfo=None)
        return value.replace(tzinfo=None)

    @property
    def has_samples(self) -> bool:
        return bool(self._times)

    def offset_for(self, moment: datetime) -> float:
        """Offset (seconds) to apply to a transaction at ``moment``.

        ``moment`` may be tz-aware or naive; it is interpreted on the source
        clock either way.
        """
        return self.resolve(moment).offset_seconds

    def resolve(self, moment: datetime) -> TimeOffsetResolution:
        """Return the offset plus the sample and lookup method that produced it."""
        if not self._times:
            return TimeOffsetResolution(
                offset_seconds=self.fallback_offset,
                method="manual_fallback",
            )

        moment_naive = self._as_source_naive(moment, self.tz)

        # 1. Same-minute sample.
        minute = moment_naive.replace(second=0, microsecond=0)
        if minute in self._by_minute:
            sample = self._by_minute[minute]
            return TimeOffsetResolution(
                offset_seconds=-float(sample.offset_seconds),
                method="same_minute",
                sample=sample,
                sample_distance_seconds=abs((sample.source_time - moment_naive).total_seconds()),
                measured_offset_seconds=float(sample.offset_seconds),
            )

        # 2. Nearest sample in time.
        idx = bisect.bisect_left(self._times, moment_naive)
        best = None
        for candidate in (idx - 1, idx):
            if 0 <= candidate < len(self._times):
                distance = abs((self._times[candidate] - moment_naive).total_seconds())
                if best is None or distance < best[0]:
                    best = (distance, self._samples[candidate])
        return TimeOffsetResolution(
            offset_seconds=-float(best[1].offset_seconds),
            method="nearest",
            sample=best[1],
            sample_distance_seconds=best[0],
            measured_offset_seconds=float(best[1].offset_seconds),
        )


def resolve_calibration_timezone(config) -> ZoneInfo:
    """Match the timezone convention used by the ZTK sync service."""
    name = str(
        config.get("VIDEO_TIMEZONE")
        or config.get("APP_TIMEZONE")
        or "Asia/Shanghai"
    ).strip() or "Asia/Shanghai"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown calibration timezone=%s, fallback to Asia/Shanghai", name)
        return ZoneInfo("Asia/Shanghai")
