"""The basics: cleaning up text, numbers, dates, reading tables, and the period each saved filing reports on."""
import datetime as dt
import os
import unittest

from support import DOCS, FIX, D, K, T, load


class TextNumbersDates(unittest.TestCase):
    def test_clean_up(self):
        """clean-up: zero-width space removed, '$ 85.2' and '20 %' closed up"""
        self.assertEqual(K.norm("$ 85.2 billion​ and 20 %"), "$85.2 billion and 20%")

    def test_numbers_in_words(self):
        """numbers written in words are recognised (two to six years)"""
        self.assertTrue(K.number_supported(2, "servers and network equipment, two to six years"))
        self.assertTrue(K.number_supported(6, "two to six years"))

    def test_fifteen_to_nineteen(self):
        """fifteen to nineteen years"""
        self.assertTrue(K.number_supported(19, "for terms of fifteen to nineteen years"))

    def test_zero_not_found_by_scaling(self):
        """zero is not 'found' by scaling a large number"""
        self.assertFalse(K.number_supported(0, "Increase in deferred revenues | 11,363 | 4,592"))

    def test_zero_as_dash(self):
        """zero is found as a printed dash"""
        self.assertTrue(K.number_supported(0, "Increase in deferred revenues | 11,363 | —"))

    def test_compare_at_lesser_precision(self):
        """compare at the lesser precision"""
        self.assertEqual(K.compare("89.023", 89.0), "same")
        self.assertEqual(K.compare("106.347", 137.214), "different")

    def test_dates(self):
        """dates in text and in URLs"""
        self.assertEqual(K.dates_in("July 29th, 2026"), [dt.date(2026, 7, 29)])
        self.assertEqual(K.dates_in("Call-26-August-2026-5_00"), [dt.date(2026, 8, 26)])


class Tables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = D.html_to_text(open(os.path.join(FIX, "nbis_table.html"), encoding="utf-8").read())

    def test_nebius_row_on_one_line(self):
        """html_to_text keeps a Nebius table row on one line (cells hold <p>, empty cells hold a zero-width space)"""
        self.assertIn("Assets not yet in use | 2,417.4 | 7,836.4", self.text, self.text[:300])

    def test_column_dates_in_order(self):
        """the code reads the column dates 'December 31, | June 30,' over '2025 | 2026' in the right order"""
        d = K.Doc(self.text)
        hits, _ = d.find("Assets not yet in use | 2,417.4 | 7,836.4")
        row = T.read_row(d, hits[0]) if hits else None
        self.assertTrue(row, "row not found")
        self.assertEqual(row["dates"], [dt.date(2025, 12, 31), dt.date(2026, 6, 30)])
        self.assertEqual(row["values"], [2417.4, 7836.4])

    def test_ordinary_tables(self):
        """ordinary tables unchanged"""
        text = D.html_to_text("<table><tr><td>Leases not yet commenced</td><td>$</td><td>4,018</td><td></td><td>137,214</td></tr></table>")
        self.assertIn("Leases not yet commenced | $4,018 | 137,214", text)


class SavedFilings(unittest.TestCase):
    def test_period_of_each_saved_filing(self):
        """the period each saved filing reports on is read correctly"""
        expected = {"AMZN_periodic": "2026-06-30", "CRWV_periodic": "2026-06-30", "NBIS_6k": "2026-06-30", "NVDA_periodic": "2026-07-26",
                    "ORCL_periodic": "2026-08-31", "MSFT_periodic": "2026-06-30", "EQIX_periodic": "2026-06-30", "DLR_periodic": "2026-06-30",
                    "GOOGL_results": "2026-06-30", "ORCL_results": "2026-08-31", "META_periodic": "2026-06-30"}
        got = {}
        for f in os.listdir(DOCS):
            key = "_".join(f.split("_")[:2])
            if f.endswith(".txt") and key in expected:
                got[key] = load(f[:-4])[1]["period_end"]
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
