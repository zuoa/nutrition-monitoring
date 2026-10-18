"""Versioned, source-checked context; never an individual diagnostic threshold."""

REPORT_VERSION = 2
REFERENCES = [
    {"id": "five-health-2026", "title": "儿童青少年“五健”促进行动计划（2026—2030年）", "kind": "国家政策",
     "publisher": "国家卫生健康委等13部门", "version": "2026—2030年", "use": "健康教育与家校协同",
     "url": "https://www.nhc.gov.cn/fys/c100078/202512/9c3da4badfb34026b5fabbf5853105ee.shtml"},
    {"id": "ws554-2017", "title": "学生餐营养指南", "kind": "推荐性卫生行业标准",
     "publisher": "原国家卫生计生委", "version": "WS/T 554—2017", "use": "表1及续表：6—17岁全天供给参考；3.3：三餐营养份额",
     "url": "https://www.nhc.gov.cn/wjw/yingyang/201708/e8a131882d2c4a8c95f10a8b7b5fe662.shtml"},
    {"id": "school-diet-2022", "title": "中国学龄儿童膳食指南（2022）", "kind": "学会指南",
     "publisher": "中国营养学会", "version": "2022", "use": "通用饮食建议",
     "url": "https://dg.cnsoc.org/article/04/7dFWBzmrTOyPOMsrVHQOfw.html"},
    {"id": "dri-2023", "title": "中国居民膳食营养素参考摄入量（2023版）", "kind": "学会科学参考资料",
     "publisher": "中国营养学会", "version": "2023", "use": "参考摄入量概念；本报告数值表未混用该版本",
     "url": "https://www.cnsoc.org/acadconfn/792310200.html"},
]

# WS/T 554—2017 table 1, printed pages 1–2; visually checked 2026-10-18.
# Shared cells in the source apply to both sexes. Do not substitute adult NRVs.
_ROWS = [
    ("6—8岁", [1700, 1550], [40, 40], 750, 12, [6.5, 6.5], [450, 450], 60, 20),
    ("9—11岁", [2100, 1900], [50, 50], 850, 14, [8, 8], [550, 550], 75, 20),
    ("12—14岁", [2450, 2100], [65, 60], 950, 18, [10.5, 9], [720, 630], 95, 20),
    ("15—17岁", [2900, 2350], [75, 60], 800, 18, [11.5, 8.5], [820, 630], 100, 25),
]
REFERENCE_TABLE = [
    {"age_group": age, "sex": sex, "calories": energy[i], "protein": protein[i],
     "calcium": calcium, "iron": iron, "zinc": zinc[i], "vitamin_a": vitamin_a[i],
     "vitamin_c": vitamin_c, "fiber": fiber}
    for age, energy, protein, calcium, iron, zinc, vitamin_a, vitamin_c, fiber in _ROWS
    for i, sex in enumerate(("男", "女"))
]

GENERAL_SUGGESTIONS = [
    "学生｜规律安排三餐，主食、蔬果和鱼肉蛋豆类合理搭配，主动饮水。",
    "家庭｜结合校内餐食安排校外饮食，关注奶类、水果和食物多样性，减少含糖饮料。",
    "食堂｜提供多样食材与带量食谱，优先采用蒸、炖、烩等烹调方式，控制油盐糖。",
]
LIMITATION = "原始值仅反映已记录的在校膳食营养估算；全天折算值假设未记录餐次具有相同营养供给水平，校外饮食、零食及剩餐未计入，不代表全天实际摄入。年龄资料未提供，未进行个人达标或生长发育评价。"


def reference_snapshot():
    from copy import deepcopy
    return {"report_version": REPORT_VERSION, "references": deepcopy(REFERENCES),
            "reference_table": deepcopy(REFERENCE_TABLE), "reference_checked_at": "2026-10-18",
            "limitations": LIMITATION}
