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


def parse_composed_description(raw: object) -> dict:
    details = empty_structured_description()
    normalized = str(raw or "").replace("\r\n", "\n").strip()
    if not normalized:
        return {"summary": "", "structured_description": details}

    summary_lines = []
    in_structured_section = False
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if not in_structured_section and summary_lines[-1:] != [""]:
                summary_lines.append("")
            continue
        if line == STRUCTURED_DESCRIPTION_SECTION:
            in_structured_section = True
            continue
        if not in_structured_section:
            summary_lines.append(line)
            continue

        matched = False
        for key, label in STRUCTURED_DESCRIPTION_FIELDS:
            for separator in ("：", ":"):
                prefix = f"{label}{separator}"
                if line.startswith(prefix):
                    details[key] = line[len(prefix):].strip()
                    matched = True
                    break
            if matched:
                break

        if not matched:
            summary_lines.append(line)

    summary = "\n".join(summary_lines).strip()
    return {"summary": summary, "structured_description": details}
