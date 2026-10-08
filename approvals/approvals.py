#!/usr/bin/env python3
"""
Step 4: your decisions on what the reader proposes, made in a spreadsheet.

Run from the project folder, in Antigravity's terminal:

    python3 Agent/approvals/approvals.py make           a spreadsheet of everything waiting for a decision
    python3 Agent/approvals/approvals.py check          what your filled-in sheet would record (writes nothing)
    python3 Agent/approvals/approvals.py record         records your decisions
    python3 Agent/approvals/approvals.py reopen F046    opens a row's decided items again
    python3 Agent/approvals/approvals.py reopen F046 --gate    opens again only the items the AI gate approved

check and record read the newest sheet in Agent/approvals/sheets/ (or give one: --sheet "path/to/file.xlsx").
Your decisions always win over the AI gate's (approvals/gate.py): a row the gate decided after the sheet was made is
still recorded, and your later line is the one in force.

Nothing here changes the paper, the models or the website. Decisions are added to the end of
Agent/reader/decisions.jsonl (earlier lines are never changed, and proposed.jsonl is never touched); the open list is
then rebuilt, and three lists are written here: Changes to make.md, Leads to use.md and Reader mistakes.md.
Standard library only: nothing to install.
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
READER = os.path.join(AGENT, "reader")
sys.path[:0] = [HERE, READER, os.path.join(AGENT, "watcher")]
import watcher as W  # noqa: E402  the watch list reader
import companies as C  # noqa: E402
import claims as R  # noqa: E402
from lib import openlist as O  # noqa: E402

import sheet as SH  # noqa: E402
import suggest as SG  # noqa: E402
import xlsx as X  # noqa: E402


def now_local():
    return dt.datetime.now().replace(microsecond=0)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read_ledger():
    """The ledger rows of the watch list (for each row's paper section); empty if the workbook cannot be read."""
    cfg = W.load_json(W.CONFIG_PATH, {})
    path = os.path.normpath(os.path.join(W.HERE, cfg.get("watch_list", "../Watch list - The AI Lose Lose Race.xlsx")))
    try:
        return {r["ID"]: r for r in W.table(W.read_workbook(path).get("Ledger", []))}
    except (OSError, KeyError, ValueError) as e:
        print(f"(Could not read the watch list, so paper sections are left blank: {e})")
        return {}


PLURAL = {"Figure update": "figure updates", "Part A figure": "Part A figures", "Arithmetic": "arithmetic",
          "No longer found": "no longer found", "To do": "to do", "Lead": "leads", "Old item": "old items"}


def counts_line(its):
    n = {}
    for it in its:
        n[it["type"]] = n.get(it["type"], 0) + 1
    return f"{len(its)} items: " + ", ".join(f"{v} {PLURAL[k] if v > 1 else k.lower() if k != 'Part A figure' else k}"
                                             for k, v in n.items()) + "."


# ------------------------------------------------------------------------------------------
# make
# ------------------------------------------------------------------------------------------
def make(reader_dir, out_dir, ledger, now):
    result, _, _ = O.load(reader_dir)
    its = SH.items(result, ledger)
    os.makedirs(os.path.join(out_dir, "sheets"), exist_ok=True)
    path = os.path.join(out_dir, "sheets", f"Approvals {now:%Y-%m-%d %H.%M}.xlsx")
    if os.path.exists(path):
        sys.exit(f"{os.path.relpath(path)} already exists (a sheet was made this minute): nothing written.")
    SH.write(path, its, now, counts_line(its))
    print(f"Made {os.path.relpath(path)}\n{counts_line(its)}\nOpen it, fill in the Decision column, save it, then run check and record.")
    return path


# ------------------------------------------------------------------------------------------
# check and record
# ------------------------------------------------------------------------------------------
def newest_sheet(out_dir):
    found = [p for p in glob.glob(os.path.join(out_dir, "sheets", "*.xlsx")) if not os.path.basename(p).startswith("~$")]
    if not found:
        sys.exit("No sheet in Agent/approvals/sheets/: run  python3 Agent/approvals/approvals.py make  first.")
    return max(found, key=os.path.getmtime)


def _by_line(result):
    """proposed.jsonl line number -> (entry or None, its group, the first-version proposal if it is one)."""
    out = {}
    for group in ("open", "gone", "closed", "aside"):
        for e in result[group]:
            for n in e["lines"]:
                out[n] = (e, group, None)
    for group in ("earlier", "earlier_decided"):
        for p in result.get(group) or []:
            out[p["_line"]] = (None, group, p)
    return out


def _type_for(entry, group, v1_line, todo):
    if v1_line is not None:
        return "Old item"
    if todo:
        return "To do"
    if group == "gone":
        return "No longer found"
    return SH.TYPE_OF_KIND.get(entry["kind"], "Figure update")


def _append(path, records):
    """Add lines to the end of a .jsonl file, starting on a new line even if the last one lost its line ending."""
    with open(path, "a+", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(f.tell() - 1)
            if f.read(1) != "\n":
                f.write("\n")
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def _all_decisions(nums, decided):
    """Every decision ever made about these lines (reopened ones included), in order."""
    return sorted({d["_n"]: d for n in nums for d in decided.get(n, [])}.values(), key=lambda d: d["_n"])


def items_when_made(reader_dir, made, ledger):
    """The sheet's items as it showed them, by ID: the open list rebuilt from the proposals, runs and decisions there
    were when it was made. A run since then that found an item again has added a line to it, so its ID has changed;
    its row is looked up here, and is recorded with the label (and scored with the suggestion) the sheet showed."""
    then = lambda when: (when or "") <= made
    lines, _ = O.read_lines(os.path.join(reader_dir, "proposed.jsonl"))
    result = O.build([p for p in lines if then(p.get("found_at"))],
                     [r for r in O.read_runs(os.path.join(reader_dir, "runs.jsonl")) if then(r.get("found_at"))],
                     [d for d in O.read_decisions(os.path.join(reader_dir, "decisions.jsonl")) if then(d.get("decided_at"))])
    return {it["id"]: it for it in SH.items(result, ledger)}


def plan(sheet_path, reader_dir, ledger):
    """What the sheet would record. Returns (decisions to add, problems, notes, rows already recorded). A problem
    anywhere means nothing is recorded."""
    result, lines, _ = O.load(reader_dir)
    errors, warnings, already, todo = [], [], [], []
    if result["bad_decisions"]:
        errors.append(f"decisions.jsonl has lines that cannot be read ({', '.join(map(str, result['bad_decisions']))}): "
                      "nothing is recorded until they are repaired (tell Claude)")
    decided = O._decisions_by_line(O.read_decisions(os.path.join(reader_dir, "decisions.jsonl")))
    by_line, line_of = _by_line(result), {p["_line"]: p for p in lines}
    for e in result["open"] + result["gone"] + result["closed"] + result["aside"]:
        for n in e.get("earlier_version") or []:        # a first-version lead a later run found again
            by_line.setdefault(n, (None, "earlier", line_of.get(n)))
    its = {it["id"]: it for it in SH.items(result, ledger)}
    its_then = None                                         # the items as the sheet showed them, built if needed
    book = X.read(sheet_path)
    made = SH.made_at(book)
    if made is None:
        errors.append("this sheet does not say when it was made (its 'How to use' sheet is missing or changed): make a new sheet")
    seen = {}
    for r in SH.read(sheet_path, X.read, book):
        where = f"row {r['sheet_row']}"
        if not r["typed"]:
            warnings.append(f"{where}: a note but no decision, so nothing is recorded for it")
            continue
        ch = SH.choice(r["typed"])
        if ch == SH.LATER:
            continue
        try:
            key, nums, is_todo = SH.parse_id(r["id"])
        except ValueError as e:
            errors.append(f"{where}: {e} ({r['id'] or 'empty'})")
            continue
        if ch is None:
            errors.append(f"{where}: '{r['typed']}' is not a choice")
            continue
        if any(n not in line_of or n not in by_line for n in nums):
            errors.append(f"{where}: the ID points at proposals that are not in proposed.jsonl ({r['id']})")
            continue
        entry, group, v1 = by_line[nums[0]]
        owners = {id(by_line[n][0]) if by_line[n][0] is not None else "v1" for n in nums}
        regrouped = len(owners) > 1 and all(by_line[n][0] is not None and by_line[n][0]["kind"] == "new fact" for n in nums)
        if regrouped:
            # a lead whose lines are now in two entries (the rules for merging leads changed after the sheet was made):
            # checked against the entry holding the version the sheet showed, where its decision holds
            entry, group, v1 = by_line[O._shown([line_of[n] for n in nums])["_line"]]
            owners = {id(entry)}
        if len(owners) > 1 or (entry is not None and key != entry["key"] and not (key.startswith("fact:") and entry["kind"] == "new fact"
                                                                                  and key.split(":")[1] == entry["company"])) \
                or (entry is None and not key.startswith("v1:")):
            errors.append(f"{where}: the ID does not match the proposals it names (was it edited?) ({r['id']})")
            continue
        kind = "first version" if v1 is not None else entry["kind"]
        typ = _type_for(entry, group, v1, is_todo)
        if is_todo and not (entry or {}).get("to_do"):
            warnings.append(f"{where}: nothing is waiting to be done for this item any more; skipped")
            continue
        if ch not in SH.CHOICES[typ]:
            errors.append(f"{where}: '{ch}' is not a choice for a {typ.lower()} (choose: {', '.join(SH.CHOICES[typ][1:])})")
            continue
        if tuple(nums) + (is_todo,) in seen:
            first_where, first_ch, first_note = seen[tuple(nums) + (is_todo,)]
            if (first_ch, first_note) != (ch, r["note"]):
                errors.append(f"{where}: the same item as {first_where}, with a different decision")
            continue
        seen[tuple(nums) + (is_todo,)] = (where, ch, r["note"])
        rec = SH.RECORDED[ch]
        if typ == "To do" and made and (entry["to_do"]["decision"].get("decided_at") or "") > made:
            td = entry["to_do"]["decision"]
            who = "the AI gate" if O.by_gate(td) else "you"
            warnings.append(f"{where}: {who} approved a newer figure after this sheet was made ({td.get('decided_at', '')[:16]}); "
                            "this row is skipped, so 'done' cannot close a change you have not seen. Make a new sheet")
            continue
        if typ == "To do":
            figure = entry["to_do"]["decision"].get("figure")
        else:
            latest = max((line_of[n] for n in nums), key=lambda p: (p.get("found_at") or "", p["_line"]))
            figure = O.figure_of(latest) if kind in O.FIGURE_KINDS else None
        active = O.active_decisions(nums, decided)
        prev = active[-1] if active else None
        if prev and not O.by_gate(prev) and prev["decision"] == rec and (prev.get("note") or "") == r["note"] and prev.get("figure") == figure:
            already.append(f"{where}: {r['id']}")
            continue
        if regrouped:
            errors.append(f"{where}: this lead has been regrouped since the sheet was made (the rules for merging leads "
                          f"changed), so it may now be two facts: make a new sheet to decide it ({r['id']})")
            continue
        # a decision made after the sheet wins over it, unless it is the AI gate's: Seb's decision always wins
        later = [d for d in _all_decisions(nums, decided) if made and (d.get("decided_at") or "") > made and not O.by_gate(d)]
        if later:
            d = later[-1]
            warnings.append(f"{where}: decided again after this sheet was made ('{d['decision']}' on {d.get('decided_at', '')[:16]}); "
                            "this row is skipped. Make a new sheet to change it")
            continue
        if entry is not None and typ != "To do" and kind in O.FIGURE_KINDS and not O.same_figure(O.figure_of(entry["latest"]), figure):
            now_fig = O.figure_of(entry["latest"])
            warnings.append(f"{where} ({entry['row']} {entry['component']}): a later run proposes {now_fig[0]} ({now_fig[1]}); "
                            f"your '{ch}' is recorded for {figure[0]} ({figure[1]}), so the item stays open")
        it = its.get(r["id"])
        if it is None and made:
            its_then = items_when_made(reader_dir, made, ledger) if its_then is None else its_then
            it = its_then.get(r["id"])
        it = it or dict(type=typ, proposal=(entry.get("show") or entry["latest"]) if entry else v1, what="")
        sug, why = SG.suggest(dict(it, type=typ))
        todo.append(dict(decision=rec, note=r["note"], key=key, lines=nums, figure=figure, kind=kind, type=typ,
                         row="" if kind == "new fact" or (v1 or {}).get("row") == "NEW FACT" else (entry or {}).get("row") or (v1 or {}).get("row") or "",
                         company=(entry or {}).get("company") or O.company_of(v1 or {}),
                         what=it.get("what") or "", suggested=SH.RECORDED.get(sug) if sug else None,
                         suggested_why=why, sheet=os.path.basename(sheet_path), _where=where))
    return todo, errors, warnings, already


def check(sheet_path, reader_dir, ledger, write=False, out_dir=None, now=None):
    todo, errors, warnings, already = plan(sheet_path, reader_dir, ledger)
    print(f"Sheet: {os.path.relpath(sheet_path)}")
    for d in todo:
        print(f"  {d['_where']:>8}  {d['decision']:<13} {_label(d)}" + (f"  ({d['note']})" if d["note"] else ""))
    for e in errors:
        print(f"  PROBLEM  {e}")
    for w in warnings:
        print(f"  note     {w}")
    n = {}
    for d in todo:
        n[d["decision"]] = n.get(d["decision"], 0) + 1
    print(f"{len(todo)} decisions to record" + (": " + ", ".join(f"{v} {k}" for k, v in n.items()) if n else "")
          + (f"; {len(already)} already recorded (skipped)" if already else "") + ".")
    if errors:
        print(f"Nothing recorded: fix the {len(errors)} problem{'s' if len(errors) > 1 else ''} above, save the sheet, and run it again.")
        return 1
    if not write:
        print("Nothing written (this was a check). To record them:  python3 Agent/approvals/approvals.py record")
        return 0
    if not todo:
        print("Nothing new to record.")
        return 0
    path = os.path.join(reader_dir, "decisions.jsonl")
    _append(path, [dict({k: v for k, v in d.items() if not k.startswith("_")}, decided_at=now.isoformat()) for d in todo])
    result = O.rebuild(reader_dir, quiet=True)
    write_lists(result, out_dir, ledger, now)
    os.makedirs(os.path.join(out_dir, "decided"), exist_ok=True)
    rpath = os.path.join(out_dir, "decided", f"Decided {now:%Y-%m-%d %H.%M.%S}.md")
    _write(rpath, decided_report(todo, warnings, now))
    print(f"Recorded {len(todo)} decisions in {os.path.relpath(path)}. The open list now has {len(result['open'])} open items.")
    print(score_line(todo))
    print(f"Written: {os.path.relpath(rpath)}, and Changes to make.md, Leads to use.md, Reader mistakes.md in {os.path.relpath(out_dir)}")
    return 0


# ------------------------------------------------------------------------------------------
# The lists written after recording
# ------------------------------------------------------------------------------------------
def score(todo):
    made = [d for d in todo if d["suggested"]]
    agree = [d for d in made if d["suggested"] == d["decision"]]
    return made, agree


def score_line(todo):
    made, agree = score(todo)
    if not made:
        return "The tool made no suggestions for these."
    return (f"The tool's own suggestions (not shown on the sheet) agreed with you on {len(agree)} of the {len(made)} it made"
            f"; it made none for {len(todo) - len(made)}.")


def _label(d):
    """'Figure update F141 CoreWeave: CoreWeave gross technology equipment'"""
    return " ".join(x for x in (d["type"], d["row"], C.NAME.get(d["company"], d["company"]) + ":") if x) + f" {d['what']}"


def decided_report(todo, warnings, now):
    L = [f"# Decided {now:%d %b %Y %H:%M}", "", f"{len(todo)} decisions recorded from `{todo[0]['sheet']}`.", ""]
    for rec in ("approved", "kept paper", "reader wrong", "use", "dismissed", "done"):
        ds = [d for d in todo if d["decision"] == rec]
        if ds:
            L += [f"## {O.DECIDED[rec].capitalize()} ({len(ds)})", ""]
            L += [f"- {_label(d)}" + (f". Note: {d['note']}" if d["note"] else "") for d in ds]
            L.append("")
    if warnings:
        L += ["## Notes", ""] + [f"- {w}" for w in warnings] + [""]
    made, agree = score(todo)
    L += ["## The tool's suggestions against your decisions", "", score_line(todo), ""]
    diff = [d for d in made if d["suggested"] != d["decision"]]
    if diff:
        L += ["Where it disagreed with you (these tune the rules in suggest.py):", ""]
        L += [f"- {_label(d)}: you said **{d['decision']}**, it suggested "
              f"{d['suggested']} ({d['suggested_why']})" + (f". Your note: {d['note']}" if d["note"] else "") for d in diff]
    return "\n".join(L) + "\n"


def _model_of(row):
    return (R.CLAIMS.get(row) or {}).get("model") or ""


def write_lists(result, out_dir, ledger, now):
    """Changes to make.md, Leads to use.md and Reader mistakes.md, from the decisions as they now stand."""
    everything = result["open"] + result["gone"] + result["closed"] + result["aside"]
    head = lambda title, text: [f"# {title} ({now:%d %b %Y %H:%M})", "", text, ""]
    link = lambda src: f" · [{src.text}]({src.url})" if src and src.url else ""
    # changes to make: what you approved and have not yet marked done
    ch = [e for e in everything if (e.get("to_do") or {}).get("decision", {}).get("decision") == "approved"]
    gate = " Those the AI gate approved say so." if any(O.by_gate(e["to_do"]["decision"]) for e in ch) else ""
    L = head("Changes to make", "Figures you approved. Nothing has been changed: this is your list for the paper and the models. "
             "Mark each one done on the next sheet once you have made the change." + gate)
    by_sec = {}
    for e in ch:
        it = SH._item("To do", e, ledger)
        by_sec.setdefault(it["section"] or "(no paper section in the ledger)", []).append((e, it))
    for sec in sorted(by_sec):
        L += [f"## {sec}", ""]
        for e, it in sorted(by_sec[sec], key=lambda x: x[0]["row"] or ""):
            d = e["to_do"]["decision"]
            L.append(f"- **{e['row']}** {C.NAME.get(e['company'], e['company'])}: {it['what']}: paper {it['paper']}, {it['paper_date'] or 'no date'} → "
                     f"**{it['found']}**, {it['found_date'] or 'no date'}" + link(it["source"]))
            if _model_of(e["row"]):
                L.append(f"    - Model: {_model_of(e['row'])}")
            L += [f"    - {q}" for q in it["quote"].split("\n") if q]
            if O.by_gate(d):
                L.append(f"    - Approved by the AI gate {d.get('decided_at', '')[:10]} (to undo: approvals.py reopen {e['row']} --gate)")
            else:
                L.append(f"    - Approved {d.get('decided_at', '')[:10]}" + (f": {d['note']}" if d.get("note") else ""))
            if e.get("decision_made") is None and any(x is e for x in result["open"]):
                L.append("    - A later run has proposed a different figure since: it is on the sheet again")
        L.append("")
    if not ch:
        L.append("None.")
    _write(os.path.join(out_dir, "Changes to make.md"), "\n".join(L) + "\n")
    # leads to use
    use = [e for e in everything if (e.get("to_do") or {}).get("decision", {}).get("decision") == "use"]
    L = head("Leads to use", "Leads (new facts) you marked to use. Each is quoted word for word from the document linked.")
    for e in sorted(use, key=lambda e: (O.name_of(e["company"]), e["first_seen"])):
        it, d = SH._item("To do", e, ledger), e["to_do"]["decision"]
        L.append(f"- **{it['what']}** · {O.name_of(e['company'])}" + (f" · rows {it['row']}" if it["row"] else "") + link(it["source"]))
        L += [f"    - {q}" for q in it["quote"].split("\n") if q]
        if d.get("note"):
            L.append(f"    - Your note: {d['note']}")
    if not use:
        L.append("None.")
    _write(os.path.join(out_dir, "Leads to use.md"), "\n".join(L) + "\n")
    # reader mistakes: every "reader wrong" still in force, whatever was decided later
    L = head("Reader mistakes", "Proposals you marked as the reader's mistake, with your notes: the checks to fix.")
    n = 0
    for e in everything:
        for d in e.get("decisions") or []:
            if d["decision"] == "reader wrong":
                it = SH._item("Figure update" if e["kind"] == "figure" else SH.TYPE_OF_KIND.get(e["kind"], "Figure update"), e, ledger)
                v, pe = d.get("figure") or [None, None]
                L.append(f"- **{e['row']}** {C.NAME.get(e['company'], e['company'])}: {it['what']}: paper {it['paper']} → proposed "
                         f"{SH._fmt(v, (it['proposal'] or {}).get('unit'))}, {pe} · marked {d.get('decided_at', '')[:10]}"
                         + (f" · {d['note']}" if d.get("note") else ""))
                n += 1
    for p in result.get("earlier_decided") or []:
        if p["decision_made"]["decision"] == "reader wrong":
            L.append(f"- line {p['_line']} (first version) {p.get('row')}" + (f" · {p['decision_made'].get('note')}" if p["decision_made"].get("note") else ""))
            n += 1
    if not n:
        L.append("None.")
    _write(os.path.join(out_dir, "Reader mistakes.md"), "\n".join(L) + "\n")


# ------------------------------------------------------------------------------------------
# reopen
# ------------------------------------------------------------------------------------------
def reopen(what, reader_dir, now, yes=False, ask=input, gate=False):
    """Open a row's decided items again (or a lead's, by words from its topic). gate=True: only the items whose decision
    in force is the AI gate's, and only the gate's decision is taken back; the gate never decides them again."""
    result, _, _ = O.load(reader_dir)
    if result["bad_decisions"]:
        print(f"decisions.jsonl has lines that cannot be read ({', '.join(map(str, result['bad_decisions']))}): nothing changed (tell Claude).")
        return 1
    w = what.strip().lower()
    named = lambda e: ((e["row"] or "").lower() == w
                       or w in ((e.get("decided_on") or e.get("show") or e["latest"]).get("topic") or "").lower())
    gates = lambda e: [d for d in e.get("decisions") or [] if O.by_gate(d) and d["decision"] != "done"]
    if gate:        # the gate's decisions in force, wherever they are: closed, or held while a later figure is open
        hits = [e for g in ("closed", "open", "gone", "aside") for e in result[g] if named(e) and gates(e)]
    else:
        hits = [e for e in result["closed"] if e.get("decision_made") and named(e)]
    old = [p for p in result.get("earlier_decided") or [] if (p.get("row") or "").lower() == w] if not gate else []
    if not hits and not old:
        print(f"No item the AI gate decided matches '{what}' (give a ledger row such as F061)." if gate else
              f"No decided item matches '{what}' (give a ledger row such as F046, or words from a lead's topic).")
        return 1
    for e in hits:
        p = e.get("decided_on") or e.get("show") or e["latest"]
        if gate:
            what = "; ".join(f"{d['decision']} {(d.get('figure') or [''])[0]} ({(d.get('figure') or ['', ''])[1]}) by the AI gate" for d in gates(e))
        else:
            what = e["decision_made"]["decision"] + (" (by the AI gate)" if O.by_gate(e["decision_made"]) else "")
        print(f"  {e['row'] or ''} {O.name_of(e['company'])}: {p.get('topic') or e['component']}: {what}")
    for p in old:
        print(f"  line {p['_line']} (first version) {p.get('row')}: {p['decision_made']['decision']}")
    if not yes and ask(f"Open these {len(hits) + len(old)} again? (y/n) ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 0
    only = dict(only=O.GATE) if gate else {}
    _append(os.path.join(reader_dir, "decisions.jsonl"),
            [dict(decision="reopen", key=e["key"], lines=sorted(e["lines"]), decided_at=now.isoformat(), **only) for e in hits]
            + [dict(decision="reopen", key="v1", lines=[p["_line"]], decided_at=now.isoformat()) for p in old])
    result = O.rebuild(reader_dir, quiet=True)
    print(("The AI gate's approvals are taken back; it will not decide these figures again. " if gate else "Opened again. ")
          + f"The open list now has {len(result['open'])} open items; they will be on the next sheet.")
    return 0


# ------------------------------------------------------------------------------------------
def main(argv=None, reader_dir=READER, out_dir=HERE, now=None, ledger=None):
    ap = argparse.ArgumentParser(description="Step 4: decide on the reader's proposals in a spreadsheet.")
    ap.add_argument("action", choices=["make", "check", "record", "reopen"])
    ap.add_argument("what", nargs="?", help="for reopen: a ledger row (F046) or words from a lead's topic")
    ap.add_argument("--sheet", help="the filled-in sheet (default: the newest in Agent/approvals/sheets/)")
    ap.add_argument("--yes", action="store_true", help="for reopen: do not ask to confirm")
    ap.add_argument("--gate", action="store_true", help="for reopen: take back only the AI gate's approvals of the row")
    args = ap.parse_args(argv)
    now = now or now_local()
    ledger = read_ledger() if ledger is None else ledger
    if args.gate and args.action != "reopen":
        ap.error("--gate goes with reopen, such as: reopen F061 --gate")
    if args.action == "make":
        make(reader_dir, out_dir, ledger, now)
        return 0
    if args.action == "reopen":
        if not args.what:
            ap.error("reopen needs a ledger row, such as: reopen F046")
        return reopen(args.what, reader_dir, now, args.yes, gate=args.gate)
    sheet_path = args.sheet or newest_sheet(out_dir)
    return check(sheet_path, reader_dir, ledger, write=args.action == "record", out_dir=out_dir, now=now)


if __name__ == "__main__":
    sys.exit(main())
