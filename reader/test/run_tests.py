#!/usr/bin/env python3
"""
Offline tests for the reader, Parts A and B. No network, no DeepSeek, no cost.

    python3 Agent/reader/test/run_tests.py                  all the tests
    python3 Agent/reader/test/run_tests.py test_periods     one file of them

They are ordinary Python unittest tests, one file per area (test_*.py), so `python3 -m unittest` run in this folder works
too. Everything runs in a temporary copy: nothing in Agent/reader is written, except test/last run/ after a full run
(the results, and three replay reports to read: test outputs only, not checks of the paper).
"""
import datetime as dt
import os
import shutil
import sys
import unittest

TEST = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TEST)
sys.dont_write_bytecode = True                        # leave no __pycache__ folders behind


class Result(unittest.TextTestResult):
    """Remembers what passed, so the results file can list every test."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test)


def describe(test):
    """The test's first docstring line, and for one case of a test that checks several, which case."""
    case = getattr(test, "_subDescription", lambda: "")()
    return (test.shortDescription() or str(test)) + (f" {case}" if case else "")


def main(names):
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(names) if names else loader.discover(TEST)
    result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    failed = result.failures + result.errors
    print(f"\n{len(result.passed)} passed, {len(failed)} failed" + (f", {len(result.skipped)} skipped" if result.skipped else ""))
    for test, why in result.skipped:
        print(f"  skipped: {describe(test)} ({why})")
    if not names:                                     # a full run: keep the results and the reports worth reading
        import support
        keep = os.path.join(TEST, "last run")
        os.makedirs(keep, exist_ok=True)
        for name, path in support.REPORTS.items():
            shutil.copy(path, os.path.join(keep, name))
        with open(os.path.join(keep, "test results.txt"), "w", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%d %b %Y %H:%M}: {len(result.passed)} passed, {len(failed)} failed, {len(result.skipped)} skipped\n")
            f.writelines(f"PASS  {describe(t)}\n" for t in result.passed)
            f.writelines(f"FAIL  {describe(t)}\n" for t, _ in failed)
            f.writelines(f"SKIP  {describe(t)} ({why})\n" for t, why in result.skipped)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
