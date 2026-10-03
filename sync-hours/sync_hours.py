#!/usr/bin/env python3
"""
Summarise a Notion "Work Hours" database into a "Weekly Work Hours" database,
one row per (week, project). Safe to re-run: rows are matched on a key
("2026-09-28 | Project A") stored in the summary database's title column.

Env vars:
  NOTION_TOKEN        integration secret
  WORK_HOURS_DB_ID    id of the Work Hours database
  SUMMARY_DB_ID       id of the Weekly Work Hours database

Usage:
  python sync_hours.py            # sync
  python sync_hours.py --dry-run  # print what would change, write nothing
"""
import argparse
import os
import sys
import time
from collections import defaultdict

import requests

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# --- Property names in Work Hours (read) ---
SRC_PROJECT = "Project"      # select
SRC_HOURS = "Hours"          # formula (number)
SRC_WEEK = "Week start"      # formula (text), e.g. 2026-09-28

# --- Property names in Weekly Work Hours (write) ---
# The title property is detected automatically and holds the key.
DST_WEEK = "Week start"      # text (rich text)
DST_PROJECT = "Project"      # select
DST_HOURS = "Hours"          # number

NO_PROJECT = "(no project)"


def session(token):
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    })
    return s


def call(s, method, path, **kwargs):
    """HTTP call with simple retry for rate limits and transient errors."""
    for attempt in range(6):
        r = s.request(method, f"{API}{path}", timeout=30, **kwargs)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            continue
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if not r.ok:
            sys.exit(f"Notion API error {r.status_code} on {method} {path}: {r.text}")
        time.sleep(0.35)  # stay under ~3 requests/second
        return r.json()
    sys.exit(f"Gave up after retries: {method} {path}")


def query_all(s, db_id):
    results, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = call(s, "POST", f"/databases/{db_id}/query", json=body)
        results.extend(data["results"])
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def title_property_name(s, db_id, required):
    db = call(s, "GET", f"/databases/{db_id}")
    props = db["properties"]
    missing = [p for p in required if p not in props]
    if missing:
        sys.exit(f"Summary database is missing properties: {missing}")
    for name, p in props.items():
        if p["type"] == "title":
            return name
    sys.exit("Summary database has no title property?")


def read_entries(pages):
    totals = defaultdict(float)
    skipped = 0
    for page in pages:
        p = page["properties"]
        week = (p.get(SRC_WEEK, {}).get("formula") or {}).get("string")
        hours = (p.get(SRC_HOURS, {}).get("formula") or {}).get("number")
        sel = (p.get(SRC_PROJECT) or {}).get("select")
        project = sel["name"] if sel else NO_PROJECT
        if not week or hours is None:
            skipped += 1
            continue
        totals[(week, project)] += hours
    if skipped:
        print(f"Skipped {skipped} entries with no week or no hours.")
    return {k: round(v, 2) for k, v in totals.items()}


def read_existing(pages, title_prop):
    existing = {}
    for page in pages:
        title = "".join(t["plain_text"] for t in page["properties"][title_prop]["title"])
        hours = page["properties"].get(DST_HOURS, {}).get("number")
        existing[title] = (page["id"], hours)
    return existing


def props_for(title_prop, key, week, project, hours):
    return {
        title_prop: {"title": [{"text": {"content": key}}]},
        DST_WEEK: {"rich_text": [{"text": {"content": week}}]},
        DST_PROJECT: {"select": {"name": project}},
        DST_HOURS: {"number": hours},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        token = os.environ["NOTION_TOKEN"]
        src_id = os.environ["WORK_HOURS_DB_ID"]
        dst_id = os.environ["SUMMARY_DB_ID"]
    except KeyError as e:
        sys.exit(f"Missing environment variable: {e}")

    s = session(token)
    title_prop = title_property_name(s, dst_id, [DST_WEEK, DST_PROJECT, DST_HOURS])

    totals = read_entries(query_all(s, src_id))
    existing = read_existing(query_all(s, dst_id), title_prop)
    print(f"{len(totals)} week/project totals; {len(existing)} existing summary rows.")

    created = updated = zeroed = 0
    computed_keys = set()

    for (week, project), hours in sorted(totals.items()):
        key = f"{week} | {project}"
        computed_keys.add(key)
        if key in existing:
            page_id, old = existing[key]
            if old is None or abs(old - hours) > 1e-9:
                print(f"UPDATE {key}: {old} -> {hours}")
                if not args.dry_run:
                    call(s, "PATCH", f"/pages/{page_id}",
                         json={"properties": {DST_HOURS: {"number": hours}}})
                updated += 1
        else:
            print(f"CREATE {key}: {hours}")
            if not args.dry_run:
                call(s, "POST", "/pages", json={
                    "parent": {"database_id": dst_id},
                    "properties": props_for(title_prop, key, week, project, hours),
                })
            created += 1

    # Rows whose entries were deleted or re-projected: set to 0 so they don't go stale.
    for key, (page_id, old) in existing.items():
        if key not in computed_keys and old not in (0, None):
            print(f"ZERO   {key}: {old} -> 0")
            if not args.dry_run:
                call(s, "PATCH", f"/pages/{page_id}",
                     json={"properties": {DST_HOURS: {"number": 0}}})
            zeroed += 1

    tag = " (dry run, nothing written)" if args.dry_run else ""
    print(f"Done{tag}: {created} created, {updated} updated, {zeroed} zeroed.")


if __name__ == "__main__":
    main()
