"""
What the tests share: a temporary copy of the reader and the saved documents, the helpers, and the replays of
earlier runs (each done once, the first time a test asks for it).

Nothing in Agent/reader is written: every run goes to the temporary copy, which is deleted at the end.
test_zz_nothing_written.py checks this after all the other tests.
"""
import atexit
import contextlib
import functools
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

TEST = os.path.dirname(os.path.abspath(__file__))
READER = os.path.dirname(TEST)
AGENT = os.path.dirname(READER)
FIX = os.path.join(TEST, "fixtures")


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


# what the tests must leave exactly as it was (taken before any test runs)
REAL_PROPOSED = os.path.join(READER, "proposed.jsonl")
BEFORE = {p: md5(p) for p in [REAL_PROPOSED] + [os.path.join(READER, "reports", f) for f in os.listdir(os.path.join(READER, "reports"))]}
OPEN_BEFORE = {f: md5(os.path.join(READER, f)) for f in ("runs.jsonl", "Open proposals.md", "proposals_open.jsonl")}
FILES_BEFORE = {d: sorted(os.listdir(os.path.join(READER, d))) for d in ("documents", "reports")}
HANDOFF_BEFORE = {os.path.join(AGENT, name): md5(os.path.join(AGENT, name)) for name in
                  ("watcher/log.jsonl", "watcher/state.json", "watcher/handoff.json",
                   "reader/handoff_receipts.jsonl", "reader/decisions.jsonl")}

# ---- the temporary copy: the code, the watch list, and only the documents saved on 26 Sep 2026
WORK = tempfile.mkdtemp(prefix="reader_test_")
atexit.register(shutil.rmtree, WORK, True)
COPY = os.path.join(WORK, "Agent", "reader")
os.makedirs(os.path.join(WORK, "Agent", "watcher"))
os.makedirs(COPY)
shutil.copy(os.path.join(AGENT, "watcher", "watcher.py"), os.path.join(WORK, "Agent", "watcher"))
for f in os.listdir(READER):
    if f.endswith(".py"):
        shutil.copy(os.path.join(READER, f), COPY)
shutil.copytree(os.path.join(READER, "lib"), os.path.join(COPY, "lib"), ignore=shutil.ignore_patterns("__pycache__"))
WATCH_LIST = os.path.join(WORK, "Agent", "Watch list - The AI Lose Lose Race.xlsx")
shutil.copy(os.path.join(AGENT, "Watch list - The AI Lose Lose Race.xlsx"), WATCH_LIST)
DOCS = os.path.join(COPY, "documents")          # the recorded answers of 26 Sep refer to these exact texts
os.makedirs(DOCS)
for f in os.listdir(os.path.join(READER, "documents")):
    if "_2026-09-26_" in f and f.endswith(".txt"):
        shutil.copy(os.path.join(READER, "documents", f), DOCS)
D28 = os.path.join(WORK, "d28full")             # the documents of the full runs of 28 Sep
os.makedirs(D28)
for f in os.listdir(os.path.join(READER, "documents")):
    if ("_2026-09-28_" in f or f.startswith("EQIX_periodic_2026-09-26_20.")) and (f.endswith(".txt") or f.endswith(".meta.json")):
        shutil.copy(os.path.join(READER, "documents", f), D28)
CACHE2 = os.path.join(WORK, "cache2")           # a synthetic newer CoreWeave 10-Q (invented figures) and two real documents
os.makedirs(CACHE2)
shutil.copy(os.path.join(FIX, "CRWV_periodic_2026-11-12_1.txt"), CACHE2)
for stem in ("NBIS_6k_2026-09-26_18", "ORCL_periodic_2026-09-26_16"):
    shutil.copy(os.path.join(DOCS, stem + ".txt"), CACHE2)
json.dump({"contact_name": "Test", "contact_email": "test@example.com", "watch_list": "../Watch list - The AI Lose Lose Race.xlsx"},
          open(os.path.join(WORK, "Agent", "watcher", "config.json"), "w"))
json.dump({"deepseek_api_key": "", "model": "deepseek-flash"}, open(os.path.join(COPY, "config.json"), "w"))

sys.path.insert(0, COPY)
import reader_text as B  # noqa: E402  the Part B command
import reader_sec as S  # noqa: E402  the Part A command
import watcher as W  # noqa: E402
import claims as R  # noqa: E402
import companies as C  # noqa: E402
from lib import ai as A, checks as K, documents as D, extract as E, leads as L, openlist as O, tables as T, verdicts as VR  # noqa: E402

__all__ = ["A", "B", "C", "D", "E", "K", "L", "O", "R", "S", "T", "VR", "W"]    # the modules, for the test files

LEDGER = {r["ID"]: r for r in W.table(W.read_workbook(WATCH_LIST)["Ledger"])}
REPORTS = {}                                    # reports worth keeping in test/last run (run_tests.py copies them)


# ---- helpers
@contextlib.contextmanager
def documents_in(folder):
    previous, D.CACHE_DIR = D.CACHE_DIR, folder
    try:
        yield
    finally:
        D.CACHE_DIR = previous


def quiet_main(argv, ai=None, folder=DOCS):
    """Part B on the documents in `folder`, without printing its report."""
    with documents_in(folder), contextlib.redirect_stdout(io.StringIO()):
        return B.main(argv, ai=ai)


def out(name):
    return os.path.join(WORK, name)


def load(stem, folder=DOCS):
    """A saved document of 26 Sep, as the checks see it."""
    raw = open(os.path.join(folder, stem + ".txt"), encoding="utf-8").read()
    title, url, _, text = raw.split("\n", 3)
    m = D.describe_doc(dict(title=title, url=url), text)
    return K.Doc(text, m["title"], m["url"], m["form"], m["filed"], m["period_end"], m["date"]), m


def load28(stem, ticker):
    """A saved document of 28 Sep, with its company's fiscal year."""
    raw = open(os.path.join(D28, stem + ".txt"), encoding="utf-8").read()
    title, url, _, text = raw.split("\n", 3)
    saved = json.load(open(os.path.join(D28, stem + ".meta.json"), encoding="utf-8"))
    m = D.describe_doc(dict(saved, title=title, url=url), text, ticker)
    m["period_end"] = m["period_end"] or saved.get("period_end", "")      # a release takes its period from its filing's other exhibit
    return K.Doc(text, m["title"], m["url"], m["form"], m["filed"], m["period_end"], m["date"], fye=C.FYE[ticker],
                 fiscal_quarter=m.get("fiscal_quarter", "")), m


def item(row, key, forms_ok=False):
    """One thing to check, as the reader asks it: a ledger row and one of its components."""
    claim = R.CLAIMS[row]
    comp = next(c for c in claim["components"] if c["key"] == key)
    if forms_ok:
        comp = dict(comp, forms=None)
    return dict(id=f"{row}:{key}", row=row, claim=claim, comp=comp, type=claim["type"])


def check_one(doc, row, key, answer, forms_ok=False):
    """An answer checked in a document and turned into a verdict."""
    it = item(row, key, forms_ok)
    return VR.combine(it, [E.extract(doc, it, answer)], [])


def row_at(doc, frag, value):
    hits, _ = doc.find(frag)
    return T.read_row(doc, hits[0], len(K.norm(frag)), value) if hits else None


def by_id(res):
    return {v["id"]: v for v in res["verdicts"]}


def formulas(row, verdicts):
    return {f["name"]: f for f in VR.formula_results(R.CLAIMS[row], verdicts)}


# ---- replays of earlier runs, each done once
@functools.lru_cache(maxsize=None)
def paid_run():
    """The paid run of 26 Sep 2026: the AI's own answers, put through today's checks."""
    res = quiet_main(["--from-cache", "--answers", os.path.join(FIX, "answers_paid_run.json"), "--out", out("out_paid_run")])
    REPORTS["Replay of the 26 Sep paid run (TEST, not a live check).md"] = res["report_path"]
    return res


CHANGED_ANSWERS = {
    "CRWV_periodic_2026-11-12_1": {"items": [
        {"item": "F152:CRWV", "found": True, "value": 41.2, "unit": "$bn", "period_end": "2026-09-30", "period_type": "instant",
         "fragments": [{"role": "context", "text": "As of September 30, 2026, the Company executed additional lease agreements, primarily for data centers, that had not yet commenced."},
                       {"role": "value", "text": "The aggregate amount of estimated future undiscounted lease payments associated with such leases is $ 41.2 billion."}]},
        {"item": "F135:CRWV", "found": True, "value": 9.376, "unit": "$bn", "period_end": "2025-12-31", "period_type": "instant",
         "fragments": [{"role": "value", "text": "Construction in progress | 14,000 | 9,376"}]},
        {"item": "F141:CRWV_te", "found": True, "value": 41.0, "unit": "$bn", "period_end": "2026-09-30", "period_type": "instant",
         "fragments": [{"role": "value", "text": "Technology equipment | $41,000 | $20,903"}, {"role": "units", "text": "(in millions)"}]}]},
    "NBIS_6k_2026-09-26_18": {"items": [
        {"item": "F152:NBIS", "found": True, "value": 12.0541, "unit": "$bn", "period_end": "2026-06-30", "period_type": "instant",
         "fragments": [{"role": "value", "text": "As of June 30, 2026, the Company had executed additional lease agreements, primarily for data center facilities, as well as equipment, that had not yet commenced. The aggregate estimated future undiscounted lease payments associated with these agreements amounted to $12,054.1"}]}]},
    "ORCL_periodic_2026-09-26_16": {"items": [
        {"item": "F152:ORCL", "found": True, "value": 288, "unit": "$bn", "period_end": "2026-08-31", "period_type": "instant",
         "fragments": [{"role": "value", "text": "As of August 31, 2026 , we had $ 288 billion of additional lease commitments"}]}]},
}


@functools.lru_cache(maxsize=None)
def changed_run():
    """A synthetic newer CoreWeave 10-Q (invented figures) in which one component of F152 has changed."""
    path = out("answers2.json")
    json.dump(CHANGED_ANSWERS, open(path, "w", encoding="utf-8"))
    res = quiet_main(["--from-cache", "--only", "CRWV,NBIS,ORCL", "--answers", path, "--out", out("out_changed")], folder=CACHE2)
    REPORTS["Synthetic changed component (TEST, invented figures).md"] = res["report_path"]
    return res


@functools.lru_cache(maxsize=None)
def live_crwv():
    """The first live DeepSeek run (CoreWeave, 28 Sep 2026)."""
    res = quiet_main(["--from-cache", "--only", "CRWV", "--answers", os.path.join(FIX, "answers_live_CRWV_2026-09-28.json"),
                      "--out", out("out_live")])
    REPORTS["Replay of the live CoreWeave run after fixes (TEST).md"] = res["report_path"]
    return res


@functools.lru_cache(maxsize=None)
def live_nbis_orcl():
    """The second live run (Nebius and Oracle, 28 Sep 2026)."""
    return quiet_main(["--from-cache", "--only", "NBIS,ORCL", "--answers", os.path.join(FIX, "answers_live_NBIS_ORCL_2026-09-28.json"),
                       "--out", out("out_live2")])


@functools.lru_cache(maxsize=None)
def live_orcl_rerun():
    """The Oracle rerun with the 'no new debt' absence rule (28 Sep 2026)."""
    return quiet_main(["--from-cache", "--only", "ORCL", "--answers", os.path.join(FIX, "answers_live_ORCL_2026-09-28_rerun.json"),
                       "--out", out("out_live3")])


@functools.lru_cache(maxsize=None)
def full_runs_28sep():
    """The full run of 28 Sep 2026 (13:33) replayed twice into one folder: (first run, second run).
    The second finds the same leads, so it shows what a repeat run does."""
    args = ["--from-cache", "--answers", os.path.join(FIX, "answers_full_run_2026-09-28"), "--out", out("out_full28")]
    return quiet_main(args, folder=D28), quiet_main(args, folder=D28)
