#!/usr/bin/env python3
"""
The AI approval gate (built 7 Oct 2026, on Seb's word). After a press of the "Check for updates" button it decides the
new figures nobody has decided: approve, or leave for Seb. It approves only when every code check passes AND DeepSeek
says yes. DeepSeek alone never approves, and what the gate does not approve stays open for Seb, as before.

    python3 Agent/approvals/gate.py                                    plan: what it would decide; asks and writes nothing
    python3 Agent/approvals/gate.py --from-line 458 --record           decides with saved answers only (free)
    python3 Agent/approvals/gate.py --from-line 458 --record --ask     may ask DeepSeek (paid: the button's run only)

--from-line N: only figures first proposed after line N of proposed.jsonl (the lines the press added) are decided.

What it may approve: a newer figure for a later period, VERIFIED — primary (a filing on www.sec.gov, or Part A), on
the same basis as the paper's figure. Never arithmetic (an ESTIMATE), "Paper differs", a company disclosure (a call or
a newsroom), a line Seb has ruled in review/paper_differs.json, a lead, "keep paper" or "reader wrong": it writes only
approvals. The checks, in order (each approval lists the ones it passed):
  1. the reader says "newer figure" and its extraction is verified (Part A: "NEWER FIGURE", and one label's value as
     the SEC gives it: a figure Part A works out, such as a quarter x 4 or a difference, is arithmetic);
  2. the new figure's period ends after the paper's, and after every period already decided for it (by Seb or the
     gate): the gate never replaces a decided figure with one for the same period or an earlier one;
  3. VERIFIED — primary: Part B's link is on www.sec.gov (Part A is the SEC's own data);
  4. exactly one Figures line has this code name, and no other (a line checked twice is published only when both
     halves are approved); the paper's value and unit match it; it is not ruled "Paper differs";
  5. the paper's date is a reporting date (the end of a month, or of a 52/53-week quarter), not a publication date;
  6. the same basis: the same period type and length (never across a 53-week fiscal year, Nvidia's 2027); a year-end
     balance is replaced only by a year-end balance (the line says "year end", or its date is the company's fiscal year
     end); no Part A note asking for care;
  7. the size: not 100 times off; within half to double the last approved figure (the paper's, if none);
  8. a clean record: Seb has never marked this rule "reader wrong" or "keep paper", nor reopened it after the gate
     decided it;
  then DeepSeek is asked one question (QUESTION below, with the paper's line, the rule and the quoted lines): the same
  measure, on the same basis? Both answers must be "yes".

Seb's decision always wins. The gate never decides a figure Seb has decided or reopened, nor anything on his newest
sheet before he records it (an unreadable sheet stops it); his later decision replaces the gate's. To take back every
approval of the gate's on a row (and keep his):
    python3 Agent/approvals/approvals.py reopen F061 --gate

Every question and answer is saved in approvals/gate/answers/, so a replay gives the same decisions for free. The gate's
approvals are added to the end of Agent/reader/decisions.jsonl with "decided_by": "AI gate", the checks they passed and
the answer's file; earlier lines are never changed. Each recording run writes a report to approvals/gate/reports/.
Nothing it writes reaches the website except, for a published figure, who approved it (review.json evidence.approved_by).
"""
import argparse
import calendar
import datetime as dt
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
READER = os.path.join(AGENT, "reader")
for _p in (os.path.join(AGENT, "watcher"), READER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import watcher as W  # noqa: E402  the workbook reader
import companies as C  # noqa: E402  fiscal year ends
import claims as R  # noqa: E402  Part B's rules
import reader_sec as RS  # noqa: E402  Part A's rules (SPECS)
from lib import ai as A, openlist as O  # noqa: E402
import approvals as AP  # noqa: E402
import sheet as SH  # noqa: E402
import xlsx as X  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location("review_build", os.path.join(AGENT, "review", "build.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_builder()        # the review builder: the website's own Figures-line matching and number rules

GATE = O.GATE
FIGURES = ("figure", "Part A figure", "arithmetic")
PERIODS = ("instant", "3m", "6m", "9m", "12m", "ttm")       # figures for a period (not 'said', 'event', 'policy' or 'absence')
SIZE = (0.5, 2.0)                                           # the new figure, against the last approved one (or the paper's)
YEAR_WORDS = re.compile(r"year[- ]end|fiscal year|\b10-K\b", re.I)
PART_A_KINDS = {"q": "one quarter", "ytd": "the year to date", "fy": "a fiscal year", "i": "a balance on one date",
                "ytd_ann": "the year to date, annualised"}

# What DeepSeek is asked: one question per finding that passed every code check.
SYSTEM = """You check one proposed update to a research paper. Answer in JSON only:
{"same_measure": "yes" or "no", "same_basis": "yes" or "no", "why": "one or two sentences"}.
same_measure: the quoted line gives exactly the quantity the paper's line gives, not a similar, wider or narrower one.
same_basis: the same kind of figure (a balance on a date, or a flow over a quarter, a year to date or a year), the same
company and scope, and the same definition as the paper's figure. A later date is expected and is not a reason to say
no. If you are not sure, say no."""
QUESTION = "Is the quoted line the same measure, on the same basis, as the paper's line? Answer yes or no for each, and why."


def default_paths(agent=AGENT):
    here, reader = os.path.join(agent, "approvals"), os.path.join(agent, "reader")
    return dict(reader_dir=reader, out_dir=here, workbook=B.default_paths(agent)["workbook"],
                paper_differs=os.path.join(agent, "review", "paper_differs.json"), sheets_dir=os.path.join(here, "sheets"),
                answers_dir=os.path.join(here, "gate", "answers"), reports_dir=os.path.join(here, "gate", "reports"),
                config=os.path.join(reader, "config.json"))


# ------------------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------------------
def num(v):
    try:
        x = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def day(s):
    try:
        return dt.date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def reporting_date(d):
    """The end of a month, or of a 52/53-week quarter (Nvidia's end on the last Sunday of the month)."""
    return d is not None and calendar.monthrange(d.year, d.month)[1] - d.day <= 6


def year_end(d, fye):
    return d is not None and fye is not None and d.month == fye and reporting_date(d)


def _date(s):
    return (s or "")[:10]


def unique(ds):
    return sorted({d["_n"]: d for d in ds}.values(), key=lambda d: d["_n"])


def component(row, key):
    return next((c for c in (R.CLAIMS.get(row) or {}).get("components", []) if c.get("key") == key), None)


def spec_of(p):
    return next((s for s in RS.SPECS if s["id"] == p.get("row") and s["what"] == p.get("figure")), None)


def computed(spec):
    """A Part A figure the code works out (a difference, a ratio, a quarter x 4, an annualised year to date), not one
    label's value as the SEC gives it."""
    if len(spec["parts"]) != 1 or spec["parts"][0][0] == "ytd_ann":
        return True
    try:
        return abs(spec["f"]([1.2345]) - 1.2345) > 1e-12
    except Exception:
        return True


WEEK_YEARS = {"NVDA": 6}      # fiscal years that end on the last <weekday> of the month (Nvidia: Sunday): 52 or 53 weeks


def last_weekday(year, month, weekday):
    d = dt.date(year, month, calendar.monthrange(year, month)[1])
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def long_year(company, end):
    """Whether `end` closes a 53-week fiscal year (its last quarter runs 14 weeks, not 13)."""
    wd, fye = WEEK_YEARS.get(company), C.FYE.get(company)
    if wd is None or end is None or fye is None or end.month != fye or end != last_weekday(end.year, fye, wd):
        return False
    return (end - last_weekday(end.year - 1, fye, wd)).days > 364


# ------------------------------------------------------------------------------------------
# Seb's newest sheet: what he is deciding now
# ------------------------------------------------------------------------------------------
def pending_sheet(sheets_dir, decisions):
    """Seb's newest sheet (by when it was made) if no decision has been recorded from it yet, and the proposed.jsonl
    lines its rows name. Returns (name or None, lines, problem or None)."""
    paths = [p for p in glob.glob(os.path.join(sheets_dir, "*.xlsx")) if not os.path.basename(p).startswith("~$")]
    if not paths:
        return None, set(), None
    made = {}
    for p in paths:
        try:
            made[p] = SH.made_at(X.read(p)) or ""
        except Exception:
            made[p] = ""
        if not made[p]:                           # it could be the sheet Seb is filling in: decide nothing
            return os.path.basename(p), set(), (f"the sheet {os.path.basename(p)} cannot be read or does not say when it was "
                                                "made, so the gate cannot see what you are deciding")
    newest = max(paths, key=lambda p: (made[p], os.path.basename(p)))
    name = os.path.basename(newest)
    if any(d.get("sheet") == name for d in decisions):
        return name, set(), None
    lines = set()
    try:
        for r in SH.read(newest, X.read, every_row=True):
            try:
                lines.update(SH.parse_id(r["id"])[1])
            except ValueError:
                continue
    except Exception as e:
        return name, set(), f"your newest sheet ({name}) cannot be read ({e.__class__.__name__}), so the gate cannot see what you are deciding"
    return name, lines, None


def sheet_note(name, decisions):
    """What the report says about Seb's newest sheet: the gate can hold back only a sheet it can see."""
    if not name:
        return ("No sheet of yours is in approvals/sheets/. The gate holds back only a sheet it can see: a sheet you are "
                "filling in on your Mac must be pushed before a press, or the gate may decide its items.")
    if any(d.get("sheet") == name for d in decisions):
        return f"Your newest sheet, {name}, is recorded: nothing on it is held back."
    return f"Your newest sheet, {name}, is not recorded yet: everything on it is left to you."


# ------------------------------------------------------------------------------------------
# The code checks: each returns (passed, what it found)
# ------------------------------------------------------------------------------------------
def check_kind(e, p):
    if e["kind"] == "arithmetic":
        return False, "arithmetic: an ESTIMATE worked out by the code, which the gate never approves"
    if e["kind"] == "Part A figure":
        if p.get("status") != "NEWER FIGURE":
            return False, f"Part A says '{(p.get('status') or 'nothing').lower()}', not a newer figure"
        spec = spec_of(p)
        if spec is None:
            return False, "Part A's rule for this figure is not in SPECS"
        if computed(spec):
            return False, f"worked out by the code from the SEC's labels ({spec['what']}): arithmetic, which the gate never approves"
        return True, "a newer figure in the SEC's labelled data (Part A), as labelled"
    said = p.get("paper_check") or p.get("kind") or "nothing"
    if p.get("kind") != "newer figure" or said != "newer figure":
        return False, f"the reader says '{said}', not 'newer figure'"
    if p.get("extraction") != "verified":
        return False, f"the extraction is '{p.get('extraction') or 'not recorded'}', not verified"
    return True, "the reader says 'newer figure', and its extraction is verified"


def check_period(p, decided=()):
    """Later than the paper's period, and than every period already decided for this figure (by Seb or the gate):
    the gate never replaces a decided figure with one for the same period or an earlier one."""
    new, old = day(p.get("new_period_end")), day(p.get("paper_period_end"))
    if not new or not old:
        return False, "no period end for the new figure or for the paper's"
    if new <= old:
        return False, f"not a later period: {new}, the paper's {old}"
    latest = max((x for x in decided if x), default=None)
    if latest and new <= latest:
        return False, f"not after the period already decided ({latest}): {new}"
    return True, f"a later period: {new}, after the paper's {old}" + (f" and the one already decided ({latest})" if latest else "")


def check_primary(e, p):
    if e["kind"] == "Part A figure":
        link = B.https(p.get("filing"))
        if link and B.host(link) == "www.sec.gov":
            return True, "VERIFIED — primary: the SEC's labelled data"
        return False, "Part A without an SEC filing link"
    link = B.https(p.get("link"))
    if not link:
        return False, "no https link to the document"
    if B.host(link) != "www.sec.gov":
        return False, f"VERIFIED — company disclosure ({B.host(link)}), not primary"
    return True, "VERIFIED — primary (www.sec.gov)"


def check_line(e, p, lines_by_code, ruled):
    """Returns (passed, text, the Figures line or None)."""
    name = B.code_name(e)
    matches = lines_by_code.get(name, [])
    if len(matches) != 1:
        return False, f"'{name}' is on {len(matches)} Figures lines, not one", None
    fl = matches[0]
    fid = fl["Figure ID"]
    if B.cell(fl.get("Ledger ID")) != B.cell(e.get("row")):
        return False, f"'{name}' is on {fid}, which belongs to another ledger row", fl
    if fid in ruled:
        return False, f"{fid} is ruled 'Paper differs' (review/paper_differs.json)", fl
    names = B.code_names(fl)
    if len(names) > 1:
        return False, (f"{fid} is checked twice ({'; '.join(names)}): it is published only when both are approved with "
                       "the same figure"), fl
    if not B.same_paper_value(p.get("paper_value"), fl.get("Value")):
        return False, f"the finding's paper value ({p.get('paper_value')}) is not {fid}'s Value ({fl.get('Value')})", fl
    if B.cell(p.get("unit")) != B.cell(fl.get("Unit")):
        return False, f"the finding's unit ({p.get('unit')}) is not {fid}'s ({fl.get('Unit')})", fl
    return True, f"Figures line {fid}: the paper's value and unit match, and it is not ruled 'Paper differs'", fl


def check_reporting(p):
    old = day(p.get("paper_period_end"))
    if reporting_date(old):
        return True, f"the paper's date ({old}) is a reporting date"
    return False, f"the paper's date ({p.get('paper_period_end')}) is not the end of a reporting period (a publication date?)"


def check_basis(e, p, fl, company):
    old, new = day(p.get("paper_period_end")), day(p.get("new_period_end"))
    if e["kind"] == "Part A figure":
        if p.get("note"):
            return False, f"the reader's note asks for care: {p['note']}"
        spec = spec_of(p)
        if spec is None:
            return False, "Part A's rule for this figure is not in SPECS"
        kinds = {k for k, _ in spec["parts"]}
        if kinds & {"ytd", "ytd_ann"} and old and new and new.month != old.month:
            return False, "a year to date of another length than the paper's"
        what = "instant" if kinds == {"i"} else "flow"
        basis = ", ".join(sorted(PART_A_KINDS.get(k, k) for k in kinds))
    else:
        pt, nt = p.get("paper_period_type"), p.get("new_period_type")
        if pt != nt:
            return False, f"a '{nt}' figure against the paper's '{pt}'"
        if pt not in PERIODS:
            return False, f"a '{pt}' figure (a statement, event or policy), not a figure for a period"
        what, basis = ("instant" if pt == "instant" else "flow"), pt
    if what == "instant":
        fye = C.FYE.get(company)
        if fye is None:
            return False, f"a balance, but the fiscal year end of '{company}' is not known"
        label = f"{B.cell((fl or {}).get('What the figure is'))} {B.cell((fl or {}).get('Period / as of'))}"
        if (year_end(old, fye) or YEAR_WORDS.search(label)) and not year_end(new, fye):
            return False, f"the paper's figure is a year-end balance ({old}); the new one is not ({new})"
        return True, f"the same basis: a balance, {'year end to year end' if year_end(new, fye) else 'neither a year-end figure'}"
    if long_year(company, new) or long_year(company, old):
        return False, (f"a fiscal year of 53 weeks ends on {new if long_year(company, new) else old}: its last quarter, "
                       "and the year, run a week longer than the paper's")
    return True, f"the same basis: {basis} to {basis}"


def check_size(e, p, by_line):
    new, paper = num(p.get("new_value")), num(p.get("paper_value"))
    if new is None or paper is None:
        return False, "not a number (a text figure)"
    if "%" in str(p.get("unit") or "") and paper and any(abs(new / paper / f - 1) < 0.005 for f in (0.01, 100)):
        return False, "100 times the paper's figure, or a hundredth of it: the scale was misread"
    approvals = [d for d in O.active_decisions(e["lines"], by_line)
                 if d["decision"] == "approved" and num((d.get("figure") or [None])[0]) is not None]
    ref, ref_from = (num(approvals[-1]["figure"][0]), "the last approved figure") if approvals else (paper, "the paper's figure")
    if not ref or not new or (ref > 0) != (new > 0):
        return False, f"a zero or a change of sign against {ref_from}"
    r = new / ref
    if not SIZE[0] <= r <= SIZE[1]:
        return False, f"{r:.2f} times {ref_from}: outside half to double"
    return True, f"{r:.2f} times {ref_from} (half to double allowed)"


def check_record(e, by_line):
    ds = unique(d for n in e["lines"] for d in by_line.get(n, []))
    seb = [d for d in ds if not O.by_gate(d)]
    bad = [d for d in seb if d["decision"] in ("reader wrong", "kept paper")]
    if bad:
        return False, f"you marked this rule '{bad[-1]['decision']}' on {_date(bad[-1].get('decided_at'))}"
    first_gate = min((d["_n"] for d in ds if O.by_gate(d)), default=None)
    undone = [d for d in seb if d["decision"] == "reopen" and first_gate is not None and d["_n"] > first_gate]
    if undone:
        return False, f"you reopened this rule after the gate decided it ({_date(undone[-1].get('decided_at'))})"
    return True, "a clean record: never marked 'reader wrong' or 'keep paper', and no gate decision reopened"


# ------------------------------------------------------------------------------------------
# DeepSeek's question, its saved answers, and its verdict
# ------------------------------------------------------------------------------------------
def question(v, ledger_by_id):
    """The one question for a finding that passed every code check: the paper's line, the reader's rule, the new
    figure and the quoted lines (only those the code found word for word)."""
    p, fl = v["proposal"], v["line"] or {}
    lr = ledger_by_id.get(v["row"]) or {}
    L = [f"The paper's line: {B.cell(fl.get('What the figure is'))}",
         f"Printed in the paper as: {B.cell(fl.get('As the paper prints it')) or 'not printed'}",
         f"Its exact value: {B.cell(fl.get('Value'))} {B.cell(fl.get('Unit'))}, as of {B.cell(fl.get('Period / as of')) or p.get('paper_period_end')}",
         f"The paper says: \"{B.cell(lr.get('What the paper says'))}\""]
    if v["kind"] == "Part A figure":
        spec = spec_of(p) or {}
        parts = ", ".join(f"{c} ({PART_A_KINDS.get(k, k)})" for k, c in spec.get("parts", []))
        L += [f"The reader's rule for it: the SEC's labelled data (XBRL): {parts}; the figure is: {spec.get('what')}",
              f"The proposed update: {p.get('new_value')} {p.get('unit')} for the period ending {p.get('new_period_end')}, from "
              f"the {p.get('form')} filed {p.get('filed')} ({p.get('filing')}). There is no quoted text: the figure is the "
              "SEC's labelled data."]
    else:
        c = component(p.get("row"), p.get("component")) or {}
        L += [f"The reader's rule for it: measure \"{c.get('measure')}\", unit {c.get('unit')}, period type {c.get('period_type')}"
              + (f". Note: {c['note']}" if c.get("note") else ""),
              f"The proposed update: {p.get('new_value')} {p.get('unit')} for the period ending {p.get('new_period_end')} "
              f"({p.get('new_period_type')}), from {p.get('document')}, {p.get('link')}",
              "The quoted lines, each found word for word in the document by the code:"]
        L += [f"- {f.get('role')}: \"{f.get('text')}\"" for f in p.get("fragments") or []
              if isinstance(f, dict) and f.get("verified") is True]
    L += ["", QUESTION]
    return "\n".join(L)


def question_hash(user):
    return hashlib.sha256((SYSTEM + "\n\n" + user).encode("utf-8")).hexdigest()


def saved_answer(folder, h):
    """A saved answer to exactly this question (system and user text), or None: (record, file name)."""
    for path in sorted(glob.glob(os.path.join(folder, f"* {h[:12]}.json"))):
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("question_hash") == h:
            return rec, os.path.basename(path)
    return None


def save_answer(folder, v, h, user, answer, usage, model, now):
    """Keep the question and DeepSeek's answer (a new file, never overwritten), so a replay is free."""
    os.makedirs(folder, exist_ok=True)
    base = f"{v['proposal']['_line']} {h[:12]}"
    name, n = base + ".json", 1
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = f"{base} ({n}).json"
    rec = dict(question_hash=h, line=v["proposal"]["_line"], key=v["key"], asked_at=now.isoformat(), model=model,
               system=SYSTEM, user=user, answer=answer, usage=usage or {})
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return name


def verdict_of(answer):
    """(both yes, same measure, same basis, why) from DeepSeek's answer, whatever its shape."""
    a = answer if isinstance(answer, dict) else {}
    word = lambda k: str(a.get(k) or "").strip().lower().rstrip(".") or "no answer"
    m, b = word("same_measure"), word("same_basis")
    return m == "yes" and b == "yes", m, b, str(a.get("why") or "").strip()


def deepseek_asker(config_path):
    """The real question to DeepSeek (paid), with the reader's own settings and call (three tries)."""
    cfg = W.load_json(config_path, {})
    if not (cfg.get("deepseek_api_key") or "").strip():
        sys.exit("Put your DeepSeek API key in Agent/reader/config.json (\"deepseek_api_key\"), then run this again.")
    cfg = dict(cfg, max_tokens=min(int(cfg.get("max_tokens", 32000)), 16000))

    def ask(system, user):
        return A.deepseek(cfg, system, user)
    ask.model = cfg.get("model", "deepseek-flash")
    return ask


# ------------------------------------------------------------------------------------------
# Deciding
# ------------------------------------------------------------------------------------------
def evaluate(e, p, mine, by_line, on_sheet, sheet, lines_by_code, ruled):
    """One finding: is it the gate's to decide, and which code checks does it pass?"""
    v = dict(key=e["key"], kind=e["kind"], row=e["row"] or "", company=p.get("company") or O.company_of(p), entry=e,
             proposal=p, lines=mine, figure=O.figure_of(p), line=None, checks=[], decision="left", why="", yours="",
             answer=None, answer_file=None)
    seb = [d for d in unique(d for n in e["lines"] for d in by_line.get(n, [])) if not O.by_gate(d)]
    on_figure = [d for d in seb if set(d["lines"]) & set(mine)]
    if on_figure:
        v["yours"] = f"you decided this figure ('{on_figure[-1]['decision']}' on {_date(on_figure[-1].get('decided_at'))})"
    elif set(e["lines"]) & on_sheet:
        v["yours"] = f"it is on your newest sheet ({sheet}), which is not recorded yet"
    if v["yours"]:
        v["decision"], v["why"] = "yours", v["yours"]
        return v
    checks = v["checks"]
    checks.append(check_kind(e, p))
    if e["kind"] != "arithmetic":
        decided = [day((d.get("figure") or [None, None])[1]) for d in unique(d for n in e["lines"] for d in by_line.get(n, []))
                   if d["decision"] not in ("reopen", "done")]
        checks.append(check_period(p, decided))
        checks.append(check_primary(e, p))
        ok, text, v["line"] = check_line(e, p, lines_by_code, ruled)
        checks.append((ok, text))
        checks.append(check_reporting(p))
        checks.append(check_basis(e, p, v["line"], v["company"]))
        checks.append(check_size(e, p, by_line))
        checks.append(check_record(e, by_line))
    failed = [t for ok, t in checks if not ok]
    v["why"] = failed[0] if failed else ""
    return v


def decide(paths, ledger, figures, ruled, from_line=0, ask=None, now=None):
    """Every figure first proposed after line `from_line`: the gate's verdict. Returns (verdicts, problems, what the
    report says about Seb's newest sheet).
    `ask(system, user) -> (answer, usage)` asks DeepSeek; without it only saved answers are used."""
    now = now or AP.now_local()
    reader_dir = paths["reader_dir"]
    result, lines, bad = O.load(reader_dir)
    decisions = O.read_decisions(os.path.join(reader_dir, "decisions.jsonl"))
    problems = []
    if bad:
        problems.append(f"proposed.jsonl has lines that cannot be read ({', '.join(map(str, bad))})")
    if result["bad_decisions"]:
        problems.append(f"decisions.jsonl has lines that cannot be read ({', '.join(map(str, result['bad_decisions']))})")
    problems += O.pointer_problems(lines, decisions)
    sheet, on_sheet, sheet_problem = pending_sheet(paths["sheets_dir"], decisions)
    if sheet_problem:
        problems.append(sheet_problem)
    if problems:
        return [], problems, sheet_note(sheet, decisions)
    line_of = {p["_line"]: p for p in lines}
    by_line = O._decisions_by_line(decisions)
    lines_by_code = {}
    for fl in figures:
        for n in B.code_names(fl):
            lines_by_code.setdefault(n, []).append(fl)
    ledger_by_id = {B.cell(r.get("ID")): r for r in ledger}
    verdicts = []
    for e in sorted(result["open"], key=lambda e: (e["row"] or "", e["key"])):
        if e["kind"] not in FIGURES:
            continue
        p = e["latest"]
        mine = sorted(n for n in e["lines"] if O.same_figure(O.figure_of(line_of[n]), O.figure_of(p)))
        if not mine or min(mine) <= from_line:
            continue                                  # not first found by this press
        verdicts.append(evaluate(e, p, mine, by_line, on_sheet, sheet, lines_by_code, ruled))
    for v in verdicts:
        if v["decision"] != "left" or v["why"]:
            continue
        user = question(v, ledger_by_id)
        h = question_hash(user)
        found = saved_answer(paths["answers_dir"], h)
        if found:
            rec, v["answer_file"] = found
            answer = rec.get("answer")
        elif ask is not None:
            answer, usage = ask(SYSTEM, user)
            v["answer_file"] = save_answer(paths["answers_dir"], v, h, user, answer, usage, getattr(ask, "model", None), now)
        else:
            v["why"] = "no saved answer, and DeepSeek was not asked (a plan or a dry run)"
            continue
        ok, m, b, why = verdict_of(answer)
        v["answer"] = dict(same_measure=m, same_basis=b, why=why)
        if ok:
            v["decision"] = "approved"
        else:
            v["why"] = f"DeepSeek: same measure {m}, same basis {b}" + (f" ({why})" if why else "")
    return verdicts, problems, sheet_note(sheet, decisions)


def record_of(v, ledger_by_id, run_id, now):
    """The decisions.jsonl line for one of the gate's approvals."""
    e = v["entry"]
    typ = SH.TYPE_OF_KIND[e["kind"]]
    a = v["answer"]
    return dict(decision="approved", decided_by=GATE, key=e["key"], lines=v["lines"], figure=v["figure"], kind=e["kind"],
                type=typ, row=v["row"], company=v["company"], what=SH._item(typ, e, ledger_by_id)["what"],
                checks=[t for _, t in v["checks"]] + [f"DeepSeek: same measure {a['same_measure']}, same basis {a['same_basis']}"],
                answer=v["answer_file"], gate_run=run_id, decided_at=now.isoformat())


# ------------------------------------------------------------------------------------------
# The report (private: Agent only) and the data for the button's pop-up
# ------------------------------------------------------------------------------------------
def shown(v):
    """(figure ID, label, the paper's figure, the new figure, document, link) as the website would show them."""
    p, fl = v["proposal"], v["line"]
    unit = (fl or {}).get("Unit") or p.get("unit")
    label = B.cell((fl or {}).get("What the figure is")) or SH._item(SH.TYPE_OF_KIND[v["kind"]], v["entry"], {})["what"]
    paper = B.paper_figure(fl)["shown"] if fl else B.cell(p.get("paper_value"))
    new = B.show(v["figure"][0], unit, computed=v["kind"] == "arithmetic")
    document = p.get("document") or (f"{p.get('form')} filed {p.get('filed')}" if p.get("form") else "worked out by the code")
    return dict(figure_id=(fl or {}).get("Figure ID") or B.code_name(v["entry"]), label=label, paper=paper, new=new,
                period_end=v["figure"][1], document=document, link=B.https(p.get("link") or p.get("filing")))


def data(verdicts, from_line, run_id, mode, sheet):
    return dict(run=run_id, mode=mode, from_line=from_line, sheet=sheet, figures=[
        dict(shown(v), key=v["key"], row=v["row"], company=v["company"],
             source={"figure": "Part B", "Part A figure": "Part A", "arithmetic": "arithmetic"}[v["kind"]], decision=v["decision"], why=v["why"],
             checks=[dict(passed=ok, text=t) for ok, t in v["checks"]], deepseek=v["answer"])
        for v in verdicts])


def report(verdicts, from_line, run_id, mode, sheet, now):
    groups = {k: [v for v in verdicts if v["decision"] == k] for k in ("approved", "left", "yours")}
    L = [f"# The AI gate, {now:%d %b %Y %H:%M} ({mode})", "",
         f"Figures first proposed after line {from_line} of proposed.jsonl: {len(verdicts)}. Approved by the gate: "
         f"{len(groups['approved'])}. Left for you: {len(groups['left'])}. Yours to decide (not looked at): {len(groups['yours'])}.",
         "", "The gate approves only when every code check passes and DeepSeek says yes. Your decisions always win; to take "
         "back the gate's approvals of a row:  python3 Agent/approvals/approvals.py reopen F061 --gate"]
    L += ["", sheet]
    for k, title in (("approved", "Approved by the AI gate"), ("left", "Left for you"), ("yours", "Yours to decide")):
        L += ["", f"## {title} ({len(groups[k])})", ""]
        if not groups[k]:
            L.append("None.")
        for v in groups[k]:
            s = shown(v)
            part_a = " (Part A)" if v["kind"] == "Part A figure" else ""
            L.append(f"- **{s['figure_id']}**{part_a} {s['label']}: paper {s['paper']} → **{s['new']}**"
                     + (f" for {s['period_end']}" if s["period_end"] else "") + f" · {s['document']}"
                     + (f" · {s['link']}" if s["link"] else ""))
            if k != "approved":
                L.append(f"    - Why: {v['why']}")
            L += [f"    - {'passed' if ok else 'FAILED'}: {t}" for ok, t in v["checks"] if ok == (k == "approved") or not ok]
            if v["answer"]:
                a = v["answer"]
                L.append(f"    - DeepSeek: same measure {a['same_measure']}, same basis {a['same_basis']}"
                         + (f": {a['why']}" if a["why"] else "") + f" ({v['answer_file']})")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------
def main(argv=None, paths=None, ask=None, now=None):
    ap = argparse.ArgumentParser(description="The AI approval gate: approve the new figures that pass every check, "
                                             "or leave them for Seb.")
    ap.add_argument("--from-line", type=int, help="only figures first proposed after this line of proposed.jsonl (the press's lines)")
    ap.add_argument("--record", action="store_true", help="add the gate's approvals to decisions.jsonl (saved answers only, unless --ask)")
    ap.add_argument("--ask", action="store_true", help="ask DeepSeek the questions with no saved answer (paid: the button's run)")
    ap.add_argument("--json", help="also write the verdicts, as data, to this file (for the button's pop-up)")
    args = ap.parse_args(argv)
    if (args.record or args.ask) and args.from_line is None:
        ap.error("--record and --ask need --from-line N: the gate decides only the figures a press found")
    if args.ask and not args.record:
        ap.error("--ask goes with --record")
    P = dict(default_paths(), **(paths or {}))
    now = now or AP.now_local()
    book = W.read_workbook(P["workbook"])
    ledger = W.table(book.get("Ledger", []))
    figures = W.table(book.get("Figures", []), first_header="Figure ID")
    ledger_by_id = {B.cell(r.get("ID")): r for r in ledger}
    ruled = set(B.read_paper_differs(P["paper_differs"]))
    asker = (ask or deepseek_asker(P["config"])) if args.ask else None
    from_line = args.from_line or 0
    try:
        verdicts, problems, sheet = decide(P, ledger, figures, ruled, from_line, asker, now)
    except W.FetchError as e:
        print(f"DeepSeek did not answer ({e}). Nothing recorded; the answers already received are saved.")
        return 1
    if problems:
        print("The gate decided nothing:\n" + "\n".join(f"  - {x}" for x in problems))
        return 1
    run_id = f"gate {now:%Y-%m-%d %H.%M.%S}"
    mode = "recorded" if args.record else "plan: nothing asked, nothing written"
    text = report(verdicts, from_line, run_id, mode, sheet, now)
    if args.record:
        approved = [v for v in verdicts if v["decision"] == "approved"]
        if approved:
            AP._append(os.path.join(P["reader_dir"], "decisions.jsonl"), [record_of(v, ledger_by_id, run_id, now) for v in approved])
            result = O.rebuild(P["reader_dir"], quiet=True)
            AP.write_lists(result, P["out_dir"], ledger_by_id, now)
        os.makedirs(P["reports_dir"], exist_ok=True)
        with open(os.path.join(P["reports_dir"], f"Gate {now:%Y-%m-%d %H.%M.%S}.md"), "w", encoding="utf-8") as f:
            f.write(text)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(data(verdicts, from_line, run_id, mode, sheet), f, ensure_ascii=False, indent=1)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
