"""Sports evidence must stay student-scoped, time-bounded and snapshot-safe."""

from copy import deepcopy
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask
import pytest

from app import db
from app.models import Report, ReportTypeEnum, SportRecord, Student
from app.services.nutrition_service import NutritionService
from app.services.report_brief import brief_report_view
from app.services.report_health import period_health_context, previous_report_period
from app.services.report_presentation import render_personal_html, serialize_personal_reports
from tests.test_report_brief import assessed_content

START, END = date(2026, 10, 19), date(2026, 10, 23)
TZ = ZoneInfo('Asia/Shanghai')


@pytest.fixture
def health_db(tmp_path):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      APP_TIMEZONE='Asia/Shanghai', LOCAL_RUNTIME_CONFIG_PATH=str(tmp_path / 'runtime.json'))
    db.init_app(app)
    with app.app_context():
        db.create_all()
        student = Student(student_no='000123', name='体育学生', age=16, gender='男')
        db.session.add(student)
        db.session.commit()
        yield student
        db.session.remove()
        db.drop_all()


def sport(student, stamp='2026-10-19T08:00:00', *, kind=1, mode=1, score=0, **metrics):
    record = SportRecord(event_key=f'event-{SportRecord.query.count()}', version='v1.1', school_id='school',
                         product_type=1, sport_type=kind, mode=mode, person_id=student.student_no,
                         start_time=int(datetime.fromisoformat(stamp).replace(tzinfo=TZ).timestamp() * 1000),
                         score=score, raw_result={'score': score, **metrics})
    db.session.add(record)
    db.session.flush()
    return record


def test_local_boundaries_modes_body_metrics_and_unknown_projects(health_db):
    student = health_db
    sport(student, '2026-10-12T00:00:00')
    sport(student, '2026-10-16T23:59:59')
    sport(student, '2026-10-17T08:00:00')  # previous weekend excluded
    sport(student, '2026-10-19T00:00:00', height=172, weight=60)
    sport(student, '2026-10-23T23:59:59', kind=6)
    sport(student, kind=99)
    sport(student, kind=1, mode=2, score=150)
    sport(student, kind=10, height=0, weight=-1)  # not exercise, invalid metrics ignored
    sport(student, kind=9)
    sport(student, '2026-10-24T00:00:00', height=190, weight=90)
    other = Student(student_no='000999', name='其他学生')
    db.session.add(other)
    db.session.flush()
    sport(other)
    result = period_health_context([(1, student.id, START, END)])[1]['sports']
    assert result['practice_count'] == 3 and result['previous_practice_count'] == 2
    assert result['practice_delta'] == 1 and result['project_count'] == 3
    assert result['test_count'] == 1
    assert [row['name'] for row in result['top_projects']] == ['跳绳', '仰卧起坐', '项目 99']
    assert result['measurements'] == {'height': {'value': 172, 'date': '2026-10-19'}, 'weight': {'value': 60, 'date': '2026-10-19'}}


def test_exact_previous_period_uses_latest_matching_snapshot(health_db):
    student = health_db
    old_start, old_end, _ = previous_report_period(START, END)
    for value, kind, end in [(7, ReportTypeEnum.personal_weekly, old_end),
                             (99, ReportTypeEnum.personal_weekly, old_end + timedelta(days=2)),
                             (98, ReportTypeEnum.personal_monthly, old_end),
                             (8, ReportTypeEnum.personal_weekly, old_end)]:
        db.session.add(Report(report_type=kind, target_id=str(student.id), period_start=old_start, period_end=end,
                              content={'meal_statistics': {'meal_count': value}, 'meal_projection': {'avg_nutrients': {'protein': 0, 'calories': None}},
                                       'avg_nutrients': {'calories': 999}}))
    db.session.commit()
    result = period_health_context([(1, student.id, START, END)])[1]['period_comparison']
    assert result['meal_count'] == 8
    assert result['nutrients'] == {'protein': 0, 'calories': None}
    assert previous_report_period(date(2026, 3, 1), date(2026, 3, 31), 'personal_monthly')[:2] == (date(2026, 2, 1), date(2026, 2, 28))
    assert previous_report_period(date(2028, 3, 1), date(2028, 3, 31), 'personal_monthly')[1] == date(2028, 2, 29)


def test_old_body_measurement_can_be_carried_forward_but_future_cannot(health_db):
    sport(health_db, '2026-09-01T08:00:00', kind=10, mode=2, height=170, weight=59)
    sport(health_db, '2026-10-26T08:00:00', kind=10, mode=2, height=175, weight=62)
    result = period_health_context([(1, health_db.id, START, END)])[1]['sports']
    assert result['measurements']['height'] == {'value': 170, 'date': '2026-09-01'}
    assert result['has_records'] is False


def test_generation_and_read_enrichment_do_not_rewrite_snapshots(health_db):
    student = health_db
    sport(student)
    generated = NutritionService().generate_personal_report(student.id, START, END)
    assert generated['sports']['practice_count'] == 1
    stored = deepcopy(generated)
    report = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(student.id), period_start=START, period_end=END, content=generated)
    legacy = Report(report_type=ReportTypeEnum.personal_weekly, target_id=str(student.id), period_start=START, period_end=END,
                    content={'student_name': student.name, 'avg_nutrients': {}})
    db.session.add_all([report, legacy])
    db.session.commit()
    sport(student, kind=6)
    enriched = serialize_personal_reports([report, legacy])
    assert enriched[0]['content']['sports']['practice_count'] == 1
    assert report.content == stored
    assert enriched[1]['content']['sports']['practice_count'] == 2
    assert 'sports' not in legacy.content
    for format in ('brief', 'detailed'):
        assert '体育锻炼分析' in render_personal_html(enriched[0]['content'], report_format=format)


def test_missing_previous_data_does_not_create_positive_highlights_or_join_chart_gaps():
    content = assessed_content()
    content['sports'] = {'practice_count': 4, 'practice_delta': 4, 'project_count': 1,
                         'previous_has_records': False, 'top_projects': [{'name': '跳绳'}]}
    content['period_comparison'] = {'has_report': False, 'meal_count': None, 'nutrients': {'calories': 0, 'protein': None, 'fat': 20}}
    original = deepcopy(content)
    view = brief_report_view(content)
    assert view['meal_delta'] is None
    assert not any('增加' in row['text'] for row in view['highlights'])
    previous_paths = [path for path in view['chart']['paths'] if path['series'] == 'previous']
    assert len(previous_paths) == 2
    assert any(point['series'] == 'previous' and point['ratio'] == 0 for point in view['chart']['points'])
    assert content == original
