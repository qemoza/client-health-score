#!/usr/bin/env python3
"""Client health score. Shows which client might leave, in green, amber or red, with the reason.

Code does the math. AI only reads the words. Nothing in here ever writes to a client.

  python3 health.py sample              score the made-up clients and print the Monday message
  python3 health.py sample --measure    run the whole flow on the made-up messages, with no AI
  python3 health.py read                AI reads each client's messages  -> out/reads.json
  python3 health.py measure             your files in data/ -> out/rows.csv
  python3 health.py score [rows.csv]    -> out/scored.csv and out/monday.txt
  python3 health.py post                send out/monday.txt to your Slack
  python3 health.py monday              read, measure, score, post, in that order

Add --as-of YYYY-MM-DD to read and measure to look back from a set day. Python 3.9 or newer. Nothing to install.
"""
import csv
import datetime as dt
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA, OUT, SAMPLE = HERE / "data", HERE / "out", HERE / "sample"
WINDOW_DAYS = 30
MIN_QUOTE_WORDS = 3
STALE_DAYS = 6
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"   # change it with AI_MODEL in .env
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

COLUMNS = [
    "Client", "Monthly fee ($)", "Account manager",
    "Reply time last month (hours)", "Reply time this month (hours)",
    "Email length change (%, negative = shorter)", "Worried questions (30 days)",
    "Calls skipped or moved (30 days)", "Approval time before (days)", "Approval time now (days)",
    "Results vs goal (%)", "Revision rounds (this month)", "Missed deadlines (30 days)", "Account manager note",
]
C_LAST, C_NOW, C_EMAIL, C_WORRIED, C_CALLS, C_APP_BEFORE, C_APP_NOW, C_RESULTS, C_REVISIONS, C_MISSED, C_NOTE = COLUMNS[3:]


class HealthError(Exception):
    """A problem to show the person in plain words."""


def load_rules(path=None):
    """The points and triggers. Change rules.json to fit your business, and the same numbers on the sheet."""
    return json.loads(Path(path or HERE / "rules.json").read_text())


def load_env(path=None):
    """The .env file, then the real environment on top. Keys never leave this dict except in a request header."""
    env = {}
    p = Path(path or HERE / ".env")
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in ("OPENROUTER_API_KEY", "SLACK_WEBHOOK_URL", "AI_MODEL")})
    return env


def real_value(v):
    return bool(v) and v != "REPLACE_ME"


# ---------------------------------------------------------------- numbers

def to_number(v):
    """12, 12.5, $5,500, -42% -> a float. Empty -> None. A percent comes back as a fraction, like the sheet."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "").replace("+", "")
    if not s:
        return None
    try:
        return float(s[:-1]) / 100 if s.endswith("%") else float(s)
    except ValueError:
        return None


def to_fraction(v):
    """A percent column. -42%, -0.42 and -42 all mean the same thing, because people type it every way."""
    n = to_number(v)
    return n / 100 if n is not None and abs(n) > 5 else n


def round_half_up(x, places=0):
    f = 10 ** places
    return math.floor(x * f + 0.5 + 1e-9) / f


def show(x):
    """4.0 -> 4 and 2.25 -> 2.3, the way the sheet shows it."""
    return f"{round_half_up(x, 1):g}"


def percent(x):
    return f"{int(round_half_up(x * 100))}%"


def plural(n, one, many):
    return one if n == 1 else many


# ---------------------------------------------------------------- the score

def score_row(row, rules=None):
    """The same math as the sheet. Every client starts at 100, each signal that fires takes its points off."""
    r = rules or load_rules()
    num = lambda c: to_number(row.get(c))
    zero = lambda c: num(c) or 0.0
    off, why = 0, {}

    results = to_fraction(row.get(C_RESULTS))
    if results is not None and results < r["results_under_goal"]["trigger"]:
        off += r["results_under_goal"]["points"]
        why["results"] = f"results at {percent(results)} of goal"

    last, now = zero(C_LAST), zero(C_NOW)
    if last > 0 and now / last >= r["replies_slower"]["trigger"]:
        off += r["replies_slower"]["points"]
        why["replies"] = f"replies {show(now / last)}x slower than last month"

    worried = zero(C_WORRIED)
    if worried >= r["worried_questions"]["trigger"]:
        off += r["worried_questions"]["points"]
        why["worried"] = f"{worried:g} worried {plural(worried, 'question', 'questions')}"

    calls = zero(C_CALLS)
    if calls >= r["calls_moved"]["trigger"]:
        off += r["calls_moved"]["points"]
        why["calls"] = f"{calls:g} {plural(calls, 'call', 'calls')} skipped or moved"

    missed = zero(C_MISSED)
    if missed >= r["missed_deadlines"]["trigger"]:
        off += r["missed_deadlines"]["points"]
        why["missed"] = f"{missed:g} missed {plural(missed, 'deadline', 'deadlines')}"

    email = to_fraction(row.get(C_EMAIL)) or 0.0
    if email <= r["emails_shorter"]["trigger"]:
        off += r["emails_shorter"]["points"]
        why["emails"] = f"emails {percent(-email)} shorter"

    before, app_now = zero(C_APP_BEFORE), zero(C_APP_NOW)
    if before > 0 and app_now / before >= r["approvals_slower"]["trigger"]:
        off += r["approvals_slower"]["points"]
        why["approvals"] = f"approvals {show(app_now / before)}x slower"

    revisions = zero(C_REVISIONS)
    if revisions >= r["revision_rounds"]["trigger"]:
        off += r["revision_rounds"]["points"]
        why["revisions"] = f"{revisions:g} revision rounds"

    order = ["results", "replies", "worried", "calls", "missed", "emails", "approvals", "revisions"]
    text = ", ".join(why[k] for k in order if k in why)
    text = text[:1].upper() + text[1:] if text else "All signals healthy"
    score = max(0, 100 - off)
    status = "Green" if score >= r["green_from"] else "Amber" if score >= r["amber_from"] else "Red"
    return {"Score": score, "Status": status, "Why": text}


def score_rows(rows, rules=None):
    r = rules or load_rules()
    return [{**row, **score_row(row, r)} for row in rows if (row.get("Client") or "").strip()]


def monday_message(scored, problems=None):
    pick = lambda status, dot: [f"{dot} {x['Client']}: {x['Why']}"
                                for x in sorted((s for s in scored if s["Status"] == status), key=lambda s: s["Score"])]
    reds, ambers = pick("Red", "🔴"), pick("Amber", "🟠")
    greens = sum(1 for s in scored if s["Status"] == "Green")
    lines = ["Client health, Monday", "", *reds, *ambers]
    if reds or ambers:
        lines.append("")
    lines.append(f"🟢 {greens} {plural(greens, 'client', 'clients')} green")
    warnings = problem_lines(problems or {})
    if warnings:
        lines += ["", *warnings]
    return "\n".join(lines)


PROBLEM_TEXT = {
    "not_read": "Worried questions could not be read for",
    "no_messages": "No message from these clients in the last 30 days",
    "no_results": "No results number for",
    "strays": "In your files but not in clients.csv, so not counted",
    "unknown_kinds": "Event kinds I do not know, so not counted",
    "bad_dates": "Rows skipped because of a date I could not read, in",
}


def problem_lines(problems):
    """A number that is missing must never look like a healthy client, so every gap is named where the manager reads."""
    return [f"⚠️ {text}: " + ", ".join(sorted(problems[key])) for key, text in PROBLEM_TEXT.items() if problems.get(key)]


def save_problems(new, out_dir=OUT):
    path = Path(out_dir) / "problems.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**old, **new}, indent=2, ensure_ascii=False))


def load_problems(out_dir=OUT):
    path = Path(out_dir) / "problems.json"
    return json.loads(path.read_text()) if path.exists() else {}


# ---------------------------------------------------------------- measure: your files -> numbers, with code only

def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def to_time(s):
    return dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00")).replace(tzinfo=None)


def windows(as_of):
    end = dt.datetime.combine(as_of, dt.time.max)
    mid = end - dt.timedelta(days=WINDOW_DAYS)
    return end - dt.timedelta(days=2 * WINDOW_DAYS), mid, end


def reply_hours(messages, as_of, now=None):
    """How long the client took to answer us, on average, last month and this month.

    A message of ours still waiting counts too, for as long as it has waited so far.
    Without that, a client who stops answering has no reply time at all and looks healthy.
    Nights and weekends count, so read it against last month and never on its own.
    """
    start, mid, end = windows(as_of)
    waits = {"last": [], "now": []}
    waiting = None

    def add(answered_at, since):
        if start < answered_at <= mid:
            waits["last"].append((answered_at - since).total_seconds() / 3600)
        elif mid < answered_at <= end:
            waits["now"].append((answered_at - since).total_seconds() / 3600)

    for m in sorted(messages, key=lambda x: x["_at"]):
        if m["_at"] > end:
            break
        if m["who"].lower() == "us":
            waiting = waiting or m["_at"]
        elif waiting:
            add(m["_at"], waiting)
            waiting = None
    if waiting:
        clock = now or (dt.datetime.now() if as_of == dt.date.today() else end)
        add(min(end, clock), waiting)
    avg = lambda x: round(sum(x) / len(x), 2) if x else None
    return avg(waits["last"]), avg(waits["now"])


def email_change(messages, as_of):
    start, mid, end = windows(as_of)
    words = {"last": [], "now": []}
    for m in messages:
        if m["who"].lower() != "client" or m.get("channel", "").lower() != "email":
            continue
        count = len(m.get("text", "").split())
        if start < m["_at"] <= mid:
            words["last"].append(count)
        elif mid < m["_at"] <= end:
            words["now"].append(count)
    if not words["last"] or not words["now"]:
        return None
    before = sum(words["last"]) / len(words["last"])
    return (sum(words["now"]) / len(words["now"])) / before - 1 if before else None


def need_csv(path):
    """A missing file must stop the run. If it did not, every client would read as healthy."""
    if not Path(path).exists():
        raise HealthError(f"I can't find {path}. Copy the shape from sample/data/.")
    return read_csv(path)


def load_messages(data_dir, problems=None):
    grouped, bad = {}, 0
    for m in need_csv(Path(data_dir) / "messages.csv"):
        try:
            m["_at"] = to_time(m["sent_at"])
        except (KeyError, ValueError):
            bad += 1
            continue
        grouped.setdefault(m.get("client", "").lower(), []).append(m)
    if bad and problems is not None:
        problems.setdefault("bad_dates", []).append(f"messages.csv ({bad})")
    return grouped


EVENT_KINDS = {"call_skipped", "call_moved", "revision_round", "missed_deadline", "approval"}


def load_events(data_dir, problems):
    grouped, bad, unknown = {}, 0, set()
    for e in need_csv(Path(data_dir) / "events.csv"):
        try:
            e["_date"] = dt.date.fromisoformat(e.get("date", ""))
        except ValueError:
            bad += 1
            continue
        if e.get("kind") not in EVENT_KINDS:
            unknown.add(e.get("kind") or "(empty)")
            continue
        grouped.setdefault(e.get("client", "").lower(), []).append(e)
    if bad:
        problems.setdefault("bad_dates", []).append(f"events.csv ({bad})")
    if unknown:
        problems["unknown_kinds"] = sorted(unknown)
    return grouped


def measure(data_dir, as_of, reads=None, problems=None):
    """One row per client in clients.csv, in the same order as the sheet's columns. Gaps go in problems."""
    data_dir = Path(data_dir)
    problems = {} if problems is None else problems
    clients = need_csv(data_dir / "clients.csv")
    if not clients:
        raise HealthError(f"I found no clients in {data_dir / 'clients.csv'}. Copy the shape from sample/data/clients.csv.")
    messages = load_messages(data_dir, problems)
    events = load_events(data_dir, problems)

    known = {c["client"].lower() for c in clients}
    strays = sorted((set(messages) | set(events)) - known)
    if strays:
        problems["strays"] = strays
    missing = [c["client"] for c in clients if to_fraction(c.get("results_vs_goal")) is None]
    if missing:
        problems["no_results"] = missing

    start, mid, end = windows(as_of)
    this_month = lambda e: mid.date() < e["_date"] <= end.date()
    last_month = lambda e: start.date() < e["_date"] <= mid.date()
    days = lambda xs: round(sum(xs) / len(xs), 1) if xs else ""
    rows = []
    for c in clients:
        key = c["client"].lower()
        msgs, evs = messages.get(key, []), events.get(key, [])
        last, now = reply_hours(msgs, as_of)
        change = email_change(msgs, as_of)
        kind = lambda names: sum(1 for e in evs if e.get("kind") in names and this_month(e))
        app = lambda when: [to_number(e.get("value")) for e in evs
                            if e.get("kind") == "approval" and when(e) and to_number(e.get("value")) is not None]
        read = (reads or {}).get(key) or {}
        note = c.get("note", "")
        if read.get("note"):
            note = f"{note}. {read['note']}" if note else read["note"]
        rows.append({
            "Client": c["client"], "Monthly fee ($)": c.get("monthly_fee", ""), "Account manager": c.get("account_manager", ""),
            C_LAST: "" if last is None else last, C_NOW: "" if now is None else now,
            C_EMAIL: "" if change is None else percent(change),
            C_WORRIED: read.get("worried_count", ""),
            C_CALLS: kind({"call_skipped", "call_moved"}),
            C_APP_BEFORE: days(app(last_month)), C_APP_NOW: days(app(this_month)),
            C_RESULTS: c.get("results_vs_goal", ""),
            C_REVISIONS: kind({"revision_round"}), C_MISSED: kind({"missed_deadline"}),
            C_NOTE: note,
        })
    return rows


# ---------------------------------------------------------------- read: AI reads the words, code checks them

def call_openrouter(prompt, env):
    key = env.get("OPENROUTER_API_KEY")
    if not real_value(key):
        raise HealthError("OPENROUTER_API_KEY is not set. Add it to .env.")
    body = {"model": env.get("AI_MODEL") or DEFAULT_MODEL, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "X-Title": "Client health score", "HTTP-Referer": "https://github.com/qemoza/client-health-score",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.load(res)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise HealthError(f"The AI call failed with status {e.code}: {e.read().decode(errors='replace')[:200]}") from None
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        raise HealthError(f"The AI call failed: {type(e).__name__}.") from None


def flat(text):
    return re.sub(r"\s+", " ", text or "").strip().lower()


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise HealthError("The AI did not send back JSON.")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise HealthError("The AI sent back broken JSON.") from None


def as_block(messages):
    return "\n".join(f"[{m['_at']:%Y-%m-%d} {m.get('channel', '')}] {m.get('text', '')}" for m in messages) or "(nothing)"


def read_client(name, messages, note, as_of, ask):
    """Ask the AI for the words, then keep only what is really in the client's messages.

    A line the code cannot find in the messages is thrown away, and so is a scrap too short to be a question.
    A line counts as many times as the client really wrote it, so the AI cannot inflate the count by repeating it.
    """
    start, mid, end = windows(as_of)
    theirs = sorted((m for m in messages if m["who"].lower() == "client"), key=lambda m: m["_at"])
    now = [m for m in theirs if mid < m["_at"] <= end]
    before = [m for m in theirs if start < m["_at"] <= mid]
    if not now:
        return {"worried_count": "", "worried_questions": [], "note": "", "dropped": 0, "no_messages": True}
    prompt = ((HERE / "prompts" / "read-words.md").read_text()
              .replace("{{CLIENT}}", name).replace("{{THIS_MONTH}}", as_block(now))
              .replace("{{LAST_MONTH}}", as_block(before)).replace("{{MANAGER_NOTE}}", note or "(none)"))
    answer = parse_json(ask(prompt))
    pool = flat(" ".join(m.get("text", "") for m in now))
    raw = answer.get("worried_questions") or []
    if not isinstance(raw, list):
        raise HealthError("The AI sent back the wrong shape.")
    kept, seen = [], {}
    for q in raw:
        line = flat(q) if isinstance(q, str) else ""
        seen[line] = seen.get(line, 0) + 1
        if len(line.split()) >= MIN_QUOTE_WORDS and seen[line] <= pool.count(line):
            kept.append(q)
    notes = []
    quote = answer.get("formal_quote") or ""
    if answer.get("formal_wording") and flat(quote) and flat(quote) in pool:
        notes.append(f"Wording got more formal: {quote}")
    return {"worried_count": len(kept), "worried_questions": kept, "note": " ".join(notes), "dropped": len(raw) - len(kept)}


def read_all(data_dir, as_of, ask):
    """Returns (reads, failed). A client the AI could not read stays blank and gets named. It is never guessed."""
    messages = load_messages(data_dir)
    reads, failed = {}, {}
    for c in need_csv(Path(data_dir) / "clients.csv"):
        key = c["client"].lower()
        try:
            reads[key] = read_client(c["client"], messages.get(key, []), c.get("note", ""), as_of, ask)
        except HealthError as e:
            failed[c["client"]] = str(e)
    return reads, failed


# ---------------------------------------------------------------- post: the Monday message, to you only

def check_fresh(path, again=False, now=None):
    """A job that skipped the score step must not send last week's message as if it were today's."""
    age = (now or dt.datetime.now()) - dt.datetime.fromtimestamp(Path(path).stat().st_mtime)
    if not again and age.days >= STALE_DAYS:
        raise HealthError(f"{path} is {age.days} days old. Run the score step first, or add --again to send it anyway.")


def post_to_slack(text, env, out_dir=OUT, today=None, again=False, opener=urllib.request.urlopen):
    url = env.get("SLACK_WEBHOOK_URL")
    if not real_value(url):
        raise HealthError("SLACK_WEBHOOK_URL is not set. Add it to .env.")
    today = today or dt.date.today()
    mark = Path(out_dir) / "posted.txt"
    if not again and mark.exists() and mark.read_text().strip() == str(today):
        raise HealthError("It already went out today. Add --again if you really want a second one.")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=30) as res:
            if res.status != 200:
                raise HealthError(f"Slack answered with status {res.status}.")
    except urllib.error.URLError as e:     # never print the URL: the webhook is the secret
        raise HealthError(f"Could not reach Slack: {type(e).__name__}.") from None
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    mark.write_text(str(today))


# ---------------------------------------------------------------- files and commands

def write_rows(rows, path, columns):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_reads(path=None):
    p = Path(path or OUT / "reads.json")
    return json.loads(p.read_text()) if p.exists() else None


def cmd_score(path, out_dir=OUT):
    rows = read_csv(path)
    if not rows:
        raise HealthError(f"I found no rows in {path}.")
    scored = score_rows(rows)
    write_rows(scored, Path(out_dir) / "scored.csv", COLUMNS + ["Score", "Status", "Why"])
    text = monday_message(scored, load_problems(out_dir))
    (Path(out_dir) / "monday.txt").write_text(text + "\n", encoding="utf-8")
    return scored, text


def cmd_read(data_dir, as_of, env):
    ask = lambda prompt: call_openrouter(prompt, env)
    if not real_value(env.get("OPENROUTER_API_KEY")):
        raise HealthError("OPENROUTER_API_KEY is not set. Add it to .env.")
    reads, failed = read_all(data_dir, as_of, ask)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reads.json").write_text(json.dumps(reads, indent=2, ensure_ascii=False))
    save_problems({"not_read": sorted(failed), "no_messages": sorted(c for c, r in reads.items() if r.get("no_messages"))})
    for key, r in reads.items():
        print(f"{key}: {r['worried_count']} worried questions"
              + (f", {r['dropped']} quotes thrown away because they were not in the messages" if r.get("dropped") else ""))
    for name, why in failed.items():
        print(f"Could not read {name}: {why}", file=sys.stderr)
    return reads, failed


def flag(args, name):
    if name in args:
        i = args.index(name)
        value = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
        return value
    return None


def main(argv):
    args = list(argv)
    as_of_text = flag(args, "--as-of")
    again = "--again" in args
    if again:
        args.remove("--again")
    command = args[0] if args else "help"
    as_of = dt.date.fromisoformat(as_of_text) if as_of_text else dt.date.today()
    env = load_env()
    if command == "sample":
        if "--measure" in args:
            problems = {}
            rows = measure(SAMPLE / "data", dt.date(2026, 11, 30), problems=problems)
            print("Made-up messages, measured with code. Worried questions stay blank because no AI ran.\n")
            print(monday_message(score_rows(rows), problems))
            return 0
        print("Made-up clients. Every name and number is an example.\n")
        print(monday_message(score_rows(read_csv(SAMPLE / "rows.csv"))))
    elif command == "read":
        _, failed = cmd_read(DATA, as_of, env)
        return 1 if failed else 0
    elif command == "measure":
        problems = {key: [] for key in ("no_results", "strays", "unknown_kinds", "bad_dates")}
        rows = measure(DATA, as_of, load_reads(), problems)
        write_rows(rows, OUT / "rows.csv", COLUMNS)
        save_problems(problems)
        print(f"Wrote {len(rows)} clients to {OUT / 'rows.csv'}")
        for line in problem_lines(problems):
            print(line)
    elif command == "score":
        _, text = cmd_score(args[1] if len(args) > 1 else OUT / "rows.csv")
        print(text)
    elif command == "post":
        path = OUT / "monday.txt"
        if not path.exists():
            raise HealthError("There is no message yet. Run: python3 health.py score")
        check_fresh(path, again)
        post_to_slack(path.read_text(encoding="utf-8").strip(), env, again=again)
        print("Sent to Slack.")
    elif command == "monday":
        reads, failed = cmd_read(DATA, as_of, env)
        problems = {key: [] for key in ("no_results", "strays", "unknown_kinds", "bad_dates")}
        rows = measure(DATA, as_of, reads, problems)
        write_rows(rows, OUT / "rows.csv", COLUMNS)
        save_problems(problems)
        _, text = cmd_score(OUT / "rows.csv")
        print(text)
        post_to_slack(text, env, again=again)
        print("\nSent to Slack.")
        return 1 if failed else 0
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except HealthError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
