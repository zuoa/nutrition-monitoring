"""Point values and grade thresholds saved with each nutrition report."""

from copy import deepcopy


SCORING_CONFIG_KEY = "REPORT_ASSESSMENT_SCORING"
SCORING_SNAPSHOT_KEY = "report_assessment_scoring"
STATUS_LABELS = {
    "low": "偏低",
    "slightly_low": "轻微偏低",
    "met": "达标",
    "slightly_high": "轻微偏高",
    "high": "偏高",
}
DEFAULT_ASSESSMENT_SCORING = {
    "status_points": {"low": 5, "slightly_low": 15, "met": 20, "slightly_high": 15, "high": 5},
    "grade_min_scores": {"A": 80, "B": 70, "C": 55},
}


def validate_report_assessment_scoring(value):
    if not isinstance(value, dict) or set(value) != {"status_points", "grade_min_scores"}:
        raise ValueError("营养报告计分须包含状态分值和等级起点")
    points = value["status_points"]
    if not isinstance(points, dict) or points.keys() != STATUS_LABELS.keys():
        raise ValueError("须填写全部五种营养状态的分值")
    for key, label in STATUS_LABELS.items():
        if type(points[key]) is not int or not 0 <= points[key] <= 20:
            raise ValueError(f"{label}分值必须是 0～20 的整数")
    grades = value["grade_min_scores"]
    if (not isinstance(grades, dict) or grades.keys() != DEFAULT_ASSESSMENT_SCORING["grade_min_scores"].keys()
            or any(type(score) is not int for score in grades.values())):
        raise ValueError("须填写 A、B、C 级的整数起点")
    minimum = 5 * min(points.values())
    maximum = 5 * max(points.values())
    if not minimum < grades["C"] < grades["B"] < grades["A"] <= maximum:
        raise ValueError(f"等级起点须满足 {minimum} < C < B < A ≤ {maximum}")
    return {"status_points": dict(points), "grade_min_scores": dict(grades)}


def get_report_assessment_scoring(config):
    try:
        return validate_report_assessment_scoring(config.get(SCORING_CONFIG_KEY))
    except ValueError:
        return deepcopy(DEFAULT_ASSESSMENT_SCORING)
