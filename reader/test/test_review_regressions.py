"""Regression tests for problems found in an independent review of this code."""
import json
import os
import unittest

from support import CACHE2, FIX, LEDGER, A, E, K, R, VR, by_id, changed_run, check_one, formulas, item, load, out, paid_run, quiet_main


class Review(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.crwv, _ = load("CRWV_periodic_2026-09-26_17")
        cls.msft_tr, _ = load("MSFT_transcript_2026-09-26_7")
        cls.googl, _ = load("GOOGL_periodic_2026-09-26_12")

    # ---- columns, measures, period lengths
    def test_old_column_in_millions(self):
        """old column caught even when the AI gives the number in $m and claims the latest date"""
        r = E.extract(self.crwv, item("F141", "CRWV_te"), {"found": True, "value": 20903, "unit": "$m", "period_end": "2026-06-30", "period_type": "instant",
                                                           "fragments": [{"role": "value", "text": "Technology equipment | $33,823 | $20,903"}]})
        self.assertTrue(r.get("code_read"), r.get("problem"))
        self.assertAlmostEqual(r["value"], 33.823, delta=1e-9)

    def test_whole_words_only(self):
        """'rpo' is not found inside 'PowerPoint' (whole words only)"""
        r = E.extract(self.msft_tr, item("F089", "MSFT"), {"found": True, "value": 84, "unit": "%", "period_end": "2026-06-30", "period_type": "instant",
                                                           "fragments": [{"role": "value", "text": "up to 84% reduced GPU costs in PowerPoint with MAI-Image-2.5."}]})
        self.assertEqual(r["status"], "rejected")
        self.assertIn("wrong measure", r["problem"])

    def test_measure_checked_at_value_fragment(self):
        """the measure is checked around the fragment that carries the number, not the first one"""
        r = E.extract(self.crwv, item("F135", "CRWV"), {"found": True, "value": 103.7, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
                                                        "fragments": [{"role": "value", "text": "Construction in progress | 11,918 | 9,376"},
                                                                      {"role": "value", "text": "As of June 30, 2026, the Company had $ 103.7 billion of unsatisfied RPO"}]})
        self.assertEqual(r["status"], "rejected")
        self.assertIn("wrong measure", r["problem"])

    def test_six_months_not_a_quarter(self):
        """a six-month figure is not accepted where the rule needs the quarter"""
        r = E.extract(self.crwv, item("F141", "CRWV_rev"), {"found": True, "value": 4.653, "unit": "$bn", "period_end": "2026-06-30", "period_type": "6m",
                                                            "fragments": [{"role": "value", "text": "Revenue | $2,575 | $1,212 | $4,653 | $2,194"}]})
        self.assertEqual(r["status"], "rejected")
        self.assertIn("period length", r["problem"])

    # ---- comparing numbers
    def test_compare_different(self):
        """compare: 6 is not 5.5 years, 0 is not 0.4, and halves are treated the same way"""
        self.assertEqual(K.compare("6", 5.5), "different")
        self.assertEqual(K.compare("0", 0.412), "different")
        self.assertEqual(K.compare("15", 15.5), "different")

    def test_compare_rounded(self):
        """compare: rounded figures still match (2.7 = 2.73, 16.0 = 15.955, 89.0 = 89.023)"""
        self.assertEqual(K.compare("2.7", 2.73), "same")
        self.assertEqual(K.compare("16.0", 15.955), "same")
        self.assertEqual(K.compare("89.023", 89.0), "same")

    def test_f003_release_and_10q(self):
        """F003: 89.0 in the release and 89.023 in the 10-Q are not a conflict"""
        self.assertIn(by_id(paid_run())["F003:NVDA"]["verdict"], ("matches", "matches, period not confirmed"))

    # ---- absence and text items
    def test_f155_ratio_is_absence(self):
        """F155's 'no ratio disclosed' part is handled as an absence claim"""
        ask, _ = R.items_for("CRWV", "periodic", {"form": "10-Q", "period_end": "2026-06-30"}, LEDGER)
        self.assertEqual(next(i for i in ask if i["id"] == "F155:CRWV_ratio")["type"], "absence")

    def test_one_word_evidence(self):
        """absence: a one-word 'evidence' fragment does not count"""
        r = E.extract(self.googl, item("F035", "GOOGL"), {"found": False, "fragments": [{"role": "evidence", "text": "revenue"}]})
        self.assertEqual(r["status"], "rejected")

    def test_found_false_as_text(self):
        """'found': "false" as text is read as false"""
        r = E.extract(self.googl, item("F035", "GOOGL"), {"found": "false", "fragments": [{"role": "evidence", "text": "The following table presents revenues by type"}]})
        self.assertEqual(r["status"], "absent")

    def test_text_item_words_in_fragment(self):
        """text items: the AI's words must be in its own fragment"""
        orcl, _ = load("ORCL_periodic_2026-09-26_16")
        r = E.extract(orcl, item("F065", "ORCL_start"),
                      {"found": True, "value_text": "first quarter of fiscal 2027", "period_end": "2026-08-31", "period_type": "instant",
                       "fragments": [{"role": "value", "text": "that are generally expected to commence between the second quarter of fiscal 2027 a nd fiscal 2029"}]})
        self.assertEqual(r["status"], "rejected")
        self.assertIn("not in its value fragment", r["problem"])

    def test_odd_answer_shapes(self):
        """odd answer shapes (numbers and lists where text is expected) do not crash"""
        r = E.extract(self.crwv, item("F141", "CRWV_te"), {"found": True, "value": 33.823, "unit": 7, "period_end": 20260630, "period_type": ["instant"],
                                                           "fragments": [{"role": 3, "text": "Technology equipment | $33,823 | $20,903"}]})
        self.assertIn(r["status"], ("verified", "rejected"))

    def test_historical_no_answer(self):
        """historical: no answer is not reported as 'no related statement found'"""
        self.assertEqual(check_one(self.msft_tr, "F085", "MSFT", None)["verdict"], "historical, not fully checked")

    def test_short_context_many_times(self):
        """a short context fragment that occurs many times ('June 30, 2026') is found next to the figure"""
        r = E.extract(self.crwv, item("F152", "CRWV"), {"found": True, "value": 35.5, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
                                                        "fragments": [{"role": "value", "text": "The aggregate amount of estimated future undiscounted lease payments associated with such leases is $ 35.5 billion."},
                                                                      {"role": "column", "text": "June 30, 2026"}]})
        self.assertEqual(r["status"], "verified", r.get("problem"))

    def test_answers_in_odd_shapes(self):
        """answers given as a dict of items, and new_facts given as one object, are read without crashing"""
        odd = {"CRWV_periodic_2026-11-12_1": {
            "items": {"F152:CRWV": {"found": True, "value": 41.2, "unit": "$bn", "period_end": "2026-09-30", "period_type": "instant",
                                    "fragments": [{"role": "value", "text": "The aggregate amount of estimated future undiscounted lease payments associated with such leases is $ 41.2 billion."}]}},
            "new_facts": {"topic": "one fact, not a list", "fragments": ["As of September 30, 2026, the Company had $ 110.0 billion of unsatisfied RPO."]}}}
        path = out("odd.json")
        json.dump(odd, open(path, "w"))
        res = quiet_main(["--from-cache", "--only", "CRWV", "--answers", path, "--out", out("out_odd")], folder=CACHE2)
        self.assertEqual(by_id(res)["F152:CRWV"]["verdict"], "newer figure")
        self.assertEqual(len(res["facts"]), 1)

    # ---- formulas and periods
    def test_formulas_never_mix_periods(self):
        """formulas never mix periods: 30 Sep equipment with 30 Jun revenue is not worked out"""
        fr = formulas("F141", [by_id(changed_run())["F141:CRWV_te"], VR.combine(item("F141", "CRWV_rev"), [], [])])["replacement_bill"]
        self.assertIn("different dates", fr.get("new_blocked") or "")

    def test_f141_paper_inputs_mix_dates(self):
        """F141: the report notes that the paper's own inputs mix 31 Dec 2025 equipment with Q2 2026 revenue"""
        V = by_id(paid_run())
        fr = formulas("F141", [V["F141:CRWV_te"], V["F141:CRWV_rev"]])["replacement_bill"]
        self.assertIn("different dates", fr.get("paper_period_note") or "")

    # ---- the command's options
    def test_answers_needs_from_cache(self):
        """--answers without --from-cache stops with a message instead of silently missing every answer"""
        with self.assertRaises(SystemExit):
            quiet_main(["--answers", os.path.join(FIX, "answers_paid_run.json")])

    def test_answers_folder_latest(self):
        """--answers can read a folder of saved answers and takes the latest for each document"""
        folder = out("answers_folder")
        os.makedirs(folder, exist_ok=True)
        json.dump({"items": []}, open(os.path.join(folder, "X_periodic_2026-09-26_1.answer 2026-09-26 10.00.00.json"), "w"))
        json.dump({"items": [{"item": "late"}]}, open(os.path.join(folder, "X_periodic_2026-09-26_1.answer 2026-09-26 12.00.00.json"), "w"))
        self.assertEqual(A.load_answers(folder)["X_periodic_2026-09-26_1"]["items"][0]["item"], "late")


if __name__ == "__main__":
    unittest.main()
