"""The open list: repeats across runs merged into one entry each, and proposed.jsonl never changed."""
import json
import os
import shutil
import unittest

from support import FIX, K, L, O, full_runs_28sep, md5, out


class OpenList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = out("open")
        os.makedirs(folder, exist_ok=True)
        cls.folder, proposed = folder, os.path.join(folder, "proposed.jsonl")
        shutil.copy(os.path.join(FIX, "proposed_2026-09-28_346_lines.jsonl"), proposed)
        cls.md5_before = md5(proposed)
        cls.res = O.rebuild(folder, quiet=True)                 # the 346 lines of the five runs of 28 Sep
        cls.md5_after = md5(proposed)
        # then, test data: a later run finds F059 matching and F060 not updated; F141's proposal is approved
        with open(os.path.join(folder, "runs.jsonl"), "w") as f:
            f.write(json.dumps(dict(run_id="test", found_at="2026-10-01T09:00:00",
                                    items={"F059:ORCL": "matches", "F060:META": "no update found"})) + "\n")
        with open(proposed, "a") as f:
            f.write(json.dumps(dict(found_at="2026-10-01T09:00:00", part="B", version=2, row="F141", component="CRWV_te", company="CRWV",
                                    kind="newer figure", new_value=33.823, new_period_end="2026-06-30", decision="approved")) + "\n")
        cls.later = O.rebuild(folder, quiet=True)

    def test_proposed_unchanged(self):
        """building the open list does not change proposed.jsonl"""
        self.assertEqual(self.md5_after, self.md5_before)

    def test_one_entry_per_row_and_component(self):
        """the figure proposals of the five runs of 28 Sep become 8 entries, one per row and component"""
        keys = [e["key"] for e in self.res["open"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sorted(e["key"] for e in self.res["open"] if e["kind"] == "figure"),
                         ["B:F046:META_total", "B:F059:ORCL", "B:F060:META", "B:F061:AMZN", "B:F062:AMZN", "B:F063:GOOGL", "B:F065:ORCL_start", "B:F141:CRWV_te"])

    def test_found_by_several_runs(self):
        """F141 CoreWeave equipment: found by several runs, listed once with its first and last dates"""
        te = next(e for e in self.res["open"] if e["key"] == "B:F141:CRWV_te")
        self.assertEqual(len(te["runs"]), len(te["lines"]))
        self.assertGreaterEqual(len(te["runs"]), 3)

    def test_sb_energy_one_entry(self):
        """the SB Energy guarantee leads of every run and document are one entry"""
        sb = [e for e in self.res["open"] if e["kind"] == "new fact" and e["company"] == "NVDA"
              and any(K.same_amount((105.0, "105"), tuple(x)) for x in e["figures"])]
        self.assertEqual(len(sb), 1)

    def test_first_version_separate(self):
        """proposals from the first version (26 Sep, before the fixes) are listed separately, not merged"""
        self.assertTrue(self.res["earlier"])
        self.assertTrue(all(p.get("version") is None for p in self.res["earlier"]))

    def test_part_a_on_the_list(self):
        """Part A's proposals are on the open list too"""
        self.assertTrue(any(e["kind"] == "Part A figure" for e in self.res["open"]))

    def test_files_written(self):
        """Open proposals.md and proposals_open.jsonl are written next to proposed.jsonl"""
        self.assertTrue(os.path.exists(os.path.join(self.folder, "Open proposals.md")))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "proposals_open.jsonl")))

    def test_no_longer_found(self):
        """a figure that a later run finds matching the paper moves to 'no longer found' (test data)"""
        self.assertTrue(any(e["key"] == "B:F059:ORCL" for e in self.later["gone"]))
        self.assertFalse(any(e["key"] == "B:F059:ORCL" for e in self.later["open"]))

    def test_approved_closed(self):
        """a proposal whose latest line is approved is no longer open (test data)"""
        self.assertTrue(any(e["key"] == "B:F141:CRWV_te" for e in self.later["closed"]))

    def test_no_update_does_not_close(self):
        """a later 'no update found' does not close a proposal (only a verdict that the paper agrees does)"""
        self.assertTrue(any(e["key"] == "B:F060:META" for e in self.later["open"]))


class Decisions(unittest.TestCase):
    """Your decisions (decisions.jsonl, written by step 4) close entries, and stay made while the figure is the same."""

    @classmethod
    def setUpClass(cls):
        cls.folder = out("decisions")
        os.makedirs(cls.folder, exist_ok=True)
        cls.proposed = os.path.join(cls.folder, "proposed.jsonl")
        shutil.copy(os.path.join(FIX, "proposed_2026-09-28_346_lines.jsonl"), cls.proposed)
        res, lines, _ = O.load(cls.folder)
        cls.te = next(e for e in res["open"] if e["key"] == "B:F141:CRWV_te")
        cls.fact = next(e for e in res["open"] if e["kind"] == "new fact")
        cls.v1 = res["earlier"][0]
        cls.lines = {p["_line"]: p for p in lines}
        cls.md5_before = md5(cls.proposed)

    def write_decisions(self, *decisions):
        with open(os.path.join(self.folder, "decisions.jsonl"), "w") as f:
            for n, d in enumerate(decisions):
                f.write(json.dumps(dict(d, decided_at=f"2026-10-01T09:00:{n:02d}")) + "\n")

    def add_proposal(self, **changes):
        line = dict(self.lines[max(self.te["lines"])], found_at="2026-10-02T09:00:00", **changes)
        line.pop("_line", None)
        with open(self.proposed, "a") as f:
            f.write(json.dumps(line) + "\n")

    def setUp(self):
        shutil.copy(os.path.join(FIX, "proposed_2026-09-28_346_lines.jsonl"), self.proposed)
        self.approve = dict(decision="approved", key=self.te["key"], lines=self.te["lines"], figure=O.figure_of(self.te["latest"]))

    def test_decision_closes(self):
        """a decided figure is no longer open, and the list shows the decision"""
        self.write_decisions(self.approve)
        res = O.rebuild(self.folder, quiet=True)
        self.assertFalse(any(e["key"] == self.te["key"] for e in res["open"]))
        self.assertEqual(next(e for e in res["closed"] if e["key"] == self.te["key"])["decision_made"]["decision"], "approved")
        self.assertRegex(open(os.path.join(self.folder, "Open proposals.md"), encoding="utf-8").read(), r"## \d+\. Decided")

    def test_same_figure_stays_decided(self):
        """a later run that finds the same figure again does not reopen a decision"""
        self.write_decisions(self.approve)
        self.add_proposal()
        res, _, _ = O.load(self.folder)
        self.assertTrue(any(e["key"] == self.te["key"] for e in res["closed"]))

    def test_different_figure_reopens(self):
        """a later run that finds a different figure opens it again, showing the earlier decision"""
        self.write_decisions(self.approve)
        self.add_proposal(new_value=41.0, new_period_end="2026-09-30")
        res = O.rebuild(self.folder, quiet=True)
        e = next(e for e in res["open"] if e["key"] == self.te["key"])
        self.assertEqual(e["earlier_decision"]["decision"], "approved")
        self.assertIn("You decided approved", open(os.path.join(self.folder, "Open proposals.md"), encoding="utf-8").read())

    def test_same_words_stay_decided(self):
        """a text figure worded again with 'the' stays decided (F065, 30 Sep 2026); a different quarter opens it again"""
        self.write_decisions()
        res, _, _ = O.load(self.folder)
        f065 = next(e for e in res["open"] if e["key"] == "B:F065:ORCL_start")
        self.write_decisions(dict(decision="approved", key=f065["key"], lines=f065["lines"],
                                  figure=["second quarter of fiscal 2027", "2026-08-31"]))
        for n, words in enumerate(("the second quarter of fiscal 2027", "the third quarter of fiscal 2027")):
            line = dict(self.lines[max(f065["lines"])], found_at=f"2026-10-0{n + 2}T09:00:00", new_value=words)
            line.pop("_line", None)
            with open(self.proposed, "a") as f:
                f.write(json.dumps(line) + "\n")
            res, _, _ = O.load(self.folder)
            if n == 0:
                self.assertTrue(any(e["key"] == f065["key"] for e in res["closed"]))
            else:
                e = next(e for e in res["open"] if e["key"] == f065["key"])
                self.assertEqual(e["earlier_decision"]["decision"], "approved")

    def test_dismissed_lead_stays_closed(self):
        """a dismissed lead stays closed when found again, and new reports count it as already decided"""
        self.write_decisions(dict(decision="dismissed", key=self.fact["key"], lines=self.fact["lines"]))
        line = dict(self.lines[self.fact["lines"][-1]], found_at="2026-10-02T09:00:00")
        line.pop("_line")
        with open(self.proposed, "a") as f:
            f.write(json.dumps(line) + "\n")
        res, _, _ = O.load(self.folder)
        self.assertFalse(any(e["key"] == self.fact["key"] for e in res["open"]))
        known = [e for e in O.open_facts(self.folder) if e["key"] == self.fact["key"]]
        self.assertTrue(known and known[0]["decision_made"]["decision"] == "dismissed")

    def test_decided_lead_shown_as_decided(self):
        """a decided lead is listed in the words you decided on, not in a later run's words for it (5 Oct 2026: after the run
        of 30 Sep 16:43, Amazon's '$25bn notes' lead was listed as a term loan)"""
        decided = self.fact["show"]
        self.write_decisions(dict(decision="use", key=self.fact["key"], lines=self.fact["lines"], note="worth using"))
        line = dict(decided, found_at="2026-10-02T09:00:00", topic="A later run's words for the same lead")
        line.pop("_line")
        with open(self.proposed, "a") as f:
            f.write(json.dumps(line) + "\n")
        res = O.rebuild(self.folder, quiet=True)
        e = next(e for e in res["closed"] if e["key"] == self.fact["key"])
        self.assertEqual(e["show"]["topic"], "A later run's words for the same lead")     # the later line was merged in
        self.assertEqual(e["to_do"]["proposal"]["_line"], decided["_line"])
        listed = open(os.path.join(self.folder, "Open proposals.md"), encoding="utf-8").read()
        self.assertIn(f"- {O.name_of(self.fact['company'])}: {decided['topic']}: use on ", listed)
        self.assertNotIn("A later run's words", listed)

    def test_first_version_item_decided(self):
        """a decided item from the first version leaves that section"""
        self.write_decisions(dict(decision="dismissed", key="v1", lines=[self.v1["_line"]]))
        res, _, _ = O.load(self.folder)
        self.assertNotIn(self.v1["_line"], [p["_line"] for p in res["earlier"]])
        self.assertIn(self.v1["_line"], [p["_line"] for p in res["earlier_decided"]])

    def test_reopen(self):
        """a later 'reopen' undoes a decision (the order of the lines counts, not their clock times)"""
        with open(os.path.join(self.folder, "decisions.jsonl"), "w") as f:
            f.write(json.dumps(dict(self.approve, decided_at="2026-10-26T01:30:00")) + "\n")
            f.write(json.dumps(dict(decision="reopen", key=self.te["key"], lines=self.te["lines"], decided_at="2026-10-26T01:10:00")) + "\n")
        res, _, _ = O.load(self.folder)
        self.assertTrue(any(e["key"] == self.te["key"] for e in res["open"]))

    def test_to_do_kept(self):
        """an approval stays on the to-do list when a later figure is marked 'reader wrong', until marked done"""
        wrong = dict(decision="reader wrong", key=self.te["key"], lines=self.te["lines"], figure=[999.0, "2026-06-30"])
        self.write_decisions(self.approve, wrong)
        res, _, _ = O.load(self.folder)
        e = next(e for e in res["open"] + res["closed"] if e["key"] == self.te["key"])
        self.assertEqual(e["to_do"]["decision"]["decision"], "approved")
        self.write_decisions(self.approve, wrong, dict(decision="done", key=self.te["key"], lines=self.te["lines"]))
        res, _, _ = O.load(self.folder)
        e = next(e for e in res["open"] + res["closed"] if e["key"] == self.te["key"])
        self.assertIsNone(e["to_do"])

    def test_proposed_never_changed(self):
        """applying decisions never changes proposed.jsonl"""
        self.write_decisions(self.approve)
        O.rebuild(self.folder, quiet=True)
        self.assertEqual(md5(self.proposed), self.md5_before)


class Merging(unittest.TestCase):
    """Leads are one entry only when they are the same fact (found 5 Oct 2026: near amounts, a lead quoting two facts
    and the same topic in one document had joined different facts, so a later run's find of a new fact was hidden)."""

    @classmethod
    def setUpClass(cls):
        lines, _ = O.read_lines(os.path.join(FIX, "proposed_2026-09-28_346_lines.jsonl"))
        cls.p = {p["_line"]: p for p in lines}

    def later(self, n, line, found_at="2026-09-28T18:49:14", **changes):
        """a later run's copy of line n, as line `line`"""
        return dict(self.p[n], found_at=found_at, _line=line, **changes)

    def amazon(self):
        """Amazon's 10-Q: 199 the $25bn notes (with a $750m tranche), 200 the $17.5bn term loan, 301 a lead quoting both;
        then a run finds the term loan again (900) and the notes again (901)"""
        return [self.p[199], self.p[200], self.p[301], self.later(200, 900, topic="New $17.5bn undrawn delayed draw term loan"),
                self.later(199, 901, topic="Post-quarter $25.0bn senior notes issuance")]

    @staticmethod
    def entry_of(res):
        return {n: e for g in ("open", "closed", "gone", "aside") for e in res[g] for n in e["lines"]}

    @staticmethod
    def decisions(*ds):
        return [dict(d, decided_at="2026-09-30T15:13:04", _n=n) for n, d in enumerate(ds, 1)]

    def test_near_amounts_are_two_facts(self):
        """Microsoft: capex up $66.9bn and finance lease liabilities of $66,594m are two leads"""
        e = self.entry_of(O.build([self.p[183], self.p[283]]))
        self.assertIsNot(e[183], e[283])

    def test_lead_quoting_two_facts_does_not_join_them(self):
        """Amazon: the lead quoting both the term loan and the notes (301) joins the notes; the term loan found again (900)
        joins the term loan (200), not the notes"""
        e = self.entry_of(O.build(self.amazon()))
        self.assertIs(e[301], e[199])
        self.assertIs(e[900], e[200])
        self.assertIsNot(e[900], e[199])
        self.assertIs(e[901], e[199])

    def test_same_topic_other_table_is_another_fact(self):
        """Nebius: 'Customer concentration' twice in one 6-K, once for receivables and once for revenue, is two leads"""
        revenue = self.later(129, 902, found_at="2026-09-30T16:43:44",
                             fragments=[{"role": "value", "text": "Customer C | * | 24% | * | 26% Customer D | * | 21% | * | 21%"}])
        e = self.entry_of(O.build([self.p[129], revenue]))
        self.assertIsNot(e[129], e[902])

    def test_decision_stays_with_the_version_decided(self):
        """a lead decided while it was merged with another fact keeps the decision on the version the sheet showed (the
        notes); the other fact (the term loan found again) is open, not decided"""
        use = dict(decision="use", key="fact:AMZN:56", lines=[199, 301, 900, 901], note="the notes")
        res = O.build(self.amazon(), decisions=self.decisions(use))
        e = self.entry_of(res)
        self.assertEqual(e[901]["decision_made"]["note"], "the notes")
        self.assertEqual(e[901]["decided_on"]["_line"], 901)
        self.assertIsNone(e[900].get("decision_made"))
        self.assertTrue(any(x is e[900] for x in res["open"]))

    def test_new_report_marks_the_right_decision(self):
        """Part B: a new report's term-loan lead is marked with the term loan's decision, not the notes' (the old grouping
        marked it with the notes' decision)"""
        res = O.build(self.amazon(), decisions=self.decisions(dict(decision="use", key="fact:AMZN:56", lines=[199, 301, 900, 901], note="the notes"),
                                                              dict(decision="use", key="fact:AMZN:57", lines=[200], note="the term loan")))
        earlier = [e for e in res["open"] + res["closed"] if e["kind"] == "new fact"]
        loan = self.p[200]
        fact = dict(topic="New delayed draw term loan", rows=[], fragments=loan["fragments"], why="", company="AMZN",
                    doc=dict(form="10-Q", url=loan["link"], title=loan["document"]))
        kept, _ = L.sort_leads([fact], [], earlier)
        self.assertEqual(kept[0]["decided"]["note"], "the term loan")


class EachRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        full_runs_28sep()                                       # two runs into one folder
        cls.folder = out("out_full28")

    def test_run_rebuilds_open_list(self):
        """each run rebuilds the open list and records what it checked (runs.jsonl)"""
        self.assertTrue(os.path.exists(os.path.join(self.folder, "Open proposals.md")))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "runs.jsonl")))

    def test_run_id_and_company(self):
        """new proposals carry the run's id, and new facts their company"""
        props = [json.loads(line) for line in open(os.path.join(self.folder, "proposed.jsonl"), encoding="utf-8")]
        self.assertTrue(all(p.get("run_id", "").startswith("B ") for p in props))
        self.assertTrue(all(p.get("company") for p in props if p["row"] == "NEW FACT"))


if __name__ == "__main__":
    unittest.main()
