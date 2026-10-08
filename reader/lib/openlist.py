"""
The open list: the proposals still waiting for a decision, with repeats merged.

Every run of the reader adds its proposals to the end of proposed.jsonl, and that file is never changed, so a finding
that is still true appears once per run. This reads proposed.jsonl (it never writes to it) and rebuilds two files next
to it from scratch:

  Open proposals.md      to read: one entry per finding, with the first and last run that found it and how many runs did
  proposals_open.jsonl   the same for the approval step (step 4): one line per entry, listing the proposed.jsonl lines it merges

How proposals are merged:
  * a figure (Part B): one entry per ledger row and component (e.g. F141 CRWV_te). The latest run's finding is shown;
    if an earlier run proposed a different value, that is listed as well.
  * arithmetic (Part B): one entry per row and formula.
  * Part A (the SEC's labelled figures): one entry per row and figure.
  * new facts: one entry per fact. Two leads of one company are the same fact when they quote the same passage, or
    state the same specific amount: three or more significant digits, such as $105 billion or 24,896, the one rounding
    to the other (35,802 million is $35.8 billion; 66,594 million is not $66.9 billion). In one document a round number
    ($100.0 billion) does not count, and two numbers in common do (leads.same_lead). A lead joins an entry only if it is
    the same fact as every lead in it, so a lead quoting two facts cannot join them.
  * proposals from the first version of Part B (26 Sep 2026, before its checks were fixed) are listed on their own and
    not merged with later findings; a later lead that is the same fact only notes that the first version found it too.
A figure that a later run checked again without proposing it (runs.jsonl records what each run checked) is moved to
"no longer found".

Your decisions (step 4, Agent/approvals) are in decisions.jsonl next to proposed.jsonl: one line per decision, added
to the end, never changed, and read in that order. Each names the proposed.jsonl lines it decides. A decided entry is
closed and stays closed when later runs find the same figure again (words compared ignoring case, spacing and 'the',
'a', 'an'); if a later run finds a different figure (or a later date), the entry opens again, showing your earlier
decision. A decided lead stays closed however often it is found again, and is listed in the words you decided on. A
"reopen" line undoes the latest decision; a "reopen" line marked "only": "AI gate" undoes every decision of the AI
gate's still in force (approvals/gate.py writes its approvals with "decided_by": "AI gate"; your own lines have no "decided_by").
A decision of yours replaces the gate's made before it on the same item. What you approved (or chose to use) stays on your to-do list until a "done" line, whatever is decided about later
figures.

Part B (reader_text.py) rebuilds it at the end of every run. After a Part A run, rebuild it by hand:

    python3 Agent/reader/open_proposals.py
"""

import datetime as dt
import json
import os
import re

import companies as C

from . import checks as K
from .leads import fact_figures, fact_text, known_values, lead_problem, repeats, same_lead

READER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSED = ("approved", "rejected", "done", "applied", "dismissed")
DECIDED = {"approved": "approved", "kept paper": "kept the paper", "reader wrong": "reader wrong", "use": "use",
           "dismissed": "dismissed", "done": "done"}                # decisions.jsonl's decisions, as the lists show them
FIGURE_KINDS = ("figure", "arithmetic", "Part A figure")
AGREES = ("matches", "newer, unchanged", "no change")      # a later run's verdict that means the proposal no longer applies
GATE = "AI gate"                                           # "decided_by" of the AI gate's decisions (approvals/gate.py)


def by_gate(d):
    """Whether a decision is the AI gate's (Seb's own decisions have no "decided_by")."""
    return (d or {}).get("decided_by") == GATE


def name_of(ticker):
    """A company's name for the list."""
    return C.NAME.get(ticker) or ticker or "company not recorded"


# ------------------------------------------------------------------------------------------
# Reading proposed.jsonl and runs.jsonl (never written here)
# ------------------------------------------------------------------------------------------
def read_lines(path):
    """Every proposal in proposed.jsonl, with its line number. Unreadable lines are reported, not dropped silently."""
    out, bad = [], []
    if not os.path.exists(path):
        return out, bad
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                bad.append(n)
                continue
            if isinstance(d, dict):
                d["_line"] = n
                out.append(d)
    return out, bad


def read_decisions(path, bad=None):
    """Your decisions (decisions.jsonl), in the order they were made. The numbers of unreadable lines are added to `bad`."""
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    ok = isinstance(d, dict) and d.get("decision") and isinstance(d.get("lines"), list)
                except json.JSONDecodeError:
                    ok = False
                if not ok:
                    if bad is not None:
                        bad.append(n)
                    continue
                d["_n"] = n
                out.append(d)
    return out


def figure_of(p):
    """What a proposal proposes: its value and the date it is for."""
    return [p.get("new_value"), p.get("new_period_end") or p.get("new_period")]


def same_figure(a, b):
    """The same value (numbers within rounding, words ignoring case, spacing and 'the', 'a', 'an') for the same date.
    The AI words a text figure a little differently from run to run ('the second quarter of fiscal 2027' against
    'second quarter of fiscal 2027'): that is the same figure, so it must not reopen a decision."""
    (va, pa), (vb, pb) = (a or [None, None]), (b or [None, None])
    if pa != pb:
        return False
    try:
        x, y = float(va), float(vb)
        return abs(x - y) <= 1e-9 * max(1.0, abs(x), abs(y))
    except (TypeError, ValueError):
        return _words(va) == _words(vb)


def _words(v):
    """A text figure as same_figure compares it: lower case, plain spacing, without the words 'the', 'a' and 'an'."""
    return " ".join(w for w in K.norm(str(v)).lower().split() if w not in ("the", "a", "an"))


def read_runs(path):
    """What each run checked (runs.jsonl)."""
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def company_of(p):
    """The company a proposal is about (older lines did not record it: read from its link)."""
    if p.get("company"):
        return p["company"]
    link = p.get("link") or p.get("filing") or ""
    m = re.search(r"/edgar/data/(\d+)/", link)
    if m and int(m.group(1)) in C.TICKER_BY_CIK:
        return C.TICKER_BY_CIK[int(m.group(1))]
    t = f"{link} {p.get('document') or ''}".lower()
    for tick, words in (("NVDA", ("nvidia", "nvda")), ("MSFT", ("microsoft.com", "msft")), ("META", ("meta-q", "/meta", "meta platforms")),
                        ("AMZN", ("amazon", "amzn")), ("GOOGL", ("alphabet", "goog")), ("ORCL", ("oracle", "orcl")),
                        ("CRWV", ("coreweave", "crwv")), ("NBIS", ("nebius", "nbis"))):
        if any(w in t for w in words):
            return tick
    return ""


def version(p):
    """Which reader made the line: 'A' (Part A), 'B2' (Part B since 28 Sep) or 'B1' (Part B's first version)."""
    return "A" if p.get("part") == "A" else ("B2" if p.get("version") == 2 else "B1")


def points_at(key, p):
    """Whether a decision's key names this proposal's own row and component (or figure, or company for a lead)."""
    kind, _, rest = (key or "").partition(":")
    if kind == "B":
        row, _, comp = rest.partition(":")
        formula = comp.endswith(":formula")
        comp = comp[:-len(":formula")] if formula else comp
        return (version(p) == "B2" and p.get("row") == row and p.get("component") == comp
                and (p.get("kind") == "formula with newer inputs") == formula)
    if kind == "A":
        row, _, figure = rest.partition(":")
        return version(p) == "A" and p.get("row") == row and p.get("figure") == figure
    if kind == "fact":
        return version(p) == "B2" and p.get("row") == "NEW FACT" and company_of(p) == rest.split(":")[0]
    if kind == "v1":
        return version(p) == "B1"
    return False


def pointer_problems(lines, decisions):
    """Every decision's lines must be proposals of the decision's own row and component: one line per pointer that is
    not. Two copies of proposed.jsonl merged by hand could make a decision point at another finding's lines."""
    at = {p["_line"]: p for p in lines}
    out = []
    for d in decisions:
        for n in d["lines"]:
            p = at.get(n)
            if p is None:
                out.append(f"decisions.jsonl line {d['_n']} ({d.get('key')}): proposed.jsonl has no line {n}")
            elif not points_at(d.get("key"), p):
                what = p.get("figure") if p.get("part") == "A" else p.get("component") or p.get("topic") or ""
                out.append(f"decisions.jsonl line {d['_n']} ({d.get('key')}): proposed.jsonl line {n} is {p.get('row')} {what}")
    return out


def _ts(p):
    try:
        return dt.datetime.fromisoformat(p.get("found_at") or "").timestamp()
    except ValueError:
        return 0


def _when(s):
    try:
        return dt.datetime.fromisoformat(s).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError):
        return s or "?"


def _new(key, kind, p):
    return dict(key=key, kind=kind, company=company_of(p), row=p.get("row"), component=p.get("component") or p.get("figure") or "",
                first_seen=p.get("found_at") or "", last_seen="", runs=[], lines=[], history=[], latest=None, earlier_version=[])


def _add(e, p):
    when = p.get("found_at") or ""
    if when not in e["runs"]:
        e["runs"].append(when)
    e["lines"].append(p["_line"])
    e["first_seen"] = min(e["first_seen"] or when, when)
    if when >= (e["last_seen"] or ""):
        e["last_seen"], e["latest"] = when, p
    val = (p.get("new_value"), p.get("new_period_end") or p.get("new_period"))
    same = lambda a, b: (re.sub(r"^(the|a|an) ", "", K.norm(a)) == re.sub(r"^(the|a|an) ", "", K.norm(b))) if isinstance(a, str) else a == b
    if not any(same(val[0], h[1]) and val[1] == h[2] for h in e["history"]):
        e["history"].append((when, val[0], val[1]))


# ------------------------------------------------------------------------------------------
# Merging
# ------------------------------------------------------------------------------------------
def build(lines, runs=(), decisions=()):
    """Merge the proposals and apply your decisions. Returns dict(open, gone, closed, aside, earlier, earlier_decided):
    lists of entries (earlier and earlier_decided: raw lines from the first version)."""
    entries, facts, v1 = _merge(lines)
    earlier = _match_first_version(v1, facts)
    all_entries = list(entries.values()) + facts
    _mark_no_longer_found(all_entries, runs)
    _mark_set_aside(facts)
    by_line = _decisions_by_line(decisions)
    line_of = {p["_line"]: p for p in lines}
    _apply_decisions(all_entries, by_line, line_of)
    out = dict(open=[], gone=[], closed=[], aside=[], earlier=[], earlier_decided=[])
    for p in earlier:
        active = active_decisions([p["_line"]], by_line)
        d = closing_decision(active)
        if d is not None:
            p["decision_made"] = d
        out["earlier_decided" if d is not None else "earlier"].append(p)
    for e in all_entries:
        dec = ((e["latest"] or {}).get("decision") or "pending").lower()
        closed = e.get("decision_made") is not None or dec in CLOSED
        group = "closed" if closed else "gone" if e.get("gone") else "aside" if e.get("set_aside") else "open"
        out[group].append(e)
    for e in facts:
        e["figures"] = [[v, s] for v, s in e.pop("_figs", [])]
    return out


def _decisions_by_line(decisions):
    by_line = {}
    for d in decisions:
        for n in d["lines"]:
            by_line.setdefault(n, []).append(d)
    return by_line


def _without_gate(active):
    """The decisions in force with every one of the AI gate's taken back (each with any "done" after it)."""
    kept, after_gate = [], False
    for x in active:
        if x["decision"] == "done":
            if not after_gate:
                kept.append(x)
            continue
        after_gate = by_gate(x)
        if not after_gate:
            kept.append(x)
    return kept


def active_decisions(line_numbers, by_line, keep=None):
    """The decisions about any of these proposed.jsonl lines still in force, in the order made: a "reopen" takes back
    the latest decision (with any "done" after it). A "reopen" marked "only": "AI gate" takes back every decision of
    the gate's still in force (each with any "done" after it); Seb's decisions stay. A decision of Seb's replaces the
    gate's made before it in the same way, so a later reopen of his opens the item and brings none of them back.
    keep(decision) can leave some out."""
    ds = sorted({d["_n"]: d for n in line_numbers for d in by_line.get(n, [])}.values(), key=lambda d: d["_n"])
    ds = [d for d in ds if keep is None or keep(d)]
    active = []
    for d in ds:
        if d["decision"] == "reopen" and d.get("only") == GATE:
            active = _without_gate(active)
        elif d["decision"] == "reopen":
            last = max((i for i, x in enumerate(active) if x["decision"] != "done"), default=None)
            if last is not None:
                del active[last:]
        elif d["decision"] != "done" and not by_gate(d):
            active = _without_gate(active) + [d]
        else:
            active.append(d)
    return active


def closing_decision(active):
    """The decision that says whether an entry is closed: the latest one, leaving out "done" (which is about your
    to-do list, not about the figure)."""
    closing = [d for d in active if d["decision"] != "done"]
    return closing[-1] if closing else None


def _decided_on(d, kind, line_of):
    """The proposal a decision was made on: for a figure, the latest of its lines; for a lead, the version the sheet
    showed (a later run may find the lead again in other words, and that version is shown while it is open)."""
    ps = [line_of[n] for n in d["lines"] if n in line_of]
    if not ps:
        return None
    return _shown(ps) if kind == "new fact" else max(ps, key=lambda p: (p.get("found_at") or "", p["_line"]))


def _to_do(active, line_of, kind):
    """What you approved or chose to use and have not yet marked done: the decision, and the proposal it approved."""
    todo = None
    for d in active:
        if d["decision"] in ("approved", "use"):
            todo = d
        elif d["decision"] == "done":
            todo = None
    if todo is None:
        return None
    return dict(decision=todo, proposal=_decided_on(todo, kind, line_of))


def _apply_decisions(all_entries, by_line, line_of):
    """A decision closes its entry. For a figure it holds only while the latest run proposes the same figure; a
    different one opens the entry again, with the earlier decision shown. A decided lead is listed in the words you
    decided on. A lead's decision holds where that version is: when the rules for merging leads change, a decision made
    on lines that are now in two entries stays with the one you saw, and the other is open ("done" and "reopen" act on
    whatever is decided where they touch)."""
    for e in all_entries:
        keep = None
        if e["kind"] == "new fact":
            keep = lambda d, e=e: d["decision"] in ("done", "reopen") or (_decided_on(d, "new fact", line_of) or {}).get("_line") in e["lines"]
        active = active_decisions(e["lines"], by_line, keep)
        e["decisions"] = active
        e["to_do"] = _to_do(active, line_of, e["kind"])
        d = closing_decision(active)
        if d is None:
            continue
        if e["kind"] in FIGURE_KINDS and not same_figure(figure_of(e["latest"]), d.get("figure")):
            e["earlier_decision"] = d
        else:
            e["decision_made"] = d
            if e["kind"] == "new fact":
                e["decided_on"] = _decided_on(d, e["kind"], line_of)


def _merge(lines):
    """One entry per finding: figures by row and component, formulas by row and name, Part A by row and figure, new
    facts by fact. Lines from the first version of Part B are kept apart."""
    entries, facts, v1 = {}, [], []
    for p in sorted(lines, key=lambda d: (d.get("found_at") or "", d["_line"])):
        ver = version(p)
        if ver == "B1":
            v1.append(p)
            continue
        if p.get("row") == "NEW FACT":
            _merge_fact(facts, p)
            continue
        if ver == "A":
            key, kind = f"A:{p.get('row')}:{p.get('figure') or ''}", "Part A figure"
        elif p.get("kind") == "formula with newer inputs":
            key, kind = f"B:{p.get('row')}:{p.get('component')}:formula", "arithmetic"
        else:
            key, kind = f"B:{p.get('row')}:{p.get('component')}", "figure"
        e = entries.get(key)
        if e is None:
            e = entries[key] = _new(key, kind, p)
        _add(e, p)
    return entries, facts, v1


def _merge_fact(facts, p):
    """A lead joins an entry only if it is the same fact as every lead already in it, so a lead that quotes two facts
    cannot join them; otherwise it starts an entry."""
    co = company_of(p)
    e = next((e for e in facts if e["company"] == co and all(same_lead(m, p) for m in e["_members"])), None)
    if e is None:
        e = _new(f"fact:{co}:{len(facts) + 1}", "new fact", p)
        e.update(_figs=[], _members=[])
        facts.append(e)
    e["_members"].append(p)
    _add(e, p)
    if e.get("show") is None or _show_key(p) <= _show_key(e["show"]):
        e["show"] = p                     # show the filing's version of a fact reported in several documents
    e["_figs"] += [f for f in fact_figures(fact_text(p)) if not any(K.same_amount(f, g) for g in e["_figs"])]


def _show_key(p):
    """Which version of a lead is shown: the best document (a filing before a release before a call), then the latest."""
    return K.source_rank(p.get("document")), -_ts(p)


def _shown(ps):
    """The version _merge_fact shows for these lines (on a tie, the later line, as there). For a decided lead, given the
    lines you decided, it is the version the sheet showed you."""
    return min(ps, key=lambda p: (_show_key(p), -p["_line"]))


def _match_first_version(v1, facts):
    """The first version's leads that are the same fact as a later one are noted on it; the rest are listed apart."""
    earlier = []
    for p in v1:
        if p.get("row") == "NEW FACT":
            co = company_of(p)          # the same fact as any of the entry's leads: it is only noted there, never merged
            e = next((e for e in facts if e["company"] == co and any(same_lead(m, p) for m in e["_members"])), None)
            if e is not None:
                e["earlier_version"].append(p["_line"])
                e["first_seen"] = min(e["first_seen"], p.get("found_at") or e["first_seen"])
                continue
        earlier.append(p)
    return earlier


def _mark_no_longer_found(all_entries, runs):
    """A figure or formula that a later run checked and found agreeing with the paper. Only such a verdict counts:
    'no update found' or an extraction problem does not."""
    for e in all_entries:
        if e["kind"] not in ("figure", "arithmetic"):
            continue
        item = f"{e['row']}:{e['component']}"
        later = [r for r in runs if (r.get("found_at") or "") > e["last_seen"]
                 and (r.get("items") or {}).get(item) in AGREES]
        if later:
            r = max(later, key=lambda r: r.get("found_at") or "")
            e["gone"] = dict(run=r.get("found_at"), verdict=r["items"][item])


def _mark_set_aside(facts):
    """The same rules as a new report: boilerplate, no figure, or only the paper's own figures."""
    for e in facts:
        text = fact_text(e["show"])
        why = lead_problem(e["show"].get("topic") or "", text)
        if not why:
            hits, all_rep = repeats(text, known_values(e["company"]))
            e["repeats"] = hits
            if all_rep:
                why = "already in the paper (" + ", ".join(f"{s} {row}" for s, row in hits) + ")" if hits else "already in the paper"
        if why:
            e["set_aside"] = why


# ------------------------------------------------------------------------------------------
# Writing Open proposals.md and proposals_open.jsonl
# ------------------------------------------------------------------------------------------
def _quote_lines(p, limit=4, width=320):
    out = []
    frs = p.get("fragments") or ([{"role": "value", "text": p["quote"]}] if p.get("quote") else [])
    for f in frs[:limit]:
        t = re.sub(r"\s+", " ", f.get("text", "")).strip()
        out.append(f"    - {f.get('role', 'value')}: “{t[:width]}{'…' if len(t) > width else ''}”")
    return out


def _seen(e):
    n = len(e["runs"])
    s = f"first seen {_when(e['first_seen'])}"
    if n > 1:
        s += f", last seen {_when(e['last_seen'])}, found by {n} runs"
    if e.get("earlier_version"):
        s += f"; the first version (26 Sep) found it too (line{'s' if len(e['earlier_version']) > 1 else ''} {', '.join(map(str, e['earlier_version']))})"
    return s


def _fmt(v):
    return K.fmt(v) if isinstance(v, (int, float)) else str(v)


def entry_lines(e):
    """One entry of the open list, as lines of Markdown."""
    p = e.get("show") or e["latest"]
    co = name_of(e["company"])
    if e["kind"] == "new fact":
        rows = f" (rows {', '.join(p.get('related_rows') or [])})" if p.get("related_rows") else ""
        out = [f"- **{p.get('topic') or 'lead'}**{rows} · {co} · [{p.get('document')}]({p.get('link')}) · {_seen(e)}"]
        out += _quote_lines(p)
        if e.get("repeats"):
            out.append("    - Repeats the paper's " + ", ".join(f"{s} ({row})" for s, row in e["repeats"]) + "; the rest is new")
        if p.get("why"):
            out.append(f"    - Why it matters (AI's comment): {p['why']}")
        return out
    if e["kind"] == "Part A figure":
        out = [f"- **{p.get('row')}** {p.get('figure')}: paper {_fmt(p.get('paper_value'))} ({p.get('paper_period_end')}) → "
               f"**{_fmt(p.get('new_value'))} {p.get('unit') or ''}** ({p.get('new_period_end')}) · {p.get('status')} · "
               f"[{p.get('form')} filed {p.get('filed')}]({p.get('filing')}) · {_seen(e)}"]
        if p.get("note"):
            out.append(f"    - Note: {p['note']}")
        return out + _earlier_decision_line(e)
    if e["kind"] == "arithmetic":
        ins = "; ".join(f"{k} = {_fmt(v)} ({(p.get('input_sources') or {}).get(k, '')})" for k, v in (p.get("inputs") or {}).items())
        return [f"- **{p.get('row')}** {p.get('component')}: `{p.get('formula')}` · paper {p.get('paper_value')} → "
                f"**{_fmt(p.get('new_value'))} {p.get('unit') or ''}** · {_seen(e)}", f"    - Inputs: {ins}"] + _earlier_decision_line(e)
    out = [f"- **{p.get('row')}** {co} {p.get('component')}: paper {_fmt(p.get('paper_value'))} {p.get('unit') or ''} "
           f"({p.get('paper_period_type')}, {p.get('paper_period_end')}) → **{_fmt(p.get('new_value'))}** "
           f"({p.get('new_period_type')}, {p.get('new_period_end')}) · {p.get('paper_check') or p.get('kind')} · "
           f"[{p.get('document')}]({p.get('link')}) · {_seen(e)}"]
    if len(e["history"]) > 1:
        out.append("    - Earlier runs proposed: " + "; ".join(f"{_fmt(v)} ({pe}) on {_when(w)}" for w, v, pe in e["history"][:-1]))
    out += _earlier_decision_line(e)
    out += _quote_lines(p)
    return out


def _earlier_decision_line(e):
    d = e.get("earlier_decision")
    if not d:
        return []
    v, pe = d.get("figure") or [None, None]
    who = "The AI gate" if by_gate(d) else "You"
    return [f"    - {who} decided {_decision_words(d, who=False)} for {_fmt(v)} ({pe}); a later run proposes a different figure"]


def write(result, md_path, jsonl_path, source, bad_lines=(), now=None):
    """Write Open proposals.md and proposals_open.jsonl."""
    _write_atomic(md_path, markdown(result, source, bad_lines, now or dt.datetime.now()))
    _write_atomic(jsonl_path, "".join(json.dumps(d, ensure_ascii=False, default=str) + "\n" for d in jsonl_records(result)))


def _write_atomic(path, text):
    """Write to a temporary file, then swap it in: a reader never sees a half-written list."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def markdown(result, source, bad_lines, now):
    """The text of Open proposals.md."""
    op, gone, earlier, aside = result["open"], result["gone"], result["earlier"], result.get("aside") or []
    figs = sorted([e for e in op if e["kind"] == "figure"], key=lambda e: (e["row"] or "", e["component"] or ""))
    arith = sorted([e for e in op if e["kind"] == "arithmetic"], key=lambda e: e["row"] or "")
    part_a = sorted([e for e in op if e["kind"] == "Part A figure"], key=lambda e: e["row"] or "")
    facts = [e for e in op if e["kind"] == "new fact"]
    everything = op + gone + result["closed"] + aside
    earlier_decided = result.get("earlier_decided") or []
    n_lines = (sum(len(e["lines"]) for e in everything) + len(earlier) + len(earlier_decided)
               + sum(len(e.get("earlier_version") or []) for e in everything))
    L = [f"# Open proposals ({now:%d %b %Y %H:%M})", "",
         f"Built from `{os.path.basename(source)}` ({n_lines} proposals, never changed). Repeats of the same finding across runs "
         + ("are merged into one entry; nothing has been approved or applied. The approval step (step 4) will work from this list."
            if not any(e.get("decision_made") for e in result["closed"]) and not earlier_decided else
            "are merged into one entry. Your decisions (step 4, decisions.jsonl) are applied: decided items are listed at the end."), "",
         f"**{len(op)} open**: {len(figs)} figure updates, {len(arith)} arithmetic, {len(part_a)} Part A figures, {len(facts)} new-fact leads"
         + (f" · {len(gone)} no longer found" if gone else "") + (f" · {len(result['closed'])} decided" if result["closed"] else "")
         + (f" · {len(aside)} leads set aside" if aside else "")
         + (f" · {len(earlier)} from the first version, listed separately" if earlier else "")
         + (f" · {len(earlier_decided)} from the first version decided" if earlier_decided else ""), ""]
    if bad_lines:
        L += [f"Lines that could not be read in {os.path.basename(source)}: {', '.join(map(str, bad_lines))}.", ""]
    if result.get("bad_decisions"):
        L += [f"Lines that could not be read in decisions.jsonl (not applied): {', '.join(map(str, result['bad_decisions']))}.", ""]
    L += ["## 1. Figure updates (Part B)", ""] + ([x for e in figs for x in entry_lines(e)] or ["None."])
    L += ["", "## 2. Arithmetic with newer inputs (Part B)", ""] + ([x for e in arith for x in entry_lines(e)] or ["None."])
    L += ["", "## 3. The SEC's labelled figures (Part A)", ""] + ([x for e in part_a for x in entry_lines(e)] or ["None."])
    L += _facts_section(facts)
    n = 5                                                 # the optional sections are numbered in order
    if gone:
        L += _gone_section(n, gone)
        n += 1
    if aside:
        L += _aside_section(n, aside)
        n += 1
    if earlier:
        L += _first_version_section(n, earlier, {e["row"] for e in figs})
        n += 1
    decided = [e for e in result["closed"] if e.get("decision_made")]
    if decided or earlier_decided:
        L += _decided_section(n, decided, earlier_decided)
    return "\n".join(L) + "\n"


def _decision_words(d, who=True):
    """'approved on 30 Sep 2026 15:13 (note)'; the gate's decisions add 'by the AI gate' (unless who=False)."""
    note = f" ({d['note']})" if d.get("note") else ""
    gate = " by the AI gate" if who and by_gate(d) else ""
    return f"{DECIDED.get(d['decision'], d['decision'])}{gate} on {_when(d.get('decided_at'))}{note}"


def _decided_section(n, decided, earlier_decided):
    L = ["", f"## {n}. Decided", "", "Your decisions (decisions.jsonl). A decided figure opens again if a later run finds a "
         "different figure.", ""]
    for e in sorted(decided, key=lambda e: (e["kind"], name_of(e["company"]), e["row"] or "")):
        p = e.get("decided_on") or e.get("show") or e["latest"]
        what = p.get("topic") if e["kind"] == "new fact" else f"{e['row']} {e['component']}"
        L.append(f"- {name_of(e['company'])}: {what}: {_decision_words(e['decision_made'])}")
    for p in earlier_decided:
        L.append(f"- line {p['_line']} (first version): {p.get('row')} {name_of(company_of(p))}: {_decision_words(p['decision_made'])}")
    return L


def _facts_section(facts):
    L = ["", "## 4. New facts (leads, not updates)", "",
         "Grouped by company; filings first, then results releases, then calls (management's words: check against a filing "
         "before relying on them).", ""]
    if not facts:
        L.append("None.")
    by_co = {}
    for e in facts:
        by_co.setdefault(e["company"], []).append(e)
    for co in sorted(by_co, key=name_of):
        L += [f"### {name_of(co)} ({len(by_co[co])})", ""]
        for e in sorted(by_co[co], key=lambda e: (K.source_rank(e["show"].get("document")), e["first_seen"])):
            L += entry_lines(e)
        L.append("")
    return L


def _gone_section(n, gone):
    L = ["", f"## {n}. No longer found", "", "A later run checked these again and did not propose them. They are kept here "
         "until you close them.", ""]
    for e in gone:
        L += entry_lines(e)
        L.append(f"    - Checked again on {_when(e['gone']['run'])}: {e['gone']['verdict']}")
    return L


def _aside_section(n, aside):
    L = ["", f"## {n}. Leads set aside", "", "Verified word for word, but boilerplate (definitions of non-GAAP measures, where to "
         "find a reconciliation), without any figure (commentary, product news), or repeating only figures the paper already "
         "has. One line each; nothing is deleted.", ""]
    for e in sorted(aside, key=lambda e: (name_of(e["company"]), e["set_aside"])):
        p = e.get("show") or e["latest"]
        L.append(f"- {name_of(e['company'])}: {p.get('topic')} · {p.get('document')} · {e['set_aside']} · {_seen(e)}")
    return L


def _first_version_section(n, earlier, open_rows):
    L = ["", f"## {n}. From the first version of Part B (26 Sep 2026), not merged", "",
         "These came from the checks before they were fixed (the F152 false alerts, for example). Later runs re-checked "
         "every row below with the fixed checks; an open entry for the same row in section 1 is noted. Leads the later runs "
         "also found are merged into section 4 instead.", ""]
    key_of = lambda q: (q.get("row"), company_of(q), q.get("status"), str(q.get("new_value")))
    shown = set()
    for p in earlier:
        if p.get("row") == "NEW FACT" or key_of(p) in shown:
            continue
        shown.add(key_of(p))
        same = [q["_line"] for q in earlier if key_of(q) == key_of(p)]
        also = "open entry in section 1 for this row" if p.get("row") in open_rows else "no open entry for this row from the later runs"
        L.append(f"- line{'s' if len(same) > 1 else ''} {', '.join(map(str, same))}: **{p.get('row')}** {name_of(company_of(p))}: "
                 f"{p.get('status')}: paper {_fmt(p.get('paper_value'))} → {_fmt(p.get('new_value'))} · {p.get('document')} · {also}")
    lf = [p for p in earlier if p.get("row") == "NEW FACT"]
    if lf:
        L += ["", f"Leads from the first version that no later run found ({len(lf)}):", ""]
        for p in lf:
            L.append(f"- line {p['_line']}: {name_of(company_of(p))}: {p.get('topic')} · {p.get('document')}")
    return L


def jsonl_records(result):
    """One record per entry, for the approval step: its status, the proposed.jsonl lines it merges, its history."""
    out = []
    for status, group in (("open", result["open"]), ("no longer found", result["gone"]), ("set aside", result.get("aside") or []),
                          ("decided", result["closed"])):
        for e in group:
            d = {k: v for k, v in e.items() if k not in ("latest", "history", "show", "decision_made", "earlier_decision", "decisions", "to_do",
                                                         "decided_on") and not k.startswith("_")}
            for k in ("decision_made", "earlier_decision"):
                if e.get(k):
                    d[k] = {x: y for x, y in e[k].items() if not x.startswith("_")}
            if e.get("to_do"):
                d["to_do"] = {x: y for x, y in e["to_do"]["decision"].items() if not x.startswith("_")}
            d.update(status=status, times_seen=len(e["runs"]), latest=e["latest"],
                     history=[dict(found_at=w, new_value=v, new_period=pe) for w, v, pe in e["history"]])
            d["latest"] = {k: v for k, v in (e["latest"] or {}).items() if k != "_line"}
            if e.get("show") is not None and e["show"] is not e["latest"]:
                d["shown"] = {k: v for k, v in e["show"].items() if k != "_line"}
            out.append(d)
    for status, group in (("first version, not merged", result["earlier"]), ("first version, decided", result.get("earlier_decided") or [])):
        for p in group:
            out.append(dict(status=status, line=p["_line"], proposal={k: v for k, v in p.items() if k not in ("_line", "decision_made")},
                            **({"decision_made": {x: y for x, y in p["decision_made"].items() if not x.startswith("_")}} if p.get("decision_made") else {})))
    return out


# ------------------------------------------------------------------------------------------
# Entry points
# ------------------------------------------------------------------------------------------
def load(folder=READER):
    """The open list as it stands, built from folder's proposed.jsonl, runs.jsonl and decisions.jsonl (none written).
    Returns (result, proposal lines, unreadable line numbers of proposed.jsonl). result["bad_decisions"] lists the
    unreadable lines of decisions.jsonl."""
    lines, bad = read_lines(os.path.join(folder, "proposed.jsonl"))
    bad_d = []
    result = build(lines, read_runs(os.path.join(folder, "runs.jsonl")), read_decisions(os.path.join(folder, "decisions.jsonl"), bad_d))
    result["bad_decisions"] = bad_d
    return result, lines, bad


def rebuild(folder=READER, quiet=False):
    """Rebuild the open list from folder/proposed.jsonl (with folder/runs.jsonl and decisions.jsonl). Returns the merged result."""
    src = os.path.join(folder, "proposed.jsonl")
    result, lines, bad = load(folder)
    md, jl = os.path.join(folder, "Open proposals.md"), os.path.join(folder, "proposals_open.jsonl")
    write(result, md, jl, src, bad)
    if not quiet:
        print(f"{len(result['open'])} open proposals ({len(lines)} lines read); written to {os.path.relpath(md)} and {os.path.relpath(jl)}")
    return result


def open_facts(folder=READER):
    """The new-fact leads already on the open list, or already decided (for marking repeats in a new report)."""
    lines, _ = read_lines(os.path.join(folder, "proposed.jsonl"))
    result = build(lines, decisions=read_decisions(os.path.join(folder, "decisions.jsonl")))
    return [e for e in result["open"] + result["closed"] if e["kind"] == "new fact"]
