"""Replay of the paid run of 26 Sep 2026: the AI's own answers from that run (fixtures/answers_paid_run.json, rebuilt from
that run's report), put through today's checks on the same saved documents."""
import os
import unittest

from support import by_id, formulas, out, paid_run


class PaidRun26Sep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = paid_run()
        cls.V = by_id(cls.res)

    # ---- F152: one lease figure per company
    def test_f152_each_company_matches(self):
        """F152: each company's lease figure is compared with its own component and matches"""
        f152 = [self.V[f"F152:{k}"] for k in ("CRWV", "NBIS", "ORCL")]
        self.assertTrue(all(v["verdict"] == "matches" for v in f152), [(v["id"], v["verdict"]) for v in f152])

    def test_f152_no_false_alerts(self):
        """F152: the three false CHANGED alerts are gone (no proposal)"""
        self.assertFalse(any(p["row"] == "F152" for p in self.res["proposals"]))

    def test_f152_total_worked_out(self):
        """F152: the code works out 35.5 + 12.0541 + 288 = 335.5541, which agrees with the paper's 335.6"""
        fr = formulas("F152", [self.V[f"F152:{k}"] for k in ("CRWV", "NBIS", "ORCL")])
        self.assertTrue(fr["total"]["paper_ok"])
        self.assertAlmostEqual(fr["total"]["at_paper"], 335.5541, delta=1e-6)

    # ---- F141: CoreWeave's equipment, from the wrong column
    def test_f141_old_column_caught(self):
        """F141: the AI's old-column answer (20.903 at 31 Dec 2025) is caught as a wrong-period error"""
        self.assertTrue(any(x.get("ai_problem", "").startswith("wrong period") for x in self.V["F141:CRWV_te"]["results"]))

    def test_f141_code_reads_latest_column(self):
        """F141: the code reads 33.823 from the 30 Jun 2026 column of the same verified row"""
        r = self.V["F141:CRWV_te"]["best"]
        self.assertTrue(r.get("code_read"))
        self.assertAlmostEqual(r["value"], 33.823, delta=1e-9)
        self.assertEqual(r["period_end"], "2026-06-30")

    def test_f141_paper_right_for_its_date(self):
        """F141: the AI's 20.903 equals the paper's figure for 31 Dec 2025 (paper right for its own date)"""
        self.assertTrue(self.V["F141:CRWV_te"]["best"]["wrong_period"]["matches_paper_for_that_date"])

    def test_f141_newer_figure(self):
        """F141: reported as a newer figure (proposed update), not as the paper being wrong"""
        self.assertEqual(self.V["F141:CRWV_te"]["verdict"], "newer figure")

    def test_f141_paper_formula_reproduced(self):
        """F141: the paper's 16.9% is reproduced by the code from its own inputs"""
        fr = formulas("F141", [self.V["F141:CRWV_te"], self.V["F141:CRWV_rev"]])["replacement_bill"]
        self.assertTrue(fr["paper_ok"])
        self.assertAlmostEqual(fr["at_paper"], 16.9118, delta=1e-3)

    def test_f141_formula_with_new_input(self):
        """F141: with the 30 Jun 2026 equipment the same formula gives 27.36% (shown, not applied)"""
        fr = formulas("F141", [self.V["F141:CRWV_te"], self.V["F141:CRWV_rev"]])["replacement_bill"]
        self.assertTrue(fr["changed"])
        self.assertAlmostEqual(fr["at_new"], 27.3649, delta=1e-3)

    # ---- F150: customer prepayments
    def test_f150_nebius_not_the_model_line(self):
        """F150 Nebius: the AI's 4,397.7 (note) is flagged as not the cash-flow line the model uses (4,395.0), although both round to $8.8bn a year"""
        v = self.V["F150:NBIS"]
        self.assertEqual(v["verdict"], "differs for the same period")
        self.assertIs(v["best"].get("disclosed_check", {}).get("same"), False)

    def test_f150_oracle_quarter_input(self):
        """F150 Oracle: the quarter's 11.4 is compared with the model's quarter input (11.363), not with the paper's trailing year"""
        self.assertEqual(self.V["F150:ORCL_ytd"]["verdict"], "matches")

    def test_f150_oracle_trailing_year(self):
        """F150 Oracle: the code's trailing year 4.592 + 11.363 - 0 = 15.955 agrees with the paper's 16.0"""
        fr = formulas("F150", [self.V[i] for i in self.V if i.startswith("F150:")])
        self.assertTrue(fr["ORCL_year"]["paper_ok"])

    def test_f150_oracle_fiscal_year_not_asked_of_10q(self):
        """F150 Oracle: the fiscal-2026 input is not asked of a 10-Q (it is only in the 10-K)"""
        v = self.V["F150:ORCL_fy"]
        self.assertEqual(v["verdict"], "not checked")
        self.assertIn("10-K", v["skips"][0]["why"])

    # ---- earlier false rejections, and answers that must not count
    def test_f063_spacing(self):
        """F063: '$ 85.2' spacing no longer rejects Alphabet's $85.2bn (now a proposed update)"""
        v = self.V["F063:GOOGL"]
        self.assertEqual(v["verdict"], "newer figure")
        self.assertAlmostEqual(v["best"]["value"], 85.2, delta=1e-9)

    def test_previously_rejected_now_match(self):
        """<id>: previously rejected by the checker, now verified and matching the paper (5 items)"""
        for i in ("F050:NBIS", "F135:NBIS", "F135:DLR", "F077:MSFT_min", "F077:MSFT_max"):
            with self.subTest(i):
                self.assertEqual(self.V[i]["verdict"], "matches")

    def test_stitched_quotes_rejected(self):
        """<id>: the AI's stitched quote is rejected, and the paper is NOT marked correct (2 items)"""
        for i in ("F103:AMZN_h1", "F108:AMZN_g"):
            with self.subTest(i):
                self.assertEqual(self.V[i]["verdict"], "not checked (extraction problems)")

    def test_f035_not_found_without_evidence(self):
        """F035 <company>: 'not found' with no verified evidence does not count as confirming the absence (3 companies)"""
        for co in ("GOOGL", "META", "ORCL"):
            with self.subTest(co):
                self.assertEqual(self.V[f"F035:{co}"]["verdict"], "not checked (extraction problems)")

    def test_f135_equinix_not_asked_of_10q(self):
        """F135 Equinix: construction in progress is not asked of the 10-Q (10-K only), so RPO cannot be substituted"""
        v = self.V["F135:EQIX"]
        self.assertEqual(v["verdict"], "not checked")
        self.assertIn("10-K", v["skips"][0]["why"])

    def test_f039_not_asked_of_older_release(self):
        """F039: not asked of a release that predates Microsoft's new Azure disclosure"""
        v = self.V["F039:MSFT"]
        self.assertEqual(v["verdict"], "not checked")
        self.assertIn("before the first one expected", v["skips"][0]["why"])

    def test_f085_f086_historical(self):
        """F085, F086: kept as historical claims, not 'missing'"""
        self.assertEqual(self.V["F085:MSFT"]["verdict"], "historical")
        self.assertEqual(self.V["F086:MSFT"]["verdict"], "historical")

    def test_f079_annual_policy(self):
        """F079: an annual policy not repeated in a 10-Q is 'no update found', not a change"""
        self.assertEqual(self.V["F079:AMZN_from"]["verdict"], "no update found")

    # ---- the report and the proposals
    def test_report_never_says_paper_still_latest(self):
        """the report never says 'paper still latest'"""
        self.assertNotIn("paper still latest", self.res["report"].lower())

    def test_report_no_update_wording(self):
        """the report says 'no update found in the documents checked' and that it is not a confirmation"""
        self.assertIn("No update found in the documents checked", self.res["report"])
        self.assertIn("not a confirmation", self.res["report"])

    def test_report_absence_wording(self):
        """absence wording: covers only the documents listed"""
        self.assertIn("It does not mean the company has never disclosed one", self.res["report"])

    def test_proposals_pending_new_version(self):
        """every proposal is pending and marked as the new version"""
        self.assertTrue(all(p.get("decision") == "pending" and p.get("version") == 2 for p in self.res["proposals"]))

    def test_report_in_test_folder(self):
        """the replay's report went to the test folder"""
        self.assertTrue(os.path.exists(self.res["report_path"]))
        self.assertTrue(self.res["report_path"].startswith(out("out_paid_run")))


if __name__ == "__main__":
    unittest.main()
