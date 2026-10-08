"""The tabular review (step 5, V1): review/build.py turns the watch list, the reader's findings and Seb's decisions into
one file, review.json, for the website. Only a finding with an approval in force is published, only for a figure in the
paper, and only the fields the README allows. Small invented fixtures (marked PRIVATE where a field must never be
published) test each rule; a copy of the data of 5 Oct 2026 tests the exact result; the live files are tested only for
rules that must always hold, so these tests survive Seb's next approvals."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

TEST = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(os.path.dirname(TEST))
BUILD = os.path.join(AGENT, "review", "build.py")
README = os.path.join(AGENT, "review", "README.md")
FIX = os.path.join(TEST, "fixtures", "review_2026-10-05")


def load_builder():
    if not os.path.exists(BUILD):
        return None
    spec = importlib.util.spec_from_file_location("review_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


b = load_builder()
SEC = "https://www.sec.gov/Archives/edgar/data/1326801/000162828026050705/meta-20260630.htm"


class Case(unittest.TestCase):
    def setUp(self):
        if b is None:
            self.fail("review/build.py does not exist yet")


# ------------------------------------------------------------------------------------------
# Small fixtures (invented). Every field that must never be published says PRIVATE.
# ------------------------------------------------------------------------------------------
def ledger(rid, section="Intro", type_="Reported", built_from="", also="", link="https://www.sec.gov/a.htm"):
    return {"ID": rid, "Paper section": section, "What the paper says": f"what the paper says about {rid}", "Value": None,
            "Unit": "$bn", "Period / as of": "", "Type": type_, "Source ID": "PRIVATE source id", "Paper fn": "12",
            "Primary document": f"primary document of {rid}", "Link": link, "Also used in": also,
            "What would supersede it": "PRIVATE supersede", "Next expected": "PRIVATE next expected",
            "How to extract": "PRIVATE how to extract", "Automation": "PRIVATE automation", "Impact": "PRIVATE impact",
            "Built from": built_from, "Status at baseline": "PRIVATE status at baseline", "Notes": "PRIVATE ledger note"}


def line(fid, printed, value, unit="$bn", period="31 Mar 2026", code=None):
    return {"Figure ID": fid, "Ledger ID": fid.split("-")[0], "What the figure is": f"label {fid}",
            "As the paper prints it": printed, "Value": value, "Unit": unit, "Period / as of": period,
            "Value from": "PRIVATE value from", "Checked by code as": code, "Notes": "PRIVATE figures note"}


FRAGMENTS = [{"role": "context", "text": "In addition to the leases on our balance sheet, we have leases not yet commenced.", "verified": True},
             {"role": "value", "text": "These lease obligations were approximately $ 120 billion.", "verified": True},
             {"role": "value", "text": "An unverified sentence.", "verified": False},
             {"role": "other", "text": "A sentence in another role.", "verified": True},
             {"role": "column", "text": "June 30, 2026 | December 31, 2025", "verified": True}]


def part_b(n, row, comp, new, new_end, paper, paper_end, found="2026-09-28T18:49:14", link=SEC, kind="newer figure", unit="$bn"):
    return {"_line": n, "found_at": found, "part": "B", "version": 2, "row": row, "component": comp, "company": "META",
            "kind": kind, "paper_value": paper, "unit": unit, "paper_period_end": paper_end, "paper_period_type": "instant",
            "new_value": new, "new_period_end": new_end, "new_period_type": "instant", "extraction": "verified",
            "paper_check": "PRIVATE paper check", "fragments": copy.deepcopy(FRAGMENTS), "document": "10-Q filed 2026-07-30",
            "link": link, "decision": "pending", "run_id": "PRIVATE run id", "note": "PRIVATE proposal note",
            "why": "PRIVATE proposal why", "topic": "PRIVATE topic", "related_rows": ["PRIVATE related row"]}


def part_a(n, row, figure, new, new_end, paper, paper_end, found="2026-09-26T15:56:37", filing="https://www.sec.gov/Archives/edgar/data/1/000/"):
    return {"_line": n, "found_at": found, "part": "A", "row": row, "figure": figure, "paper_value": paper,
            "paper_period_end": paper_end, "new_value": new, "new_period_end": new_end, "status": "NEWER FIGURE",
            "unit": "$bn", "filing": filing, "form": "10-Q", "filed": "2026-07-30", "note": "PRIVATE part A note",
            "decision": "pending"}


def arithmetic(n, row, comp, new, paper, found="2026-09-28T18:49:14"):
    return {"_line": n, "found_at": found, "part": "B", "version": 2, "row": row, "component": comp,
            "kind": "formula with newer inputs", "paper_value": paper, "unit": "% of revenue", "new_value": new,
            "formula": "CRWV_te * (1/4 - 1/6) / (CRWV_rev * 4) * 100", "inputs": {"CRWV_rev": 2.575, "CRWV_te": 33.823},
            "input_sources": {"CRWV_rev": "confirmed in 10-Q filed 2026-08-12", "CRWV_te": "newer figure in 10-Q filed 2026-08-12, 2026-06-30"},
            "decision": "pending", "run_id": "PRIVATE run id"}


def decision(n, what, lines, figure, at="2026-09-30T15:13:04"):
    return {"_n": n, "decision": what, "lines": lines, "figure": figure, "note": "PRIVATE decision note",
            "suggested": "PRIVATE suggested", "suggested_why": "PRIVATE suggested why", "sheet": "PRIVATE sheet.xlsx",
            "decided_at": at, "key": "PRIVATE key", "type": "Figure update", "what": "PRIVATE what"}


def run(ledger_rows, lines, proposals, decisions=(), runs=(), part_a_report=None, watcher_run=None, paper_differs=None):
    """Build the open list with the reader's own code, then the review."""
    result = b.O.build(copy.deepcopy(proposals), runs, decisions)
    return b.build(ledger_rows, lines, result, copy.deepcopy(proposals), runs, decisions, part_a=part_a_report,
                   watcher_run=watcher_run, paper_differs=paper_differs)


def figures_of(review):
    return {f["id"]: f for s in review["sections"] for r in s["rows"] for f in r["figures"]}


def strings(x):
    """Every string in the output, keys and values."""
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)
    elif isinstance(x, str):
        yield x


# A small paper: F001 is checked in documents (Part B), F002 in the SEC's labelled data (Part A), F003 is built from
# F001, F004 from F003, and the rest are not watched.
LEDGER = [ledger("F001", also="Intro; core model"), ledger("F002"),
          ledger("F003", "Hidden figures", "Author calculation", built_from="F001"),
          ledger("F004", "Summary", "Model output", built_from="F003", also="Test 5; Summary"),
          ledger("F005", "Funding", "Secondary research"), ledger("F006", "Funding", "Company statement"),
          ledger("F007", "Funding", "Market data"), ledger("F008", "Funding", "Press"),
          ledger("F009", "Circular Financing", "Rule"), ledger("T001", "Tracker", "Tracker", built_from="F002")]
LINES = [line("F001-1", "$100bn", 100, code="claims.py F001 META"),
         line("F002-1", "$30 billions", 30, period="Q2 2026", code="Part A: Meta capex, quarter"),
         line("F003-1", "$200bn", 200), line("F004-1", "5%", 5, unit="%"), line("F005-1", "~$9bn", 9),
         line("F006-1", "$7bn", 7), line("F007-1", "$5tn", 5, unit="$tn"), line("F008-1", "$1bn", 1),
         line("F009-1", "ten years", "ten years", unit="text"), line("T001-1", None, None)]
NEWER = part_b(1, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31")
DIFFERS = part_a(2, "F002", "Meta capex, quarter", 31.078, "2026-06-30", 30, "2026-06-30")
APPROVALS = [decision(1, "approved", [1], [120.0, "2026-06-30"]), decision(2, "approved", [2], [31.078, "2026-06-30"])]


class States(Case):
    def setUp(self):
        super().setUp()
        self.review, self.notes = run(LEDGER, LINES, [NEWER, DIFFERS], APPROVALS)
        self.f = figures_of(self.review)

    def test_each_state(self):
        """a newer figure, a same-period difference, the rows built from them, and lines not watched each get their state"""
        self.assertEqual({k: v["state"] for k, v in self.f.items()},
                         {"F001-1": "newer", "F002-1": "differs", "F003-1": "recalc", "F004-1": "recalc", "F005-1": "not_watched",
                          "F006-1": "not_watched", "F007-1": "not_watched", "F008-1": "not_watched", "F009-1": "not_watched",
                          "T001-1": "recalc"})

    def test_newer_shows_the_latest_and_the_evidence(self):
        """Newer: the approved figure beside the paper's, the document, its link, and the verified quote in order"""
        f = self.f["F001-1"]
        self.assertEqual(f["paper"]["shown"], "$100bn")
        self.assertEqual(f["latest"], {"shown": "$120bn", "value": 120.0, "period_end": "2026-06-30"})
        ev = f["evidence"]
        self.assertEqual((ev["source"], ev["basis"], ev["document"], ev["link"]), ("Part B", "VERIFIED — primary", "10-Q filed 2026-07-30", SEC))
        self.assertEqual(ev["quote"], [{"role": "context", "text": FRAGMENTS[0]["text"]}, {"role": "value", "text": FRAGMENTS[1]["text"]},
                                       {"role": "column", "text": FRAGMENTS[4]["text"]}])
        self.assertEqual(f["affects"]["text"], "core model; F003; F004 (Test 5)")

    def test_paper_differs_keeps_the_papers_figure(self):
        """Paper differs: the Paper column keeps the paper's figure; Latest is the document's figure for the same period"""
        f = self.f["F002-1"]
        self.assertEqual(f["paper"]["shown"], "$30 billions")
        self.assertEqual(f["latest"]["shown"], "$31.078bn")
        self.assertEqual(f["evidence"]["how"], "From the SEC's labelled data (no quoted text)")
        self.assertEqual((f["evidence"]["document"], f["evidence"]["link_text"], f["evidence"]["basis"]),
                         ("10-Q filed 2026-07-30", "SEC filing index", "VERIFIED — primary"))
        self.assertNotIn("quote", f["evidence"])

    def test_recalculation_is_transitive(self):
        """a row built from a changed row needs recalculating, and so does a row built from that one"""
        self.assertEqual(self.f["F003-1"]["recalc"]["path"], "F003 ← F001")
        self.assertEqual(self.f["F004-1"]["recalc"]["path"], "F004 ← F003 ← F001")
        self.assertEqual(self.f["T001-1"]["recalc"]["path"], "T001 ← F002")
        self.assertEqual(self.f["F003-1"]["paper"]["shown"], "$200bn")              # the value is unchanged
        self.assertEqual(self.review["counts"]["filters"], {"all": 10, "changes": 2, "recalc": 3})

    def test_not_watched_reasons(self):
        """each line not checked by code says why, from the ledger's Type; model outputs and author calculations have no dot"""
        why = {k: (v.get("dot"), v.get("reason")) for k, v in self.f.items() if not v["watched"]}
        self.assertEqual(why, {
            "F003-1": (False, "a model output (or an author calculation): not checked by the reader"),
            "F004-1": (False, "a model output (or an author calculation): not checked by the reader"),
            "F005-1": (True, "research, checked by hand"), "F006-1": (True, "no reader rule yet"),
            "F007-1": (True, "market data, refreshed by hand"), "F008-1": (True, "press"), "F009-1": (True, "a rule"),
            "T001-1": (True, "a tracker, not in the paper")})
        self.assertEqual(self.notes["no_reader_rule"], ["F006-1"])

    def test_paper_figure_words(self):
        """the paper's figure is the printed text; a word value stays words; a tracker says it is not in the paper"""
        self.assertEqual(self.f["F009-1"]["paper"]["shown"], "ten years")
        self.assertEqual(self.f["T001-1"]["paper"]["shown"], "tracker: not in the paper")
        lines = LINES + [line("F005-2", None, 17.4)]
        review, _ = run(LEDGER, lines, [])
        self.assertEqual(figures_of(review)["F005-2"]["paper"]["shown"], "$17.4bn (not printed in the paper)")

    def test_counts_add_up(self):
        """the counts per state and per filter match the rows"""
        c = self.review["counts"]
        self.assertEqual(c["states"], {"newer": 1, "differs": 1, "recalc": 3, "unchanged": 0, "not_watched": 5})
        self.assertEqual((c["rows"], c["figures"], c["watched_rows"], c["watched_figures"]), (10, 10, 2, 2))

    def test_section_order(self):
        """sections come in the paper's order, whatever the ledger's order; rows keep ledger order inside a section"""
        mixed = [ledger("F010", "Summary"), ledger("F011", "Intro"), ledger("F012", "Summary"), ledger("F013", "Funding")]
        review, _ = run(mixed, [line("F010-1", "1", 1), line("F011-1", "2", 2), line("F012-1", "3", 3), line("F013-1", "4", 4)], [])
        self.assertEqual([(s["name"], [r["id"] for r in s["rows"]]) for s in review["sections"]],
                         [("Intro", ["F011"]), ("Funding", ["F013"]), ("Summary", ["F010", "F012"])])
        self.assertEqual(b.SECTIONS[0], "Intro")
        self.assertEqual(b.SECTIONS[-1], "Tracker")
        self.assertEqual(len(b.SECTIONS), 15)

    def test_unknown_section_stops(self):
        """a ledger row in a section the builder does not know stops the build, naming it (no row is dropped)"""
        with self.assertRaisesRegex(ValueError, "Appendix"):
            run([ledger("F010", "Appendix")], [line("F010-1", "1", 1)], [])


class Gate(Case):
    """Only a finding with an approval in force is published."""

    def state(self, proposals, decisions):
        review, notes = run(LEDGER, LINES, proposals, decisions)
        return figures_of(review)["F001-1"], notes

    def test_reopen_takes_the_approval_back(self):
        """approved, then reopened: not shown"""
        f, _ = self.state([NEWER], [decision(1, "approved", [1], [120.0, "2026-06-30"]), decision(2, "reopen", [1], None)])
        self.assertEqual(f["state"], "unchanged")
        self.assertNotIn("latest", f)

    def test_later_run_with_a_different_figure(self):
        """approved, then a later run found another figure: the approved figure is shown, with the approved line's evidence"""
        later = part_b(5, "F001", "META", 125.0, "2026-06-30", "100", "2026-03-31", found="2026-09-30T16:43:44",
                       link="https://www.sec.gov/later.htm")
        f, notes = self.state([NEWER, later], [decision(1, "approved", [1], [120.0, "2026-06-30"])])
        self.assertEqual((f["state"], f["latest"]["value"], f["evidence"]["link"]), ("newer", 120.0, SEC))
        self.assertEqual([x["figure"] for x in notes["earlier_approval"]], ["F001-1"])

    def test_kept_paper_replaces_the_approval(self):
        """approved, then 'kept paper' (or 'reader wrong'): not shown"""
        for last in ("kept paper", "reader wrong"):
            with self.subTest(last):
                f, _ = self.state([NEWER], [decision(1, "approved", [1], [120.0, "2026-06-30"]), decision(2, last, [1], [120.0, "2026-06-30"])])
                self.assertEqual(f["state"], "unchanged")

    def test_never_shown(self):
        """dismissed, reader wrong and undecided findings are never shown"""
        for ds in ([decision(1, "dismissed", [1], [120.0, "2026-06-30"])], [decision(1, "reader wrong", [1], [120.0, "2026-06-30"])], []):
            with self.subTest(ds and ds[0]["decision"] or "undecided"):
                f, _ = self.state([NEWER], ds)
                self.assertEqual(f["state"], "unchanged")
                self.assertNotIn("evidence", f)

    def test_done_keeps_it_published(self):
        """approved, then 'done': still shown (the PDF on the site has not changed)"""
        f, _ = self.state([NEWER], [decision(1, "approved", [1], [120.0, "2026-06-30"]), decision(2, "done", [1], [120.0, "2026-06-30"])])
        self.assertEqual(f["state"], "newer")

    def test_evidence_only_from_the_decisions_own_lines(self):
        """the evidence is the newest of the decision's own lines with the decision's figure, never an undecided line"""
        older = part_b(1, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31", found="2026-09-28T12:07:16", link="https://www.sec.gov/older.htm")
        newer = part_b(3, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31", found="2026-09-28T18:49:14", link="https://www.sec.gov/newer.htm")
        undecided = part_b(4, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31", found="2026-09-30T16:43:44", link="https://www.sec.gov/undecided.htm")
        f, _ = self.state([older, newer, undecided], [decision(1, "approved", [1, 3], [120.0, "2026-06-30"])])
        self.assertEqual(f["evidence"]["link"], "https://www.sec.gov/newer.htm")

    def test_new_facts_are_ignored(self):
        """the reader's new facts (leads) never reach the review, even when Seb chose to use one"""
        lead = {"_line": 9, "found_at": "2026-09-28T18:49:14", "part": "B", "version": 2, "row": "NEW FACT", "company": "META",
                "topic": "a $105 billion lease", "fragments": [{"role": "value", "text": "We signed $105 billion of leases.", "verified": True}],
                "document": "10-Q filed 2026-07-30", "link": SEC, "decision": "pending"}
        review, _ = run(LEDGER, LINES, [lead], [decision(1, "use", [9], [None, None])])
        self.assertNotIn("105", json.dumps(review))

    def test_unknown_kind_stops(self):
        """an approved finding of a kind the builder does not know stops the build, naming its key"""
        result = {"open": [], "gone": [], "aside": [], "closed": [{"key": "X:F001:odd", "kind": "something new", "row": "F001",
                                                                   "component": "odd", "lines": [1], "decision_made": APPROVALS[0]}]}
        with self.assertRaisesRegex(ValueError, "X:F001:odd"):
            b.build(LEDGER, LINES, result, [NEWER], (), APPROVALS)


class Mapping(Case):
    """Each finding is matched to one Figures line through 'Checked by code as'."""

    def test_the_three_kinds(self):
        """figure -> claims.py ROW COMPONENT; arithmetic -> claims.py ROW formula NAME; Part A -> Part A: NAME"""
        lines = [line("F001-1", "$100bn", 100, code="claims.py F001 META"),
                 line("F001-2", "16.9% of revenue", 16.9, unit="% of revenue", code=" claims.py F001 formula bill ; "),
                 line("F002-1", "$30bn", 30, code="Part A: Meta capex, quarter")]
        ps = [NEWER, arithmetic(3, "F001", "bill", 27.3649, "16.9"), part_a(2, "F002", "Meta capex, quarter", 33.0, "2026-09-30", 30, "2026-06-30")]
        ds = APPROVALS[:1] + [decision(2, "approved", [3], [27.3649, None]), decision(3, "approved", [2], [33.0, "2026-09-30"])]
        review, notes = run(LEDGER, lines, ps, ds)
        f = figures_of(review)
        self.assertEqual({k: f[k]["state"] for k in ("F001-1", "F001-2", "F002-1")}, {"F001-1": "newer", "F001-2": "newer", "F002-1": "newer"})
        self.assertEqual(notes["declined"], [])

    def test_no_line_or_two_lines(self):
        """an approved finding matching no line, or more than one, is not published and is listed"""
        lines = LINES + [line("F001-2", "$5bn", 5, code="claims.py F001 TWICE"), line("F001-3", "$5bn", 5, code="claims.py F001 TWICE")]
        ps = [part_b(1, "F001", "NOWHERE", 6.0, "2026-06-30", "5", "2026-03-31"), part_b(3, "F001", "TWICE", 6.0, "2026-06-30", "5", "2026-03-31")]
        ds = [decision(1, "approved", [1], [6.0, "2026-06-30"]), decision(2, "approved", [3], [6.0, "2026-06-30"])]
        review, notes = run(LEDGER, lines, ps, ds)
        self.assertTrue(all(f["state"] in ("unchanged", "not_watched", "recalc") for f in figures_of(review).values()))
        self.assertEqual(sorted(x["key"] for x in notes["declined"]), ["B:F001:NOWHERE", "B:F001:TWICE"])

    def test_dates_decide_newer_or_differs(self):
        """the finding's own ISO dates: later is Newer, equal is Paper differs, earlier or missing is not published"""
        for new_end, paper_end, want in (("2026-06-30", "2026-03-31", "newer"), ("2026-03-31", "2026-03-31", "differs"),
                                         ("2025-12-31", "2026-03-31", "unchanged"), ("2026-06-30", None, "unchanged"),
                                         (None, "2026-03-31", "unchanged")):
            with self.subTest(new_end=new_end, paper_end=paper_end):
                p = part_b(1, "F001", "META", 120.0, new_end, "100", paper_end)
                review, notes = run(LEDGER, LINES, [p], [decision(1, "approved", [1], [120.0, new_end])])
                self.assertEqual(figures_of(review)["F001-1"]["state"], want)
                self.assertEqual(len(notes["declined"]), 0 if want != "unchanged" else 1)

    def test_paper_value_must_be_the_lines_value(self):
        """a finding whose paper value is not the Figures line's Value (within rounding; words ignoring case and 'the') is not published"""
        for paper, value, unit, ok in (("100", 100, "$bn", True), ("104", 103.768, "$bn", True), ("100.0", 100, "$bn", True),
                                       ("105", 103.768, "$bn", False), ("90", 100, "$bn", False),
                                       ("The First quarter of fiscal 2027", "first quarter of fiscal 2027", "text", True),
                                       ("second quarter of fiscal 2027", "first quarter of fiscal 2027", "text", False)):
            with self.subTest(paper=paper, value=value):
                new = "second quarter" if unit == "text" else 120.0
                lines = [line("F001-1", "x", value, unit=unit, code="claims.py F001 META")] + LINES[1:]
                p = part_b(1, "F001", "META", new, "2026-06-30", paper, "2026-03-31", unit=unit)
                review, notes = run(LEDGER, lines, [p], [decision(1, "approved", [1], [new, "2026-06-30"])])
                self.assertEqual(figures_of(review)["F001-1"]["state"], "newer" if ok else "unchanged")

    def test_unit_must_be_the_lines_unit(self):
        """a finding in another unit than the Figures line is not published (it would be shown in the wrong unit)"""
        p = part_b(1, "F001", "META", 0.2, "2026-06-30", "100", "2026-03-31", unit="share")
        review, notes = run(LEDGER, LINES, [p], [decision(1, "approved", [1], [0.2, "2026-06-30"])])
        self.assertEqual(figures_of(review)["F001-1"]["state"], "unchanged")
        self.assertEqual(len(notes["declined"]), 1)

    def test_arithmetic_must_have_newer_inputs(self):
        """an arithmetic finding of any kind other than 'formula with newer inputs' is not published"""
        lines = [line("F001-1", "16.9% of revenue", 16.9, unit="% of revenue", code="claims.py F001 formula bill")] + LINES[1:]
        p = arithmetic(3, "F001", "bill", 27.3649, "16.9")
        p["kind"] = "formula changed"
        d = decision(1, "approved", [3], [27.3649, None])
        result = {"open": [], "gone": [], "aside": [], "closed": [{"key": "B:F001:bill:formula", "kind": "arithmetic", "row": "F001",
                                                                   "component": "bill", "lines": [3], "decision_made": d}]}
        review, notes = b.build(LEDGER, lines, result, [p], (), [d])
        self.assertEqual(figures_of(review)["F001-1"]["state"], "unchanged")
        self.assertEqual(len(notes["declined"]), 1)


class TwoCodeNames(Case):
    """A line checked twice (in documents and in the SEC's labelled data) gets one state, one panel, one bullet."""
    LINES = [line("F001-1", "$103.77bn", 103.77, period="31 Dec 2025", code="claims.py F001 META; Part A: Meta leases not yet commenced")] + LINES[1:]

    def go(self, b_value, a_value, approve_a=True):
        ps = [part_b(1, "F001", "META", b_value, "2026-06-30", "103.77", "2025-12-31"),
              part_a(2, "F001", "Meta leases not yet commenced", a_value, "2026-06-30", 103.77, "2025-12-31")]
        ds = [decision(1, "approved", [1], [b_value, "2026-06-30"])] + ([decision(2, "approved", [2], [a_value, "2026-06-30"])] if approve_a else [])
        review, notes = run(LEDGER, self.LINES, ps, ds)
        return figures_of(review)["F001-1"], review, notes

    def test_both_approved_the_same(self):
        """both approved with the same figure: the documents' evidence, plus 'also in the SEC's labelled data'; one bullet"""
        f, review, _ = self.go(278.99, 278.99)
        self.assertEqual((f["state"], f["evidence"]["source"], f["evidence"]["also"]), ("newer", "Part B", "also in the SEC's labelled data"))
        self.assertEqual(len(review["bullets"]), 1)

    def test_they_differ_or_one_is_not_approved(self):
        """different figures, or only one approved: neither is published, and the line is listed"""
        for b_value, a_value, approve_a in ((278.99, 279.5, True), (278.99, 278.99, False)):
            with self.subTest(a_value=a_value, approve_a=approve_a):
                f, review, notes = self.go(b_value, a_value, approve_a)
                self.assertEqual(f["state"], "unchanged")
                self.assertEqual(review["bullets"], [])
                self.assertEqual([x["figure"] for x in notes["two_code_names"]], ["F001-1"])


class Recalculation(Case):
    def test_own_finding_keeps_its_state(self):
        """in a row that needs recalculating, a line with its own published finding keeps its own state"""
        lines = LINES[:2] + [line("F003-1", "$200bn", 200), line("F003-2", "$50bn", 50, code="claims.py F003 X")] + LINES[3:]
        ps = [NEWER, part_b(3, "F003", "X", 60.0, "2026-06-30", "50", "2026-03-31")]
        review, _ = run(LEDGER, lines, ps, APPROVALS[:1] + [decision(3, "approved", [3], [60.0, "2026-06-30"])])
        f = figures_of(review)
        self.assertEqual((f["F003-1"]["state"], f["F003-2"]["state"]), ("recalc", "newer"))

    def test_affects_names_models_only(self):
        """'Affects' lists the row's model entries and the rows built from it, with theirs; otherwise 'no model'"""
        review, _ = run(LEDGER, LINES, [part_a(2, "F002", "Meta capex, quarter", 33.0, "2026-09-30", 30, "2026-06-30")],
                        [decision(1, "approved", [2], [33.0, "2026-09-30"])])
        self.assertEqual(figures_of(review)["F002-1"]["affects"]["text"], "T001")
        alone = [ledger("F001", also="Intro; Tests section; $962bn; Figure 9")]
        review, _ = run(alone, LINES[:1], [NEWER], APPROVALS[:1])
        self.assertEqual(figures_of(review)["F001-1"]["affects"]["text"], "no model")

    def test_model_prefixes_against_todays_ledger(self):
        """the model list holds exactly these entries of today's 'Also used in' (sections, quotes and figures are not models)"""
        wb = b.W.read_workbook(os.path.join(FIX, "Watch list - The AI Lose Lose Race.xlsx"))
        entries = {e.strip() for r in b.W.table(wb["Ledger"]) for e in str(r.get("Also used in") or "").split(";") if e.strip()}
        self.assertEqual({e for e in entries if b.is_model(e)}, {
            "Annual charge", "Basket", "Basket revenue proxy (89,000m)", "Capital base K", "Core model: Capital Base Detail",
            "Test 1", "Test 1 exposure (Nvidia 92.5%)", "Test 2", "Test 3", "Test 3 (22% a year assumption)", "Test 3 bridge",
            "Test 3 terms", "Test 4", "Test 4 (Source inputs sheet)", "Test 5", "Test 5 ($48.5bn vs $77.4bn revenue)", "Test 6",
            "Test 6 (Oracle trailing year)", "Tests 1, 2, 3, 5", "basket revenue proxy", "basket revenue proxy (29,417m)",
            "capital base K", "core model", "core model AI Contractual Commitments", "core model Denominator",
            "website calculator", "website calculator (AI_RUNRATE = 62)", "website calculator (CLOUD = 415.22)",
            "website headline and calculator", "website model"})
        self.assertFalse(b.is_model("Tests section"))
        self.assertFalse(b.is_model("website key findings"))


class Evidence(Case):
    def test_basis_labels(self):
        """basis: an SEC link is 'VERIFIED — primary', another https host 'VERIFIED — company disclosure', no link 'not known';
        Part A is always primary and arithmetic always an estimate"""
        for link, want in ((SEC, "VERIFIED — primary"), ("https://investor.nvidia.com/q2.pdf", "VERIFIED — company disclosure"),
                           (None, "not known"), ("http://www.sec.gov/plain.htm", "not known")):
            with self.subTest(link=link):
                review, _ = run(LEDGER, LINES, [part_b(1, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31", link=link)], APPROVALS[:1])
                ev = figures_of(review)["F001-1"]["evidence"]
                self.assertEqual(ev["basis"], want)
                self.assertEqual(ev.get("link"), link if want != "not known" else None)
        review, _ = run(LEDGER, LINES, [DIFFERS], APPROVALS[1:])
        self.assertEqual(figures_of(review)["F002-1"]["evidence"]["basis"], "VERIFIED — primary")
        lines = [line("F001-1", "16.9% of revenue", 16.9, unit="% of revenue", code="claims.py F001 formula bill")] + LINES[1:]
        review, _ = run(LEDGER, lines, [arithmetic(3, "F001", "bill", 27.3649, "16.9")], [decision(1, "approved", [3], [27.3649, None])])
        ev = figures_of(review)["F001-1"]["evidence"]
        self.assertEqual((ev["basis"], ev["how"], ev["formula"]), ("ESTIMATE", "Worked out by the code", "CRWV_te * (1/4 - 1/6) / (CRWV_rev * 4) * 100"))
        self.assertEqual(ev["inputs"], [{"name": "CRWV_rev", "value": 2.575, "source": "confirmed in 10-Q filed 2026-08-12"},
                                        {"name": "CRWV_te", "value": 33.823, "source": "newer figure in 10-Q filed 2026-08-12, 2026-06-30"}])
        self.assertNotIn("link", ev)

    def test_only_https_links(self):
        """only https links reach the output: http, javascript: and anything else is dropped"""
        rows = [ledger("F001", link="http://example.com/a; https://www.sec.gov/b.htm ; javascript:alert(1); ftp://x/y; https://")] + LEDGER[1:]
        ps = [NEWER, part_a(2, "F002", "Meta capex, quarter", 31.078, "2026-06-30", 30, "2026-06-30", filing="javascript:alert(1)")]
        review, _ = run(rows, LINES, ps, APPROVALS)
        row = review["sections"][0]["rows"][0]
        self.assertEqual(row["citation"]["links"], ["https://www.sec.gov/b.htm"])
        self.assertNotIn("link", figures_of(review)["F002-1"]["evidence"])
        links = [s for s in strings(review) if "://" in s or s.lower().startswith("javascript")]
        self.assertTrue(links and all(s.startswith("https://") for s in links), links)


class Privacy(Case):
    def setUp(self):
        super().setUp()
        lines = LINES + [line("F001-2", "16.9% of revenue", 16.9, unit="% of revenue", code="claims.py F001 formula bill")]
        ps = [NEWER, DIFFERS, arithmetic(3, "F001", "bill", 27.3649, "16.9")]
        self.review, _ = run(LEDGER, lines, ps, APPROVALS + [decision(3, "approved", [3], [27.3649, None])],
                             runs=[{"found_at": "2026-09-28T18:49:14", "replay": False, "items": {"F001:META": "matches"}}],
                             watcher_run="2026-10-04T16:45:15")

    def test_every_key_is_in_the_schema(self):
        """every key in review.json is in the documented schema, and the README documents every key of the schema"""
        self.assertEqual(b.schema_problems(self.review), [])
        doc = open(README, encoding="utf-8").read()
        for obj, keys in b.SCHEMA.items():
            for k in keys:
                self.assertIn(f"`{k}`", doc, f"{obj}.{k} is not in review/README.md")

    def test_nothing_private(self):
        """no Notes, note, suggested_why, how-to-extract, automation, sheet, run id or other private field reaches the output"""
        leaked = [s for s in strings(self.review) if "PRIVATE" in s]
        self.assertEqual(leaked, [])
        self.assertNotIn("2026-09-30T15:13:04", json.dumps(self.review).replace('"built": "2026-10-04T16:45:15"', ""))

    def test_schema_check_catches_an_extra_key(self):
        """the schema check names a key that is not allowed"""
        bad = copy.deepcopy(self.review)
        bad["sections"][0]["rows"][0]["figures"][0]["note"] = "x"
        self.assertTrue(any("note" in p for p in b.schema_problems(bad)))


class LastChecked(Case):
    RUNS = [{"found_at": "2026-09-28T18:49:14", "replay": False,
             "items": {"F001:META": "matches", "F005:A": "not checked", "F006:A": "no update found", "F007:A": "newer figure",
                       "F008:A": "no change", "F009:A": "no separate figure found"}},
            {"found_at": "2026-09-30T16:43:44", "replay": False,
             "items": {"F001:META": "not checked (extraction problems)", "F005:A": "not checked", "F006:A": "historical",
                       "F008:bill": "newer, unchanged"}},
            {"found_at": "2026-10-01T10:00:00", "replay": True, "items": {"F001:META": "newer figure", "F005:A": "matches"}}]
    PART_A = "\n".join(["# Reader Part A", "", "## Newer figure published since the paper's figure", "",
                        "| Row | Figure | Paper | Paper's date | Latest | Latest date | Filing |", "|---|---|---|---|---|---|---|",
                        "| F009 | Meta leases not yet commenced | $103.77bn | 2025-12-31 | **$278.99bn** | 2026-06-30 | [10-Q](https://x) |",
                        "", "## Up to date (the paper matches the latest filing)", "",
                        "- F002 Meta capex, quarter: $30bn at 2026-06-30", "- F003 Nvidia revenue, quarter: $96.221bn at 2026-07-26",
                        "- F087 Microsoft total remaining performance obligations: $684bn at 2026-06-30", "",
                        "## Basket: other companies", "", "- NVDA: up to date"])

    def setUp(self):
        super().setUp()
        rows = [ledger(r) for r in ("F001", "F002", "F003", "F005", "F006", "F007", "F008", "F009", "F010")]
        lines = [line("F001-1", "1", 1, code="claims.py F001 META"), line("F002-1", "2", 2, code="Part A: Meta capex, quarter"),
                 line("F003-1", "3", 3, code="claims.py F003 NVDA; Part A: Nvidia revenue, quarter"),
                 line("F005-1", "5", 5, code="claims.py F005 A"), line("F006-1", "6", 6, code="claims.py F006 A"),
                 line("F007-1", "7", 7, code="claims.py F007 A"), line("F008-1", "8", 8, code="claims.py F008 formula bill"),
                 line("F009-1", "9", 9, code="claims.py F009 A; Part A: Meta leases not yet commenced"),
                 line("F010-1", "10", 10, code="claims.py F010 A")]
        self.part_a = b.parse_part_a("Part A 2026-09-30 16.41.28.md", self.PART_A)
        self.review, self.notes = run(rows, lines, [], runs=self.RUNS, part_a_report=self.part_a)
        self.f = {k: v.get("last_checked") for k, v in figures_of(self.review).items()}

    def test_part_b_verdicts(self):
        """runs.jsonl (not replays): same, nothing newer, 'not checked' skipped back to an earlier check, never checked, not known"""
        self.assertEqual(self.f["F001-1"], {"text": "checked 2026-09-28: same as the paper", "date": "2026-09-28", "by": "Part B"})
        self.assertEqual(self.f["F005-1"]["text"], "not checked yet")
        self.assertEqual(self.f["F006-1"]["text"], "checked 2026-09-30: nothing newer in the documents read")
        self.assertEqual(self.f["F008-1"]["text"], "checked 2026-09-30: same as the paper")
        self.assertEqual(self.f["F010-1"]["text"], "not known")

    def test_a_difference_not_published_shows_only_the_date(self):
        """a verdict that found a difference shows only 'last checked <date>' and is listed, never the verdict"""
        self.assertEqual(self.f["F007-1"]["text"], "last checked 2026-09-28")
        self.assertEqual([x["figure"] for x in self.notes["unpublished_differences"]], ["F007-1", "F009-1"])
        self.assertNotIn("newer figure", json.dumps(self.review))

    def test_part_a_report(self):
        """the newest Part A report's 'Up to date' lines, matched by figure name; its date is the report's"""
        self.assertEqual(self.part_a["time"], "2026-09-30T16:41:28")
        self.assertEqual(self.f["F002-1"], {"text": "checked 2026-09-30: same as the paper", "date": "2026-09-30", "by": "Part A"})
        self.assertEqual(self.notes["part_a_unmatched"], ["F087 Microsoft total remaining performance obligations"])

    def test_two_code_names_show_the_later_check(self):
        """a line checked by both parts shows the later check, and says which part made it"""
        self.assertEqual(self.f["F003-1"], {"text": "checked 2026-09-30: same as the paper", "date": "2026-09-30", "by": "Part A"})
        self.assertEqual(self.f["F009-1"], {"text": "last checked 2026-09-30", "date": "2026-09-30", "by": "Part A"})

    def test_newest_part_a_report_never_a_test(self):
        """the builder reads the newest Part A report, never one ending in ' test'"""
        d = tempfile.mkdtemp()
        try:
            for name in ("Part A 2026-09-26 15.56.37.md", "Part A 2026-09-30 16.41.28.md", "Part A 2026-10-02 09.00.00 test.md", "Part B 2026-10-03 10.00.00.md"):
                open(os.path.join(d, name), "w").close()
            self.assertEqual(os.path.basename(b.newest_part_a(d)), "Part A 2026-09-30 16.41.28.md")
        finally:
            shutil.rmtree(d)


class Bullets(Case):
    def test_format(self):
        """each bullet is built from fields: label, latest, the paper's figure, document, period, link, models"""
        review, _ = run(LEDGER, LINES, [NEWER, DIFFERS], APPROVALS)
        self.assertEqual(len(review["bullets"]), 1)                        # not for 'Paper differs' or 'Needs recalculating'
        bl = review["bullets"][0]
        self.assertEqual("".join(s["text"] for s in bl["segments"]),
                         "label F001-1: now $120bn (paper: $100bn). 10-Q filed 2026-07-30, figure for 2026-06-30 · www.sec.gov · "
                         "Affects: core model; F003; F004 (Test 5)")
        self.assertEqual(bl["segments"][0], {"text": "label F001-1", "figure": "F001-1"})
        self.assertEqual([s.get("href") for s in bl["segments"] if s.get("href")], [SEC])

    def test_part_a_and_arithmetic(self):
        """a Part A bullet links the SEC filing index; an arithmetic bullet names its inputs' sources and has no link"""
        lines = [line("F001-1", "16.9% of revenue", 16.9, unit="% of revenue", code="claims.py F001 formula bill")] + LINES[1:]
        ps = [arithmetic(3, "F001", "bill", 27.3649, "16.9"), part_a(2, "F002", "Meta capex, quarter", 33.0, "2026-09-30", 30, "2026-06-30")]
        review, _ = run(LEDGER, lines, ps, [decision(1, "approved", [3], [27.3649, None]), decision(2, "approved", [2], [33.0, "2026-09-30"])])
        texts = ["".join(s["text"] for s in bl["segments"]) for bl in review["bullets"]]
        self.assertEqual(texts, [
            "label F001-1: now 27.36% of revenue (paper: 16.9% of revenue). Worked out by the code (ESTIMATE), inputs from "
            "confirmed in 10-Q filed 2026-08-12; newer figure in 10-Q filed 2026-08-12, 2026-06-30 · Affects: core model; F003; F004 (Test 5)",
            "label F002-1: now $33bn (paper: $30 billions). 10-Q filed 2026-07-30, figure for 2026-09-30 · SEC filing index · Affects: T001"])
        self.assertFalse(any(s.get("href") for s in review["bullets"][0]["segments"]))

    def test_text_value_in_the_documents_words(self):
        """a text figure is shown in the document's words, not reworded"""
        lines = [line("F001-1", "Q1 FY2027", "first quarter of fiscal 2027", unit="text", code="claims.py F001 META")] + LINES[1:]
        p = part_b(1, "F001", "META", "second quarter of fiscal 2027", "2026-08-31", "first quarter of fiscal 2027", "2026-05-31", unit="text")
        review, _ = run(LEDGER, lines, [p], [decision(1, "approved", [1], ["second quarter of fiscal 2027", "2026-08-31"])])
        self.assertTrue("".join(s["text"] for s in review["bullets"][0]["segments"]).startswith(
            "label F001-1: now second quarter of fiscal 2027 (paper: Q1 FY2027)."))


class Numbers(Case):
    def test_number_rule(self):
        """the paper's unit; the decimals the source gives (no trailing zeros); two decimals for the code's own arithmetic"""
        for args, want in (((288.0, "$bn"), "$288bn"), ((278.99, "$bn"), "$278.99bn"), ((-5.855, "$bn"), "−$5.855bn"),
                           ((1300, "$bn"), "$1,300bn"), ((36.369, "$bn"), "$36.369bn"), ((0.98, "share of revenue"), "0.98 share of revenue"),
                           ((16.53, "%"), "16.53%"), ((24.285, "bn shares"), "24.285 bn shares"), ((12.5, "$ per share"), "$12.5 per share"),
                           ((5, "$tn"), "$5tn"), ((114, "$bn a year"), "$114bn a year"), ((91, "days"), "91 days"),
                           (("second quarter of fiscal 2027", "text"), "second quarter of fiscal 2027"), (("2031", "year"), "2031"),
                           (("none", "absence"), "none")):
            with self.subTest(args=args):
                self.assertEqual(b.show(*args), want)
        self.assertEqual(b.show(27.3649, "% of revenue", computed=True), "27.36% of revenue")
        self.assertEqual(b.show(1234.5, "$bn", computed=True), "$1,234.50bn")


class PaperDiffers(Case):
    """Seb can rule that a line's finding is the paper's own figure stated differently, not a later one
    (review/paper_differs.json). The builder never decides this itself."""

    def test_ruling_turns_newer_into_paper_differs(self):
        """a ruled line with a newer published finding is shown as 'Paper differs': no bullet, no 'Affects'; rows built
        from it still need recalculating"""
        review, notes = run(LEDGER, LINES, [NEWER], APPROVALS[:1], paper_differs={"F001-1": {"by": "PRIVATE ruling by", "why": "PRIVATE ruling why"}})
        f = figures_of(review)
        self.assertEqual((f["F001-1"]["state"], f["F003-1"]["state"], f["F004-1"]["state"]), ("differs", "recalc", "recalc"))
        self.assertNotIn("affects", f["F001-1"])
        self.assertEqual(f["F001-1"]["latest"], {"shown": "$120bn", "value": 120.0, "period_end": "2026-06-30"})
        self.assertEqual(review["bullets"], [])
        self.assertEqual((notes["paper_differs"], notes["paper_differs_unused"]), (["F001-1"], []))
        self.assertEqual([x for x in strings(review) if "PRIVATE" in x], [])

    def test_ruling_without_a_newer_finding_changes_nothing(self):
        """a ruling on a line with no approved newer finding, or on a line that does not exist, changes nothing and is listed"""
        review, notes = run(LEDGER, LINES, [NEWER], [], paper_differs={"F001-1": {}, "F005-1": {}, "F999-9": {}})
        f = figures_of(review)
        self.assertEqual((f["F001-1"]["state"], f["F005-1"]["state"]), ("unchanged", "not_watched"))
        self.assertEqual((notes["paper_differs"], sorted(notes["paper_differs_unused"])), ([], ["F001-1", "F005-1", "F999-9"]))

    def test_reading_the_rulings(self):
        """the rulings file: a missing file is no ruling; a file that is not the documented shape stops the build"""
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(b.read_paper_differs(os.path.join(d, "none.json")), {})
            good = os.path.join(d, "good.json")
            json.dump({"about": "x", "lines": {"F046-1": {"decided": "2026-10-05", "by": "Seb", "why": "y"}}}, open(good, "w"))
            self.assertEqual(list(b.read_paper_differs(good)), ["F046-1"])
            bad = os.path.join(d, "bad.json")
            json.dump({"lines": ["F046-1"]}, open(bad, "w"))
            with self.assertRaises(ValueError):
                b.read_paper_differs(bad)
        finally:
            shutil.rmtree(d)


# ------------------------------------------------------------------------------------------
# The data of 5 Oct 2026 (a copy in fixtures/review_2026-10-05): the exact result
# ------------------------------------------------------------------------------------------
def gate_decision(n, what, lines, figure, by="AI gate"):
    """A decision of the AI gate (approvals/gate.py): its checks, answer file and run must never be published."""
    return dict(decision(n, what, lines, figure), decided_by=by, checks=["PRIVATE gate check"], answer="PRIVATE answer.json",
                gate_run="PRIVATE gate run")


class ApprovedBy(Case):
    """Who approved each published figure: Seb, or the AI gate within its limits (7 Oct 2026)."""

    def test_seb_or_the_gate(self):
        """a figure Seb approved says 'Seb', one the gate approved 'AI gate'; nothing else of the gate's decision is published"""
        review, notes = run(LEDGER, LINES, [NEWER, DIFFERS], [gate_decision(1, "approved", [1], [120.0, "2026-06-30"]), APPROVALS[1]])
        f = figures_of(review)
        self.assertEqual((f["F001-1"]["evidence"]["approved_by"], f["F002-1"]["evidence"]["approved_by"]), ("AI gate", "Seb"))
        self.assertEqual((f["F001-1"]["state"], notes["declined"]), ("newer", []))
        self.assertEqual([s for s in strings(review) if "PRIVATE" in s], [])
        self.assertEqual(b.schema_problems(review), [])

    def test_two_code_names_are_sebs_only_if_both_are(self):
        """a line checked twice says 'Seb' only when both approvals are his"""
        ps = [part_b(1, "F001", "META", 278.99, "2026-06-30", "103.77", "2025-12-31"),
              part_a(2, "F001", "Meta leases not yet commenced", 278.99, "2026-06-30", 103.77, "2025-12-31")]
        for gate_on, want in (((), "Seb"), ((2,), "AI gate"), ((1, 2), "AI gate")):
            with self.subTest(gate_on=gate_on):
                ds = [(gate_decision if n in gate_on else decision)(n, "approved", [n], [278.99, "2026-06-30"]) for n in (1, 2)]
                review, _ = run(LEDGER, TwoCodeNames.LINES, ps, ds)
                self.assertEqual(figures_of(review)["F001-1"]["evidence"]["approved_by"], want)

    def test_the_gates_limits(self):
        """the builder declines (and prints) a gate approval of arithmetic, of the paper's own period ('Paper differs'), of
        a company disclosure, or on a line Seb has ruled; and an approval by anyone but Seb or the gate"""
        arith_lines = [line("F001-1", "16.9% of revenue", 16.9, unit="% of revenue", code="claims.py F001 formula bill")] + LINES[1:]
        nvidia = part_b(1, "F001", "META", 120.0, "2026-06-30", "100", "2026-03-31", link="https://investor.nvidia.com/q2.pdf")
        cases = [("arithmetic", arith_lines, [arithmetic(3, "F001", "bill", 27.3649, "16.9")], gate_decision(1, "approved", [3], [27.3649, None]),
                  None, "F001-1", "approved by the AI gate, but arithmetic (an ESTIMATE)"),
                 ("paper differs", LINES, [DIFFERS], gate_decision(1, "approved", [2], [31.078, "2026-06-30"]), None, "F002-1",
                  "approved by the AI gate, but not a newer figure (Paper differs)"),
                 ("company disclosure", LINES, [nvidia], gate_decision(1, "approved", [1], [120.0, "2026-06-30"]), None, "F001-1",
                  "approved by the AI gate, but not VERIFIED — primary"),
                 ("ruled", LINES, [NEWER], gate_decision(1, "approved", [1], [120.0, "2026-06-30"]), {"F001-1": {}}, "F001-1",
                  "approved by the AI gate, but on a line Seb ruled 'Paper differs'"),
                 ("someone else", LINES, [NEWER], gate_decision(1, "approved", [1], [120.0, "2026-06-30"], by="Claude"), None, "F001-1",
                  "approved by 'Claude', who is neither Seb nor the AI gate")]
        # the same limits hold for a gate approval still in force while a later figure waits (its earlier decision)
        later = part_b(3, "F001", "META", 130.0, "2026-09-30", "100", "2026-03-31", found="2026-10-20T09:00:00")
        cases += [("company disclosure, earlier decision", LINES, [nvidia, later], gate_decision(1, "approved", [1], [120.0, "2026-06-30"]),
                   None, "F001-1", "approved by the AI gate, but not VERIFIED — primary"),
                  ("ruled, earlier decision", LINES, [NEWER, later], gate_decision(1, "approved", [1], [120.0, "2026-06-30"]),
                   {"F001-1": {}}, "F001-1", "approved by the AI gate, but on a line Seb ruled 'Paper differs'")]
        # and Part A's link: the filing on www.sec.gov
        for filing in (None, "https://investor.example.com/q2.pdf"):
            cases.append((f"Part A filing {filing}", LINES, [part_a(2, "F002", "Meta capex, quarter", 31.078, "2026-09-30", 30, "2026-06-30",
                                                                    filing=filing)],
                          gate_decision(1, "approved", [2], [31.078, "2026-09-30"]), None, "F002-1",
                          "approved by the AI gate, but not VERIFIED — primary"))
        for name, lines, ps, d, ruled, fid, why in cases:
            with self.subTest(name):
                review, notes = run(LEDGER, lines, ps, [d], paper_differs=ruled)
                self.assertNotIn("evidence", figures_of(review)[fid])
                self.assertEqual(len(notes["declined"]), 1)
                self.assertTrue(notes["declined"][0]["why"].startswith(why), notes["declined"][0]["why"])
        # the control: within the limits, the same two shapes are published as the gate's
        for ps, d, fid in (([NEWER, later], gate_decision(1, "approved", [1], [120.0, "2026-06-30"]), "F001-1"),
                           ([part_a(2, "F002", "Meta capex, quarter", 31.078, "2026-09-30", 30, "2026-06-30")],
                            gate_decision(1, "approved", [2], [31.078, "2026-09-30"]), "F002-1")):
            review, notes = run(LEDGER, LINES, ps, [d])
            self.assertEqual((figures_of(review)[fid]["evidence"]["approved_by"], notes["declined"]), ("AI gate", []))


class RealData(Case):
    @classmethod
    def setUpClass(cls):
        if b is None:
            return
        cls.inputs = b.read_inputs(os.path.join(FIX, "Watch list - The AI Lose Lose Race.xlsx"), FIX,
                                   os.path.join(FIX, "Part A 2026-09-30 16.41.28.md"), os.path.join(FIX, "handoff.json"),
                                   os.path.join(FIX, "paper_differs.json"))
        cls.review, cls.notes = b.build(**cls.inputs)
        cls.f = figures_of(cls.review)

    def by_state(self, state):
        return sorted(k for k, v in self.f.items() if v["state"] == state)

    def test_states(self):
        """Newer: 8 lines; Paper differs: F017-1, and F046-1 by Seb's ruling of 5 Oct; Needs recalculating: F064-1, F157-1"""
        self.assertEqual(self.by_state("newer"), ["F015-1", "F059-1", "F060-1", "F061-1", "F062-1", "F063-1", "F065-1", "F141-3"])
        self.assertEqual(self.by_state("differs"), ["F017-1", "F046-1"])
        self.assertEqual(self.notes["paper_differs"], ["F046-1"])
        self.assertEqual(self.by_state("recalc"), ["F064-1", "F157-1"])
        self.assertEqual((self.f["F046-2"]["state"], self.f["F141-1"]["state"]), ("unchanged", "unchanged"))

    def test_counts(self):
        """160 rows, 345 lines, 131 watched lines on 72 rows; the states and filters add up"""
        c = self.review["counts"]
        self.assertEqual((c["rows"], c["figures"], c["watched_rows"], c["watched_figures"]), (160, 345, 72, 131))
        self.assertEqual(c["states"], {"newer": 8, "differs": 2, "recalc": 2, "unchanged": 121, "not_watched": 212})
        self.assertEqual(c["filters"], {"all": 160, "changes": 10, "recalc": 2})
        self.assertEqual(len(self.notes["no_reader_rule"]), 70)

    def test_paper_differs_f017(self):
        """F017-1: same period (30 Jun 2026); the paper's 30 stays, the SEC's labelled data says 31.078"""
        f = self.f["F017-1"]
        self.assertEqual((f["paper"]["value"], f["paper"]["shown"]), (30, "$30 billions"))
        self.assertEqual(f["latest"], {"shown": "$31.078bn", "value": 31.078, "period_end": "2026-06-30"})

    def test_recalculation_path(self):
        """F157 needs recalculating through F064, which uses the five changed lease rows"""
        self.assertEqual(self.f["F064-1"]["recalc"]["path"], "F064 ← F059, F060, F061, F062, F063")
        self.assertEqual(self.f["F157-1"]["recalc"]["path"], "F157 ← F064 ← F059, F060, F061, F062, F063")

    def test_evidence(self):
        """F059-1 quotes the 10-Q word for word; F060-1 uses the documents' evidence and is also in the SEC's labelled data;
        F015-1 is Part A; F141-3 is the code's estimate"""
        ev = self.f["F059-1"]["evidence"]
        self.assertEqual((ev["basis"], ev["document"]), ("VERIFIED — primary", "10-Q filed 2026-09-11"))
        self.assertTrue(ev["quote"][0]["text"].startswith("As of August 31, 2026 , we had $ 288 billion of additional lease commitments"))
        self.assertEqual((self.f["F060-1"]["evidence"]["source"], self.f["F060-1"]["evidence"]["also"]), ("Part B", "also in the SEC's labelled data"))
        self.assertEqual((self.f["F015-1"]["evidence"]["source"], self.f["F015-1"]["latest"]["shown"]), ("Part A", "$36.369bn"))
        self.assertEqual((self.f["F141-3"]["evidence"]["basis"], self.f["F141-3"]["latest"]["shown"]), ("ESTIMATE", "27.36% of revenue"))
        self.assertEqual(self.f["F065-1"]["latest"]["shown"], "second quarter of fiscal 2027")

    def test_bullets(self):
        """eight bullets, in section order and then ledger order (F046-1 is 'Paper differs', so it has none)"""
        self.assertEqual([x["figure"] for x in self.review["bullets"]],
                         ["F059-1", "F060-1", "F061-1", "F062-1", "F063-1", "F065-1", "F015-1", "F141-3"])
        texts = {x["figure"]: "".join(s["text"] for s in x["segments"]) for x in self.review["bullets"]}
        self.assertEqual(texts["F015-1"], "Oracle cash and cash equivalents, year end: now $36.369bn (paper: $31.3bn). "
                                          "10-Q filed 2026-09-11, figure for 2026-08-31 · SEC filing index · Affects: no model")
        self.assertEqual(texts["F059-1"], "Oracle additional lease commitments: now $288bn (paper: $260bn). 10-Q filed 2026-09-11, "
                                          "figure for 2026-08-31 · www.sec.gov · Affects: core model; F064 (core model AI Contractual Commitments); F157")
        self.assertTrue(texts["F141-3"].endswith("· Affects: Test 5"))

    def test_unchanged_lines_last_checked(self):
        """F046-2 ('reader wrong') was last found unchanged; F141-1's undecided difference shows only its date"""
        self.assertEqual(self.f["F046-2"]["last_checked"]["text"], "checked 2026-09-30: same as the paper")
        self.assertEqual(self.f["F141-1"]["last_checked"]["text"], "last checked 2026-09-30")
        self.assertEqual([x["figure"] for x in self.notes["unpublished_differences"]], ["F141-1"])
        self.assertEqual(self.notes["part_a_unmatched"], ["F087 Microsoft total remaining performance obligations"])
        self.assertEqual(self.f["F141-2"]["last_checked"]["by"], "Part B")

    def test_nothing_declined_and_the_lists_for_the_pr(self):
        """no approval is declined today; the lists the pull requests quote"""
        self.assertEqual(self.notes["declined"], [])
        self.assertEqual(self.notes["earlier_approval"], [])
        self.assertEqual(self.notes["two_code_names"], [])
        self.assertEqual(self.notes["model_outputs_without_built_from"], ["F126", "F142", "F145", "F146", "F147", "F153", "F158"])
        self.assertEqual(self.notes["watched_research_or_calculation"],
                         [("F046", "Secondary research"), ("F100", "Author calculation"), ("F143", "Author calculation"), ("F149", "Author calculation")])

    def test_built_time_and_same_bytes(self):
        """'built' is the newest input (the watcher run of 4 Oct), not the clock; the same inputs give the same bytes"""
        self.assertEqual(self.review["built"], "2026-10-04T16:45:15")
        self.assertEqual(self.review["runs"], {"watcher": "2026-10-04T16:45:15", "part_b": "2026-09-30T16:43:44", "part_a": "2026-09-30T16:41:28"})
        again, _ = b.build(**b.read_inputs(os.path.join(FIX, "Watch list - The AI Lose Lose Race.xlsx"), FIX,
                                           os.path.join(FIX, "Part A 2026-09-30 16.41.28.md"), os.path.join(FIX, "handoff.json"),
                                           os.path.join(FIX, "paper_differs.json")))
        self.assertEqual(b.to_json(again), b.to_json(self.review))

    def test_nothing_private_in_real_data(self):
        """no note, Notes or suggested_why text of the real inputs appears in the output, and every key is in the schema"""
        texts = set()
        wb = b.W.read_workbook(os.path.join(FIX, "Watch list - The AI Lose Lose Race.xlsx"))
        for sheet, head in (("Ledger", "ID"), ("Figures", "Figure ID")):
            texts |= {str(r.get("Notes")).strip() for r in b.W.table(wb[sheet], first_header=head) if r.get("Notes")}
        for name, keys in (("decisions.jsonl", ("note", "suggested_why")), ("proposed.jsonl", ("note", "why"))):
            for raw in open(os.path.join(FIX, name), encoding="utf-8"):
                if raw.strip():
                    d = json.loads(raw)
                    texts |= {str(d[k]).strip() for k in keys if d.get(k)}
        rulings = json.load(open(os.path.join(FIX, "paper_differs.json"), encoding="utf-8"))
        texts |= {str(v).strip() for x in rulings["lines"].values() for k, v in x.items() if k in ("why", "by") and v}
        texts.add(str(rulings.get("about")).strip())
        out = [s for s in strings(self.review) if s not in (b.SEB, b.GATE)]   # approved_by's two values are published by design
        self.assertGreater(len(texts), 500)
        self.assertEqual([t for t in texts if any(t in s for s in out)], [])
        self.assertEqual(b.schema_problems(self.review), [])
        self.assertFalse(any(FIX in s or AGENT in s or "jsonl" in s for s in out))


# ------------------------------------------------------------------------------------------
# The live files: only what must always hold
# ------------------------------------------------------------------------------------------
def md5s(paths):
    return {p: hashlib.md5(open(p, "rb").read()).hexdigest() for p in paths}


class LiveData(Case):
    @classmethod
    def setUpClass(cls):
        if b is None:
            return
        cls.paths = b.default_paths(AGENT)
        cls.review, cls.notes = b.build(**b.read_inputs(**cls.paths))

    def test_the_gate_holds(self):
        """every published figure has an approval in force, for one of its line's code names, with the same figure"""
        result, _, _ = b.O.load(self.paths["reader_dir"])
        approved = {}
        for e in result["open"] + result["closed"] + result["gone"] + result["aside"]:
            d = e.get("decision_made") or e.get("earlier_decision")
            if d and d["decision"] == "approved":
                approved[b.code_name(e)] = d["figure"]
        wb = b.W.read_workbook(self.paths["workbook"])
        codes = {r["Figure ID"]: [c.strip() for c in str(r.get("Checked by code as") or "").split(";") if c.strip()]
                 for r in b.W.table(wb["Figures"], first_header="Figure ID")}
        for fid, f in figures_of(self.review).items():
            if f["state"] in ("newer", "differs"):
                with self.subTest(fid):
                    figs = [approved[c] for c in codes[fid] if c in approved]
                    self.assertTrue(figs)
                    self.assertTrue(any(b.O.same_figure([f["latest"]["value"], f["latest"].get("period_end")], x) for x in figs))

    def test_rulings_name_real_lines(self):
        """every line in review/paper_differs.json is a Figures line"""
        wb = b.W.read_workbook(self.paths["workbook"])
        ids = {r["Figure ID"] for r in b.W.table(wb["Figures"], first_header="Figure ID")}
        self.assertTrue(set(b.read_paper_differs(self.paths["paper_differs"])) <= ids)

    def test_counts_add_up(self):
        """every line has one state; the counts match the rows; nothing private; every key in the schema"""
        c = self.review["counts"]
        figs = figures_of(self.review)
        self.assertEqual(sum(c["states"].values()), c["figures"])
        self.assertEqual(len(figs), c["figures"])
        self.assertEqual({s: sum(1 for f in figs.values() if f["state"] == s) for s in b.STATES}, c["states"])
        self.assertEqual(c["filters"]["all"], sum(len(s["rows"]) for s in self.review["sections"]))
        self.assertEqual(b.schema_problems(self.review), [])
        self.assertEqual(len(self.review["bullets"]), c["states"]["newer"])

    def test_command_writes_only_review_json(self):
        """the command writes review.json and nothing else, and refuses a folder inside the Agent repo"""
        inputs = [self.paths["workbook"], self.paths["handoff"], self.paths["part_a_report"], self.paths["paper_differs"]] + \
                 [os.path.join(self.paths["reader_dir"], f) for f in ("proposed.jsonl", "decisions.jsonl", "runs.jsonl")]
        before = md5s(inputs)
        out = tempfile.mkdtemp()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                b.main(["--out", out])
            self.assertEqual(os.listdir(out), ["review.json"])
            self.assertEqual(open(os.path.join(out, "review.json"), encoding="utf-8").read(), b.to_json(self.review))
        finally:
            shutil.rmtree(out)
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            b.main(["--out", os.path.join(AGENT, "review")])
        self.assertEqual(md5s(inputs), before)
        self.assertFalse(os.path.exists(os.path.join(AGENT, "review", "review.json")))

    def test_command_without_out_uses_the_temp_folder(self):
        """with no --out, it writes to a new folder in the system's temp folder and prints the path"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            b.main([])
        path = next(ln.split(": ", 1)[1].strip() for ln in buf.getvalue().splitlines() if ln.startswith("Written: "))
        try:
            self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(tempfile.gettempdir())))
            self.assertFalse(os.path.realpath(path).startswith(os.path.realpath(AGENT)))
            self.assertEqual(os.path.basename(path), "review.json")
        finally:
            shutil.rmtree(os.path.dirname(path))


if __name__ == "__main__":
    unittest.main()
