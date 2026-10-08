"""
The approvals spreadsheet: one row per item waiting for a decision, built from the open list, and reading your
decisions back from it.

Each row carries an ID (the open list's key and the proposed.jsonl lines it covers). Those line numbers never change,
because proposed.jsonl is only ever added to, so a decision always points at exactly the proposals you saw.
"""
import os
import re

import claims as R
import companies as C
from lib import checks as K
from lib import openlist as O
from lib.leads import fact_text, known_values, repeats

import xlsx as X

# the choices for each type of item, and what is recorded for each. "later" (first, so that a program that fills a
# drop-down with its first choice fills in nothing) means the same as leaving it empty.
LATER = "later"
FIGURE_CHOICES = (LATER, "approve", "keep paper", "reader wrong")
CHOICES = {"Figure update": FIGURE_CHOICES, "Part A figure": FIGURE_CHOICES, "Arithmetic": FIGURE_CHOICES,
           "No longer found": (LATER, "dismiss"), "To do": (LATER, "done"), "Lead": (LATER, "use", "dismiss"), "Old item": (LATER, "dismiss")}
RECORDED = {"approve": "approved", "keep paper": "kept paper", "reader wrong": "reader wrong", "use": "use",
            "dismiss": "dismissed", "done": "done"}
SHORT = {"a": "approve", "approved": "approve", "k": "keep paper", "keep": "keep paper", "kept paper": "keep paper",
         "w": "reader wrong", "wrong": "reader wrong", "u": "use", "used": "use", "d": "dismiss", "dismissed": "dismiss"}
KIND_OF_TYPE = {"Figure update": ("figure",), "Part A figure": ("Part A figure",), "Arithmetic": ("arithmetic",),
                "No longer found": ("figure", "arithmetic"), "To do": ("figure", "arithmetic", "Part A figure", "new fact"),
                "Lead": ("new fact",), "Old item": ("first version",)}
TYPE_OF_KIND = {"figure": "Figure update", "Part A figure": "Part A figure", "arithmetic": "Arithmetic", "new fact": "Lead"}

COLUMNS = ["#", "Decision", "Note", "Type", "Row", "Company", "What", "Paper", "Paper's date", "Found", "Found's date",
           "Source", "Quote", "Seen", "Paper section", "Reader's note", "ID (do not edit)"]
WIDTHS = [5, 14, 26, 14, 7, 13, 34, 14, 12, 14, 12, 24, 70, 20, 20, 44, 22]
STYLES = ["top", "decision", "decision", "top", "top", "wrap", "wrap", "wrap", "wrap", "wrap", "wrap", "link", "wrap", "wrap", "wrap", "wrap", "small"]
ID_COL = "ID (do not edit)"
MADE = "Sheet made (do not edit): "


# ------------------------------------------------------------------------------------------
# Items: what is waiting for a decision
# ------------------------------------------------------------------------------------------
def _fmt(v, unit=""):
    if v is None or v == "":
        return ""
    s = K.fmt(v) if isinstance(v, (int, float)) or re.fullmatch(r"-?[\d,]*\.?\d+", str(v)) else str(v)
    return f"{s} {unit}".strip() if unit and unit not in s else s


def _date(end, kind=None):
    return f"{end} ({kind})" if end and kind else (end or "")


def _day(s):
    return (s or "")[:10]


def _seen(e):
    n = len(e["runs"])
    return f"{n} run{'s' if n > 1 else ''}: first {_day(e['first_seen'])}" + (f", last {_day(e['last_seen'])}" if n > 1 else "")


def _quote(p, limit=2, width=320):
    frs = p.get("fragments") or ([{"role": "value", "text": p["quote"]}] if p.get("quote") else [])
    frs = sorted(frs, key=lambda f: f.get("role") != "value")          # the fragment with the figure first
    out = []
    for f in frs[:limit]:
        t = re.sub(r"\s+", " ", str(f.get("text", ""))).strip()
        out.append(f"{f.get('role', 'value')}: “{t[:width]}{'…' if len(t) > width else ''}”")
    return "\n".join(out)


def _component_label(row, key):
    for c in (R.CLAIMS.get(row) or {}).get("components", []):
        if c.get("key") == key:
            return c.get("label") or key
    for f in (R.CLAIMS.get(row) or {}).get("formulas", []):
        if f.get("name") == key:
            return f.get("label") or key
    return key or ""


def _decision_note(d):
    if not d:
        return ""
    v, pe = d.get("figure") or [None, None]
    fig = f" for {_fmt(v)} ({pe})" if v is not None else ""
    who = "The AI gate" if O.by_gate(d) else "You"
    return f"{who} decided '{d['decision']}' on {_day(d.get('decided_at'))}{fig}; a later run proposes a different figure."


def _to_do_note(e):
    """A to-do row's note: who decided it, and what to do next."""
    d = e["to_do"]["decision"]
    if O.by_gate(d):
        return (f"The AI gate approved this on {_day(d.get('decided_at'))}: mark it done once you have changed the paper. "
                f"To undo the gate's approval:  python3 Agent/approvals/approvals.py reopen {e['row']} --gate")
    return f"You decided '{d['decision']}' on {_day(d.get('decided_at'))}: mark it done once you have changed the paper (or used the lead)."


def _item(kind_type, e, ledger, reader_note=""):
    """One row of the sheet from an open-list entry (for a to-do, the proposal you approved)."""
    approved = kind_type == "To do" and e.get("to_do")      # the proposal you decided on (a lead: the version the sheet showed)
    p = (e["to_do"]["proposal"] if approved else None) or e.get("show") or e["latest"]
    row = p.get("row") if e["kind"] != "new fact" else ", ".join(p.get("related_rows") or [])
    first_row = (p.get("related_rows") or [""])[0] if e["kind"] == "new fact" else p.get("row")
    it = dict(type=kind_type, key=e["key"], lines=sorted(e["lines"]), kind=e["kind"], proposal=p, entry=e,
              company=e["company"], row=row or "", section=(ledger.get(first_row) or {}).get("Paper section", "") or "",
              seen=_seen(e), what="", paper="", paper_date="", found="", found_date="", source=None, quote="", note="")
    notes = [reader_note] if reader_note else []
    if e["kind"] == "figure":
        it.update(what=_component_label(p.get("row"), p.get("component")), paper=_fmt(p.get("paper_value"), p.get("unit")),
                  paper_date=_date(p.get("paper_period_end"), p.get("paper_period_type")), found=_fmt(p.get("new_value"), p.get("unit")),
                  found_date=_date(p.get("new_period_end"), p.get("new_period_type")), source=X.Link(p.get("link"), p.get("document") or "document"),
                  quote=_quote(p))
        check = p.get("paper_check") or p.get("kind") or ""
        notes.append("" if check == "newer figure" else check)
        if len(e["history"]) > 1:
            notes.append("Earlier runs proposed: " + "; ".join(f"{_fmt(v)} ({pe}) on {_day(w)}" for w, v, pe in e["history"][:-1]))
    elif e["kind"] == "Part A figure":
        it.update(what=p.get("figure") or "", paper=_fmt(p.get("paper_value"), p.get("unit")), paper_date=p.get("paper_period_end") or "",
                  found=_fmt(p.get("new_value"), p.get("unit")), found_date=p.get("new_period_end") or "",
                  source=X.Link(p.get("filing"), f"{p.get('form')} filed {p.get('filed')}"), quote="From the SEC's labelled data (no quote).")
        notes += ["the filings give a different figure for the same period" if p.get("status") == "DIFFERS FROM FILINGS" else "",
                  p.get("note") or ""]
    elif e["kind"] == "arithmetic":
        cos = {c.get("company") for c in (R.CLAIMS.get(p.get("row")) or {}).get("components", [])}
        it["company"] = it["company"] or (cos.pop() if len(cos) == 1 else "")
        ins = "; ".join(f"{k} = {_fmt(v)} ({(p.get('input_sources') or {}).get(k, '')})" for k, v in (p.get("inputs") or {}).items())
        it.update(what=_component_label(p.get("row"), p.get("component")), paper=_fmt(p.get("paper_value"), p.get("unit")),
                  found=_fmt(p.get("new_value"), p.get("unit")), quote=f"Formula: {p.get('formula')}\nInputs: {ins}")
    else:                                                                  # a lead
        it.update(what=p.get("topic") or "", source=X.Link(p.get("link"), p.get("document") or "document"), quote=_quote(p))
        if p.get("why"):
            notes.append(f"AI's comment (not checked): {p['why']}")
        reps = e.get("repeats") if p is e.get("show") else repeats(fact_text(p), known_values(e["company"]))[0]   # of the words quoted
        if reps:
            notes.append("Repeats the paper's " + ", ".join(f"{s} ({r})" for s, r in reps) + "; the rest is new")
    if kind_type != "To do":
        notes.append(_decision_note(e.get("earlier_decision")))
    if e.get("gone"):
        notes.append(f"Checked again on {_day(e['gone']['run'])}: {e['gone']['verdict']}")
    it["note"] = "\n".join(n for n in notes if n)
    return it


def _old_items(earlier, ledger):
    """The first version's proposals (26 Sep, before the checks were fixed): figures grouped as the open list shows them."""
    key_of = lambda q: (q.get("row"), O.company_of(q), q.get("status"), str(q.get("new_value")))
    groups = {}
    for p in earlier:
        groups.setdefault(key_of(p) if p.get("row") != "NEW FACT" else ("fact", p["_line"]), []).append(p)
    out = []
    for ps in groups.values():
        p = ps[0]
        fact = p.get("row") == "NEW FACT"
        out.append(dict(type="Old item", key=f"v1:{'fact' if fact else p.get('row')}", lines=sorted(q["_line"] for q in ps),
                        kind="first version", proposal=p, entry=None, company=O.company_of(p),
                        row=", ".join(p.get("related_rows") or []) if fact else p.get("row") or "",
                        section=(ledger.get(p.get("row")) or {}).get("Paper section", "") or "",
                        seen="first version (26 Sep)",
                        what=(p.get("topic") if fact else f"{(p.get('status') or '').lower()}: paper {_fmt(p.get('paper_value'))} → {_fmt(p.get('new_value'))}") or "",
                        paper="" if fact else _fmt(p.get("paper_value")), paper_date="" if fact else str(p.get("paper_period") or ""),
                        found="" if fact else _fmt(p.get("new_value")), found_date="" if fact else str(p.get("new_period") or ""),
                        source=X.Link(p.get("link"), p.get("document") or "document"), quote=_quote(p),
                        note="From the first version of Part B, before its checks were fixed." + (f"\n{p['note']}" if p.get("note") else "")))
    return out


def items(result, ledger):
    """Everything waiting for a decision, in the order of the sheet."""
    op = result["open"]
    by_kind = lambda k: [e for e in op if e["kind"] == k]
    out = [_item("Figure update", e, ledger) for e in sorted(by_kind("figure"), key=lambda e: (e["row"] or "", e["component"] or ""))]
    out += [_item("Part A figure", e, ledger) for e in sorted(by_kind("Part A figure"), key=lambda e: e["row"] or "")]
    out += [_item("Arithmetic", e, ledger) for e in sorted(by_kind("arithmetic"), key=lambda e: e["row"] or "")]
    out += [_item("No longer found", e, ledger) for e in sorted(result["gone"], key=lambda e: (e["row"] or "", e["component"] or ""))]
    everything = op + result["gone"] + result["closed"] + result.get("aside", [])
    todo = [e for e in everything if e.get("to_do")]
    out += [_item("To do", e, ledger, reader_note=_to_do_note(e))
            for e in sorted(todo, key=lambda e: (e["kind"] == "new fact", e["row"] or "", e["component"] or ""))]
    leads = sorted(by_kind("new fact"), key=lambda e: (O.name_of(e["company"]), K.source_rank((e.get("show") or e["latest"]).get("document")),
                                                       e["first_seen"]))
    out += [_item("Lead", e, ledger) for e in leads]
    out += _old_items(result["earlier"], ledger)
    for it in out:
        it["id"] = f"{'todo:' if it['type'] == 'To do' else ''}{it['key']}#{'.'.join(map(str, it['lines']))}"
    return out


# ------------------------------------------------------------------------------------------
# Writing the sheet
# ------------------------------------------------------------------------------------------
def write(path, its, made_at, counts_line, filled=None):
    """The sheet. `filled` ({id: (decision, note)}) puts decisions in already (the tests use it)."""
    rows = [COLUMNS]
    filled = filled or {}
    for n, it in enumerate(its, 1):
        dec, note = filled.get(it["id"], (None, None))
        rows.append([n, dec, note, it["type"], it["row"], C.NAME.get(it["company"], it["company"]), it["what"], it["paper"],
                     it["paper_date"], it["found"], it["found_date"], it["source"], it["quote"], it["seen"], it["section"],
                     it["note"], it["id"]])
    validations, start = [], 2
    for i in range(1, len(its) + 1):                       # one drop-down list per run of rows of the same type
        if i == len(its) or its[i]["type"] != its[i - 1]["type"]:
            validations.append((1, 1, start, i + 1, CHOICES[its[i - 1]["type"]]))
            start = i + 2
    decide = X.Sheet("Decide", rows, WIDTHS, STYLES, validations=validations, freeze=(3, 1))
    X.write(path, [decide, X.Sheet("How to use", how_to_use(made_at, counts_line), [110], ["wrap"], header=False,
                                    row_styles={1: "title"})])


def how_to_use(made_at, counts_line):
    L = [["Approvals: how to use this sheet"], [f"Made {made_at:%d %b %Y %H:%M} from the open list. {counts_line}"],
         [MADE + made_at.isoformat()], [""],
         ["1. On the Decide sheet, fill in the yellow Decision column for the items you want to decide now. Leave it empty "
          "(or choose 'later') to decide later: the item stays open and comes back on the next sheet."],
         ["2. The choices (there is a drop-down list in each cell):"]]
    L += [[f"     {t}: {', '.join(c)}"] for t, c in CHOICES.items()]
    L += [["     approve = the paper should change to the new figure (it goes on 'Changes to make.md'). keep paper = the new "
           "figure is real but the paper is right as it stands. reader wrong = the proposal is a mistake. use = a lead worth "
           "using (it goes on 'Leads to use.md'). dismiss = close it. done = you have changed the paper (or used the lead)."],
          ["3. Note (optional): why. For 'reader wrong', say what it got wrong: it goes on 'Reader mistakes.md' to be fixed."],
          ["4. Do not edit the ID column. You can sort and filter; only Decision, Note and ID are read back."],
          ["5. Save it as .xlsx in the same folder (in Numbers: File > Export To > Excel)."],
          ["6. In the terminal, from the project folder:  python3 Agent/approvals/approvals.py check   shows what will be "
           "recorded and writes nothing. Then:  python3 Agent/approvals/approvals.py record"],
          ["7. Changed your mind?  python3 Agent/approvals/approvals.py reopen F046   opens a row's decided items again. "
           "To undo only the AI gate's approvals of a row:  python3 Agent/approvals/approvals.py reopen F046 --gate"],
          [""], ["Nothing here changes the paper, the models or the website. Your decisions are added to "
                 "Agent/reader/decisions.jsonl; earlier lines are never changed."]]
    return L


# ------------------------------------------------------------------------------------------
# Reading your decisions back
# ------------------------------------------------------------------------------------------
def made_at(book):
    """When the sheet was made (from its How to use sheet), or None."""
    for rows in book.values():
        for row in rows:
            for v in row:
                if isinstance(v, str) and v.startswith(MADE):
                    return v[len(MADE):].strip()
    return None


def read(path, read_workbook, book=None, every_row=False):
    """The rows with something in Decision or Note (every_row: all the rows): [(sheet row, id, decision as typed, note,
    type)]. Finds the table by its header, on whichever sheet it is (Numbers may rename sheets or add a title row)."""
    book = book or read_workbook(path)
    for rows in book.values():
        for h, header in enumerate(rows):
            names = [str(x).strip() if x is not None else "" for x in header]
            if "Decision" in names and ID_COL in names:
                col = {n: i for i, n in enumerate(names) if n}
                out = []
                for r, row in enumerate(rows[h + 1:], h + 2):
                    get = lambda n: row[col[n]] if n in col and col[n] < len(row) and row[col[n]] is not None else ""
                    dec, note, ident = str(get("Decision")).strip(), str(get("Note")).strip(), str(get(ID_COL)).strip()
                    if dec or note or (every_row and ident):
                        out.append(dict(sheet_row=r, id=ident, typed=dec, note=note, type=str(get("Type")).strip()))
                return out
    raise ValueError(f"no table with a 'Decision' and an '{ID_COL}' column in {os.path.basename(path)}")


def parse_id(ident):
    """'B:F141:CRWV_te#109.151' -> ('B:F141:CRWV_te', [109, 151], False); a to-do's ID starts 'todo:' (True)."""
    key, sep, nums = ident.rpartition("#")
    todo = key.startswith("todo:")
    key = key[len("todo:"):] if todo else key
    try:
        lines = [int(n) for n in nums.split(".")]
    except ValueError:
        lines = []
    if not sep or not key or not lines:
        raise ValueError("the ID is not one the sheet made")
    return key, lines, todo


def choice(typed):
    """The choice as typed ('a', 'Approve', 'dismissed'...) -> its full name, or None."""
    t = re.sub(r"\s+", " ", typed.strip().lower())
    t = SHORT.get(t, t)
    return t if t in RECORDED or t == LATER else None
