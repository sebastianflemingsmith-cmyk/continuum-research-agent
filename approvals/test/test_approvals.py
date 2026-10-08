#!/usr/bin/env python3
"""
Offline tests for step 4 (approvals). No network, no cost.

    python3 Agent/approvals/test/test_approvals.py

They work on a temporary copy of the proposals of 28 Sep 2026 (Agent/reader/test/fixtures/proposed_2026-09-28_346_lines.jsonl)
and never write to Agent/reader or Agent/approvals: the last test checks that.
"""
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

TEST = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TEST)
AGENT = os.path.dirname(HERE)
READER = os.path.join(AGENT, "reader")
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import approvals as AP  # noqa: E402
import sheet as SH  # noqa: E402
import suggest as SG  # noqa: E402
import xlsx as X  # noqa: E402

O = AP.O
FIXTURE = os.path.join(READER, "test", "fixtures", "proposed_2026-09-28_346_lines.jsonl")
FILLED_BY_LIBREOFFICE = os.path.join(TEST, "fixtures", "filled in LibreOffice.xlsx")
SHEET_30_SEP = os.path.join(TEST, "fixtures", "Approvals 2026-09-30 01.11 (rest filled in by Claude).xlsx")
NOW = dt.datetime(2026, 10, 1, 9, 0)
LEDGER = AP.read_ledger()
LATER_WORDS = "A later run's words for the same lead"


def md5(p):
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def listing(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder) for d, _, fs in os.walk(folder) for f in fs if "__pycache__" not in d)


REAL_BEFORE = {p: md5(p) for p in [os.path.join(READER, f) for f in ("proposed.jsonl", "decisions.jsonl", "runs.jsonl", "Open proposals.md")]}
HERE_BEFORE = listing(HERE)


class Work:
    """A temporary reader folder with the 346 proposals, and a folder for the sheets and lists."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="approvals_test_")
        self.reader, self.out = os.path.join(self.root, "reader"), os.path.join(self.root, "approvals")
        os.makedirs(self.reader)
        os.makedirs(self.out)
        shutil.copy(FIXTURE, os.path.join(self.reader, "proposed.jsonl"))

    def run(self, *argv, now=NOW):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = AP.main(list(argv), reader_dir=self.reader, out_dir=self.out, now=now, ledger=LEDGER)
        return code, buf.getvalue()

    def items(self):
        res, _, _ = O.load(self.reader)
        return SH.items(res, LEDGER)

    def filled_sheet(self, choose, name="filled.xlsx"):
        """A sheet with decisions already in: choose(item) -> (decision, note) or None."""
        its = self.items()
        filled = {it["id"]: choose(it) for it in its if choose(it)}
        path = os.path.join(self.out, "sheets", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        SH.write(path, its, NOW, AP.counts_line(its), filled=filled)
        return path, filled

    def decisions(self):
        path = os.path.join(self.reader, "decisions.jsonl")
        return [json.loads(line) for line in text(path).splitlines()] if os.path.exists(path) else []

    def append_proposal(self, line_no, **changes):
        lines = {p["_line"]: p for p in O.read_lines(os.path.join(self.reader, "proposed.jsonl"))[0]}
        p = dict(lines[line_no], found_at="2026-10-05T09:00:00", **changes)
        p.pop("_line")
        with open(os.path.join(self.reader, "proposed.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(p) + "\n")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def first(its, typ, row=None, component=None):
    return next(it for it in its if it["type"] == typ and (row is None or it["row"] == row)
                and (component is None or (it.get("entry") or {}).get("component") == component))


class Sheet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Work()
        cls.code, cls.printed = cls.w.run("make")
        cls.path = os.path.join(cls.w.out, "sheets", "Approvals 2026-10-01 09.00.xlsx")
        cls.book = X.read(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_every_item_once(self):
        """the sheet has one row per open item and per first-version item, each with its own ID"""
        res, _, _ = O.load(self.w.reader)
        rows = self.book["Decide"][1:]
        ids = [r[16] for r in rows]
        self.assertEqual(len(rows), len(res["open"]) + len(SH._old_items(res["earlier"], LEDGER)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.book["Decide"][0], SH.COLUMNS)

    def test_decisions_empty(self):
        """the Decision and Note columns start empty, and reading the sheet back finds no decisions"""
        self.assertEqual(SH.read(self.path, X.read), [])

    def test_order_and_types(self):
        """figures first, then Part A, arithmetic, leads and the first version's items, with a drop-down list for each"""
        types = [r[3] for r in self.book["Decide"][1:]]
        order = ["Figure update", "Part A figure", "Arithmetic", "Lead", "Old item"]
        self.assertEqual([t for i, t in enumerate(types) if i == 0 or types[i - 1] != t], order)

    def test_link_and_quote(self):
        """a figure shows the paper's value, the new value, a link to the filing and its quote"""
        row = next(r for r in self.book["Decide"][1:] if r[16].startswith("B:F141:CRWV_te#"))
        self.assertEqual((row[7], row[9], row[11]), ("20.903 $bn", "33.823 $bn", "10-Q filed 2026-08-12"))
        self.assertIn("Technology equipment | $33,823 | $20,903", row[12])

    def test_how_to_use(self):
        """a second sheet explains the choices and the commands"""
        how = " ".join(str(r[0]) for r in self.book["How to use"] if r)
        for words in ("approve", "keep paper", "reader wrong", "approvals.py check", "approvals.py record"):
            self.assertIn(words, how)

    def test_nothing_else_written(self):
        """making a sheet writes nothing but the sheet"""
        self.assertEqual(listing(self.w.reader), ["proposed.jsonl"])
        self.assertEqual(listing(self.w.out), ["sheets/Approvals 2026-10-01 09.00.xlsx"])


class Record(unittest.TestCase):
    def setUp(self):
        self.w = Work()
        its = self.w.items()
        self.te = first(its, "Figure update", "F141", "CRWV_te")
        self.part_a = first(its, "Part A figure", "F015")
        self.lead = first(its, "Lead")
        self.old = first(its, "Old item")
        self.choices = {self.te["id"]: ("approve", "use the 30 Jun column"), self.part_a["id"]: ("keep paper", "year-end on purpose"),
                        self.lead["id"]: ("use", ""), self.old["id"]: ("Dismiss", "")}

    def tearDown(self):
        self.w.close()

    def sheet(self, extra=None):
        choices = dict(self.choices, **(extra or {}))
        return self.w.filled_sheet(lambda it: choices.get(it["id"]))[0]

    def test_check_writes_nothing(self):
        """check says what would be recorded and writes nothing"""
        path = self.sheet()
        before = listing(self.w.root)
        code, printed = self.w.run("check", "--sheet", path)
        self.assertEqual(code, 0)
        self.assertIn("4 decisions to record", printed)
        self.assertEqual(listing(self.w.root), before)

    def test_record(self):
        """record adds one line per decision to decisions.jsonl, closes those items, and leaves proposed.jsonl alone"""
        m0 = md5(os.path.join(self.w.reader, "proposed.jsonl"))
        open_before = len(O.load(self.w.reader)[0]["open"])
        code, printed = self.w.run("record", "--sheet", self.sheet())
        self.assertEqual(code, 0, printed)
        ds = self.w.decisions()
        self.assertEqual(sorted(d["decision"] for d in ds), ["approved", "dismissed", "kept paper", "use"])
        self.assertEqual(md5(os.path.join(self.w.reader, "proposed.jsonl")), m0)
        res, _, _ = O.load(self.w.reader)
        self.assertEqual(len(res["open"]), open_before - 3)
        self.assertNotIn(self.old["lines"][0], [p["_line"] for p in res["earlier"]])
        self.assertTrue(os.path.exists(os.path.join(self.w.reader, "Open proposals.md")))

    def test_lists(self):
        """Changes to make lists the approved figure with its source; Leads to use the lead; the record of the import its notes"""
        self.w.run("record", "--sheet", self.sheet())
        changes = text(os.path.join(self.w.out, "Changes to make.md"))
        self.assertIn("**F141**", changes)
        self.assertIn("33.823 $bn", changes)
        self.assertIn("https://www.sec.gov/", changes)
        self.assertNotIn("F015", changes)
        leads = text(os.path.join(self.w.out, "Leads to use.md"))
        self.assertIn(self.lead["what"], leads)
        decided = text(os.path.join(self.w.out, "decided", "Decided 2026-10-01 09.00.00.md"))
        self.assertIn("year-end on purpose", decided)
        self.assertIn("The tool's own suggestions", decided)

    def test_reader_wrong_listed(self):
        """'reader wrong' goes on Reader mistakes.md with the note"""
        self.w.run("record", "--sheet", self.sheet({self.te["id"]: ("reader wrong", "old column")}))
        mistakes = text(os.path.join(self.w.out, "Reader mistakes.md"))
        self.assertIn("F141", mistakes)
        self.assertIn("old column", mistakes)

    def test_bad_word_stops_everything(self):
        """a word that is not a choice stops the whole import: nothing is recorded, and the row is named"""
        path = self.sheet({self.part_a["id"]: ("approv", "")})
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 1)
        self.assertIn("'approv' is not a choice", printed)
        self.assertEqual(self.w.decisions(), [])

    def test_wrong_choice_for_type(self):
        """a choice that does not fit the item ('use' for a figure) stops the import"""
        code, printed = self.w.run("record", "--sheet", self.sheet({self.te["id"]: ("use", "")}))
        self.assertEqual(code, 1)
        self.assertIn("not a choice for a figure update", printed)
        self.assertEqual(self.w.decisions(), [])

    def test_edited_id(self):
        """an edited ID stops the import"""
        its = self.w.items()
        path = os.path.join(self.w.out, "sheets", "edited.xlsx")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bad = dict(its[0], id="B:F141:CRWV_te#99999")
        SH.write(path, [bad] + its[1:], NOW, "", filled={"B:F141:CRWV_te#99999": ("approve", "")})
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 1)
        self.assertIn("not in proposed.jsonl", printed)
        self.assertEqual(self.w.decisions(), [])

    def test_record_twice(self):
        """recording the same sheet twice adds nothing the second time"""
        path = self.sheet()
        self.w.run("record", "--sheet", path)
        code, printed = self.w.run("record", "--sheet", path, now=NOW + dt.timedelta(minutes=5))
        self.assertIn("4 already recorded", printed)
        self.assertEqual(len(self.w.decisions()), 4)

    def test_later_records_nothing(self):
        """'later' (the first choice in every list) records nothing for that row"""
        code, printed = self.w.run("record", "--sheet", self.sheet({self.te["id"]: ("later", "")}))
        self.assertEqual(code, 0, printed)
        self.assertEqual(len(self.w.decisions()), 3)
        self.assertFalse(any(d["key"] == self.te["key"] for d in self.w.decisions()))

    def test_id_of_another_item(self):
        """an ID edited to name another item's proposals stops the import"""
        other = first(self.w.items(), "Lead")
        forged = f"{self.te['key']}#{other['lines'][0]}"
        its = self.w.items()
        path = os.path.join(self.w.out, "sheets", "forged.xlsx")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        SH.write(path, [dict(its[0], id=forged)] + its[1:], NOW, "", filled={forged: ("approve", "")})
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 1)
        self.assertIn("does not match", printed)
        self.assertEqual(self.w.decisions(), [])

    def test_same_item_twice(self):
        """the same item twice with different decisions stops the import"""
        path = os.path.join(self.w.out, "sheets", "twice.xlsx")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = [SH.COLUMNS] + [[1, "approve", "", "Figure update"] + [""] * 12 + [self.te["id"]],
                               [2, "reader wrong", "", "Figure update"] + [""] * 12 + [self.te["id"]]]
        X.write(path, [X.Sheet("Decide", rows), X.Sheet("How to use", [[SH.MADE + NOW.isoformat()]], header=False)])
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 1)
        self.assertIn("the same item as row 2", printed)
        self.assertEqual(self.w.decisions(), [])

    def test_no_made_date(self):
        """a sheet that does not say when it was made is refused"""
        path = os.path.join(self.w.out, "sheets", "undated.xlsx")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        X.write(path, [X.Sheet("Decide", [SH.COLUMNS, [1, "approve", "", "Figure update"] + [""] * 12 + [self.te["id"]]])])
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 1)
        self.assertIn("does not say when it was made", printed)

    def test_unreadable_decisions_line(self):
        """if decisions.jsonl has a line that cannot be read, nothing more is recorded"""
        with open(os.path.join(self.w.reader, "decisions.jsonl"), "w") as f:
            f.write('{"decision": "approved", "lines": [1]\n')
        code, printed = self.w.run("record", "--sheet", self.sheet())
        self.assertEqual(code, 1)
        self.assertIn("cannot be read", printed)

    def test_missing_line_ending(self):
        """a decisions.jsonl whose last line lost its line ending (as some editors do) is added to on a new line"""
        path = self.sheet()
        with open(os.path.join(self.w.reader, "decisions.jsonl"), "w") as f:
            f.write(json.dumps(dict(decision="dismissed", key="v1", lines=[self.old["lines"][0]], decided_at="2026-09-30T09:00:00")))
        code, printed = self.w.run("record", "--sheet", path)
        self.assertEqual(code, 0, printed)
        self.assertEqual(len(self.w.decisions()), 4)

    def test_old_lead_found_again(self):
        """a first-version lead that a later run has since found again can still be dismissed from an older sheet"""
        old_lead = next(it for it in self.w.items() if it["type"] == "Old item" and it["key"] == "v1:fact")
        path = self.w.filled_sheet(lambda it: ("dismiss", "") if it["id"] == old_lead["id"] else None, name="oldlead.xlsx")[0]
        lines = {p["_line"]: p for p in O.read_lines(os.path.join(self.w.reader, "proposed.jsonl"))[0]}
        again = dict(lines[old_lead["lines"][0]], version=2, found_at="2026-10-05T09:00:00")
        again.pop("_line")
        with open(os.path.join(self.w.reader, "proposed.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(again) + "\n")
        self.assertFalse(any(it["id"] == old_lead["id"] for it in self.w.items()))
        code, printed = self.w.run("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(code, 0, printed)
        self.assertEqual([d["decision"] for d in self.w.decisions()], ["dismissed"])

    def test_leads_listed_as_decided(self):
        """Leads to use, the sheet's to-do row and the open list show a lead in the words you decided on, after a later run
        finds it again in other words (5 Oct 2026: Amazon's '$25bn notes' lead was listed as a term loan)"""
        self.w.run("record", "--sheet", self.sheet())
        self.w.append_proposal(self.lead["proposal"]["_line"], topic=LATER_WORDS)
        AP.write_lists(O.rebuild(self.w.reader, quiet=True), self.w.out, LEDGER, NOW + dt.timedelta(days=5))
        leads = text(os.path.join(self.w.out, "Leads to use.md"))
        self.assertIn(f"**{self.lead['what']}**", leads)
        self.assertNotIn(LATER_WORDS, leads)
        todo = next(it for it in self.w.items() if it["type"] == "To do" and it["key"] == self.lead["key"])
        self.assertEqual((todo["what"], todo["proposal"]["_line"]), (self.lead["what"], self.lead["proposal"]["_line"]))
        self.assertNotIn(LATER_WORDS, text(os.path.join(self.w.reader, "Open proposals.md")))

    def test_lead_to_do_says_what_its_quote_repeats(self):
        """a lead's to-do row says which of the paper's figures the quote you decided on repeats, not what a later run's
        words repeat (Microsoft 'Finance leases obtained' lost "Repeats the paper's 24,608 (F047)" after 30 Sep 16:43)"""
        lead = next(it for it in self.w.items() if it["type"] == "Lead" and "Repeats the paper's" in it["note"])
        said = next(n for n in lead["note"].split("\n") if n.startswith("Repeats the paper's"))
        path = self.w.filled_sheet(lambda it: ("use", "") if it["id"] == lead["id"] else None, name="repeats.xlsx")[0]
        self.w.run("record", "--sheet", path)
        self.w.append_proposal(lead["proposal"]["_line"], fragments=[{"role": "value", "text": LATER_WORDS + ", with none of the paper's figures."}])
        todo = next(it for it in self.w.items() if it["type"] == "To do" and it["key"] == lead["key"])
        self.assertEqual(todo["proposal"]["_line"], lead["proposal"]["_line"])
        self.assertIn(said, todo["note"].split("\n"))

    def test_found_again_since_the_sheet(self):
        """a row whose item a later run found again after the sheet was made (so its ID has gained a line) is recorded with
        the label the sheet showed, not a blank one (F141-1, recorded 5 Oct 2026)"""
        path = self.sheet()
        self.w.append_proposal(max(self.te["lines"]))                                   # the same figure again
        self.w.append_proposal(self.lead["proposal"]["_line"], topic=LATER_WORDS)       # the lead again, in other words
        self.assertFalse(any(it["id"] in (self.te["id"], self.lead["id"]) for it in self.w.items()))
        code, printed = self.w.run("record", "--sheet", path, now=dt.datetime(2026, 10, 6, 9, 0))
        self.assertEqual(code, 0, printed)
        ds = {d["key"]: d for d in self.w.decisions()}
        self.assertEqual((ds[self.te["key"]]["what"], ds[self.te["key"]]["lines"]), (self.te["what"], self.te["lines"]))
        self.assertEqual(ds[self.lead["key"]]["what"], self.lead["what"])
        decided = text(os.path.join(self.w.out, "decided", "Decided 2026-10-06 09.00.00.md"))
        self.assertIn(f"Figure update F141 CoreWeave: {self.te['what']}. Note: use the 30 Jun column", decided)

    def test_short_forms(self):
        """short forms and capitals are read (a, k, w, u, d; 'Approve')"""
        self.assertEqual([SH.choice(x) for x in ("a", "Approve", " K ", "w", "u", "D", "dismissed", "done", "Later", "maybe")],
                         ["approve", "approve", "keep paper", "reader wrong", "use", "dismiss", "dismiss", "done", "later", None])


class Sticking(unittest.TestCase):
    def setUp(self):
        self.w = Work()
        its = self.w.items()
        self.te = first(its, "Figure update", "F141", "CRWV_te")
        self.lead = first(its, "Lead")
        path, _ = self.w.filled_sheet(lambda it: {self.te["id"]: ("approve", ""), self.lead["id"]: ("dismiss", "")}.get(it["id"]))
        self.w.run("record", "--sheet", path)

    def tearDown(self):
        self.w.close()

    def test_same_figure_found_again(self):
        """a later run that finds the approved figure again does not bring it back"""
        self.w.append_proposal(max(self.te["lines"]))
        self.assertFalse(any(it["key"] == self.te["key"] and it["type"] == "Figure update" for it in self.w.items()))

    def test_new_figure_reopens(self):
        """a later run with a different figure brings it back, with your earlier decision shown"""
        self.w.append_proposal(max(self.te["lines"]), new_value=41.0, new_period_end="2026-09-30")
        it = next(it for it in self.w.items() if it["key"] == self.te["key"] and it["type"] == "Figure update")
        self.assertIn("You decided 'approved'", it["note"])
        self.assertIn("41 $bn", it["found"])

    def test_dismissed_lead_found_again(self):
        """a dismissed lead found again by a later run stays closed"""
        self.w.append_proposal(max(self.lead["lines"]))
        self.assertFalse(any(it["key"] == self.lead["key"] for it in self.w.items()))

    def test_to_do_then_done(self):
        """an approved figure comes back as 'to do'; marking it done takes it off Changes to make"""
        todo = first(self.w.items(), "To do")
        self.assertEqual(todo["key"], self.te["key"])
        path, _ = self.w.filled_sheet(lambda it: ("done", "") if it["id"] == todo["id"] else None, name="second.xlsx")
        code, printed = self.w.run("record", "--sheet", path, now=NOW + dt.timedelta(days=1))
        self.assertEqual(code, 0, printed)
        self.assertNotIn("**F141**", text(os.path.join(self.w.out, "Changes to make.md")))
        self.assertFalse(any(it["type"] == "To do" for it in self.w.items()))

    def test_reopen(self):
        """reopen F141 opens the decided figure again"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            AP.reopen("F141", self.w.reader, NOW + dt.timedelta(hours=1), yes=True)
        self.assertTrue(any(it["key"] == self.te["key"] and it["type"] == "Figure update" for it in self.w.items()))

    def test_reopen_lead_by_the_words_decided(self):
        """reopen finds a decided lead by words from the topic you decided on, after a later run found it in other words"""
        self.w.append_proposal(self.lead["proposal"]["_line"], topic=LATER_WORDS)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = AP.reopen(self.lead["what"], self.w.reader, NOW + dt.timedelta(days=5), yes=True)
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn(self.lead["what"], buf.getvalue())
        self.assertTrue(any(it["key"] == self.lead["key"] and it["type"] == "Lead" for it in self.w.items()))

    def test_old_sheet_cannot_undo(self):
        """an older sheet saved again cannot undo a decision made after it (the row is skipped, with a note)"""
        old_sheet = os.path.join(self.w.out, "sheets", "filled.xlsx")
        buf = io.StringIO()
        with redirect_stdout(buf):
            AP.reopen("F141", self.w.reader, NOW + dt.timedelta(days=2), yes=True)
        code, printed = self.w.run("record", "--sheet", old_sheet, now=NOW + dt.timedelta(days=3))
        self.assertIn("decided again after this sheet was made", printed)
        self.assertTrue(any(it["key"] == self.te["key"] and it["type"] == "Figure update" for it in self.w.items()))

    def test_approval_survives_later_figure(self):
        """an approval stays on Changes to make when a later run proposes another figure and that one is marked 'reader wrong'"""
        self.w.append_proposal(max(self.te["lines"]), new_value=999.0)
        it = next(it for it in self.w.items() if it["key"] == self.te["key"] and it["type"] == "Figure update")
        path, _ = self.w.filled_sheet(lambda x: ("reader wrong", "nonsense") if x["id"] == it["id"] else None, name="later.xlsx")
        code, printed = self.w.run("record", "--sheet", path, now=NOW + dt.timedelta(days=1))
        self.assertEqual(code, 0, printed)
        changes = text(os.path.join(self.w.out, "Changes to make.md"))
        self.assertIn("**F141**", changes)
        self.assertIn("33.823 $bn", changes)
        self.assertIn("F141", text(os.path.join(self.w.out, "Reader mistakes.md")))

    def test_stale_sheet(self):
        """a decision from a sheet made before a newer figure arrived is kept for the old figure; the item stays open"""
        its = self.w.items()
        other = first(its, "Figure update", "F059")
        path, _ = self.w.filled_sheet(lambda it: ("approve", "") if it["id"] == other["id"] else None, name="stale.xlsx")
        self.w.append_proposal(max(other["lines"]), new_value=300.0)
        code, printed = self.w.run("record", "--sheet", path, now=NOW + dt.timedelta(hours=2))
        self.assertEqual(code, 0, printed)
        self.assertIn("a later run proposes 300.0", printed)
        self.assertTrue(any(it["key"] == other["key"] and it["type"] == "Figure update" for it in self.w.items()))


class RealSheet(unittest.TestCase):
    def test_sheet_of_30_sep_after_regrouping(self):
        """the real sheet of 30 Sep, on the data of 5 Oct, still checks cleanly although leads are now grouped by stricter
        rules (some of its rows name lines that are now in two entries): only row 10 (F141-1) is left to record"""
        root = tempfile.mkdtemp(prefix="approvals_real_")
        try:
            reader = os.path.join(root, "reader")
            os.makedirs(reader)
            for f in ("proposed.jsonl", "decisions.jsonl", "runs.jsonl"):
                shutil.copy(os.path.join(READER, "test", "fixtures", "review_2026-10-05", f), reader)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = AP.main(["check", "--sheet", SHEET_30_SEP], reader_dir=reader, out_dir=os.path.join(root, "approvals"), now=NOW, ledger=LEDGER)
            printed = buf.getvalue()
            self.assertEqual(code, 0, printed)
            self.assertNotIn("PROBLEM", printed)
            self.assertIn("1 decisions to record: 1 approved; 138 already recorded (skipped).", printed)
            self.assertIn("row 10  approved      Figure update F141 CoreWeave: CoreWeave gross technology equipment", printed)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class SpreadsheetApps(unittest.TestCase):
    def test_saved_by_libreoffice(self):
        """a sheet filled in and saved by a spreadsheet program (LibreOffice) is read back and recorded"""
        w = Work()
        try:
            code, printed = w.run("record", "--sheet", FILLED_BY_LIBREOFFICE)
            self.assertEqual(code, 0, printed)
            self.assertEqual(sorted(d["decision"] for d in w.decisions()), ["approved", "dismissed", "dismissed", "kept paper", "use"])
            self.assertTrue(any(d["note"] == "typed in LibreOffice" for d in w.decisions()))
        finally:
            w.close()

    def test_formula_without_value(self):
        """a link saved without its value (as some programs do) does not stop the sheet being read"""
        import zipfile
        root = tempfile.mkdtemp(prefix="approvals_test_")
        try:
            path = os.path.join(root, "x.xlsx")
            row = [1, "approve", "", "Figure update"] + [""] * 7 + [X.Link("https://www.sec.gov/", "y")] + [""] * 4 + ["B:F141:CRWV_te#109"]
            X.write(path, [X.Sheet("Decide", [SH.COLUMNS, row])])
            with zipfile.ZipFile(path) as z:
                parts = {n: z.read(n) for n in z.namelist()}
            self.assertIn(b"<v>y</v>", parts["xl/worksheets/sheet1.xml"])
            parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(b"<v>y</v>", b"<v></v>")
            with zipfile.ZipFile(path, "w") as z:
                for n, data in parts.items():
                    z.writestr(n, data)
            self.assertEqual(SH.read(path, X.read)[0]["typed"], "approve")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class Suggestions(unittest.TestCase):
    def test_rules(self):
        """the tool's rules: a percentage 100 times off is the reader's mistake; a date quoted on purpose keeps the paper;
        the first version's items are dismissed; a lead from a call is dismissed; a newer figure from a filing is approved"""
        cases = [(dict(type="Figure update", proposal=dict(paper_value="20", new_value=0.2, unit="%")), "reader wrong"),
                 (dict(type="Part A figure", proposal=dict(note="The paper deliberately quotes the year-end balance")), "keep paper"),
                 (dict(type="Old item", proposal={}), "dismiss"),
                 (dict(type="Lead", proposal=dict(document="Microsoft earnings call transcript", related_rows=["F089"])), "dismiss"),
                 (dict(type="Figure update", proposal=dict(kind="newer figure", document="10-Q filed 2026-08-12",
                                                           paper_value="20.903", new_value=33.823, unit="$bn")), "approve")]
        self.assertEqual([SG.suggest(c)[0] for c, _ in cases], [want for _, want in cases])


from test_gate import GateChecks, GateLists, GateNeverTouches, GateReplay, GateUndo  # noqa: E402,F401  the AI gate's tests


class ZzNothingWritten(unittest.TestCase):
    def test_real_files_untouched(self):
        """nothing in Agent/reader or Agent/approvals was written by these tests"""
        after = {p: md5(p) for p in REAL_BEFORE}
        self.assertEqual(after, REAL_BEFORE)
        self.assertEqual(listing(HERE), HERE_BEFORE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
