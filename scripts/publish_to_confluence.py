"""Publish native KPI tables and an iframe, never the Gantt HTML or attachments."""

import base64
import json
import os
import urllib.request
from collections import Counter
from urllib.error import HTTPError, URLError
from pathlib import Path

from render_gantt import hours, safe, task_list

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "gantt-data.json"


def request_json(url, email, token, method="GET", payload=None, operation="request"):
    auth = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    errors = []
    for authorization in (f"Bearer {token}", f"Basic {auth}"):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", authorization)
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Atlassian-Token", "no-check")
        req.add_header("User-Agent", "gantt-spqm1-github-actions")
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                content = response.read().decode("utf-8")
                try:
                    return json.loads(content) if content else {}
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Confluence returned non-JSON HTTP {response.status}: "
                        f"{content[:300]}"
                    ) from exc
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            errors.append(f"HTTP {exc.code}: {detail}")
            if exc.code != 401:
                break
        except URLError as exc:
            raise RuntimeError(f"Confluence API is unreachable: {exc.reason}") from exc
    raise RuntimeError(
        f"Confluence {operation} failed at {url}. "
        "Check CONFLUENCE_BASE_URL, CONFLUENCE_USER_EMAIL, "
        f"CONFLUENCE_API_TOKEN and page permissions. {' | '.join(errors)}"
    )


def page_content(data):
    tasks = task_list(data)
    counts = Counter(task["statusClass"] for task in tasks)

    def total(field):
        values = [task[field] for task in tasks]
        if any(value is None for value in values):
            return "-"
        return hours(sum(values))

    return (
        "<p><strong>Aggiornamento automatico:</strong> dati Jira aggiornati al "
        + safe(data["meta"]["today"])
        + ", basati esclusivamente sui worklog registrati.</p>"
        "<h2>KPI attivit&#224;</h2><table><tbody><tr><th>Fatto</th><th>In corso</th>"
        "<th>Da completare</th><th>Totale</th></tr><tr>"
        f"<td>{counts['done']}</td><td>{counts['prog']}</td><td>{counts['todo']}</td>"
        f"<td>{len(tasks)}</td></tr></tbody></table>"
        "<h2>KPI ore</h2><table><tbody><tr><th>Stimato</th><th>Registrato</th>"
        "<th>Rimanente</th></tr><tr>"
        f"<td>{total('estimatedHours')}</td><td>{total('loggedHours')}</td>"
        f"<td>{total('remainingHours')}</td></tr></tbody></table>"
        '<p><a href="https://ccarroccio.github.io/gantt-spqm1/">'
        "Apri il Gantt a schermo intero</a></p>"
        '<ac:structured-macro ac:name="html" ac:schema-version="1">'
        '<ac:plain-text-body><![CDATA[<iframe src="https://ccarroccio.github.io/gantt-spqm1/" '
        'width="100%" height="900px"></iframe>]]></ac:plain-text-body></ac:structured-macro>'
    )


def main():
    base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip().rstrip("/")
    email = os.getenv("CONFLUENCE_USER_EMAIL", "").strip()
    token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
    page_id = os.getenv("CONFLUENCE_PAGE_ID", "229969082").strip()

    missing = [
        name for name, value in {
            "CONFLUENCE_BASE_URL": base_url,
            "CONFLUENCE_USER_EMAIL": email,
            "CONFLUENCE_API_TOKEN": token,
            "CONFLUENCE_PAGE_ID": page_id,
        }.items() if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required Confluence configuration: {', '.join(missing)}")

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    page_url = f"{base_url}/rest/api/content/{page_id}?expand=version,body.storage"
    page = request_json(page_url, email, token, operation="page read")
    version = int(page["version"]["number"])
    title = page["title"]

    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version + 1, "minorEdit": True},
        "body": {"storage": {"value": page_content(data), "representation": "storage"}},
        "metadata": {"properties": {
            "content-appearance-published": {"value": "full-width"},
            "content-appearance-draft": {"value": "full-width"},
        }},
    }
    request_json(
        f"{base_url}/rest/api/content/{page_id}",
        email,
        token,
        "PUT",
        payload,
        operation="page update with iframe and Jira KPIs",
    )
    print(f"Updated Confluence page {page_id} to version {version + 1}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Confluence publish::{message}", flush=True)
        raise
