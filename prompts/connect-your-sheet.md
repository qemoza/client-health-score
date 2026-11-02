# Send the numbers to your Google Sheet

Paste this into Claude Code, inside this repo.

```
After python3 health.py measure, put out/rows.csv into my Google Sheet, on the Clients tab, from row 3 down. The first 14 columns only. Never touch Score, Status or Why, because those are formulas.

Walk me through a Google service account, click by click. Share only this one sheet with it. Keep its key file out of git, and check that .gitignore covers its name.

Match clients by name. If a client is in the csv and not in the sheet, add a row. If a client is in the sheet and not in the csv, leave it and tell me.
When it runs, read one row back from the sheet and show me it matches the csv.
```
