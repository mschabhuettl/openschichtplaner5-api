"""Schedule PDF export router.

GET /api/schedule/pdf?group_id=X&year=YYYY&month=MM

Returns a print-optimized HTML page (A4 landscape) with the schedule table.
The browser can use Ctrl+P → Save as PDF to export.
Requires Planer role (Planer+).
"""

import calendar
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from ..dependencies import get_db, require_planer

router = APIRouter()

_MONTH_NAMES_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]

_WEEKDAY_SHORT_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _build_schedule_html(
    year: int,
    month: int,
    group_id: int | None,
    db,
) -> str:
    """Build a print-friendly HTML page with the schedule table."""
    num_days = calendar.monthrange(year, month)[1]
    month_name = _MONTH_NAMES_DE[month - 1]

    # Fetch schedule entries
    entries = db.get_schedule(year=year, month=month, group_id=group_id)

    # Fetch group name if group_id given
    group_name = ""
    if group_id is not None:
        groups = db.get_groups()
        for g in groups:
            if g.get("ID") == group_id:
                group_name = g.get("NAME", "")
                break

    # Build employee set from entries (maintain stable order)
    emp_ids_seen: list[int] = []
    emp_map: dict[int, dict] = {}
    for e in entries:
        eid = e.get("employee_id")
        if eid and eid not in emp_map:
            emp_ids_seen.append(eid)
            emp_map[eid] = {
                "name": e.get("employee_name", "") or e.get("display_name", "") or str(eid),
                "short": e.get("employee_short", "") or "",
            }

    # If no entries, try to get employees via group
    if not emp_ids_seen:
        all_emps = db.get_employees(include_hidden=False)
        if group_id is not None:
            members = db.get_group_members(group_id)
            member_set = set(members) if members else set()
            all_emps = [e for e in all_emps if e.get("ID") in member_set]
        for emp in all_emps:
            eid = emp.get("ID")
            if eid:
                emp_ids_seen.append(eid)
                emp_map[eid] = {
                    "name": f"{emp.get('NAME', '')}, {emp.get('FIRSTNAME', '')}".strip(", "),
                    "short": emp.get("SHORTNAME", ""),
                }

    # Sort employees by name
    emp_ids_seen.sort(key=lambda eid: emp_map[eid]["name"])

    # Build grid: emp_id → day → list of cell labels
    grid: dict[int, dict[int, list[str]]] = {
        eid: {d: [] for d in range(1, num_days + 1)}
        for eid in emp_ids_seen
    }

    for e in entries:
        eid = e.get("employee_id")
        date_str = e.get("date", "")
        if not eid or eid not in grid or len(date_str) < 10:
            continue
        try:
            day = int(date_str[8:10])
        except (ValueError, IndexError):
            continue
        if day < 1 or day > num_days:
            continue

        kind = e.get("kind", "")
        if kind == "absence":
            label = e.get("leave_short") or e.get("leave_name", "U")[:3]
        else:
            label = (
                e.get("display_name")
                or e.get("custom_short")
                or e.get("shift_short")
                or e.get("shift_name", "?")[:4]
            )
        if label:
            grid[eid][day].append(label)

    # Determine weekend/holiday columns
    today = date.today()
    today_day = today.day if (today.year == year and today.month == month) else -1

    day_meta: list[dict] = []
    for d in range(1, num_days + 1):
        wd = date(year, month, d).weekday()  # 0=Mon, 6=Sun
        day_meta.append({
            "day": d,
            "weekday": wd,
            "is_weekend": wd >= 5,
            "is_today": d == today_day,
            "short": _WEEKDAY_SHORT_DE[wd],
        })

    # Build HTML
    header = f"{month_name} {year}"
    if group_name:
        header += f" — {group_name}"

    # Company name from DB stats if available
    company_name = "OpenSchichtplaner5"
    try:
        stats = db.get_stats()
        if isinstance(stats, dict):
            company_name = stats.get("company_name", "") or company_name
    except Exception:
        pass

    rows_html = ""
    for eid in emp_ids_seen:
        emp = emp_map[eid]
        emp_label = emp["name"] or emp["short"] or str(eid)
        cells = ""
        for meta in day_meta:
            d = meta["day"]
            labels = grid[eid][d]
            cell_text = "<br>".join(labels) if labels else ""
            css_class = "cell"
            if meta["is_weekend"]:
                css_class += " weekend"
            if meta["is_today"]:
                css_class += " today"
            cells += f'<td class="{css_class}">{cell_text}</td>'
        rows_html += f"<tr><td class='emp-name'>{emp_label}</td>{cells}</tr>\n"

    # Day header row
    day_headers = ""
    for meta in day_meta:
        css = "day-header"
        if meta["is_weekend"]:
            css += " weekend"
        if meta["is_today"]:
            css += " today"
        day_headers += f'<th class="{css}">{meta["day"]}<br><span class="wd">{meta["short"]}</span></th>'

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dienstplan {header}</title>
<style>
  @page {{
    size: A4 landscape;
    margin: 10mm 8mm;
  }}
  * {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 7pt;
    color: #000;
    margin: 0;
    padding: 0;
    background: #fff;
  }}
  .page-header {{
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    margin-bottom: 4mm;
    padding-bottom: 2mm;
    border-bottom: 1.5px solid #333;
  }}
  .company-name {{
    font-size: 9pt;
    font-weight: bold;
    color: #1a1a2e;
  }}
  .plan-title {{
    font-size: 12pt;
    font-weight: bold;
    color: #1a1a2e;
    text-align: center;
    flex: 1;
  }}
  .print-date {{
    font-size: 7pt;
    color: #666;
    text-align: right;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }}
  th, td {{
    border: 0.5px solid #bbb;
    padding: 1px 2px;
    text-align: center;
    vertical-align: middle;
    overflow: hidden;
    word-break: break-word;
  }}
  th {{
    background-color: #1a1a2e;
    color: #fff;
    font-weight: bold;
    font-size: 6.5pt;
  }}
  th.weekend {{
    background-color: #3a4a6e;
  }}
  th.today {{
    background-color: #e65c00;
  }}
  td.emp-name {{
    text-align: left;
    font-weight: bold;
    padding-left: 3px;
    background-color: #f5f5f5;
    min-width: 28mm;
    max-width: 28mm;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  td.cell {{
    font-size: 6.5pt;
    min-width: 5.5mm;
    height: 5mm;
  }}
  td.weekend {{
    background-color: #f0f0f5;
    color: #555;
  }}
  td.today {{
    background-color: #fff3e0;
    font-weight: bold;
  }}
  tr:nth-child(even) td:not(.emp-name) {{
    background-color: #fafafa;
  }}
  tr:nth-child(even) td.weekend {{
    background-color: #eeeef5;
  }}
  tr:nth-child(even) td.today {{
    background-color: #fff0d8;
  }}
  .day-header .wd {{
    font-size: 5.5pt;
    font-weight: normal;
    opacity: 0.85;
  }}
  .legend {{
    margin-top: 3mm;
    font-size: 6.5pt;
    color: #555;
  }}
  .no-data {{
    text-align: center;
    padding: 15mm;
    color: #666;
    font-size: 10pt;
  }}
  @media screen {{
    body {{
      padding: 10px;
      font-size: 9pt;
      max-width: 1200px;
      margin: 0 auto;
    }}
    table {{
      font-size: 8pt;
    }}
    .print-btn {{
      display: inline-block;
      margin: 8px 0;
      padding: 8px 18px;
      background: #1a1a2e;
      color: #fff;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 9pt;
      text-decoration: none;
    }}
  }}
  @media print {{
    .print-btn {{ display: none !important; }}
  }}
</style>
</head>
<body>
<div class="page-header">
  <div class="company-name">{company_name}</div>
  <div class="plan-title">Dienstplan {header}</div>
  <div class="print-date">Stand: {today.strftime('%d.%m.%Y')}</div>
</div>
<div style="margin-bottom:4px">
  <a class="print-btn" href="javascript:window.print()">🖨️ Als PDF drucken</a>
</div>
"""

    if not emp_ids_seen:
        html += '<p class="no-data">Keine Mitarbeiter für diesen Zeitraum gefunden.</p>'
    else:
        html += f"""<table>
<thead>
<tr>
  <th style="text-align:left;min-width:28mm;max-width:28mm;">Mitarbeiter</th>
  {day_headers}
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
<div class="legend">
  <strong>Legende:</strong>
  Schichtkürzel laut Schichtdefinition &nbsp;|&nbsp;
  <span style="background:#f0f0f5;padding:0 3px;">Grau</span> = Wochenende &nbsp;|&nbsp;
  <span style="background:#fff3e0;padding:0 3px;">Orange</span> = Heute
</div>
"""

    html += """
</body>
</html>"""
    return html


@router.get(
    "/api/schedule/pdf",
    tags=["Export"],
    summary="Schedule PDF export (print view)",
    description=(
        "Returns a print-optimized HTML page with the monthly schedule table. "
        "Open in browser and use Ctrl+P → Save as PDF for PDF export. "
        "A4 landscape layout with embedded CSS. Requires Planer role."
    ),
    response_class=HTMLResponse,
)
def get_schedule_pdf(
    year: int = Query(..., description="Year (YYYY)", ge=2000, le=2100),
    month: int = Query(..., description="Month (1-12)", ge=1, le=12),
    group_id: int | None = Query(None, description="Filter by group ID"),
    _cur_user: dict = Depends(require_planer),
):
    """Return a print-optimized HTML schedule for the given month.

    The response is Content-Type: text/html. Open in a browser and
    use the browser's print function (Ctrl+P) to save as PDF.
    """
    if not (1 <= month <= 12):
        raise HTTPException(
            status_code=400, detail="Invalid month: must be between 1 and 12"
        )
    if not (2000 <= year <= 2100):
        raise HTTPException(
            status_code=400, detail="Invalid year: must be between 2000 and 2100"
        )

    db = get_db()

    # Validate group_id if given
    if group_id is not None:
        groups = db.get_groups()
        group_ids = {g.get("ID") for g in groups}
        if group_id not in group_ids:
            raise HTTPException(
                status_code=404, detail=f"Gruppe {group_id} nicht gefunden"
            )

    html = _build_schedule_html(year=year, month=month, group_id=group_id, db=db)
    return HTMLResponse(
        content=html,
        status_code=200,
        headers={
            "Content-Disposition": f'inline; filename="dienstplan-{year}-{month:02d}.html"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )
