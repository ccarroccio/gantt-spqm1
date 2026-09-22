import copy
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from publish_to_confluence import page_content
from render_gantt import render_existing
from update_from_jira import complete_worklogs, update_from_jira


def issue(key="SPQM-2", estimate=3600, remaining=1800, entries=None):
    entries = entries if entries is not None else [
        {"id": "1", "started": "2026-07-14T09:00:00.000+0200", "timeSpentSeconds": 1800},
        {"id": "2", "started": "2026-07-17T09:00:00.000+0200", "timeSpentSeconds": 3600},
    ]
    return {
        "key": key,
        "fields": {
            "parent": {"key": "SPQM-1"},
            "summary": "BE-01 - A & B",
            "assignee": None,
            "status": {"name": "Fatto", "statusCategory": {"key": "done"}},
            "timeoriginalestimate": estimate,
            "timespent": sum(w["timeSpentSeconds"] for w in entries) if entries else None,
            "timeestimate": remaining,
            "worklog": {"startAt": 0, "total": len(entries), "worklogs": entries},
        },
    }


class WorklogPublicationTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "index.html").read_bytes().decode("utf-8")
        self.old = {"meta": {"title": "SPQM-1", "deadline": "16/10/2026"}, "sections": []}
        self.now = datetime(2026, 9, 22, 6, tzinfo=timezone.utc)

    def data(self, issues):
        return update_from_jira(self.old, issues, "https://jira.gigroup.com/jira", self.now)

    def row(self, rendered, key):
        return next(row for row in re.findall(r"<tr>.*?</tr>", rendered, re.S) if f"/{key}'" in row)

    def test_real_remaining_and_fractional_hours_not_recomputed(self):
        data = self.data([issue()])
        task = data["sections"][0]["tasks"][0]
        self.assertEqual((task["estimatedHours"], task["loggedHours"], task["remainingHours"]), (1, 1.5, 0.5))
        self.assertEqual(task["status"], "Fatto")
        self.assertEqual(task["owner"], "Unassigned")
        self.assertNotIn("deadline", data["meta"])
        self.assertEqual(data["meta"]["today"], "22/09/2026")

    def test_preserve_css_headers_and_no_bars_between_worklogs(self):
        data = self.data([issue()])
        rendered = render_existing(self.source, data)
        self.assertEqual(re.search(r"<style>.*?</style>", self.source, re.S)[0],
                         re.search(r"<style>.*?</style>", rendered, re.S)[0])
        self.assertEqual(re.findall(r"<div class='sct'>.*?</div>", self.source),
                         re.findall(r"<div class='sct'>.*?</div>", rendered))
        self.assertEqual(re.findall(r"<th\b[^>]*>[^<]*</th>", self.source),
                         re.findall(r"<th\b[^>]*>[^<]*</th>", rendered))
        row = self.row(rendered, "SPQM-2")
        self.assertIn("1h / 1.5h", row)
        self.assertIn("background:#f0883e", row)
        self.assertIn("14/07-17/07", row)
        self.assertEqual(row.count("class='wl-bar'"), 2)
        self.assertNotIn("Worklog: 15/07", row)
        self.assertIn("0.5h", row)
        self.assertIn("A &amp; B", row)
        self.assertEqual(render_existing(rendered, data), rendered)

    def test_missing_worklogs_show_dash_and_today_only(self):
        rendered = render_existing(self.source, self.data([issue(entries=[])]))
        row = self.row(rendered, "SPQM-2")
        self.assertIn("color:#6e7681'>-</span>", row)
        self.assertNotIn("class='wl-bar'", row)
        self.assertEqual(row.count("class='today-inner'"), 1)
        self.assertIn("background:#3fb950", row)

    def test_equal_estimate_is_green_and_zero_estimate_overrun_is_orange(self):
        for estimate, color in [(5400, "#3fb950"), (0, "#f0883e")]:
            with self.subTest(estimate=estimate):
                row = self.row(render_existing(self.source, self.data([issue(estimate=estimate)])), "SPQM-2")
                self.assertIn(f"width:100%;height:100%;background:{color}", row)

    def test_unknown_estimates_are_not_fabricated(self):
        data = self.data([issue(estimate=None, remaining=None)])
        row = self.row(render_existing(self.source, data), "SPQM-2")
        self.assertIn("- / 1.5h", row)
        self.assertNotIn("0h &#10003;", row)
        self.assertIn("<td>-</td><td>1.5h</td><td>-</td>", page_content(data))

    def test_removed_and_new_subtasks_are_reconciled(self):
        data = self.data([issue("SPQM-999")])
        rendered = render_existing(self.source, data)
        self.assertEqual(re.findall(r"/browse/(SPQM-\d+)'", rendered), ["SPQM-999"])
        self.assertIn("<b style='color:#f0883e'>1</b>Totale", rendered)

    def test_incomplete_or_inconsistent_data_fails(self):
        for field in ("total", "timespent", "parent"):
            bad = issue()
            if field == "total":
                bad["fields"]["worklog"]["total"] += 1
            elif field == "timespent":
                bad["fields"]["timespent"] += 1
            else:
                bad["fields"]["parent"]["key"] = "SPQM-9"
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.data([bad])

    def test_paginated_worklogs_are_fetched_completely(self):
        raw = issue()
        entries = copy.deepcopy(raw["fields"]["worklog"]["worklogs"])
        raw["fields"]["worklog"]["worklogs"] = entries[:1]
        calls = []

        def request(path, query):
            start = query["startAt"]
            calls.append(start)
            return {"startAt": start, "total": 2, "worklogs": entries[start:start + 1]}

        complete_worklogs(raw, request)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(raw["fields"]["worklog"]["worklogs"], entries)

    def test_cross_year_dates_sort_chronologically(self):
        entries = [
            {"id": "1", "started": "2025-12-31T23:00:00+0100", "timeSpentSeconds": 1800},
            {"id": "2", "started": "2026-01-02T09:00:00+0100", "timeSpentSeconds": 1800},
        ]
        row = self.row(render_existing(self.source, self.data([issue(entries=entries)])), "SPQM-2")
        self.assertIn("31/12-02/01", row)

    def test_confluence_contains_only_iframe_in_html_macro(self):
        content = page_content(self.data([issue()]))
        cdata = re.findall(r"<!\[CDATA\[(.*?)\]\]>", content, re.S)
        self.assertEqual(cdata, [
            '<iframe src="https://ccarroccio.github.io/gantt-spqm1/" width="100%" height="900px"></iframe>'
        ])
        self.assertIn("<td>1h</td><td>1.5h</td><td>0.5h</td>", content)
        self.assertIn("Aggiornamento automatico:", content)
        self.assertIn("Apri il Gantt a schermo intero", content)
        self.assertNotRegex(content.lower(), r"<!doctype|<style|fine prevista|ferie|malattia|ri:attachment")


if __name__ == "__main__":
    unittest.main()
