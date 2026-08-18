import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
OUT = ROOT / "data" / "gantt-data.json"

STATUS_MAP = {
    "fatto": "Fatto",
    "in corso": "In corso",
    "da completare": "Da completare",
}


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_hours(text: str):
    m = re.search(r"(\d+)h\s*/\s*(\d+)h", text)
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


def parse_date_range(text: str):
    m = re.search(r"(\d{2}/\d{2})\s*-\s*(\d{2}/\d{2})", text)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def parse_section_owner(title: str):
    # Example: "Backend ? D. Vivarelli"
    normalized = title.replace("?", "-")
    parts = [p.strip() for p in normalized.split("-", 1)]
    if len(parts) == 2:
        return parts[0], parts[1]
    return title, ""


def parse_header_sub(sub_html: str):
    text = clean(sub_html)
    # Example: Fine prevista: 16/10/2026 | BE: 09/10/2026 | FE: 08/10/2026 | Oggi: 18/08/2026
    out = {
        "deadline": None,
        "beDeadline": None,
        "feDeadline": None,
        "today": None,
    }
    m = re.search(r"Fine prevista:\s*(\d{2}/\d{2}/\d{4})", text)
    if m:
        out["deadline"] = m.group(1)
    m = re.search(r"BE:\s*(\d{2}/\d{2}/\d{4})", text)
    if m:
        out["beDeadline"] = m.group(1)
    m = re.search(r"FE:\s*(\d{2}/\d{2}/\d{4})", text)
    if m:
        out["feDeadline"] = m.group(1)
    m = re.search(r"Oggi:\s*(\d{2}/\d{2}/\d{4})", text)
    if m:
        out["today"] = m.group(1)
    return out


def main():
    html = INDEX.read_text(encoding="utf-8", errors="replace")

    h1 = re.search(r"<div class='h1'>(.*?)</div>", html, flags=re.S)
    sub = re.search(r"<div class='sub'>(.*?)</div>", html, flags=re.S)
    title = clean(h1.group(1) if h1 else "SPQM-1 - Query Manager per Service Desk")
    title = title.replace("?", "-")
    meta = parse_header_sub(sub.group(1) if sub else "")

    sections = []
    sec_blocks = re.findall(r"<div class='sec'>\s*<div class='sct'>(.*?)</div>\s*<table>(.*?)</table>\s*</div>", html, flags=re.S)

    for sec_title_raw, table_html in sec_blocks:
        sec_title = clean(sec_title_raw).replace("?", "-")
        sec_name, sec_owner = parse_section_owner(sec_title)
        tbody_match = re.search(r"<tbody>(.*?)</tbody>", table_html, flags=re.S)
        if not tbody_match:
            continue
        tbody = tbody_match.group(1)
        row_blocks = re.findall(r"<tr>(.*?)</tr>", tbody, flags=re.S)
        tasks = []
        for row in row_blocks:
            cells = re.findall(r"<td(?:\s+[^>]*)?>(.*?)</td>", row, flags=re.S)
            if len(cells) < 7:
                continue

            ticket_match = re.search(r">(SPQM-\d+)<", cells[0])
            jira_match = re.search(r"href='([^']+)'", cells[0])
            status_text = clean(cells[3])
            status_key = status_text.lower()
            status = STATUS_MAP.get(status_key, status_text)

            logged, estimated = parse_hours(clean(cells[4]))

            date_cell_text = clean(cells[5]).replace(" ? ", " ? ")
            date_parts = [p.strip() for p in date_cell_text.split("?") if p.strip()]
            planned_text = date_parts[0] if date_parts else ""
            actual_text = date_parts[1] if len(date_parts) > 1 else ""
            plan_start, plan_end = parse_date_range(planned_text)
            actual_start, actual_end = parse_date_range(actual_text)

            remaining_match = re.search(r"(\d+)h", clean(cells[6]))
            remaining = int(remaining_match.group(1)) if remaining_match else 0

            task = {
                "ticket": ticket_match.group(1) if ticket_match else "",
                "jira": jira_match.group(1) if jira_match else "",
                "task": clean(cells[1]),
                "owner": clean(cells[2]),
                "status": status,
                "estimatedHours": estimated,
                "loggedHours": logged,
                "remainingHours": remaining,
                "planStart": plan_start,
                "planEnd": plan_end,
                "actualStart": actual_start,
                "actualEnd": actual_end,
            }
            tasks.append(task)

        sections.append({
            "name": sec_name,
            "owner": sec_owner,
            "tasks": tasks,
        })

    data = {
        "meta": {
            "title": title,
            "deadline": meta["deadline"],
            "beDeadline": meta["beDeadline"],
            "feDeadline": meta["feDeadline"],
            "today": meta["today"],
            "timezone": "Europe/Rome",
            "autoRefreshMinutes": 10,
        },
        "sections": sections,
    }

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
