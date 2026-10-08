"""
Tests of the AI approval gate (approvals/gate.py), run with the approvals tests:

    python3 Agent/approvals/test/test_approvals.py

They work on temporary copies of the data of 5 Oct 2026 (Agent/reader/test/fixtures/review_2026-10-05/) and never call
DeepSeek: its answers are invented by the tests (marked as such) or replayed from answers the tests saved. No network,
no cost, nothing written outside the temporary folders.
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TEST = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(TEST)
AGENT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import approvals as AP  # noqa: E402
import gate as G  # noqa: E402
import sheet as SH  # noqa: E402

O = AP.O
FIX5 = os.path.join(AGENT, "reader", "test", "fixtures", "review_2026-10-05")
WORKBOOK = os.path.join(FIX5, "Watch list - The AI Lose Lose Race.xlsx")
NOW = dt.datetime(2026, 10, 7, 9, 0)
_BOOK = G.W.read_workbook(WORKBOOK)
LEDGER = G.W.table(_BOOK["Ledger"])
FIGURES = G.W.table(_BOOK["Figures"], first_header="Figure ID")
LEDGER_BY_ID = {r["ID"]: r for r in LEDGER}
RULED = set(G.B.read_paper_differs(os.path.join(FIX5, "paper_differs.json")))
with open(os.path.join(FIX5, "decisions.jsonl"), encoding="utf-8") as _f:
    SEB_5_OCT = [json.loads(x) for x in _f if x.strip()]
F061, F062 = [145, 247, 347, 406], [146, 248, 348, 407]     # the lines of each figure in the copy of 5 Oct


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Asker:
    """Stands in for DeepSeek: answers every question the same way (invented for the tests) and counts the questions."""

    def __init__(self, measure="yes", basis="yes", why="invented for the test"):
        self.answer, self.asked = dict(same_measure=measure, same_basis=basis, why=why), []

    def __call__(self, system, user):
        self.asked.append(user.splitlines()[0])
        return dict(self.answer), {"prompt_tokens": 1000, "completion_tokens": 200}


class Work:
    """A temporary copy of the 5 Oct data: proposals, runs (optional) and decisions (none, or the ones given)."""

    def __init__(self, decisions=(), runs=True, full=False):
        """full=False keeps only the first ten leads (of the current reader): the others' lines are left empty, so every line keeps its number
        and the open list is quick to build (the figures are all there)."""
        self.root = tempfile.mkdtemp(prefix="gate_test_")
        self.reader, self.out = os.path.join(self.root, "reader"), os.path.join(self.root, "approvals")
        os.makedirs(self.reader)
        os.makedirs(os.path.join(self.out, "sheets"))
        kept, out = 0, []
        for raw in read(os.path.join(FIX5, "proposed.jsonl")).splitlines():
            d = json.loads(raw) if raw.strip() else {}
            lead = d.get("row") == "NEW FACT"
            kept += 1 if lead and d.get("version") == 2 else 0
            out.append("" if lead and not full and (kept > 10 or d.get("version") != 2) else raw)
        with open(os.path.join(self.reader, "proposed.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        if runs:
            shutil.copy(os.path.join(FIX5, "runs.jsonl"), self.reader)
        if decisions:
            self.add_decisions(decisions)
        self.paths = dict(reader_dir=self.reader, out_dir=self.out, workbook=WORKBOOK, paper_differs=os.path.join(FIX5, "paper_differs.json"),
                          sheets_dir=os.path.join(self.out, "sheets"), answers_dir=os.path.join(self.root, "answers"),
                          reports_dir=os.path.join(self.root, "reports"), config=os.path.join(self.root, "no config.json"))

    def add_decisions(self, ds):
        AP._append(os.path.join(self.reader, "decisions.jsonl"), [{k: v for k, v in d.items() if not k.startswith("_")} for d in ds])

    def decisions(self):
        path = os.path.join(self.reader, "decisions.jsonl")
        return [json.loads(x) for x in read(path).splitlines()] if os.path.exists(path) else []

    def lines(self):
        return O.read_lines(os.path.join(self.reader, "proposed.jsonl"))[0]

    def add_proposal(self, base, **changes):
        """A later run's proposal: a copy of line `base` with changes. Returns its line number."""
        p = dict(next(q for q in self.lines() if q["_line"] == base), found_at="2026-10-20T09:00:00", run_id="B 2026-10-20 09.00.00")
        p.update(changes)
        p.pop("_line")
        with open(os.path.join(self.reader, "proposed.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(p) + "\n")
        return self.last_line()

    def last_line(self):
        return max(p["_line"] for p in self.lines())

    def gate(self, *argv, ask=None, now=NOW):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = G.main(list(argv), paths=self.paths, ask=ask, now=now)
        return code, buf.getvalue()

    def verdicts(self, from_line=0, ask=None):
        vs, problems, _ = G.decide(self.paths, LEDGER, FIGURES, RULED, from_line, ask, NOW)
        if problems:
            raise AssertionError(f"the gate found problems: {problems}")
        return {v["key"]: v for v in vs}

    def approvals(self, *argv, now=NOW):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = AP.main(list(argv), reader_dir=self.reader, out_dir=self.out, now=now, ledger=LEDGER_BY_ID)
        return code, buf.getvalue()

    def entry(self, key):
        res, _, _ = O.load(self.reader)
        return next(e for g in ("open", "gone", "closed", "aside") for e in res[g] if e["key"] == key)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


class Case(unittest.TestCase):
    def work(self, **kw):
        w = Work(**kw)
        self.addCleanup(w.close)
        return w


# ------------------------------------------------------------------------------------------
# The data of 5 Oct 2026, replayed
# ------------------------------------------------------------------------------------------
LEFT_5_OCT = {   # why the gate leaves each figure for Seb, with nobody's decisions, as Seb asked to see (first reason)
    "B:F046:META_total": "F046-1 is ruled 'Paper differs'",
    "B:F059:ORCL": "the paper's figure is a year-end balance (2026-05-31)",
    "B:F060:META": "F060-1 is checked twice",
    "A:F060:Meta leases not yet commenced": "F060-1 is checked twice",
    "B:F063:GOOGL": "the paper's figure is a year-end balance (2025-12-31)",
    "B:F065:ORCL_start": "the reader says 'text differs'",
    "A:F015:Oracle cash and cash equivalents": "the reader's note asks for care",
    "A:F017:Meta capex incl. finance-lease principal, quarter": "Part A says 'differs from filings'",
    "B:F141:replacement_bill:formula": "arithmetic: an ESTIMATE",
    "B:F141:CRWV_te": "the paper's figure is a year-end balance (2025-12-31)",
}


class GateReplay(Case):
    def test_only_amazon_reaches_deepseek(self):
        """the data of 5 Oct with nobody's decisions: only F061-1 and F062-1 pass every code check, so only they are put to
        DeepSeek and (on invented 'yes' answers) approved; each other figure is left for Seb, with its reason"""
        w, ask = self.work(), Asker()
        code, printed = w.gate("--from-line", "0", "--record", "--ask", ask=ask)
        self.assertEqual(code, 0, printed)
        self.assertEqual(ask.asked, ["The paper's line: Amazon leases not yet commenced",
                                     "The paper's line: Amazon unconditional purchase obligations"])
        ds = w.decisions()
        self.assertEqual([(d["key"], d["decided_by"], d["lines"]) for d in ds],
                         [("B:F061:AMZN", "AI gate", F061), ("B:F062:AMZN", "AI gate", F062)])
        vs = w.verdicts()
        self.assertEqual({k: v["decision"] for k, v in vs.items() if k not in ("B:F061:AMZN", "B:F062:AMZN")},
                         {k: "left" for k in LEFT_5_OCT})
        for key, why in LEFT_5_OCT.items():
            self.assertTrue(vs[key]["why"].startswith(why), f"{key}: {vs[key]['why']}")

    def test_never_approves_meta_share(self):
        """F046-2 (Meta's share), which Seb marked 'reader wrong': left for Seb on the code's checks alone (a publication
        date, and 100 times off), and never put to DeepSeek, even with every answer 'yes'"""
        w, ask = self.work(runs=False), Asker()               # without the runs, the later check that agreed does not close it
        w.gate("--from-line", "0", "--record", "--ask", ask=ask)
        self.assertNotIn("B:F046:META_share", [d["key"] for d in w.decisions()])
        self.assertFalse(any("share" in q for q in ask.asked))
        v = w.verdicts()["B:F046:META_share"]
        self.assertEqual(v["decision"], "left")
        failed = [t for ok, t in v["checks"] if not ok]
        self.assertTrue(failed[0].startswith("the paper's date (2026-02-19) is not the end of a reporting period"))
        self.assertTrue(any(t.startswith("100 times") for t in failed))

    def test_with_sebs_decisions(self):
        """with Seb's decisions of 5 Oct, the gate approves nothing: his figures are closed, and the one still open
        (F141-1, a year-end balance against a quarter's) is left for him"""
        w, ask = self.work(decisions=SEB_5_OCT, full=True), Asker()
        w.gate("--from-line", "0", "--record", "--ask", ask=ask)
        self.assertEqual(ask.asked, [])
        self.assertEqual(len(w.decisions()), len(SEB_5_OCT))
        self.assertEqual({k: v["decision"] for k, v in w.verdicts().items()}, {"B:F141:CRWV_te": "left"})

    def test_deepseek_no_leaves_it(self):
        """a 'no' from DeepSeek leaves the figure for Seb, with DeepSeek's reason"""
        w = self.work()
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker(basis="no", why="a different scope"))
        self.assertEqual(w.decisions(), [])
        v = w.verdicts()["B:F061:AMZN"]
        self.assertEqual(v["why"], "DeepSeek: same measure yes, same basis no (a different scope)")

    def test_answers_saved_and_replayed_free(self):
        """every answer is saved with its question; a replay gives the same decisions without asking; a changed
        question is never answered from an old answer"""
        w = self.work()
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        saved = sorted(os.listdir(w.paths["answers_dir"]))
        self.assertEqual(len(saved), 2)
        rec = load(os.path.join(w.paths["answers_dir"], saved[0]))
        self.assertEqual((rec["system"], rec["answer"]["same_measure"]), (G.SYSTEM, "yes"))
        self.assertIn(G.QUESTION, rec["user"])
        again = self.work()
        again.paths["answers_dir"] = w.paths["answers_dir"]
        code, printed = again.gate("--from-line", "0", "--record")          # no --ask: saved answers only
        self.assertEqual(code, 0, printed)
        self.assertEqual([d["key"] for d in again.decisions()], ["B:F061:AMZN", "B:F062:AMZN"])
        changed = self.work()
        changed.paths["answers_dir"] = w.paths["answers_dir"]
        changed.add_proposal(406, fragments=[{"role": "value", "text": "Leases not yet commenced | 137,214", "verified": True}])
        v = changed.verdicts(from_line=0)["B:F061:AMZN"]            # the same figure, quoted in other words
        self.assertEqual(v["why"], "no saved answer, and DeepSeek was not asked (a plan or a dry run)")

    def test_plan_writes_nothing(self):
        """without --record it asks nothing and writes nothing"""
        w = self.work()
        before = sorted(os.listdir(w.reader))
        code, printed = w.gate("--from-line", "0", ask=Asker())
        self.assertEqual(code, 0)
        self.assertIn("plan: nothing asked, nothing written", printed)
        self.assertEqual(sorted(os.listdir(w.reader)), before)
        self.assertFalse(os.path.exists(w.paths["answers_dir"]) or os.path.exists(w.paths["reports_dir"]))

    def test_record_needs_from_line(self):
        """--record and --ask need --from-line: the gate decides only what a press found"""
        w = self.work()
        for argv in (["--record"], ["--record", "--ask"], ["--from-line", "0", "--ask"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                w.gate(*argv)

    def test_only_what_the_press_found(self):
        """figures first proposed before --from-line are not the gate's: a figure found again by the press is not new"""
        w = self.work()
        self.assertEqual(w.verdicts(from_line=w.last_line()), {})
        n = w.add_proposal(406)                                   # the press finds Amazon's June figure again
        self.assertEqual(w.verdicts(from_line=n - 1), {})
        n = w.add_proposal(406, new_value=150.1, new_period_end="2026-09-30")    # and a new September figure
        self.assertEqual(list(w.verdicts(from_line=n - 1)), ["B:F061:AMZN"])

    def test_appends_only(self):
        """the gate adds lines to decisions.jsonl and never changes an earlier one"""
        w = self.work(decisions=[d for d in SEB_5_OCT if d["key"] == "B:F059:ORCL"])
        path = os.path.join(w.reader, "decisions.jsonl")
        before = read(path)
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        after = read(path)
        self.assertTrue(after.startswith(before))
        new = [json.loads(x) for x in after[len(before):].splitlines()]
        self.assertEqual([d["key"] for d in new], ["B:F061:AMZN", "B:F062:AMZN"])
        d = new[0]
        self.assertEqual((d["decision"], d["decided_by"], d["figure"], d["type"], d["row"]), ("approved", "AI gate", [137.214, "2026-06-30"],
                                                                                            "Figure update", "F061"))
        self.assertEqual(len(d["checks"]), 9)
        self.assertTrue(d["answer"].endswith(".json") and d["gate_run"].startswith("gate "))


# ------------------------------------------------------------------------------------------
# Seb's decisions always win
# ------------------------------------------------------------------------------------------
class GateNeverTouches(Case):
    def test_a_figure_seb_decided_or_reopened(self):
        """a figure Seb reopened is his: the gate leaves it ('yours'), and asks nothing"""
        w, ask = self.work(), Asker()
        w.add_decisions([dict(decision="kept paper", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00"),
                         dict(decision="reopen", key="B:F061:AMZN", lines=F061, decided_at="2026-10-02T09:00:00")])
        v = w.verdicts(ask=ask)["B:F061:AMZN"]
        self.assertEqual(v["decision"], "yours")
        self.assertTrue(v["why"].startswith("you decided this figure ('reopen'"))
        self.assertNotIn("The paper's line: Amazon leases not yet commenced", ask.asked)

    def test_newest_sheet_not_yet_recorded(self):
        """the items on Seb's newest sheet are his until he records it; then the gate may decide them"""
        w = self.work()
        self.assertEqual(w.approvals("make")[0], 0)
        sheet = os.listdir(w.paths["sheets_dir"])[0]
        self.assertEqual(w.verdicts()["B:F061:AMZN"]["decision"], "yours")
        self.assertIn(sheet, w.verdicts()["B:F061:AMZN"]["why"])
        lead = next(it for it in SH.items(O.load(w.reader)[0], LEDGER_BY_ID) if it["type"] == "Lead")
        w.add_decisions([dict(decision="dismissed", key=lead["key"], lines=lead["lines"], sheet=sheet, decided_at="2026-10-07T08:00:00")])
        self.assertEqual(w.verdicts(ask=Asker())["B:F061:AMZN"]["decision"], "approved")

    def test_report_says_which_sheet_it_held_back(self):
        """the report says whether it found a sheet of Seb's, and whether it held back its items: a sheet on his Mac
        that was never pushed cannot be seen"""
        w = self.work()
        code, printed = w.gate("--from-line", "0")
        self.assertEqual(code, 0, printed)
        self.assertIn("No sheet of yours is in approvals/sheets/. The gate holds back only a sheet it can see", printed)
        self.assertEqual(w.approvals("make")[0], 0)
        sheet = os.listdir(w.paths["sheets_dir"])[0]
        out = os.path.join(w.root, "gate.json")
        code, printed = w.gate("--from-line", "0", "--json", out)
        self.assertIn(f"Your newest sheet, {sheet}, is not recorded yet: everything on it is left to you.", printed)
        self.assertEqual(load(out)["sheet"], f"Your newest sheet, {sheet}, is not recorded yet: everything on it is left to you.")

    def test_unreadable_newest_sheet_stops(self):
        """if the newest sheet cannot be read, the gate decides nothing"""
        w = self.work()
        with open(os.path.join(w.paths["sheets_dir"], "Approvals 2026-10-07 08.00.xlsx"), "w") as f:
            f.write("not a spreadsheet")
        code, printed = w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        self.assertEqual(code, 1)
        self.assertIn("cannot be read", printed)
        self.assertEqual(w.decisions(), [])

    def test_unreadable_sheet_beside_an_older_one_stops(self):
        """an unreadable sheet stops the gate even when an older, recorded sheet is there too"""
        w = self.work()
        self.assertEqual(w.approvals("make", now=NOW - dt.timedelta(days=2))[0], 0)
        old = os.listdir(w.paths["sheets_dir"])[0]
        lead = next(it for it in SH.items(O.load(w.reader)[0], LEDGER_BY_ID) if it["type"] == "Lead")
        w.add_decisions([dict(decision="dismissed", key=lead["key"], lines=lead["lines"], sheet=old, decided_at="2026-10-06T08:00:00")])
        with open(os.path.join(w.paths["sheets_dir"], "Approvals 2026-10-07 08.00.xlsx"), "w") as f:
            f.write("half saved")
        code, printed = w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        self.assertEqual(code, 1)
        self.assertIn("Approvals 2026-10-07 08.00.xlsx cannot be read", printed)
        self.assertEqual(len(w.decisions()), 1)

    def test_wrong_pointer_stops(self):
        """a decision pointing at another finding's lines (two copies merged by hand) stops the gate"""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F062, figure=[130.065, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        code, printed = w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        self.assertEqual(code, 1)
        self.assertIn("proposed.jsonl line 146 is F062 AMZN", printed)

    def test_sebs_sheet_decision_wins(self):
        """Seb decides an item on a sheet made before the gate approved it: his decision is recorded (not skipped as
        'decided again after the sheet') and is the one in force"""
        w = self.work()
        its = SH.items(O.load(w.reader)[0], LEDGER_BY_ID)
        lead = next(it for it in its if it["type"] == "Lead")
        f061 = next(it for it in its if it["key"] == "B:F061:AMZN")
        path = os.path.join(w.paths["sheets_dir"], "first.xlsx")
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", "")})
        self.assertEqual(w.approvals("record", "--sheet", path, now=NOW - dt.timedelta(hours=20))[0], 0)
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        self.assertTrue(O.by_gate(w.entry("B:F061:AMZN")["decision_made"]))
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its),
                 filled={lead["id"]: ("dismiss", ""), f061["id"]: ("keep paper", "the paper's date is deliberate")})
        code, printed = w.approvals("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(code, 0, printed)
        self.assertNotIn("decided again after this sheet was made", printed)
        e = w.entry("B:F061:AMZN")
        self.assertEqual((e["decision_made"]["decision"], O.by_gate(e["decision_made"])), ("kept paper", False))
        self.assertEqual([d["decision"] for d in e["decisions"]], ["kept paper"])   # his decision replaces the gate's
        self.assertIsNone(e["to_do"])
        self.assertNotIn("**F061**", read(os.path.join(w.out, "Changes to make.md")))

    def test_sebs_approval_of_the_same_figure_is_his(self):
        """Seb approving a figure the gate approved records his own approval (it is not 'already recorded')"""
        w = self.work()
        its = SH.items(O.load(w.reader)[0], LEDGER_BY_ID)
        lead = next(it for it in its if it["type"] == "Lead")
        f061 = next(it for it in its if it["key"] == "B:F061:AMZN")
        path = os.path.join(w.paths["sheets_dir"], "first.xlsx")
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", "")})
        w.approvals("record", "--sheet", path, now=NOW - dt.timedelta(hours=20))
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", ""), f061["id"]: ("approve", "")})
        w.approvals("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
        e = w.entry("B:F061:AMZN")
        self.assertFalse(O.by_gate(e["decision_made"]))
        self.assertFalse(O.by_gate(e["to_do"]["decision"]))

    def test_plain_reopen_of_sebs_decision_opens_it(self):
        """Seb approves (or keeps the paper on) a figure the gate approved, then reopens it: the item is open again, and
        the gate's approval does not come back"""
        for choice in ("approve", "keep paper"):
            with self.subTest(choice=choice):
                w = self.work()
                its = SH.items(O.load(w.reader)[0], LEDGER_BY_ID)
                lead = next(it for it in its if it["type"] == "Lead")
                f061 = next(it for it in its if it["key"] == "B:F061:AMZN")
                path = os.path.join(w.paths["sheets_dir"], "first.xlsx")
                SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", "")})
                w.approvals("record", "--sheet", path, now=NOW - dt.timedelta(hours=20))
                w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
                SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", ""), f061["id"]: (choice, "")})
                w.approvals("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
                code, printed = w.approvals("reopen", "F061", "--yes", now=NOW + dt.timedelta(hours=2))
                self.assertEqual(code, 0, printed)
                e = w.entry("B:F061:AMZN")
                self.assertEqual((e.get("decision_made"), e.get("earlier_decision"), e["decisions"], e["to_do"]), (None, None, [], None))
                self.assertIn(e, O.load(w.reader)[0]["open"])


# ------------------------------------------------------------------------------------------
# The code checks, one at a time, on later findings
# ------------------------------------------------------------------------------------------
class GateChecks(Case):
    def verdict(self, w, base, key, ask=None, **changes):
        n = w.add_proposal(base, **changes)
        return w.verdicts(from_line=n - 1, ask=ask or Asker())[key]

    def test_year_end_to_year_end(self):
        """a year-end balance is replaced only by a year-end balance: Oracle's next 10-K passes, its 10-Q does not"""
        w = self.work()
        v = self.verdict(w, 411, "B:F059:ORCL", new_value=300.0, new_period_end="2027-05-31")
        self.assertEqual(v["decision"], "approved", v["why"])
        self.assertIn("the same basis: a balance, year end to year end", [t for _, t in v["checks"]])
        v = self.verdict(self.work(), 411, "B:F059:ORCL", new_value=300.0, new_period_end="2026-11-30")
        self.assertTrue(v["why"].startswith("the paper's figure is a year-end balance"))

    def test_company_disclosure_never(self):
        """a figure from a call or a newsroom (not www.sec.gov) is VERIFIED — company disclosure: never approved"""
        v = self.verdict(self.work(), 406, "B:F061:AMZN", new_value=150.1, new_period_end="2026-09-30",
                         link="https://ir.aboutamazon.com/q3-2026-transcript")
        self.assertEqual(v["why"], "VERIFIED — company disclosure (ir.aboutamazon.com), not primary")

    def test_period_type(self):
        """a figure for another kind of period (a quarter's flow against a balance) is not on the same basis"""
        v = self.verdict(self.work(), 406, "B:F061:AMZN", new_value=150.1, new_period_end="2026-09-30", new_period_type="3m")
        self.assertEqual(v["why"], "a '3m' figure against the paper's 'instant'")

    def test_size_against_the_last_approval(self):
        """the size is judged against the last approved figure: 200 is 1.46 times Seb's 137.214 (passes), 300 is 2.19
        times (left), although both are within or near double the paper's 106.347"""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        v = self.verdict(w, 406, "B:F061:AMZN", new_value=200.0, new_period_end="2026-09-30")
        self.assertEqual(v["decision"], "approved", v["why"])
        self.assertIn("1.46 times the last approved figure (half to double allowed)", [t for _, t in v["checks"]])
        w2 = self.work()
        w2.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        v = self.verdict(w2, 406, "B:F061:AMZN", new_value=300.0, new_period_end="2026-09-30")
        self.assertEqual(v["why"], "2.19 times the last approved figure: outside half to double")

    def test_never_the_same_or_an_earlier_period_than_decided(self):
        """after Seb approves a figure for a period, the gate never decides another value for that period, nor an earlier
        period: only a later one"""
        approved_june = dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")
        w = self.work()
        w.add_decisions([approved_june])
        v = self.verdict(w, 406, "B:F061:AMZN", new_value=137.0, new_period_end="2026-06-30")
        self.assertEqual(v["why"], "not after the period already decided (2026-06-30): 2026-06-30")
        w = self.work()
        n = w.add_proposal(406, new_value=150.1, new_period_end="2026-09-30")
        w.add_decisions([dict(approved_june, lines=F061 + [n], figure=[150.1, "2026-09-30"])])
        v = self.verdict(w, 406, "B:F061:AMZN", new_value=137.5, new_period_end="2026-06-30", found_at="2026-10-21T09:00:00")
        self.assertEqual(v["why"], "not after the period already decided (2026-09-30): 2026-06-30")
        v = self.verdict(w, 406, "B:F061:AMZN", new_value=160.0, new_period_end="2026-12-31", found_at="2026-10-22T09:00:00")
        self.assertEqual(v["decision"], "approved", v["why"])

    def test_a_line_checked_twice(self):
        """a line checked in documents and in the SEC's labelled data (F060-1) is left for Seb: approving one half would take
        the line off the site"""
        w = self.work(decisions=[d for d in SEB_5_OCT if d["key"] in ("B:F060:META", "A:F060:Meta leases not yet commenced")])
        v = self.verdict(w, 410, "B:F060:META", new_value=300.0, new_period_end="2026-12-31")
        self.assertTrue(v["why"].startswith("F060-1 is checked twice (claims.py F060 META; Part A: Meta leases not yet commenced)"), v["why"])

    def test_part_a_arithmetic(self):
        """a Part A figure the code works out (Oracle's quarter x 4) is arithmetic: never approved"""
        w = self.work()
        v = self.verdict(w, 1, "A:F143:Oracle revenue, latest quarter x 4", row="F143", figure="Oracle revenue, latest quarter x 4",
                         paper_value=77.4, paper_period_end="2026-08-31", new_value=84.0, new_period_end="2026-11-30", note="")
        self.assertEqual(v["why"], "worked out by the code from the SEC's labels (Oracle revenue, latest quarter x 4): arithmetic, "
                                   "which the gate never approves")

    def test_nvidia_53_week_year(self):
        """Nvidia's fiscal 2027 has 53 weeks (25 Jan 2026 to 31 Jan 2027): its fourth quarter runs 14 weeks, against the
        paper's 13, so it is left for Seb; its third quarter is on the same basis"""
        base = dict(row="F002", figure="Nvidia revenue, quarter", paper_value=96.221, paper_period_end="2026-07-26", note="",
                    filing="https://www.sec.gov/Archives/edgar/data/1045810/000104581027000010/")
        w = self.work()
        v = self.verdict(w, 1, "A:F002:Nvidia revenue, quarter", new_value=110.0, new_period_end="2027-01-31", **base)
        self.assertTrue(v["why"].startswith("a fiscal year of 53 weeks ends on 2027-01-31"), v["why"])
        v = self.verdict(self.work(), 1, "A:F002:Nvidia revenue, quarter", new_value=105.0, new_period_end="2026-10-25", **base)
        self.assertEqual(v["decision"], "approved", v["why"])
        self.assertTrue(G.long_year("NVDA", G.day("2027-01-31")) and not G.long_year("NVDA", G.day("2026-01-25")))

    def test_clean_record(self):
        """a rule Seb ever marked 'reader wrong' or 'keep paper' is never approved by the gate again"""
        for decision in ("reader wrong", "kept paper"):
            with self.subTest(decision=decision):
                w = self.work()
                w.add_decisions([dict(decision=decision, key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"],
                                      decided_at="2026-10-01T09:00:00")])
                v = self.verdict(w, 406, "B:F061:AMZN", new_value=150.1, new_period_end="2026-09-30")
                self.assertEqual(v["why"], f"you marked this rule '{decision}' on 2026-10-01")

    def test_clean_record_after_an_undo(self):
        """once Seb has taken back a gate approval of a rule (reopen --gate), the gate never approves that rule again"""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        n = w.add_proposal(406, new_value=150.1, new_period_end="2026-09-30")
        w.gate("--from-line", str(n - 1), "--record", "--ask", ask=Asker())
        self.assertEqual(w.approvals("reopen", "F061", "--gate", "--yes")[0], 0)
        v = self.verdict(w, 406, "B:F061:AMZN", new_value=160.0, new_period_end="2026-12-31", found_at="2026-10-21T09:00:00")
        self.assertEqual(v["decision"], "left")
        self.assertTrue(v["why"].startswith("you reopened this rule after the gate decided it"), v["why"])

    def test_question_shows_the_rule_and_the_quotes(self):
        """DeepSeek's question holds the paper's line, the reader's rule and only the verified quotes"""
        ask = Asker()
        w = self.work()
        self.verdict(w, 406, "B:F061:AMZN", ask=ask, new_value=150.1, new_period_end="2026-09-30")
        rec = load(os.path.join(w.paths["answers_dir"], os.listdir(w.paths["answers_dir"])[0]))
        q = rec["user"]
        for words in ("The paper's line: Amazon leases not yet commenced", "Printed in the paper as: $106bn",
                      "measure \"the 'Leases not yet commenced' total in the commitments table\"",
                      "The proposed update: 150.1 $bn for the period ending 2026-09-30 (instant), from 10-Q filed 2026-07-31",
                      "- value: \"Leases not yet commenced |", G.QUESTION):
            self.assertIn(words, q)


# ------------------------------------------------------------------------------------------
# Undoing the gate, and what the lists say
# ------------------------------------------------------------------------------------------
class GateUndo(Case):
    def approved_twice(self):
        """Seb approved Amazon's June figure; the gate approved its September figure."""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        n = w.add_proposal(406, new_value=150.1, new_period_end="2026-09-30")
        w.gate("--from-line", str(n - 1), "--record", "--ask", ask=Asker())
        self.assertTrue(O.by_gate(w.entry("B:F061:AMZN")["decision_made"]))
        return w, n

    def test_reopen_gate_takes_back_only_the_gates(self):
        """reopen F061 --gate takes back the gate's approval only: Seb's approval of June is the one in force again, and
        the gate never decides the September figure again"""
        w, n = self.approved_twice()
        code, printed = w.approvals("reopen", "F061", "--gate", "--yes")
        self.assertEqual(code, 0, printed)
        e = w.entry("B:F061:AMZN")
        self.assertEqual((e.get("decision_made"), e["earlier_decision"]["figure"]), (None, [137.214, "2026-06-30"]))
        self.assertEqual(w.decisions()[-1]["only"], "AI gate")
        ask = Asker()
        self.assertEqual(w.verdicts(from_line=n - 1, ask=ask)["B:F061:AMZN"]["decision"], "yours")
        self.assertEqual(ask.asked, [])

    def test_reopen_gate_takes_back_every_gate_approval(self):
        """two gate approvals (September, then December), or one held while a later figure waits: reopen --gate takes
        back all of them, and nothing of the gate's is published any more"""
        w, n = self.approved_twice()
        n2 = w.add_proposal(406, new_value=160.0, new_period_end="2026-12-31", found_at="2026-10-21T09:00:00")
        w.gate("--from-line", str(n2 - 1), "--record", "--ask", ask=Asker())
        self.assertEqual(sum(O.by_gate(d) for d in w.entry("B:F061:AMZN")["decisions"]), 2)
        code, printed = w.approvals("reopen", "F061", "--gate", "--yes")
        self.assertEqual(code, 0, printed)
        e = w.entry("B:F061:AMZN")
        self.assertFalse(any(O.by_gate(d) for d in e["decisions"]))
        self.assertEqual(e["earlier_decision"]["figure"], [137.214, "2026-06-30"])
        w2, _ = self.approved_twice()
        w2.add_proposal(406, new_value=400.0, new_period_end="2026-12-31", found_at="2026-10-21T09:00:00")   # left: outside double
        self.assertTrue(O.by_gate(w2.entry("B:F061:AMZN")["earlier_decision"]))
        code, printed = w2.approvals("reopen", "F061", "--gate", "--yes")
        self.assertEqual(code, 0, printed)
        self.assertFalse(any(O.by_gate(d) for d in w2.entry("B:F061:AMZN")["decisions"]))

    def test_old_to_do_row_cannot_close_a_newer_gate_approval(self):
        """'done' on a to-do row of a sheet made before the gate approved a newer figure is skipped, so it cannot take a
        change Seb has not seen off Changes to make"""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        its = SH.items(O.load(w.reader)[0], LEDGER_BY_ID)
        todo = next(it for it in its if it["type"] == "To do" and it["key"] == "B:F061:AMZN")
        path = os.path.join(w.paths["sheets_dir"], "old.xlsx")
        SH.write(path, its, NOW - dt.timedelta(days=3), AP.counts_line(its), filled={})
        n = w.add_proposal(406, new_value=150.1, new_period_end="2026-09-30")
        lead = next(it for it in its if it["type"] == "Lead")
        w.add_decisions([dict(decision="dismissed", key=lead["key"], lines=lead["lines"], sheet="old.xlsx", decided_at="2026-10-05T09:00:00")])
        w.gate("--from-line", str(n - 1), "--record", "--ask", ask=Asker())
        SH.write(path, its, NOW - dt.timedelta(days=3), AP.counts_line(its), filled={todo["id"]: ("done", ""), lead["id"]: ("dismiss", "")})
        code, printed = w.approvals("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(code, 0, printed)
        self.assertIn("the AI gate approved a newer figure after this sheet was made", printed)
        self.assertIn("**F061**", read(os.path.join(w.out, "Changes to make.md")))
        self.assertTrue(O.by_gate(w.entry("B:F061:AMZN")["to_do"]["decision"]))

    def test_reopen_gate_without_a_gate_decision(self):
        """reopen --gate on a row the gate has not decided changes nothing"""
        w = self.work()
        w.add_decisions([dict(decision="approved", key="B:F061:AMZN", lines=F061, figure=[137.214, "2026-06-30"], decided_at="2026-10-01T09:00:00")])
        before = w.decisions()
        code, printed = w.approvals("reopen", "F061", "--gate", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("No item the AI gate decided matches 'F061'", printed)
        self.assertEqual(w.decisions(), before)

    def test_plain_reopen_still_works(self):
        """plain reopen still takes back the latest decision, the gate's included; the gate leaves the figure to Seb after"""
        w, n = self.approved_twice()
        self.assertEqual(w.approvals("reopen", "F061", "--yes")[0], 0)
        e = w.entry("B:F061:AMZN")
        self.assertIsNone(e.get("decision_made"))
        self.assertEqual(w.verdicts(from_line=n - 1)["B:F061:AMZN"]["decision"], "yours")

    def test_gate_only_reopen_keeps_a_later_decision_of_sebs(self):
        """a reopen marked 'only': 'AI gate' never takes back a decision of Seb's made after the gate's"""
        d = lambda n, decision, **kw: dict(dict(_n=n, decision=decision, lines=[1]), **kw)
        by_line = O._decisions_by_line([d(1, "approved", decided_by="AI gate"), d(2, "kept paper"), d(3, "reopen", only="AI gate")])
        self.assertEqual([x["decision"] for x in O.active_decisions([1], by_line)], ["kept paper"])
        by_line = O._decisions_by_line([d(1, "approved"), d(2, "approved", decided_by="AI gate"), d(3, "done"), d(4, "reopen", only="AI gate")])
        self.assertEqual([x["_n"] for x in O.active_decisions([1], by_line)], [1])
        by_line = O._decisions_by_line([d(1, "approved"), d(2, "done"), d(3, "approved", decided_by="AI gate"),
                                        d(4, "approved", decided_by="AI gate"), d(5, "reopen", only="AI gate")])
        self.assertEqual([x["_n"] for x in O.active_decisions([1], by_line)], [1, 2])


class GateLists(Case):
    def test_lists_mark_the_gate(self):
        """Changes to make, the open list and the sheet's to-do row say the gate approved it, and how to undo it"""
        w = self.work()
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        changes = read(os.path.join(w.out, "Changes to make.md"))
        self.assertIn("Approved by the AI gate 2026-10-07 (to undo: approvals.py reopen F061 --gate)", changes)
        self.assertIn("Those the AI gate approved say so.", changes)
        self.assertIn("F061 AMZN: approved by the AI gate on 07 Oct 2026 09:00", read(os.path.join(w.reader, "Open proposals.md")))
        todo = next(it for it in SH.items(O.load(w.reader)[0], LEDGER_BY_ID) if it["type"] == "To do" and it["key"] == "B:F061:AMZN")
        self.assertIn("The AI gate approved this on 2026-10-07", todo["note"])
        self.assertIn("reopen F061 --gate", todo["note"])
        self.assertEqual(len(os.listdir(w.paths["reports_dir"])), 1)

    def test_json_for_the_popup(self):
        """--json gives each figure as the pop-up will show it: ID, the paper's figure, the new one, the source and the
        gate's decision with its reason"""
        w = self.work()
        out = os.path.join(w.root, "gate.json")
        w.gate("--from-line", "0", "--record", "--ask", "--json", out, ask=Asker())
        figs = {f["key"]: f for f in load(out)["figures"]}
        f = figs["B:F061:AMZN"]
        self.assertEqual((f["figure_id"], f["paper"], f["new"], f["period_end"], f["document"], f["source"], f["decision"]),
                         ("F061-1", "$106bn", "$137.214bn", "2026-06-30", "10-Q filed 2026-07-31", "Part B", "approved"))
        a = figs["A:F015:Oracle cash and cash equivalents"]
        self.assertEqual((a["source"], a["decision"]), ("Part A", "left"))
        self.assertTrue(a["why"].startswith("the reader's note asks for care"))

    def test_suggestions_scored_against_sebs_decisions_only(self):
        """the tool's suggestions are scored against Seb's sheet decisions only: his decisions on figures the gate had
        approved count, the gate's own approvals never do"""
        w = self.work()
        its = SH.items(O.load(w.reader)[0], LEDGER_BY_ID)
        lead = next(it for it in its if it["type"] == "Lead")
        f061, f062 = (next(it for it in its if it["key"] == k) for k in ("B:F061:AMZN", "B:F062:AMZN"))
        self.assertEqual((AP.SG.suggest(f061)[0], AP.SG.suggest(f062)[0]), ("approve", "approve"))
        path = os.path.join(w.paths["sheets_dir"], "first.xlsx")
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its), filled={lead["id"]: ("dismiss", "")})
        w.approvals("record", "--sheet", path, now=NOW - dt.timedelta(hours=20))
        w.gate("--from-line", "0", "--record", "--ask", ask=Asker())
        self.assertEqual(sum(O.by_gate(d) for d in w.decisions()), 2)
        SH.write(path, its, NOW - dt.timedelta(days=1), AP.counts_line(its),
                 filled={lead["id"]: ("dismiss", ""), f061["id"]: ("keep paper", ""), f062["id"]: ("approve", "")})
        code, printed = w.approvals("record", "--sheet", path, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(code, 0, printed)
        reports = sorted(os.listdir(os.path.join(w.out, "decided")))
        report = read(os.path.join(w.out, "decided", reports[-1]))
        self.assertIn("2 decisions recorded from `first.xlsx`.", report)
        self.assertIn("The tool's own suggestions (not shown on the sheet) agreed with you on 1 of the 2 it made; it made none for 0.", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
