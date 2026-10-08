"""The second full live run (28 Sep 2026 18:49) exposed two checker problems: a percentage read on the wrong scale
(Meta's 20% share proposed as 0.2) and short absence evidence rejected. These tests keep them fixed."""
import unittest

from support import E, check_one, item, load28

META_SHARE = {"found": True, "value": 0.2, "value_as_printed": "20 %", "unit": "%", "period_end": "2026-06-30", "period_type": "instant",
              "fragments": [{"role": "value", "text": "Our non-marketable equity method investments include an arrangement, entered into in October 2025, to co-develop a data center campus in Louisiana (the Venture), in which we hold a 20 % membership interest."}]}


class Percentages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta, _ = load28("META_periodic_2026-09-28_1", "META")

    def test_share_read_on_printed_scale(self):
        """F046: the AI's 0.2 for a share printed as '20 %' is read as 20%, not proposed as a change from 20 to 0.2"""
        v = check_one(self.meta, "F046", "META_share", META_SHARE)
        self.assertEqual(v["verdict"], "newer, unchanged")
        self.assertAlmostEqual(v["best"]["value"], 20, delta=1e-9)

    def test_percentage_on_no_scale(self):
        """a percentage that matches the printed one on no scale is rejected"""
        self.assertEqual(E.extract(self.meta, item("F046", "META_share"), dict(META_SHARE, value=7))["status"], "rejected")


class AbsenceEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.googl, _ = load28("GOOGL_results_2026-09-28_1", "GOOGL")

    def test_short_table_row_counts(self):
        """F035: a short table row ('YouTube ads | 9,796 | 11,055') counts as evidence of where the AI looked"""
        answer = {"found": False, "fragments": [
            {"role": "evidence", "text": "Google Cloud saw a meaningful acceleration in growth as revenues increased 82% to $24.8 billion, led by an increase in Google Cloud Platform (GCP) across enterprise AI Solutions and enterprise AI Infrastructure, as well as core GCP services."},
            {"role": "evidence", "text": "Google Cloud | 13,624 | 24,768"}, {"role": "evidence", "text": "YouTube ads | 9,796 | 11,055"}]}
        self.assertEqual(check_one(self.googl, "F035", "GOOGL", answer)["verdict"], "no separate figure found")

    def test_one_word_still_rejected(self):
        """a one-word 'evidence' fragment still does not count"""
        r = E.extract(self.googl, item("F035", "GOOGL"), {"found": False, "fragments": [{"role": "evidence", "text": "revenues"}]})
        self.assertEqual(r["status"], "rejected")

    def test_invented_context_still_rejected(self):
        """F103: an invented context quote is still rejected (the paper is not marked checked)"""
        amzn, _ = load28("AMZN_periodic_2026-09-28_1", "AMZN")
        answer = {"found": True, "value": 21.3, "unit": "$bn", "period_end": "2026-06-30", "period_type": "event",
                  "fragments": [{"role": "value", "text": "Subsequent to June 30, 2026, we invested the remaining $ 21.3 billion Commitment Amount in shares of Series C Preferred Stock of OpenAI."},
                                {"role": "context", "text": "Subsequent to June 30, 2026, we funded the remaining Commitment Amount of $21.3 billion."}]}
        r = E.extract(amzn, item("F103", "AMZN_after"), answer)
        self.assertEqual(r["status"], "rejected")
        self.assertIn("context fragment not in the document", r["problem"])


if __name__ == "__main__":
    unittest.main()
