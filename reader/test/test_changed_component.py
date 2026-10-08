"""A changed component in a newer document: a synthetic CoreWeave 10-Q for 30 Sep 2026 (fixtures/CRWV_periodic_2026-11-12_1.txt,
invented figures, clearly marked) in which one of F152's three lease figures has changed."""
import unittest

from support import by_id, changed_run, formulas


class ChangedComponent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = changed_run()
        cls.V = by_id(cls.res)

    def test_change_detected(self):
        """a genuine change in one company's component (CoreWeave 35.5 -> 41.2 at 30 Sep) is detected"""
        v = self.V["F152:CRWV"]
        self.assertEqual(v["verdict"], "newer figure")
        self.assertAlmostEqual(v["best"]["value"], 41.2, delta=1e-9)

    def test_other_components_unaffected(self):
        """the other companies' components are unaffected"""
        self.assertEqual(self.V["F152:NBIS"]["verdict"], "matches")
        self.assertEqual(self.V["F152:ORCL"]["verdict"], "matches")

    def test_total_recomputed(self):
        """the combined total is recomputed by the code: 41.2 + 12.0541 + 288 = 341.2541"""
        fr = formulas("F152", [self.V["F152:CRWV"], self.V["F152:NBIS"], self.V["F152:ORCL"]])
        self.assertTrue(fr["total"]["changed"])
        self.assertAlmostEqual(fr["total"]["at_new"], 341.2541, delta=1e-6)

    def test_old_column_corrected(self):
        """an old-column answer (9.376 at 31 Dec 2025) is corrected by the code to the latest column (14.0 at 30 Sep 2026)"""
        v = self.V["F135:CRWV"]
        self.assertEqual(v["verdict"], "newer figure")
        self.assertAlmostEqual(v["best"]["value"], 14.0, delta=1e-9)
        self.assertTrue(v["best"].get("code_read"))

    def test_proposals(self):
        """proposals: the changed component and the recomputed total, each pending"""
        kinds = {(p["row"], p.get("component"), p["kind"]) for p in self.res["proposals"]}
        self.assertIn(("F152", "CRWV", "newer figure"), kinds)
        self.assertIn(("F152", "total", "formula with newer inputs"), kinds)

    def test_no_proposal_for_unchanged(self):
        """no proposal for the unchanged components"""
        self.assertFalse(any(p["row"] == "F152" and p.get("component") in ("NBIS", "ORCL") for p in self.res["proposals"]))


if __name__ == "__main__":
    unittest.main()
