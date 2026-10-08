"""Periods read from fiscal-quarter headings ('Q2 FY27', 'Fiscal 2027 Q1', 'Q2 2026') and from call transcripts,
on the documents of the full runs of 28 Sep 2026."""
import json
import os
import unittest

from support import FIX, D, E, T, by_id, full_runs_28sep, item, load28, row_at


class FiscalQuarters(unittest.TestCase):
    def test_fiscal_quarter_ends(self):
        """fiscal quarters: Nvidia Q2 FY27 ends Jul 2026, Microsoft Q4 FY26 Jun 2026, Oracle Q1 FY27 Aug 2026, Amazon Q2 2026 Jun 2026"""
        self.assertEqual(T.fiscal_quarter_end(2, 2027, 1), (2026, 7))
        self.assertEqual(T.fiscal_quarter_end(4, 2026, 6), (2026, 6))
        self.assertEqual(T.fiscal_quarter_end(1, 2027, 5), (2026, 8))
        self.assertEqual(T.fiscal_quarter_end(2, 2026, 12), (2026, 6))

    def test_q2_2026_only_for_calendar_years(self):
        """'Q2 2026' without 'FY' is read only for a company whose fiscal year is the calendar year"""
        self.assertIsNone(T.label_period("Q2 2026", 1))
        self.assertEqual(T.label_period("Q2 2026", 12)["end"], (2026, 6))


class TableHeadings(unittest.TestCase):
    def test_nvidia_quarter_columns(self):
        """Nvidia CFO commentary: columns 'Q2 FY27 | Q1 FY27 | Q2 FY26' read as the quarters to Jul 2026, Apr 2026, Jul 2025"""
        nv, _ = load28("NVDA_results_2026-09-28_1", "NVDA")
        r = row_at(nv, "Data Center | $89,023 | $75,246 | $41,096 | 18 | % | 117 | %", 89023)
        self.assertTrue(r and r["columns_match"], r and r["cols"])
        self.assertEqual((r["cols"][0]["end"], r["cols"][0]["months"], r["cols"][2]["end"]), ((2026, 7), 3, (2025, 7)))

    def test_nvidia_change_columns(self):
        """Nvidia: the Q/Q and Y/Y columns belong to the latest quarter"""
        nv, _ = load28("NVDA_results_2026-09-28_1", "NVDA")
        r = row_at(nv, "Data Center | $89,023 | $75,246 | $41,096 | 18 | % | 117 | %", 89023)
        self.assertTrue(r)
        self.assertEqual((r["cols"][3]["kind"], r["cols"][4]["kind"]), ("change", "change"))
        self.assertEqual(r["cols"][4]["end"], (2026, 7))

    def test_amazon_trend_table(self):
        """Amazon trend table: 'Q1 2025 … Q2 2026 | Y/Y % Change' read by the code"""
        am, _ = load28("AMZN_results_2026-09-28_1", "AMZN")
        r = row_at(am, "Net sales | $29,267 | $30,873 | $33,006 | $35,579 | $37,587 | $42,232 | 37 | %", 42232)
        self.assertTrue(r and r["columns_match"])
        self.assertEqual(r["cols"][5]["end"], (2026, 6))

    def test_amazon_segment_table(self):
        """Amazon segment table: a fragment over two lines ('AWS' / 'Net sales | …') is read at the row, under 'Three Months Ended | Six Months Ended' over four years"""
        am, _ = load28("AMZN_results_2026-09-28_1", "AMZN")
        r = row_at(am, "AWS\nNet sales | $30,873 | $42,232 | $60,140 | $79,819", 42232)
        self.assertTrue(r)
        self.assertEqual(r["label"], "Net sales")
        self.assertEqual([(c["end"], c["months"]) for c in r["cols"]], [((2025, 6), 3), ((2026, 6), 3), ((2025, 6), 6), ((2026, 6), 6)])

    def test_wrong_period_length_corrected(self):
        """wrong period length: the AI's six-month AWS figure (79.8) is caught, and the code reads the three-month 42.232 from the same row"""
        am, _ = load28("AMZN_results_2026-09-28_1", "AMZN")
        six = {"found": True, "value": 79.819, "unit": "$bn", "period_end": "2026-06-30", "period_type": "3m",
               "fragments": [{"role": "value", "text": "AWS\nNet sales | $30,873 | $42,232 | $60,140 | $79,819"}]}
        r = E.extract(am, item("F040", "AMZN"), six)
        self.assertEqual(r["status"], "verified", r.get("problem"))
        self.assertTrue(r.get("code_read"))
        self.assertAlmostEqual(r["value"], 42.232, delta=1e-9)
        self.assertIn("length", r.get("ai_problem", ""))

    def test_ttm_row(self):
        """a row labelled 'TTM' under quarter columns is read as twelve months"""
        am, _ = load28("AMZN_results_2026-09-28_1", "AMZN")
        fcf = row_at(am, "Free cash flow -- TTM (1) | $25,925 | $18,184 | $14,788 | $11,194 | $1,232 | $(7,604) | (142) | %", -7604)
        self.assertTrue(fcf)
        self.assertEqual(fcf["cols"][5]["months"], 12)

    def test_oracle_one_number_per_line(self):
        """Oracle: one number per line under 'Fiscal 2026 | Fiscal 2027' / 'Q1 … TOTAL': the empty later quarters are found because the quarters add up to the totals"""
        orc, _ = load28("ORCL_results_2026-09-28_1", "ORCL")
        r = row_at(orc, "Cloud infrastructure 3,347 4,079 4,888 5,787 18,101 7,388 7,388", 7388)
        self.assertTrue(r and r["columns_match"], r and [(c["label"], c["end"]) for c in r["cols"]])
        self.assertEqual((r["cols"][5]["label"], r["cols"][5]["end"], r["cols"][4]["months"]), ("Q1 FY2027", (2026, 8), 12))

    def test_alphabet_ttm_lengths_unknown(self):
        """Alphabet: 'Quarter Ended | TTM' over 'Q3 2025 … Q2 2026 | Q2 2026': the dates are read, the lengths left unknown rather than guessed"""
        go, _ = load28("GOOGL_results_2026-09-28_1", "GOOGL")
        r = row_at(go, "Free cash flow | $24,461 | $24,551 | $10,116 | $(5,855) | $53,273", -5855)
        self.assertTrue(r)
        self.assertEqual(r["cols"][3]["end"], (2026, 6))
        self.assertIsNone(r["cols"][3]["months"])


class CallTranscripts(unittest.TestCase):
    def test_quarter_from_title_or_link(self):
        """call transcripts: the quarter is read from the title or link (Microsoft Q4 FY2026, Nvidia Q2 FY2027, Meta Q2 2026)"""
        got = {t: (m["fiscal_quarter"], m["period_end"][:7]) for t, m in
               (("MSFT", load28("MSFT_transcript_2026-09-28_1", "MSFT")[1]), ("NVDA", load28("NVDA_transcript_2026-09-28_1", "NVDA")[1]),
                ("META", load28("META_transcript_2026-09-28_1", "META")[1]))}
        self.assertEqual(got, {"MSFT": ("Q4 FY2026", "2026-06"), "NVDA": ("Q2 FY2027", "2026-07"), "META": ("Q2 FY2026", "2026-06")})

    def test_old_quarter_in_title_not_used(self):
        """a title whose quarter ended long before the call is not used"""
        m = D.describe_doc(dict(title="Second Quarter 2025: Transcript", url="x", form="transcript"), "Operator: welcome, 26 August 2026", "NVDA")
        self.assertFalse(m["fiscal_quarter"])

    def test_sentence_on_the_call_confirmed(self):
        """F089: a sentence on the Q4 FY2026 call that names no other period is confirmed for the quarter to Jun 2026"""
        mt, _ = load28("MSFT_transcript_2026-09-28_1", "MSFT")
        r = E.extract(mt, item("F089", "MSFT"), {"found": True, "value": 84, "unit": "%", "period_end": "2026-06-30", "period_type": "instant",
                                                 "fragments": [{"role": "value", "text": "Commercial remaining performance obligation grew 84% to $678 billion."}]})
        self.assertEqual(r["status"], "verified", r.get("problem"))
        self.assertTrue(r["period_confirmed"], r.get("period_evidence"))
        self.assertIn("Q4 FY2026", r["period_evidence"])

    def test_outlook_sentence_not_confirmed(self):
        """a sentence about another period (an FY27 outlook) is not confirmed"""
        mt, _ = load28("MSFT_transcript_2026-09-28_1", "MSFT")
        loose = dict(item("F089", "MSFT"), comp=dict(item("F089", "MSFT")["comp"], must=[]))
        r = E.extract(mt, loose, {"found": True, "value": 20, "unit": "%", "period_end": "2026-06-30", "period_type": "instant",
                                  "fragments": [{"role": "value", "text": "And finally, we expect our FY27 effective tax rate to be approximately 20%."}]})
        self.assertEqual(r["status"], "verified", r.get("problem"))
        self.assertFalse(r["period_confirmed"])
        self.assertTrue(any("another period" in w for w in r["warnings"]), r.get("warnings"))


class FullRun28Sep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = full_runs_28sep()[0]
        cls.V = by_id(cls.res)
        cls.before = json.load(open(os.path.join(FIX, "verdicts_full_run_2026-09-28_before_fixes.json"), encoding="utf-8"))["verdicts"]
        cls.was_unconfirmed = sorted(k for k, v in cls.before.items() if v == "matches, period not confirmed")

    def test_eight_unconfirmed_now_confirmed(self):
        """full run of 28 Sep: the eight 'period not confirmed' matches (Nvidia, AWS, Oracle, Microsoft's call) are now confirmed"""
        self.assertEqual(self.was_unconfirmed, ["F003:NVDA", "F005:NVDA", "F006:NVDA", "F006:NVDA_growth", "F040:AMZN", "F042:ORCL", "F089:MSFT", "F089:MSFT_ex"])
        self.assertEqual({k: self.V[k]["verdict"] for k in self.was_unconfirmed}, {k: "matches" for k in self.was_unconfirmed})

    def test_every_other_verdict_unchanged(self):
        """full run of 28 Sep: every other verdict is exactly as before the fixes"""
        changed = {k: (self.before[k], self.V.get(k, {}).get("verdict")) for k in self.before
                   if k not in self.was_unconfirmed and self.before[k] != self.V.get(k, {}).get("verdict")}
        self.assertEqual(changed, {})
        self.assertEqual(set(self.before), set(self.V))

    def test_no_period_not_confirmed_left(self):
        """the report has no 'period not confirmed' left for this run"""
        self.assertNotIn("period not confirmed**", self.res["report"])


if __name__ == "__main__":
    unittest.main()
