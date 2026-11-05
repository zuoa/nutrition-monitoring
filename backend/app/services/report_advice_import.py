"""Read-only Excel preview and portable template for the fixed advice catalog."""

from io import BytesIO
from itertools import islice
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.exceptions import InvalidFileException

from app.services.report_advice import CATALOG, RULES, get_report_advice_rules, normalize_phrases

# These rows describe engine behavior, not independently selectable copy.
POLICY_ROWS = {
    "个人周报": {"建议数量", "多项指标同时异常", "相同食物重复出现", "体型数据缺失", "判定条件不完整"},
    "班级周报": {"班级引导", "家校沟通", "多项营养偏低", "群体均值正常但存在个体偏差", "无体育记录", "体育次数下降且营养估算偏高"},
    "年级周报": {"膳食观察", "体育观察", "食动配合", "周际样本构成变化", "体育次数变化", "群体营养偏差＋体育次数变化", "数据不完整"},
}


def preview_advice_import(stream, config):
    """Validate the entire workbook without writing configuration."""
    raw = stream.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Excel 文件不能超过5MB")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 20 * 1024 * 1024:
                raise ValueError("Excel 解压后内容过大")
        workbook = load_workbook(BytesIO(raw), read_only=True, data_only=False)
    except (BadZipFile, OSError, KeyError, ValueError, ParseError, InvalidFileException) as exc:
        raise ValueError("无法读取 Excel，请上传有效的 .xlsx 文件") from exc
    current = {rule["id"]: rule for rule in get_report_advice_rules(config)}
    updates, changes, errors, seen = {}, [], [], set()

    def accept(key, value, sheet, cell, description=None):
        location = f"{sheet}!{cell}"
        if key not in RULES:
            errors.append(f"{location}：未知策略编号 {key}")
            return
        if key in seen:
            errors.append(f"{location}：重复策略编号 {key}")
            return
        seen.add(key)
        try:
            phrases = normalize_phrases(value, RULES[key])
            if not phrases:
                raise ValueError("话术不能为空")
            if description is not None and (not isinstance(description, str) or len(description.strip()) > 500):
                raise ValueError("策略说明最多500字")
        except ValueError as exc:
            errors.append(f"{location}：{exc}")
            return
        before = current[key]["phrases"]
        merged = list(dict.fromkeys(before + phrases))
        if len(merged) > 100:
            errors.append(f"{location}：追加后超过100条话术")
            return
        updates[key] = {"phrases": merged}
        changes.append({"id": key, "condition": RULES[key]["condition"], "location": location,
                        "added_count": len(merged) - len(before), "phrases": merged})

    try:
        for sheet in workbook:
            if (sheet.max_row or 0) > 2000 or (sheet.max_column or 0) > 20:
                errors.append(f"{sheet.title}!A1：工作表超过2000行或20列")
                continue
            rows = list(islice(sheet.iter_rows(values_only=True), 2001))
            if len(rows) > 2000 or any(len(row) > 20 for row in rows):
                errors.append(f"{sheet.title}!A1：工作表超过2000行或20列")
                continue
            header = list(rows[0]) if rows else []
            if header[:3] == ["策略编号", "策略说明", "多行话术"]:
                for number, row in enumerate(rows[1:], 2):
                    values = list(row)
                    if not any(value is not None for value in values):
                        continue
                    key, description, phrases = (values + [None] * 3)[:3]
                    accept(key, phrases, sheet.title, f"C{number}", description)
                continue
            if sheet.title not in POLICY_ROWS:
                errors.append(f"{sheet.title}!A1：不支持的工作表或模板表头")
                continue
            section = ""
            for number, row in enumerate(rows, 1):
                values = list(row)
                condition = str(values[0] or "").strip() if values else ""
                if not condition:
                    if any(value for value in values[1:]):
                        errors.append(f"{sheet.title}!A{number}：缺少条件")
                    continue
                if condition == sheet.title or condition == "指标状态":
                    continue
                if condition[0].isdigit() and " " in condition:
                    section = condition
                    continue
                if condition.startswith("组合示例"):
                    section = "example"
                    continue
                if section == "example":
                    continue
                matched = [rule for rule in CATALOG if rule.get("source", {}).get("sheet") == sheet.title
                           and rule["source"]["section"] == section and rule["source"]["condition"] == condition]
                if not matched:
                    if condition in POLICY_ROWS[sheet.title] and "组合与冲突处理" in section:
                        continue
                    errors.append(f"{sheet.title}!A{number}：未知条件 {condition}")
                    continue
                for rule in matched:
                    column = rule["source"]["column"]
                    index = ord(column) - ord("A")
                    accept(rule["id"], values[index] if index < len(values) else None, sheet.title, f"{column}{number}")
        if not seen and not errors:
            errors.append("未找到可导入的策略话术")
    except (BadZipFile, KeyError, IndexError, ParseError, ValueError) as exc:
        raise ValueError("无法读取 Excel 内容，请检查文件是否损坏") from exc
    finally:
        workbook.close()
    return {"updates": updates if not errors else {}, "changes": changes, "errors": errors,
            "strategy_count": len(changes), "added_count": sum(change["added_count"] for change in changes)}


def advice_import_template(config):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "周报建议库"
    sheet.append(["策略编号", "策略说明", "多行话术", "触发条件（只读）", "可用占位符（只读）"])
    for rule in get_report_advice_rules(config):
        sheet.append([rule["id"], rule["description"], "\n".join(rule["phrases"]), rule["condition"], "、".join(rule["placeholders"]) or "无"])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="126448")
    for col, width in (("A", 45), ("B", 38), ("C", 70), ("D", 42), ("E", 24)):
        sheet.column_dimensions[col].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            # Configuration is literal text, including user-entered leading '='.
            cell.data_type = "s"
        sheet.row_dimensions[row[0].row].height = 45
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
