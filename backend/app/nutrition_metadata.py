from dataclasses import dataclass


@dataclass(frozen=True)
class NutritionField:
    key: str
    label: str
    unit: str
    recommended: float
    is_energy: bool = False
    upper_limit: bool = False

    @property
    def excel_header(self) -> str:
        return f"{self.label}({self.unit})"


NUTRITION_FIELDS = (
    NutritionField("calories", "能量", "kcal", 2000, is_energy=True),
    NutritionField("protein", "蛋白质", "g", 60),
    NutritionField("fat", "脂肪", "g", 65),
    NutritionField("cholesterol", "胆固醇", "mg", 300, upper_limit=True),
    NutritionField("carbohydrate", "碳水化合物", "g", 275),
    NutritionField("added_sugar", "添加糖", "g", 50, upper_limit=True),
    NutritionField("fiber", "膳食纤维", "g", 25),
    NutritionField("sodium", "钠", "mg", 2000, upper_limit=True),
    NutritionField("calcium", "钙", "mg", 1000),
    NutritionField("iron", "铁", "mg", 12),
    NutritionField("zinc", "锌", "mg", 10),
    NutritionField("vitamin_a", "维生素A", "ug RAE", 800),
    NutritionField("vitamin_c", "维生素C", "mg", 100),
    NutritionField("vitamin_d", "维生素D", "ug", 15),
)

NUTRITION_FIELD_KEYS = tuple(field.key for field in NUTRITION_FIELDS)
NUTRITION_FIELD_LABELS = {field.key: field.label for field in NUTRITION_FIELDS}
NUTRITION_FIELD_UNITS = {field.key: field.unit for field in NUTRITION_FIELDS}
DAILY_RECOMMENDED = {field.key: field.recommended for field in NUTRITION_FIELDS}
UPPER_LIMIT_NUTRITION_KEYS = frozenset(field.key for field in NUTRITION_FIELDS if field.upper_limit)
