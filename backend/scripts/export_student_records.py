"""Export stored student meal, payment, and sport records from the backend DB."""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from app.services.runtime_config import get_effective_config
from app.services.student_record_exports import StudentRecordExportError, default_export_dates, export_timezone, write_student_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, help="开始日期（含当日），默认结束日期往前 13 天")
    parser.add_argument("--end", type=date.fromisoformat, help="结束日期（含当日），默认应用时区的今天")
    parser.add_argument("--output", type=Path, help="新建 XLSX 文件，省略时写到标准输出")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        config = get_effective_config(app.config)
        tz = export_timezone(config)
        _, today = default_export_dates(tz)
        end_date = args.end or today
        try:
            start_date = args.start or end_date - timedelta(days=13)
            if args.output:
                with args.output.open("xb") as output:
                    try:
                        counts = write_student_records(output, start_date, end_date, tz, config)
                    except Exception:
                        args.output.unlink()
                        raise
            else:
                counts = write_student_records(sys.stdout.buffer, start_date, end_date, tz, config)
        except (OverflowError, StudentRecordExportError) as exc:
            parser.error(str(exc))
    print(f"{start_date} 至 {end_date}（{tz}）｜" + "，".join(f"{name} {count} 条" for name, count in counts.items()), file=sys.stderr)


if __name__ == "__main__":
    main()
