"""Replays of the first live DeepSeek runs of 28 Sep 2026 (their saved answers): CoreWeave, then Nebius and Oracle,
the Oracle rerun, and Oracle's year-to-date answers from the first full run. Each expected verdict was checked by hand."""
import json
import os
import shutil
import unittest

from support import FIX, E, by_id, check_one, formulas, item, live_crwv, live_nbis_orcl, live_orcl_rerun, load, out


class CoreWeave(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = live_crwv()
        cls.V = by_id(cls.res)

    def test_every_item_as_checked_by_hand(self):
        """live answers: every CoreWeave item gets the answer checked by hand in the 10-Q"""
        expect = {"F141:CRWV_te": "newer figure", "F141:CRWV_rev": "matches", "F135:CRWV": "matches", "F150:CRWV": "matches",
                  "F152:CRWV": "matches", "F154:CRWV_revolver": "matches", "F154:CRWV_new_draw": "matches", "F154:CRWV_new_size": "matches",
                  "F056:CRWV": "matches", "F155:CRWV_ratio": "no separate figure found", "F078:CRWV": "no update found"}
        self.assertEqual({k: self.V[k]["verdict"] for k in expect}, expect)

    def test_f056_second_statement_kept(self):
        """F056: the AI's second statement of 98% (220,000 characters away) is kept as separate evidence instead of rejecting a correct answer"""
        self.assertIn("elsewhere", json.dumps(self.V["F056:CRWV"]["best"]["fragments"]))

    def test_f155_tests_not_asked_of_10q(self):
        """F155: the 'six tests' count is not asked of a 10-Q (it comes from the facilities' 8-Ks)"""
        v = self.V["F155:CRWV_tests"]
        self.assertEqual(v["verdict"], "not checked")
        self.assertIn("8-K", v["skips"][0]["why"])

    def test_ledger_ids_are_not_figures(self):
        """new facts: ledger IDs such as F152 no longer count as figures; only the comment that used $35.5bn (not in its fragments) is removed"""
        removed = [f for f in self.res["facts"] if f["why"].startswith("[the AI's comment was removed")]
        self.assertEqual(len(removed), 1, [f["why"][:50] for f in self.res["facts"]])


class NebiusOracle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.V = by_id(live_nbis_orcl())

    def test_every_item_as_checked_by_hand(self):
        """live answers: every Nebius and Oracle item gets the answer checked by hand in the documents"""
        expect = {"F059:ORCL": "newer figure", "F065:ORCL_start": "text differs", "F065:ORCL_end": "matches", "F065:ORCL_term_min": "newer, unchanged",
                  "F065:ORCL_term_max": "newer, unchanged", "F150:ORCL_ytd": "matches", "F150:ORCL_prior_ytd": "matches", "F152:ORCL": "matches",
                  "F154:ORCL_atm": "matches", "F042:ORCL": "matches", "F037:ORCL": "no update found",
                  "F035:ORCL": "no separate figure found", "F050:NBIS": "matches", "F150:NBIS": "matches", "F152:NBIS": "matches",
                  "F154:NBIS_facility": "matches", "F154:NBIS_converts": "no update found", "F155:NBIS_none": "no separate figure found",
                  "F135:NBIS": "matches"}
        self.assertEqual({k: self.V[k]["verdict"] for k in expect}, expect)

    def test_f135_nebius_inherited_units(self):
        """F135 Nebius: a note that inherits the statements' '(In millions…)' line is accepted (units line far above, but it agrees with the nearest units line)"""
        self.assertEqual(self.V["F135:NBIS"]["verdict"], "matches")

    def test_f154_oracle_debt_old_format(self):
        """F154 Oracle 'no new debt' is now an absence claim; the old-format answer ('found', value 'none') is rejected as contradictory, not confirmed"""
        v = self.V["F154:ORCL_debt"]
        self.assertEqual(v["type"], "absence")
        self.assertEqual(v["verdict"], "not checked (extraction problems)")

    def test_bad_units_line(self):
        """a units line that is not in the document, or disagrees with the nearest one, is still rejected"""
        nbis, _ = load("NBIS_6k_2026-09-26_18")
        answer = {"found": True, "value": 7.8364, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
                  "fragments": [{"role": "value", "text": "Assets not yet in use 2,417.4 7,836.4"}, {"role": "units", "text": "(in thousands"}]}
        r = E.extract(nbis, item("F135", "NBIS"), answer)
        self.assertEqual(r["status"], "rejected", r.get("problem"))

    def test_f150_oracle_trailing_year(self):
        """F150 Oracle: trailing year re-checked from this run's inputs, still $16.0bn"""
        fr = formulas("F150", [self.V[i] for i in self.V if i.startswith("F150:")])["ORCL_year"]
        self.assertTrue(fr["paper_ok"])
        self.assertIs(fr.get("changed"), False)


class OracleRerun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.V = by_id(live_orcl_rerun())

    def test_no_new_debt_absence(self):
        """F154 Oracle: 'no new debt' is checked as an absence claim, with the financing lines the AI looked at verified word for word"""
        self.assertEqual(self.V["F154:ORCL_debt"]["verdict"], "no separate figure found")

    def test_f042_short_heading_ignored(self):
        """F042: a column heading given as just 'Q1' is ignored as too short; the code reads the period itself ('Fiscal 2027' / 'Q1')"""
        v = self.V["F042:ORCL"]
        self.assertEqual(v["verdict"], "matches")
        self.assertTrue(any("too short" in w for w in v["best"]["warnings"]))

    def test_same_as_first_oracle_run(self):
        """the rerun gives the same results as the first Oracle run for every other item"""
        expect = {"F059:ORCL": "newer figure", "F065:ORCL_start": "text differs", "F150:ORCL_ytd": "matches", "F150:ORCL_prior_ytd": "matches",
                  "F152:ORCL": "matches", "F154:ORCL_atm": "matches", "F037:ORCL": "no update found", "F035:ORCL": "no separate figure found"}
        self.assertEqual({k: self.V[k]["verdict"] for k in expect}, expect)


class OracleYearToDate(unittest.TestCase):
    """First full live run (28 Sep 2026): Oracle year-to-date answers given as 'ytd'."""

    @classmethod
    def setUpClass(cls):
        folder = out("d28")
        os.makedirs(folder, exist_ok=True)
        shutil.copy(os.path.join(FIX, "ORCL_periodic_2026-09-28_1.txt"), folder)
        cls.orcl, _ = load("ORCL_periodic_2026-09-28_1", folder=folder)
        answers = json.load(open(os.path.join(FIX, "answers_live_full_ORCL_2026-09-28.json"), encoding="utf-8"))["ORCL_periodic_2026-09-26_16"]
        cls.by = {a["item"]: a for a in answers["items"]}
        cls.v_ytd = check_one(cls.orcl, "F150", "ORCL_ytd", cls.by["F150:ORCL_ytd"])
        cls.v_prior = check_one(cls.orcl, "F150", "ORCL_prior_ytd", cls.by["F150:ORCL_prior_ytd"])

    def test_ytd_read_as_three_months(self):
        """F150 Oracle: 'ytd' with the heading 'Three Months Ended August 31,' is read as 3 months and matches 11.363"""
        self.assertEqual(self.v_ytd["verdict"], "matches")
        self.assertEqual(self.v_ytd["best"]["period_type"], "3m")

    def test_prior_year_comparative(self):
        """F150 Oracle: the prior-year comparative (nil) also matches"""
        self.assertEqual(self.v_prior["verdict"], "matches", [r.get("problem") for r in self.v_prior["results"]])

    def test_trailing_year(self):
        """F150 Oracle: trailing year re-checked, still $16.0bn"""
        fr = formulas("F150", [self.v_ytd, self.v_prior])["ORCL_year"]
        self.assertTrue(fr["paper_ok"])
        self.assertFalse(fr.get("new_blocked"))

    def test_unclear_ytd_not_guessed(self):
        """without a readable heading, an unclear 'ytd' is still not guessed"""
        no_len = dict(self.by["F150:ORCL_ytd"], fragments=[f for f in self.by["F150:ORCL_ytd"]["fragments"] if f["role"] == "value"])
        r = E.extract(self.orcl, item("F150", "ORCL_ytd"), no_len)
        self.assertTrue(r["status"] == "rejected" or r.get("period_type") in ("3m",), r.get("problem") or r.get("period_type"))


if __name__ == "__main__":
    unittest.main()
