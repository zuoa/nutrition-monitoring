"""Read stored student meal, payment, and sport events into an Excel workbook."""

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func, or_, select

from app import db
from app.models import (
    CapturedImage, Class, ConsumptionRecord, Dish, DishRecognition, MatchResult, MatchStatusEnum, SportRecord, Student,
)
from app.models.menu import get_meal_slot_map, resolve_meal_slot_for_datetime
from app.services.sport_ingestion_service import SPORT_NAMES


PERSON_COLUMNS = (("学号", 18), ("姓名", 14), ("班级", 20))
JSON_CHUNK_SIZE = 16000  # Fits in one Excel cell even with supplementary Unicode characters.


class StudentRecordExportError(ValueError):
    pass


def export_timezone(config):
    try:
        return ZoneInfo(config.get("APP_TIMEZONE") or "Asia/Shanghai")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("Asia/Shanghai")


def default_export_dates(tz):
    today = datetime.now(tz).date()
    return today - timedelta(days=13), today


def _person_columns(source_no, source_name=None):
    number = func.coalesce(Student.student_no, func.nullif(source_no, ""))
    name = Student.name if source_name is None else func.coalesce(Student.name, source_name)
    return number.label("student_no"), name, func.coalesce(Class.name, Student.class_name)


def _student_link(student_id):
    return or_(Student.id == student_id, and_(
        student_id.is_(None), Student.student_no == func.nullif(ConsumptionRecord.student_no, ""),
    ))


def _meal_query(start, stop):
    transaction = ConsumptionRecord
    match = MatchResult
    recognition = DishRecognition
    moment = func.coalesce(transaction.transaction_time, match.captured_at, CapturedImage.captured_at)
    person = _person_columns(transaction.student_no, transaction.student_name)
    return (
        select(*person, moment, Dish.name, recognition.dish_name_raw, recognition.confidence,
               recognition.is_low_confidence, recognition.is_manual, match.status, match.id,
               match.image_id, recognition.id, recognition.dish_id, transaction.id)
        .select_from(match)
        .join(CapturedImage, CapturedImage.id == match.image_id)
        .join(recognition, recognition.image_id == match.image_id)
        .outerjoin(transaction, transaction.id == match.consumption_record_id)
        .outerjoin(Student, _student_link(func.coalesce(match.student_id, transaction.student_id)))
        .outerjoin(Class, Class.id == Student.class_id)
        .outerjoin(Dish, Dish.id == recognition.dish_id)
        .where(match.status.in_((MatchStatusEnum.matched, MatchStatusEnum.confirmed)),
               moment >= start, moment < stop)
        .order_by(person[0].nulls_last(), moment, match.id, recognition.id)
    )


def _payment_query(start, stop):
    record = ConsumptionRecord
    person = _person_columns(record.student_no, record.student_name)
    return (
        select(*person, record.transaction_time, record.amount, record.channel_id, record.transaction_id,
               record.id, record.source_system, record.source_record_id, record.student_no)
        .select_from(record)
        .outerjoin(Student, _student_link(record.student_id))
        .outerjoin(Class, Class.id == Student.class_id)
        .where(record.transaction_time >= start, record.transaction_time < stop)
        .order_by(person[0].nulls_last(), record.transaction_time, record.id)
    )


def _sport_query(start, stop):
    record = SportRecord
    person = _person_columns(record.person_id)
    return (
        select(*person, record.start_time, record.sport_type, record.mode, record.score,
               record.score_unit, record.all_time, record.school_id, record.person_id, record.id, record.raw_result)
        .select_from(record)
        .outerjoin(Student, Student.student_no == func.nullif(record.person_id, ""))
        .outerjoin(Class, Class.id == Student.class_id)
        .where(record.start_time >= int(start.timestamp() * 1000), record.start_time < int(stop.timestamp() * 1000))
        .order_by(person[0].nulls_last(), record.start_time, record.id)
    )


def _local_time(moment, tz):
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz)


def _append(sheet, values):
    cells = []
    for value in values:
        if isinstance(value, str):
            cell = WriteOnlyCell(sheet, value=value)
            cell.data_type = "s"  # Preserve leading zeros and store formula-like input as text.
            value = cell
        cells.append(value)
    sheet.append(cells)


def _write_sheet(workbook, name, columns, rows):
    sheet = workbook.create_sheet(name)
    sheet.freeze_panes = "D2"
    header = []
    for index, (label, width) in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
        cell = WriteOnlyCell(sheet, value=label)
        cell.fill = PatternFill("solid", fgColor="174B50")
        cell.font = Font(bold=True, color="FFFFFF")
        header.append(cell)
    sheet.append(header)
    count = 0
    for count, row in enumerate(rows, 1):
        if count >= 1_048_576:
            raise StudentRecordExportError(f"{name}超过 Excel 行数上限，请缩短日期范围")
        _append(sheet, row)
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{count + 1}"
    sheet.close()
    return count


def _populate_workbook(workbook, connection, start, stop, tz, config, student_no):
    slots = get_meal_slot_map(config)

    def records(query):
        if student_no:
            query = query.where(query.selected_columns.student_no == student_no)
        return connection.execute(query)

    def meals():
        for row in records(_meal_query(start, stop)):
            number, name, class_name, moment, dish_name, raw_name, confidence, low_confidence, manual, status, *ids = row
            local = _local_time(moment, tz)
            slot = resolve_meal_slot_for_datetime(local, str(tz), config)
            yield (number, name, class_name, local.isoformat(sep=" "), slots.get(slot, {}).get("label"),
                   dish_name, raw_name, confidence, low_confidence, manual, status.value, *ids)

    def payments():
        for number, name, class_name, moment, *values in records(_payment_query(start, stop)):
            yield (number, name, class_name, _local_time(moment, tz).isoformat(sep=" "), *values)

    sport_overflow = []

    def sports():
        for number, name, class_name, epoch, sport_type, mode, *values, record_id, raw in records(_sport_query(start, stop)):
            local = datetime.fromtimestamp(epoch / 1000, tz)
            payload = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
            if len(payload) > JSON_CHUNK_SIZE:
                for offset in range(0, len(payload), JSON_CHUNK_SIZE):
                    sport_overflow.append((record_id, offset // JSON_CHUNK_SIZE + 1, payload[offset:offset + JSON_CHUNK_SIZE]))
                payload = "见运动指标续页"
            yield (number, name, class_name, local.isoformat(sep=" "), SPORT_NAMES.get(sport_type, f"项目 {sport_type}"),
                   {1: "练习", 2: "测试"}.get(mode, str(mode)), *values, record_id, sport_type, mode, epoch, payload)

    counts = {}
    counts["识别菜品"] = _write_sheet(workbook, "识别菜品", PERSON_COLUMNS + (
        ("就餐时间", 32), ("餐次", 12), ("菜品档案名称", 24), ("原始识别名称", 24), ("置信度", 12), ("低置信度", 13),
        ("人工标注", 13), ("匹配状态", 15), ("匹配ID", 12), ("餐盘图片ID", 14), ("识别ID", 12), ("菜品ID", 12), ("流水ID", 12),
    ), meals())
    counts["消费流水"] = _write_sheet(workbook, "消费流水", PERSON_COLUMNS + (
        ("刷卡时间", 32), ("原始金额", 13), ("通道", 14), ("交易号", 24), ("流水ID", 12),
        ("来源系统", 16), ("来源记录号", 24), ("原始学号", 18),
    ), payments())
    counts["运动记录"] = _write_sheet(workbook, "运动记录", PERSON_COLUMNS + (
        ("运动时间", 32), ("运动项目", 18), ("模式", 12), ("成绩", 12), ("单位", 12), ("用时（秒）", 14), ("设备学校编号", 18),
        ("设备人员编号", 18), ("记录ID", 12), ("项目编号", 12), ("模式编号", 12), ("原始时间戳（毫秒）", 24), ("设备原始指标JSON", 52),
    ), sports())
    if sport_overflow:
        _write_sheet(workbook, "运动指标续页", (("记录ID", 12), ("分段序号", 12), ("设备原始指标JSON", 70)), sport_overflow)
    return counts


def write_student_records(output, start_date, end_date, tz, config, *, student_no=None):
    if start_date > end_date:
        raise StudentRecordExportError("开始日期不能晚于结束日期")
    if end_date == date.max:
        raise StudentRecordExportError("结束日期超出可导出范围")
    start = datetime.combine(start_date, time.min, tz)
    stop = datetime.combine(end_date + timedelta(days=1), time.min, tz)
    workbook = Workbook(write_only=True)
    try:
        with db.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            counts = _populate_workbook(workbook, connection.execution_options(yield_per=1000), start, stop, tz, config, student_no)
        workbook.save(output)
    except Exception:
        # Release the write-only worksheets' working files even when a query or the export fails.
        for sheet in workbook.worksheets:
            writer = sheet._writer
            if writer and Path(writer.out).exists():
                if not sheet.closed:
                    sheet.close()
                writer.cleanup()
        raise
    return counts
