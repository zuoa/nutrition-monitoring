import logging
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import ConsumptionRecord, ConsumptionSyncState, Student
from app.services.runtime_config import get_effective_config

logger = logging.getLogger(__name__)


def get_or_create_consumption_sync_state(source_system: str, *, lock: bool = False) -> ConsumptionSyncState:
    query = ConsumptionSyncState.query.filter_by(source_system=source_system)
    if lock:
        query = query.with_for_update()
    state = query.first()
    if state:
        return state

    state = ConsumptionSyncState(source_system=source_system)
    db.session.add(state)
    try:
        db.session.flush()
        return state
    except IntegrityError:
        # Another worker may have created the singleton state row after our
        # initial read. Recover and re-read it instead of failing the sync.
        db.session.rollback()
        query = ConsumptionSyncState.query.filter_by(source_system=source_system)
        if lock:
            query = query.with_for_update()
        state = query.first()
        if state:
            return state
        raise


class ZtkConsumptionSyncService:
    SOURCE_SYSTEM = "ztk_plus"

    def __init__(self, config=None, connection_factory=None):
        # Default to effective config so runtime overrides (runtime_config.json)
        # reach the Celery worker too, not just the Flask API process.
        self.config = config if config is not None else get_effective_config(current_app.config)
        self.connection_factory = connection_factory
        self._accounts_available: bool | None = None

    def sync_once(self, *, batch_id: str | None = None) -> dict:
        batch_id = batch_id or self._make_batch_id()
        state = self._get_or_create_state()
        source_cursor_time, source_cursor_id = self._resolve_initial_cursor(state)
        page_size = max(1, int(self.config.get("ZTK_SYNC_PAGE_SIZE") or 1000))
        max_rows = max(1, int(self.config.get("ZTK_SYNC_MAX_ROWS_PER_RUN") or 50000))

        imported = 0
        skipped_duplicates = 0
        total_rows = 0
        errors = []
        last_source_time = None
        last_source_record_id = None

        try:
            self._validate_config()
            conn = self._open_connection()
            try:
                cursor = conn.cursor(as_dict=True)
                while total_rows < max_rows:
                    fetch_limit = min(page_size, max_rows - total_rows)
                    rows = self._fetch_rows(cursor, source_cursor_time, source_cursor_id, fetch_limit)
                    if not rows:
                        break

                    total_rows += len(rows)
                    for row in rows:
                        try:
                            rec_id = _normalize_text(row.get("RecID"))
                            source_deal_time = row.get("DealTime")
                            if not rec_id or source_deal_time is None:
                                errors.append({"record_id": rec_id, "error": "RecID 或 DealTime 为空"})
                                continue

                            source_cursor_time = _as_source_naive_datetime(source_deal_time, self._timezone())
                            source_cursor_id = _to_int(rec_id)
                            last_source_time = source_cursor_time
                            last_source_record_id = rec_id

                            record = self._build_record(row, batch_id)
                            if self._find_existing_record(record.transaction_id, rec_id):
                                skipped_duplicates += 1
                                continue

                            self._link_student(record, row)
                            db.session.add(record)
                            imported += 1
                        except Exception as exc:
                            errors.append({
                                "record_id": _normalize_text(row.get("RecID")),
                                "error": str(exc),
                            })

                    if len(rows) < fetch_limit:
                        break
            finally:
                conn.close()

            now = datetime.now(timezone.utc)
            if last_source_time is not None:
                state.cursor_transaction_time = self._localize_source_datetime(last_source_time)
                state.cursor_source_record_id = str(last_source_record_id)
            state.last_batch_id = batch_id
            state.last_synced_at = now
            state.last_success_count = imported
            state.last_skipped_count = skipped_duplicates
            state.last_error_count = len(errors)
            state.last_error = errors[0]["error"] if errors else None
            state.updated_at = now
            db.session.commit()

            return {
                "source_system": self.SOURCE_SYSTEM,
                "batch_id": batch_id,
                "imported": imported,
                "skipped_duplicates": skipped_duplicates,
                "errors": errors[:50],
                "total_rows": total_rows,
                "max_rows_per_run": max_rows,
                "has_more": total_rows >= max_rows,
                "cursor_transaction_time": state.cursor_transaction_time.isoformat() if state.cursor_transaction_time else None,
                "cursor_source_record_id": state.cursor_source_record_id,
            }
        except Exception as exc:
            db.session.rollback()
            self._record_sync_failure(str(exc))
            logger.error("ZTK consumption sync failed: %s", exc, exc_info=True)
            raise

    def status(self) -> dict:
        state = ConsumptionSyncState.query.filter_by(source_system=self.SOURCE_SYSTEM).first()
        try:
            interval_minutes = max(1, int(self.config.get("ZTK_SYNC_INTERVAL_MINUTES") or 5))
        except (TypeError, ValueError):
            interval_minutes = 5
        return {
            "source_system": self.SOURCE_SYSTEM,
            "enabled": bool(self.config.get("ZTK_SYNC_ENABLED")),
            "sync_interval_minutes": interval_minutes,
            "configured": self._is_configured(),
            "state": state.to_dict() if state else None,
        }

    def test_connection(self) -> dict:
        """Connect to the ZTK SQL Server and probe the transaction (payment books) table.

        Only the single transaction table is synced, so only it is probed here.
        Always opens a real connection (ignores ``connection_factory``) so the
        test reflects the actual configured credentials/table. Returns a
        structured result; never raises — callers can serialize it directly.
        """
        try:
            self._validate_config()
        except RuntimeError as exc:
            return {
                "ok": False,
                "message": str(exc),
                "latency_ms": 0.0,
                "server_version": None,
                "tables": {"payment_books": False},
            }

        import pymssql

        payment_books_table = self._payment_books_table()
        server_version = None
        tables = {"payment_books": False}
        table_errors: list[str] = []

        try:
            start = time.monotonic()
            conn = pymssql.connect(**self._connection_kwargs())
        except Exception as exc:
            logger.warning("ZTK connection test failed: %s", exc)
            return {
                "ok": False,
                "message": f"连接失败: {exc}",
                "latency_ms": 0.0,
                "server_version": None,
                "tables": tables,
            }

        def _run(sql: str):
            # Fresh cursor per query so a failed statement can't poison the
            # remaining probes via a shared, error-state cursor.
            cur = conn.cursor()
            cur.execute(sql)
            return cur.fetchall()

        try:
            try:
                version_rows = _run("SELECT @@VERSION")
                first = version_rows[0] if version_rows else None
                server_version = _normalize_text(first[0]) if first and first[0] else None
            except Exception as exc:
                table_errors.append(f"获取版本失败: {exc}")

            try:
                _run(f"SELECT TOP 1 1 FROM {payment_books_table}")
                tables["payment_books"] = True
            except Exception as exc:
                tables["payment_books"] = False
                table_errors.append(f"{payment_books_table}: {exc}")
        finally:
            conn.close()

        latency_ms = round((time.monotonic() - start) * 1000.0, 1)
        if tables["payment_books"]:
            message = f"连接成功（{int(latency_ms)} ms）"
        else:
            message = f"连接成功，但交易表不可访问：{'；'.join(table_errors)}"
        return {
            "ok": True,
            "message": message,
            "latency_ms": latency_ms,
            "server_version": server_version,
            "tables": tables,
        }

    def _get_or_create_state(self) -> ConsumptionSyncState:
        return get_or_create_consumption_sync_state(self.SOURCE_SYSTEM, lock=True)

    def _resolve_initial_cursor(self, state: ConsumptionSyncState) -> tuple[datetime | None, int]:
        if not state.cursor_transaction_time:
            return None, 0

        cursor_time = _as_source_naive_datetime(state.cursor_transaction_time, self._timezone())
        lookback_minutes = max(0, int(self.config.get("ZTK_SYNC_LOOKBACK_MINUTES") or 0))
        if lookback_minutes > 0:
            return cursor_time - timedelta(minutes=lookback_minutes), 0
        return cursor_time, _to_int(state.cursor_source_record_id)

    def _validate_config(self) -> None:
        missing = [
            key for key in ("ZTK_DB_HOST", "ZTK_DB_NAME", "ZTK_DB_USER", "ZTK_DB_PASSWORD")
            if not _normalize_text(self.config.get(key))
        ]
        if missing:
            raise RuntimeError(f"一卡通数据库配置缺失: {', '.join(missing)}")
        self._payment_books_table()

    def _is_configured(self) -> bool:
        return all(
            _normalize_text(self.config.get(key))
            for key in ("ZTK_DB_HOST", "ZTK_DB_NAME", "ZTK_DB_USER", "ZTK_DB_PASSWORD")
        )

    def _open_connection(self):
        if self.connection_factory:
            return self.connection_factory()

        import pymssql

        return pymssql.connect(**self._connection_kwargs())

    def _connection_kwargs(self) -> dict:
        """Single source of truth for pymssql.connect kwargs.

        Used by both the production sync path (_open_connection) and
        test_connection so the admin "测试连接" probe exercises the same
        connection parameters that production uses — no drift.
        """
        try:
            port = int(self.config.get("ZTK_DB_PORT") or 1433)
        except (TypeError, ValueError):
            port = 1433
        return {
            "server": self.config.get("ZTK_DB_HOST"),
            "port": port,
            "user": self.config.get("ZTK_DB_USER"),
            "password": self.config.get("ZTK_DB_PASSWORD"),
            "database": self.config.get("ZTK_DB_NAME"),
            # The deployed Zhengyuan PLUS database runs SQL Server 2008 R2
            # RTM.  FreeTDS 1.4 fails its TLS handshake when negotiating the
            # newer TDS protocol versions, while TDS 7.0 connects successfully.
            # Keep encryption disabled for this legacy, internal-network
            # connection until the server is upgraded with TLS 1.2 support.
            "tds_version": "7.0",
            "encryption": "off",
            "login_timeout": 10,
            "timeout": 30,
            # Client charset UTF-8 works correctly for nvarchar, but Chinese
            # varchar columns (e.g. AccName, Remark) come back as mojibake over a
            # TDS 7.0 connection. We work around this by forcing UCS-2 transport
            # in the query with CONVERT(NVARCHAR(...), col) — see _execute_fetch_rows.
            "charset": "UTF-8",
        }

    def _fetch_rows(self, cursor, cursor_time: datetime | None, cursor_record_id: int, page_size: int) -> list[dict]:
        try:
            return self._execute_fetch_rows(
                cursor, cursor_time, cursor_record_id, page_size,
                include_accounts=self._accounts_available is not False,
            )
        except Exception as exc:
            if self._accounts_available is False:
                raise
            # Account enrichment is optional. A missing table, incompatible
            # schema, or missing SELECT permission must not stop consumption
            # ingestion; retry the same page using only the payment view.
            self._accounts_available = False
            logger.warning("ZTK accounts enrichment unavailable, continuing without it: %s", exc)
            return self._execute_fetch_rows(
                cursor, cursor_time, cursor_record_id, page_size,
                include_accounts=False,
            )

    def _execute_fetch_rows(
        self,
        cursor,
        cursor_time: datetime | None,
        cursor_record_id: int,
        page_size: int,
        *,
        include_accounts: bool,
    ) -> list[dict]:
        payment_books_table = self._payment_books_table()
        account_columns = ""
        account_join = ""
        if include_accounts:
            accounts_table = self._accounts_table()
            account_columns = """,
                account.AccNO AS AccountAccNO,
                account.CardNO AS AccountCardNO,
                CONVERT(NVARCHAR(255), account.AccName) AS AccountName,
                account.PerCode AS AccountPerCode,
                account.CertCode AS CertCode,
                account.PerSex AS AccountSex,
                account.DepCode AS AccountDepCode"""
            # CardCode can have historical account rows. Pick the newest AccNO
            # so one payment row never expands into duplicates.
            account_join = f"""
            OUTER APPLY (
                SELECT TOP 1
                    a.AccNO, a.CardNO, a.AccName, a.PerCode,
                    a.CertCode, a.PerSex, a.DepCode
                FROM {accounts_table} a WITH (NOLOCK)
                WHERE a.CardCode = p.CardCode
                ORDER BY a.AccNO DESC
            ) account"""
        select_sql = f"""
            SELECT TOP ({int(page_size)})
                p.RecID,
                p.PayAreaNum,
                p.StaNum,
                p.CardCode,
                p.AccAreaNum,
                p.AccNum,
                p.WriteFlag,
                p.FeeNum,
                p.EWalletNum,
                p.MonDeal,
                p.MonCard,
                p.MonDBCurr,
                p.FeeDeposit,
                p.FeeCard,
                p.FeeHandling,
                p.ConcessionsMon,
                p.DealerNum,
                p.DealTime,
                p.RecTime,
                p.OptNum,
                p.TerminalNum,
                p.TerminalSN,
                p.StaSID,
                p.TerSID,
                p.AccSID,
                p.SubsidySID,
                p.MonGather,
                p.ChgFlag,
                p.RecFlag,
                p.VouchCode,
                p.ChkFlag,
                p.DealPeriodNo,
                CONVERT(NVARCHAR(4000), p.Remark) AS Remark,
                p.CardType,
                p.BalFlag{account_columns}
            FROM {payment_books_table} p WITH (NOLOCK)
            {account_join}
            WHERE p.DealTime IS NOT NULL
        """
        params: tuple = ()
        if cursor_time is not None:
            select_sql += """
                AND (
                    p.DealTime > %s
                    OR (p.DealTime = %s AND p.RecID > %s)
                )
            """
            params = (cursor_time, cursor_time, int(cursor_record_id or 0))

        select_sql += " ORDER BY p.DealTime ASC, p.RecID ASC"
        cursor.execute(select_sql, params)
        if include_accounts:
            self._accounts_available = True
        return list(cursor.fetchall())

    def _payment_books_table(self) -> str:
        return _quote_table_name(
            self.config.get("ZTK_PAYMENT_BOOKS_TABLE") or "dbo.view_ac_paymentbooks",
            "ZTK_PAYMENT_BOOKS_TABLE",
        )

    def _accounts_table(self) -> str:
        return _quote_table_name(
            self.config.get("ZTK_ACCOUNTS_TABLE") or "dbo.ac_dict_Accounts",
            "ZTK_ACCOUNTS_TABLE",
        )

    def _build_record(self, row: dict, batch_id: str) -> ConsumptionRecord:
        rec_id = _normalize_text(row.get("RecID"))
        if not rec_id:
            raise ValueError("RecID 为空")

        deal_time = row.get("DealTime")
        if deal_time is None:
            raise ValueError("DealTime 为空")

        transaction_time = self._localize_source_datetime(deal_time)
        amount = _to_decimal(row.get("MonDeal"))
        # AccNum is the student's school number in the payment-books view.
        # CardCode is a separate card identifier and must not be stored as a
        # fallback student number.
        account_number = _normalize_text(row.get("AccNum"))
        card_code = _normalize_text(row.get("CardCode"))
        return ConsumptionRecord(
            student_no=account_number or None,
            card_code=card_code or None,
            student_name=_normalize_text(row.get("AccountName")) or None,
            transaction_time=transaction_time,
            amount=amount,
            transaction_id=f"ztk:PaymentBooks:{rec_id}",
            channel_id=self._resolve_channel_id(row),
            import_batch=batch_id,
            source_system=self.SOURCE_SYSTEM,
            source_record_id=rec_id,
            source_payload={key: _json_safe(value) for key, value in row.items()},
            source_synced_at=datetime.now(timezone.utc),
        )

    def _resolve_channel_id(self, row: dict) -> str | None:
        # The channel must line up with the camera (video) channel used in
        # matching. Cameras watch a checkout station, so build the channel from
        # PayAreaNum (收费区, e.g. 一食堂) + StaNum (结算台) as "收费区-结算台",
        # e.g. "1-3". StaNum alone is reused across areas, so the area prefix is
        # what makes it unique. A value of 0 means "unassigned" in the ZTK schema
        # and is treated as missing. Falls back to TerminalNum only when no
        # station can be resolved.
        area = _normalize_text(row.get("PayAreaNum"))
        station = _normalize_text(row.get("StaNum"))
        has_area = bool(area) and area != "0"
        has_station = bool(station) and station != "0"

        if has_station:
            return f"{area}-{station}" if has_area else station
        terminal = _normalize_text(row.get("TerminalNum"))
        if terminal and terminal != "0":
            return terminal
        return None

    def _find_existing_record(self, transaction_id: str, source_record_id: str) -> ConsumptionRecord | None:
        return ConsumptionRecord.query.filter(
            or_(
                ConsumptionRecord.transaction_id == transaction_id,
                (ConsumptionRecord.source_system == self.SOURCE_SYSTEM)
                & (ConsumptionRecord.source_record_id == source_record_id),
            )
        ).first()

    def _link_student(self, record: ConsumptionRecord, row: dict) -> None:
        account_number = _normalize_text(row.get("AccNum"))
        if not account_number:
            return

        student = Student.query.filter_by(student_no=account_number).first()
        if not student:
            return

        record.student_id = student.id
        if student.name:
            record.student_name = student.name

    def _localize_source_datetime(self, value) -> datetime:
        return _as_source_naive_datetime(value, self._timezone()).replace(tzinfo=self._timezone())

    def _timezone(self) -> ZoneInfo:
        timezone_name = str(
            self.config.get("VIDEO_TIMEZONE")
            or self.config.get("APP_TIMEZONE")
            or "Asia/Shanghai"
        ).strip() or "Asia/Shanghai"
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning("Unknown ZTK sync timezone=%s, fallback to Asia/Shanghai", timezone_name)
            return ZoneInfo("Asia/Shanghai")

    def _make_batch_id(self) -> str:
        return datetime.now(self._timezone()).strftime("ztk-%Y%m%d%H%M%S%f")[:-3]

    def _record_sync_failure(self, error: str) -> None:
        state = get_or_create_consumption_sync_state(self.SOURCE_SYSTEM)
        now = datetime.now(timezone.utc)
        state.last_synced_at = now
        state.last_error_count = 1
        state.last_error = error[:2000]
        state.updated_at = now
        db.session.commit()


def _normalize_text(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _quote_table_name(value: object, config_key: str) -> str:
    raw = _normalize_text(value)
    if not raw:
        raise RuntimeError(f"{config_key} 不能为空")

    parts = raw.split(".")
    if len(parts) > 3:
        raise RuntimeError(f"{config_key} 表名格式无效: {raw}")

    quoted_parts = []
    for part in parts:
        normalized = part.strip().strip("[]")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
            raise RuntimeError(f"{config_key} 表名格式无效: {raw}")
        quoted_parts.append(f"[{normalized}]")
    return ".".join(quoted_parts)


def _to_int(value) -> int:
    try:
        return int(str(value or "0").strip())
    except (TypeError, ValueError):
        return 0


def _to_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"无法解析金额: {value}") from exc


def _as_source_naive_datetime(value, tz: ZoneInfo) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"无法解析时间: {value}")
    if value.tzinfo is None:
        return value.replace(tzinfo=None)
    return value.astimezone(tz).replace(tzinfo=None)


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
