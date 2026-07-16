import logging
from datetime import date, timedelta
from app import db
from app.models import NutritionLog, MatchResult, DishRecognition, Dish, MatchStatusEnum, Student
from app.nutrition_metadata import (
    DAILY_RECOMMENDED,
    NUTRITION_FIELD_LABELS,
    UPPER_LIMIT_NUTRITION_KEYS,
)

logger = logging.getLogger(__name__)

ALERT_DEFICIENCY_RATIO = 0.6
ALERT_EXCESS_RATIO = 1.5
ALERT_DAYS_THRESHOLD = 5
ALERT_SKIP_MEAL_DAYS = 3
ALERT_DIVERSITY_DAYS = 7
ALERT_DIVERSITY_MIN_SCORE = 0.4


def _calculate_average_nutrients(meal_days: list[NutritionLog]) -> tuple[dict, dict]:
    totals = {k: 0.0 for k in DAILY_RECOMMENDED}
    sample_counts = {k: 0 for k in DAILY_RECOMMENDED}

    for log in meal_days:
        nutrient_totals = log.nutrient_totals or {}
        for nutrient in totals:
            if nutrient not in nutrient_totals:
                continue
            totals[nutrient] += float(nutrient_totals.get(nutrient) or 0)
            sample_counts[nutrient] += 1

    avg_nutrients = {}
    for nutrient, total in totals.items():
        count = sample_counts[nutrient]
        avg_nutrients[nutrient] = round(total / count, 1) if count else None

    return avg_nutrients, sample_counts


def _calculate_nutrition_score(avg_nutrients: dict, sample_counts: dict) -> int:
    scores = []
    for nutrient, avg in avg_nutrients.items():
        if sample_counts.get(nutrient, 0) == 0 or avg is None:
            continue
        recommended = DAILY_RECOMMENDED[nutrient]
        if recommended <= 0:
            continue
        ratio = avg / recommended
        if nutrient in UPPER_LIMIT_NUTRITION_KEYS:
            score = 100 if ratio <= 1 else 100 - min(100, (ratio - 1) * 100)
        else:
            score = 100 - min(100, abs(1 - ratio) * 100)
        scores.append(score)
    return round(sum(scores) / len(scores)) if scores else 0


def _nutrient_status(nutrient: str, ratio: float) -> str:
    if nutrient in UPPER_LIMIT_NUTRITION_KEYS:
        return "high" if ratio > 1.2 else "ok"
    if ratio < 0.8:
        return "low"
    if ratio > 1.2:
        return "high"
    return "ok"


def _calculate_alerts(avg_nutrients: dict, sample_counts: dict, recorded_day_count: int) -> list:
    if recorded_day_count < 3:
        return []

    alerts = []
    for nutrient, avg in avg_nutrients.items():
        if sample_counts.get(nutrient, 0) == 0 or avg is None:
            continue
        recommended = DAILY_RECOMMENDED[nutrient]
        ratio = avg / recommended if recommended > 0 else 1
        label = NUTRITION_FIELD_LABELS.get(nutrient, nutrient)
        if nutrient not in UPPER_LIMIT_NUTRITION_KEYS and ratio < ALERT_DEFICIENCY_RATIO:
            alerts.append({
                "type": "deficiency",
                "nutrient": nutrient,
                "ratio": round(ratio, 2),
                "message": f"周期日均{label}摄入不足（达到推荐量的{int(ratio * 100)}%）",
            })
        elif ratio > ALERT_EXCESS_RATIO:
            alerts.append({
                "type": "excess",
                "nutrient": nutrient,
                "ratio": round(ratio, 2),
                "message": f"周期日均{label}摄入偏高（达到推荐量的{int(ratio * 100)}%）",
            })
    return alerts


class NutritionService:
    def compute_daily_log(self, student_id: int, log_date: date) -> NutritionLog:
        """Compute and persist daily nutrition log for a student."""
        # Get all matched consumption records for this student on this date
        matches = MatchResult.query.filter(
            MatchResult.student_id == student_id,
            MatchResult.match_date == log_date,
            MatchResult.status.in_([MatchStatusEnum.matched, MatchStatusEnum.confirmed]),
        ).all()

        totals = {k: 0.0 for k in DAILY_RECOMMENDED}
        dish_ids_consumed = []

        for match in matches:
            if not match.image_id:
                continue
            recs = DishRecognition.query.filter_by(
                image_id=match.image_id,
                is_low_confidence=False,
            ).all()
            for rec in recs:
                if rec.dish_id:
                    dish = Dish.query.get(rec.dish_id)
                    if dish:
                        dish_ids_consumed.append(rec.dish_id)
                        for nutrient in totals:
                            val = getattr(dish, nutrient, None)
                            if val:
                                totals[nutrient] += float(val)

        log = NutritionLog.query.filter_by(
            student_id=student_id, log_date=log_date
        ).first()
        if not log:
            log = NutritionLog(student_id=student_id, log_date=log_date)
            db.session.add(log)

        log.nutrient_totals = totals
        log.meal_count = len(matches)
        log.dish_ids = list(set(dish_ids_consumed))
        db.session.commit()
        return log

    def generate_personal_report(
        self, student_id: int, period_start: date, period_end: date
    ) -> dict:
        """Generate a personal report based only on period-average nutrition."""
        student = Student.query.get(student_id)
        if not student:
            return {}

        logs = NutritionLog.query.filter(
            NutritionLog.student_id == student_id,
            NutritionLog.log_date >= period_start,
            NutritionLog.log_date <= period_end,
        ).order_by(NutritionLog.log_date).all()

        recorded_days = [log for log in logs if log.meal_count > 0]
        avg_nutrients, nutrient_sample_counts = _calculate_average_nutrients(recorded_days)
        alerts = _calculate_alerts(avg_nutrients, nutrient_sample_counts, len(recorded_days))
        overall_score = _calculate_nutrition_score(avg_nutrients, nutrient_sample_counts)
        suggestions = _generate_suggestions(avg_nutrients, nutrient_sample_counts)

        return {
            "student_id": student_id,
            "student_name": student.name,
            "class_name": (student.class_.name if student.class_ else student.class_name),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "analysis_basis": "period_daily_average",
            "avg_nutrients": avg_nutrients,
            "recommended_nutrients": DAILY_RECOMMENDED,
            "nutrient_sample_counts": nutrient_sample_counts,
            "alerts": alerts,
            "overall_score": overall_score,
            "suggestions": suggestions,
        }

    def generate_class_report(
        self, class_id: int, period_start: date, period_end: date
    ) -> dict:
        return self.generate_group_report("class", class_id, period_start, period_end)

    def generate_group_report(
        self, scope_type: str, scope_id: int, period_start: date, period_end: date
    ) -> dict:
        """Generate class, grade, or campus analysis without individual details."""
        from app.models import Campus, Class, Grade, Stage

        scope_models = {"class": Class, "grade": Grade, "campus": Campus}
        scope_model = scope_models.get(scope_type)
        scope = scope_model.query.get(scope_id) if scope_model else None
        if scope is None:
            return {}

        students_query = Student.query.filter(Student.is_active.is_(True))
        if scope_type == "class":
            students_query = students_query.filter(Student.class_id == scope_id)
        elif scope_type == "grade":
            students_query = students_query.join(Class, Student.class_id == Class.id).filter(Class.grade_id == scope_id)
        else:
            students_query = (
                students_query
                .join(Class, Student.class_id == Class.id)
                .join(Grade, Class.grade_id == Grade.id)
                .join(Stage, Grade.stage_id == Stage.id)
                .filter(Stage.campus_id == scope_id)
            )
        students = students_query.order_by(Student.id).all()
        student_ids = [student.id for student in students]

        logs_by_student = {student_id: [] for student_id in student_ids}
        if student_ids:
            logs = NutritionLog.query.filter(
                NutritionLog.student_id.in_(student_ids),
                NutritionLog.log_date >= period_start,
                NutritionLog.log_date <= period_end,
                NutritionLog.meal_count > 0,
            ).order_by(NutritionLog.student_id, NutritionLog.log_date).all()
            for log in logs:
                logs_by_student[log.student_id].append(log)

        student_averages = []
        for student_id in student_ids:
            averages, sample_counts = _calculate_average_nutrients(logs_by_student[student_id])
            has_data = any(count > 0 for count in sample_counts.values())
            student_averages.append({
                "averages": averages,
                "sample_counts": sample_counts,
                "has_data": has_data,
                "score": _calculate_nutrition_score(averages, sample_counts) if has_data else None,
            })

        student_count = len(students)
        students_with_data = sum(1 for item in student_averages if item["has_data"])
        avg_nutrients = {}
        nutrient_sample_counts = {}
        nutrient_distributions = {}

        for nutrient, recommended in DAILY_RECOMMENDED.items():
            values = [
                item["averages"][nutrient]
                for item in student_averages
                if item["sample_counts"].get(nutrient, 0) > 0 and item["averages"][nutrient] is not None
            ]
            avg_nutrients[nutrient] = round(sum(values) / len(values), 1) if values else None
            nutrient_sample_counts[nutrient] = len(values)

            counts = {"low": 0, "ok": 0, "high": 0, "no_data": student_count - len(values)}
            for value in values:
                status = _nutrient_status(nutrient, value / recommended if recommended > 0 else 1)
                counts[status] += 1
            measured = len(values)
            nutrient_distributions[nutrient] = {
                **counts,
                "measured_count": measured,
                "low_rate": round(counts["low"] * 100 / measured) if measured else 0,
                "ok_rate": round(counts["ok"] * 100 / measured) if measured else 0,
                "high_rate": round(counts["high"] * 100 / measured) if measured else 0,
                "coverage_rate": round(measured * 100 / student_count) if student_count else 0,
            }

        scores = [item["score"] for item in student_averages if item["score"] is not None]
        score_distribution = {"excellent": 0, "good": 0, "attention": 0, "improve": 0, "no_data": student_count - len(scores)}
        for score in scores:
            if score >= 90:
                score_distribution["excellent"] += 1
            elif score >= 75:
                score_distribution["good"] += 1
            elif score >= 60:
                score_distribution["attention"] += 1
            else:
                score_distribution["improve"] += 1

        focus_nutrients = []
        for nutrient, distribution in nutrient_distributions.items():
            measured = distribution["measured_count"]
            attention_count = distribution["low"] + distribution["high"]
            if not measured or not attention_count:
                continue
            dominant_status = "low" if distribution["low"] >= distribution["high"] else "high"
            focus_nutrients.append({
                "nutrient": nutrient,
                "label": NUTRITION_FIELD_LABELS.get(nutrient, nutrient),
                "dominant_status": dominant_status,
                "attention_rate": round(attention_count * 100 / measured),
                "low_rate": distribution["low_rate"],
                "high_rate": distribution["high_rate"],
                "measured_count": measured,
            })
        focus_nutrients.sort(key=lambda item: (-item["attention_rate"], -item["measured_count"], item["nutrient"]))

        average_score = round(sum(scores) / len(scores)) if scores else 0
        result = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "scope_name": scope.name,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "analysis_basis": "student_period_average",
            "student_count": student_count,
            "students_with_data": students_with_data,
            "data_coverage_rate": round(students_with_data * 100 / student_count) if student_count else 0,
            "avg_nutrients": avg_nutrients,
            "recommended_nutrients": DAILY_RECOMMENDED,
            "nutrient_sample_counts": nutrient_sample_counts,
            "nutrient_distributions": nutrient_distributions,
            "average_score": average_score,
            "score_distribution": score_distribution,
            "focus_nutrients": focus_nutrients[:5],
            "suggestions": _generate_suggestions(avg_nutrients, nutrient_sample_counts),
        }
        if scope_type == "class":
            result["class_id"] = scope_id
            result["class_avg_score"] = average_score
        return result

    def get_alerts_for_user(self, user) -> list:
        alerts = []
        if user.role.value == "parent":
            for student_id in (user.student_ids or []):
                student_alerts = self._check_student_alerts(student_id)
                alerts.extend(student_alerts)
        elif user.role.value == "teacher":
            for class_id in (user.managed_class_ids or []):
                students = Student.query.filter_by(class_id=class_id, is_active=True).all()
                for s in students[:20]:  # limit
                    alerts.extend(self._check_student_alerts(s.id))
        return alerts[:50]

    def _check_student_alerts(self, student_id: int) -> list:
        today = date.today()
        start = today - timedelta(days=ALERT_DAYS_THRESHOLD)
        logs = NutritionLog.query.filter(
            NutritionLog.student_id == student_id,
            NutritionLog.log_date >= start,
        ).all()
        alerts = []

        # Check skip meal
        meal_days = sum(1 for log in logs if log.meal_count > 0)
        if len(logs) >= ALERT_SKIP_MEAL_DAYS and meal_days == 0:
            student = Student.query.get(student_id)
            alerts.append({
                "type": "no_meal",
                "student_id": student_id,
                "student_name": student.name if student else "",
                "message": f"连续{ALERT_SKIP_MEAL_DAYS}天无就餐记录",
            })
        return alerts


def _has_nutrient_data(sample_counts: dict | None, nutrient: str) -> bool:
    return sample_counts is None or sample_counts.get(nutrient, 0) > 0


def _generate_suggestions(avg_nutrients: dict, sample_counts: dict | None = None) -> list:
    suggestions = []
    rec = DAILY_RECOMMENDED
    if _has_nutrient_data(sample_counts, "protein") and (avg_nutrients.get("protein") or 0) < rec["protein"] * 0.8:
        suggestions.append("建议增加豆制品、蛋类、禽肉等富含蛋白质的食物")
    if _has_nutrient_data(sample_counts, "fiber") and (avg_nutrients.get("fiber") or 0) < rec["fiber"] * 0.7:
        suggestions.append("建议多吃蔬菜、全谷物，增加膳食纤维摄入")
    if _has_nutrient_data(sample_counts, "calcium") and (avg_nutrients.get("calcium") or 0) < rec["calcium"] * 0.7:
        suggestions.append("建议增加奶类、豆制品或深绿色蔬菜，补充钙摄入")
    if _has_nutrient_data(sample_counts, "iron") and (avg_nutrients.get("iron") or 0) < rec["iron"] * 0.7:
        suggestions.append("建议适量增加瘦肉、动物肝脏或深色蔬菜，关注铁摄入")
    if _has_nutrient_data(sample_counts, "vitamin_c") and (avg_nutrients.get("vitamin_c") or 0) < rec["vitamin_c"] * 0.7:
        suggestions.append("建议增加新鲜蔬菜和水果，补充维生素C")
    if _has_nutrient_data(sample_counts, "sodium") and (avg_nutrients.get("sodium") or 0) > rec["sodium"] * 1.3:
        suggestions.append("建议减少重口味菜肴，控制钠盐摄入")
    if _has_nutrient_data(sample_counts, "added_sugar") and (avg_nutrients.get("added_sugar") or 0) > rec["added_sugar"] * 1.2:
        suggestions.append("建议减少含糖饮料、甜点和糖醋类高糖菜品")
    if _has_nutrient_data(sample_counts, "cholesterol") and (avg_nutrients.get("cholesterol") or 0) > rec["cholesterol"] * 1.2:
        suggestions.append("建议控制高胆固醇食物摄入，优先选择清蒸、少油烹调")
    if _has_nutrient_data(sample_counts, "calories") and (avg_nutrients.get("calories") or 0) < rec["calories"] * 0.7:
        suggestions.append("能量摄入偏低，建议适当增加主食和坚果类食物")
    if not suggestions:
        suggestions.append("整体营养摄入均衡，保持良好的饮食习惯")
    return suggestions
