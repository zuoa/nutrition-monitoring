"""Published BMI cutoffs, birthdays and missing demographics must stay explicit."""

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.report_body import activity_reference, assess_body, health_advice, measured_profile
from app.services.report_presentation import render_personal_html
from tests.test_report_brief import assessed_content


def measurements(bmi=20, day='2026-10-19'):
    return {'height': {'value': 100, 'date': day}, 'weight': {'value': bmi, 'date': day}}


@pytest.mark.parametrize('age,gender,bmi,category', [
    (6, '男', 13.4, 'thinness'), (6, '男', 13.5, 'reference'),
    (6, '男', 16.3, 'reference'), (6, '男', 16.4, 'overweight'), (6, '男', 17.7, 'obesity'),
    (6, '女', 13.1, 'thinness'), (6, '女', 13.2, 'reference'),
    (6, '女', 16.2, 'overweight'), (6, '女', 17.5, 'obesity'),
    (16, '男', 17.3, 'thinness'), (16, '男', 17.4, 'reference'),
    (16, '男', 23.3, 'overweight'), (16, '男', 27.1, 'obesity'),
    (16, '女', 17.0, 'thinness'), (16, '女', 23.6, 'overweight'),
    (18, '男', 17.9, 'thinness'), (18, '女', 17.3, 'thinness'),
    (18, '女', 24, 'overweight'), (18, '男', 28, 'obesity'),
    (6, '男', 16.35, 'overweight'),  # Compare BMI rounded to one decimal, per standard.
])
def test_published_cutoffs_and_inclusive_boundaries(age, gender, bmi, category):
    result = assess_body(measurements(bmi), SimpleNamespace(age=age, gender=gender))
    assert result['category'] == category


def test_half_year_boundary_uses_measurement_day_and_birth_date_before_entered_age():
    student = SimpleNamespace(birth_date=date(2016, 4, 19), age=16, gender='女')
    before = assess_body(measurements(19.8, '2026-10-18'), student)
    after = assess_body(measurements(19.8, '2026-10-19'), student)
    assert before['profile']['screening_age'] == 10
    assert before['category'] == 'overweight'
    assert after['profile']['screening_age'] == 10.5
    assert after['category'] == 'reference'
    assert after['profile']['age_source'] == 'birth_date'


@pytest.mark.parametrize('birthday,as_of,expected', [
    (date(2020, 8, 31), date(2026, 2, 28), 5.5),
    (date(2020, 2, 29), date(2026, 2, 28), 6),
    (date(2020, 10, 20), date(2026, 10, 19), 5.5),
    (date(2020, 10, 19), date(2026, 10, 19), 6),
])
def test_birthday_and_month_end_boundaries(birthday, as_of, expected):
    profile = measured_profile(SimpleNamespace(birth_date=birthday), as_of)
    assert profile['screening_age'] == expected


@pytest.mark.parametrize('profile', [
    {}, {'age': 16}, {'gender': '男'}, {'age': 5, 'gender': '女'}, {'age': 19, 'gender': '男'},
    {'birth_date': date(2030, 1, 1), 'gender': '男'}, {'grade_name': '高一', 'gender': '男'},
])
def test_missing_or_outside_demographics_do_not_classify(profile):
    result = assess_body(measurements(), SimpleNamespace(**profile))
    assert result['bmi'] == 20 and result['category'] == 'unknown'
    assert result['reason']


@pytest.mark.parametrize('value', [0, -1, None, True, '170', float('nan'), float('inf'), 1e-300, 1e300])
def test_invalid_measurement_values_are_not_screened(value):
    pair = measurements()
    pair['height']['value'] = value
    assert assess_body(pair)['bmi'] is None


def test_different_measurement_dates_are_not_combined():
    pair = measurements()
    pair['height']['date'] = '2026-10-18'
    assert assess_body(pair)['bmi'] is None


def test_activity_guidance_age_range_and_no_grade_inference():
    for age in (5, 17):
        assert activity_reference(SimpleNamespace(age=age), date(2026, 10, 19))
    for age in (4, 18, None):
        assert activity_reference(SimpleNamespace(age=age), date(2026, 10, 19)) is None
    assert activity_reference(SimpleNamespace(grade_name='高一'), date(2026, 10, 19)) is None


@pytest.mark.parametrize('gender', [None, '', '未知'])
def test_missing_gender_keeps_bmi_and_activity_guidance_without_classification(gender):
    student = SimpleNamespace(age=16, gender=gender)
    result = assess_body(measurements(27.1), student)
    assert result['bmi'] == 27.1
    assert result['category'] == 'unknown' and result['label'] == 'BMI 记录'
    assert result['reason'] == '缺少性别，BMI 仅作数值记录，暂不作体型分类。'
    assert 'thresholds' not in result
    assert activity_reference(student, date(2026, 10, 19))
    advice = health_advice({'body_assessment': result})[0]['text']
    assert '定期记录身高体重' in advice and '补充' not in advice


def test_missing_gender_reports_show_records_and_actionable_advice_including_old_snapshots():
    content = assessed_content()
    content['gender'] = None
    content['body_assessment'] = assess_body(measurements(27.1), SimpleNamespace(age=16))
    # Already saved snapshots retain their old label; presentation still uses record copy.
    content['body_assessment']['label'] = '待评价'
    for report_format in ('brief', 'detailed'):
        html = render_personal_html(content, report_format=report_format)
        assert 'BMI 记录' in html and '27.1' in html and '缺少性别' in html
        assert '定期记录身高体重' in html
        assert '性别：' not in html
        assert '肥胖筛查提示' not in html and '超重筛查提示' not in html
        assert 'WS/T 456' not in html and 'WS/T 586' not in html
        assert '补充同次身高体重' not in html


def test_advice_and_both_templates_share_body_and_activity_results():
    content = assessed_content()
    content['body_assessment'] = assess_body(measurements(27.1), SimpleNamespace(age=16, gender='男'))
    content['sports'] = {
        'version': 2, 'practice_count': 12, 'previous_practice_count': 6, 'practice_delta': 6,
        'project_count': 1, 'previous_project_count': 1, 'top_projects': [], 'test_count': 0,
        'previous_has_records': True, 'has_records': True,
        'active_days': 3, 'duration_record_count': 8, 'recorded_minutes': 26,
        'active_days_delta': 1, 'minutes_delta': None,
        'activity_reference': activity_reference(SimpleNamespace(age=16), date(2026, 10, 19)),
    }
    assert '含糖饮料' in health_advice(content)[0]['text']
    for report_format in ('brief', 'detailed'):
        html = render_personal_html(content, report_format=report_format)
        for expected in ('体型与成长', '肥胖筛查提示', '27.1', '8 / 12', '26', '60分钟',
                         '暂无法判断全天运动量是否充足', '含糖饮料', '时长覆盖不完整'):
            assert expected in html
    content['sports']['active_days'] = 1
    assert health_advice(content)[1]['title'] == '分散安排运动'
    content['body_assessment'] = assess_body(measurements(16), SimpleNamespace(age=16, gender='男'))
    assert '均衡搭配' in health_advice(content)[0]['text']
