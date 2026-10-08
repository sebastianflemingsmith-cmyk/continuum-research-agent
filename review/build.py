#!/usr/bin/env python3
"""
Step 5, V1: the tabular review. Every figure in the paper, with the state of each one, in one file (review.json) for
the website.

Run from the project folder:

    python3 Agent/review/build.py --out <folder>     writes <folder>/review.json
    python3 Agent/review/build.py                    writes it to a new folder in the system's temp folder, and prints where

It reads the watch list (Ledger and Figures sheets), the reader's findings (proposed.jsonl), Seb's decisions
(decisions.jsonl), what each run checked (runs.jsonl), the newest Part A report and the watcher's latest run
(handoff.json). It changes none of them and writes nothing but review.json. Standard library only, no network.

Only a finding with an approval in force is published, only for a figure in the paper, and only the fields listed in
SCHEMA. Everything it declined to publish is printed, never written. See review/README.md.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import urllib.parse
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

sys.dont_write_bytecode = True                        # writes nothing but its output, not even __pycache__
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
READER = os.path.join(AGENT, "reader")
for _p in (os.path.join(AGENT, "watcher"), READER):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import watcher as W  # noqa: E402  the watch list reader, watch_rules and recalculate
from lib import openlist as O  # noqa: E402  the open list: which decision is in force

WORKBOOK = "Watch list - The AI Lose Lose Race.xlsx"
PAPER = {"title": "The AI Lose Lose Race", "date": "23 September 2026",
         "pdf": "/reports/the-ai-lose-lose-race/files/the-ai-lose-lose-race.pdf"}

# The paper's sections, in the paper's order (the ledger's "Paper section" values). Rows are grouped by section; inside
# a section they keep ledger order.
SECTIONS = ["Intro", "How much should this industry be worth?", "What is AI earning now?", "Hidden figures",
            "Contractual obligation", "The financing", "Life of the equipment", "The revenue", "Circular Financing",
            "Funding", "Who holds the exposure? · Test 4", "Who holds the exposure? · Tests 1, 2, 3, 5",
            "Who holds the exposure? · Test 6", "Summary", "Tracker"]

STATES = ("newer", "differs", "recalc", "unchanged", "not_watched")
FIGURE_KINDS = ("figure", "arithmetic", "Part A figure")      # the open list's kinds that can be published

# An "Also used in" entry names a model if it starts (ignoring case) with one of these. "Tests section" is a section.
MODEL_PREFIXES = ("test ", "tests ", "core model", "basket", "basket revenue proxy", "capital base k", "annual charge",
                  "website calculator", "website model", "website headline and calculator")
NOT_MODELS = ("tests section",)

# Why a line is not watched, from the ledger's Type. Model outputs and author calculations have no dot.
MODEL_REASON = "a model output (or an author calculation): not checked by the reader"
TYPE_REASONS = {"Secondary research": "research, checked by hand", "Official research": "research, checked by hand",
                "Market data": "market data, refreshed by hand", "Press": "press", "Rule": "a rule",
                "Tracker": "a tracker, not in the paper", "Model output": MODEL_REASON, "Author calculation": MODEL_REASON}
NO_RULE = "no reader rule yet"

# runs.jsonl verdicts: what a check found
SAME = ("matches", "no change", "newer, unchanged")
NOTHING_NEWER = ("no update found", "no separate figure found", "historical")

PRIMARY, COMPANY, ESTIMATE, NOT_KNOWN = "VERIFIED — primary", "VERIFIED — company disclosure", "ESTIMATE", "not known"
SEB = "Seb"                                           # evidence.approved_by: who approved the published finding
GATE = O.GATE                                         # "AI gate": approvals/gate.py
FROM_SEC_DATA = "From the SEC's labelled data (no quoted text)"
BY_CODE = "Worked out by the code"
ALSO_SEC = "also in the SEC's labelled data"
QUOTE_ROLES = ("value", "context", "column")

# Every key review.json may hold, by object. schema_problems() checks the output against it; review/README.md
# documents each key, and the tests check both.
SCHEMA = {
    "review": ["schema", "built", "paper", "runs", "counts", "sections", "bullets"],
    "paper": ["title", "date", "pdf"],
    "runs": ["watcher", "part_b", "part_a"],
    "counts": ["rows", "figures", "watched_rows", "watched_figures", "states", "filters"],
    "states": list(STATES),
    "filters": ["all", "changes", "recalc"],
    "section": ["name", "rows"],
    "row": ["id", "type", "says", "built_from", "models", "citation", "figures"],
    "citation": ["fn", "document", "links"],
    "figure": ["id", "label", "paper", "watched", "state", "dot", "reason", "latest", "evidence", "affects", "recalc",
               "last_checked"],
    "paper_figure": ["printed", "shown", "value", "unit", "period"],
    "latest": ["shown", "value", "period_end", "inputs_from"],
    "evidence": ["source", "basis", "how", "document", "link", "link_text", "figure", "period_end", "quote", "formula",
                 "inputs", "also", "approved_by"],
    "quote": ["role", "text"],
    "input": ["name", "value", "source"],
    "affects": ["models", "rows", "text"],
    "affects_row": ["id", "models"],
    "recalc": ["path"],
    "last_checked": ["text", "date", "by"],
    "bullet": ["figure", "row", "segments"],
    "segment": ["text", "href", "figure"],
}


# ------------------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------------------
def cell(v):
    """A workbook cell as text ('' when empty)."""
    return "" if v is None else str(v).strip()


def code_names(figure_row):
    """A Figures line's 'Checked by code as', split on ';' and trimmed."""
    return [c.strip() for c in cell(figure_row.get("Checked by code as")).split(";") if c.strip()]


def code_name(entry):
    """The Figures name of an open-list entry: claims.py ROW COMPONENT, claims.py ROW formula NAME or Part A: NAME."""
    if entry["kind"] == "figure":
        return f"claims.py {entry.get('row')} {entry.get('component')}"
    if entry["kind"] == "arithmetic":
        return f"claims.py {entry.get('row')} formula {entry.get('component')}"
    return f"Part A: {entry.get('component')}"


def run_item(name):
    """The runs.jsonl item a claims.py name is checked as: 'claims.py F141 formula replacement_bill' -> 'F141:replacement_bill'."""
    m = re.fullmatch(r"claims\.py (\S+) (?:formula )?(\S+)", name)
    return f"{m.group(1)}:{m.group(2)}" if m else None


def https(url):
    """The address if it is a plain https link, else None (http, javascript: and anything else are dropped)."""
    u = cell(url)
    if not u or any(ch.isspace() for ch in u) or any(ord(ch) < 32 for ch in u):
        return None
    try:
        parts = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    return u if parts.scheme == "https" and parts.netloc and u.lower().startswith("https://") else None


def https_links(text):
    return [u for u in (https(p) for p in cell(text).split(";")) if u]


def host(url):
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_model(entry):
    e = entry.strip().lower()
    return e not in NOT_MODELS and e.startswith(MODEL_PREFIXES)


def models_of(ledger_row):
    """The model entries of a ledger row's 'Also used in', as written."""
    return [e.strip() for e in cell((ledger_row or {}).get("Also used in")).split(";") if e.strip() and is_model(e)]


ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def iso_date(v):
    return v if isinstance(v, str) and ISO_DATE.fullmatch(v) else None


def iso_time(v):
    """A time as YYYY-MM-DDTHH:MM:SS, or None."""
    try:
        return dt.datetime.fromisoformat(str(v)).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------
# Showing numbers (review/README.md, "How figures are shown")
# ------------------------------------------------------------------------------------------
def _digits(x, computed):
    """A number's digits without its sign: two decimals for the code's own arithmetic; otherwise as recorded, with no
    trailing zeros. Thousands are grouped with commas."""
    if computed:
        return f"{abs(x):,.2f}"
    d = Decimal(repr(abs(x))) if isinstance(x, float) else Decimal(abs(x))
    whole, _, frac = format(d.normalize() if d != d.to_integral() else d.to_integral(), "f").partition(".")
    return f"{int(whole):,}" + (f".{frac}" if frac else "")


def show(value, unit, computed=False):
    """A figure in its unit: '$288bn', '−$5.855bn', '27.36% of revenue', '6 years'. Words stay words."""
    if value is None or value == "":
        return ""
    if isinstance(value, str) or isinstance(value, bool):
        return str(value).strip()
    unit = cell(unit)
    sign = "−" if value < 0 else ""
    n = _digits(value, computed)
    if unit.startswith("$"):
        return f"{sign}${n}{unit[1:]}"
    if unit.startswith("%"):
        return f"{sign}{n}{unit}"
    return f"{sign}{n} {unit}" if unit else f"{sign}{n}"


def _decimal(v):
    try:
        d = Decimal(str(v).strip().replace(",", "")) if not isinstance(v, bool) else None
    except (InvalidOperation, ValueError):
        return None
    return d if d is not None and d.is_finite() else None


def same_paper_value(a, b):
    """The finding's paper value against the Figures line's Value: numbers within rounding (rounded to the coarser of the
    two), words ignoring case, spacing and a leading 'the'."""
    x, y = _decimal(a), _decimal(b)
    if x is not None and y is not None:
        places = max(x.as_tuple().exponent, y.as_tuple().exponent)        # the coarser of the two
        q = Decimal(1).scaleb(places)
        return x.quantize(q, ROUND_HALF_UP) == y.quantize(q, ROUND_HALF_UP)
    if x is not None or y is not None:
        return False
    words = lambda s: re.sub(r"^the ", "", " ".join(cell(s).lower().split()))
    return words(a) == words(b)


# ------------------------------------------------------------------------------------------
# Reading the inputs
# ------------------------------------------------------------------------------------------
PART_A_NAME = re.compile(r"^Part A (\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2})\.md$")


def newest_part_a(folder):
    """The newest Part A report in a folder (never one ending in ' test'), or None."""
    found = sorted(f for f in os.listdir(folder) if PART_A_NAME.match(f)) if os.path.isdir(folder) else []
    return os.path.join(folder, found[-1]) if found else None


def parse_part_a(name, text):
    """A Part A report: its time, the figures it found up to date, and the figures it found different (by name)."""
    m = PART_A_NAME.match(os.path.basename(name))
    out = dict(time=f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}" if m else None, same=[], differs=[])
    section = ""
    for ln in text.splitlines():
        if ln.startswith("## "):
            section = ln[3:].strip()
            continue
        if section.startswith("Up to date"):
            hit = re.match(r"- ([FT]\d{3}) (.+?): .+ at \d{4}-\d{2}-\d{2}\s*$", ln)
            if hit:
                out["same"].append((hit.group(1), hit.group(2)))
        elif section.startswith(("Newer figure", "Same period")) and ln.startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) > 1 and re.fullmatch(r"[FT]\d{3}", cells[0]):
                out["differs"].append((cells[0], cells[1]))
    return out


def default_paths(agent=AGENT):
    """Where the inputs are in the Agent folder."""
    return dict(workbook=os.path.join(agent, WORKBOOK), reader_dir=os.path.join(agent, "reader"),
                part_a_report=newest_part_a(os.path.join(agent, "reader", "reports")),
                handoff=os.path.join(agent, "watcher", "handoff.json"),
                paper_differs=os.path.join(agent, "review", "paper_differs.json"))


def read_paper_differs(path):
    """Seb's rulings (review/paper_differs.json): {Figure ID: ruling} for the lines whose approved newer finding is the
    paper's own figure stated differently, not a later one. No file is no ruling. Nothing in it is published."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in lines.items()):
        raise ValueError(f"{os.path.basename(path)}: expected {{\"lines\": {{\"F046-1\": {{\"decided\": ..., \"by\": ..., \"why\": ...}}}}}}")
    return lines


def read_inputs(workbook, reader_dir, part_a_report, handoff, paper_differs=None):
    """Everything build() needs, read from files (none is written). The open list is built in memory by openlist.load."""
    book = W.read_workbook(workbook)
    result, lines, bad = O.load(reader_dir)
    decisions = O.read_decisions(os.path.join(reader_dir, "decisions.jsonl"))
    runs = O.read_runs(os.path.join(reader_dir, "runs.jsonl"))
    part_a = None
    if part_a_report and os.path.exists(part_a_report):
        with open(part_a_report, encoding="utf-8") as f:
            part_a = parse_part_a(part_a_report, f.read())
    watcher_run = None
    if handoff and os.path.exists(handoff):
        with open(handoff, encoding="utf-8") as f:
            watcher_run = (json.load(f) or {}).get("run_id")
    return dict(ledger=W.table(book.get("Ledger", [])), figures=W.table(book.get("Figures", []), first_header="Figure ID"),
                openlist=result, proposals=lines, runs=runs, decisions=decisions, part_a=part_a, watcher_run=watcher_run,
                built_from=W.watch_rules(book)["built_from"], unreadable=dict(proposed=bad, decisions=result.get("bad_decisions") or []),
                paper_differs=read_paper_differs(paper_differs))


def built_from_of(ledger):
    """The ledger's 'Built from' links, read by the watcher's own watch_rules."""
    return W.watch_rules({"Ledger": [["ID", "Built from"]] + [[r.get("ID"), r.get("Built from")] for r in ledger]})["built_from"]


# ------------------------------------------------------------------------------------------
# The gate: which findings are published
# ------------------------------------------------------------------------------------------
def _entries(openlist):
    return [e for g in ("open", "gone", "aside", "closed") for e in (openlist.get(g) or [])]


def published_findings(openlist, lines_by_code, proposal_at, notes, ruled=()):
    """{code name: finding} for every finding with an approval in force that passes every check. A finding that fails
    one is listed in notes['declined'], never published. An approval is Seb's (no "decided_by") or the AI gate's; the
    gate's must also stay within its limits: a newer figure (Part B or Part A), VERIFIED — primary, on a line Seb has
    not ruled 'Paper differs'."""
    out = {}
    for e in _entries(openlist):
        made, earlier = e.get("decision_made"), e.get("earlier_decision")
        d = made or earlier
        if not d or d.get("decision") != "approved":
            continue
        if e.get("kind") not in FIGURE_KINDS:
            raise ValueError(f"an approved finding of a kind the review does not know: {e.get('key')} ({e.get('kind')})")

        def decline(why, figure=None):
            notes["declined"].append(dict(key=e.get("key"), figure=figure, why=why))

        who = d.get("decided_by")
        if who not in (None, "", GATE):
            decline(f"approved by '{who}', who is neither Seb nor the AI gate")
            continue
        by_gate = who == GATE
        name = code_name(e)
        matches = lines_by_code.get(name, [])
        if len(matches) != 1:
            decline(f"'{name}' is on {len(matches)} Figures lines, not one")
            continue
        fl = matches[0]
        fid = fl["Figure ID"]
        if cell(fl.get("Ledger ID")) != cell(e.get("row")):
            decline(f"'{name}' is on {fid}, which belongs to another ledger row", fid)
            continue
        own = [proposal_at[n] for n in d.get("lines") or [] if n in proposal_at]
        own = [p for p in own if O.same_figure(O.figure_of(p), d.get("figure"))]
        if not own:
            decline("none of the decision's own lines has the decision's figure", fid)
            continue
        p = max(own, key=lambda q: (q.get("found_at") or "", q["_line"]))
        if not same_paper_value(p.get("paper_value"), fl.get("Value")):
            decline(f"the finding's paper value ({p.get('paper_value')}) is not the line's Value ({fl.get('Value')})", fid)
            continue
        if cell(p.get("unit")) != cell(fl.get("Unit")):
            decline(f"the finding's unit ({p.get('unit')}) is not the line's unit ({fl.get('Unit')})", fid)
            continue
        if e["kind"] == "arithmetic":
            if p.get("kind") != "formula with newer inputs":
                decline(f"an arithmetic finding of kind '{p.get('kind')}'", fid)
                continue
            state = "newer"
        else:
            new, old = iso_date(p.get("new_period_end")), iso_date(p.get("paper_period_end"))
            if not new or not old:
                decline("the finding has no period end for the new figure or for the paper's", fid)
                continue
            if new < old:
                decline(f"the new figure ({new}) is earlier than the paper's ({old})", fid)
                continue
            state = "newer" if new > old else "differs"
        if by_gate:
            link = https(p.get("filing") if e["kind"] == "Part A figure" else p.get("link"))
            limit = ("arithmetic (an ESTIMATE)" if e["kind"] == "arithmetic" else
                     "not a newer figure (Paper differs)" if state != "newer" else
                     "on a line Seb ruled 'Paper differs'" if fid in ruled else
                     "not VERIFIED — primary" if not link or host(link) != "www.sec.gov" else None)
            if limit:
                decline(f"approved by the AI gate, but {limit}: outside what the gate may approve", fid)
                continue
        if not made:
            notes["earlier_approval"].append(dict(key=e.get("key"), figure=fid))
        out[name] = dict(entry=e, decision=d, proposal=p, figure_id=fid, state=state, kind=e["kind"],
                         approved_by=GATE if by_gate else SEB)
    return out


def resolve_line(fl, published, notes):
    """The one finding a Figures line shows, or None. A line with two code names is published only when both have an
    approved finding with the same figure; the documents' (Part B) evidence is used, noting the SEC's labelled data."""
    names = code_names(fl)
    found = [published[n] for n in names if n in published]
    if not found:
        if len(names) > 1 and any(x["figure_id"] == fl["Figure ID"] for x in notes["declined"]):
            notes["two_code_names"].append(dict(figure=fl["Figure ID"], why="one of its findings was declined"))
        return None
    if len(names) == 1:
        return found[0]
    if len(found) < len(names):
        notes["two_code_names"].append(dict(figure=fl["Figure ID"], why="only one of its code names has an approved finding"))
        return None
    if not all(O.same_figure(found[0]["decision"].get("figure"), x["decision"].get("figure")) for x in found[1:]):
        notes["two_code_names"].append(dict(figure=fl["Figure ID"], why="its two approved findings differ"))
        return None
    first = next((x for x in found if x["kind"] != "Part A figure"), found[0])
    by = GATE if any(x["approved_by"] == GATE for x in found) else SEB      # Seb's only when every approval behind it is his
    return dict(first, also=any(x["kind"] == "Part A figure" for x in found if x is not first), approved_by=by)


def evidence(f, fl):
    """The evidence of a published finding, from the allowed fields only, and who approved it ("Seb" or "AI gate")."""
    return dict(_evidence(f, fl), approved_by=f["approved_by"])


def _evidence(f, fl):
    p, value = f["proposal"], f["decision"].get("figure") or [None, None]
    unit = fl.get("Unit")
    if f["kind"] == "arithmetic":
        ins = p.get("inputs") or {}
        srcs = p.get("input_sources") or {}
        return dict(source="arithmetic", basis=ESTIMATE, how=BY_CODE, figure=show(value[0], unit, computed=True),
                    formula=cell(p.get("formula")) or NOT_KNOWN,
                    inputs=[dict(name=k, value=v, source=cell(srcs.get(k)) or NOT_KNOWN) for k, v in ins.items()])
    if f["kind"] == "Part A figure":
        form, filed = cell(p.get("form")), cell(p.get("filed"))
        ev = dict(source="Part A", basis=PRIMARY, how=FROM_SEC_DATA, document=f"{form} filed {filed}" if form and filed else NOT_KNOWN,
                  figure=show(value[0], unit), period_end=value[1])
        link = https(p.get("filing"))
        if link:
            ev.update(link=link, link_text="SEC filing index")
        return ev
    link = https(p.get("link"))
    ev = dict(source="Part B", basis=(PRIMARY if host(link) == "www.sec.gov" else COMPANY) if link else NOT_KNOWN,
              document=cell(p.get("document")) or NOT_KNOWN, figure=show(value[0], unit), period_end=value[1],
              quote=[dict(role=fr.get("role"), text=fr.get("text")) for fr in p.get("fragments") or []
                     if isinstance(fr, dict) and fr.get("verified") is True and fr.get("role") in QUOTE_ROLES
                     and isinstance(fr.get("text"), str)])
    if link:
        ev.update(link=link, link_text=host(link))
    if f.get("also"):
        ev["also"] = ALSO_SEC
    return ev


def latest(f, fl):
    value, period = (f["decision"].get("figure") or [None, None])[:2]
    if f["kind"] == "arithmetic":
        srcs = f["proposal"].get("input_sources") or {}
        return dict(shown=show(value, fl.get("Unit"), computed=True), value=value,
                    inputs_from="; ".join(cell(srcs.get(k)) or NOT_KNOWN for k in (f["proposal"].get("inputs") or {})) or NOT_KNOWN)
    return dict(shown=show(value, fl.get("Unit")), value=value, period_end=period)


# ------------------------------------------------------------------------------------------
# Last checked (lines shown unchanged)
# ------------------------------------------------------------------------------------------
def check_part_b(item, runs):
    """The newest real check of a runs.jsonl item (replays and 'not checked' are not checks)."""
    seen = False
    for r in sorted((r for r in runs if r.get("replay") is False), key=lambda r: r.get("found_at") or "", reverse=True):
        v = (r.get("items") or {}).get(item)
        if not isinstance(v, str):
            continue
        seen = True
        if v.startswith("not checked"):
            continue
        t = iso_time(r.get("found_at"))
        if not t:
            continue
        kind = "same" if v in SAME else "nothing newer" if v in NOTHING_NEWER else "difference"
        return dict(time=t, kind=kind, part="Part B", verdict=v)
    return dict(time=None, kind="not checked yet", part="Part B") if seen else None


def check_part_a(name, part_a):
    if not part_a or not part_a.get("time"):
        return None
    if name in {n for _, n in part_a["same"]}:
        return dict(time=part_a["time"], kind="same", part="Part A")
    if name in {n for _, n in part_a["differs"]}:
        return dict(time=part_a["time"], kind="difference", part="Part A", verdict="found a different figure")
    return None


def last_checked(fl, runs, part_a, notes):
    checks = []
    for name in code_names(fl):
        if name.startswith("Part A: "):
            c = check_part_a(name[len("Part A: "):], part_a)
        else:
            item = run_item(name)
            c = check_part_b(item, runs) if item else None
        if c:
            checks.append(c)
    real = [c for c in checks if c["time"]]
    if not real:
        return dict(text="not checked yet", by="Part B") if checks else dict(text=NOT_KNOWN)
    c = max(real, key=lambda c: (c["time"], c["part"] == "Part B"))
    date = c["time"][:10]
    if c["kind"] == "difference":
        notes["unpublished_differences"].append(dict(figure=fl["Figure ID"], date=date, by=c["part"], verdict=c["verdict"]))
        text = f"last checked {date}"
    else:
        text = f"checked {date}: " + ("same as the paper" if c["kind"] == "same" else "nothing newer in the documents read")
    return dict(text=text, date=date, by=c["part"])


# ------------------------------------------------------------------------------------------
# Needs recalculating, and the models a change affects
# ------------------------------------------------------------------------------------------
def path_text(rid, uses, order, seen=()):
    """'F157 ← F064 ← F059, F060, F061, F062, F063': a row, the rows it uses that moved, and so on back to the changes."""
    us = sorted(uses.get(rid, ()), key=lambda r: order.get(r, len(order)))
    parts = []
    for u in us:
        if u in uses and u not in seen and u != rid:
            sub = path_text(u, uses, order, seen + (rid,))
            parts.append(sub if len(us) == 1 else f"({sub})")
        else:
            parts.append(u)
    return f"{rid} ← " + ", ".join(parts)


def affects(rid, built_from, ledger_by_id):
    reached = [x["id"] for x in W.recalculate([rid], built_from)]
    own = models_of(ledger_by_id.get(rid))
    rows = [dict(id=r, models=models_of(ledger_by_id.get(r))) for r in reached]
    parts = own + [r["id"] + (" (" + "; ".join(r["models"]) + ")" if r["models"] else "") for r in rows]
    return dict(models=own, rows=rows, text="; ".join(parts) or "no model")


# ------------------------------------------------------------------------------------------
# The review
# ------------------------------------------------------------------------------------------
def paper_figure(fl):
    printed = cell(fl.get("As the paper prints it"))
    value, unit = fl.get("Value"), cell(fl.get("Unit"))
    if printed:
        shown = printed
    elif value not in (None, ""):
        shown = f"{show(value, unit)} (not printed in the paper)"
    else:
        shown = "tracker: not in the paper"
    out = dict(shown=shown, value=value, unit=unit, period=cell(fl.get("Period / as of")))
    if printed:
        out = dict(printed=printed, **out)
    return out


def bullet(fid, rid, label, f, fig):
    """'<label>: now <latest> (paper: <paper>). <document>, figure for <period end> · <link> · Affects: <models>'."""
    ev, head = fig["evidence"], f"{fig['paper']['shown']}"
    middle = f": now {fig['latest']['shown']} (paper: {head}). "
    segs = [dict(text=label, figure=fid)]
    tail = f" · Affects: {fig['affects']['text']}"
    if f["kind"] == "arithmetic":
        segs.append(dict(text=f"{middle}{BY_CODE} ({ESTIMATE}), inputs from {fig['latest']['inputs_from']}{tail}"))
    else:
        segs.append(dict(text=f"{middle}{ev['document']}, figure for {fig['latest']['period_end']} · "))
        segs.append(dict(text=ev["link_text"], href=ev["link"]) if ev.get("link") else dict(text=NOT_KNOWN))
        segs.append(dict(text=tail))
    return dict(figure=fid, row=rid, segments=segs)


def build(ledger, figures, openlist, proposals, runs=(), decisions=(), part_a=None, watcher_run=None, built_from=None,
          unreadable=None, paper_differs=None):
    """The review, as a dict (review.json), and notes for Seb (printed, never written): what was declined and why.
    Plain data in: ledger and Figures rows (dicts), the open list (openlist.build or load), the proposal lines (with
    their '_line'), runs, decisions, the parsed Part A report, the watcher's latest run_id, and Seb's rulings of
    'Paper differs' (Figure IDs; only the IDs are used)."""
    notes = dict(declined=[], earlier_approval=[], two_code_names=[], unpublished_differences=[], part_a_unmatched=[],
                 no_reader_rule=[], model_outputs_without_built_from=[], watched_research_or_calculation=[],
                 paper_differs=[], paper_differs_unused=[], unreadable=unreadable or {})
    built_from = built_from_of(ledger) if built_from is None else built_from
    ledger_by_id = {cell(r.get("ID")): r for r in ledger}
    order = {rid: i for i, rid in enumerate(ledger_by_id)}
    for r in ledger:
        if cell(r.get("Paper section")) not in SECTIONS:
            raise ValueError(f"ledger row {r.get('ID')} is in a section the review does not know: {r.get('Paper section')!r}")
    lines_of = {}
    lines_by_code = {}
    for fl in figures:
        lines_of.setdefault(cell(fl.get("Ledger ID")), []).append(fl)
        for n in code_names(fl):
            lines_by_code.setdefault(n, []).append(fl)
    proposal_at = {p["_line"]: p for p in proposals if isinstance(p, dict) and "_line" in p}

    ruled = list(paper_differs or ())                 # Seb's rulings: the paper's own figure, stated differently
    published = published_findings(openlist, lines_by_code, proposal_at, notes, ruled)
    shown = {}                                        # Figure ID -> the finding it shows
    for fl in figures:
        f = resolve_line(fl, published, notes)
        if f:
            shown[fl["Figure ID"]] = f
    for fl in figures:
        fid = fl["Figure ID"]
        if fid in ruled and fid in shown and shown[fid]["state"] == "newer":
            shown[fid] = dict(shown[fid], state="differs")
            notes["paper_differs"].append(fid)
    notes["paper_differs_unused"] = [fid for fid in ruled if fid not in notes["paper_differs"]]
    changed = []
    for rid in ledger_by_id:
        if any(fl["Figure ID"] in shown for fl in lines_of.get(rid, [])):
            changed.append(rid)
    uses = {}
    for c in changed:
        for x in W.recalculate([c], built_from):
            uses.setdefault(x["id"], set()).update(x["uses"])

    sections, bullets = [], []
    for name in SECTIONS:
        rows = []
        for rid, lr in ledger_by_id.items():
            if cell(lr.get("Paper section")) != name:
                continue
            typ = cell(lr.get("Type"))
            figs = []
            for fl in lines_of.get(rid, []):
                fid, label = fl["Figure ID"], cell(fl.get("What the figure is"))
                watched = bool(code_names(fl))
                fig = dict(id=fid, label=label, paper=paper_figure(fl), watched=watched)
                f = shown.get(fid)
                fig["state"] = f["state"] if f else "recalc" if rid in uses else "unchanged" if watched else "not_watched"
                if not watched:
                    reason = TYPE_REASONS.get(typ, NO_RULE)
                    fig.update(dot=reason != MODEL_REASON, reason=reason)
                    if reason == NO_RULE:
                        notes["no_reader_rule"].append(fid)
                elif typ in ("Secondary research", "Official research", "Model output", "Author calculation", "Market data") and \
                        (rid, typ) not in notes["watched_research_or_calculation"]:
                    notes["watched_research_or_calculation"].append((rid, typ))
                if f:
                    fig["latest"] = latest(f, fl)
                    fig["evidence"] = evidence(f, fl)
                    if f["state"] == "newer":
                        fig["affects"] = affects(rid, built_from, ledger_by_id)
                elif fig["state"] == "recalc":
                    fig["recalc"] = dict(path=path_text(rid, uses, order))
                if watched and not f:
                    fig["last_checked"] = last_checked(fl, runs, part_a, notes)
                figs.append(fig)
                if f and f["state"] == "newer":
                    bullets.append(bullet(fid, rid, label, f, fig))
            if typ == "Model output" and not built_from.get(rid):
                notes["model_outputs_without_built_from"].append(rid)
            rows.append(dict(id=rid, type=typ, says=cell(lr.get("What the paper says")), built_from=list(built_from.get(rid, [])),
                             models=models_of(lr), citation=dict(fn=cell(lr.get("Paper fn")), document=cell(lr.get("Primary document")),
                                                                 links=https_links(lr.get("Link"))), figures=figs))
        if rows:
            sections.append(dict(name=name, rows=rows))

    names = {n[len("Part A: "):] for n in lines_by_code if n.startswith("Part A: ")}
    for rid, n in (part_a or {}).get("same", []) + (part_a or {}).get("differs", []):
        if n not in names:
            notes["part_a_unmatched"].append(f"{rid} {n}")

    all_figs = [f for s in sections for r in s["rows"] for f in r["figures"]]
    all_rows = [r for s in sections for r in s["rows"]]
    part_b_runs = [iso_time(r.get("found_at")) for r in runs if r.get("replay") is False]
    part_b = max([t for t in part_b_runs if t], default=None)
    times = [iso_time(d.get("decided_at")) for d in decisions] + [part_b, (part_a or {}).get("time"), iso_time(watcher_run)]
    review = dict(
        schema=1, built=max([t for t in times if t], default=None), paper=dict(PAPER),
        runs=dict(watcher=iso_time(watcher_run), part_b=part_b, part_a=(part_a or {}).get("time")),
        counts=dict(rows=len(all_rows), figures=len(all_figs),
                    watched_rows=sum(1 for r in all_rows if any(f["watched"] for f in r["figures"])),
                    watched_figures=sum(1 for f in all_figs if f["watched"]),
                    states={s: sum(1 for f in all_figs if f["state"] == s) for s in STATES},
                    filters=dict(all=len(all_rows),
                                 changes=sum(1 for r in all_rows if any(f["state"] in ("newer", "differs") for f in r["figures"])),
                                 recalc=sum(1 for r in all_rows if any(f["state"] == "recalc" for f in r["figures"])))),
        sections=sections, bullets=bullets)
    return review, notes


# ------------------------------------------------------------------------------------------
# The schema check, the file and the command
# ------------------------------------------------------------------------------------------
CHILDREN = {("review", "paper"): "paper", ("review", "runs"): "runs", ("review", "counts"): "counts",
            ("counts", "states"): "states", ("counts", "filters"): "filters", ("review", "sections"): "section",
            ("section", "rows"): "row", ("row", "citation"): "citation", ("row", "figures"): "figure",
            ("figure", "paper"): "paper_figure", ("figure", "latest"): "latest", ("figure", "evidence"): "evidence",
            ("evidence", "quote"): "quote", ("evidence", "inputs"): "input", ("figure", "affects"): "affects",
            ("affects", "rows"): "affects_row", ("figure", "recalc"): "recalc", ("figure", "last_checked"): "last_checked",
            ("review", "bullets"): "bullet", ("bullet", "segments"): "segment"}


def schema_problems(review):
    """Every key in the output that is not in SCHEMA, as 'object.key'."""
    problems = []

    def walk(obj, kind, where):
        if isinstance(obj, list):
            for i, x in enumerate(obj):
                walk(x, kind, f"{where}[{i}]")
            return
        if not isinstance(obj, dict):
            return
        for k, v in obj.items():
            if k not in SCHEMA[kind]:
                problems.append(f"{where}.{k} (not allowed in {kind})")
                continue
            child = CHILDREN.get((kind, k))
            if child:
                walk(v, child, f"{where}.{k}")
            elif isinstance(v, dict) or (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
                problems.append(f"{where}.{k} (an object the schema does not describe)")
    walk(review, "review", "review")
    return problems


def to_json(review):
    """The file's text: the same inputs give the same bytes."""
    return json.dumps(review, ensure_ascii=False, indent=1) + "\n"


def _inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def print_notes(review, notes):
    c = review["counts"]
    print(f"{c['rows']} rows, {c['figures']} figures ({c['watched_figures']} watched, on {c['watched_rows']} rows).")
    print("States: " + ", ".join(f"{k} {v}" for k, v in c["states"].items()) + ".")
    print("Filters: " + ", ".join(f"{k} {v}" for k, v in c["filters"].items()) + f". Bullets: {len(review['bullets'])}.")
    print(f"Built (the newest input): {review['built']}")
    sections = [("Approved but not published", [f"{x['key']} ({x['figure'] or 'no line'}): {x['why']}" for x in notes["declined"]]),
                ("Published from an earlier approval (a later run found another figure)", [f"{x['figure']} ({x['key']})" for x in notes["earlier_approval"]]),
                ("Lines with two code names shown unchanged", [f"{x['figure']}: {x['why']}" for x in notes["two_code_names"]]),
                ("A difference found but not published (shown as 'last checked <date>')",
                 [f"{x['figure']}: {x['by']} on {x['date']}: {x['verdict']}" for x in notes["unpublished_differences"]]),
                ("Part A report lines with no Figures line (skipped)", notes["part_a_unmatched"]),
                ("Watched lines on research, model or calculation rows", [f"{r} ({t})" for r, t in notes["watched_research_or_calculation"]]),
                ("Model outputs with an empty 'Built from'", notes["model_outputs_without_built_from"]),
                ("Shown as 'Paper differs' by Seb's ruling (review/paper_differs.json)", notes["paper_differs"]),
                ("Rulings with no approved newer finding to apply to (no effect)", notes["paper_differs_unused"]),
                (f"Lines with no reader rule yet ({len(notes['no_reader_rule'])})", [", ".join(notes["no_reader_rule"])] if notes["no_reader_rule"] else [])]
    for title, items in sections:
        print(f"\n{title}: " + ("none" if not items else ""))
        for it in items:
            print(f"  - {it}")
    bad = {k: v for k, v in (notes.get("unreadable") or {}).items() if v}
    if bad:
        print(f"\nUnreadable input lines: {bad}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build review.json, the tabular review of every figure in the paper.")
    ap.add_argument("--out", help="the folder to write review.json in (default: a new folder in the system's temp folder)")
    args = ap.parse_args(argv)
    if args.out and _inside(args.out, AGENT):
        ap.error("--out is inside the Agent folder; give a folder outside it (for example your research-tool clone)")
    review, notes = build(**read_inputs(**default_paths(AGENT)))
    problems = schema_problems(review)
    if problems:
        raise SystemExit("Not written: keys outside the schema: " + "; ".join(problems))
    out = args.out or tempfile.mkdtemp(prefix="review-")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "review.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(to_json(review))
    print_notes(review, notes)
    print(f"\nWritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
