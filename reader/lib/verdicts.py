"""
Does the paper agree? Asked only of figures that passed the extraction check (extract.py): a rejected, missing or
failed extraction never counts as confirming the paper.

  * paper_check: one verified figure against the paper's figure for that component;
  * combine: one verdict per component, from every document it was asked of;
  * formula_results: the arithmetic in claims.py, worked out with the paper's inputs and with this run's;
  * sections: which part of the report each verdict belongs in.
"""

import claims as R

from . import checks as K
from .extract import MONTHS_IN


def _order(comp, ext):
    cp, ep = K.parse_date(comp["period_end"]), K.parse_date(ext.get("period_end"))
    if comp["period_type"] == "event":
        return 0
    if not (cp and ep):
        return None
    if comp["period_type"] == "said":
        return (ep > cp) - (ep < cp)
    return (K.month_key(ep) > K.month_key(cp)) - (K.month_key(ep) < K.month_key(cp))


def paper_check(item, ext):
    """Compare a VERIFIED extraction with the paper's figure for this component. Returns (verdict, value)."""
    comp = item["comp"]
    order = _order(comp, ext)
    if comp.get("text"):
        same = K.norm(comp["value"]) in K.norm(ext.get("carrier") or "")
        if order is not None and order < 0:
            return "document older than the paper's figure", None
        return ("matches" if same else "text differs"), None
    value = ext["derived"]["value"] if ext.get("derived") else ext["value"]
    same = K.compare(comp["value"], round(float(value), 4)) == "same"
    dis = comp.get("disclosed")
    if dis and ext.get("derived") and order == 0 and (ext.get("period_type") or "") == dis.get("period_type"):
        # the paper's model used a disclosed figure for this same period: it must be the same line
        ext["disclosed_check"] = dict(model=dis["value"], document=ext["derived"]["x"],
                                      same=K.compare(dis["value"], round(ext["derived"]["x"], 4)) == "same")
        if not ext["disclosed_check"]["same"]:
            return "differs for the same period", value
    if order is None:
        return ("matches, period not confirmed" if same else "differs, period unknown"), value
    if order < 0:
        return "document older than the paper's figure", value
    if order > 0:
        if same and not ext.get("period_confirmed"):
            return "same value as the paper's older figure, period not confirmed", value
        return ("newer, unchanged" if same else "newer figure"), value
    if same and not ext.get("period_confirmed"):
        return "matches, period not confirmed", value
    return ("matches" if same else "differs for the same period"), value


# ------------------------------------------------------------------------------------------
# Combining the documents: one verdict per component
# ------------------------------------------------------------------------------------------
CONFIRMED = ("matches", "newer, unchanged")
USABLE = ("matches", "newer, unchanged", "newer figure", "differs for the same period", "matches, period not confirmed")


def combine(item, results, skips):
    """One verdict per component, from all documents it was asked of."""
    comp, typ = item["comp"], item["type"]
    v = dict(id=item["id"], row=item["row"], key=comp["key"], company=comp["company"], comp=comp, type=typ,
             results=results, skips=skips)
    ok = [r for r in results if r["status"] == "verified"]
    problems = [r for r in results if r["status"] in ("rejected", "no answer", "failed")]
    if typ == "historical":
        rel = [x for r in results for x in r.get("related") or []]
        clean = bool(results) and not problems and not any(r.get("dropped") for r in results)
        v.update(verdict="historical" if clean else "historical, not fully checked", related=rel)
        return v
    if typ == "absence":
        if ok:
            v.update(verdict="figure found", best=ok[0])
        elif results and all(r["status"] == "absent" for r in results):
            v.update(verdict="no separate figure found")
        elif not results:
            v.update(verdict="not checked")
        else:
            v.update(verdict="not checked (extraction problems)")
        return v
    if ok:
        for r in ok:
            r["paper"], r["compared_value"] = paper_check(item, r)
        latest = max(ok, key=lambda r: r.get("period_end") or "")
        same_period = [r for r in ok if (r.get("period_end") or "")[:7] == (latest.get("period_end") or "")[:7]
                       and r.get("compared_value") is not None]
        clash = any(K.compare(a["compared_value"], round(float(b["compared_value"]), 4)) == "different"
                    for a in same_period for b in same_period if a is not b)
        v.update(verdict="conflict" if clash and not comp.get("text") else latest["paper"], best=latest)
        return v
    if problems:
        v.update(verdict="not checked (extraction problems)")
    elif results:
        v.update(verdict="no update found")
    else:
        v.update(verdict="not checked")
    return v


# ------------------------------------------------------------------------------------------
# Formulas
# ------------------------------------------------------------------------------------------
def _month_index(d):
    return d.year * 12 + d.month - 1 if d else None


def _period_problem(f, periods):
    """Check a formula's period rules on the periods of its inputs. Returns a reason, or None."""
    for rule in f.get("periods") or []:
        kind, names = rule[0], rule[1:]
        if kind == "same":
            names = [n for n in names[0] if n in periods]
            ends = {_month_index(periods[n][0]) for n in names}
            if len(ends) > 1:
                return "inputs are for different dates: " + ", ".join(
                    f"{n} {K.show_date(periods[n][0].isoformat() if periods[n][0] else '')}" for n in names)
        elif kind == "year_apart":
            a, b = names
            if a in periods and b in periods and (_month_index(periods[a][0]) or 0) - (_month_index(periods[b][0]) or 0) != 12:
                return f"{b} should be the same period a year before {a}"
            if a in periods and b in periods and periods[a][1] and periods[b][1] and periods[a][1] != periods[b][1]:
                return f"{a} and {b} cover different lengths of time ({periods[a][1]} and {periods[b][1]} months)"
        elif kind == "follows":
            fy, ytd = names
            if fy in periods and ytd in periods:
                if not periods[ytd][1]:
                    return f"the length of {ytd} is not known"
                if (_month_index(periods[fy][0]) or 0) + periods[ytd][1] != (_month_index(periods[ytd][0]) or 0):
                    return f"{ytd} does not follow straight on from {fy}"
    return None


def formula_results(claim, verdicts):
    """Evaluate each formula twice: with the paper's inputs (checks the paper's own arithmetic) and with the
    newest verified inputs from this run (shows what the figure would become). Inputs for mismatched periods
    are never mixed."""
    out = []
    by_key = {v["key"]: v for v in verdicts}
    for f in claim.get("formulas") or []:
        paper_in, new_in, sources, p_per, n_per = {}, {}, {}, {}, {}
        for name in R.names_in(f["expr"]):
            comp = next((c for c in claim["components"] if c["key"] == name), None)
            if comp is None:
                continue
            paper_in[name] = float(comp["value"])
            p_per[name] = (K.parse_date(comp["period_end"]), MONTHS_IN.get(comp["period_type"]) or comp.get("months"))
            v = by_key.get(name)
            best = v.get("best") if v and v.get("verdict") in USABLE else None
            if best is not None and v["verdict"] in CONFIRMED:
                dv = float(best["compared_value"])          # confirmed: keep the more precise of the two numbers
                new_in[name] = paper_in[name] if K.decimals(comp["value"]) >= K.decimals(repr(round(dv, 6))) else dv
                sources[name] = f"confirmed in {best['doc']['title']}"
                n_per[name] = p_per[name]
            elif best is not None and v["verdict"] in ("newer figure", "differs for the same period"):
                new_in[name] = float(best["compared_value"])
                sources[name] = f"{v['verdict']} in {best['doc']['title']}, {best.get('period_end') or ''}"
                n_per[name] = (K.parse_date(best.get("period_end")), MONTHS_IN.get(best.get("period_type")) or comp.get("months"))
            else:
                new_in[name] = paper_in[name]
                sources[name] = "paper/model value, not re-checked in this run"
                n_per[name] = p_per[name]
        try:
            at_paper = R.evaluate(f["expr"], paper_in)
        except (KeyError, ZeroDivisionError, ValueError) as e:
            out.append(dict(f, error=str(e)))
            continue
        res = dict(f, paper_inputs=paper_in, new_inputs=new_in, sources=sources, at_paper=at_paper,
                   paper_ok=K.compare(f["paper"], round(at_paper, 4)) == "same", paper_period_note=_period_problem(f, p_per))
        why = _period_problem(f, n_per) if new_in != paper_in else None
        if why:
            res.update(at_new=None, changed=None, new_blocked=why)
        else:
            try:
                res["at_new"] = R.evaluate(f["expr"], new_in)
                res["changed"] = K.compare(f["paper"], round(res["at_new"], 4)) != "same"
            except (KeyError, ZeroDivisionError, ValueError) as e:
                res.update(at_new=None, changed=None, new_blocked=str(e))
        out.append(res)
    return out


def worked_with_new_inputs(f):
    """Was this formula worked out again with inputs that changed in this run? (Not if it failed, or if the new inputs
    are for mismatched periods.) The report shows these; those that would change the paper's figure are proposed."""
    return not f.get("error") and f["new_inputs"] != f["paper_inputs"] and not f.get("new_blocked")


def formulas_by_row(verdicts, ledger):
    """{row: formula results} for every row with a verdict in this run."""
    by_row = {}
    for v in verdicts:
        by_row.setdefault(v["row"], []).append(v)
    return {row: formula_results(R.claim_for(row, ledger.get(row)), vs) for row, vs in by_row.items()}


# ------------------------------------------------------------------------------------------
# Which part of the report each verdict belongs in
# ------------------------------------------------------------------------------------------
SECTION_OF = {"newer figure": "update",
              "differs for the same period": "differs", "conflict": "differs", "differs, period unknown": "differs",
              "same value as the paper's older figure, period not confirmed": "differs",
              "text differs": "text",
              "matches": "matches", "newer, unchanged": "matches", "matches, period not confirmed": "matches",
              "no update found": "noupdate"}


def sections(verdicts):
    """The verdicts sorted into the report's sections. A component with an extraction problem is also listed under
    'problems' (section 4), whatever its verdict."""
    out = {k: [] for k in ("update", "differs", "text", "matches", "noupdate", "absence", "historical", "problems", "na")}
    for v in verdicts:
        vd, typ = v["verdict"], v["type"]
        if typ in ("historical", "absence"):
            out[typ].append(v)
        elif vd in SECTION_OF:
            out[SECTION_OF[vd]].append(v)
        elif not vd.startswith("not checked (extraction"):
            out["na"].append(v)
        if any(r["status"] in ("rejected", "no answer", "failed") or r.get("ai_problem") or r.get("dropped") for r in v["results"]) \
                or (typ == "historical" and not v["results"]):
            out["problems"].append(v)
    return out


def items_checked(verdicts, formulas):
    """What this run checked, for runs.jsonl: {item or formula: verdict}. The open list uses it to tell a proposal a
    later run no longer finds."""
    items = {v["id"]: v["verdict"] for v in verdicts}
    for row in sorted(formulas):
        for fr in formulas[row]:
            items[f"{row}:{fr['name']}"] = ("no change" if fr.get("changed") is False else
                                            "would change" if fr.get("changed") else "not worked out")
    return items
