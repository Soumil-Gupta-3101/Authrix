import csv
from datetime import datetime
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

HEADERS = ["Roll number", "Name", "Class", "Subject", "Session date", "Attendance time", "Method", "Status", "Mode"]


def safe_cell(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


def rows_for_report(rows, timezone):
    zone = ZoneInfo(timezone)
    for row in rows:
        start = datetime.fromisoformat(row["started_at"]).astimezone(zone)
        recorded = datetime.fromisoformat(row["recorded_at"]).astimezone(zone) if row["recorded_at"] else None
        yield [safe_cell(x) for x in [row["roll"], row["name"], row["class_name"], row["subject"], start.date().isoformat(),
                                    recorded.strftime("%Y-%m-%d %H:%M:%S %Z") if recorded else "", row["method"], row["status"], row["mode"]]]


def export_csv(rows, timezone):
    out = StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(HEADERS)
    writer.writerows(rows_for_report(rows, timezone))
    return out.getvalue().encode("utf-8-sig")


def export_xlsx(rows, timezone):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    book = Workbook()
    sheet = book.active
    sheet.title = "Attendance"
    sheet.append(HEADERS)
    for row in rows_for_report(rows, timezone):
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="176B59")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip("ABCDEFGHI", [20, 28, 20, 25, 17, 30, 24, 14, 20]):
        sheet.column_dimensions[column].width = width
    out = BytesIO()
    book.save(out)
    return out.getvalue()
