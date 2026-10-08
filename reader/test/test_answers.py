"""Answers in the new format, on the saved documents of 26 Sep 2026, for cases the paid run did not cover:
several fragments, a wrong measure, a wrong column or period, absence evidence, historical claims, new facts, a failed AI call."""
import unittest

from support import E, L, W, by_id, check_one, formulas, item, load, out, quiet_main

AMZN_F103 = {
    "F103:AMZN_h1": {"found": True, "value": 28.7, "unit": "$bn", "period_end": "2026-06-30", "period_type": "6m",
                     "fragments": [{"role": "value", "text": "We invested $28.7 billion in OpenAI’s Series C Preferred Stock for the six months ended June 30, 2026, including $13.7 billion invested in Q2 2026."}]},
    "F103:AMZN_q2": {"found": True, "value": 13.7, "unit": "$bn", "period_end": "2026-06-30", "period_type": "3m",
                     "fragments": [{"role": "value", "text": "In Q2 2026, we invested $ 13.7 billion of the Commitment Amount in Series C Preferred Stock."}]},
    "F103:AMZN_after": {"found": True, "value": 21.3, "unit": "$bn", "period_end": "2026-07-31", "period_type": "event",
                        "fragments": [{"role": "value", "text": "Subsequent to June 30, 2026, we invested the remaining $ 21.3 billion Commitment Amount in shares of Series C Preferred Stock of OpenAI."}]},
}


class SeveralFragments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.amzn, _ = load("AMZN_periodic_2026-09-26_10")
        cls.vs = [check_one(cls.amzn, "F103", k.split(":")[1], ans) for k, ans in AMZN_F103.items()]

    def test_f103_each_fragment_verified(self):
        """F103: three separate fragments, each verified on its own"""
        self.assertTrue(all(v["verdict"] == "matches" for v in self.vs),
                        [(v["id"], v["verdict"], [r.get("problem") for r in v["results"]]) for v in self.vs])

    def test_f103_code_works_out_q1_and_total(self):
        """F103: the code works out Q1 = 28.7 - 13.7 = 15.0 and total = 28.7 + 21.3 = 50.0"""
        fr = formulas("F103", self.vs)
        self.assertTrue(fr["q1"]["paper_ok"] and fr["total"]["paper_ok"])

    def test_quote_joined_across_page_break(self):
        """a quote joined across a page break is rejected as one fragment"""
        stitched = {"found": True, "value": 21.3, "unit": "$bn", "period_type": "event",
                    "fragments": [{"role": "value", "text": "Subsequent to June 30, 2026, we funded the remaining Commitment Amount of $21.3 billion."}]}
        r = E.extract(self.amzn, item("F103", "AMZN_after"), stitched)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("not in the document", r["problem"])


class WrongMeasureColumnPeriod(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crwv, _ = load("CRWV_periodic_2026-09-26_17")
        cls.nbis, _ = load("NBIS_6k_2026-09-26_18")

    def test_rpo_for_construction_in_progress(self):
        """wrong metric: RPO offered for CoreWeave's construction in progress is rejected"""
        answer = {"found": True, "value": 103.7, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
                  "fragments": [{"role": "value", "text": "As of June 30, 2026, the Company had $ 103.7 billion of unsatisfied RPO"}]}
        r = E.extract(self.crwv, item("F135", "CRWV"), answer)
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(r["problem"].startswith("wrong measure"), r.get("problem"))

    def test_equinix_rpo(self):
        """wrong metric: the paid run's Equinix RPO answer is rejected even if asked"""
        eqix, _ = load("EQIX_periodic_2026-09-26_20")
        answer = {"found": True, "value": 15.0, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
                  "fragments": [{"role": "value", "text": "Approximately $ 15.0 billion of revenues, including deferred installation revenues, are expected to be recognized in future periods related to unsatisfied performance obligations as of June 30, 2026."}]}
        r = E.extract(eqix, item("F135", "EQIX", forms_ok=True), answer)
        self.assertEqual(r["status"], "rejected", r.get("problem"))
        self.assertTrue(r["problem"].startswith("wrong measure"), r.get("problem"))

    COL_BAD = {"found": True, "value": 33.823, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
               "fragments": [{"role": "value", "text": "Technology equipment | $33,823 | $20,903"}, {"role": "column", "text": "September 30, 2027"}]}

    def test_column_heading_not_in_document(self):
        """a column heading that is not in the document is rejected"""
        r = E.extract(self.crwv, item("F141", "CRWV_te"), self.COL_BAD)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("column fragment", r["problem"])

    def test_right_column_with_units(self):
        """the right column with its units fragment passes unchanged"""
        ok = dict(self.COL_BAD, fragments=[{"role": "value", "text": "Technology equipment | $33,823 | $20,903"}, {"role": "units", "text": "(in millions)"}])
        r = E.extract(self.crwv, item("F141", "CRWV_te"), ok)
        self.assertEqual(r["status"], "verified", r.get("problem"))
        self.assertFalse(r.get("code_read"))

    def test_wrong_period_in_sentence(self):
        """wrong period in a sentence (no table): rejected, left for a human"""
        answer = {"found": True, "value": 12.0541, "unit": "$bn", "period_end": "2025-12-31", "period_type": "instant",
                  "fragments": [{"role": "value", "text": "The aggregate estimated future undiscounted lease payments associated with these agreements amounted to $12,054.1"}]}
        r = E.extract(self.nbis, item("F152", "NBIS"), answer)
        self.assertEqual(r["status"], "rejected")
        self.assertTrue(r["problem"].startswith("wrong period"), r.get("problem"))

    def test_f150_nebius_annualised(self):
        """F150 Nebius: the cash-flow line 4,395.0 for six months, annualised by the code (x 12 / 6 = 8.79), matches $8.8bn"""
        answer = {"found": True, "value": 4.395, "unit": "$bn", "period_end": "2026-06-30", "period_type": "6m",
                  "fragments": [{"role": "value", "text": "Deferred revenue 3.0 4,395.0"}]}
        v = check_one(self.nbis, "F150", "NBIS", answer)
        self.assertEqual(v["verdict"], "matches")
        self.assertAlmostEqual(v["best"]["derived"]["value"], 8.79, delta=1e-9)


class AbsenceHistoricalNewFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.googl, _ = load("GOOGL_periodic_2026-09-26_12")
        cls.meta, _ = load("META_periodic_2026-09-26_13")

    def test_absence_needs_verified_evidence(self):
        """absence: 'no separate figure found' needs a verified evidence fragment showing where the AI looked"""
        answer = {"found": False, "fragments": [{"role": "evidence", "text": "The following table presents revenues by type"}]}
        self.assertEqual(check_one(self.googl, "F035", "GOOGL", answer)["verdict"], "no separate figure found")

    def test_absence_invented_evidence(self):
        """absence: invented evidence is rejected, not counted"""
        answer = {"found": False, "fragments": [{"role": "evidence", "text": "Revenue is disaggregated into AI and non-AI revenue"}]}
        self.assertEqual(check_one(self.meta, "F035", "META", answer)["verdict"], "not checked (extraction problems)")

    def test_absence_no_answer(self):
        """absence: no answer is not a confirmation"""
        self.assertEqual(check_one(self.meta, "F035", "META", None)["verdict"], "not checked (extraction problems)")

    def test_historical_related_statement(self):
        """historical: a related newer statement is kept as context, not as a change"""
        msft_tr, _ = load("MSFT_transcript_2026-09-26_7")
        answer = {"related": [{"summary": "Q4 FY2026: weighted average duration 2.3 years",
                               "fragments": [{"role": "value", "text": "RPO, including OpenAI, has a weighted average duration of 2.3 years."}]}]}
        v = check_one(msft_tr, "F085", "MSFT", answer)
        self.assertEqual(v["verdict"], "historical")
        self.assertEqual(len(v["related"]), 1)

    NVDA_FACT = {"topic": "guarantee", "related_rows": ["F092"],
                 "fragments": [{"role": "value", "text": "In August 2026, we entered into guarantees, capped at a total of $ 105 billion, to provide credit support"}],
                 "why_it_matters": "Replaces the $100bn investment with a $105bn guarantee."}

    def test_new_fact_comment_with_unsupported_number(self):
        """new facts: a comment using a number not in the fragments ($100bn) is removed"""
        nvda, _ = load("NVDA_periodic_2026-09-26_3")
        fc = L.check_fact(nvda, self.NVDA_FACT, {"F003", "F092"})
        self.assertTrue(fc)
        self.assertTrue(fc["why"].startswith("[the AI's comment was removed"))

    def test_new_fact_listed_once(self):
        """new facts: the same fact from two documents is listed once"""
        nvda, _ = load("NVDA_periodic_2026-09-26_3")
        fc = L.check_fact(nvda, self.NVDA_FACT, {"F003", "F092"})
        dup = L.dedupe_facts([fc, dict(fc, doc=dict(fc["doc"], title="another exhibit"))])
        self.assertEqual(len(dup), 1)
        self.assertEqual(len(dup[0]["also_in"]), 1)


class FailedAICall(unittest.TestCase):
    def test_failed_call_not_checked(self):
        """a failed AI call leaves the paper not checked (never 'same as paper')"""
        def failing_ai(system, user):
            raise W.FetchError("DeepSeek did not answer (test)")
        res = quiet_main(["--from-cache", "--only", "DLR", "--go", "--out", out("out_fail")], ai=failing_ai)
        self.assertEqual(by_id(res)["F135:DLR"]["verdict"], "not checked (extraction problems)")


if __name__ == "__main__":
    unittest.main()
