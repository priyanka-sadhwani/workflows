# Automations

Scheduled jobs that keep Notion work-hour summaries up to date.

## sync-hours

Reads the Notion **Work Hours** database and writes one row per week and project into **Weekly Work Hours**. Re-running is safe: each summary row is matched on a key like `2026-09-28 | Project A`. Rows whose source entries disappeared are set to 0.

### Run locally

```bash
cd sync-hours
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NOTION_TOKEN=secret_...
export WORK_HOURS_DB_ID=...
export SUMMARY_DB_ID=...

python sync_hours.py --dry-run
python sync_hours.py
```

### GitHub Actions

`.github/workflows/sync-hours.yml` runs the sync daily at 06:00 UTC, and on demand from the Actions tab. Add these repository secrets:

- `NOTION_TOKEN` — Notion integration secret
- `WORK_HOURS_DB_ID` — id of the Work Hours database
- `SUMMARY_DB_ID` — id of the Weekly Work Hours database

The Notion integration needs access to both databases.
