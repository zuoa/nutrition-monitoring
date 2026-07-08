import logging
import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_, or_

from app import db
from app.models import CategoryEnum, Dish, NutritionLog, Report, ReportPushLog, ReportTypeEnum, Student
from app.modules.students.models.organization import School, Campus, Stage, Grade, Class
from app.modules.students.models.student import StudentSourceEnum
from app.nutrition_metadata import NUTRITION_FIELD_KEYS
from app.services.nutrition_service import DAILY_RECOMMENDED, NutritionService

logger = logging.getLogger(__name__)

MEAL_FACTOR_BY_COUNT = {
    0: 0.0,
    1: 0.58,
    2: 0.85,
    3: 1.0,
}

PROFILE_MULTIPLIERS = {
    "balanced": {
        "calories": 0.96,
        "protein": 0.97,
        "fat": 0.98,
        "carbohydrate": 0.95,
        "sodium": 0.92,
        "fiber": 1.02,
    },
    "low_protein": {
        "calories": 0.8,
        "protein": 0.56,
        "fat": 0.82,
        "carbohydrate": 0.9,
        "sodium": 0.88,
        "fiber": 0.8,
    },
    "high_sodium": {
        "calories": 1.03,
        "protein": 0.94,
        "fat": 1.08,
        "carbohydrate": 1.01,
        "sodium": 2.0,
        "fiber": 0.72,
    },
    "low_fiber": {
        "calories": 1.0,
        "protein": 0.9,
        "fat": 1.05,
        "carbohydrate": 1.02,
        "sodium": 1.12,
        "fiber": 0.46,
    },
    "low_energy": {
        "calories": 0.6,
        "protein": 0.72,
        "fat": 0.74,
        "carbohydrate": 0.64,
        "sodium": 0.82,
        "fiber": 0.68,
    },
    "high_energy": {
        "calories": 1.25,
        "protein": 1.03,
        "fat": 1.28,
        "carbohydrate": 1.18,
        "sodium": 1.18,
        "fiber": 0.84,
    },
}

PROFILE_DISH_POOLS = {
    "balanced": ["杂粮饭", "白米饭", "清蒸鸡腿", "番茄炒蛋", "炒西兰花", "清炒菠菜", "豆腐汤", "冬瓜海带汤", "凉拌黄瓜"],
    "low_protein": ["白米饭", "花卷", "小米粥", "蒸南瓜", "清炒菠菜", "炒西兰花", "凉拌黄瓜", "冬瓜海带汤"],
    "high_sodium": ["白米饭", "红烧肉", "鱼香肉丝", "宫保鸡丁", "扬州炒饭", "紫菜蛋花汤"],
    "low_fiber": ["白米饭", "花卷", "红烧肉", "番茄炒蛋", "宫保鸡丁", "扬州炒饭", "紫菜蛋花汤"],
    "low_energy": ["小米粥", "蒸南瓜", "清炒菠菜", "凉拌黄瓜", "冬瓜海带汤", "白米饭"],
    "high_energy": ["扬州炒饭", "白米饭", "土豆牛肉", "红烧肉", "宫保鸡丁", "花卷", "紫菜蛋花汤"],
}

PROFILE_WEEKDAY_SKIP_RATE = {
    "balanced": 0.03,
    "low_protein": 0.06,
    "high_sodium": 0.04,
    "low_fiber": 0.05,
    "low_energy": 0.18,
    "high_energy": 0.03,
}

PROFILE_WEEKEND_MEAL_RATE = {
    "balanced": 0.42,
    "low_protein": 0.3,
    "high_sodium": 0.36,
    "low_fiber": 0.34,
    "low_energy": 0.18,
    "high_energy": 0.4,
}

DEMO_DISH_CATALOG = [
    {"name": "白米饭", "price": 2.0, "category": CategoryEnum.staple, "calories": 232, "protein": 5.2, "fat": 0.6, "carbohydrate": 51.2, "sodium": 4, "fiber": 0.6},
    {"name": "杂粮饭", "price": 2.5, "category": CategoryEnum.staple, "calories": 210, "protein": 6.8, "fat": 1.2, "carbohydrate": 43.0, "sodium": 6, "fiber": 2.8},
    {"name": "花卷", "price": 2.0, "category": CategoryEnum.staple, "calories": 238, "protein": 7.2, "fat": 1.0, "carbohydrate": 50.5, "sodium": 280, "fiber": 1.5},
    {"name": "小米粥", "price": 2.0, "category": CategoryEnum.staple, "calories": 120, "protein": 3.4, "fat": 1.8, "carbohydrate": 23.0, "sodium": 12, "fiber": 1.1},
    {"name": "蒸南瓜", "price": 3.0, "category": CategoryEnum.vegetable, "calories": 88, "protein": 2.0, "fat": 0.4, "carbohydrate": 20.4, "sodium": 8, "fiber": 2.2},
    {"name": "清蒸鸡腿", "price": 9.0, "category": CategoryEnum.meat, "calories": 260, "protein": 24.5, "fat": 13.8, "carbohydrate": 2.0, "sodium": 220, "fiber": 0.0},
    {"name": "宫保鸡丁", "price": 9.0, "category": CategoryEnum.meat, "calories": 172, "protein": 15.2, "fat": 10.8, "carbohydrate": 4.9, "sodium": 680, "fiber": 0.5},
    {"name": "红烧肉", "price": 8.0, "category": CategoryEnum.meat, "calories": 395, "protein": 13.7, "fat": 37.0, "carbohydrate": 2.6, "sodium": 685, "fiber": 0.0},
    {"name": "鱼香肉丝", "price": 8.0, "category": CategoryEnum.meat, "calories": 148, "protein": 11.2, "fat": 9.6, "carbohydrate": 4.1, "sodium": 820, "fiber": 0.3},
    {"name": "土豆牛肉", "price": 10.0, "category": CategoryEnum.meat, "calories": 245, "protein": 18.6, "fat": 12.4, "carbohydrate": 12.8, "sodium": 420, "fiber": 1.8},
    {"name": "番茄炒蛋", "price": 6.0, "category": CategoryEnum.meat, "calories": 135, "protein": 8.8, "fat": 8.4, "carbohydrate": 7.2, "sodium": 320, "fiber": 0.8},
    {"name": "炒西兰花", "price": 5.0, "category": CategoryEnum.vegetable, "calories": 42, "protein": 3.6, "fat": 1.1, "carbohydrate": 5.2, "sodium": 46, "fiber": 3.1},
    {"name": "清炒菠菜", "price": 4.0, "category": CategoryEnum.vegetable, "calories": 36, "protein": 2.9, "fat": 0.8, "carbohydrate": 4.0, "sodium": 52, "fiber": 2.5},
    {"name": "凉拌黄瓜", "price": 4.0, "category": CategoryEnum.vegetable, "calories": 28, "protein": 1.4, "fat": 0.6, "carbohydrate": 4.2, "sodium": 92, "fiber": 1.7},
    {"name": "紫菜蛋花汤", "price": 3.0, "category": CategoryEnum.soup, "calories": 22, "protein": 1.8, "fat": 0.9, "carbohydrate": 1.8, "sodium": 240, "fiber": 0.2},
    {"name": "豆腐汤", "price": 3.0, "category": CategoryEnum.soup, "calories": 36, "protein": 4.1, "fat": 1.8, "carbohydrate": 1.8, "sodium": 180, "fiber": 0.3},
    {"name": "冬瓜海带汤", "price": 3.0, "category": CategoryEnum.soup, "calories": 24, "protein": 1.3, "fat": 0.6, "carbohydrate": 3.2, "sodium": 105, "fiber": 1.2},
    {"name": "扬州炒饭", "price": 7.0, "category": CategoryEnum.staple, "calories": 420, "protein": 12.0, "fat": 14.6, "carbohydrate": 58.4, "sodium": 760, "fiber": 1.1},
]


DEMO_NUTRITION_DEFAULTS = {
    CategoryEnum.staple.value: {
        "cholesterol": 0, "added_sugar": 0.5, "calcium": 10, "iron": 1.0,
        "zinc": 1.0, "vitamin_a": 5, "vitamin_c": 0, "vitamin_d": 0,
    },
    CategoryEnum.meat.value: {
        "cholesterol": 85, "added_sugar": 1.0, "calcium": 20, "iron": 1.8,
        "zinc": 2.4, "vitamin_a": 35, "vitamin_c": 2, "vitamin_d": 0.8,
    },
    CategoryEnum.vegetable.value: {
        "cholesterol": 0, "added_sugar": 0.5, "calcium": 55, "iron": 1.2,
        "zinc": 0.5, "vitamin_a": 180, "vitamin_c": 35, "vitamin_d": 0,
    },
    CategoryEnum.soup.value: {
        "cholesterol": 20, "added_sugar": 0.2, "calcium": 35, "iron": 0.7,
        "zinc": 0.4, "vitamin_a": 45, "vitamin_c": 6, "vitamin_d": 0.3,
    },
}


def _demo_nutrition_value(item: dict, field: str) -> float:
    if field in item:
        return item[field]

    category = item["category"].value if isinstance(item.get("category"), CategoryEnum) else str(item.get("category") or "")
    defaults = DEMO_NUTRITION_DEFAULTS.get(category, {})
    value = defaults.get(field, 0)
    name = str(item.get("name") or "")

    if field == "cholesterol" and ("蛋" in name or "鸡腿" in name):
        return max(value, 170 if "蛋" in name else 95)
    if field == "added_sugar" and any(token in name for token in ("红烧", "鱼香", "宫保")):
        return max(value, 3.0)
    if field == "calcium" and any(token in name for token in ("豆腐", "紫菜", "海带")):
        return max(value, 80)
    if field == "iron" and any(token in name for token in ("牛肉", "菠菜", "红烧肉")):
        return max(value, 2.2)
    if field == "zinc" and any(token in name for token in ("牛肉", "鸡", "肉")):
        return max(value, 2.2)
    if field == "vitamin_a" and any(token in name for token in ("南瓜", "菠菜", "西兰花")):
        return max(value, 250)
    if field == "vitamin_c" and any(token in name for token in ("西兰花", "菠菜", "黄瓜", "番茄")):
        return max(value, 30)
    if field == "vitamin_d" and any(token in name for token in ("蛋", "鱼")):
        return max(value, 1.5)
    return value


@dataclass(frozen=True)
class DemoStudentTemplate:
    name: str
    class_code: str
    class_name: str
    grade_code: str
    grade_name: str
    profile: str


DEMO_STUDENT_TEMPLATES = [
    DemoStudentTemplate("林晓彤", "G7-01", "七年级（1）班", "G7", "七年级", "balanced"),
    DemoStudentTemplate("周子谦", "G7-01", "七年级（1）班", "G7", "七年级", "low_protein"),
    DemoStudentTemplate("许沐阳", "G7-01", "七年级（1）班", "G7", "七年级", "high_sodium"),
    DemoStudentTemplate("唐雨桐", "G7-01", "七年级（1）班", "G7", "七年级", "low_fiber"),
    DemoStudentTemplate("陈嘉禾", "G7-01", "七年级（1）班", "G7", "七年级", "balanced"),
    DemoStudentTemplate("苏芷晴", "G7-02", "七年级（2）班", "G7", "七年级", "low_energy"),
    DemoStudentTemplate("赵承泽", "G7-02", "七年级（2）班", "G7", "七年级", "high_energy"),
    DemoStudentTemplate("沈知远", "G7-02", "七年级（2）班", "G7", "七年级", "low_protein"),
    DemoStudentTemplate("顾若宁", "G7-02", "七年级（2）班", "G7", "七年级", "balanced"),
    DemoStudentTemplate("季明朗", "G7-02", "七年级（2）班", "G7", "七年级", "high_sodium"),
]


class DemoDataService:
    def __init__(self, *, today: date | None = None, seed: int = 20260422):
        self.today = today or date.today()
        self._rng = random.Random(seed)

    def seed_historical_data(self, *, weeks: int = 8, report_weeks: int = 4, student_prefix: str = "DEMO") -> dict:
        if weeks < 2:
            raise ValueError("weeks 必须大于等于 2")
        if report_weeks < 1:
            raise ValueError("report_weeks 必须大于等于 1")
        if report_weeks > weeks:
            raise ValueError("report_weeks 不能大于 weeks")
        if not student_prefix.strip():
            raise ValueError("student_prefix 不能为空")

        latest_report_start = self._latest_report_start()
        latest_report_end = latest_report_start + timedelta(days=6)
        history_start = latest_report_start - timedelta(days=(weeks - 1) * 7)
        history_end = latest_report_end

        self._reset_existing_demo_data(student_prefix)
        dishes_by_name = self._ensure_demo_dishes()
        students = self._create_demo_students(student_prefix)
        log_count = self._seed_nutrition_logs(students, dishes_by_name, history_start, history_end)
        personal_report_count, class_report_count = self._seed_reports(students, report_weeks, latest_report_start)

        db.session.commit()
        logger.info(
            "Seeded demo historical data prefix=%s students=%s logs=%s personal_reports=%s class_reports=%s",
            student_prefix,
            len(students),
            log_count,
            personal_report_count,
            class_report_count,
        )

        return {
            "student_count": len(students),
            "class_count": len({student.class_id for student in students}),
            "nutrition_log_count": log_count,
            "personal_report_count": personal_report_count,
            "class_report_count": class_report_count,
            "history_start": history_start.isoformat(),
            "history_end": history_end.isoformat(),
            "latest_report_start": latest_report_start.isoformat(),
            "latest_report_end": latest_report_end.isoformat(),
            "student_prefix": student_prefix,
        }

    def _ensure_demo_dishes(self) -> dict[str, Dish]:
        existing = {
            dish.name: dish
            for dish in Dish.query.filter(Dish.name.in_([item["name"] for item in DEMO_DISH_CATALOG])).all()
        }

        for item in DEMO_DISH_CATALOG:
            dish = existing.get(item["name"])
            if dish is None:
                dish = Dish(name=item["name"], price=item["price"], category=item["category"])
                db.session.add(dish)
                existing[item["name"]] = dish

            dish.price = item["price"]
            dish.category = item["category"]
            for field in NUTRITION_FIELD_KEYS:
                setattr(dish, field, _demo_nutrition_value(item, field))
            dish.is_active = True

        db.session.flush()
        return existing

    def _reset_existing_demo_data(self, student_prefix: str) -> None:
        demo_students = Student.query.filter(Student.student_no.like(f"{student_prefix}%")).all()
        if not demo_students:
            return

        student_ids = [student.id for student in demo_students]
        student_target_ids = [str(student_id) for student_id in student_ids]
        class_ids = sorted({str(s.class_id) for s in demo_students if s.class_id})

        report_ids = [
            report_id
            for (report_id,) in db.session.query(Report.id).filter(
                or_(
                    and_(
                        Report.report_type.in_([ReportTypeEnum.personal_weekly, ReportTypeEnum.personal_monthly]),
                        Report.target_id.in_(student_target_ids),
                    ),
                    and_(
                        Report.report_type == ReportTypeEnum.class_weekly,
                        Report.target_id.in_(class_ids),
                    ),
                )
            ).all()
        ]

        if report_ids:
            db.session.query(ReportPushLog).filter(ReportPushLog.report_id.in_(report_ids)).delete(synchronize_session=False)
            db.session.query(Report).filter(Report.id.in_(report_ids)).delete(synchronize_session=False)

        db.session.query(NutritionLog).filter(NutritionLog.student_id.in_(student_ids)).delete(synchronize_session=False)
        db.session.query(Student).filter(Student.id.in_(student_ids)).delete(synchronize_session=False)
        db.session.flush()

    def _ensure_demo_class_map(self) -> dict[str, Class]:
        """按模板构建（或复用）演示组织节点，返回 class_code → Class。"""
        school = School.query.first()
        if not school:
            school = School(name="默认学校", is_active=True)
            db.session.add(school)
            db.session.flush()
        campus = Campus.query.filter_by(school_id=school.id).first()
        if not campus:
            campus = Campus(school_id=school.id, name="默认校区", is_active=True)
            db.session.add(campus)
            db.session.flush()
        stage = Stage.query.filter_by(campus_id=campus.id).first()
        if not stage:
            stage = Stage(campus_id=campus.id, name="默认学段", stage_type="other", is_active=True)
            db.session.add(stage)
            db.session.flush()

        grade_by_code: dict[str, Grade] = {}
        class_by_code: dict[str, Class] = {}
        for template in DEMO_STUDENT_TEMPLATES:
            grade = grade_by_code.get(template.grade_code)
            if not grade:
                grade = Grade.query.filter_by(stage_id=stage.id, name=template.grade_name).first()
                if not grade:
                    grade = Grade(stage_id=stage.id, name=template.grade_name, is_active=True)
                    db.session.add(grade)
                    db.session.flush()
                grade_by_code[template.grade_code] = grade
            cls = class_by_code.get(template.class_code)
            if not cls:
                cls = Class.query.filter_by(grade_id=grade.id, name=template.class_name).first()
                if not cls:
                    cls = Class(grade_id=grade.id, name=template.class_name, is_active=True)
                    db.session.add(cls)
                    db.session.flush()
                class_by_code[template.class_code] = cls
        return class_by_code

    def _create_demo_students(self, student_prefix: str) -> list[Student]:
        class_by_code = self._ensure_demo_class_map()
        students = []
        for index, template in enumerate(DEMO_STUDENT_TEMPLATES, start=1):
            cls = class_by_code.get(template.class_code)
            student = Student(
                student_no=f"{student_prefix}{index:03d}",
                name=template.name,
                class_id=cls.id if cls else None,
                class_name=template.class_name,
                grade_name=template.grade_name,
                legacy_class_code=template.class_code,
                legacy_grade_code=template.grade_code,
                card_no=f"{student_prefix}-CARD-{index:03d}",
                source=StudentSourceEnum.local,
                is_active=True,
            )
            db.session.add(student)
            students.append(student)

        db.session.flush()
        return students

    def _seed_nutrition_logs(
        self,
        students: list[Student],
        dishes_by_name: dict[str, Dish],
        history_start: date,
        history_end: date,
    ) -> int:
        log_count = 0
        for student in students:
            profile = self._student_profile(student.name)
            current = history_start
            while current <= history_end:
                meal_count = self._pick_meal_count(profile, current)
                nutrient_totals, dish_ids = self._build_log_payload(profile, meal_count, current, dishes_by_name)
                db.session.add(
                    NutritionLog(
                        student_id=student.id,
                        log_date=current,
                        nutrient_totals=nutrient_totals,
                        meal_count=meal_count,
                        dish_ids=dish_ids,
                    )
                )
                log_count += 1
                current += timedelta(days=1)

        db.session.flush()
        return log_count

    def _seed_reports(self, students: list[Student], report_weeks: int, latest_report_start: date) -> tuple[int, int]:
        svc = NutritionService()
        personal_report_count = 0
        class_report_count = 0
        class_ids = sorted({s.class_id for s in students if s.class_id})
        ordered_periods = [
            latest_report_start - timedelta(days=7 * offset)
            for offset in range(report_weeks - 1, -1, -1)
        ]

        for period_start in ordered_periods:
            period_end = period_start + timedelta(days=6)

            for student in students:
                content = svc.generate_personal_report(student.id, period_start, period_end)
                db.session.add(
                    Report(
                        report_type=ReportTypeEnum.personal_weekly,
                        target_id=str(student.id),
                        period_start=period_start,
                        period_end=period_end,
                        content=content,
                        summary=self._summarize_personal(content),
                        push_status="pending",
                    )
                )
                personal_report_count += 1

            for class_id in class_ids:
                content = svc.generate_class_report(class_id, period_start, period_end)
                db.session.add(
                    Report(
                        report_type=ReportTypeEnum.class_weekly,
                        target_id=str(class_id),
                        period_start=period_start,
                        period_end=period_end,
                        content=content,
                        summary=f"班级周报 {period_start.isoformat()} - {period_end.isoformat()}",
                        push_status="pending",
                    )
                )
                class_report_count += 1

        db.session.flush()
        return personal_report_count, class_report_count

    def _pick_meal_count(self, profile: str, current: date) -> int:
        if current.weekday() >= 5:
            if self._rng.random() > PROFILE_WEEKEND_MEAL_RATE[profile]:
                return 0
            return 1 if self._rng.random() < 0.65 else 2

        if self._rng.random() < PROFILE_WEEKDAY_SKIP_RATE[profile]:
            return 0

        if profile == "low_energy":
            return 2 if self._rng.random() < 0.8 else 1
        if profile in {"balanced", "high_energy"}:
            return 3 if self._rng.random() < 0.45 else 2
        return 2 if self._rng.random() < 0.75 else 3

    def _build_log_payload(
        self,
        profile: str,
        meal_count: int,
        current: date,
        dishes_by_name: dict[str, Dish],
    ) -> tuple[dict, list[int]]:
        if meal_count == 0:
            return ({nutrient: 0.0 for nutrient in DAILY_RECOMMENDED}, [])

        nutrient_totals = {}
        meal_factor = MEAL_FACTOR_BY_COUNT[meal_count]
        if current.weekday() >= 5:
            meal_factor *= 0.92

        for nutrient, recommended in DAILY_RECOMMENDED.items():
            jitter = self._rng.uniform(0.94, 1.08)
            ratio = PROFILE_MULTIPLIERS[profile].get(nutrient, 1.0)
            nutrient_totals[nutrient] = round(recommended * ratio * meal_factor * jitter, 1)

        pool = PROFILE_DISH_POOLS[profile]
        dish_count = min(len(pool), max(3, meal_count + self._rng.randint(1, 3)))
        dish_ids = sorted(
            {
                dishes_by_name[name].id
                for name in self._rng.sample(pool, k=dish_count)
                if name in dishes_by_name
            }
        )
        return nutrient_totals, dish_ids

    def _latest_report_start(self) -> date:
        return self.today - timedelta(days=self.today.weekday() + 7)

    @staticmethod
    def _summarize_personal(content: dict) -> str:
        alerts = content.get("alerts") or []
        alert_text = f"，{alerts[0]['message']}" if alerts else ""
        return (
            f"{content.get('student_name', '')}本期就餐{content.get('meal_days', 0)}/{content.get('total_days', 7)}天，"
            f"综合评分{content.get('overall_score', 0)}分{alert_text}"
        )

    @staticmethod
    def _student_profile(student_name: str) -> str:
        for template in DEMO_STUDENT_TEMPLATES:
            if template.name == student_name:
                return template.profile
        return "balanced"
