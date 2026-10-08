"""
Reading a table row by code, so the choice of column never rests on the AI: the cells of the row, the period of each
column (from dates such as "June 30, 2026 | December 31, 2025", grouped headings such as "Three Months Ended |
Six Months Ended", or fiscal labels such as "Q2 FY27" and "Fiscal 2027 / Q1 ... TOTAL"), and the units line.
"""

import re

from .checks import MONTHS, MONTH_RE, NUM_CELL, make_date, month_end, norm, number_supported, numbers_in

# ------------------------------------------------------------------------------------------
# Fiscal periods ('Q2 FY27', 'Fiscal 2027 Q1', 'Second Quarter 2027', 'FY26 Q4')
# ------------------------------------------------------------------------------------------
QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def fiscal_quarter_end(q, fy, fye):
    """(year, month) in which quarter q of fiscal year fy ends, for a company whose fiscal year ends in month fye.
    Fiscal year N is the one that ends in calendar year N: Nvidia's FY2027 ends in January 2027, so its Q2 FY27
    ends in July 2026; Oracle's FY2027 ends in May 2027, so Q1 FY27 ends in August 2026."""
    total = fy * 12 + (fye - 1) - (4 - q) * 3
    return total // 12, total % 12 + 1


def _year(s):
    y = int(s)
    return y + 2000 if y < 100 else y


def label_period(label, fye, fiscal_if_plain=False):
    """What period a column label or document title names. Returns dict(end=(year, month), months=3 or 12,
    label=...) or None. 'Q2 2026' without 'FY'/'fiscal' is read only for a company whose fiscal year is the
    calendar year (or when fiscal_if_plain: an investor site's own report titles, which use fiscal years)."""
    n = norm(label).replace("-", " ").replace("_", " ").strip(" .:|")
    n = re.sub(r"\s+", " ", n)
    if not fye:
        return None
    plain_ok = fye == 12 or fiscal_if_plain
    m = (re.fullmatch(r"q([1-4])\s*(?:fy|fiscal(?: year)?)\s*'?(\d{4}|\d{2})", n)
         or re.fullmatch(r"(?:fy|fiscal(?: year)?)\s*'?(\d{4}|\d{2})\s*q([1-4])", n))
    if m:
        q, y = (m.group(1), m.group(2)) if n.startswith("q") else (m.group(2), m.group(1))
        return dict(end=fiscal_quarter_end(int(q), _year(y), fye), months=3, label=label.strip())
    m = re.fullmatch(r"(first|second|third|fourth) quarter(?: of)?( fiscal)?(?: year)? (\d{4})", n)
    if m and (m.group(2) or plain_ok):
        return dict(end=fiscal_quarter_end(QUARTER_WORDS[m.group(1)], int(m.group(3)), fye), months=3, label=label.strip())
    m = re.fullmatch(r"q([1-4])\s*(\d{4})", n)
    if m and plain_ok:
        return dict(end=fiscal_quarter_end(int(m.group(1)), int(m.group(2)), fye), months=3, label=label.strip())
    m = re.fullmatch(r"(?:fy|fiscal(?: year)?)\s*'?(\d{4}|\d{2})", n)
    if m:
        return dict(end=(_year(m.group(1)), fye), months=12, label=label.strip(), fy=_year(m.group(1)), fye=fye)
    return None


def fiscal_quarter_in(text, fye, fiscal_if_plain=True):
    """The fiscal quarter named in a title or link, e.g. 'Second Quarter 2027' or '.../earnings-fy-2026-q4'.
    Returns dict(end=(y, m), months=3, label='Q2 FY2027') or None."""
    t = norm(text).replace("-", " ").replace("_", " ").replace("/", " ")
    pats = [(r"\b(first|second|third|fourth) quarter(?: of)?( fiscal)?(?: year)? (\d{4})\b", lambda m: (QUARTER_WORDS[m.group(1)], m.group(3))),
            (r"\bq([1-4]) ?(?:fy|fiscal(?: year)?) ?'?(\d{4}|\d{2})\b", lambda m: (m.group(1), m.group(2))),
            (r"\b(?:fy|fiscal(?: year)?) ?'?(\d{4}|\d{2}) ?q([1-4])\b", lambda m: (m.group(2), m.group(1))),
            (r"\bq([1-4]) (\d{4})\b", lambda m: (m.group(1), m.group(2)))]
    for pat, get in pats:
        m = re.search(pat, t)
        if m:
            q, y = get(m)
            p = label_period(f"q{q} fy{y}", fye) if fiscal_if_plain else label_period(m.group(0), fye)
            if p:
                return dict(p, label=f"Q{q} FY{_year(y)}")
    return None


def quarter_of(doc_end, fye):
    """Which fiscal quarter ends in doc_end's month, and its fiscal year: (q, fy), or None."""
    if not (doc_end and fye):
        return None
    k = (doc_end.month - fye - 1) % 12
    if k % 3 != 2:
        return None
    q = k // 3 + 1
    fy = doc_end.year + (1 if doc_end.month > fye else 0)
    return q, fy


def quarter_label(doc_end, fye):
    """'Q2 FY2027' for the quarter that ends in this month, or ''."""
    qf = quarter_of(doc_end, fye)
    return f"Q{qf[0]} FY{qf[1]}" if qf else ""


# ------------------------------------------------------------------------------------------
# Column headings
# ------------------------------------------------------------------------------------------
_HEADERISH_DROP = re.compile(r"\b(%s)|\b\d{1,2}(st|nd|rd|th)?\b|\b\d{4}\b|\bended\b|\bas of\b|\b(three|six|nine|twelve) months\b|"
                             r"\bquarter\b|\byear\b|\bfiscal\b|\bat\b|\band\b|\bunaudited\b|\bto[- ]date\b|\bytd\b|[|,()-]" % MONTH_RE)


def _headerish(line):
    n = norm(line)
    if not n or not (re.search(r"\b" + MONTH_RE, n) or re.fullmatch(r"(\d{4}\s*)+", n)):
        return False
    rest = re.sub(r"\s+", "", _HEADERISH_DROP.sub(" ", n))
    return len(rest) <= 3


_LEN_WORDS = {"three": 3, "six": 6, "nine": 9, "twelve": 12}


def _header_columns(block, fye=None):
    """Columns of a date header, in order: [(date, months or None)]. Handles 'June 30, 2026 | December 31, 2025',
    'December 31, | June 30,' over '2025 | 2026', 'June 30,' over '2026 | 2025', and grouped headers such as
    'Three Months Ended June 30, | Six Months Ended June 30,' over '2025 | 2026 | 2025 | 2026'. The length of each
    column (3, 6, 9 or 12 months) comes from the words before its date ('three months ended', 'year to date')."""
    toks, cur = [], None
    pat = (r"\b(three|six|nine|twelve) months\b|\bquarters? ended\b|\b(?:fiscal )?years? ended\b|\byear to date\b|\bytd\b|"
           r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?\b,?|\b(\d{4})\b" % MONTH_RE)
    for m in re.finditer(pat, norm(block)):
        if m.group(2):
            toks.append(("md", MONTHS[m.group(2).rstrip(".")], int(m.group(3)), cur))
        elif m.group(4):
            toks.append(("y", int(m.group(4))))
        else:
            w = m.group(0)
            cur = (_LEN_WORDS[m.group(1)] if m.group(1) else 3 if w.startswith("quarter") else 12 if "ended" in w else "ytd")
    mds = [t for t in toks if t[0] == "md"]
    ys = [t for t in toks if t[0] == "y"]

    def col(md, y):
        d = make_date(y[1], md[1], md[2])
        months = md[3]
        if months == "ytd":
            months = ((md[1] - fye) % 12 or 12) if fye else None
        return (d, months)

    if toks and all(t[0] == ("md" if k % 2 == 0 else "y") for k, t in enumerate(toks)) and len(toks) % 2 == 0:
        out = [col(toks[k], toks[k + 1]) for k in range(0, len(toks), 2)]       # alternating month-day, year
    elif mds and len(mds) == len(ys):
        out = [col(md, y) for md, y in zip(mds, ys)]
    elif len(mds) == 1 and len(ys) > 1:                                          # 'June 30,' over '2026 | 2025'
        out = [col(mds[0], y) for y in ys]
    elif len(mds) > 1 and len(ys) % len(mds) == 0:                               # grouped: each date over its own years
        per = len(ys) // len(mds)
        out = [col(md, y) for g, md in enumerate(mds) for y in ys[g * per:(g + 1) * per]]
    else:
        return []
    return [c for c in out if c[0]]


_CHANGE_RE = re.compile(r"(?:q/q|y/y|yoy|qoq|y-o-y|q-o-q)(?: %)?(?: change)?|%? ?change|% chg|chg")


def _label_cell(cell, fye):
    """One header cell of a table headed by fiscal labels. ('period', p) for 'Q2 FY27' or 'Q2 2026',
    ('year', p) for 'Fiscal 2027', ('q', n) for a bare 'Q1', ('total', None), ('change', None); None otherwise."""
    n = norm(cell).strip(" .:")
    if not n:
        return None
    if re.fullmatch(r"q[1-4]", n):
        return ("q", int(n[1]))
    if n == "total":
        return ("total", None)
    if _CHANGE_RE.fullmatch(n):
        return ("change", {"label": cell.strip()})
    p = label_period(cell, fye)
    if p:
        return ("year" if p["months"] == 12 else "period", p)
    return None


def _label_line(line, fye):
    """A header line made only of fiscal labels (a leading units cell such as '($ in millions)' is allowed)."""
    cells = [c.strip() for c in line.split("|")]
    if cells and (units_of(cells[0]) or not norm(cells[0])):
        cells = cells[1:]
    if not cells:
        return None
    parsed = [_label_cell(c, fye) for c in cells]
    return parsed if all(parsed) else None


def _sum_split(values, n_groups, per):
    """'Fiscal 2026 | Fiscal 2027' over 'Q1 Q2 Q3 Q4 TOTAL' twice, where the quarters not yet reported are empty
    (so the row has fewer numbers than the header). Split the row into each year's reported quarters and its total,
    using the fact that the quarters add up to the total. Returns [k quarters per group] or None if not unique."""
    out, pos = [], 0
    for g in range(n_groups):
        fits = []
        for k in range(1, per):
            if pos + k < len(values) and None not in values[pos:pos + k + 1]:
                if abs(sum(values[pos:pos + k]) - values[pos + k]) <= 0.5 * k + 1e-6 and values[pos + k] != 0:
                    fits.append(k)
        if len(fits) != 1:
            return None
        out.append(fits[0])
        pos += fits[0] + 1
    return out if pos == len(values) else None


def _label_columns(block, values):
    """Columns (aligned to the row's values) of a table headed by fiscal labels, or None if they cannot be aligned."""
    flat = [c for line in block for c in line]
    years = [p for k, p in flat if k == "year"]
    rest = [(k, p) for k, p in flat if k != "year"]
    cols = []
    if years and rest and all(k in ("q", "total") for k, _ in rest) and len(rest) % len(years) == 0:
        per = len(rest) // len(years)
        groups = [(years[g], rest[g * per:(g + 1) * per]) for g in range(len(years))]
        if len(rest) == len(values):
            ks = [None] * len(groups)
        elif [k for k, _ in rest[:per]] == ["q"] * (per - 1) + ["total"]:
            ks = _sum_split(values, len(groups), per)
            if ks is None:
                return None
        else:
            return None
        for (yp, labs), k in zip(groups, ks):
            fy, fye = yp["fy"], yp["fye"]
            quarters = [p for kind, p in labs if kind == "q"]
            if k is not None:
                quarters = quarters[:k]
                labs = [("q", q) for q in quarters] + [("total", None)]
            for kind, p in labs:
                if kind == "q":
                    cols.append(dict(kind="period", end=fiscal_quarter_end(p, fy, fye), months=3, label=f"Q{p} FY{fy}"))
                else:
                    n = len(quarters)
                    cols.append(dict(kind="period", end=fiscal_quarter_end(n, fy, fye), months=3 * n,
                                     label=f"FY{fy} total ({n} quarter{'s' if n > 1 else ''})"))
    else:
        if len(flat) != len(values):
            return None
        for kind, p in flat:
            if kind in ("period", "year"):
                cols.append(dict(kind="period", end=p["end"], months=p["months"], label=p["label"]))
            else:
                cols.append(dict(kind=kind, end=None, months=None, label=p["label"] if isinstance(p, dict) else kind))
    return cols if len(cols) == len(values) else None


def _change_periods(cols):
    """A change column (Q/Q, Y/Y, % change) belongs to the latest dated column since the previous change column."""
    group, prev_change = [], False
    for c in cols:
        if c["kind"] == "change":
            dated = [x for x in group if x.get("end")]
            if dated:
                best = max(dated, key=lambda x: x["end"])
                c.update(end=best["end"], months=best["months"], of=best["label"])
            prev_change = True
        else:
            if prev_change:
                group = []
            group.append(c)
            prev_change = False
    return cols


def _find_header(doc, li, fye):
    """Walk up from the row to its header: date lines ('June 30, | 2026 | 2025'), or fiscal labels ('Q2 FY27'),
    whichever comes first. Returns (kind, parsed, raw lines): ('dates', lines, lines), ('labels', parsed, lines)
    or (None, None, [])."""
    date_lines, seen_date, j = [], False, li - 1
    while j >= 0 and li - j <= 400:
        ln = doc.lines[j]
        n = norm(ln)
        if not n:
            j -= 1
            continue
        lab = _label_line(ln, fye) if (fye and not seen_date) else None
        if lab is not None and any(k in ("period", "year") for k, _ in lab) or \
                (lab is not None and any(k == "q" for k, _ in lab) and _near_year_line(doc, j, fye)):
            block, raw, k = [lab], [ln], j - 1
            while k >= 0 and j - k <= 20:
                if not norm(doc.lines[k]):
                    k -= 1
                    continue
                lab2 = _label_line(doc.lines[k], fye)
                if lab2 is None:
                    break
                block.insert(0, lab2)
                raw.insert(0, doc.lines[k])
                k -= 1
            above, a = [], k
            while a >= 0 and len(above) < 2 and k - a <= 6:   # the words just above the labels ('Quarter Ended | TTM')
                if norm(doc.lines[a]):
                    above.insert(0, doc.lines[a])
                a -= 1
            k = j + 1                             # and any label lines just below (a trailing 'TOTAL', 'Y/Y')
            while k < li:
                if not norm(doc.lines[k]):
                    k += 1
                    continue
                lab2 = _label_line(doc.lines[k], fye)
                if lab2 is None:
                    break
                block.append(lab2)
                raw.append(doc.lines[k])
                k += 1
            return "labels", block, raw, above
        if li - j <= 120 and _headerish(ln):
            date_lines.insert(0, ln)
            seen_date = True
        elif seen_date and not (units_of(ln) or n in ("(unaudited)", "unaudited")):
            break                                 # only the header block nearest to the row (not an earlier table's)
        elif "|" not in ln and not NUM_CELL.match(n.replace("$", "").strip()) and len(n) > (60 if seen_date else 100):
            break                                 # a sentence: above the header block, or no header in this table
        if seen_date and len(date_lines) >= 8:
            break
        j -= 1
    return ("dates", date_lines, date_lines, []) if date_lines else (None, None, [], [])


def _near_year_line(doc, j, fye):
    """Is a bare 'Q1' line part of a 'Fiscal 2027' / 'Q1 Q2 Q3 Q4 TOTAL' header block?"""
    k = j
    while k >= 0 and j - k <= 20:
        lab = _label_line(doc.lines[k], fye) if norm(doc.lines[k]) else []
        if lab is None:
            return False
        if any(kind == "year" for kind, _ in lab):
            return True
        k -= 1
    return False


# ------------------------------------------------------------------------------------------
# The row itself
# ------------------------------------------------------------------------------------------
def _row_cells(doc, li):
    """(label, numeric cells) of the table row on line li, or None."""
    line = doc.lines[li]
    cells = []
    if "|" in line:
        parts = [p.strip() for p in line.split("|")]
        label = parts[0]
        for p in parts[1:]:
            p2 = norm(p).replace("$", "").strip()
            if NUM_CELL.match(p2 or "x"):
                cells.append(p2)
        return (label, cells) if cells else None
    label = line.strip()
    if len(norm(label)) > 120:            # a sentence, not a table label
        return None
    # numbers on the same line after the label: 'Deferred revenue 3.0 4,395.0'
    toks = norm(label).split(" ")
    tail = []
    while toks and NUM_CELL.match(toks[-1].replace("$", "")) and not toks[-1].endswith(","):
        tail.insert(0, toks.pop().replace("$", ""))
    if len(tail) >= 2 and toks and re.search(r"[a-z]", toks[-1]) and not re.fullmatch(MONTH_RE, toks[-1]):
        return (" ".join(toks), tail)
    # one cell per line: the label, then each number on its own line
    j = li + 1
    while j < len(doc.lines) and len(cells) < 12:
        p2 = norm(doc.lines[j]).replace("$", "").strip()
        if not p2:
            j += 1
            continue
        if NUM_CELL.match(p2):
            cells.append(p2)
            j += 1
            continue
        break
    return (label, cells) if cells else None


def read_row(doc, offset, length=0, value=None):
    """The table row at this offset: its cells, the period of each column (read by the code from the header above
    it: dates, 'Three Months Ended', or fiscal labels such as 'Q2 FY27'), and the units line.
    A fragment that spans several lines (a segment name on one line, its row on the next) is read at the line
    that holds the value. Returns None when the fragment is not a table row the code can read."""
    li = doc.line_of(offset)
    if length and value is not None and not ("|" in doc.lines[li] and number_supported(value, doc.lines[li])):
        li_end = doc.line_of(offset + max(length - 1, 0))
        for k in range(li + 1, li_end + 1):
            if "|" in doc.lines[k] and number_supported(value, doc.lines[k]):
                li = k
                break
    got = _row_cells(doc, li)
    if not got:
        return None
    label, cells = got
    values = []
    for c in cells:
        if re.fullmatch(r"[-—–]", c):
            values.append(0.0)
        else:
            nums = numbers_in(c)
            values.append(nums[0] if nums else None)
    fye = getattr(doc, "fye", None)
    kind, block, raw, above = _find_header(doc, li, fye)
    cols, header = None, " | ".join(norm(h) for h in raw)
    if kind == "labels":
        cols = _label_columns(block, values)
        if cols:
            labels = [c["label"].lower() for c in cols if c["kind"] == "period"]
            # a second header line such as 'Quarter Ended | TTM' over 'Q1 2026 | Q2 2026 | Q2 2026': the dates are
            # right, but which columns are quarters and which are longer periods cannot be told by position
            if len(labels) != len(set(labels)) or re.search(r"\bttm\b|trailing|year to date|\bytd\b|(six|nine|twelve) months|full year",
                                                             " ".join(norm(h) for h in above)):
                for c in cols:
                    if c["kind"] == "period" and not c["label"].startswith("FY"):
                        c.update(months=None, months_unclear=True)
            _change_periods(cols)
    elif kind == "dates":
        hc = _header_columns(header, fye)
        if hc and len(hc) == len(values):
            cols = [dict(kind="period", end=(d.year, d.month), day=d, months=m, label=f"{d:%d %b %Y}") for d, m in hc]
    if cols:
        # the row's own label can say how long its figures run: 'Free cash flow -- TTM' under quarter columns
        ln = norm(label)
        rl = 12 if re.search(r"\bttm\b|trailing (twelve|12) months", ln) else \
            "?" if re.search(r"\b(three|six|nine|twelve) months\b|year[- ]to[- ]date|\bytd\b", ln) else None
        if rl:
            for c in cols:
                if c.get("end"):
                    c.update(months=rl if rl != "?" else None, months_from_label=True)
    dates = [(c.get("day") or month_end(*c["end"])) if c.get("end") else None for c in cols] if cols else \
        ([d for d, _ in _header_columns(header, fye)] if kind == "dates" else [])
    units = units_above_line(doc, li)
    return {"label": label, "cells": cells, "values": values, "dates": dates, "cols": cols or [],
            "header": header, "units": units, "line": li,
            "columns_match": bool(cols) and len(cols) == len(values) and any(d for d in dates)}


# ------------------------------------------------------------------------------------------
# Units ('(in millions)')
# ------------------------------------------------------------------------------------------
_UNITS_RE = re.compile(r"\b(?:in|\$ in|amounts in|dollars in)\s+(millions|billions|thousands)\b")


def units_of(text):
    """'millions', 'billions' or 'thousands' if the text is a units line such as '(in millions)', else None."""
    m = _UNITS_RE.search(norm(text))
    return m.group(1) if m else None


def units_above(doc, offset):
    """The units stated by the nearest units line at or above this point (notes often inherit the statements' units)."""
    return units_above_line(doc, doc.line_of(offset))


def units_above_line(doc, li):
    """The units stated by the nearest units line at or above line li."""
    for k in range(li, -1, -1):
        u = units_of(doc.lines[k])
        if u:
            return u
    return None


def to_unit(value, units, target):
    """Convert a table number printed in `units` (millions/thousands/billions) to the target unit ($bn or $m)."""
    if value is None:
        return None
    scale = {"millions": 1e6, "thousands": 1e3, "billions": 1e9}.get(units or "", None)
    if scale is None:
        return None
    tgt = {"$bn": 1e9, "$m": 1e6}.get(target)
    return value * scale / tgt if tgt else None
