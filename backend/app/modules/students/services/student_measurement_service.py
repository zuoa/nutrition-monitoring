"""Populate student measurements from the latest valid BMI sport results."""

import math

from app import db
from app.models.sport import SportRecord
from app.modules.students.models.student import Student


BMI_SPORT_TYPE = 10
MEASUREMENT_FIELDS = ("height", "weight")


def _positive_number(value):
    # Older stored JSON may predate callback validation. Never copy strings,
    # booleans, zero, or non-finite values into the student profile.
    try:
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            return float(value)
    except (OverflowError, ValueError):
        pass
    return None


def sync_student_measurements(student_nos=None):
    """Sync all students, or only the numbers in a BMI callback, without committing.

    Re-read persisted history so retries, corrected results and out-of-order
    callbacks use the same selection rules as the manual backfill. Each field
    independently uses the newest positive measurement; absent values preserve
    the existing profile. Equal event times are resolved by record id.
    """
    summary = {"matched": 0, "updated": 0, "unchanged": 0}
    students_query = Student.query.filter(
        Student.student_no != "",
        db.session.query(SportRecord.id).filter(
            SportRecord.person_id == Student.student_no,
            SportRecord.sport_type == BMI_SPORT_TYPE,
        ).exists(),
    )
    records_query = db.session.query(SportRecord.person_id, SportRecord.raw_result).join(
        Student, Student.student_no == SportRecord.person_id,
    ).filter(SportRecord.sport_type == BMI_SPORT_TYPE, SportRecord.person_id != "")
    if student_nos is not None:
        student_nos = sorted(set(student_nos) - {""})
        if not student_nos:
            return summary
        students_query = students_query.filter(Student.student_no.in_(student_nos))
        records_query = records_query.filter(SportRecord.person_id.in_(student_nos))

    # Serialize overlapping callbacks and backfills on the student rows. All
    # paths acquire locks in the same order and read history after locking.
    students = {
        student.student_no: student
        for student in students_query.order_by(Student.id).populate_existing().with_for_update().all()
    }
    if not students:
        return summary

    latest = {}
    records = records_query.order_by(
        SportRecord.person_id, SportRecord.start_time.desc(), SportRecord.id.desc(),
    ).yield_per(500)
    for person_id, raw_result in records:
        if person_id not in students or not isinstance(raw_result, dict):
            continue
        measurements = latest.setdefault(person_id, {})
        for field in MEASUREMENT_FIELDS:
            if field not in measurements:
                value = _positive_number(raw_result.get(field))
                if value is not None:
                    measurements[field] = value

    for person_id, measurements in latest.items():
        if not measurements:
            continue
        student = students[person_id]
        changed = False
        for field, value in measurements.items():
            if getattr(student, field) != value:
                setattr(student, field, value)
                changed = True
        summary["matched"] += 1
        summary["updated" if changed else "unchanged"] += 1
    return summary
