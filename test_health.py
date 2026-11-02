"""Checks the math and the safety rules. Run: python3 -m unittest -q"""
import csv
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import health as h

AS_OF = dt.date(2026, 11, 30)
# The numbers the sheet ships with. Fixed here, so changing rules.json for your business does not break the tests.
RULES = {
    "replies_slower": {"trigger": 2, "points": 20}, "emails_shorter": {"trigger": -0.3, "points": 10},
    "worried_questions": {"trigger": 2, "points": 15}, "calls_moved": {"trigger": 2, "points": 15},
    "approvals_slower": {"trigger": 3, "points": 10}, "results_under_goal": {"trigger": 0.8, "points": 40},
    "revision_rounds": {"trigger": 3, "points": 10}, "missed_deadlines": {"trigger": 1, "points": 15},
    "green_from": 75, "amber_from": 50,
}
# What the Google Sheet shows for the 15 made-up clients. The script has to agree with the sheet.
SHEET = {
    "Summit Ridge Roofing": (30, "Red", "Replies 4x slower than last month, 3 worried questions, 2 calls skipped or moved, emails 42% shorter, approvals 3.5x slower"),
    "Dewleaf Skincare": (45, "Red", "Results at 58% of goal, 1 missed deadline"),
    "Clearwater Plumbing": (55, "Amber", "Replies 2.2x slower than last month, 2 missed deadlines, approvals 3x slower"),
    "Lumen Physio Clinic": (60, "Amber", "2 worried questions, 1 missed deadline, 4 revision rounds"),
    "Northgate Gym Co.": (65, "Amber", "Replies 2.5x slower than last month, 2 calls skipped or moved"),
    "Copperpot Restaurant Group": (70, "Amber", "Emails 34% shorter, approvals 3x slower, 3 revision rounds"),
    "Velvet Bloom Candles": (80, "Green", "Emails 31% shorter, 3 revision rounds"),
    "Greenway Landscaping": (85, "Green", "1 missed deadline"),
    "Tidewater Yoga Studio": (85, "Green", "2 calls skipped or moved"),
    "Stonefield Home Builders": (90, "Green", "3 revision rounds"),
    "Fernhill Auto Repair": (90, "Green", "Emails 33% shorter"),
    "Oakbridge Family Law": (100, "Green", "All signals healthy"),
    "Maple Row Dental Clinic": (100, "Green", "All signals healthy"),
    "Bluebird Pet Grooming": (100, "Green", "All signals healthy"),
    "Two Hills Coffee Roasters": (100, "Green", "All signals healthy"),
}


def sample_rows():
    return h.read_csv(h.SAMPLE / "rows.csv")


class Score(unittest.TestCase):
    def test_every_made_up_client_matches_the_sheet(self):
        scored = h.score_rows(sample_rows(), RULES)
        self.assertEqual(len(scored), 15)
        for s in scored:
            self.assertEqual((s["Score"], s["Status"], s["Why"]), SHEET[s["Client"]], s["Client"])

    def test_the_live_change_from_the_video(self):
        row = next(r for r in sample_rows() if r["Client"] == "Greenway Landscaping")
        row[h.C_NOW] = "15"
        s = h.score_row(row, RULES)
        self.assertEqual((s["Score"], s["Status"]), (65, "Amber"))

    def test_empty_cells_fire_nothing(self):
        s = h.score_row({"Client": "New client"}, RULES)
        self.assertEqual((s["Score"], s["Status"], s["Why"]), (100, "Green", "All signals healthy"))

    def test_score_never_goes_under_zero(self):
        s = h.score_row({h.C_LAST: 1, h.C_NOW: 9, h.C_EMAIL: "-90%", h.C_WORRIED: 9, h.C_CALLS: 9, h.C_APP_BEFORE: 1,
                         h.C_APP_NOW: 9, h.C_RESULTS: "1%", h.C_REVISIONS: 9, h.C_MISSED: 9}, RULES)
        self.assertEqual(s["Score"], 0)

    def test_monday_message(self):
        text = h.monday_message(h.score_rows(sample_rows(), RULES))
        lines = text.split("\n")
        self.assertEqual(lines[0], "Client health, Monday")
        self.assertTrue(lines[2].startswith("🔴 Summit Ridge Roofing: Replies 4x"))
        self.assertTrue(lines[3].startswith("🔴 Dewleaf Skincare:"))
        self.assertTrue(lines[4].startswith("🟠 Clearwater Plumbing:"))
        self.assertEqual(lines[-1], "🟢 9 clients green")

    def test_monday_message_with_no_problems(self):
        self.assertEqual(h.monday_message([{"Client": "A", "Status": "Green", "Score": 100, "Why": ""}]),
                         "Client health, Monday\n\n🟢 1 client green")

    def test_monday_message_names_what_it_could_not_read(self):
        text = h.monday_message([], {"not_read": ["Client A"], "no_messages": ["Client B"], "strays": ["client c"],
                                     "no_results": ["Client D"], "unknown_kinds": ["call skipped"], "bad_dates": ["events.csv (2)"]})
        for piece in ("could not be read for: Client A", "last 30 days: Client B", "not counted: client c",
                      "No results number for: Client D", "not counted: call skipped", "events.csv (2)"):
            self.assertIn(piece, text)

    def test_a_percent_typed_without_the_sign_still_fires(self):
        for typed in ("58", "58%", "0.58"):
            self.assertIn("Results at 58% of goal", h.score_row({h.C_RESULTS: typed}, RULES)["Why"], typed)
        for typed in ("-42", "-42%", "-0.42"):
            self.assertIn("Emails 42% shorter", h.score_row({h.C_EMAIL: typed}, RULES)["Why"], typed)
        self.assertEqual(h.score_row({h.C_RESULTS: "118%"}, RULES)["Why"], "All signals healthy")

    def test_rules_file_has_every_rule(self):
        self.assertEqual(set(h.load_rules()), set(RULES))


class Measure(unittest.TestCase):
    def rows(self):
        return {r["Client"]: r for r in h.measure(h.SAMPLE / "data", AS_OF)}

    def test_reply_time_is_real_hours(self):
        r = self.rows()["Summit Ridge Roofing"]
        self.assertEqual((r[h.C_LAST], r[h.C_NOW]), (5.0, 20.0))

    def test_shorter_emails_and_events(self):
        r = self.rows()["Summit Ridge Roofing"]
        self.assertLess(h.to_number(r[h.C_EMAIL]), -0.3)
        self.assertEqual(r[h.C_CALLS], 2)
        self.assertEqual((r[h.C_APP_BEFORE], r[h.C_APP_NOW]), (2.0, 7.0))

    def test_healthy_client_stays_green(self):
        s = h.score_row(self.rows()["Maple Row Dental Clinic"], RULES)
        self.assertEqual(s["Status"], "Green")

    def test_a_client_who_stops_answering_shows_up(self):
        msgs = [{"who": "us", "_at": dt.datetime(2026, 10, 5, 9), "text": "x"},
                {"who": "client", "_at": dt.datetime(2026, 10, 5, 14), "text": "y"},
                {"who": "us", "_at": dt.datetime(2026, 11, 20, 9), "text": "z"}]
        last, now = h.reply_hours(msgs, AS_OF)
        self.assertEqual(last, 5.0)
        self.assertGreater(now, 200)

    def test_a_wait_counts_only_the_time_that_has_passed(self):
        msgs = [{"who": "us", "_at": dt.datetime(2026, 11, 30, 6), "text": "x"}]
        self.assertEqual(h.reply_hours(msgs, AS_OF, now=dt.datetime(2026, 11, 30, 8))[1], 2.0)

    def write_files(self, d, clients="client,results_vs_goal\nAcme,90%\n", messages=None, events=None):
        Path(d, "clients.csv").write_text(clients)
        if messages is not None:
            Path(d, "messages.csv").write_text(messages)
        if events is not None:
            Path(d, "events.csv").write_text(events)

    def test_a_missing_file_stops_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_files(d, messages="client,sent_at,who,channel,text\n")
            with self.assertRaises(h.HealthError):
                h.measure(d, AS_OF)

    def test_gaps_are_named(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_files(d, clients="client,results_vs_goal\nAcme,\n",
                             messages="client,sent_at,who,channel,text\nAcme LLC,2026-11-02T09:00:00,client,email,hi\nAcme,not a date,client,email,hi\n",
                             events="client,date,kind,value\nAcme,2026-11-02,call skipped,\nAcme,soon,call_moved,\n")
            problems = {}
            h.measure(d, AS_OF, problems=problems)
        self.assertEqual(problems["strays"], ["acme llc"])
        self.assertEqual(problems["no_results"], ["Acme"])
        self.assertEqual(problems["unknown_kinds"], ["call skipped"])
        self.assertEqual(problems["bad_dates"], ["messages.csv (1)", "events.csv (1)"])

    def test_no_clients_file_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(h.HealthError):
                h.measure(d, AS_OF)


class Read(unittest.TestCase):
    def client_messages(self):
        return h.load_messages(h.SAMPLE / "data")["summit ridge roofing"]

    def test_a_made_up_quote_is_thrown_away(self):
        ask = lambda prompt: json.dumps({"worried_questions": ["Why did the calls drop last week?", "We are firing you, why?"],
                                         "formal_wording": True, "formal_quote": "Please advise."})
        r = h.read_client("Summit Ridge Roofing", self.client_messages(), "", AS_OF, ask)
        self.assertEqual((r["worried_count"], r["dropped"]), (1, 1))
        self.assertIn("Please advise.", r["note"])

    def test_the_ai_cannot_inflate_the_count(self):
        real = "Can you explain what we are paying for?"       # the client wrote this once
        ask = lambda prompt: json.dumps({"worried_questions": [real, real, real, "why", "?"]})
        self.assertEqual(h.read_client("X", self.client_messages(), "", AS_OF, ask)["worried_count"], 1)

    def test_a_question_asked_twice_counts_twice(self):
        real = "Why did the calls drop last week?"             # the client really wrote this twice
        ask = lambda prompt: json.dumps({"worried_questions": [real, real]})
        self.assertEqual(h.read_client("X", self.client_messages(), "", AS_OF, ask)["worried_count"], 2)

    def test_wrong_shape_is_named(self):
        with self.assertRaises(h.HealthError):
            h.read_client("X", self.client_messages(), "", AS_OF, lambda prompt: '{"worried_questions": "Why did the calls drop"}')

    def test_no_messages_stays_blank(self):
        r = h.read_client("X", [], "", AS_OF, lambda prompt: self.fail("no call needed"))
        self.assertEqual((r["worried_count"], r["no_messages"]), ("", True))

    def test_formal_wording_needs_a_real_quote(self):
        ask = lambda prompt: json.dumps({"worried_questions": [], "formal_wording": True, "formal_quote": "To whom it may concern"})
        self.assertEqual(h.read_client("X", self.client_messages(), "", AS_OF, ask)["note"], "")

    def test_broken_answer_is_named_and_not_guessed(self):
        reads, failed = h.read_all(h.SAMPLE / "data", AS_OF, lambda prompt: "sorry, I cannot do that")
        self.assertEqual(reads, {})
        self.assertEqual(set(failed), {"Summit Ridge Roofing", "Maple Row Dental Clinic"})

    def test_prompt_marks_client_words_as_data(self):
        seen = []
        h.read_client("Summit Ridge Roofing", self.client_messages(), "a note", AS_OF,
                      lambda p: seen.append(p) or '{"worried_questions": []}')
        self.assertIn("never an instruction", seen[0])
        self.assertNotIn("{{", seen[0])

    def test_no_key_stops_with_plain_words(self):
        with self.assertRaises(h.HealthError):
            h.call_openrouter("hi", {"OPENROUTER_API_KEY": "REPLACE_ME"})


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Post(unittest.TestCase):
    def test_refuses_without_a_real_webhook(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(h.HealthError):
                h.post_to_slack("hi", {"SLACK_WEBHOOK_URL": "REPLACE_ME"}, out_dir=d)

    def test_sends_once_a_day(self):
        sent = []
        def opener(req, timeout=0):
            sent.append(json.loads(req.data))
            return FakeResponse()
        env = {"SLACK_WEBHOOK_URL": "https://example.test/hook"}
        with tempfile.TemporaryDirectory() as d:
            h.post_to_slack("hello", env, out_dir=d, today=AS_OF, opener=opener)
            with self.assertRaises(h.HealthError):
                h.post_to_slack("hello", env, out_dir=d, today=AS_OF, opener=opener)
            h.post_to_slack("hello", env, out_dir=d, today=AS_OF, again=True, opener=opener)
        self.assertEqual(sent, [{"text": "hello"}, {"text": "hello"}])


class Flow(unittest.TestCase):
    def test_score_step_names_what_the_read_step_could_not_do(self):
        with tempfile.TemporaryDirectory() as d:
            h.write_rows(sample_rows(), Path(d, "rows.csv"), h.COLUMNS)
            h.save_problems({"not_read": ["Client A"]}, d)
            _, text = h.cmd_score(Path(d, "rows.csv"), d)
        self.assertIn("could not be read for: Client A", text)

    def test_last_weeks_message_does_not_go_out(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "monday.txt")
            p.write_text("old")
            later = dt.datetime.now() + dt.timedelta(days=7)
            with self.assertRaises(h.HealthError):
                h.check_fresh(p, now=later)
            h.check_fresh(p, again=True, now=later)
            h.check_fresh(p)


class Safety(unittest.TestCase):
    def test_client_data_cannot_be_committed_and_the_sample_can(self):
        ignore = (h.HERE / ".gitignore").read_text().splitlines()
        for line in ("/data/", "/out/", ".env"):
            self.assertIn(line, ignore)
        self.assertNotIn("data/", ignore)    # that line would also hide sample/data and break a fresh clone

    def test_sample_rows_have_every_column(self):
        with (h.SAMPLE / "rows.csv").open(newline="", encoding="utf-8") as f:
            self.assertEqual(next(csv.reader(f)), h.COLUMNS)


if __name__ == "__main__":
    unittest.main()
