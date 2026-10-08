#!/usr/bin/env python3
"""
The probe: can this machine reach the sources the watcher and the reader read? It is step 0 of the "Check for
updates" button (6 Oct 2026). Run once on GitHub's machine and once on Seb's Mac, it shows where the button's checks
can run.

It does four things, all free, and saves nothing in Agent:
  1. the watcher with --dry-run (its report goes to the probe's own folder, not to watcher/reports; the bookmarks, the
     log and the handoff are not touched);
  2. Part B's plan, as reader_text.py without --go: the latest documents are found and downloaded, DeepSeek reads
     nothing, and nothing is saved;
  3. Part A: the SEC's labelled data is downloaded and compared with the paper, without saving a report or a proposal;
  4. one request to DeepSeek's address, with no key (an answer of HTTP 401 means the address can be reached).

On Seb's Mac, from the paper's folder:

    python3 Agent/runner/probe.py

It prints a short summary to paste to Claude, and keeps it, with the reports, in a new folder in the system's temp
folder (or --out <folder>, outside Agent). It uses the SEC name and email in Agent/watcher/config.json, as the watcher
does, and never prints them. On GitHub's machine the workflow (.github/workflows/probe.yml) first writes that file from
two secrets (--write-sec-config). Standard library only, plus pypdf if it is installed.
"""
import argparse
import contextlib
import io
import json
import os
import platform
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True                        # leaves no __pycache__ folders behind
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
for _p in (os.path.join(AGENT, "reader"), os.path.join(AGENT, "watcher")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import watcher as W  # noqa: E402  the watcher (its main, its fetch and its config)
import reader_sec as RS  # noqa: E402  Part A
import reader_text as RT  # noqa: E402  Part B
from lib import ai as A, handoff as H  # noqa: E402

DEEPSEEK_CHECK = "https://api.deepseek.com/models"     # asked with no key: HTTP 401 means the address answers
SECRETS = ("SEC_CONTACT_NAME", "SEC_CONTACT_EMAIL")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
HIDDEN = "[SEC contact]"


# ------------------------------------------------------------------------------------------
# The SEC contact: written on GitHub's machine from the secrets, and kept out of everything the probe writes
# ------------------------------------------------------------------------------------------
def write_sec_config(env=None, path=None):
    """On GitHub's machine: write watcher/config.json from the secrets SEC_CONTACT_NAME and SEC_CONTACT_EMAIL, readable
    only by this user. It never prints them, and never replaces a config.json that is already there."""
    env = os.environ if env is None else env
    path = path or W.CONFIG_PATH
    name, email = (str(env.get(k) or "").strip() for k in SECRETS)
    if not name or not EMAIL.fullmatch(email):
        print("The secrets SEC_CONTACT_NAME and SEC_CONTACT_EMAIL are missing, or are not a name and an email. Add them "
              "in Agent > Settings > Secrets and variables > Actions, then run the probe again.")
        return 1
    if os.path.exists(path):
        print("watcher/config.json is already there: nothing written.")
        return 1
    cfg = W.load_json(os.path.join(os.path.dirname(path), "config.example.json"), {})
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg.update(contact_name=name, contact_email=email)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    print("Wrote watcher/config.json from the secrets (not shown).")
    return 0


def contact():
    """What must never appear in the probe's output: the SEC user agent (name and email) and the email alone."""
    cfg = W.load_json(W.CONFIG_PATH, {})
    email = str(cfg.get("contact_email") or "").strip()
    if not EMAIL.fullmatch(email):
        return []
    agent = f"{cfg.get('contact_name') or 'Watcher'} {email}"       # as the watcher and Part A send it
    return [agent, email]


def hide(text, secrets):
    """The text with the SEC contact replaced by [SEC contact], whatever its case."""
    for s in secrets:
        text = re.sub(re.escape(s), HIDDEN, text, flags=re.I)
    return text


# ------------------------------------------------------------------------------------------
# The four checks
# ------------------------------------------------------------------------------------------
def probe_watcher(folder):
    """The watcher with --dry-run. Its report goes to `folder`; the bookmarks, the log and the handoff are not touched.
    Returns (what answered and what failed, what it printed)."""
    keep, buf = W.REPORTS_DIR, io.StringIO()
    W.REPORTS_DIR = folder
    try:
        with contextlib.redirect_stdout(buf):
            out = W.main(["--dry-run"], fetcher=W.fetch)
    except SystemExit as e:
        return dict(stopped=str(e.code)), buf.getvalue()
    except Exception as e:                            # a fault in the watcher is a finding of the probe, not its end
        return dict(stopped=f"{e.__class__.__name__}: {e}"), buf.getvalue()
    finally:
        W.REPORTS_DIR = keep
    failed = [dict(source=e.get("source"), what=e.get("what"), why=e.get("why"), url=e.get("url")) for e in out["errors"]]
    return dict(sec_answered=sorted(c[4:] for c in out["checked"] if c.startswith("SEC ")),
                sec_failed=[f for f in failed if f["what"] == "SEC filings"],
                web_answered=sorted(c[4:].split(" (")[0] for c in out["checked"] if c.startswith("Web ")),
                web_failed=[f for f in failed if f["what"] != "SEC filings"],
                not_watched=[dict(source=s.get("source"), why=s.get("why")) for s in out["skipped"]],
                new=len(out["new"]), filtered_out=len(out["ignored"])), buf.getvalue()


def probe_part_b():
    """Part B's plan, as reader_text.py without --go: the latest documents are found and downloaded (with the watcher's
    finds that have a reading rule); DeepSeek reads nothing and nothing is saved. Returns (result, what it printed)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            run = RT.settings(RT.parse_args([]), None)
            handoff = H.load(run, run.handoff_path) if run.use_handoff else None
            jobs, problems = RT.collect_documents(run)
            if handoff is not None:
                H.merge(handoff, jobs, run)
            RT.prepare(jobs, problems, run.ledger)
            RT.print_plan(jobs, problems, A.cost_line(jobs))
            if handoff is not None:
                print(H.report(handoff, run.started))
    except SystemExit as e:
        return dict(stopped=str(e.code)), buf.getvalue()
    except Exception as e:
        return dict(stopped=f"{e.__class__.__name__}: {e}"), buf.getvalue()
    docs = [dict(company=j["t"], kind=j["kind"], title=j["meta"].get("title") or "",
                 host=urllib.parse.urlsplit(j["meta"].get("url") or "").hostname or "", chars=len(j["text"]),
                 period_end=j["meta"].get("period_end") or "") for j in jobs]
    finds = [dict(status=r["status"], source=r["source"], url=r["url"], detail=r["detail"])
             for r in (H.statuses(handoff) if handoff is not None else [])]
    return dict(documents=docs, not_available=list(problems), watcher_finds=finds,
                watcher_problems=list((handoff or {}).get("problems") or [])), buf.getvalue()


def probe_part_a():
    """Part A: the SEC's labelled data downloaded and compared with the paper. No report or proposal is saved: the
    report's text is returned. Returns (result, what it printed, the report's text)."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cfg = W.load_json(W.CONFIG_PATH, {})
            ua = RS.sec_user_agent(cfg)
            ledger, basket = RS.read_watch_list(cfg)
            tickers = sorted({s["co"] for s in RS.SPECS} | {str(b["Ticker"]).upper() for b in basket})
            facts, errors = RS.load_company_facts(tickers, W.fetch, ua)
            results = [RS.check_figure(s, facts.get(s["co"]), ledger) for s in RS.SPECS]
            basket_rows = [RS.check_basket_company(b, facts.get(str(b["Ticker"]).upper()) or {}) for b in basket]
            report = RS.build_report(W.now_local(), results, basket_rows, errors)
    except SystemExit as e:
        return dict(stopped=str(e.code)), buf.getvalue(), ""
    except Exception as e:
        return dict(stopped=f"{e.__class__.__name__}: {e}"), buf.getvalue(), ""
    statuses = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    not_loaded = [dict(zip(("company", "why"), e.split(": ", 1))) if ": " in e else dict(company="", why=e) for e in errors]
    return dict(companies=len(tickers), loaded=len(facts), not_loaded=not_loaded, figures=len(results), statuses=statuses,
                could_not_check=[dict(id=r["id"], what=r["what"], why=r.get("why") or "") for r in results
                                 if r["status"] == "COULD NOT CHECK"]), buf.getvalue(), report


def probe_deepseek(opener=None):
    """One request to DeepSeek's address with no key: HTTP 401 means it answers. Nothing is sent but the address."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(DEEPSEEK_CHECK, headers={"Accept": "application/json"})
    try:
        with opener(req, timeout=30) as r:
            return dict(answers=True, how=f"HTTP {r.status}")
    except urllib.error.HTTPError as e:
        return dict(answers=True, how=f"HTTP {e.code}" + (" without a key, as expected" if e.code == 401 else " (not the 401 expected: look at it by hand)"))
    except Exception as e:
        return dict(answers=False, how=f"could not connect ({e.__class__.__name__}: {e})")


# ------------------------------------------------------------------------------------------
# The summary
# ------------------------------------------------------------------------------------------
def machine():
    try:
        import pypdf
        pdf = getattr(pypdf, "__version__", "installed")
    except ImportError:
        pdf = None
    where = "GitHub's machine" if os.environ.get("GITHUB_ACTIONS") == "true" else "this computer"
    system = "macOS " + platform.mac_ver()[0] if platform.system() == "Darwin" else platform.system()
    return dict(where=where, system=system, python=platform.python_version(), pypdf=pdf)


def took(seconds):
    s = int(round(seconds))
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def grouped(pairs):
    """[(name, reason)] as one line per reason, so a source that blocks everything takes one line, not twenty."""
    by = {}
    for name, why in pairs:
        names = by.setdefault(why, [])
        if name not in names:
            names.append(name)
    return [f"   - {why}: {', '.join(names)}" for why, names in by.items()]


NOT_FOUND = re.compile(r"^(?P<what>.+?): (?P<why>could not find the document \(.*\)|no document found|no saved document)$")


def summary(result):
    """The lines to paste to Claude: what answered, counted, and everything that failed, one line per reason."""
    m = result["machine"]
    L = [f"Probe of {result['started'][:16].replace('T', ' ')} on {m['where']}: {m['system']}, Python {m['python']}, "
         + (f"pypdf {m['pypdf']}" if m["pypdf"] else "pypdf not installed (PDF transcripts cannot be read)")]
    w = result["watcher"]
    L += ["", f"1. Watcher, dry run (nothing saved), {took(result['seconds']['watcher'])}"]
    if w.get("stopped"):
        L.append(f"   Stopped: {w['stopped']}")
    else:
        for label, ok, failed in (("SEC filings", w["sec_answered"], w["sec_failed"]), ("Web pages", w["web_answered"], w["web_failed"])):
            L.append(f"   {label}: {len(ok)} of {len(ok) + len(failed)} answered." + (" Could not check:" if failed else ""))
            L += grouped((f["source"], f["why"]) for f in failed)
        if w["not_watched"]:
            L.append("   Not watched, by rule (the same on every machine): " + ", ".join(s["source"] for s in w["not_watched"]))
        L.append(f"   New since the last real run: {w['new']} (filtered out: {w['filtered_out']}).")
    b = result["part_b"]
    L += ["", f"2. Part B plan (DeepSeek reads nothing, nothing saved), {took(result['seconds']['part_b'])}"]
    if b.get("stopped"):
        L.append(f"   Stopped: {b['stopped']}")
    else:
        hosts = sorted({d["host"] for d in b["documents"] if d["host"]})
        L.append(f"   Documents found and downloaded: {len(b['documents'])}" + (f" (from {', '.join(hosts)})" if hosts else "") + ".")
        if b["not_available"]:
            L.append("   Not available:")
            pairs, other = [], []
            for p in b["not_available"]:
                hit = NOT_FOUND.match(p)
                if hit:
                    pairs.append((hit.group("what"), hit.group("why")))
                else:
                    other.append(p)
            L += grouped(pairs) + [f"   - {p}" for p in other]
        finds = b["watcher_finds"]
        failed = [f for f in finds if f["status"] == "could not read"]
        review = sum(1 for f in finds if f["status"] == "needs review")
        if finds:
            L.append(f"   The watcher's finds: {len(finds)}. Need review (no reading rule, or nothing to check: the same on "
                     f"every machine): {review}. Could not be downloaded: {len(failed)}" + (":" if failed else "."))
            L += grouped((f"{f['source']} {f['url']}", f["detail"]) for f in failed)
        L += [f"   Note: {p}" for p in b["watcher_problems"]]
    a = result["part_a"]
    L += ["", f"3. Part A, the SEC's labelled data (nothing saved), {took(result['seconds']['part_a'])}"]
    if a.get("stopped"):
        L.append(f"   Stopped: {a['stopped']}")
    else:
        L.append(f"   Companies: {a['loaded']} of {a['companies']} loaded." + (" Not loaded:" if a["not_loaded"] else ""))
        L += grouped((e["company"], e["why"]) for e in a["not_loaded"])
        L.append(f"   Paper figures: {a['figures']}: " + ", ".join(f"{v} {k.lower()}" for k, v in sorted(a["statuses"].items())) + "."
                 + (" Could not check:" if a["could_not_check"] else ""))
        L += grouped((c["id"], c["why"]) for c in a["could_not_check"])
    d = result["deepseek"]
    L += ["", "4. DeepSeek's address: " + ("answers" if d["answers"] else "DOES NOT ANSWER") + f" ({d['how']})."]
    return L


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(description="Can this machine reach the sources the watcher and the reader read? "
                                             "Free; saves nothing in Agent.")
    ap.add_argument("--out", help="the folder for the summary and the reports (default: a new folder in the system's temp folder)")
    ap.add_argument("--summary", help="also add the summary, as Markdown, to this file (the workflow's job summary)")
    ap.add_argument("--write-sec-config", action="store_true",
                    help="GitHub's machine only: write watcher/config.json from the secrets SEC_CONTACT_NAME and SEC_CONTACT_EMAIL")
    args = ap.parse_args(argv)
    if args.write_sec_config:
        return write_sec_config()
    if args.out and _inside(args.out, AGENT):
        ap.error("--out is inside the Agent folder: give a folder outside it")
    secrets = contact()
    if not secrets:
        print("Put your name and email in Agent/watcher/config.json first (the SEC asks for them), then run this again.")
        return 1
    folder = args.out or tempfile.mkdtemp(prefix="probe-")
    os.makedirs(folder, exist_ok=True)
    result = dict(started=W.now_local().isoformat(), machine=machine(), seconds={})
    print(f"Probe on {result['machine']['where']}: watcher (dry run), Part B plan, Part A, DeepSeek's address. "
          "This takes a few minutes; nothing is saved in Agent.", flush=True)

    t = time.time()
    result["watcher"], printed = probe_watcher(folder)
    result["seconds"]["watcher"] = time.time() - t
    for name in os.listdir(folder):                   # the watcher's own report of this dry run
        if name.endswith(" test.md"):
            path = os.path.join(folder, name)
            _write(path, hide(open(path, encoding="utf-8").read(), secrets))
    if result["watcher"].get("stopped"):
        _write(os.path.join(folder, "Watcher (printed).txt"), hide(printed, secrets))
    print("  1/4 watcher done", flush=True)

    t = time.time()
    result["part_b"], printed = probe_part_b()
    result["seconds"]["part_b"] = time.time() - t
    _write(os.path.join(folder, "Part B plan (nothing read or saved).txt"), hide(printed, secrets))
    print("  2/4 Part B plan done", flush=True)

    t = time.time()
    result["part_a"], printed, report = probe_part_a()
    result["seconds"]["part_a"] = time.time() - t
    _write(os.path.join(folder, "Part A (not saved, not proposals).md"), hide(report or printed, secrets))
    print("  3/4 Part A done", flush=True)

    result["deepseek"] = probe_deepseek(opener)
    print("  4/4 DeepSeek's address done", flush=True)

    lines = summary(result)
    text = hide("\n".join(lines) + "\n", secrets)
    data = json.loads(hide(json.dumps(result, ensure_ascii=False, default=str), secrets))
    _write(os.path.join(folder, "probe.json"), json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    _write(os.path.join(folder, "probe summary.txt"), text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("## Probe\n\n```text\n" + text + "```\n")
    print("\n" + text)
    print(f"Saved in {folder}: this summary, the watcher's dry-run report, the Part B plan and the Part A report "
          "(none of them in Agent). Paste the summary above to Claude.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
