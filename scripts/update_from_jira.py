import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "gantt-data.json"

STATUS_DONE = {"done", "fatto", "closed", "resolved"}
STATUS_PROGRESS = {"in progress", "in corso"}


def normalize_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in STATUS_DONE:
        return "Fatto"
    if s in STATUS_PROGRESS:
        return "In corso"
    return "Da completare"


def parse_iso_to_ddmm(value: str):
    if not value:
        return None
    try:
        # Jira can return both date and datetime formats
        if "T" in value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.strftime("%d/%m")
    except Exception:
        return None


def jira_request(base_url: str, email: str, token: str, jql: str, start_at: int):
    query = {
        "jql": jql,
        "startAt": start_at,
        "maxResults": 100,
        "fields": "summary,status,assignee,timeoriginalestimate,timespent,aggregatetimeestimate,worklog",
    }

    # Optional custom date fields can be requested by name, if provided.
    start_field = os.getenv("JIRA_PLAN_START_FIELD", "").strip()
    end_field = os.getenv("JIRA_PLAN_END_FIELD", "").strip()
    if start_field:
        query["fields"] += f",{start_field}"
    if end_field:
        query["fields"] += f",{end_field}"

    url = f"{base_url.rstrip('/')}/rest/api/2/search?{urllib.parse.urlencode(query)}"
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"Jira API HTTP {exc.code} at {base_url.rstrip('/')}. "
            "Check JIRA_BASE_URL, JIRA_USER_EMAIL and JIRA_API_TOKEN. "
            f"Response: {detail}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"Jira API is unreachable at {base_url.rstrip('/')}: {exc.reason}"
        ) from exc
    return payload


def load_data():
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Missing {DATA_FILE}")
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_existing_task_index(data):
    index = {}
    for section in data.get("sections", []):
        for t in section.get("tasks", []):
            if t.get("ticket"):
                index[t["ticket"]] = t
    return index


def find_section_for_task(summary: str):
    s = (summary or "").upper()
    if s.startswith("BE-"):
        return "Backend"
    if s.startswith("FE-"):
        return "Frontend + governance"
    return "Altro"


def ensure_section(data, name):
    for section in data.get("sections", []):
        if section.get("name") == name:
            return section
    section = {"name": name, "owner": "", "tasks": []}
    data.setdefault("sections", []).append(section)
    return section


def update_from_jira(data, issues):
    existing = collect_existing_task_index(data)
    seen = set()

    plan_start_field = os.getenv("JIRA_PLAN_START_FIELD", "").strip()
    plan_end_field = os.getenv("JIRA_PLAN_END_FIELD", "").strip()

    for issue in issues:
        key = issue.get("key")
        fields = issue.get("fields", {})
        if not key:
            continue

        seen.add(key)
        task = existing.get(key, {
            "ticket": key,
            "jira": "",
            "task": "",
            "owner": "",
            "status": "Da completare",
            "estimatedHours": 0,
            "loggedHours": 0,
            "remainingHours": 0,
            "planStart": None,
            "planEnd": None,
            "actualStart": None,
            "actualEnd": None,
        })

        task["ticket"] = key
        task["jira"] = f"{os.getenv('JIRA_BASE_URL','').rstrip('/')}/browse/{key}"
        task["task"] = fields.get("summary") or task.get("task")
        assignee = fields.get("assignee") or {}
        task["owner"] = assignee.get("displayName") or task.get("owner") or ""
        task["status"] = normalize_status((fields.get("status") or {}).get("name"))

        est_sec = fields.get("timeoriginalestimate") or 0
        spent_sec = fields.get("timespent") or 0
        rem_sec = fields.get("aggregatetimeestimate")
        if rem_sec is None:
            rem_sec = max(est_sec - spent_sec, 0)

        task["estimatedHours"] = int(round(est_sec / 3600))
        task["loggedHours"] = int(round(spent_sec / 3600))
        task["remainingHours"] = int(round(rem_sec / 3600))

        if plan_start_field and fields.get(plan_start_field):
            task["planStart"] = parse_iso_to_ddmm(fields.get(plan_start_field)) or task.get("planStart")
        if plan_end_field and fields.get(plan_end_field):
            task["planEnd"] = parse_iso_to_ddmm(fields.get(plan_end_field)) or task.get("planEnd")

        worklogs = ((fields.get("worklog") or {}).get("worklogs") or [])
        wl_dates = []
        for w in worklogs:
            started = w.get("started")
            if started:
                d = parse_iso_to_ddmm(started)
                if d:
                    wl_dates.append(d)

        if wl_dates:
            # dd/mm sorted by month/day only; good enough for same-year board
            parsed = [datetime.strptime(x, "%d/%m") for x in wl_dates]
            task["actualStart"] = min(parsed).strftime("%d/%m")
            task["actualEnd"] = max(parsed).strftime("%d/%m")

        if key not in existing:
            section_name = find_section_for_task(task.get("task", ""))
            ensure_section(data, section_name)["tasks"].append(task)

    data.setdefault("meta", {})["today"] = datetime.now().strftime("%d/%m/%Y")
    data["meta"]["lastSync"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Keep existing tasks not returned by JQL; this avoids accidental drops.
    return data


def main():
    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    email = os.getenv("JIRA_USER_EMAIL", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()
    jql = os.getenv("JIRA_JQL", "").strip() or "project = SPQM ORDER BY key ASC"

    data = load_data()

    if not (base_url and email and token):
        missing = [
            name for name, value in {
                "JIRA_BASE_URL": base_url,
                "JIRA_USER_EMAIL": email,
                "JIRA_API_TOKEN": token,
            }.items() if not value
        ]
        raise RuntimeError(f"Missing required Jira configuration: {', '.join(missing)}")

    issues = []
    start_at = 0
    total = None

    while total is None or start_at < total:
        payload = jira_request(base_url, email, token, jql, start_at)
        batch = payload.get("issues", [])
        total = int(payload.get("total", 0))
        issues.extend(batch)
        start_at += len(batch)
        if not batch:
            break

    updated = update_from_jira(data, issues)
    save_data(updated)
    print(f"Updated {len(issues)} issues from Jira.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
