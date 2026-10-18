"""Calendar periods shared by report queries and generation tasks."""

import calendar
import re
from datetime import date, timedelta


def parse_report_period(report_type: str, period: str) -> tuple[date, date]:
    """Parse an ISO week (YYYY-Www) or calendar month (YYYY-MM)."""
    try:
        if report_type.endswith("_weekly") and re.fullmatch(r"\d{4}-W\d{2}", period):
            year, week = period.split("-W")
            start = date.fromisocalendar(int(year), int(week), 1)
            return start, start + timedelta(days=6)
        if report_type.endswith("_monthly") and re.fullmatch(r"\d{4}-\d{2}", period):
            year, month = map(int, period.split("-"))
            return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    except (TypeError, ValueError, OverflowError):
        pass
    raise ValueError("请选择有效的报告周期：周报使用 YYYY-Www，月报使用 YYYY-MM")


def generation_period(report_type: str, start: str | None = None, end: str | None = None,
                      period: str | None = None, today: date | None = None) -> tuple[date, date]:
    if period is not None:
        if start is not None or end is not None:
            raise ValueError("报告周期与起止日期不能同时指定")
        return parse_report_period(report_type, period)

    if start is not None or end is not None:
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if report_type.endswith("_weekly"):
                valid = first.weekday() == 0 and last == first + timedelta(days=6)
            else:
                valid = first.day == 1 and last == first.replace(day=calendar.monthrange(first.year, first.month)[1])
            if valid:
                return first, last
        except (TypeError, ValueError, OverflowError):
            pass
        raise ValueError("请选择完整的报告周期：周报为周一至周日，月报为自然月")

    today = today or date.today()
    if report_type.endswith("_weekly"):
        first = today - timedelta(days=today.weekday() + 7)
        return first, first + timedelta(days=6)
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last
