"""Offline handoff tests. Network and AI are replaced; all output is in temporary folders."""
import contextlib
import datetime as dt
import functools
import hashlib
import io
import json
import os
import tempfile
import types
import unittest
from unittest.mock import patch

from support import B, D, O, R, W, CACHE2, CHANGED_ANSWERS, D28, FIX, REPORTS, WATCH_LIST, WORK, documents_in, quiet_main
from lib import handoff as H
import replay_watcher_report as RW

NOW = dt.datetime(2026, 9, 30, 16, 0, 0)
BASE = "https://www.sec.gov/Archives/edgar/data/1769628/000176962826000100/"
RUN_4OCT = os.path.join(FIX, "watcher_2026-10-04")   # the watcher run of 4 Oct 2026 16:45: its .json report and state.json


def discovery(url=BASE + "crwv.htm", source="CRWV", form="10-Q", **kwargs):
    return dict(url=url, source=source, form=form, filed="2026-08-12", description="Quarterly report", **kwargs)


def job(url=BASE + "crwv.htm", kind="periodic", ticker="CRWV"):
    return dict(t=ticker, kind=kind, stem=None, text="TEST document: invented text, no financial facts",
                meta=dict(url=url, title="TEST", form="10-Q", filed="2026-08-12", report_date=""), ask=[{}])


class Handoff(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="handoff_", dir=WORK)
        self.path = os.path.join(self.folder, "discoveries.jsonl")
        self.run = types.SimpleNamespace(out_dir=self.folder, only=None, from_cache=False, max_chars=100000,
                                         started=NOW, ua="Test SEC contact", replay=False, handoff_path=None,
                                         reports_dir=os.path.join(self.folder, "reports"))
        os.makedirs(self.run.reports_dir)

    def load(self, rows):
        with open(self.path, "w") as f:
            f.writelines(json.dumps(row) + "\n" for row in rows)
        return H.load(self.run, self.path)

    def test_normal_search_and_duplicate_once(self):
        """the watcher adds a missed document and a repeated normal-search URL is read only once"""
        original = job()
        extra = discovery(BASE + "other.htm")
        h = self.load([discovery(), discovery(), extra])
        jobs = [original]
        with patch.object(D, "get_text", return_value="TEST invented document") as fetch:
            H.merge(h, jobs, self.run)
        self.assertIs(jobs[0], original)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(fetch.call_count, 1)
        self.assertIs(h["entries"][0]["jobs"][0], original)

    def test_unsupported_stays_visible(self):
        """newsrooms, non-results 8-Ks and amended filings need review, never silently count as read"""
        h = self.load([discovery("https://openai.com/news/example", "OPENAI", "page"),
                       discovery(BASE + "debt.htm", form="8-K", items="2.03"),
                       discovery(BASE + "amended.htm", form="10-Q/A")])
        jobs = [job()]
        H.merge(h, jobs, self.run)
        self.assertEqual(len(jobs), 1)
        self.assertEqual([r["status"] for r in H.statuses(h, True)], ["needs review"] * 3)
        self.assertIn("Outside current reader coverage", H.report(h, NOW, True))

    def test_event_page_becomes_transcript_at_same_url(self):
        """a first-seen event page does not hide the transcript posted later at the same URL"""
        url = "https://www.microsoft.com/en-us/investor/events/test"
        h = self.load([discovery(url, "TR-MSFT", "event"), discovery(url, "TR-MSFT", "transcript")])
        jobs = []
        with patch.object(D, "get_text", return_value="TEST transcript"):
            H.merge(h, jobs, self.run)
        self.assertEqual(len(h["entries"]), 1)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["kind"], "transcript")

    def test_failed_fetch_does_not_block_normal_search(self):
        """a failed watcher document stays pending without removing the reader's normal documents"""
        h = self.load([discovery(BASE + "blocked.htm")])
        jobs = [job()]
        with patch.object(D, "get_text", side_effect=W.FetchError("HTTP 403")):
            H.merge(h, jobs, self.run)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(H.statuses(h, True)[0]["status"], "could not read")

    def test_plan_never_acknowledges(self):
        """planning and a received-but-unread document cannot count as a completed reading"""
        h = self.load([discovery()])
        H.merge(h, [job()], self.run)
        self.assertEqual(H.statuses(h)[0]["status"], "ready")
        self.assertEqual(H.statuses(h, True)[0]["status"], "needs review")
        self.assertFalse(os.path.exists(h["receipts_path"]))

    def test_successful_read_and_retry(self):
        """a successful document gets a receipt; it is not added again, but the normal scan still reads it"""
        h = self.load([discovery()])
        j = job()
        H.merge(h, [j], self.run)
        j["handoff_read"] = True
        H.save(h, self.run)
        again = H.load(self.run, self.path)
        H.merge(again, [], self.run)
        self.assertEqual(H.statuses(again, True)[0]["status"], "already checked")
        fresh = H.load(self.run, self.path)
        normal = [job()]
        H.merge(fresh, normal, self.run)
        self.assertEqual(H.statuses(fresh)[0]["status"], "ready")
        self.assertEqual(len(normal), 1)

    def test_check_failure_remains_pending(self):
        """failed evidence checks are visible and remain eligible for a later reading"""
        h = self.load([discovery()])
        j = job()
        H.merge(h, [j], self.run)
        j.update(handoff_read=True, handoff_problem="A quote was rejected")
        H.save(h, self.run)
        again = H.load(self.run, self.path)
        with patch.object(D, "get_text", return_value="TEST") as fetch:
            H.merge(again, [], self.run)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(H.statuses(h, True)[0]["status"], "needs review")

    def test_partial_exhibits_not_acknowledged(self):
        """a filing with one failed exhibit cannot count as fully read"""
        h = self.load([discovery(BASE + "cover.htm", form="8-K", items="2.02")])
        # Use a company whose existing plan covers results.
        h["entries"][0]["item"]["source"] = "NVDA"
        good = job(BASE + "ex99-1.htm", "results", "NVDA")
        self.run.document_failures = [job(BASE + "ex99-2.htm", "results", "NVDA")]
        H.merge(h, [good], self.run)
        good["handoff_read"] = True
        self.assertEqual(H.statuses(h, True)[0]["status"], "could not read")

    def test_exact_results_filing_is_expanded(self):
        """an older results discovery resolves its own exhibits, never a different latest release"""
        base = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/"
        h = self.load([discovery(base + "cover.htm", source="NVDA", form="8-K", items="2.02, 9.01")])
        jobs = []
        with patch.object(D, "filing_files", return_value=(base, ["cover.htm", "ex99-1.htm", "ex99-2.htm"])) as index, \
                patch.object(D, "get_text", return_value="TEST results release") as fetch:
            H.merge(h, jobs, self.run)
        self.assertEqual(index.call_args.args[:2], (1045810, "0001045810-26-000001"))
        self.assertEqual(len(jobs), 2)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len({j["handoff_stem"] for j in jobs}), 2)

    def test_transcript_never_receives_sec_contact(self):
        """a watcher transcript uses the browser user agent, keeping SEC contact details off company sites"""
        h = self.load([discovery("https://investor.nvidia.com/test-transcript.pdf", "TR-NVDA", "transcript")])
        with patch.object(D, "get_text", return_value="TEST call") as fetch:
            H.merge(h, [], self.run)
        self.assertEqual(fetch.call_args.args[1], W.BROWSER_UA)

    def test_only_keeps_other_companies_pending(self):
        """--only never acknowledges watcher documents for other companies"""
        self.run.only = {"MSFT"}
        h = self.load([discovery()])
        H.merge(h, [], self.run)
        self.assertEqual(H.statuses(h, True)[0]["status"], "deferred")

    def test_no_applicable_claims(self):
        """a downloaded document with no applicable claims is flagged instead of called checked"""
        h = self.load([discovery()])
        j = job()
        j["ask"] = []
        H.merge(h, [j], self.run)
        self.assertEqual(H.statuses(h, True)[0]["status"], "needs review")

    def test_cache_mode_never_downloads(self):
        """an explicit cached handoff reuses a saved document and never falls back to network"""
        self.run.from_cache = True
        with documents_in(D28):
            saved = D.cache_read_latest("CRWV", "periodic")[0]
            h = self.load([discovery(saved[1]["url"]), discovery(BASE + "missing.htm")])
            with patch.object(D, "get_text", side_effect=AssertionError("network used")):
                jobs = []
                H.merge(h, jobs, self.run)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["stem"], saved[0])
        self.assertEqual(h["entries"][1]["status"], "could not read")

    def test_structured_report_keeps_coverage_gaps(self):
        """blocked, skipped and first-visit sources remain explicit in the handoff report"""
        p = os.path.join(self.folder, "report.json")
        with open(p, "w") as f:
            json.dump(dict(schema_version=1, new=[], run_id="TEST", scope="web", errors=[dict(source="X", why="blocked")],
                           skipped=[dict(name="Y", why="manual")], baselined=[dict(name="Z")]), f)
        report = H.report(H.load(self.run, p), NOW)
        for s in ("blocked", "manual", "baseline only", "No saved watcher discoveries"):
            self.assertIn(s, report)

    def test_bad_lines_and_empty_handoff(self):
        """a malformed discovery is reported and does not conceal the valid later lines"""
        with open(self.path, "w") as f:
            f.write('not json\n' + json.dumps(discovery()) + '\n')
        h = H.load(self.run, self.path)
        self.assertEqual(len(h["entries"]), 1)
        self.assertIn("line 1", h["problems"][0])
        empty = self.load([])
        jobs = [job()]
        H.merge(empty, jobs, self.run)
        self.assertEqual(len(jobs), 1)

    def test_missing_explicit_file_is_an_error(self):
        """a mistyped handoff path stops clearly instead of silently omitting its findings"""
        with self.assertRaises(SystemExit):
            H.load(self.run, self.path)

    def test_default_reads_log_and_latest_report(self):
        """the default handoff includes older discoveries, not just the latest watcher run"""
        self.load([discovery(BASE + "old.htm")])
        p = os.path.join(self.folder, "handoff.json")
        with open(p, "w") as f:
            json.dump(dict(schema_version=1, new=[discovery()], errors=[], checked=[]), f)
        with patch.object(W, "LOG_PATH", self.path), patch.object(W, "HANDOFF_PATH", p):
            h = H.load(self.run)
        self.assertEqual(len(h["entries"]), 2)

    def test_real_default_live_flow_with_fake_network_and_ai(self):
        """the normal live command automatically reads the watcher log and puts its extra document through AI and checks"""
        self.load([discovery()])
        s = B.settings(B.parse_args(["--only", "CRWV", "--go", "--out", self.folder]), ai=lambda s, u: ({}, {}))
        with patch.object(W, "LOG_PATH", self.path), patch.object(W, "HANDOFF_PATH", os.path.join(self.folder, "absent.json")), \
                patch.object(B, "settings", return_value=s), patch.object(D, "find_doc", return_value=[]), \
                patch.object(D, "get_text", return_value="TEST quarterly report June 30, 2026"), \
                patch.object(D, "cache_write"), patch.object(D, "save_answer"), patch.object(B.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            result = B.main([])
        self.assertEqual(len(result["handoff"]), 1)
        self.assertEqual(result["handoff"][0]["status"], "needs review")  # empty AI answer is not success
        self.assertTrue(result["verdicts"])  # the added document really reached the existing checks
        self.assertTrue(os.path.exists(result["handoff_report_path"]))


class ReplayCompatibility(unittest.TestCase):
    def test_watcher_recovers_missed_document_with_same_verdicts(self):
        """when the normal search misses the synthetic changed filing, watcher delivery restores the same verified results"""
        folder = tempfile.mkdtemp(prefix="handoff_missed_", dir=WORK)
        answers = os.path.join(folder, "answers.json")
        with open(answers, "w") as f:
            json.dump(CHANGED_ANSWERS, f)
        args = ["--from-cache", "--only", "CRWV,NBIS,ORCL", "--answers", answers]
        with patch.object(W, "now_local", return_value=NOW):
            original = quiet_main(args + ["--out", os.path.join(folder, "original")], folder=CACHE2)
        with documents_in(CACHE2):
            meta = D.cache_read_latest("CRWV", "periodic")[0][1]
        p = os.path.join(folder, "handoff.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps(discovery(meta["url"])) + "\n")
        collect = B.collect_documents

        def missing(run):
            jobs, problems = collect(run)
            return [j for j in jobs if j["t"] != "CRWV"], problems

        with patch.object(W, "now_local", return_value=NOW), patch.object(B, "collect_documents", side_effect=missing):
            recovered = quiet_main(args + ["--handoff", p, "--out", os.path.join(folder, "recovered")], folder=CACHE2)
        self.assertEqual({v["id"]: v for v in recovered["verdicts"]}, {v["id"]: v for v in original["verdicts"]})
        encode = lambda rows: sorted(json.dumps(p, sort_keys=True, default=str) for p in rows)
        self.assertEqual(encode(recovered["proposals"]), encode(original["proposals"]))

    def test_full_saved_run_byte_identical(self):
        """the saved 28 Sep run, with every document handed over twice, preserves all five original outputs byte for byte"""
        folder = tempfile.mkdtemp(prefix="handoff_replay_", dir=WORK)
        discoveries = []
        with documents_in(D28):
            for ticker, kind in R.PLAN:
                for stem, meta, body in D.cache_read_latest(ticker, kind):
                    discoveries.append(discovery(meta["url"], "TR-" + ticker if kind == "transcript" else ticker,
                                                 meta.get("form") or ("transcript" if kind == "transcript" else "10-Q")))
        p = os.path.join(folder, "handoff.jsonl")
        with open(p, "w") as f:
            f.writelines(json.dumps(row) + "\n" for row in discoveries * 2)
        write_open = functools.partial(O.write, now=NOW)
        with patch.object(W, "now_local", return_value=NOW), patch.object(O, "write", side_effect=write_open):
            result = quiet_main(["--from-cache", "--answers", os.path.join(FIX, "answers_full_run_2026-09-28"),
                                 "--handoff", p, "--out", folder], folder=D28)
        with open(os.path.join(FIX, "handoff_baseline_30sep.json")) as f:
            expected = json.load(f)
        for name, digest in expected.items():
            with open(os.path.join(folder, name), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, name)
        self.assertEqual(len(result["handoff"]), len({row["url"] for row in discoveries}))
        self.assertFalse(os.path.exists(os.path.join(folder, "handoff_receipts.jsonl")))


class WatcherProducer(unittest.TestCase):
    def test_interrupted_log_append_preserves_later_record(self):
        """after a partial last line, the next discovery stays readable and existing bytes are preserved"""
        folder = tempfile.mkdtemp(prefix="watcher_interrupted_", dir=WORK)
        path = os.path.join(folder, "log.jsonl")
        original = b'{"partial":'
        with open(path, "wb") as f:
            f.write(original)
        W.append_jsonl(path, [discovery()])
        problems = []
        self.assertEqual(H.json_lines(path, problems), [discovery()])
        self.assertEqual(len(problems), 1)
        with open(path, "rb") as f:
            self.assertTrue(f.read().startswith(original))

    def test_changing_link_folder_is_not_new(self):
        """AMD's changing link folder (30 Sep 2026) does not make old transcripts new; a new transcript still is"""
        old, now = "_2cc0365702ea365c1c931183919aa7d4", "_340ae277fcb444e1340a876d892b9c73"
        cdn = "https://d1io3yog0oux5.cloudfront.net/{}/amd/db/841/{}/webcast_transcript/{}"
        known = [("9232", "AMD_1Q_2026_Earnings.pdf"), ("9237", "AMD2Q26EarningsCallTranscript.pdf")]
        made_up = ("9999", "TEST_new_transcript.pdf")                  # TEST: an invented later transcript
        state = {"web": {"TR-AMD": {"seen": [cdn.format(old, *d) for d in known]}}}
        page = "".join('<a href="{}">Transcript</a>'.format(cdn.format(now, *d)) for d in known + [made_up])
        out = dict(new=[], checked=[], errors=[], skipped=[], baselined=[])
        with patch.object(W.time, "sleep"):
            W.check_web([("TR-AMD", "AMD (call documents)", [])], state, {}, types.SimpleNamespace(dry_run=False), out,
                        lambda url, ua, accept="*/*", timeout=30: (200, page))
        self.assertEqual(out["errors"], [])
        self.assertEqual([x["url"] for x in out["new"]], [cdn.format(now, *made_up)])

    def test_only_live_runs_write_handoff(self):
        """watcher report and structured handoff agree; dry runs cannot replace the real handoff or bookmarks"""
        folder = tempfile.mkdtemp(prefix="watcher_output_", dir=WORK)
        report_dir = os.path.join(folder, "reports")
        log, state, handoff = [os.path.join(folder, n) for n in ("log.jsonl", "state.json", "handoff.json")]

        def check(sources, st, cfg, args, ledger, models, out, fetch):
            out["new"].append(discovery(channel="sec", company="Test", why="Quarterly report", priority=W.HIGH))
            out["errors"].append(dict(source="X", what="TEST source", why="blocked", url="https://example.com"))

        with patch.object(W, "REPORTS_DIR", report_dir), patch.object(W, "LOG_PATH", log), \
                patch.object(W, "STATE_PATH", state), patch.object(W, "HANDOFF_PATH", handoff), \
                patch.object(W, "check_sec", side_effect=check), patch.object(W, "check_web"), \
                patch.object(W, "now_local", return_value=NOW), contextlib.redirect_stdout(io.StringIO()):
            W.main([])
            before = {p: open(p, "rb").read() for p in (log, state, handoff)}
            data = json.loads(before[handoff])
            self.assertEqual(data["new"][0]["url"], BASE + "crwv.htm")
            self.assertEqual(data["errors"][0]["why"], "blocked")
            with open(os.path.join(report_dir, data["report"])) as f:
                self.assertIn(data["new"][0]["url"], f.read())
            W.main(["--dry-run"])
            self.assertEqual({p: open(p, "rb").read() for p in before}, before)

    # ---- the fixes of 4 Oct 2026, from the watcher run of 4 Oct 2026 16:45 (one test each)
    def state_4oct(self):
        with open(os.path.join(RUN_4OCT, "state.json")) as f:
            return json.load(f)

    def web_run(self, pages, state):
        """The real watcher main() over the web pages only, with the real watch list and the 4 Oct bookmarks.
        Pages not given answer 404. Everything is written to a temporary folder."""
        folder = tempfile.mkdtemp(prefix="watcher_fixes_", dir=WORK)
        paths = {n: os.path.join(folder, n) for n in ("log.jsonl", "state.json", "handoff.json", "reports")}
        with open(paths["state.json"], "w") as f:
            json.dump(state, f)

        def fake(url, ua, accept="*/*", timeout=30):
            page = pages.get(url)
            if isinstance(page, Exception):
                raise page
            return (200, page) if page is not None else (404, "")

        with patch.object(W, "REPORTS_DIR", paths["reports"]), patch.object(W, "LOG_PATH", paths["log.jsonl"]), \
                patch.object(W, "STATE_PATH", paths["state.json"]), patch.object(W, "HANDOFF_PATH", paths["handoff.json"]), \
                patch.object(W, "now_local", return_value=dt.datetime(2026, 10, 4, 16, 45, 15)), \
                patch.object(W.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            out = W.main(["--only", "web"], fetcher=fake)
        with open(os.path.join(paths["reports"], "2026-10-04 16.45.15.md"), encoding="utf-8") as f:
            report = f.read()
        with open(paths["log.jsonl"], encoding="utf-8") as f:
            log = [json.loads(line) for line in f]
        return out, report, log

    # SoftBank's press page, shaped like the title saved on 4 Oct ("IROct. 1, 2026Execution of …"). The words are
    # SoftBank's headline as the 4 Oct report saved it; the tags around them are assumed (the page HTML was not kept).
    SOFTBANK_PAGE = ('<a href="https://group.softbank/en/news/press/20261001"><span>IR</span><span>Oct. 1, 2026</span>'
                     '<span>Execution of Follow-on Investment (Third Tranche) in OpenAI</span></a>')

    ASML_BEFORE_4_OCT = {"type": "next", "url": "https://www.asml.com/en/investors/financial-results/q2-2026",
                         "pattern": r"transcript|prepared remarks", "site": "asml"}

    def test_error_names_the_address_tried(self):
        """fix 1: a 'next page' timeout names the page actually fetched and when it was last checked (ASML's former rule)"""
        state = self.state_4oct()               # ASML's bookmark is …/q2-2026, last checked 26 Sep 2026
        q3 = "https://www.asml.com/en/investors/financial-results/q3-2026"
        out = dict(new=[], checked=[], errors=[], skipped=[], baselined=[], ignored=[])

        def timeout(url, ua, accept="*/*", timeout=30):
            raise W.FetchError("could not connect (timeout: The read operation timed out)")

        with patch.object(W.time, "sleep"), patch.dict(W.WEB_RULES, {"TR-ASML": self.ASML_BEFORE_4_OCT}):
            W.check_web([("TR-ASML", "ASML (call documents)", [])], state, {}, types.SimpleNamespace(dry_run=False), out, timeout)
        self.assertEqual(out["errors"][0]["url"], q3)
        report = W.write_report(out, NOW, types.SimpleNamespace(since=None, dry_run=False))
        self.assertIn(q3 + " · last checked 2026-09-26", report)
        self.assertNotIn("q2-2026", report)

    def test_asml_not_watched(self):
        """fix 1: ASML's page, which answered no script on 4 Oct 2026, is not fetched and is listed as not watched, with why"""
        out = dict(new=[], checked=[], errors=[], skipped=[], baselined=[], ignored=[])

        def no_fetch(url, ua, accept="*/*", timeout=30):
            raise AssertionError("ASML's page was fetched: " + url)

        with patch.object(W.time, "sleep"):
            W.check_web([("TR-ASML", "ASML (call documents)", [])], self.state_4oct(), {}, types.SimpleNamespace(dry_run=False),
                        out, no_fetch)
        self.assertEqual(out["errors"], [])
        self.assertEqual([x["source"] for x in out["skipped"]], ["TR-ASML"])
        report = W.write_report(out, NOW, types.SimpleNamespace(since=None, dry_run=False))
        self.assertIn("ASML (call documents): Site does not answer scripts (4 Oct 2026); results arrive through the SEC feed", report)

    def test_web_dates_are_publication_dates(self):
        """fix 2: a web item shows the date the source gives (feed, address, link text), else the day it was found"""
        state = self.state_4oct()
        state["web"]["SOFTBANK"]["seen"].remove("https://group.softbank/en/news/press/20261001")
        rss = ("<rss><channel><item><title>TEST post</title><link>https://openai.com/index/test-post</link>"
               "<pubDate>Thu, 01 Oct 2026 16:00:00 GMT</pubDate></item></channel></rss>")    # TEST: an invented post
        pages = {"https://group.softbank/en/news/press": self.SOFTBANK_PAGE, "https://openai.com/news/rss.xml": rss,
                 "https://www.anthropic.com/news": '<a href="/news/test-post"><time>Oct 2, 2026</time><span>TEST post</span></a>',
                 "https://www.aboutamazon.com/news": '<a href="https://www.aboutamazon.com/news/company-news/test-post">TEST post</a>'}
        out, report, log = self.web_run(pages, state)
        dates = {x["source"]: (x["published"], x["date_from"], x["found"]) for x in out["new"]}
        self.assertEqual(dates, {"SOFTBANK": ("2026-10-01", "the address", "2026-10-04"),
                                 "OPENAI": ("2026-10-01", "the feed", "2026-10-04"),
                                 "ANTHROPIC": ("2026-10-02", "the link text", "2026-10-04"),
                                 "AMZN-NEWS": (None, None, "2026-10-04")})
        for shown in ("published 2026-10-01 (date from the address)", "published 2026-10-01 (date from the feed)",
                      "published 2026-10-02 (date from the link text)", "found 2026-10-04 · [open](https://www.aboutamazon.com/"):
            self.assertIn(shown, report)
        self.assertNotIn("· 2026-10-04 ·", report)  # the day found is never shown as if it were a publication date
        self.assertEqual([x.get("published") for x in log], [x["published"] for x in out["new"]])

    def test_link_text_joined_with_spaces(self):
        """fix 3: the pieces of a link's text are joined with spaces (Anthropic's page, 4 Oct 2026)"""
        anthropic = ('<a href="/news/claude-frontier-academy"><div><time>Oct 2, 2026</time><span>Announcements</span></div>'
                     '<span>Anthropic invests $100 million to train 10,000 engineers</span></a>')
        self.assertEqual(W.page_links(anthropic, "https://www.anthropic.com/news")[0][1],
                         "Oct 2, 2026 Announcements Anthropic invests $100 million to train 10,000 engineers")
        self.assertEqual(W.page_links(self.SOFTBANK_PAGE, "https://group.softbank/en/news/press")[0][1],
                         "IR Oct. 1, 2026 Execution of Follow-on Investment (Third Tranche) in OpenAI")

    def test_rows_built_from_hit_rows_and_event(self):
        """fix 4: SoftBank's third tranche also lists F106 (built from F104) to recalculate, and the Events row with its note"""
        state = self.state_4oct()
        state["web"]["SOFTBANK"]["seen"].remove("https://group.softbank/en/news/press/20261001")
        out, report, log = self.web_run({"https://group.softbank/en/news/press": self.SOFTBANK_PAGE}, state)
        x = next(x for x in out["new"] if x["source"] == "SOFTBANK")
        self.assertEqual(x["ledger_rows"], ["F104", "F109", "F110"])
        self.assertEqual(x["recalculate"], [{"id": "F106", "uses": ["F104"]}])
        self.assertIn("  - Recalculate, built from those rows: F106 (uses F104)", report)
        self.assertEqual([(e["event"], e["matched"]) for e in x["events"]], [("SoftBank third $10bn tranche to OpenAI", "'Third Tranche'")])
        self.assertIn("  - The watch list's own note, not a sourced figure: \"If executed: paid becomes $80bn, 65.6% of $122bn.", report)
        self.assertEqual(log[0]["recalculate"], x["recalculate"])
        self.assertEqual(W.event_window(46296), W.event_window("1 Oct 2026 (Japan time)"))  # the date retyped as an Excel date
        moved = W.recalculate(["F019"], W.watch_rules(W.read_workbook(WATCH_LIST))["built_from"])
        self.assertEqual(moved[0], {"id": "F023", "uses": ["F019"]})
        self.assertIn({"id": "F026", "uses": ["F023"]}, moved)  # through a built row: F026 is built from F023

    def test_newsroom_headlines_ranked_never_dropped(self):
        """fix 5: a newsroom headline matching a Topics search term or an Events row is High and names the match; other newsroom headlines are Low and still reported and logged"""
        state = self.state_4oct()
        state["web"]["SOFTBANK"]["seen"].remove("https://group.softbank/en/news/press/20261001")
        rss = ("<rss><channel><item><title>TEST customer story</title><link>https://openai.com/index/test-story</link>"
               "</item></channel></rss>")                                                  # TEST: an invented post
        out, report, log = self.web_run({"https://group.softbank/en/news/press": self.SOFTBANK_PAGE,
                                         "https://openai.com/news/rss.xml": rss}, state)
        rank = {x["source"]: (x["priority"], x["why"]) for x in out["new"]}
        self.assertEqual(rank["SOFTBANK"][0], W.HIGH)
        for named in ("T09 Lab funding: 'tranche'", "Events 1 Oct 2026 (Japan time), SoftBank third $10bn tranche to OpenAI: 'Third Tranche'"):
            self.assertIn(named, rank["SOFTBANK"][1])
        self.assertEqual(rank["OPENAI"], (W.LOW, "Newsroom headline: no topic or event match"))
        high, low = report.split("## High")[1].split("## Low")
        self.assertIn("Execution of Follow-on Investment (Third Tranche)", high)
        self.assertIn("TEST customer story", low)
        self.assertEqual({x["url"] for x in log}, {x["url"] for x in out["new"]})
        self.assertEqual(len(log), 2)

    def test_replay_of_the_4_oct_run(self):
        """the 18 items of 4 Oct 2026 replayed offline: all kept; SoftBank High with F106 and its event; ASML not watched"""
        with open(os.path.join(RUN_4OCT, "2026-10-04 16.45.15.json")) as f:
            saved = json.load(f)
        path = os.path.join(tempfile.mkdtemp(prefix="watcher_replay_", dir=WORK), "replay.md")
        with contextlib.redirect_stdout(io.StringIO()):
            out, report = RW.main([os.path.join(RUN_4OCT, "2026-10-04 16.45.15.json"), os.path.join(RUN_4OCT, "state.json"),
                                   "--watch-list", WATCH_LIST, "--out", path])
        REPORTS["Watcher replay of 4 Oct 2026 (TEST, not a live check).md"] = path
        self.assertEqual([x["url"] for x in out["new"]], [x["url"] for x in saved["new"]])
        self.assertEqual([sum(x["priority"] == p for x in out["new"]) for p in (W.HIGH, W.NORMAL, W.LOW)], [5, 3, 10])
        softbank = next(x for x in out["new"] if x["source"] == "SOFTBANK")
        self.assertEqual((softbank["priority"], softbank["published"], softbank["recalculate"][0]["id"]), (W.HIGH, "2026-10-01", "F106"))
        self.assertEqual(out["errors"], [])                          # ASML timed out on 4 Oct; it is no longer fetched
        self.assertIn("TR-ASML", [x["source"] for x in out["skipped"]])
        self.assertIn("**New: 18** (5 high, 3 normal, 10 low)", report)


if __name__ == "__main__":
    unittest.main()
