import logging
from datetime import date, timedelta
from app import db
from app.models import NutritionLog, MatchResult, DishRecognition, Dish, MatchStatusEnum, Student

logger = logging.getLogger(__name__)

# Chinese dietary guidelines (per day for school-age children ~10-15 years)
DAILY_RECOMMENDED = {
    "calories": 2000,    # kcal
    "protein": 60,       # g
    "fat": 65,           # g
    "carbohydrate": 275, # g
    "sodium": 2000,      # mg
    "fiber": 25,         # g
}

ALERT_DEFICIENCY_RATIO = 0.6
ALERT_EXCESS_RATIO = 1.5
ALERT_DAYS_THRESHOLD = 5
ALERT_SKIP_MEAL_DAYS = 3
ALERT_DIVERSITY_DAYS = 7
ALERT_DIVERSITY_MIN_SCORE = 0.4


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
                            val = getattr(dish, nutrient)
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

        meal_days = [l for l in logs if l.meal_count > 0]
        total_days = (period_end - period_start).days + 1

        # Average nutrients
        avg_nutrients = {k: 0.0 for k in DAILY_RECOMMENDED}
        if meal_days:
            for log in meal_days:
                for k in avg_nutrients:
                    avg_nutrients[k] += log.nutrient_totals.get(k, 0)
            for k in avg_nutrients:
                avg_nutrients[k] = round(avg_nutrients[k] / len(meal_days), 1)

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
            rec = DAILY_RECOMMENDED[nutrient]
            ratio = avg / rec if rec > 0 else 1
            if ratio < ALERT_DEFICIENCY_RATIO and len(meal_days) >= 3:
                alerts.append({
                    "type": "deficiency",
                    "nutrient": nutrient,
                    "ratio": round(ratio, 2),
                    "message": f"{nutrient}摄入不足（仅达到推荐量的{int(ratio*100)}%）",
                })
            elif ratio > ALERT_EXCESS_RATIO and len(meal_days) >= 3:
                alerts.append({
                    "type": "excess",
                    "nutrient": nutrient,
                    "ratio": round(ratio, 2),
                    "message": f"{nutrient}摄入超标（达到推荐量的{int(ratio*100)}%）",
                })

        # Nutrition score (0-100)
        scores = []
        for nutrient, avg in avg_nutrients.items():
            rec = DAILY_RECOMMENDED[nutrient]
            if rec > 0:
                ratio = avg / rec
                score = 100 - min(100, abs(1 - ratio) * 100)
                scores.append(score)
        overall_score = round(sum(scores) / len(scores)) if scores else 0

        # Suggestions
        suggestions = _generate_suggestions(avg_nutrients)

        return {
            "student_id": student_id,
            "student_name": student.name,
            "class_name": student.class_name,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "meal_days": len(meal_days),
            "total_days": total_days,
            "avg_nutrients": avg_nutrients,
            "recommended_nutrients": DAILY_RECOMMENDED,
            "top_dishes": top_dish_names,
            "alerts": alerts,
            "overall_score": overall_score,
            "suggestions": suggestions,
        }

    def generate_class_report(
        self, class_id: str, period_start: date, period_end: date
    ) -> dict:
        students = Student.query.filter_by(class_id=class_id, is_active=True).all()
        if not students:
            return {}

        reports = []
        for s in students:
            r = self.generate_personal_report(s.id, period_start, period_end)
            reports.append(r)

        avg_class = {k: 0.0 for k in DAILY_RECOMMENDED}
        for r in reports:
            for k in avg_class:
                avg_class[k] += r.get("avg_nutrients", {}).get(k, 0)
        if reports:
            for k in avg_class:
                avg_class[k] = round(avg_class[k] / len(reports), 1)

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
        meal_days = sum(1 for l in logs if l.meal_count > 0)
        if len(logs) >= ALERT_SKIP_MEAL_DAYS and meal_days == 0:
            student = Student.query.get(student_id)
            alerts.append({
                "type": "no_meal",
                "student_id": student_id,
                "student_name": student.name if student else "",
                "message": f"连续{ALERT_SKIP_MEAL_DAYS}天无就餐记录",
            })
        return alerts


def _generate_suggestions(avg_nutrients: dict) -> list:
    suggestions = []
    rec = DAILY_RECOMMENDED
    if avg_nutrients.get("protein", 0) < rec["protein"] * 0.8:
        suggestions.append("建议增加豆制品、蛋类、禽肉等富含蛋白质的食物")
    if avg_nutrients.get("fiber", 0) < rec["fiber"] * 0.7:
        suggestions.append("建议多吃蔬菜、全谷物，增加膳食纤维摄入")
    if avg_nutrients.get("sodium", 0) > rec["sodium"] * 1.3:
        suggestions.append("建议减少重口味菜肴，控制钠盐摄入")
    if avg_nutrients.get("calories", 0) < rec["calories"] * 0.7:
        suggestions.append("热量摄入偏低，建议适当增加主食和坚果类食物")
    if not suggestions:
        suggestions.append("整体营养摄入均衡，保持良好的饮食习惯")
    return suggestions
