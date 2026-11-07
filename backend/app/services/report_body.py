"""Age/sex specific BMI screening, independent of meal-supply assessment."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math

from app.services.report_assessment import reference_profile

BODY_REFERENCES = [
    {"title": "WS/T 456—2014《学龄儿童青少年营养不良筛查》", "url": "https://www.nhc.gov.cn/ewebeditor/uploadfile/2014/07/20140704142652587.pdf"},
    {"title": "WS/T 586—2018《学龄儿童青少年超重与肥胖筛查》", "url": "https://www.nhc.gov.cn/ewebeditor/uploadfile/2018/03/20180330094031236.pdf"},
]
WHO_REFERENCE = {"title": "WHO 身体活动和久坐行为指南（2020）", "url": "https://www.who.int/publications/i/item/9789240015128"}
BMI_STATUS = {"thinness": "偏瘦", "reference": "标准", "overweight": "偏胖", "obesity": "偏胖"}
BODY_COORDINATION = {
    "thinness": "规律安排三餐，保证充足且多样的营养供给；参加适宜运动，兼顾休息和生长。",
    "reference": "保持规律三餐和多样搭配，结合兴趣规律运动，维持良好的饮食与活动习惯。",
    "overweight": "合理安排三餐与食物份量，适量减少油炸和高脂菜品；循序渐进增加规律运动。",
    "obesity": "合理安排三餐与食物份量，适量减少油炸和高脂菜品；循序渐进增加规律运动。",
    "unknown": "结合实际餐食保持均衡饮食，规律安排适宜运动，持续关注生长变化。",
}


def bmi_status(body):
    return BMI_STATUS.get((body or {}).get("category"), "待评价")


def body_coordination_advice(body):
    return BODY_COORDINATION.get((body or {}).get("category"), BODY_COORDINATION["unknown"])


def group_coordination_advice():
    return "结合有效 BMI 筛查结果，支持学生规律三餐和适宜运动；关注偏瘦、偏胖学生的营养供给与活动安排。"

# Half-year rows from age 6.0 through 18.0. Each tuple is
# (boys' overweight, obesity, girls' overweight, obesity), WS/T 586 table 1.
OVERWEIGHT = (
    (16.4, 17.7, 16.2, 17.5), (16.7, 18.1, 16.5, 18.0), (17.0, 18.7, 16.8, 18.5),
    (17.4, 19.2, 17.2, 19.0), (17.8, 19.7, 17.6, 19.4), (18.1, 20.3, 18.1, 19.9),
    (18.5, 20.8, 18.5, 20.4), (18.9, 21.4, 19.0, 21.0), (19.2, 21.9, 19.5, 21.5),
    (19.6, 22.5, 20.0, 22.1), (19.9, 23.0, 20.5, 22.7), (20.3, 23.6, 21.1, 23.3),
    (20.7, 24.1, 21.5, 23.9), (21.0, 24.7, 21.9, 24.5), (21.4, 25.2, 22.2, 25.0),
    (21.9, 25.7, 22.6, 25.6), (22.3, 26.1, 22.8, 25.9), (22.6, 26.4, 23.0, 26.3),
    (22.9, 26.6, 23.2, 26.6), (23.1, 26.9, 23.4, 26.9), (23.3, 27.1, 23.6, 27.1),
    (23.5, 27.4, 23.7, 27.4), (23.7, 27.6, 23.8, 27.6), (23.8, 27.8, 23.9, 27.8),
    (24.0, 28.0, 24.0, 28.0),
)
# Inclusive upper limits for any thinness (mild or moderate/severe), boys/girls,
# WS/T 456 table B.1. Its final row is 17.5–18.0.
THINNESS = (
    (13.4, 13.1), (13.8, 13.3), (13.9, 13.4), (13.9, 13.5), (14.0, 13.6), (14.0, 13.7),
    (14.1, 13.8), (14.2, 13.9), (14.4, 14.0), (14.6, 14.1), (14.9, 14.3), (15.1, 14.5),
    (15.4, 14.7), (15.6, 14.9), (15.9, 15.3), (16.1, 15.6), (16.4, 16.0), (16.7, 16.3),
    (16.9, 16.6), (17.0, 16.8), (17.3, 17.0), (17.5, 17.1), (17.7, 17.2), (17.9, 17.3),
)


def measured_profile(student, as_of):
    """Use measured-date birthdays or explicitly entered age, never grade inference."""
    birthday = getattr(student, "birth_date", None)
    age = getattr(student, "age", None)
    profile = reference_profile(as_of=as_of, birth_date=birthday, age=age, gender=getattr(student, "gender", None))
    months = None
    if birthday:
        # Clamp month-end anniversaries, including February 29 births.
        anniversary = min(birthday.day, monthrange(as_of.year, as_of.month)[1])
        months = (as_of.year - birthday.year) * 12 + as_of.month - birthday.month - (as_of.day < anniversary)
    elif type(age) is int and age >= 0:
        months = age * 12
    profile["age_months"] = months
    profile["screening_age"] = months // 6 / 2 if months is not None and months >= 0 else None
    return profile


def assess_body(measurements, student=None):
    result = {"version": 1, "height": None, "weight": None, "measurement_date": None,
              "bmi": None, "category": "unknown", "label": "待评价", "profile": {},
              "reason": "暂无同一次有效的身高体重测量，暂不作体型筛查。", "references": BODY_REFERENCES}
    height, weight = measurements.get("height") or {}, measurements.get("weight") or {}
    values = [height.get("value"), weight.get("value")]
    if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in values):
        return result
    if not height.get("date") or height.get("date") != weight.get("date"):
        return result
    try:
        day = date.fromisoformat(height["date"])
        raw_bmi = values[1] / (values[0] / 100) ** 2
        if not math.isfinite(raw_bmi) or raw_bmi <= 0:
            return result
        bmi = float(Decimal(str(raw_bmi)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        if bmi <= 0:
            return result
    except (ValueError, OverflowError, ZeroDivisionError, InvalidOperation):
        return result
    profile = measured_profile(student, day)
    result.update(height=values[0], weight=values[1], measurement_date=day.isoformat(), bmi=bmi, profile=profile)
    months, sex = profile["age_months"], profile["gender"]
    if months is None or not sex:
        missing = "、".join(label for label, absent in (("明确年龄", months is None), ("性别", not sex)) if absent)
        result.update(label="BMI 记录", reason=f"缺少{missing}，BMI 仅作数值记录，暂不作体型分类。")
        return result
    if not 72 <= months <= 216:
        result["reason"] = "年龄超出本报告筛查表的适用范围（6.0—18.0岁），仅展示 BMI。"
        return result
    index, sex_index = (months - 72) // 6, 0 if sex == "男" else 1
    thin = THINNESS[min(index, len(THINNESS) - 1)][sex_index]
    overweight, obesity = OVERWEIGHT[index][sex_index * 2:sex_index * 2 + 2]
    if bmi >= obesity:
        category, label = "obesity", "肥胖筛查提示"
    elif bmi >= overweight:
        category, label = "overweight", "超重筛查提示"
    elif bmi <= thin:
        category, label = "thinness", "消瘦筛查提示"
    else:
        category, label = "reference", "参考范围内"
    result.update(category=category, label=label, reason="BMI 筛查结果仅供成长观察，不作为健康诊断。",
                  thresholds={"thinness_max": thin, "overweight_min": overweight, "obesity_min": obesity})
    return result


def activity_reference(student, end):
    months = measured_profile(student, end)["age_months"]
    if months is None or not 60 <= months < 216:
        return None
    return {**WHO_REFERENCE, "text": "5—17岁参考：每周平均每天至少60分钟中高强度活动；每周至少3天安排高强度及增强肌肉、骨骼的活动。"}


def health_advice(content):
    """Short evidence-based copy shared by both report formats."""
    if content.get("advice_snapshot"):
        from app.services.report_advice import snapshot_items
        return snapshot_items(content, "sports", "family")
    body, sports = content.get("body_assessment") or {}, content.get("sports") or {}
    category = body.get("category")
    if category in ("overweight", "obesity"):
        body_advice = "均衡安排三餐，减少含糖饮料，循序渐进安排活动；与家长、校医一起关注并定期复测。"
    elif category == "thinness":
        body_advice = "规律用餐、均衡搭配，运动循序渐进并保证休息；与家长、校医一起关注生长并定期复测。"
    elif category == "reference":
        body_advice = "保持均衡饮食、规律活动和充足睡眠，定期测量身高体重，持续关注成长。"
    elif body.get("bmi") is not None and body.get("profile", {}).get("age_months") is not None and not 72 <= body["profile"]["age_months"] <= 216:
        body_advice = "本次 BMI 仅作记录；保持均衡饮食和规律活动，可请校医结合年龄与生长记录评估。"
    elif body.get("bmi") is not None:
        body_advice = "定期记录身高体重，结合成长过程持续观察；保持均衡饮食、规律活动和充足睡眠。"
    else:
        body_advice = "定期测量并记录同次身高体重，结合成长过程持续观察，保持均衡饮食和规律活动。"
    if not sports.get("practice_count"):
        title, text = "持续记录日常活动", "本期暂无练习记录；结合体育课、课外和家庭活动持续记录，逐步形成规律。"
    elif sports.get("active_days") == 1:
        title, text = "分散安排运动", "本期练习记录集中在1天，可结合体育课和家庭活动分散安排，注意休息。"
    else:
        title, text = "运动保持规律", "结合体育课、课外和家庭活动规律运动，循序渐进，避免集中堆量。"
    if sports.get("project_count") == 1:
        text += "可交替安排适合自己的跑跳、柔韧等不同活动。"
    return [{"icon": "family", "title": "关注体型与成长", "text": body_advice},
            {"icon": "running", "title": title, "text": text}]
