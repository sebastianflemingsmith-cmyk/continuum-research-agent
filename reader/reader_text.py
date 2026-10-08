#!/usr/bin/env python3
"""
Reader, Part B: the paper's figures that live in text.

For each company this finds the latest document of each kind (results release, 10-Q/10-K, call transcript, Nebius's
6-K), gives it to DeepSeek with the ledger rows that come from that kind of document, and asks for the figures. What
each row means (the measure, the company, the period, any arithmetic) and which documents it is checked in are in
claims.py.

Nothing the AI says is trusted on its own. Two separate questions are answered for every figure:

  1. Did the agent read the document correctly? (extraction: lib/extract.py)
  2. Does the paper agree? (paper check: lib/verdicts.py) Only answered from a figure that passed step 1.
     A failed or missing extraction is never counted as confirming the paper.

Run it from the project folder, in Antigravity's terminal:

    python3 Agent/reader/reader_text.py                    # plan only: what it would read, and the rough cost
    python3 Agent/reader/reader_text.py --only NVDA --go   # read one company
    python3 Agent/reader/reader_text.py --go               # read everything (the catch-up check)
    python3 Agent/reader/reader_text.py --from-cache --answers FILE   # replay saved documents and answers (free)

Needs your DeepSeek key in Agent/reader/config.json for --go. Transcripts that are PDFs need one extra package:
    python3 -m pip install --user pypdf

The code is in lib/ (see lib/__init__.py for what each file does). This file only runs the steps in order.
"""

import argparse
import json
import os
import re
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "watcher"))
sys.path.insert(0, HERE)
import watcher as W  # noqa: E402  (the workbook reader and the web rules)
import claims as R  # noqa: E402
import companies as C  # noqa: E402
from lib import ai as A, checks as K, documents as D, extract as E, leads as L  # noqa: E402
from lib import handoff as H, openlist as O, report as P, verdicts as V  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "config.json")


def main(argv=None, ai=None):
    """One run. `ai` stands in for DeepSeek (the tests use it): ai(system, user) -> (answer, usage). With --answers the
    saved answers are used instead, looked up by document."""
    run = settings(parse_args(argv), ai)
    handoff = H.load(run, run.handoff_path) if run.use_handoff else None
    jobs, problems = collect_documents(run)
    if handoff is not None:
        H.merge(handoff, jobs, run)
    prepare(jobs, problems, run.ledger)
    plan_line = A.cost_line(jobs)
    print_plan(jobs, problems, plan_line)
    if handoff is not None:
        print(H.report(handoff, run.started))
    if not run.go:
        print("\nPlan only. Add --go to send these documents to DeepSeek.")
        return None
    asked, facts = read_documents(jobs, problems, run)
    result = finish(asked, facts, jobs, problems, plan_line, run)
    if handoff is not None:
        result["handoff_report_path"] = H.save(handoff, run)
        result["handoff"] = H.statuses(handoff, completed=True)
        print(H.report(handoff, run.started, completed=True))
        print("Handoff report saved: " + os.path.relpath(result["handoff_report_path"]))
    return result


def parse_args(argv):
    """The command-line options."""
    ap = argparse.ArgumentParser(description="Read the latest documents against the paper's text-based figures.")
    ap.add_argument("--go", action="store_true", help="actually send documents to DeepSeek (without it: plan only)")
    ap.add_argument("--only", help="one or more tickers, e.g. NVDA or NVDA,MSFT")
    ap.add_argument("--from-cache", action="store_true", help="use the documents saved in documents/ instead of downloading")
    ap.add_argument("--answers", help="replay: use saved AI answers from this JSON file instead of calling DeepSeek (no cost)")
    ap.add_argument("--out", help="write the report and proposals to this folder instead of the reader folder")
    handoff = ap.add_mutually_exclusive_group()
    handoff.add_argument("--handoff", metavar="FILE", help="include a saved watcher .json report or .jsonl discovery log")
    handoff.add_argument("--without-handoff", action="store_true", help="normal search alone (for before/after comparisons)")
    return ap.parse_args(argv)


def settings(args, ai):
    """Everything the run needs to know before it starts, checked up front (stops with a message if something is missing)."""
    wcfg = W.load_json(W.CONFIG_PATH, {})
    if not args.from_cache and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", wcfg.get("contact_email") or ""):
        sys.exit("Put your email in Agent/watcher/config.json first (the SEC asks for it).")
    cfg = W.load_json(CONFIG_PATH, {})
    replay = None
    if args.answers:
        if not args.from_cache:
            sys.exit("--answers replays saved answers against saved documents: use it together with --from-cache.")
        replay = A.load_answers(args.answers)
        ai = ai or (lambda s, u, stem=None: (replay.get(stem) or {}, {}))
    go = args.go or bool(args.answers)
    if go and not ai and not (cfg.get("deepseek_api_key") or "").strip():
        sys.exit("Put your DeepSeek API key in Agent/reader/config.json (\"deepseek_api_key\"), then run this again.")
    wl_path = os.path.normpath(os.path.join(W.HERE, wcfg.get("watch_list", "../Watch list - The AI Lose Lose Race.xlsx")))
    out_dir = args.out or HERE
    return types.SimpleNamespace(
        from_cache=args.from_cache, replay=replay is not None,        # replay: saved answers, no AI call and nothing saved
        ua=f"{wcfg.get('contact_name') or 'Reader'} {wcfg.get('contact_email', '')}", cfg=cfg, ai=ai, go=go,
        ledger={r["ID"]: r for r in W.table(W.read_workbook(wl_path).get("Ledger", []))},
        only={t.strip().upper() for t in args.only.split(",")} if args.only else None,
        started=W.now_local(), max_chars=int(cfg.get("max_doc_chars", 1_500_000)), out_dir=out_dir,
        reports_dir=os.path.join(out_dir, "reports"), proposed_path=os.path.join(out_dir, "proposed.jsonl"),
        runs_path=os.path.join(out_dir, "runs.jsonl"), handoff_path=args.handoff,
        use_handoff=bool(args.handoff) or (not args.from_cache and not args.without_handoff))


def collect_documents(run):
    """The latest document of each kind in the plan: downloaded, or the saved copy with --from-cache."""
    jobs, problems = [], []
    run.document_failures = []
    print("Finding the latest documents…" if not run.from_cache else "Using the saved documents…")
    for t, kind in R.PLAN:
        if run.only and t not in run.only:
            continue
        if run.from_cache:
            docs = D.cache_read_latest(t, kind)
            if not docs:
                problems.append(f"{C.NAME[t]} {kind}: no saved document")
                continue
        else:
            try:
                found = D.find_doc(t, kind, run.ua)
            except (W.FetchError, ValueError, KeyError) as e:
                problems.append(f"{C.NAME[t]} {kind}: could not find the document ({e})")
                continue
            if not found:
                problems.append(f"{C.NAME[t]} {kind}: no document found")
                continue
            docs = []
            for m in found:
                try:
                    text = D.get_text(m["url"], W.BROWSER_UA if kind == "transcript" else run.ua)
                except W.FetchError as e:
                    problems.append(f"{C.NAME[t]}: {m['title']}: {e}")
                    run.document_failures.append(dict(kind=kind, meta=m))
                    continue
                time.sleep(0.15)
                docs.append((None, m, text))
        for stem, meta, text in docs:
            jobs.append(dict(t=t, kind=kind, stem=stem, meta=meta, text=text[:run.max_chars]))
    return jobs, problems


def prepare(jobs, problems, ledger):
    """The period each document reports on, and the items to ask of it."""
    for j in jobs:
        j["meta"] = D.describe_doc(j["meta"], j["text"], j["t"])
        msg = D.fye_problem(j["meta"], j["t"])
        if msg and msg not in problems:
            problems.append(msg)
    D.fill_release_periods(jobs)
    for j in jobs:
        j["ask"], j["skip"] = R.items_for(j["t"], j["kind"], j["meta"], ledger)


def print_plan(jobs, problems, plan_line):
    for j in jobs:
        m = j["meta"]
        print(f"  {C.NAME[j['t']]:<14} {j['kind']:<10} {len(j['text']):>9,} chars  period {m['period_end'] or '?':<10}  "
              f"{len(j['ask'])} items asked, {len(j['skip'])} not applicable  {m['title']}")
    print()
    print(plan_line)
    for p in problems:
        print("  Not available:", p)


def read_documents(jobs, problems, run):
    """Ask the AI about each document and check every answer. Returns ({item id: results}, the leads offered)."""
    facts, asked = [], {}
    entry = lambda it: asked.setdefault(it["id"], dict(item=it, results=[], skips=[]))
    for n, j in enumerate(jobs, 1):
        print(f"Reading {n}/{len(jobs)}: {C.NAME[j['t']]} {j['kind']}…", flush=True)
        m = j["meta"]
        k = sum(1 for x in jobs[:n] if (x["t"], x["kind"]) == (j["t"], j["kind"]))   # 1, 2… within this company and kind
        stem = j["stem"] or j.get("handoff_stem") or f"{j['t']}_{j['kind']}_{run.started.strftime('%Y-%m-%d')}_{k}"
        doc = K.Doc(j["text"], m["title"], m["url"], m["form"], m["filed"], m["period_end"], m["date"],
                    fye=C.FYE.get(j["t"]), fiscal_quarter=m.get("fiscal_quarter", ""))
        for it, why in j["skip"]:
            entry(it)["skips"].append(dict(doc=K.doc_info(doc), why=why))
        if not j["ask"]:
            continue
        if not run.replay and not run.from_cache:
            D.cache_write(stem, m, j["text"])                  # keep the document even if the AI step fails
        user = A.build_user_prompt(j["t"], m, j["ask"], run.ledger, j["text"])
        try:
            if run.replay:
                answer, _ = run.ai(A.SYSTEM, user, stem)
            else:
                answer, _ = (run.ai or (lambda s, u: A.deepseek(run.cfg, s, u)))(A.SYSTEM, user)
        except W.FetchError as e:
            j["handoff_problem"] = "AI call failed; remains pending"
            problems.append(f"{C.NAME[j['t']]} {j['kind']}: {e}")
            for it in j["ask"]:
                entry(it)["results"].append(dict(status="failed", problem=str(e), doc=K.doc_info(doc), id=it["id"], warnings=[]))
            continue
        if not run.replay:
            D.save_answer(stem, run.started, answer)
        answers = A.answers_by_item(answer, j["ask"])
        extraction_problems = 0
        for it in j["ask"]:
            extraction = E.extract(doc, it, answers[it["id"]])
            entry(it)["results"].append(extraction)
            if extraction["status"] in ("failed", "no answer", "rejected"):
                extraction_problems += 1
        j["handoff_read"] = True
        if extraction_problems:
            j["handoff_problem"] = "{} claim extraction(s) need attention; see Part B report".format(extraction_problems)
        allowed = set(R.PLAN.get((j["t"], j["kind"]), []))
        for fct in A.new_facts_in(answer):
            try:
                ok = L.check_fact(doc, fct, allowed, j["t"])
            except Exception:
                ok = None
            facts.append(ok if ok else dict(dropped=True, topic=E.as_str(fct.get("topic")), doc=K.doc_info(doc)))
        if not run.replay:
            time.sleep(1)
    return asked, facts


def finish(asked, facts, jobs, problems, plan_line, run):
    """Verdicts, leads, the report and the proposals; then the files: the report, proposed.jsonl and runs.jsonl
    (both added to, never rewritten), and the rebuilt open list."""
    verdicts = [V.combine(a["item"], a["results"], a["skips"]) for a in asked.values()]
    try:
        earlier_open = O.open_facts(run.out_dir)
    except Exception as e:                          # the open list is a convenience: never stop a run for it
        earlier_open = []
        problems.append(f"could not read the earlier open list ({e.__class__.__name__}: {e})")
    verified = [f for f in facts if not f.get("dropped")]
    leads, set_aside = L.sort_leads(L.dedupe_facts(verified), verdicts, earlier_open)
    dropped = [f for f in facts if f.get("dropped")]
    formulas = V.formulas_by_row(verdicts, run.ledger)
    report = P.build_report(run.started, plan_line, verdicts, formulas, leads, set_aside, dropped, problems, run.ledger, jobs,
                            lead_counts=dict(offered=len(facts), not_verified=len(dropped), verified=len(verified)))
    proposals = P.build_proposals(run.started, verdicts, formulas, leads)

    run_id = "B " + run.started.strftime("%Y-%m-%d %H.%M.%S")
    os.makedirs(run.reports_dir, exist_ok=True)
    rpath = os.path.join(run.reports_dir, "Part B " + run.started.strftime("%Y-%m-%d %H.%M.%S") + ".md")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(report)
    with open(run.proposed_path, "a", encoding="utf-8") as f:
        for p in proposals:
            f.write(json.dumps(dict(p, run_id=run_id), ensure_ascii=False, default=str) + "\n")
    with open(run.runs_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(run_id=run_id, found_at=run.started.isoformat(), only=sorted(run.only) if run.only else None,
                                  replay=run.replay, items=V.items_checked(verdicts, formulas)), ensure_ascii=False) + "\n")
    try:
        open_result = O.rebuild(run.out_dir, quiet=True)
        open_line = (f"Open list rebuilt: {len(open_result['open'])} open proposals, repeats merged "
                     f"({os.path.relpath(os.path.join(run.out_dir, 'Open proposals.md'))}).")
    except Exception as e:
        open_line = f"The open list could not be rebuilt ({e.__class__.__name__}: {e}); run open_proposals.py by hand."
    print()
    print(report)
    print(f"Report saved: {os.path.relpath(rpath)}")
    print(open_line)
    return dict(verdicts=verdicts, facts=leads, set_aside=set_aside, proposals=proposals, report=report, report_path=rpath)


if __name__ == "__main__":
    main()
