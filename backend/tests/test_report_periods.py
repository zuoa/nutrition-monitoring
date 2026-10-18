import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.report_periods import generation_period, parse_report_period  # noqa: E402


class ReportPeriodTests(unittest.TestCase):
    def test_iso_weeks_cross_calendar_years(self):
        self.assertEqual(parse_report_period("personal_weekly", "2020-W53"), (date(2020, 12, 28), date(2021, 1, 3)))
        self.assertEqual(parse_report_period("class_weekly", "2026-W01"), (date(2025, 12, 29), date(2026, 1, 4)))

    def test_calendar_months_include_leap_day(self):
        for year, month, days in [(2024, 2, 29), (2026, 2, 28), (2026, 4, 30), (2026, 12, 31)]:
            with self.subTest(year=year, month=month):
                self.assertEqual(parse_report_period("personal_monthly", f"{year}-{month:02}"), (date(year, month, 1), date(year, month, days)))

    def test_invalid_periods_are_rejected(self):
        for report_type, period in [("personal_weekly", "2021-W53"), ("personal_weekly", "2026-W00"),
                                    ("personal_weekly", "2026-07"), ("personal_monthly", "2026-13"),
                                    ("personal_monthly", "2026-W28"), ("personal_monthly", "2026-2"), ("personal_monthly", "")]:
            with self.subTest(report_type=report_type, period=period), self.assertRaises(ValueError):
                parse_report_period(report_type, period)

    def test_generation_defaults_to_previous_complete_period(self):
        self.assertEqual(generation_period("campus_weekly", today=date(2026, 1, 5)), (date(2025, 12, 29), date(2026, 1, 4)))
        self.assertEqual(generation_period("grade_monthly", today=date(2026, 1, 1)), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_generation_rejects_partial_reversed_or_conflicting_periods(self):
        for report_type, start, end in [("personal_weekly", "2026-07-07", "2026-07-13"),
                                        ("personal_weekly", "2026-07-06", "2026-07-05"),
                                        ("personal_weekly", "2026-07-06", None),
                                        ("class_monthly", "2024-02-01", "2024-02-28"),
                                        ("class_monthly", "2026-07-01", "2026-08-31")]:
            with self.subTest(report_type=report_type, start=start, end=end), self.assertRaises(ValueError):
                generation_period(report_type, start, end)
        with self.assertRaises(ValueError):
            generation_period("personal_monthly", "2026-07-01", "2026-07-31", period="2026-07")
