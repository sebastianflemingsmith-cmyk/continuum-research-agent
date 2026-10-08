"""The probe (Agent/runner/probe.py, step 0 of the "Check for updates" button): what it reports for sources that answer,
fail or are not watched; the SEC contact kept out of everything it prints and writes; the contact file it writes on
GitHub's machine; and nothing changed in Agent (here, the temporary copy). No network: the test answers every request."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

from support import AGENT, WATCH_LIST, WORK, D, W

CONTACT = "test@example.com"                     # the temporary copy's watcher/config.json (support.py)


def load_probe():
    """runner/probe.py, working on the reader and watcher of the temporary copy (support.py imported them first)."""
    saved = list(sys.path)
    spec = importlib.util.spec_from_file_location("probe", os.path.join(AGENT, "runner", "probe.py"))
    probe = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(probe)
    finally:
        sys.path[:] = saved
    return probe


def snapshot(folder):
    """Every file under a folder, with a fingerprint of its content."""
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            p = os.path.join(root, f)
            out[os.path.relpath(p, folder)] = hashlib.md5(open(p, "rb").read()).hexdigest()
    return out


def fake_fetch(url, ua, accept="*/*", timeout=30):
    """The SEC answers, with no filings and no labelled figures. Every other site refuses, with a reason that names the
    SEC contact, so the test can see that the probe keeps it out of its output."""
    if url.startswith("https://data.sec.gov/submissions/"):
        return 200, json.dumps({"filings": {"recent": {"accessionNumber": [], "filingDate": [], "form": []}}})
    if url.startswith("https://data.sec.gov/api/xbrl/companyfacts/"):
        return 200, json.dumps({"facts": {"us-gaap": {}}})
    raise W.FetchError(f"HTTP 403 (blocked) for {CONTACT.upper()}")


def fake_fetch_bytes(url, ua, timeout=90):
    raise W.FetchError("HTTP 403")


def deepseek_401(req, timeout=30):
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)


class Probe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = load_probe()
        cls.out = tempfile.mkdtemp(prefix="probe_test_")
        cls.addClassCleanup(shutil.rmtree, cls.out, True)
        cls.before = snapshot(os.path.join(WORK, "Agent"))
        printed = io.StringIO()
        with mock.patch.object(W, "fetch", fake_fetch), mock.patch.object(D, "fetch_bytes", fake_fetch_bytes), \
                mock.patch.object(time, "sleep", lambda s: None), contextlib.redirect_stdout(printed):
            cls.code = cls.probe.main(["--out", cls.out, "--summary", os.path.join(cls.out, "job summary.md")], opener=deepseek_401)
        cls.printed = printed.getvalue()
        cls.after = snapshot(os.path.join(WORK, "Agent"))
        with open(os.path.join(cls.out, "probe.json"), encoding="utf-8") as f:
            cls.result = json.load(f)

    def test_runs_to_the_end(self):
        """the probe runs all four checks to the end, even when most sites refuse"""
        self.assertEqual(self.code, 0)
        for part in ("watcher", "part_b", "part_a"):
            self.assertNotIn("stopped", self.result[part], part)
        self.assertIn("deepseek", self.result)

    def test_watcher_sources(self):
        """the watcher's dry run: every SEC feed that answers is counted, every page that refuses is listed with its reason"""
        sec = [s["ID"] for s in W.table(W.read_workbook(WATCH_LIST)["Sources"])
               if str(s.get("Feed / page") or "").startswith("https://data.sec.gov/submissions/")]
        w = self.result["watcher"]
        self.assertEqual(sorted(w["sec_answered"]), sorted(sec))
        self.assertEqual(w["sec_failed"], [])
        self.assertTrue(w["web_failed"])
        self.assertEqual(w["web_answered"], [])
        self.assertEqual({f["why"] for f in w["web_failed"]}, {"HTTP 403 (blocked) for [SEC contact]"})
        self.assertTrue({"TR-ASML", "IMF"} <= {s["source"] for s in w["not_watched"]})

    def test_part_b_plan(self):
        """Part B's plan: every document it could not find or download is listed, and none is read or saved"""
        b = self.result["part_b"]
        self.assertEqual(b["documents"], [])
        self.assertTrue(any("transcript: could not find the document" in p for p in b["not_available"]))
        self.assertTrue(any("periodic: no document found" in p for p in b["not_available"]))

    def test_part_a(self):
        """Part A: the companies whose labelled data loaded, and the figures it could not check"""
        a = self.result["part_a"]
        self.assertEqual(a["loaded"], a["companies"])
        self.assertEqual(a["statuses"], {"COULD NOT CHECK": a["figures"]})
        self.assertEqual(len(a["could_not_check"]), a["figures"])

    def test_deepseek_address(self):
        """DeepSeek's address answering HTTP 401 without a key counts as reachable"""
        self.assertTrue(self.result["deepseek"]["answers"])
        self.assertTrue(self.result["deepseek"]["how"].startswith("HTTP 401"))

    def test_summary(self):
        """the summary counts what answered and gives each reason for failing once, with what failed for it, and goes
        into the workflow's job summary"""
        n = len(self.result["watcher"]["sec_answered"])
        web = [f["source"] for f in self.result["watcher"]["web_failed"]]
        self.assertIn(f"SEC filings: {n} of {n} answered.", self.printed)
        self.assertIn(f"Web pages: 0 of {len(web)} answered. Could not check:\n"
                      f"   - HTTP 403 (blocked) for [SEC contact]: {', '.join(web)}\n", self.printed)
        for why in {c["why"] for c in self.result["part_a"]["could_not_check"]}:
            self.assertEqual(self.printed.count(f"   - {why}: "), 1, why)
        self.assertIn("DeepSeek's address: answers (HTTP 401 without a key, as expected).", self.printed)
        with open(os.path.join(self.out, "job summary.md"), encoding="utf-8") as f:
            self.assertTrue(f.read().startswith("## Probe\n"))

    def test_contact_kept_out(self):
        """the SEC contact appears nowhere in what the probe prints or writes, in any case"""
        self.assertNotIn(CONTACT, self.printed.lower())
        self.assertIn("[SEC contact]", self.printed)
        names = os.listdir(self.out)
        self.assertTrue(any(n.endswith(" test.md") for n in names))      # the watcher's dry-run report
        for name in names:
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                self.assertNotIn(CONTACT, f.read().lower(), name)

    def test_nothing_changed_in_agent(self):
        """nothing in Agent (here, the temporary copy) is written: no report, bookmark, log, proposal or document"""
        self.assertEqual(self.after, self.before)

    def test_out_inside_agent_refused(self):
        """an --out folder inside Agent is refused before anything runs"""
        target = os.path.join(self.probe.AGENT, "probe-out")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.probe.main(["--out", target])
        self.assertFalse(os.path.exists(target))

    def test_stops_without_contact(self):
        """with no SEC email in watcher/config.json, it stops before asking any site anything"""
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "config.json")
            with open(empty, "w", encoding="utf-8") as f:
                json.dump({"contact_name": "", "contact_email": ""}, f)
            asked = []
            with mock.patch.object(W, "CONFIG_PATH", empty), mock.patch.object(W, "fetch", lambda *a, **k: asked.append(a)), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.probe.main(["--out", os.path.join(tmp, "out")]), 1)
            self.assertEqual(asked, [])
            self.assertFalse(os.path.exists(os.path.join(tmp, "out")))


class SecConfig(unittest.TestCase):
    """--write-sec-config, on GitHub's machine: watcher/config.json from the two secrets."""

    def setUp(self):
        self.probe = load_probe()
        self.tmp = tempfile.mkdtemp(prefix="probe_config_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "config.json")
        with open(os.path.join(self.tmp, "config.example.json"), "w", encoding="utf-8") as f:
            json.dump({"contact_name": "", "contact_email": "", "watch_list": "../Watch list.xlsx", "_help": "a note"}, f)

    def write(self, env):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = self.probe.write_sec_config(env, self.path)
        return code, printed.getvalue()

    def test_writes_the_file_unseen(self):
        """it writes the name and email into config.json, readable only by its owner, and never prints them"""
        code, printed = self.write({"SEC_CONTACT_NAME": "Jo Test", "SEC_CONTACT_EMAIL": CONTACT})
        self.assertEqual(code, 0)
        with open(self.path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg, {"contact_name": "Jo Test", "contact_email": CONTACT, "watch_list": "../Watch list.xlsx"})
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertNotIn(CONTACT, printed)
        self.assertNotIn("Jo Test", printed)

    def test_never_replaces_a_file(self):
        """a config.json that is already there is never replaced"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{}")
        code, _ = self.write({"SEC_CONTACT_NAME": "Jo Test", "SEC_CONTACT_EMAIL": CONTACT})
        self.assertEqual(code, 1)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{}")

    def test_missing_secrets(self):
        """missing secrets, or an email that is not one, write nothing"""
        for env in ({}, {"SEC_CONTACT_NAME": "Jo Test"}, {"SEC_CONTACT_NAME": "Jo Test", "SEC_CONTACT_EMAIL": "not an email"}):
            with self.subTest(env=sorted(env)):
                code, _ = self.write(env)
                self.assertEqual(code, 1)
                self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
