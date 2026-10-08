#!/usr/bin/env python3
"""
Saves, once, the SEC data that Part A reads, so Part A can be tested offline and for free.

    python3 Agent/reader/test/record_sec_data.py

It runs Part A as normal (one request to the SEC per company, free, under a minute), with three differences:
  - its report and proposals go into test/fixtures/sec_<date>/, not into reports/ or proposed.jsonl;
  - the SEC data it read is saved there (sec_data.json.gz, cut down to the labels Part A uses), with a copy
    of the watch list as it was, so the test does not change when the watch list does;
  - it then runs Part A again from the saved data, offline, and checks the report comes out the same, byte for byte.

test/test_part_a.py replays the newest recording. If you change SPECS in reader_sec.py, record again.
"""
import contextlib
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

TEST = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(TEST, "fixtures")
DATA, WORKBOOK, META = "sec_data.json.gz", "watch list.xlsx", "recorded.json"


def labels_used(S):
    """The SEC labels Part A reads: those in SPECS, and the basket companies' revenue and operating profit."""
    basket = getattr(S, "BASKET_REVENUE", (S.REV_C, "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax"))
    return {c for s in S.SPECS for _, c in s["parts"]} | set(basket) | {"OperatingIncomeLoss"}


def cut_down(body, keep):
    """The SEC's answer with only the labels Part A reads (the rest is most of the size and never used)."""
    try:
        data = json.loads(body)
        gaap = data["facts"]["us-gaap"]
    except (ValueError, KeyError, TypeError):
        gaap = None
    if not isinstance(gaap, dict):
        return body                                   # not company facts: keep exactly as received
    data["facts"] = {"us-gaap": {c: v for c, v in gaap.items() if c in keep}}
    return json.dumps(data, separators=(",", ":"))


def recording(fetch, W, store, keep):
    def fetcher(url, ua, accept="*/*", timeout=30):
        try:
            status, body = fetch(url, ua, accept, timeout)
        except W.FetchError as e:
            store[url] = {"error": str(e)}
            raise
        store[url] = {"status": status, "body": cut_down(body, keep)}
        return status, body
    return fetcher


def replaying(W, store):
    def fetcher(url, ua, accept="*/*", timeout=30):
        saved = store.get(url)
        if saved is None:
            raise W.FetchError("not in the saved SEC data")
        if "error" in saved:
            raise W.FetchError(saved["error"])
        return saved["status"], saved["body"]
    return fetcher


def run_part_a(S, fetcher, out, started=None, workbook=None):
    """Run Part A with its report and proposals going to `out`. With `started`, the clock is fixed to it; with
    `workbook`, Part A reads that copy of the watch list. Returns (report path, proposals path, started)."""
    W = S.W
    os.makedirs(out, exist_ok=True)
    saved = (S.REPORTS_DIR, S.PROPOSED_PATH, W.now_local, W.CONFIG_PATH, S.time.sleep)
    clock, real_now = {}, W.now_local
    try:
        W.now_local = lambda: clock.setdefault("t", started or real_now())
        S.REPORTS_DIR, S.PROPOSED_PATH = out, proposals_path(out)
        if workbook:
            W.CONFIG_PATH = os.path.join(out, "config (test).json")
            json.dump({"contact_name": "Test", "contact_email": "test@example.com", "watch_list": os.path.abspath(workbook)},
                      open(W.CONFIG_PATH, "w", encoding="utf-8"))
            S.time.sleep = lambda s: None             # no pause between companies when nothing is downloaded
        with contextlib.redirect_stdout(io.StringIO()):
            S.main(fetcher=fetcher, argv=[])
    finally:
        S.REPORTS_DIR, S.PROPOSED_PATH, W.now_local, W.CONFIG_PATH, S.time.sleep = saved
    t = clock["t"]
    return os.path.join(out, "Part A " + t.strftime("%Y-%m-%d %H.%M.%S") + ".md"), proposals_path(out), t


def proposals_path(out):
    return os.path.join(out, "proposals (test copy).jsonl")


def latest_recording():
    """The newest test/fixtures/sec_<date>/ folder, or None."""
    found = sorted(d for d in os.listdir(FIXTURES) if d.startswith("sec_") and os.path.exists(os.path.join(FIXTURES, d, META)))
    return os.path.join(FIXTURES, found[-1]) if found else None


def load_recording(folder):
    meta = json.load(open(os.path.join(folder, META), encoding="utf-8"))
    store = json.loads(gzip.decompress(open(os.path.join(folder, DATA), "rb").read()).decode("utf-8"))
    return meta, store


def replay(S, folder, out):
    """Part A run offline from a recording, at the recording's time. Returns (report text, proposals text)."""
    meta, store = load_recording(folder)
    rpath, ppath, _ = run_part_a(S, replaying(S.W, store), out, dt.datetime.fromisoformat(meta["started"]),
                                 os.path.join(folder, WORKBOOK))
    read = lambda p: open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    return read(rpath), read(ppath)


def failures(store):
    return {u: v.get("error") or f"HTTP {v.get('status')}" for u, v in store.items() if "error" in v or v.get("status") != 200}


def main():
    sys.path.insert(0, os.path.dirname(TEST))
    import reader_sec as S
    W = S.W
    cfg = W.load_json(W.CONFIG_PATH, {})
    workbook = os.path.normpath(os.path.join(W.HERE, cfg.get("watch_list", "../Watch list - The AI Lose Lose Race.xlsx")))
    folder = os.path.join(FIXTURES, "sec_" + dt.date.today().isoformat())
    if os.path.exists(folder):
        sys.exit(f"Already recorded today ({os.path.relpath(folder)}): nothing done.")
    work = tempfile.mkdtemp(prefix="sec_recording_")      # moved into test/fixtures/ only if the recording is complete
    try:
        store = {}
        print("Reading the SEC data Part A uses (free, under a minute)…")
        rpath, ppath, started = run_part_a(S, recording(W.fetch, W, store, labels_used(S)), work)
        failed = failures(store)
        if failed:
            sys.exit(f"The SEC did not answer for {len(failed)} of {len(store)} companies (first: {next(iter(failed.values()))}). "
                     "Nothing kept: try again later.")
        shutil.copy(workbook, os.path.join(work, WORKBOOK))
        with open(os.path.join(work, DATA), "wb") as f:
            f.write(gzip.compress(json.dumps(store, sort_keys=True).encode("utf-8"), mtime=0))
        json.dump({"started": started.isoformat(), "companies": len(store), "reader_sec.py md5": hashlib.md5(open(S.__file__, "rb").read()).hexdigest(),
                   "python": sys.version.split()[0]}, open(os.path.join(work, META), "w", encoding="utf-8"), indent=1)
        # the check: the same report and proposals from the saved data alone
        with tempfile.TemporaryDirectory() as tmp:
            rep, props = replay(S, work, tmp)
        live_props = open(ppath, encoding="utf-8").read() if os.path.exists(ppath) else ""
        if rep != open(rpath, encoding="utf-8").read() or props != live_props:
            sys.exit("The offline replay did not give the same report: nothing kept (tell Claude).")
        shutil.move(work, folder)
        os.chmod(folder, 0o755)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"Saved: {os.path.relpath(folder)} ({len(store)} companies, {os.path.getsize(os.path.join(folder, DATA)) / 1e6:.1f} MB)")
    print("Offline replay gives the same report and proposals: yes")
    print("Nothing was added to reports/ or proposed.jsonl.")


if __name__ == "__main__":
    main()
