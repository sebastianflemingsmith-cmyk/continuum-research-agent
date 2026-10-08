"""New facts (leads): boilerplate, no figure, repeats of the paper's own figures, the same fact in several documents."""
import unittest

from support import A, K, L, full_runs_28sep


class LeadRules(unittest.TestCase):
    def test_non_gaap_definition_is_boilerplate(self):
        """a non-GAAP definition is set aside as boilerplate"""
        self.assertEqual(L.lead_problem("Free cash flow definition", "Free cash flow is cash flow from operations reduced by purchases of property and equipment."),
                         "boilerplate")

    def test_change_of_definition_kept(self):
        """a change of definition is kept even without a figure"""
        self.assertEqual(L.lead_problem("Reclassification", "During the second quarter we reclassified a company from ACIE to Hyperscale and recast the prior period revenue."), "")

    def test_financing_structure_kept(self):
        """a financing structure (take-or-pay) is kept even without a figure"""
        self.assertEqual(L.lead_problem("Take-or-pay", "NVIDIA provides a take-or-pay commitment on a portion of the facility's capacity"), "")

    def test_opinion_set_aside(self):
        """an opinion without a figure is set aside"""
        self.assertEqual(L.lead_problem("Capacity", "we believe that overall industry capacity is going to remain tight for the foreseeable future."), "no figure")

    def test_paper_figure_is_repeat(self):
        """AWS's $25bn AI run rate is the paper's own figure (F034): set aside as a repeat"""
        _, all_repeats = L.repeats("Exceeded a $25 billion annual revenue run rate for AWS's AI business", L.known_values("AMZN"))
        self.assertTrue(all_repeats)

    def test_same_number_other_measure(self):
        """a figure that equals one of the paper's numbers but names another measure is not a repeat"""
        _, all_repeats = L.repeats("In June 2026, we issued an aggregate of $ 25.0 billion of senior unsecured notes", L.known_values("NVDA"))
        self.assertFalse(all_repeats)

    def test_near_is_not_the_same_amount(self):
        """two amounts are the same only if one rounds to the other: $66,594m is not $66.9bn (Microsoft's finance leases and
        capex were merged), and 128,894 is not 128,320 (Amazon's long-term debt and capex); 35,802 is $35.8bn"""
        same = lambda a, b: K.same_amount((float(a.replace(",", "")), a), (float(b.replace(",", "")), b))
        self.assertEqual([same("66,594", "66.9"), same("128,894", "128,320"), same("46,172", "46.2")], [False, False, True])
        self.assertEqual([same("35,802", "35.8"), same("750", "0.75"), same("105", "105,000"), same("24,896", "24,896")], [True] * 4)
        self.assertFalse(same("1", "746"))                       # $1 billion is not $746 million

    def test_same_lead_both_ways(self):
        """same_lead gives the same answer both ways round (Amazon's '$1 billion' lead and its debt table had joined one way)"""
        q = "https://www.sec.gov/amzn-ex991.htm"
        invest = dict(link=q, fragments=[{"role": "value", "text": "Announced an investment of $1 billion to create AWS Forward Deployed Engineering, and $1 billion more."}])
        debt = dict(link=q, fragments=[{"role": "value", "text": "Long-term debt | 65,648 | 128,894 Proceeds from long-term debt | — | 13,557 | 746 | 66,998"}])
        self.assertEqual((L.same_lead(invest, debt), L.same_lead(debt, invest)), (False, False))

    def test_round_number_in_one_document(self):
        """in one document, a round number such as $100.0 billion does not make two leads one fact (AWS's OpenAI and
        Anthropic deals); between two documents it does (Nvidia's $500 billion platforms in the release and the 10-Q)"""
        q = "https://www.sec.gov/amzn-20260630.htm"
        openai = dict(link=q, fragments=[{"role": "value", "text": "In Q2 2026, AWS and OpenAI announced an expansion of the existing commitment by $ 100.0 billion over 8.0 years."}])
        anthropic = dict(link=q, fragments=[{"role": "value", "text": "AWS and Anthropic announced an expansion of the existing multi-year commitment by more than $ 100.0 billion over 10.0 years."}])
        self.assertFalse(L.same_lead(openai, anthropic))
        self.assertTrue(L.same_lead(openai, dict(anthropic, link="https://www.sec.gov/amzn-ex991.htm")))

    def test_same_sentence_is_the_same_fact(self):
        """two leads quoting the same sentence are one fact, whatever else they quote"""
        shared = "There were no borrowings outstanding under the Term Loan as of June 30, 2026."
        a = dict(link="x", topic="Undrawn term loan", fragments=[{"role": "value", "text": "The Term Loan matures three years from the date of borrowing."},
                                                                 {"role": "value", "text": shared}])
        b = dict(link="x", topic="Term loan not yet drawn", fragments=[{"role": "value", "text": shared},
                                                                       {"role": "value", "text": "We may draw on the Term Loan until the commitment ends."}])
        self.assertTrue(L.same_lead(a, b))
        self.assertFalse(L.same_lead(a, dict(b, fragments=b["fragments"][1:])))

    def test_prompt(self):
        """the prompt now asks for at most 3 facts a document, each with a figure, and no boilerplate"""
        self.assertIn("AT MOST 3 facts", A.SYSTEM)
        self.assertIn("non-GAAP", A.SYSTEM)


class FullRun28Sep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.first, cls.second = full_runs_28sep()
        cls.facts = {f["topic"]: f for f in cls.first["facts"]}
        cls.aside = {f["topic"]: f for f in cls.first["set_aside"]}

    def test_amazon_definitions_set_aside(self):
        """full run of 28 Sep: Amazon's non-GAAP definitions and reconciliation notes are set aside"""
        for t in ("Free cash flow definition", "Free cash flow limitations", "Reconciliation location", "Foreign exchange non-GAAP measure"):
            with self.subTest(t):
                self.assertIn(t, self.aside)
                self.assertEqual(self.aside[t]["set_aside"], "boilerplate")

    def test_useful_leads_stay(self):
        """useful leads stay: Alphabet's $811.0bn of purchase commitments, CoreWeave's customer concentration"""
        self.assertIn("Purchase commitments", self.facts)
        self.assertTrue(any("811.0" in x["text"] for x in self.facts["Purchase commitments"]["fragments"]))
        self.assertIn("Customer concentration in the quarter", self.facts)

    def test_sb_energy_one_lead(self):
        """Nvidia's $105bn SB Energy guarantee, reported in the 10-Q, the CFO commentary and the call, is one lead, in the 10-Q's words"""
        sb = next((f for f in self.first["facts"] if "SB Energy" in f["topic"]), None)
        self.assertTrue(sb)
        self.assertEqual(sb["doc"]["form"], "10-Q")
        self.assertEqual(len(sb["also_in"]), 2)

    def test_fewer_leads_shown(self):
        """full run of 28 Sep: fewer leads shown than the 91 before, and at least 10 set aside (one line each)"""
        self.assertLess(len(self.first["facts"]), 91)
        self.assertGreaterEqual(len(self.first["set_aside"]), 10)

    def test_second_run_marks_leads_seen(self):
        """a second run that finds the same leads marks every one as already on the open list"""
        self.assertTrue(self.second["facts"])
        self.assertEqual([f["topic"] for f in self.second["facts"] if not f.get("seen_before")], [])


if __name__ == "__main__":
    unittest.main()
