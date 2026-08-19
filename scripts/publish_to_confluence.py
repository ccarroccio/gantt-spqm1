import base64
import json
import os
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_FILE = ROOT / "index.html"


def request_json(url, email, token, method="GET", payload=None):
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
        f"Confluence API authentication failed at {url}. "
        "Check CONFLUENCE_BASE_URL, CONFLUENCE_USER_EMAIL, "
        f"CONFLUENCE_API_TOKEN and page permissions. {' | '.join(errors)}"
    )


def html_macro(html):
    # Confluence Server's HTML macro expects the page HTML in plain-text-body.
    return (
        '<ac:structured-macro ac:name="html">'
        '<ac:plain-text-body><![CDATA['
        + html.replace("]]>", "]]]]><![CDATA[>")
        + "]]></ac:plain-text-body></ac:structured-macro>"
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

    html = INDEX_FILE.read_text(encoding="utf-8")
    page_url = f"{base_url}/rest/api/content/{page_id}?expand=version,body.storage"
    page = request_json(page_url, email, token)
    version = int(page["version"]["number"])
    title = page["title"]

    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version + 1, "minorEdit": True},
        "body": {"storage": {"value": html_macro(html), "representation": "storage"}},
    }
    request_json(f"{base_url}/rest/api/content/{page_id}", email, token, "PUT", payload)
    print(f"Updated Confluence page {page_id} to version {version + 1}.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Confluence publish::{message}", flush=True)
        raise
