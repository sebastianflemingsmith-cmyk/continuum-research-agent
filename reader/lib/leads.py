"""
New facts ("leads"): facts in a document that the paper does not have. The AI offers them; the code checks every
fragment word for word and then sorts them, with no AI:

  * set aside (listed one line each, never hidden): boilerplate such as non-GAAP definitions; leads with no figure at
    all, unless they report a change of definition or accounting or a financing structure; leads whose only figures are
    the paper's own for the same measure (or were checked in this run);
  * the same fact from several documents of one company is shown once, in the filing's words;
  * a lead already on the open list from an earlier run is marked with the date it was first seen.
The same rules sort the leads on the open list (openlist.py).
"""

import re

import claims as R

from . import checks as K
from .extract import as_str, fragments_of, verify_fragments


# ------------------------------------------------------------------------------------------
# Checking the AI's leads
# ------------------------------------------------------------------------------------------
def check_fact(doc, fct, allowed_rows, ticker=""):
    """A lead the AI offered, if every fragment is in the document word for word (else None). A comment that uses
    figures not in the fragments is removed."""
    frags = verify_fragments(doc, fragments_of(fct))
    if not frags or not all(f["ok"] for f in frags):
        return None
    why = as_str(fct.get("why_it_matters"))
    text = " ".join(f["text"] for f in frags)
    bare = re.sub(r"\bF\d{3}(?::\w+)?\b", " ", why)                     # ledger IDs such as F152 are not figures
    extra = [n for n in K.numbers_in(re.sub(r"\b(19|20)\d{2}\b", " ", bare)) if n not in (0, 1) and not K.number_supported(n, text)]
    if extra:
        why = f"[the AI's comment was removed: it used figures not in the fragments ({', '.join(f'{x:g}' for x in extra[:4])})]"
    rr = fct.get("related_rows") or []
    rows = [r for r in (rr if isinstance(rr, list) else [rr]) if isinstance(r, str) and r.split(":")[0] in allowed_rows]
    return dict(topic=as_str(fct.get("topic")), rows=sorted({r.split(":")[0] for r in rows}), fragments=frags, why=why, doc=K.doc_info(doc),
                company=ticker)


def dedupe_facts(facts):
    """The same words from two documents are one fact, kept in the better source (a filing before a release or a call)."""
    out = []
    for f in sorted(facts, key=lambda f: K.source_rank(f["doc"]["form"])):
        key = K.norm(max((x["text"] for x in f["fragments"]), key=len))
        # the same words: one text inside the other, and not much shorter (a short quote inside a longer one that
        # adds other figures is a different lead)
        dup = next((g for g in out if (key in g["_key"] or g["_key"] in key)
                    and min(len(key), len(g["_key"])) >= 0.8 * max(len(key), len(g["_key"]))), None)
        if dup:
            dup["also_in"].append(f["doc"])
            continue
        f["_key"], f["also_in"] = key, []
        out.append(f)
    return out


def sort_leads(facts, verdicts, earlier_open=()):
    """Sort the verified new facts (no AI). Returns (kept, set aside).
      * set aside, with the reason: boilerplate (non-GAAP definitions, where to find a reconciliation); no figure at all
        (commentary, product news), unless it reports a change of definition or accounting or a financing structure;
        or every amount in it is already the paper's own figure for the same measure (or was checked in this run);
      * the same fact from two documents of one company (the same specific amount, e.g. $105 billion) is shown once,
        in the filing's words, with the other documents named;
      * a lead already on the open list from an earlier run is marked with the date it was first seen."""
    extra = {}
    for v in verdicts:
        c = v["comp"]
        for r in v["results"]:
            if r["status"] != "verified" or r.get("value") is None or c.get("text"):
                continue
            try:
                val = float(r["value"])
            except (TypeError, ValueError):
                continue
            kind = "money" if c["unit"].startswith("$") else "pct" if c["unit"] in ("%", "share") else None
            if kind:
                extra.setdefault(v["company"], []).append((kind, val * 100 if c["unit"] == "share" else val, v["row"], c.get("must") or []))
    kept, aside = [], []
    for f in facts:
        text = "\n".join(x["text"] for x in f["fragments"])
        why = lead_problem(f["topic"], text)
        if not why:
            hits, all_rep = repeats(text, known_values(f["company"], extra.get(f["company"], [])))
            f["repeats"] = hits
            if all_rep:
                why = "only figures the paper already has, or this run checked" + (
                    " (" + ", ".join(f"{s} {row}" for s, row in hits) + ")" if hits else "")
        if why:
            f["set_aside"] = why
            aside.append(f)
        else:
            kept.append(f)
    merged = []
    for f in sorted(kept, key=lambda f: K.source_rank(f["doc"]["form"])):
        text = "\n".join(x["text"] for x in f["fragments"])
        figs = fact_figures(text)
        g = next((g for g in merged if g["company"] == f["company"] and g["doc"]["url"] != f["doc"]["url"]
                  and same_fact(g["_figs"], g["_text"], figs, text)), None)
        if g is not None:
            g["also_in"].append(f["doc"])
            g.setdefault("merged", []).append(f["topic"])
            continue
        f["_figs"], f["_text"] = figs, text
        merged.append(f)
    for f in merged:
        as_p = dict(fragments=f["fragments"], link=f["doc"]["url"], topic=f["topic"], document=f["doc"]["title"])
        e = next((e for e in earlier_open if e["company"] == f["company"] and          # as the open list will group it
                  all(same_lead(m, as_p) for m in e.get("_members") or [e.get("show") or e["latest"]])), None)
        if e is not None:
            f["seen_before"] = e["first_seen"]
            if e.get("decision_made"):
                f["decided"] = e["decision_made"]
    return merged, aside


# ------------------------------------------------------------------------------------------
# Is it the same fact? (the same passage, or the same specific amount)
# ------------------------------------------------------------------------------------------
def fact_text(p):
    """All the words a lead quotes."""
    frs = p.get("fragments") or []
    if not frs and p.get("quote"):
        frs = [{"role": "value", "text": p["quote"]}]
    return "\n".join(f.get("text", "") for f in frs if isinstance(f, dict))


def fact_figures(text):
    """The specific amounts a lead states (money or capacity, three or more significant digits)."""
    return [(v, s) for kind, v, s in K.figures_in(text) if kind in ("money", "qty") and K.significant(s)]


def same_fact(figs_a, text_a, figs_b, text_b):
    """Same company assumed. The same specific amount, or the same words (one quote inside the other)."""
    if any(K.same_amount(x, y) for x in figs_a for y in figs_b):
        return True
    na, nb = K.norm(text_a), K.norm(text_b)
    return bool(len(na) > 30 and len(nb) > 30 and (na in nb or nb in na))


def _sentences(p, shortest=40):
    """The quoted passages that state the fact (fragments in the 'value' role), as compared."""
    frs = p.get("fragments") or ([{"role": "value", "text": p["quote"]}] if p.get("quote") else [])
    out = [K.norm(f.get("text") or "") for f in frs if isinstance(f, dict) and f.get("role", "value") == "value"]
    return [s for s in out if len(s) >= shortest]


def same_lead(a, b):
    """Are two leads (proposal-shaped: fragments or quote, link, document) the same fact? Same company assumed. They are
    when they quote the same passage, or state the same specific amount (as printed, allowing for millions against
    billions). In one document a round number such as $100.0 billion does not count (a lead found again there quotes the
    same words, while two deals of $100.0 billion are two facts); two numbers in common do. The topic does not count:
    two tables in one filing can share a topic."""
    ta, tb = fact_text(a), fact_text(b)
    one_doc = bool(a.get("link")) and a.get("link") == b.get("link")
    fa, fb = fact_figures(ta), fact_figures(tb)
    if one_doc:
        fa, fb = [x for x in fa if not K.round_number(x[1])], [x for x in fb if not K.round_number(x[1])]
    if same_fact(fa, ta, fb, tb) or any(x in y or y in x for x in _sentences(a) for y in _sentences(b)):
        return True
    if one_doc:
        xa = [(v, s) for _, v, s in K.figures_in(ta)]
        xb = [(v, s) for _, v, s in K.figures_in(tb)]
        in_b = sum(1 for x in xa if any(K.same_amount(x, y) for y in xb))
        in_a = sum(1 for y in xb if any(K.same_amount(x, y) for x in xa))
        return min(in_a, in_b) >= 2                 # counted both ways, so that a and b give the same answer
    return False


# ------------------------------------------------------------------------------------------
# Boilerplate and leads without a figure
# ------------------------------------------------------------------------------------------
_BOILER = re.compile(r"non-gaap|is defined as|\bwe define|\bdefined as\b|reconciliation|should not be considered|"
                     r"does not (incorporate|include|reflect)|forward-looking|safe harbor|limitations?\b|constant currency|"
                     r"presented in accordance with gaap|this (press release|current report)|see [\"']")
_BOILER_TOPIC = re.compile(r"definition|limitation|reconciliation|non-gaap|forward-looking|disclaimer|methodolog|location")
_CHANGE = re.compile(r"reclassif|recast|restat|redefin|no longer (report|disclos|includ|present|provid)|useful li(fe|ves)|"
                     r"extend(ed|ing)? the estimated|"
                     r"\bchang(e|ed|es|ing) (in|to|of) (the |our |its )?(definition|presentation|methodology|accounting|estimate|"
                     r"segment|reporting|useful)")
_STRUCTURE = re.compile(r"guarantee|take[- ]or[- ]pay|backstop|credit (support|enhancement)|residual value|revenue[- ]shar|"
                        r"prepayment|off[- ]balance|special purpose|variable interest|joint venture|covenant|collateral|recourse")


def lead_problem(topic, text):
    """Why a verified lead is set aside ('boilerplate' or 'no figure'), or '' to keep it. A lead with no amount is kept
    only if it reports a change of definition or accounting, or a financing structure (a guarantee, take-or-pay, a
    backstop...)."""
    t, top = K.norm(text), K.norm(topic)
    if K.figures_in(text):
        return ""
    if _CHANGE.search(t) or _CHANGE.search(top) or _STRUCTURE.search(t):
        return ""
    if _BOILER.search(t) or _BOILER_TOPIC.search(top):
        return "boilerplate"
    return "no figure"


# ------------------------------------------------------------------------------------------
# Figures the paper already has
# ------------------------------------------------------------------------------------------
_PART_A_MEASURES = [(r"free cash flow", [["free cash flow"]]),
                    (r"capex", [["capex", "capital expenditure*", "property and equipment", "property, plant and equipment"]]),
                    (r"operating cash flow", [["operating activities", "operating cash flow", "cash from operations"]]),
                    (r"cash and cash equivalents", [["cash and cash equivalents"]]),
                    (r"leases not yet commenced", [["not yet commenced", "have not commenced", "not commenced"]]),
                    (r"remaining performance obligations", [["remaining performance obligation*", "rpo"]]),
                    (r"finance lease", [["finance lease*"]]),
                    (r"revenue", [["revenue*"]])]
_PART_A = None


def _part_a_values():
    """The paper's figures that Part A checks (reader_sec.SPECS), with measure words taken from their names."""
    global _PART_A
    if _PART_A is None:
        _PART_A = []
        try:
            import reader_sec
            for sp in reader_sec.SPECS:
                if sp.get("unit") and not str(sp["unit"]).startswith("$"):
                    continue
                must = next((m for pat, m in _PART_A_MEASURES if re.search(pat, sp.get("what", "").lower())), None)
                if must and sp.get("paper") is not None:
                    _PART_A.append((sp["co"], ("money", float(sp["paper"]), sp["id"], must)))
        except (ImportError, AttributeError, KeyError, TypeError, ValueError):
            pass                                  # Part A missing or changed: its figures are simply not used here
    return _PART_A


def known_values(company, extra=()):
    """The paper's own figures for this company (from claims.py), plus any extra given: [(kind, value, row, must)],
    where `must` is the rule's measure words (a lead repeats a figure only if it also names the same measure)."""
    out = list(extra) + [k for co, k in _part_a_values() if co == company]
    for row, claim in R.CLAIMS.items():
        for c in claim["components"]:
            if c.get("company") != company or c.get("text") or c.get("value") in (None, ""):
                continue
            try:
                v = float(c["value"])
            except (TypeError, ValueError):
                continue
            unit, must = c.get("unit") or "", c.get("must") or []
            if unit.startswith("$"):
                out.append(("money", v, row, must))
            elif unit == "%":
                out.append(("pct", v, row, must))
            elif unit == "share":
                out.append(("pct", v * 100, row, must))
            if c.get("disclosed"):
                try:
                    out.append(("money", float(c["disclosed"]["value"]), row, must))
                except (TypeError, ValueError, KeyError):
                    pass
    return out


def repeats(text, known):
    """Which of a lead's amounts the paper already has: ([(printed, row)], all amounts repeated?)."""
    figs = K.figures_in(text)
    hits, n_hit = [], 0
    for kind, v, s in figs:
        rows = sorted({row for k, kv, row, must in known if k == kind and K.number_supported(kv, s)
                       and K.number_supported(v, K.fmt(kv, 6)) and not K.mentions(text, must)})
        if rows:
            n_hit += 1
            if K.significant(s) or kind == "pct":
                hits.append((s + ("%" if kind == "pct" else ""), rows[0]))
    return hits, bool(figs) and n_hit == len(figs)
