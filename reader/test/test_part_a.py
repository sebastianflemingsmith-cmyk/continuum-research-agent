"""Part A (reader_sec.py): the period arithmetic on the SEC's labelled data, a run with the SEC unreachable, and a replay
of the SEC data saved by record_sec_data.py (skipped until that has been run once). The small tables below are invented
test data, not real figures."""
import datetime as dt
import json
import os
import unittest

import record_sec_data as REC
from support import W, S, WATCH_LIST, out


def facts(*rows):
    return S.Facts({"USD": [dict(r) for r in rows]})


Q1 = dict(start="2026-01-01", end="2026-03-31", val=10e9, filed="2026-05-01", form="10-Q", accn="0000000001-26-000001")
H1 = dict(start="2026-01-01", end="2026-06-30", val=25e9, filed="2026-08-01", form="10-Q", accn="0000000001-26-000002")
Q2 = dict(start="2026-04-01", end="2026-06-30", val=15e9, filed="2026-08-01", form="10-Q", accn="0000000001-26-000002")
FY = dict(start="2025-01-01", end="2025-12-31", val=40e9, filed="2026-02-15", form="10-K", accn="0000000001-26-000000")
CASH = dict(end="2026-06-30", val=7e9, filed="2026-08-01", form="10-Q", accn="0000000001-26-000002")


class PeriodArithmetic(unittest.TestCase):
    def test_three_month_figure_used(self):
        """a quarter is the three-month figure when the SEC has one"""
        q = facts(Q1, H1, Q2).quarter()
        self.assertEqual((q["end"], q["val"], q["how"]), ("2026-06-30", 15e9, "three-month figure"))

    def test_quarter_from_year_to_date(self):
        """without one, the quarter is year to date minus the previous year to date (25 - 10 = 15)"""
        q = facts(Q1, H1).quarter()
        self.assertEqual((q["start"], q["end"], q["val"]), ("2026-03-31", "2026-06-30", 15e9))
        self.assertEqual(q["how"], "year to date to 2026-06-30 minus year to date to 2026-03-31")

    def test_restatement_wins(self):
        """the most recently filed version of a period is used (restatements win)"""
        restated = dict(Q2, val=16e9, filed="2027-02-01")
        self.assertEqual(facts(Q1, H1, Q2, restated).quarter()["val"], 16e9)

    def test_year_to_date_and_full_year(self):
        """year to date takes the longest period to the latest date; a full year is 350 to 380 days"""
        f = facts(FY, Q1, H1, Q2)
        self.assertEqual(f.ytd()["val"], 25e9)
        self.assertEqual(f.fy()["end"], "2025-12-31")
        self.assertEqual(f.latest_end("fy"), "2025-12-31")
        self.assertEqual(f.latest_end("ytd"), "2026-06-30")

    def test_balance_on_a_date(self):
        """a balance is the latest one on a single date"""
        f = S.Facts({"USD": [CASH, dict(CASH, end="2025-12-31", val=5e9)]})
        self.assertEqual(f.instant()["val"], 7e9)
        self.assertEqual(f.get("i", "2025-12-31")["val"], 5e9)

    def test_year_to_date_annualised(self):
        """six months to date, annualised: 25 x 12 / 6 = 50"""
        self.assertEqual(facts(Q1, H1).get("ytd_ann")["val"], 50e9)

    def test_formatting(self):
        """figures are shown in billions, with the unit when it is not dollars"""
        self.assertEqual((S.fmt(-5.855, 3), S.fmt(16.53, 2, "%"), S.fmt(89, 0, "days"), S.fmt(None, 1)),
                         ("-$5.855bn", "16.53%", "89 days", "—"))


class Runs(unittest.TestCase):
    def test_sec_unreachable(self):
        """with the SEC unreachable, every figure is 'could not check' and the report says why; nothing is proposed"""
        def offline(url, ua, accept="*/*", timeout=30):
            raise W.FetchError("could not connect (test)")
        folder = out("part_a_offline")
        rpath, ppath, _ = REC.run_part_a(S, offline, folder, dt.datetime(2026, 10, 1, 9, 30), WATCH_LIST)
        report = open(rpath, encoding="utf-8").read()
        self.assertIn(f"Could not check: {len(S.SPECS)}.", report)
        self.assertIn("could not connect (test)", report)
        self.assertEqual(open(ppath, encoding="utf-8").read(), "")

    def test_email_needed(self):
        """Part A stops with a message when there is no email for the SEC"""
        folder = out("part_a_no_email")
        os.makedirs(folder, exist_ok=True)
        saved, W.CONFIG_PATH = W.CONFIG_PATH, os.path.join(folder, "config.json")
        json.dump({"contact_email": ""}, open(W.CONFIG_PATH, "w"))
        try:
            with self.assertRaises(SystemExit) as stop:
                S.main(fetcher=None, argv=[])
            self.assertIn("Put your email", str(stop.exception))
        finally:
            W.CONFIG_PATH = saved


@unittest.skipUnless(REC.latest_recording(), "no SEC data saved yet: run test/record_sec_data.py once")
class Recorded(unittest.TestCase):
    def test_replay_same_as_live(self):
        """Part A on the saved SEC data gives the report and proposals of the live run, byte for byte"""
        folder = REC.latest_recording()
        report, props = REC.replay(S, folder, out("part_a_replay"))
        live = [f for f in os.listdir(folder) if f.startswith("Part A ") and f.endswith(".md")]
        self.assertEqual(report, open(os.path.join(folder, live[0]), encoding="utf-8").read())
        self.assertEqual(props, open(REC.proposals_path(folder), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
