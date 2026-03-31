STRUCTURED_DESCRIPTION_SECTION = "【识别特征】"

STRUCTURED_DESCRIPTION_FIELDS = [
    ("mainIngredients", "主食材"),
    ("colors", "颜色"),
    ("cuts", "切法/形态"),
    ("texture", "质地"),
    ("sauce", "汁感"),
    ("garnishes", "常见配菜"),
    ("confusableWith", "易混淆菜"),
]

STRUCTURED_DESCRIPTION_ALIASES = {
    "mainIngredients": ["mainIngredients", "main_ingredients", "ingredients", "mainIngredient"],
    "colors": ["colors", "color", "colour"],
    "cuts": ["cuts", "shapes", "shape", "forms"],
    "texture": ["texture"],
    "sauce": ["sauce", "sauciness", "gravy"],
    "garnishes": ["garnishes", "sideIngredients", "side_ingredients", "side_dishes"],
    "confusableWith": ["confusableWith", "confusable_with", "similarDishes", "similar_dishes"],
}


def empty_structured_description() -> dict:
    return {key: "" for key, _ in STRUCTURED_DESCRIPTION_FIELDS}


def normalize_structured_description(raw: object) -> dict:
    normalized = empty_structured_description()
    if not isinstance(raw, dict):
        return normalized

    for key, aliases in STRUCTURED_DESCRIPTION_ALIASES.items():
        for alias in aliases:
            value = raw.get(alias)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                normalized[key] = text
                break

    return normalized


def has_structured_description(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    return any(str(value or "").strip() for value in raw.values())


def compose_structured_description(summary: str, details: object) -> str:
    parts = []
    normalized_summary = str(summary or "").replace("\r\n", "\n").strip()
    normalized_details = normalize_structured_description(details)

    if normalized_summary:
        parts.append(normalized_summary)

    detail_lines = []
    for key, label in STRUCTURED_DESCRIPTION_FIELDS:
        value = normalized_details.get(key, "").strip()
        if value:
            detail_lines.append(f"{label}：{value}")

    if detail_lines:
        parts.append("\n".join([STRUCTURED_DESCRIPTION_SECTION, *detail_lines]))

    return "\n\n".join(parts).strip()
