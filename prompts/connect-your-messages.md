# Connect your messages

Paste this into Claude Code, inside this repo.

```
Help me fill data/messages.csv, data/events.csv and data/clients.csv from my real tools. Look at sample/data/ for the shape.

Ask me one thing at a time:
1. Where do I talk to clients? Email, Slack, WhatsApp, something else.
2. How do I know which message belongs to which client? An email domain, a Slack channel, a label.
3. Where do calls, approvals, revision rounds and deadlines live? My calendar, my task board, my call notes.
4. Where do each client's results live, and what is the goal? An ad account, a report, my CRM. That fills results_vs_goal in data/clients.csv.

Then write one small script per tool. Each one adds rows to the csv files and never adds the same message twice. Read my tools only. Never send, edit or delete anything in them. Save every key in .env and never print one.

If a tool has no good way in, tell me and show me the export I can do by hand.
When it runs, show me 5 rows for one client I know, so I can check them against the real thread.
```
