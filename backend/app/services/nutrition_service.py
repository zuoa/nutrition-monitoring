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
        """Generate personal nutrition report for date range."""
        student = Student.query.get(student_id)
        if not student:
            return {}

        logs = NutritionLog.query.filter(
            NutritionLog.student_id == student_id,
            NutritionLog.log_date >= period_start,
            NutritionLog.log_date <= period_end,
        ).order_by(NutritionLog.log_date).all()

        meal_days = [log for log in logs if log.meal_count > 0]
        total_days = (period_end - period_start).days + 1

        avg_nutrients, nutrient_sample_counts = _calculate_average_nutrients(meal_days)

        # Dish frequency
        all_dish_ids = []
        for log in meal_days:
            all_dish_ids.extend(log.dish_ids or [])
        dish_freq = {}
        for did in all_dish_ids:
            dish_freq[did] = dish_freq.get(did, 0) + 1
        top_dishes = sorted(dish_freq.items(), key=lambda x: -x[1])[:5]
        top_dish_names = []
        for did, cnt in top_dishes:
            d = Dish.query.get(did)
            if d:
                top_dish_names.append({"name": d.name, "count": cnt})

        # Alerts
        alerts = []
        for nutrient, avg in avg_nutrients.items():
            if nutrient_sample_counts.get(nutrient, 0) == 0 or avg is None:
                continue
            rec = DAILY_RECOMMENDED[nutrient]
            ratio = avg / rec if rec > 0 else 1
            label = NUTRITION_FIELD_LABELS.get(nutrient, nutrient)
            if nutrient not in UPPER_LIMIT_NUTRITION_KEYS and ratio < ALERT_DEFICIENCY_RATIO and len(meal_days) >= 3:
                alerts.append({
                    "type": "deficiency",
                    "nutrient": nutrient,
                    "ratio": round(ratio, 2),
                    "message": f"{label}摄入不足（仅达到推荐量的{int(ratio * 100)}%）",
                })
            elif ratio > ALERT_EXCESS_RATIO and len(meal_days) >= 3:
                alerts.append({
                    "type": "excess",
                    "nutrient": nutrient,
                    "ratio": round(ratio, 2),
                    "message": f"{label}摄入超标（达到推荐量的{int(ratio * 100)}%）",
                })

        # Nutrition score (0-100)
        scores = []
        for nutrient, avg in avg_nutrients.items():
            if nutrient_sample_counts.get(nutrient, 0) == 0 or avg is None:
                continue
            rec = DAILY_RECOMMENDED[nutrient]
            if rec > 0:
                ratio = avg / rec
                if nutrient in UPPER_LIMIT_NUTRITION_KEYS:
                    score = 100 if ratio <= 1 else 100 - min(100, (ratio - 1) * 100)
                else:
                    score = 100 - min(100, abs(1 - ratio) * 100)
                scores.append(score)
        overall_score = round(sum(scores) / len(scores)) if scores else 0

        # Suggestions
        suggestions = _generate_suggestions(avg_nutrients, nutrient_sample_counts)

        return {
            "student_id": student_id,
            "student_name": student.name,
            "class_name": (student.class_.name if student.class_ else student.class_name),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "meal_days": len(meal_days),
            "total_days": total_days,
            "avg_nutrients": avg_nutrients,
            "recommended_nutrients": DAILY_RECOMMENDED,
            "nutrient_sample_counts": nutrient_sample_counts,
            "top_dishes": top_dish_names,
            "alerts": alerts,
            "overall_score": overall_score,
            "suggestions": suggestions,
        }

    def generate_class_report(
        self, class_id: int, period_start: date, period_end: date
    ) -> dict:
        students = Student.query.filter_by(class_id=class_id, is_active=True).all()
        if not students:
            return {}

        reports = []
        for s in students:
            r = self.generate_personal_report(s.id, period_start, period_end)
            reports.append(r)

        avg_class = {k: 0.0 for k in DAILY_RECOMMENDED}
        class_sample_counts = {k: 0 for k in DAILY_RECOMMENDED}
        for r in reports:
            for k in avg_class:
                avg_value = r.get("avg_nutrients", {}).get(k)
                if r.get("nutrient_sample_counts", {}).get(k, 0) == 0 or avg_value is None:
                    continue
                avg_class[k] += avg_value
                class_sample_counts[k] += 1
        for k in avg_class:
            avg_class[k] = round(avg_class[k] / class_sample_counts[k], 1) if class_sample_counts[k] else None

        # Students with alerts (anonymized)
        flagged = []
        for r in reports:
            if r.get("alerts"):
                name = r.get("student_name", "")
                masked = (name[0] + "*") if name else "**"
                flagged.append({
                    "name_masked": masked,
                    "alerts": [a["message"] for a in r["alerts"]],
                    "score": r.get("overall_score", 0),
                })

        return {
            "class_id": class_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "student_count": len(students),
            "avg_nutrients": avg_class,
            "recommended_nutrients": DAILY_RECOMMENDED,
            "nutrient_sample_counts": class_sample_counts,
            "flagged_students": flagged,
            "class_avg_score": round(sum(r.get("overall_score", 0) for r in reports) / len(reports)) if reports else 0,
        }

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
