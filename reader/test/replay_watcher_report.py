#!/usr/bin/env python3
"""
Replay a saved watcher run with the current watcher rules. Offline: no network, and nothing in Agent/watcher is written.

    python3 Agent/reader/test/replay_watcher_report.py
    python3 Agent/reader/test/replay_watcher_report.py "Agent/watcher/reports/2026-10-04 16.45.15.json" Agent/watcher/state.json

It takes the items the run found (its .json report), the bookmarks (state.json) and the watch list, and prints the
report the current code would have written: dates, rows to recalculate, matching events and ranks. The result is a
TEST, not a live check. Two things cannot be replayed, because the run saved neither: the page HTML (so titles stay as
saved; a live run joins link text with spaces) and RSS dates (so OpenAI items show the day found).
"""
import argparse
import copy
import datetime as dt
import os
import sys

TEST = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(os.path.dirname(TEST))
sys.path.insert(0, os.path.join(AGENT, "watcher"))
sys.dont_write_bytecode = True
import watcher as W  # noqa: E402

RUN = os.path.join(AGENT, "watcher", "reports", "2026-10-04 16.45.15.json")


def replay(saved, state, book):
    """The saved run's discoveries and source checks as the current code reports them. Inputs are not changed."""
    out = copy.deepcopy({k: saved.get(k, []) for k in ("new", "ignored", "errors", "baselined", "skipped", "checked")})
    for x in out["new"]:
        if x.get("channel") == "web" and "found" not in x:  # before 4 Oct 2026 the day found was saved as "filed"
            x["found"] = x.pop("filed")
            x["published"], x["date_from"] = W.publication_date(x["url"], x.get("description") or "")
    errors, out["errors"] = out["errors"], []
    for e in errors:
        rule = W.WEB_RULES.get(e["source"]) or {}
        st = state.get("web", {}).get(e["source"]) or {}
        if rule.get("type") == "skip":  # no longer fetched (ASML since 4 Oct 2026): listed as not watched, with why
            out["skipped"].append({"source": e["source"], "name": e["what"], "why": rule["why"]})
            continue
        if rule.get("type") == "next":  # the address the watcher fetched: the quarter after the bookmark
            e["url"] = W.next_quarter_url(st.get("current", rule["url"]), rule["site"])
        e.setdefault("last_checked", st.get("last_checked"))
        out["errors"].append(e)
    W.rank_and_match(out["new"], W.watch_rules(book))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Replay a saved watcher run with the current rules (offline, saves nothing).")
    ap.add_argument("run", nargs="?", default=RUN, help="a saved watcher report (.json)")
    ap.add_argument("state", nargs="?", default=os.path.join(AGENT, "watcher", "state.json"), help="the bookmarks")
    ap.add_argument("--watch-list", default=os.path.join(AGENT, "Watch list - The AI Lose Lose Race.xlsx"))
    ap.add_argument("--out", help="also save the replayed report here")
    args = ap.parse_args(argv)
    saved, state = W.load_json(args.run, {}), W.load_json(args.state, {})
    out = replay(saved, state, W.read_workbook(args.watch_list))
    started = dt.datetime.fromisoformat(saved["run_id"])
    report = W.write_report(out, started, argparse.Namespace(since=None, dry_run=False)).split("\n", 1)[1]
    report = (f"# Replay of the watcher run of {started:%d %b %Y %H:%M} with the current rules (TEST, not a live check)\n\n"
              f"From {os.path.basename(args.run)} and {os.path.basename(args.state)}, offline. Titles are as saved (the "
              "page HTML was not kept) and OpenAI's RSS dates were not saved, so those items show the day found.\n" + report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
    print(report)
    return out, report


if __name__ == "__main__":
    main()
