"""Update data in the checked-in HTML; its CSS and table layout are authoritative."""

import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "gantt-data.json"
INDEX_FILE = ROOT / "index.html"


def safe(value):
    return html.escape(str(value), quote=True)


def hours(value):
    return "-" if value is None else f"{value:g}h"


def task_list(data):
    return [task for section in data["sections"] for task in section["tasks"]]


def worklog_date(worklog):
    return datetime.fromisoformat(worklog["started"].replace("Z", "+00:00")).date()


def replace_one(pattern, replacement, text):
    result, count = re.subn(pattern, replacement, text, flags=re.S)
    if count != 1:
        raise ValueError(f"Expected one match, found {count}: {pattern}")
    return result


def render_existing(source, data):
    tasks = task_list(data)
    by_key = {task["ticket"]: task for task in tasks}
    if not tasks or len(by_key) != len(tasks):
        raise ValueError("Empty or duplicate Jira task list")
    today = datetime.strptime(data["meta"]["today"], "%d/%m/%Y").date()
    dates = [worklog_date(w) for task in tasks for w in task["worklogs"]]
    start = min([today] + dates)
    end = max([today] + dates)
    span = max(1, (end - start).days)

    def position(day):
        return (day - start).days * 100 / span

    ticks = []
    tick = start
    while tick <= end:
        ticks.append(f"<span style='left:{position(tick):.2f}%'>{tick:%d/%m}</span>")
        tick += timedelta(days=7)

    def update_row(row, task):
        cells = re.findall(r"<td\b[^>]*>.*?</td>", row, flags=re.S)
        if len(cells) != 8:
            raise ValueError(f"Unexpected table structure for {task['ticket']}")
        updated = cells.copy()
        updated[0] = replace_one(
            r"<a\b.*?</a>",
            lambda _: f"<a href='{safe(task['jira'])}' target='_blank'>{safe(task['ticket'])}</a>",
            cells[0],
        )
        updated[1] = (
            f"<td class='tname' title='{safe(task['task'])}'>{safe(task['task'])}</td>"
        )
        updated[2] = f"<td class='town'>{safe(task['owner'])}</td>"
        updated[3] = (
            f"<td><span class='badge {task['statusClass']}'>{safe(task['status'])}</span></td>"
        )
        estimated, logged = task["estimatedHours"], task["loggedHours"]
        if estimated is None:
            ratio, color = 0, "#8b949e"
        else:
            ratio = min(100, round(logged * 100 / estimated)) if estimated else (100 if logged else 0)
            color = "#f0883e" if logged > estimated else "#3fb950"
        updated[4] = replace_one(
            r"width:[\d.]+%;height:100%;background:#[0-9a-f]+",
            lambda _: f"width:{ratio}%;height:100%;background:{color}",
            cells[4],
        )
        updated[4] = replace_one(
            r"(<span\b[^>]*>).*?(</span>)",
            lambda m: m[1] + f"{hours(estimated)} / {hours(logged)}" + m[2],
            updated[4],
        )
        logged_dates = sorted(worklog_date(w) for w in task["worklogs"])
        if logged_dates:
            date_label = f"{logged_dates[0]:%d/%m}-{logged_dates[-1]:%d/%m}"
            date_span = f"<span style='font-size:10px;color:#79c0ff' title='date worklog Jira'>{date_label}</span>"
        else:
            date_span = "<span style='font-size:10px;color:#6e7681'>-</span>"
        updated[5] = f"<td class='tdate'>{date_span}</td>"
        remaining = task["remainingHours"]
        remaining_color = "#3fb950" if remaining == 0 else "#8b949e"
        remaining_label = "0h &#10003;" if remaining == 0 else hours(remaining)
        updated[6] = (
            f"<td style='font-size:11px;text-align:center;color:{remaining_color};"
            f"vertical-align:middle'>{remaining_label}</td>"
        )
        bars = "".join(
            f"<div class='wl-bar' style='left:{position(day):.2f}%;width:0.50%' "
            f"title='Worklog: {day:%d/%m}'></div>"
            for day in logged_dates
        )
        updated[7] = replace_one(
            r"(<div class='track'>).*?(\s*</div>\s*</td>)",
            lambda m: m[1] + f"<div class='today-inner' style='left:{position(today):.2f}%'></div>" + bars + m[2],
            cells[7],
        )
        iterator = iter(updated)
        return re.sub(r"<td\b[^>]*>.*?</td>", lambda _: next(iterator), row, flags=re.S)

    bodies = list(re.finditer(r"(<tbody>)(.*?)(</tbody>)", source, flags=re.S))
    if len(bodies) != 2:
        raise ValueError("Expected the existing Backend and Frontend tables")
    template = next(
        (row.group() for body in bodies if (row := re.search(r"<tr>.*?</tr>", body[2], re.S))),
        None,
    )
    if template is None:
        raise ValueError("No existing row available to preserve the layout")
    existing_keys = re.findall(r"/browse/(SPQM-\d+)'", source)
    if len(existing_keys) != len(set(existing_keys)):
        raise ValueError("Duplicate existing rows")
    additions = [task for task in tasks if task["ticket"] not in existing_keys]
    body_index = 0

    def update_body(match):
        nonlocal body_index
        index = body_index
        body_index += 1
        def existing_row(row_match):
            key = re.search(r"/browse/(SPQM-\d+)'", row_match[0]).group(1)
            return update_row(row_match[0], by_key[key]) if key in by_key else ""

        body = re.sub(r"<tr>.*?</tr>", existing_row, match[2], flags=re.S)
        for task in additions:
            target = 0 if task["task"].startswith("BE-") else 1
            if target == index:
                body += "\n" + update_row(template, task)
        return match[1] + body + match[3]

    result = re.sub(r"(<tbody>)(.*?)(</tbody>)", update_body, source, flags=re.S)
    result, tick_count = re.subn(
        r"(<div class='tick-header'>).*?(</div>)",
        lambda m: m[1] + "".join(ticks) + m[2],
        result,
        flags=re.S,
    )
    if tick_count != 2:
        raise ValueError("Expected two timeline headers")
    counts = Counter(task["statusClass"] for task in tasks)
    for css, status in (("ok", "done"), ("pr", "prog"), ("td", "todo")):
        result = replace_one(
            rf"(<b class='{css}'>)\d+(</b>)",
            lambda m: m[1] + str(counts[status]) + m[2],
            result,
        )
    result = replace_one(
        r"(<b style='color:#f0883e'>)\d+(</b>Totale)",
        lambda m: m[1] + str(len(tasks)) + m[2],
        result,
    )
    result = replace_one(
        r"(Aggiornato al: )\d{2}/\d{2}/\d{4}",
        lambda m: m[1] + data["meta"]["today"],
        result,
    )
    if re.search(r"<style>.*?</style>", source, re.S)[0] != re.search(r"<style>.*?</style>", result, re.S)[0]:
        raise ValueError("Stylesheet changed")
    if set(re.findall(r"/browse/(SPQM-\d+)'", result)) != set(by_key):
        raise ValueError("Rendered rows do not match Jira")
    return result


def main():
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    source = INDEX_FILE.read_bytes().decode("utf-8")
    output = render_existing(source, data)
    INDEX_FILE.write_bytes(output.encode("utf-8"))
    print(f"Updated {len(task_list(data))} rows without replacing the existing layout.")


if __name__ == "__main__":
    main()
