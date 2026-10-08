"""
The report of a Part B run (reports/Part B <date>.md) and the proposals it adds to proposed.jsonl.

The report has five parts: (1) figures that track the latest position, with the code's arithmetic; (2) absence claims;
(3) historical claims; (4) extraction problems (agent errors, which do not check the paper); (5) new facts (leads).
"""

import re

import claims as R
import companies as C

from . import checks as K
from .verdicts import sections, worked_with_new_inputs


# ------------------------------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------------------------------
def _doc_line(di):
    if di.get("period_end"):
        when = f"period to {K.show_date(di['period_end'])}"
    else:
        when = f"dated {K.show_date(di['date'])}" if di.get("date") else "undated"
    if di.get("fiscal_quarter"):
        pe = K.parse_date(di.get("period_end"))
        when = f"call of {K.show_date(di.get('date'))} on {di['fiscal_quarter']}" + (f", the quarter to {pe:%b %Y}" if pe else "")
    return f"[{di['title']}]({di['url']}) ({when})"


def _val(v, unit):
    if v is None:
        return "—"
    return f"{K.fmt(v)} {unit}".strip()


def _frag_lines(frags, indent="    "):
    out = []
    for i, f in enumerate(frags, 1):
        how = ("ignored: too short to check" if f.get("ignored") else
               ("verified, elsewhere in the document" if f.get("elsewhere") else "verified") if f.get("ok")
               else "NOT VERIFIED" if "ok" in f else "not checked: the answer was rejected before this fragment")
        if f.get("match") == "ignoring spaces":
            how += ", ignoring spacing"
        shown = re.sub(r"\s+", " ", f["text"].replace("\u200b", "")).strip()
        out.append(f"{indent}- Fragment {i} ({f['role']}, {how}): “{shown}”")
    return out


def _col_name(c):
    if c.get("kind") == "change":
        return f"{c['label']} (change{', for ' + c['of'] if c.get('of') else ''})"
    if c.get("day"):
        base = f"{c['day']:%d %b %Y}"
    else:
        end = K.month_end(*c["end"]) if c.get("end") else None
        base = f"{c['label']} (to {end:%b %Y})" if end else c["label"]
    return base + (f", {c['months']} months" if c.get("months") else "")


def _context_lines(r, indent="    "):
    t = r.get("table")
    if not t:
        return []
    if t.get("cols"):
        cols = "; ".join(_col_name(c) for c in t["cols"])
    else:
        cols = ", ".join(f"{d:%d %b %Y}" for d in t["dates"] if d) if t.get("dates") else "not read"
    return [f"{indent}- Table context read by the code: row “{t['label'][:70]}”; columns {cols}; "
            f"cells {', '.join(t['cells'][:8])}; units {t['units'] or 'not found'}"]


def _links(ledger, row_id, claim):
    r = ledger.get(row_id, {})
    bits = [f"paper: {r.get('Paper section') or '?'}", f"fn {r.get('Paper fn') or '—'}"]
    if r.get("Also used in"):
        bits.append(f"also used in: {r['Also used in']}")
    if claim.get("model"):
        bits.append(f"model: {claim['model']}")
    return " · ".join(bits)


# ------------------------------------------------------------------------------------------
# The report
# ------------------------------------------------------------------------------------------
def build_report(started, plan_line, verdicts, formulas, facts, set_aside, dropped, problems, ledger, jobs, lead_counts):
    """The report's text."""
    return _Report(started, verdicts, formulas, ledger).text(plan_line, facts, set_aside, dropped, problems, jobs, lead_counts)


class _Report:
    def __init__(self, started, verdicts, formulas, ledger):
        self.started, self.verdicts, self.formulas, self.ledger = started, verdicts, formulas, ledger
        self.claims = {v["row"]: R.claim_for(v["row"], ledger.get(v["row"])) for v in verdicts}
        self.sections = sections(verdicts)

    def text(self, plan_line, facts, set_aside, dropped, problems, jobs, lead_counts):
        L = self.header(plan_line)
        L += self.tracked() + self.arithmetic() + self.absence() + self.historical() + self.problems()
        L += self.new_facts(facts, set_aside, dropped, lead_counts)
        L += ["", "## Documents read", ""]
        for j in jobs:
            m = j["meta"]
            L.append(f"- {C.NAME[j['t']]} {j['kind']}: {_doc_line(m)}; {len(j['ask'])} items asked, {len(j['skip'])} not applicable")
        if problems:
            L += ["", "## Could not read", ""] + [f"- {p}" for p in problems]
        return "\n".join(L) + "\n"

    def header(self, plan_line):
        s = self.sections
        L = [f"# Reader Part B: text figures, {self.started.strftime('%d %b %Y %H:%M')}", "", plan_line, "",
             "Two separate checks for every figure. **Extraction**: did the agent read the document correctly (every fragment "
             "found word for word, the number in it, the right measure, the latest period; any arithmetic done by the code)? "
             "**Paper**: does the paper's figure agree? The paper is only compared with figures that passed the first check. "
             "Nothing has been changed in the paper, the models or the website.", ""]
        counts = [("Newer figure (proposed update)", "update"), ("Differs for the same period", "differs"), ("Text to compare", "text"),
                  ("Matches the paper", "matches"), ("No update found", "noupdate"), ("Absence claims", "absence"),
                  ("Historical claims", "historical"), ("With extraction problems", "problems")]
        L.append(" · ".join(f"{name}: {len(s[k])}" for name, k in counts if s[k]))
        return L

    # -- pieces used by several sections
    def head(self, v):
        c = v["comp"]
        links = _links(self.ledger, v["row"], self.claims[v["row"]])
        return f"- **{v['row']}** {C.NAME.get(v['company'], v['company'])}: {c['label']} · {links}"

    @staticmethod
    def paper_line(v):
        c = v["comp"]
        p = c["value"] if c.get("text") else _val(c["value"], c["unit"])
        return f"  - Paper: {p} ({c['period_type']}, {K.show_date(c['period_end'])})"

    @staticmethod
    def found_lines(v, r):
        c = v["comp"]
        out = []
        if c.get("text"):
            out.append(f"  - Document: the value fragment below says it (AI's reading: “{r.get('value_text', '')}”) · "
                       f"extraction verified · paper: {r.get('paper')}")
        else:
            got = _val(r.get("value"), c["unit"])
            if r.get("derived"):
                d = r["derived"]
                got = (f"{K.fmt(d['x'])} {c['unit']} for {r.get('period_type')} to {K.show_date(r.get('period_end'))}; "
                       f"code: {d['formula']} = **{K.fmt(round(d['value'], 4))} {c['unit']}**")
            else:
                got = f"**{got}** ({r.get('period_type') or '?'}, {K.show_date(r.get('period_end'))})"
            out.append(f"  - Document: {got} · extraction verified · paper: {r.get('paper')}")
        out.append(f"    - Source: {_doc_line(r['doc'])}")
        if r.get("disclosed_check") and not r["disclosed_check"]["same"]:
            dc = r["disclosed_check"]
            out.append(f"    - The document's figure for the period ({K.fmt(dc['document'])}) is not the one the paper's model used "
                       f"({dc['model']}): probably a different line. The annualised figures agree only at the paper's rounding.")
        if r.get("code_read"):
            wp = r.get("wrong_period") or {}
            out.append(f"    - Read by the code from the latest column of the verified table row. The AI had given "
                       f"{K.fmt(wp.get('ai_value'))} from the {K.show_date(wp.get('ai_period'))} column (an extraction error, see section 4)"
                       + ("; that older figure equals the paper's, so the paper is right for its own date." if wp.get("matches_paper_for_that_date") else "."))
        out += _frag_lines(r["fragments"])
        out += _context_lines(r)
        if r.get("period_confirmed") and r.get("period_evidence"):
            out.append(f"    - Period confirmed by the code: {r['period_evidence']}")
        for w in r.get("warnings") or []:
            out.append(f"    - Warning: {w}")
        if r.get("comment"):
            out.append(f"    - AI's note (not checked): {r['comment']}")
        return out

    @staticmethod
    def checked_docs(v):
        out = []
        for r in v["results"]:
            what = {"not found": "no figure found", "absent": "no separate figure found", "verified": "figure found",
                    "historical": "checked", "rejected": "extraction rejected", "no answer": "no answer", "failed": "failed"}.get(r["status"], r["status"])
            out.append(f"    - {_doc_line(r['doc'])}: {what}" + (f" ({r['comment']})" if r.get("comment") and r["status"] == "not found" else ""))
        for s in v["skips"]:
            out.append(f"    - {_doc_line(s['doc'])}: not asked, {s['why']}")
        return out

    @staticmethod
    def notes(v):
        return [f"  - Rule note: {v['comp']['note']}"] if v["comp"].get("note") else []

    # -- 1. figures that track the latest position
    def tracked(self):
        s = self.sections
        L = ["", "## 1. Figures that track the latest position"]
        L += ["", "### 1a. Newer figure found: proposed updates", ""]
        if not s["update"]:
            L.append("None.")
        for v in s["update"]:
            L += [self.head(v), self.paper_line(v)] + self.found_lines(v, v["best"]) + self.notes(v)
        L += ["", "### 1b. Differs from the paper for the same period (a paper error or an extraction error: check)", ""]
        if not s["differs"]:
            L.append("None.")
        for v in s["differs"]:
            L += [self.head(v), self.paper_line(v)]
            for r in v["results"]:
                if r["status"] == "verified":
                    L += self.found_lines(v, r)
            L += self.notes(v)
        if s["text"]:
            L += ["", "### 1c. Wording differs (compare by hand)", ""]
            for v in s["text"]:
                L += [self.head(v), self.paper_line(v)] + self.found_lines(v, v["best"]) + self.notes(v)
        L += ["", "### 1d. Matches the paper (extraction verified)", ""]
        if not s["matches"]:
            L.append("None.")
        for v in s["matches"]:
            L += self.match_lines(v)
        L += ["", "### 1e. No update found in the documents checked", "",
              "The AI found no figure for these in the documents listed. That is not a confirmation that the paper's figure "
              "is still the latest: the figure may be published elsewhere, or only in another kind of document.", ""]
        if not s["noupdate"]:
            L.append("None.")
        for v in s["noupdate"]:
            L += [self.head(v), self.paper_line(v), "  - Documents checked:"] + self.checked_docs(v) + self.notes(v)
        if s["na"]:
            L += ["", "### 1f. Not checked in this run", ""]
            for v in s["na"]:
                L += [self.head(v), self.paper_line(v), "  - Documents:"] + self.checked_docs(v) + self.notes(v)
        return L

    def match_lines(self, v):
        r, c = v["best"], v["comp"]
        shown = r.get("value_text") if c.get("text") else (r["derived"]["value"] if r.get("derived") else r.get("value"))
        extra = {"newer, unchanged": " (confirmed at a newer date)",
                 "matches, period not confirmed": " (**period not confirmed**: neither a readable column date nor a date "
                                                  "next to the figure confirms it; see the warnings)"}.get(v["verdict"], "")
        L = [self.head(v),
             f"  - Paper {c['value'] if c.get('text') else _val(c['value'], c['unit'])} ({K.show_date(c['period_end'])}) = document "
             f"{shown if c.get('text') else _val(round(shown, 4), c['unit'])} ({K.show_date(r.get('period_end'))}){extra} · {_doc_line(r['doc'])}"]
        L += _frag_lines(r["fragments"])
        if r.get("period_confirmed") and r.get("period_evidence") and c["period_type"] not in ("said", "event", "policy"):
            L.append(f"    - Period confirmed by the code: {r['period_evidence']}")
        for w in r.get("warnings") or []:
            L.append(f"    - Warning: {w}")
        return L

    # -- the code's arithmetic
    def arithmetic(self):
        fl = [(row, f) for row, fs in self.formulas.items() for f in fs]
        if not fl:
            return []
        L = ["", "### Arithmetic done by the code", "",
             "Each formula is worked twice: with the paper's own inputs (does the paper's arithmetic hold?) and with the newest "
             "verified inputs from this run (what the figure would become).", ""]
        for row, f in fl:
            if f.get("error"):
                L.append(f"- **{row}** {f['label']}: could not be worked out ({f['error']})")
                continue
            ins = ", ".join(f"{k} = {K.fmt(f['paper_inputs'][k])}" for k in f["paper_inputs"])
            if f.get("paper_period_note"):
                ins += f"; note: the paper's {f['paper_period_note']}"
            L.append(f"- **{row}** {f['label']}: `{f['expr']}`")
            L.append(f"  - With the paper's inputs ({ins}): {K.fmt(round(f['at_paper'], 4))} → paper prints {f['paper']} {f['unit']}: "
                     f"{'agrees' if f['paper_ok'] else '**does not agree**'}")
            rechecked = [k for k, src in f["sources"].items() if not src.startswith("paper/model")]
            if rechecked:
                L.append(f"  - Inputs re-checked in this run: {', '.join(rechecked)}; not re-checked: "
                         f"{', '.join(k for k in f['sources'] if k not in rechecked) or 'none'}")
            if f["new_inputs"] != f["paper_inputs"] and f.get("new_blocked"):
                L.append(f"  - Not worked out with the newer inputs: {f['new_blocked']}. The code does not mix periods; "
                         f"the other inputs need re-checking for the same period first.")
            elif worked_with_new_inputs(f):
                ins2 = "; ".join(f"{k} = {K.fmt(f['new_inputs'][k])} ({f['sources'][k]})" for k in f["new_inputs"])
                L.append(f"  - With the newest verified inputs ({ins2}): **{K.fmt(round(f['at_new'], 4))} {f['unit']}**"
                         + (" (would change)" if f["changed"] else " (no change at the paper's precision)"))
            else:
                L.append("  - No input changed in this run." if rechecked else "  - No input was re-checked in this run.")
        return L

    # -- 2. absence claims
    def absence(self):
        L = ["", "## 2. Absence claims (the paper says something is not disclosed)", "",
             "“No separate figure found” covers only the documents listed. It does not mean the company has never disclosed one, "
             "and a failed or missing extraction is never counted.", ""]
        if not self.sections["absence"]:
            L.append("None.")
        for v in self.sections["absence"]:
            L.append(self.head(v))
            if v["verdict"] == "figure found":
                L.append("  - **A figure was found: check.**")
                L += self.found_lines(v, v["best"])
            elif v["verdict"] == "no separate figure found":
                L.append("  - No separate figure found in these documents:")
                for r in v["results"]:
                    L.append(f"    - {_doc_line(r['doc'])}. Where the AI looked:")
                    L += _frag_lines(r["fragments"], indent="      ")
            else:
                L.append(f"  - {v['verdict'][0].upper() + v['verdict'][1:]}: see section 4.")
                L += self.checked_docs(v)
        return L

    # -- 3. historical claims
    def historical(self):
        L = ["", "## 3. Historical claims (dated statements)", "",
             "A newer document cannot update what was said on a past date. Related newer statements are shown for context only; "
             "they are not changes to the paper.", ""]
        if not self.sections["historical"]:
            L.append("None.")
        for v in self.sections["historical"]:
            c = v["comp"]
            L.append(self.head(v))
            L.append(f"  - Paper: {c['value']} {'' if c.get('text') else c['unit']} (said {K.show_date(c['period_end'])})")
            for rel in v["related"]:
                L.append(f"  - Related newer statement (context, not a change): {rel['summary']}")
                L += _frag_lines(rel["fragments"])
            if v["verdict"] != "historical":
                L.append("  - Not fully checked (no answer, or statements dropped because their fragments are not in the document): see section 4.")
                L += self.checked_docs(v)
            elif not v["related"]:
                L.append("  - No related newer statement found in the documents checked:")
                L += self.checked_docs(v)
        return L

    # -- 4. extraction problems
    def problems(self):
        L = ["", "## 4. Extraction problems (agent errors)", "",
             "These are problems with the agent's reading, not findings about the paper. The paper's figure is **not checked** "
             "by these answers, whichever way they point.", ""]
        if not self.sections["problems"]:
            L.append("None.")
        for v in self.sections["problems"]:
            L += [self.head(v), self.paper_line(v)]
            for r in v["results"]:
                if r.get("ai_problem"):
                    L.append(f"  - {_doc_line(r['doc'])}: **{r['ai_problem']}**")
                    L.append(f"    - Handled: the code read {K.fmt(round(r['value'], 4))} {v['comp']['unit']} from the "
                             f"{K.show_date(r['period_end'])} column of the same verified row (section 1 uses that, flagged).")
                    wp = r.get("wrong_period") or {}
                    if wp.get("matches_paper_for_that_date"):
                        L.append(f"    - The AI's older-column figure ({K.fmt(wp['ai_value'])}) equals the paper's figure for "
                                 f"{K.show_date(wp['ai_period'])}: the paper's figure is right for its own date.")
                    L += _frag_lines(r.get("fragments") or [])
                    L += _context_lines(r)
                    continue
                if r.get("dropped"):
                    L.append(f"  - {_doc_line(r['doc'])}: **{r['dropped']} related statement(s) dropped: fragments not in the document**")
                    continue
                if r["status"] not in ("rejected", "no answer", "failed"):
                    continue
                L.append(f"  - {_doc_line(r['doc'])}: **{r.get('problem', r['status'])}**")
                L += _frag_lines(r.get("fragments") or [])
                L += _context_lines(r)
            L.append(f"  - Paper check from these answers: not checked. Overall for this component: {v['verdict']}.")
        return L

    # -- 5. new facts
    def new_facts(self, facts, set_aside, dropped, lc):
        merged_n = sum(len(f.get("also_in") or []) for f in facts)
        seen = [f for f in facts if f.get("seen_before")]
        fresh = [f for f in facts if not f.get("seen_before")]
        reasons = {}
        for f in set_aside:
            reasons[f["set_aside"].split(" (")[0]] = reasons.get(f["set_aside"].split(" (")[0], 0) + 1
        L = ["", "## 5. New facts the paper does not have (leads, not updates)", "",
             f"The AI offered {lc['offered']} leads. {lc['not_verified']} were dropped because a fragment is not in the document. "
             f"Of the rest, the code set aside {len(set_aside)} (listed at the end of this section, one line each"
             + (": " + "; ".join(f"{n} {r}" for r, n in reasons.items()) if reasons else "") + ")"
             + (f", and merged {merged_n} that repeat the same fact from another document of the same company" if merged_n else "")
             + f". That leaves **{len(fresh)} new** and {len(seen)} already on the open list"
             + (" or decided" if any(f.get("decided") for f in seen) else "") + " from an earlier run.", "",
             "Every fragment is in the document word for word. “Why it matters” is the AI's comment and is not checked beyond its numbers.", ""]
        groups = [("5a. From filings (10-K, 10-Q, 6-K)", 1), ("5b. From results releases", 2),
                  ("5c. From calls (management's words: check against a filing before relying on them)", 3)]
        for title, rank in groups:
            fs = [f for f in fresh if K.source_rank(f["doc"]["form"]) == rank]
            L += ["", f"### {title}", ""]
            if not fs:
                L.append("None new.")
            for f in fs:
                L += _fact_block(f)
        if seen:
            L += ["", "### 5d. Already on the open list" + (" or decided" if any(f.get("decided") for f in seen) else "") + " (found again)", ""]
            for f in seen:
                L += _fact_block(f, full=False)
        if set_aside:
            L += ["", "### 5e. Set aside by the code", ""]
            for f in set_aside:
                L.append(f"- {f['topic']} · {C.NAME.get(f.get('company'), '')} · {f['doc']['title']} · {f['set_aside']}")
        if dropped:
            L += ["", f"({len(dropped)} further 'new facts' were dropped because a fragment was not in the document.)"]
        return L


def _fact_block(f, full=True):
    rel = f" (rows {', '.join(f['rows'])})" if f["rows"] else ""
    out = [f"- **{f['topic']}**{rel} · {C.NAME.get(f.get('company'), f.get('company') or '')} · {_doc_line(f['doc'])}"]
    if not full:
        d = f.get("decided")
        out[0] += (f" · you decided: {d['decision']} ({K.show_date((d.get('decided_at') or '')[:10])})" if d
                   else f" · on the open list since {K.show_date(f['seen_before'][:10])}")
        return out
    out += _frag_lines(f["fragments"])
    if f.get("repeats"):
        out.append("    - Repeats the paper's " + ", ".join(f"{s} ({row})" for s, row in f["repeats"]) + "; the rest is new")
    if f["why"]:
        out.append(f"    - Why it matters (AI): {f['why']}")
    for d in f["also_in"]:
        out.append(f"    - Also reported in {_doc_line(d)}")
    return out


# ------------------------------------------------------------------------------------------
# The proposals (added to proposed.jsonl, in the order the report lists them)
# ------------------------------------------------------------------------------------------
def build_proposals(started, verdicts, formulas, facts):
    """The run's proposals for proposed.jsonl: newer, differing and reworded figures, arithmetic that would change,
    figures found for absence claims, and new facts."""
    s = sections(verdicts)
    found_at = started.isoformat()
    out = [_figure_proposal(found_at, v, "newer figure") for v in s["update"]]
    out += [_figure_proposal(found_at, v, v["verdict"]) for v in s["differs"]]
    out += [_figure_proposal(found_at, v, "text differs") for v in s["text"]]
    for row, fs in formulas.items():
        for f in fs:
            if worked_with_new_inputs(f) and f["changed"]:
                out.append({"found_at": found_at, "part": "B", "version": 2, "row": row, "component": f["name"],
                            "kind": "formula with newer inputs", "paper_value": f["paper"], "unit": f["unit"],
                            "new_value": round(f["at_new"], 4), "formula": f["expr"], "inputs": f["new_inputs"],
                            "input_sources": f["sources"], "decision": "pending"})
    out += [_figure_proposal(found_at, v, "figure found") for v in s["absence"] if v["verdict"] == "figure found"]
    for f in facts:
        out.append({"found_at": found_at, "part": "B", "version": 2, "row": "NEW FACT", "company": f.get("company"),
                    "related_rows": f["rows"], "topic": f["topic"], "fragments": [{"role": x["role"], "text": x["text"]} for x in f["fragments"]],
                    "why": f["why"], "document": f["doc"]["title"], "link": f["doc"]["url"],
                    "also_in": [d["url"] for d in f["also_in"]], "seen_before": f.get("seen_before") or None,
                    "status": "NEW FACT", "decision": "pending"})
    return out


def _figure_proposal(found_at, v, kind):
    c, r = v["comp"], v.get("best") or {}
    return {"found_at": found_at, "part": "B", "version": 2, "row": v["row"], "component": c["key"],
            "company": c["company"], "kind": kind, "paper_value": c["value"], "unit": c["unit"],
            "paper_period_end": c["period_end"], "paper_period_type": c["period_type"],
            "new_value": r.get("value_text") if c.get("text") else (r.get("derived") or {}).get("value", r.get("value")),
            "new_period_end": r.get("period_end"), "new_period_type": r.get("period_type"),
            "extraction": r.get("status"), "paper_check": r.get("paper") or v["verdict"],
            "fragments": [{"role": f["role"], "text": f["text"], "verified": bool(f.get("ok"))} for f in r.get("fragments") or []],
            "document": r.get("doc", {}).get("title"), "link": r.get("doc", {}).get("url"),
            "decision": "pending"}
