"""
Did the agent read the document correctly? The extraction check, done by code with no AI, for one item in one document:

  * every fragment the AI quotes is in the document, word for word, each on its own;
  * the number is in the value fragment, and on the scale it is printed on;
  * the words around it name the measure the rule asks for (the wrong-metric guard);
  * the figure is for the document's latest period (the wrong-period guard). For a table the code reads the column
    dates, lengths and units itself; if the AI took an older or wrong-length column, the code reads the right one from
    the same verified row and says so;
  * any arithmetic the rule needs (annualising, a formula) is done by the code.

The result says how the check went ('verified', 'rejected', 'not found', 'absent', 'historical', 'no answer') and why.
Whether the paper agrees is a separate question (verdicts.py), asked only of verified results.
"""

import re

import claims as R

from . import checks as K
from . import tables as T

MONTHS_IN = {"3m": 3, "6m": 6, "9m": 9, "12m": 12, "ttm": 12}
LATEST_RULE = {"instant", "3m", "6m", "9m", "12m", "ytd", "ttm"}   # period types that must come from the latest column
PERIOD_WORDS = {"3m": ["three months", "3 months", "quarter"], "6m": ["six months", "6 months", "half"],
                "9m": ["nine months", "9 months"], "12m": ["twelve months", "12 months", "year", "fiscal"],
                "ttm": ["trailing twelve months", "trailing 12 months", "ttm"]}


# ------------------------------------------------------------------------------------------
# Reading the AI's answer
# ------------------------------------------------------------------------------------------
def as_str(x):
    """The AI's value as text, whatever type it came as ('' for nothing)."""
    return x if isinstance(x, str) else ("" if x is None else str(x))


def truthy(x):
    """The AI's 'found': only a real true (or the word true/yes) counts. 'false' as a string is false."""
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return x != 0
    return as_str(x).strip().lower() in ("true", "yes", "y")


def fragments_of(ans):
    """The fragments the AI quotes, as [{"role", "text"}], whatever shape they came in."""
    out = []
    frs = ans.get("fragments") if isinstance(ans, dict) else None
    if isinstance(frs, (str, dict)):
        frs = [frs]
    for f in frs if isinstance(frs, list) else []:
        if isinstance(f, dict) and as_str(f.get("text")).strip():
            out.append({"role": as_str(f.get("role") or "value").strip().lower() or "value", "text": as_str(f["text"])})
        elif isinstance(f, str) and f.strip():
            out.append({"role": "value", "text": f})
    if not out and isinstance(ans, dict) and as_str(ans.get("quote")).strip():   # older answer format: one quote
        out.append({"role": "value", "text": as_str(ans["quote"])})
    return out


def verify_fragments(doc, frags, near=None, before=20000, after=3000):
    """Check each fragment on its own against the document. With `near`, a fragment must also sit within
    `before` characters before (or `after` characters after) that offset: context, column and units lines
    belong to the figure, not to some other table."""
    for f in frags:
        if near is None:
            hits, mode = doc.find(f["text"])
            f.update(ok=bool(hits), match=mode, offsets=hits)
        else:
            k, mode = doc.find_near(f["text"], near, before, after)
            if k is not None:
                f.update(ok=True, match=mode, offsets=[k])
            else:
                hits, mode = doc.find(f["text"], limit=1)
                f.update(ok=False, match=mode, offsets=hits, far=bool(hits))
    return frags


# ------------------------------------------------------------------------------------------
# Units
# ------------------------------------------------------------------------------------------
_UNIT_FACTORS = {("$m", "$bn"): 1e-3, ("$bn", "$m"): 1e3, ("$thousands", "$bn"): 1e-6, ("%", "share"): 1e-2,
                 ("share", "%"): 1e2, ("million", "billion"): 1e-3, ("billion", "million"): 1e3}


def _unit_name(u, like=""):
    u = as_str(u).strip().lower().replace(" ", "")
    money = "$" in u or "usd" in u or "dollar" in u or as_str(like).startswith("$")
    if u in ("", "text"):
        return u
    if re.search(r"(bn|billion)", u) and "gw" not in u:
        return "$bn" if money else "billion"
    if re.search(r"(^|\$|usd)(m|mn|mm|million)s?$", u) or u in ("$m", "usdm", "million$", "$million", "millionusd"):
        return "$m" if money else "million"
    if u in ("%", "percent", "pct", "percentage"):
        return "%"
    if u in ("share", "fraction", "ratio", "decimal"):
        return "share"
    return u


def convert(value, from_u, to_u):
    """The AI's number in the rule's unit, and a note if it was converted (or could not be)."""
    f, t = _unit_name(from_u, to_u or ""), _unit_name(to_u, to_u or "")
    if not f or f == t or not t or t == "text":
        return value, None
    k = _UNIT_FACTORS.get((f, t))
    if k is None:
        return value, f"the AI gave the unit as '{as_str(from_u)}', the rule uses '{to_u}'"
    return value * k, f"converted from {as_str(from_u)} to {to_u}"


# ------------------------------------------------------------------------------------------
# One item in one document
# ------------------------------------------------------------------------------------------
class Reject(Exception):
    """The answer fails a check: the item is 'rejected' with this reason, and the paper is not checked by it."""


def extract(doc, item, ans):
    """Extraction check for one item in one document. Never raises: an answer the code cannot read is a rejection."""
    comp = item["comp"]
    res = dict(id=item["id"], row=item["row"], key=comp["key"], company=comp["company"], type=item["type"], warnings=[],
               doc=K.doc_info(doc), comment=as_str((ans or {}).get("comment") if isinstance(ans, dict) else ""))
    if ans is None:
        res.update(status="no answer", problem="the AI gave no answer for this item")
        return res
    try:
        if not isinstance(ans, dict):
            raise Reject("the AI's answer for this item is not in the expected form")
        frags = fragments_of(ans)
        res["fragments"] = frags
        if item["type"] == "historical":
            return _historical(doc, ans, res)
        found = truthy(ans.get("found"))
        if item["type"] == "absence":
            return _absence_found(doc, ans, frags, res) if found else _absence_not_found(doc, frags, res)
        if not found:
            res.update(status="not found")
            return res
        return FigureCheck(doc, item, ans, frags, res).run()
    except Reject as r:
        res.update(status="rejected", problem=str(r))
        return res
    except Exception as e:                      # a malformed answer must not stop the run
        res.update(status="rejected", problem=f"the code could not read the AI's answer for this item ({e.__class__.__name__}: {e})")
        return res


def _historical(doc, ans, res):
    """A dated statement: a newer document cannot change it. Only related newer statements are kept, each verified."""
    related, dropped = [], 0
    rel = ans.get("related")
    for r in (rel if isinstance(rel, list) else [rel] if isinstance(rel, dict) else []):
        fr = verify_fragments(doc, fragments_of(r) if isinstance(r, dict) else [])
        if fr and all(f["ok"] for f in fr):
            related.append(dict(summary=as_str(r.get("summary")), fragments=fr))
        else:
            dropped += 1
    if dropped:
        res["warnings"].append(f"{dropped} related statement(s) dropped: their fragments are not in the document")
    res.update(status="historical", related=related, dropped=dropped)
    return res


def _absence_not_found(doc, frags, res):
    """The paper says something is not disclosed, and the AI did not find it: that needs verified evidence of where
    the AI looked."""
    ev = [f for f in frags if f["role"] in ("evidence", "value", "context")]
    if not ev:
        raise Reject("no evidence of where the AI looked")
    # A table row with its label ('YouTube ads | 9,796 | 11,055') shows where the AI looked even when it is short;
    # a bare word or two does not. Short fragments are ignored if the answer has other, adequate evidence.
    def adequate(f):
        n = K.norm(f["text"])
        row = "|" in f["text"] and re.search(r"[a-z]{3}", n) and len(re.findall(r"\d[\d,.]*", n)) >= 2
        return bool(row) or (len(n) >= 25 and len(n.split()) >= 4)
    short = [f for f in ev if not adequate(f)]
    if len(short) == len(ev):
        raise Reject("evidence too short to show where the AI looked: " + "; ".join(f"“{f['text'][:40]}”" for f in short))
    for f in short:
        res["warnings"].append(f"an evidence fragment too short to show anything was ignored (“{f['text'][:40]}”)")
    ev = [f for f in ev if adequate(f)]
    verify_fragments(doc, ev)
    if not all(f["ok"] for f in ev):
        raise Reject("evidence fragment not in the document: " + "; ".join(f"“{f['text'][:80]}”" for f in ev if not f["ok"]))
    res.update(status="absent", fragments=ev)
    return res


def _absence_found(doc, ans, frags, res):
    """The AI says a figure the paper calls undisclosed now exists: show it, verified, for a human to judge."""
    v = as_str(ans.get("value_text") or ans.get("value")).strip().lower()
    if v in ("", "none", "no", "nil", "0", "n/a", "not disclosed"):
        raise Reject("contradictory answer: 'found' is true, but no figure is given")
    verify_fragments(doc, frags)
    if not frags or not all(f["ok"] for f in frags):
        raise Reject("the AI said a figure exists, but its fragments are not in the document")
    res.update(status="verified", value=ans.get("value"), period_end=as_str(ans.get("period_end")),
               period_type=as_str(ans.get("period_type")))
    return res


class FigureCheck:
    """A figure (or a text value) the AI reports, checked step by step, in order. Each step either passes, filling in
    what later steps need, or raises Reject."""

    def __init__(self, doc, item, ans, frags, res):
        self.doc, self.item, self.comp, self.ans, self.frags, self.res = doc, item, item["comp"], ans, frags, res
        # the value fragment (value_fragment)
        self.raw, self.value_text = None, ""       # the AI's number as given, or its words for a text item
        self.carrier, self.voff, self.clen = None, None, 0   # the fragment that carries it, where it is, its length
        self.others = []                           # the context, column and units fragments (surroundings)
        self.row = None                            # the table row, as read by the code (read_table_row)
        # the period (claimed_period): the AI's claim, the rule's, the document's
        self.pend, self.ptype = None, ""
        self.rule_pt, self.rule_months, self.doc_end = item["comp"]["period_type"], None, None
        # the value in the rule's unit (value_in_rule_unit), and the table column it sits in (column_of_value)
        self.value, self.col, self.col_months = None, None, None
        # is the period confirmed, and how (latest_period, confirm_period)
        self.confirmed, self.how, self.ev = False, "", None

    def run(self):
        self.value_fragment()
        self.surroundings()
        self.read_table_row()
        self.measure()
        self.claimed_period()
        self.value_in_rule_unit()
        self.column_of_value()
        self.period_length()
        # recorded now, so that if a later step rejects the answer the result still shows what the AI gave
        self.res.update(value=self.value, value_text=self.value_text, period_end=self.pend.isoformat() if self.pend else "",
                        period_type=self.ptype)
        self.latest_period()
        self.derive()
        self.confirm_period()
        self.period_warnings()
        self.res.update(period_evidence=self.how or self.ev, period_confirmed=self.confirmed, status="verified")
        return self.res

    # -- the fragment that carries the number (or the words)
    def value_fragment(self):
        doc, comp, ans, res = self.doc, self.comp, self.ans, self.res
        vfr = [f for f in self.frags if f["role"] == "value"] or self.frags[:1]
        if not vfr:
            raise Reject("no fragment given")
        verify_fragments(doc, vfr)
        bad = [f for f in vfr if not f["ok"]]
        if bad:
            raise Reject("value fragment not in the document: " + "; ".join(f"“{f['text'][:90]}”" for f in bad))
        if comp.get("text"):
            self.value_text = as_str(ans.get("value_text") or ans.get("value")).strip()
            carrier = next((f for f in vfr if self.value_text and K.norm(self.value_text) in K.norm(f["text"])), None)
            if carrier is None:
                raise Reject(f"the AI's words (“{self.value_text[:60]}”) are not in its value fragment")
        else:
            try:
                self.raw = float(as_str(ans.get("value")).replace(",", "").replace("$", "").strip())
            except ValueError:
                raise Reject(f"no number given (value: {ans.get('value')!r})")
            cands = [f for f in vfr if K.number_supported(self.raw, f["text"])]
            carrier = cands[0] if cands else None
            if len(cands) > 1:
                # the AI gave the number twice (a sentence and a table row): check it where the code can read the column
                conv = convert(self.raw, ans.get("unit"), comp["unit"])[0]
                for f in cands:
                    rw = T.read_row(doc, f["offsets"][0], len(K.norm(f["text"])), self.raw)
                    if rw and rw.get("columns_match") and column_of(rw, conv, comp["unit"]) is not None:
                        carrier = f
                        break
            if carrier is None:
                raise Reject(f"the number {self.raw:g} is not in the value fragment")
        self.carrier = carrier
        self.voff = carrier["offsets"][0]
        self.clen = len(K.norm(carrier["text"]))
        res["carrier"] = carrier["text"]

    # -- context, column and units fragments must belong to this figure
    def surroundings(self):
        doc, res, voff = self.doc, self.res, self.voff
        others = [f for f in self.frags if f is not self.carrier and f["role"] in ("column", "units", "context")]
        for f in [f for f in others if len(K.norm(f["text"])) < 3]:     # e.g. a column heading given as just "Q1"
            f.update(ok=True, ignored=True)
            res["warnings"].append(f"a {f['role']} fragment is too short to check (“{f['text']}”) and was ignored")
        others = [f for f in others if not f.get("ignored")]
        verify_fragments(doc, [f for f in others if f["role"] != "units"], near=voff)
        # Units lines apply to everything below them (a note inherits the statements' "(in millions)"), so a units
        # fragment may sit anywhere above the figure, but it must say the same as the nearest units line above it.
        nearest_units = T.units_above(doc, voff)
        for f in [f for f in others if f["role"] == "units"]:
            verify_fragments(doc, [f], near=voff, before=voff + 1, after=0)
            said = T.units_of(f["text"])
            if f["ok"] and said and nearest_units and said != nearest_units:
                f.update(ok=False, far=False)
                raise Reject(f"units fragment says '{said}', but the nearest units line above the figure says '{nearest_units}'")
        # A context fragment found elsewhere in the document is kept as separate evidence (verified on its own), but only
        # nearby context counts towards naming the measure. Column and units lines must be next to the figure.
        for f in others:
            if not f["ok"] and f.get("far") and f["role"] == "context":
                f["ok"], f["elsewhere"] = True, True
                res["warnings"].append(f"a context fragment is elsewhere in the document, not next to the figure: kept as "
                                       f"separate evidence, not used to identify the measure (“{f['text'][:60]}”)")
        bad = [f for f in others if not f["ok"]]
        if bad:
            raise Reject("; ".join(f"{f['role']} fragment {'not near the figure' if f.get('far') else 'not in the document'}: "
                                   f"“{f['text'][:80]}”" for f in bad))
        for f in self.frags:
            if f.get("match") == "ignoring spaces":
                res["warnings"].append(f"fragment matched only when spaces are ignored: “{f['text'][:60]}”")
        self.others = others

    def read_table_row(self):
        doc = self.doc
        self.row = T.read_row(doc, self.voff, self.clen, self.raw) if self.raw is not None else T.read_row(doc, self.voff)
        self.res["table"] = self.row

    # -- the measure (wrong-metric guard): around the fragment that carries the number
    def measure(self):
        doc, comp, row, voff = self.doc, self.comp, self.row, self.voff
        if row:
            where = row["label"] + " " + row["header"]
            above = " ".join(doc.lines[k] for k in range(doc.line_of(voff), row.get("line", 0)))
            if above and not re.search(r"\d", above) and len(K.norm(above)) <= 80:
                where += " " + above                   # a heading at the start of the value fragment ('AWS' over 'Net sales | …')
        else:
            where = doc.window(voff, before=400, after=50, length=self.clen)
        close = [f for f in self.others if f["role"] == "context" and f["offsets"] and voff - 1500 <= f["offsets"][0] <= voff + 300]
        where += " " + " ".join(f["text"] for f in close)
        missing = K.mentions(where, comp.get("must"))
        banned = [p for p in comp.get("must_not") or [] if K.mentions(self.carrier["text"], [[p]]) == []]
        if missing:
            raise Reject("wrong measure: the text around the figure does not say " +
                         "; ".join(" or ".join(f"'{p.rstrip('*')}'" for p in g) for g in missing))
        if banned:
            raise Reject(f"wrong measure: the figure is described as {', '.join(repr(b) for b in banned)}")

    # -- the period the AI claims, and the rule's
    def claimed_period(self):
        doc, comp, ans, res = self.doc, self.comp, self.ans, self.res
        self.ptype = as_str(ans.get("period_type")).strip().lower()
        self.pend = K.parse_date(as_str(ans.get("period_end")))
        self.rule_pt = rule_pt = comp["period_type"]
        self.rule_months = MONTHS_IN.get(rule_pt) if not comp.get("derive") else None
        self.doc_end = doc_end = K.parse_date(doc.period_end)
        self.confirmed = rule_pt in ("said", "event", "policy")
        if rule_pt == "said":
            self.pend = K.parse_date(doc.date) or self.pend      # a statement is dated by the document it is in
            self.confirmed = bool(K.parse_date(doc.date))
        elif rule_pt == "policy" and not self.pend:
            self.pend = doc_end                                   # a policy stated in a filing is in force at its date
        if not self.pend and rule_pt in LATEST_RULE and doc_end and not comp.get("comparative"):
            self.pend = doc_end
            res["warnings"].append(f"the AI gave no period: taken as the document's period ({doc_end:%d %b %Y}); check it by hand")

    # -- the number in the rule's unit, on the scale it is printed on
    def value_in_rule_unit(self):
        comp, res, raw = self.comp, self.res, self.raw
        if comp.get("text"):
            return
        value, note = convert(raw, self.ans.get("unit"), comp["unit"])
        if note:
            res["warnings"].append(note)
        res["raw_value"] = raw
        # a percentage must be on the same scale as it is printed: the AI giving 0.2 for "20 %" is not a change to 0.2
        pcts = [n for kind, n, _ in K.figures_in(self.carrier["text"]) if kind == "pct"]
        if comp["unit"] in ("%", "share") and pcts and value is not None:
            as_pct = value if comp["unit"] == "%" else value * 100
            near = lambda a, b: abs(a - b) <= 0.0051 * max(abs(b), 1e-9)
            if not any(near(as_pct, p) for p in pcts):
                fixed = next((f for f in (100, 0.01) if any(near(as_pct * f, p) for p in pcts)), None)
                if fixed is None:
                    raise Reject(f"the value {value:g} is not on the scale of the percentage printed in the fragment")
                value = value * fixed
                res["warnings"].append(f"the AI gave {raw:g} for a percentage printed as "
                                       f"{K.fmt(as_pct * fixed)}%; read as {K.fmt(value)} {comp['unit']}")
        self.value = value

    # -- the column the number sits in, as read by the code (its date and length come from the header, not the AI)
    def column_of_value(self):
        row = self.row
        ai_col = column_of(row, self.value, self.comp["unit"]) if self.value is not None else None
        self.col = row["cols"][ai_col] if (ai_col is not None and row.get("cols")) else None
        self.col_months = self.col.get("months") if self.col else None

    # -- period length
    def period_length(self):
        comp, rule_pt, ptype = self.comp, self.rule_pt, self.ptype
        if rule_pt in ("3m", "6m", "9m", "12m", "ttm") and not comp.get("derive"):
            if ptype and ptype != rule_pt and not (rule_pt == "12m" and ptype == "ttm") and not (rule_pt == "ttm" and ptype == "12m"):
                raise Reject(f"wrong period length: the rule needs a {rule_pt} figure, the AI gave {ptype}")
        if (comp.get("derive") or rule_pt == "ytd") and ptype not in MONTHS_IN:
            # "ytd" alone does not say how long the period is: read the length from the column
            heads = " ".join(f["text"] for f in self.others if f["role"] == "column") + " " + ((self.row or {}).get("header") or "")
            found = {k for k, w in (("3m", "three months"), ("6m", "six months"), ("9m", "nine months"), ("12m", "twelve months"))
                     if w in K.norm(heads)}
            if self.col_months in (3, 6, 9, 12):
                self.ptype = f"{self.col_months}m"
                self.res["warnings"].append(f"the AI gave the period as '{as_str(self.ans.get('period_type'))}'; its length "
                                            f"({self.ptype}) was read from the column")
            elif len(found) == 1:
                self.ptype = found.pop()
                self.res["warnings"].append(f"the AI gave the period as '{as_str(self.ans.get('period_type'))}'; its length "
                                            f"({self.ptype}) was read from the column heading")
            else:
                raise Reject(f"period length missing or unclear ('{ptype}'): needed for the code's arithmetic")

    # -- the latest period (wrong-period guard)
    def latest_period(self):
        if not (self.rule_pt in LATEST_RULE and self.doc_end):
            return
        if self.comp.get("comparative"):
            self.comparative_period()
        else:
            self.latest_column_check()

    def comparative_period(self):
        """A prior-year comparative figure must be for the same period a year before the document's."""
        col, doc_end, pend, rule_months = self.col, self.doc_end, self.pend, self.rule_months
        want = (doc_end.year - 1) * 12 + doc_end.month - 1
        if pend and (pend.year * 12 + pend.month - 1) != want:
            raise Reject(f"wrong period: a comparative figure should be for the period a year before "
                         f"{doc_end:%d %b %Y}, the AI gave {pend:%d %b %Y}")
        if col and col.get("end") == (doc_end.year - 1, doc_end.month) and (not rule_months or self.col_months in (None, rule_months)):
            self.confirmed, self.how = True, f"column “{col['label']}” read by the code"

    def latest_column_check(self):
        """The figure must be for the document's latest period (and the rule's length). If the AI took an older or
        wrong-length column of a table the code can read, the code reads the right column of the same verified row."""
        comp, res, row, col, doc_end = self.comp, self.res, self.row, self.col, self.doc_end
        rule_months, col_months, pend = self.rule_months, self.col_months, self.pend
        value = self.value
        latest_col = None
        if value is not None:
            latest_col = latest_column(row, doc_end, comp["unit"], rule_months, col["kind"] if col else "period")
        col_mk = col.get("end") if col else None
        if col_mk and col_mk == K.month_key(pend or doc_end) and (not rule_months or col_months in (None, rule_months)):
            self.confirmed = True
            self.how = f"column “{col['label']}”" + (f" (the change for {col['of']})" if col.get("of") else "") + " read by the code"
        used_old = (pend and K.month_key(pend) < K.month_key(doc_end)) or (col_mk is not None and col_mk < K.month_key(doc_end))
        wrong_len = bool(rule_months and col_months and col_months != rule_months)
        if not (used_old or wrong_len):
            return
        used = ((col.get("day") or K.month_end(*col_mk)) if col_mk else None) or pend
        if used_old:
            msg = f"wrong period: the AI used the {used:%d %b %Y} figure; the document's latest period is {doc_end:%d %b %Y}"
        else:
            msg = (f"wrong period length: the AI used the {col_months}-month column (“{col['label']}”); "
                   f"the rule needs the {rule_months}-month figure")
        res["wrong_period"] = dict(ai_value=value, ai_period=used.isoformat() if used else "", ai_months=col_months,
                                   matches_paper_for_that_date=(value is not None and used is not None and not wrong_len
                                                                and K.month_key(used) == K.month_key(K.parse_date(comp["period_end"]))
                                                                and K.compare(comp["value"], round(value, 4)) == "same"))
        if latest_col is None:
            raise Reject(msg + (" (the code could not read this table's columns: check by hand)" if row
                                else " (the figure is not in a table the code can read: check by hand)"))
        # The row itself is verified text: the code reads the latest column instead of trusting the AI's choice.
        res["ai_problem"] = msg
        res["code_read"] = True
        self.value, self.pend, self.confirmed = latest_col[0], K.parse_date(latest_col[1]), True
        self.how = f"column “{latest_col[2]}” read by the code"
        if rule_months:
            self.ptype = self.rule_pt
        res.update(value=self.value, period_end=self.pend.isoformat(), period_type=self.ptype)
        res["warnings"].append(f"the AI used {'an older' if used_old else 'the wrong-length'} column; the value shown was read by "
                               f"the code from the “{latest_col[2]}” column of the same verified table row")

    # -- arithmetic the rule needs (annualising, a formula in x)
    def derive(self):
        comp, value = self.comp, self.value
        if comp.get("derive") and value is not None:
            months = MONTHS_IN[self.ptype]
            if comp["derive"] == "annualise":
                self.res["derived"] = dict(formula=f"x × 12 / {months}", x=value, value=value * 12 / months)
            else:
                self.res["derived"] = dict(formula=comp["derive"], x=value, value=R.evaluate(comp["derive"], {"x": value}))

    # -- is the period confirmed? (a column the code read, a date next to the figure, or the quarter a call reports on)
    def confirm_period(self):
        doc, comp, res, pend, doc_end = self.doc, self.comp, self.res, self.pend, self.doc_end
        self.ev = K.period_evidence(doc, self.voff, self.clen, pend, self.ptype,
                                    tuple(d for d in ((self.row or {}).get("dates") or ()) if d))
        if self.ev:
            self.confirmed = True
            self.how = self.how or self.ev
        # a sentence in a call transcript or a results release: the document reports on one quarter, and a sentence
        # that names no other period (no other date, quarter or year, no outlook) is about that quarter
        about_latest = bool(pend and doc_end and K.month_key(pend) == K.month_key(doc_end))
        if not self.confirmed and self.rule_pt in LATEST_RULE and about_latest and not comp.get("comparative") \
                and doc.form in ("transcript", "8-K"):
            other = other_period(self.carrier["text"], doc, doc_end)
            if other:
                res["warnings"].append(f"the sentence mentions another period ({other}): the period is not confirmed")
            else:
                self.confirmed = True
                q = getattr(doc, "fiscal_quarter", "") or T.quarter_label(doc_end, getattr(doc, "fye", None))
                self.how = (f"the {'call' if doc.form == 'transcript' else 'release'} reports on {q + ', ' if q else ''}the quarter to "
                            f"{doc_end:%b %Y}, and the sentence names no other period")

    def period_warnings(self):
        doc, res, pend, ptype, rule_pt = self.doc, self.res, self.pend, self.ptype, self.rule_pt
        if rule_pt in LATEST_RULE and pend and not self.confirmed and not res.get("code_read"):
            res["warnings"].append(f"the period ({pend:%d %b %Y}) is not visible next to the figure: check it by hand")
        words = PERIOD_WORDS.get(ptype)
        length_from_column = bool(self.col_months and MONTHS_IN.get(ptype) == self.col_months)
        if words and rule_pt not in ("said", "event", "policy") and not length_from_column and not res.get("code_read"):
            near_txt = doc.window(self.voff, before=6000, after=100, length=self.clen) + " " + ((self.row or {}).get("header") or "")
            if not any(w in near_txt for w in words):
                res["warnings"].append(f"the period length ({ptype}) is not visible near the figure: check it by hand")


def other_period(text, doc, doc_end):
    """Does a sentence name a period other than the document's quarter? Returns what it names, or ''."""
    t = K.norm(text)
    for d in K.dates_in(text):
        if K.month_key(d) != K.month_key(doc_end):
            return f"{d:%d %b %Y}"
    m = re.search(r"\b(?:last|prior|previous|next|coming|same) (?:quarter|year|fiscal year)\b|\bfull[- ]year\b|\bfiscal year\b|"
                  r"\b(?:this|next|last) year\b|\byear[- ]to[- ]date\b|\bytd\b|\bttm\b|\btrailing\b|\b(?:six|nine|twelve) months\b|"
                  r"\b(?:next|last|past|prior|coming) \d+ (?:months|quarters|years)\b|"
                  r"\bwill\b|\bexpect|\bguid|\boutlook|\bforecast|\banticipat", t)
    if m:
        return f"“{m.group(0)}”"
    qf = T.quarter_of(doc_end, getattr(doc, "fye", None))
    for m in re.finditer(r"\b(first|second|third|fourth) quarter\b|\bq([1-4])\b", t):
        q = T.QUARTER_WORDS.get(m.group(1)) if m.group(1) else int(m.group(2))
        if not qf or q != qf[0]:
            return f"“{m.group(0)}”"
    for m in re.finditer(r"\b(?:fy|fiscal) ?'?(\d{4}|\d{2})\b|\b(20\d{2})\b", t):
        y = int(m.group(1) or m.group(2))
        y = y + 2000 if y < 100 else y
        if not qf or (m.group(1) and y != qf[1]) or (m.group(2) and y not in (doc_end.year, qf[1])):
            return f"“{m.group(0)}”"
    return ""


# ------------------------------------------------------------------------------------------
# Table cells in the rule's unit
# ------------------------------------------------------------------------------------------
def cell_in_unit(row, i, unit):
    """Cell i of a table row in the rule's unit ($bn or $m converted from the table's units)."""
    if not row or row["values"][i] is None:
        return None
    if unit in ("$bn", "$m"):
        return T.to_unit(row["values"][i], row["units"], unit)
    return row["values"][i]


def latest_column(row, doc_end, unit, months=None, kind="period"):
    """(value, date, column label) of the column for the document's latest period (and the rule's length, when the
    header gives lengths), of the same kind as the AI's column (a figure, or a change such as Y/Y). None if unclear."""
    if not row or not row.get("columns_match"):
        return None
    cols = row.get("cols") or []
    idx = [i for i, c in enumerate(cols) if c.get("end") == K.month_key(doc_end) and c.get("kind") == kind
           and (not months or c.get("months") in (None, months))]
    if months:
        exact = [i for i in idx if cols[i].get("months") == months]
        if exact:
            idx = exact
        elif len(idx) > 1:
            return None
    vals = {round(cell_in_unit(row, i, unit), 6) for i in idx if cell_in_unit(row, i, unit) is not None}
    if not idx or len(vals) != 1:
        return None
    return vals.pop(), doc_end.isoformat(), cols[idx[0]]["label"]


def column_of(row, value, unit):
    """Which column of a readable table row holds this value (in the rule's unit)? None if unclear."""
    if not row or not row.get("columns_match") or value is None:
        return None
    hits = []
    for i in range(len(row["values"])):
        c = cell_in_unit(row, i, unit)
        if c is not None and abs(c - value) <= 0.0005 * max(abs(value), 1):
            hits.append(i)
    cols = row.get("cols") or []
    if len(hits) > 1 and cols and len({(cols[i].get("end"), cols[i].get("months"), cols[i].get("kind")) for i in hits}) == 1:
        return hits[0]                                   # the same figure twice for the same period (Q1 and year to date)
    return hits[0] if len(hits) == 1 else None
