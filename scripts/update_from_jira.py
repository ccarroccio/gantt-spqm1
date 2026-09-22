"""Fetch only SPQM-1 children, exact Jira estimates, and fully paginated worklogs."""

import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "gantt-data.json"


def jira_request(base_url, email, token, path, query=None):
    url = base_url.rstrip("/") + "/rest/api/2/" + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    basic = base64.b64encode(f"{email}:{token}".encode()).decode("ascii")
    for index, authorization in enumerate((f"Bearer {token}", f"Basic {basic}")):
        request = urllib.request.Request(
            url, headers={"Authorization": authorization, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 401 or index == 1:
                raise RuntimeError(f"Jira request failed: HTTP {error.code} at {url}") from error


def complete_worklogs(issue, request):
    worklog = issue["fields"]["worklog"]
    if worklog["startAt"] == 0 and len(worklog["worklogs"]) == worklog["total"]:
        return
    entries = []
    total = worklog["total"]
    while len(entries) < total:
        page = request(f"issue/{issue['key']}/worklog", {"startAt": len(entries), "maxResults": 100})
        if page["startAt"] != len(entries) or not page["worklogs"]:
            raise ValueError(f"Incomplete worklog pagination for {issue['key']}")
        total = page["total"]
        entries.extend(page["worklogs"])
    if len(entries) != total or len({w["id"] for w in entries}) != total:
        raise ValueError(f"Inconsistent worklogs for {issue['key']}")
    issue["fields"]["worklog"] = {"startAt": 0, "total": total, "worklogs": entries}


def update_from_jira(data, issues, base_url, now=None):
    if not issues:
        raise ValueError("Jira returned no SPQM-1 subtasks")
    sections = [
        {"name": "Backend", "owner": "D. Vivarelli", "tasks": []},
        {"name": "Frontend + governance", "owner": "M. Caiella", "tasks": []},
    ]
    existing_section = {
        task["ticket"]: section["name"]
        for section in data["sections"]
        for task in section["tasks"]
    }
    seen = set()
    for issue in issues:
        key, fields = issue["key"], issue["fields"]
        if key in seen or fields["parent"]["key"] != "SPQM-1":
            raise ValueError(f"Duplicate or unrelated issue: {key}")
        seen.add(key)
        worklog = fields["worklog"]
        entries = worklog["worklogs"]
        if worklog["startAt"] != 0 or len(entries) != worklog["total"]:
            raise ValueError(f"Incomplete worklogs for {key}")
        if len({w["id"] for w in entries}) != len(entries):
            raise ValueError(f"Duplicate worklogs for {key}")
        for entry in entries:
            datetime.fromisoformat(entry["started"].replace("Z", "+00:00"))
        spent = fields["timespent"] or 0
        if sum(w["timeSpentSeconds"] for w in entries) != spent:
            raise ValueError(f"Worklogs and time spent disagree for {key}")
        category = fields["status"]["statusCategory"]["key"]
        status_class = {"done": "done", "indeterminate": "prog", "new": "todo"}[category]
        estimated = fields["timeoriginalestimate"]
        remaining = fields["timeestimate"]
        task = {
            "ticket": key,
            "jira": base_url.rstrip("/") + "/browse/" + key,
            "task": fields["summary"],
            "owner": (fields["assignee"] or {}).get("displayName", "Unassigned"),
            "status": fields["status"]["name"],
            "statusClass": status_class,
            "estimatedHours": estimated / 3600 if estimated is not None else None,
            "loggedHours": spent / 3600,
            "remainingHours": remaining / 3600 if remaining is not None else None,
            "worklogs": [
                {field: entry[field] for field in ("id", "started", "timeSpentSeconds")}
                for entry in entries
            ],
        }
        section_name = existing_section.get(key)
        if section_name not in {"Backend", "Frontend + governance"}:
            section_name = "Backend" if task["task"].startswith("BE-") else "Frontend + governance"
        next(s for s in sections if s["name"] == section_name)["tasks"].append(task)
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo("Europe/Rome"))
    return {
        "meta": {
            "title": data["meta"]["title"],
            "today": local_now.strftime("%d/%m/%Y"),
            "timezone": "Europe/Rome",
            "lastSync": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "sections": sections,
    }


def main():
    base_url = os.environ["JIRA_BASE_URL"].strip()
    email = os.environ["JIRA_USER_EMAIL"].strip()
    token = os.environ["JIRA_API_TOKEN"].strip()
    if not base_url or not token:
        raise ValueError("JIRA_BASE_URL and JIRA_API_TOKEN must not be empty")

    def request(path, query=None):
        return jira_request(base_url, email, token, path, query)

    issues = []
    total = None
    while total is None or len(issues) < total:
        page = request("search", {
            "jql": "parent = SPQM-1 ORDER BY key ASC",
            "startAt": len(issues),
            "maxResults": 100,
            "fields": "summary,status,assignee,parent,timeoriginalestimate,timespent,timeestimate,worklog",
        })
        total = page["total"]
        if page["startAt"] != len(issues) or not page["issues"]:
            raise ValueError("Incomplete or empty Jira issue search")
        issues.extend(page["issues"])
    if len(issues) != total:
        raise ValueError("Jira issue count changed during pagination")
    for issue in issues:
        complete_worklogs(issue, request)
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    updated = update_from_jira(data, issues, base_url)
    DATA_FILE.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {len(issues)} actual SPQM-1 subtasks and their complete worklogs.")


if __name__ == "__main__":
    main()
