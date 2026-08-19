import html
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "gantt-data.json"
INDEX_FILE = ROOT / "index.html"

CSS = """
*{box-sizing:border-box;margin:0;padding:0}body{font-family:Segoe UI,Arial,sans-serif;background:#0d1117;color:#e6edf3;font-size:13px}a{color:#58a6ff;text-decoration:none}.head{padding:14px 20px;background:#161b22;border-bottom:1px solid #30363d}.h1{font-size:18px;font-weight:700}.sub{font-size:11px;color:#8b949e;margin-top:3px}.kpi{display:flex;gap:10px;padding:10px 20px;background:#161b22;border-bottom:1px solid #30363d;align-items:center;flex-wrap:wrap}.card{background:#21262d;padding:8px 14px;border-radius:8px;min-width:100px;text-align:center}.card b{font-size:20px;display:block}.ok{color:#3fb950}.pr{color:#388bfd}.td{color:#8b949e}.main{padding:14px 20px}.sec{margin-bottom:22px}.sct{font-size:12px;font-weight:600;color:#8b949e;margin:8px 0 4px;text-transform:uppercase;letter-spacing:.5px}.legend{font-size:10px;color:#8b949e;margin-bottom:8px;display:flex;gap:14px;flex-wrap:wrap;align-items:center}.ldot{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:middle;margin-right:3px}table{width:100%;border-collapse:collapse}th,td{border-bottom:1px solid #21262d;padding:5px 7px;text-align:left;white-space:nowrap;vertical-align:middle}th{color:#8b949e;background:#161b22;font-size:11px;font-weight:500}.tname{max-width:240px;overflow:hidden;text-overflow:ellipsis}.town{max-width:120px}.tdate{font-size:10px;color:#8b949e}.badge{padding:2px 7px;border-radius:10px;font-size:10px}.badge.done{background:#163621;color:#3fb950}.badge.prog{background:#122f53;color:#58a6ff}.badge.todo{background:#2f343d;color:#8b949e}.barcell{min-width:420px;padding:0 4px}.track{position:relative;height:28px;background:#161b22;border-radius:3px}.wl-bar{position:absolute;top:16px;height:6px;border-radius:3px;background:#79c0ff;opacity:.9}.today-inner{position:absolute;top:-3px;bottom:-3px;width:2px;background:#f85149;z-index:5;border-radius:1px}.tick-header{position:relative;height:16px;min-width:420px;margin:0 4px 2px}.tick-header span{position:absolute;font-size:9px;color:#484f58;transform:translateX(-50%);white-space:nowrap}
"""


def date_value(value, year):
    if not value:
        return None
    for fmt in ("%d/%m", "%d/%m/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(year=year)
        except ValueError:
            pass
    return None


def date_text(value):
    return value or "-"


def percent(value, start, end):
    if value is None:
        return 0
    return max(0, min(100, (value - start).total_seconds() * 100 / (end - start).total_seconds()))


def safe(value):
    return html.escape(str(value or ""), quote=True)


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    sections = data.get("sections", [])
    tasks = [task for section in sections for task in section.get("tasks", [])]
    year = int((meta.get("deadline") or "2026")[-4:])
    today = date_value(meta.get("today"), year) or datetime.now()
    dates = [today]
    for task in tasks:
        for key in ("actualStart", "actualEnd"):
            value = date_value(task.get(key), year)
            if value:
                dates.append(value)
    start = min(dates) - timedelta(days=2)
    end = max(dates) + timedelta(days=2)
    ticks = []
    tick = start
    while tick <= end:
        ticks.append(tick)
        tick += timedelta(days=7)

    counts = {"Fatto": 0, "In corso": 0, "Da completare": 0}
    remaining_total = 0
    for task in tasks:
        status = task.get("status", "Da completare")
        counts[status] = counts.get(status, 0) + 1
        remaining_total += int(task.get("remainingHours") or 0)

    def tick_markup():
        return "".join(f"<span style='left:{percent(tick,start,end):.2f}%'>{tick:%d/%m}</span>" for tick in ticks)

    def row(task):
        actual_start = date_value(task.get("actualStart"), year)
        actual_end = date_value(task.get("actualEnd"), year)
        worklog = "-"
        bar = ""
        if actual_start and actual_end:
            worklog = f"{task['actualStart']}-{task['actualEnd']}"
            left = percent(actual_start, start, end)
            width = max(.5, percent(actual_end, start, end) - left)
            bar = f"<div class='wl-bar' style='left:{left:.2f}%;width:{width:.2f}%' title='Worklog: {safe(worklog)}'></div>"
        status = task.get("status", "Da completare")
        status_class = {"Fatto": "done", "In corso": "prog"}.get(status, "todo")
        remaining = int(task.get("remainingHours") or 0)
        remaining_label = "0h ✓" if remaining == 0 else f"{remaining}h"
        remaining_color = "#3fb950" if remaining == 0 else "#8b949e"
        logged = int(task.get("loggedHours") or 0)
        estimated = int(task.get("estimatedHours") or 0)
        ratio = min(100, round(logged * 100 / estimated)) if estimated else 0
        return f"""<tr><td><a href='{safe(task.get('jira'))}' target='_blank' rel='noopener'>{safe(task.get('ticket'))}</a></td><td class='tname' title='{safe(task.get('task'))}'>{safe(task.get('task'))}</td><td class='town'>{safe(task.get('owner'))}</td><td><span class='badge {status_class}'>{safe(status)}</span></td><td><div style='display:flex;align-items:center;gap:6px;min-width:130px'><div style='flex:1;background:#21262d;border-radius:3px;height:7px;min-width:60px'><div style='width:{ratio}%;height:100%;background:#3fb950;border-radius:3px'></div></div><span style='font-size:10px;white-space:nowrap'>{logged}h / {estimated}h</span></div></td><td class='tdate'><span style='font-size:10px;color:#388bfd' title='date reali worklog'>{worklog}</span></td><td style='font-size:11px;text-align:center;color:{remaining_color};vertical-align:middle'>{remaining_label}</td><td class='barcell'><div class='track'><div class='today-inner' style='left:{percent(today,start,end):.2f}%'></div>{bar}</div></td></tr>"""

    sections_html = []
    for section in sections:
        title = f"{section.get('name', '').replace('?', '-')} - {section.get('owner', '')}".strip(" -")
        rows = "".join(row(task) for task in section.get("tasks", []))
        sections_html.append(f"<div class='sec'><div class='sct'>{safe(title)}</div><table><thead><tr><th>Ticket</th><th>Task</th><th>Owner</th><th>Stato</th><th>Stimato vs Registrato</th><th>Date reali</th><th>Rimanente</th><th><div class='tick-header'>{tick_markup()}</div></th></tr></thead><tbody>{rows}</tbody></table></div>")

    title = safe(meta.get("title", "SPQM-1 - Query Manager per Service Desk").replace("?", "-"))
    output = f"""<!doctype html><html lang='it'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>{CSS}</style></head><body><div class='head'><div class='h1'>{title}</div><div class='sub'>Fine prevista: <strong>{safe(meta.get('deadline'))}</strong> &nbsp;|&nbsp; BE: {safe(meta.get('beDeadline'))} &nbsp;|&nbsp; FE: {safe(meta.get('feDeadline'))} &nbsp;|&nbsp; Oggi: {safe(meta.get('today'))}</div></div><div class='kpi'><div class='card'><b class='ok'>{counts.get('Fatto',0)}</b>Fatto</div><div class='card'><b class='pr'>{counts.get('In corso',0)}</b>In corso</div><div class='card'><b class='td'>{counts.get('Da completare',0)}</b>Da completare</div><div class='card'><b style='color:#f0883e'>{len(tasks)}</b>Totale</div><div class='card'><b style='color:#f85149'>{remaining_total}h</b>Rimanente Totale</div></div><div class='main'><div class='legend'><span><span class='ldot' style='background:#79c0ff;height:6px'></span>Worklog reale</span><span><span class='ldot' style='background:#f85149;width:2px;border-radius:0'></span>Oggi</span><span>Ore rimanenti aggiornate da Jira</span></div>{''.join(sections_html)}</div></body></html>"""
    INDEX_FILE.write_text(output, encoding="utf-8")
    print(f"Rendered {len(tasks)} tasks and {remaining_total} remaining hours.")


if __name__ == "__main__":
    main()
