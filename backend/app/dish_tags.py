"""Controlled, user-confirmed dish labels; independent of nutrition estimates."""

FOOD_GROUPS = ("谷薯", "畜肉", "禽肉", "鱼虾", "蛋", "豆", "蔬菜", "水果", "奶", "坚果")
COOKING_METHODS = ("蒸", "煮", "炖", "炒", "煎", "炸", "烤", "凉拌", "其他")
VEGETABLE_TYPES = ("深色蔬菜",)
TAG_FIELDS = ("food_groups", "cooking_method", "vegetable_types")


def normalize_dish_tags(data):
    """Validate only supplied fields so partial updates retain existing labels."""
    result = {}
    for field, allowed in (("food_groups", FOOD_GROUPS), ("vegetable_types", VEGETABLE_TYPES)):
        if field not in data:
            continue
        values = data[field]
        if not isinstance(values, list) or any(not isinstance(value, str) or value not in allowed for value in values):
            raise ValueError(f"{field} 必须是数组，可选：{'、'.join(allowed)}")
        result[field] = list(dict.fromkeys(values))
    if "cooking_method" in data:
        value = data["cooking_method"]
        if value not in (None, "", *COOKING_METHODS):
            raise ValueError(f"cooking_method 无效，可选：{'、'.join(COOKING_METHODS)}")
        result["cooking_method"] = value or None
    return result


def ai_tag_suggestions(data):
    """Ignore malformed model suggestions without discarding valid nutrition."""
    result = {}
    for field in TAG_FIELDS:
        if field in data:
            try:
                result.update(normalize_dish_tags({field: data[field]}))
            except ValueError:
                pass
    return result
