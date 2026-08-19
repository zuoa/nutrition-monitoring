"""Staged bidirectional time windows for consumption-to-image matching."""

from datetime import datetime, timedelta

DEFAULT_MATCH_WINDOW_STAGES = (1, 3, 5)


def normalize_match_window_stages(stages=None) -> tuple[int, ...]:
    """Return increasing positive whole-second stages, or the default 1/3/5."""
    if stages is None:
        return DEFAULT_MATCH_WINDOW_STAGES
    if isinstance(stages, str):
        raw_items = stages.replace("，", ",").split(",")
    elif isinstance(stages, (list, tuple)):
        raw_items = list(stages)
    else:
        try:
            seconds = int(stages)
        except (TypeError, ValueError):
            return DEFAULT_MATCH_WINDOW_STAGES
        return (seconds,) if seconds > 0 else DEFAULT_MATCH_WINDOW_STAGES

    values = []
    seen = set()
    for item in raw_items:
        try:
            seconds = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if seconds <= 0 or seconds in seen:
            continue
        seen.add(seconds)
        values.append(seconds)
    return tuple(sorted(values)) if values else DEFAULT_MATCH_WINDOW_STAGES


def max_match_window_seconds(stages=None) -> int:
    return max(normalize_match_window_stages(stages))


def matching_windows(tx_time: datetime, stages=None) -> list[tuple[datetime, datetime, bool]]:
    """Expanding inclusive windows: ±1s, then ±3s, then ±5s by default.

    Callers try each window in order and keep the first non-empty candidate set,
    so a ±1s hit is preferred over a better-priced ±3s/±5s hit.
    """
    windows = []
    for seconds in normalize_match_window_stages(stages):
        delta = timedelta(seconds=seconds)
        windows.append((tx_time - delta, tx_time + delta, True))
    return windows
