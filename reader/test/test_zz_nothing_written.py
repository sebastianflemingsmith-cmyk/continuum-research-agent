"""Runs after all the other tests (the files run in alphabetical order): nothing outside the test folder was written."""
import os
import unittest

from support import BEFORE, FILES_BEFORE, HANDOFF_BEFORE, OPEN_BEFORE, READER, md5


class NothingWritten(unittest.TestCase):
    def test_watcher_handoff_and_decisions_untouched(self):
        """the live watcher state/log, handoff receipts and human decisions are untouched"""
        self.assertEqual({p: md5(p) for p in HANDOFF_BEFORE}, HANDOFF_BEFORE)

    def test_proposed_and_reports_unchanged(self):
        """Agent/reader/proposed.jsonl and the earlier reports are unchanged"""
        self.assertEqual({p: md5(p) for p in BEFORE}, BEFORE)

    def test_no_files_added(self):
        """no answer files were added to Agent/reader/documents, and no report to Agent/reader/reports"""
        self.assertEqual({d: sorted(os.listdir(os.path.join(READER, d))) for d in FILES_BEFORE}, FILES_BEFORE)

    def test_open_list_untouched(self):
        """runs.jsonl and the open list in Agent/reader were not touched by the tests"""
        self.assertEqual({f: md5(os.path.join(READER, f)) for f in OPEN_BEFORE}, OPEN_BEFORE)


if __name__ == "__main__":
    unittest.main()
