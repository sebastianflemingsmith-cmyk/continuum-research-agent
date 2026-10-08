"""
Checks on the text itself, with no AI: cleaning text up so quotes can be matched, finding a fragment in a document,
reading numbers and dates, deciding whether two figures agree, and which measure a passage names.

Tables (column dates, fiscal labels, units) are read in tables.py.
"""

import bisect
import datetime as dt
import re

# ------------------------------------------------------------------------------------------
# Text clean-up (applied to the document and to the AI's fragments alike)
# ------------------------------------------------------------------------------------------
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SPACES = "\xa0         "


def norm(s):
    """Lower case; zero-width characters removed; odd spaces, quotes and dashes made plain;
    table bars treated as spaces; '$ 85.2' -> '$85.2', '20 %' -> '20%'; whitespace collapsed."""
    s = str(s or "").translate(_ZERO_WIDTH)
    for ch in _SPACES:
        s = s.replace(ch, " ")
    s = (s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
         .replace("—", "-").replace("–", "-").replace("−", "-").replace("…", "..."))
    s = s.replace("|", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\$ (?=[\d(])", "$", s)
    s = re.sub(r"\( (?=[\d$])", "(", s)
    s = re.sub(r"(?<=\d) \)", ")", s)
    s = re.sub(r"(?<=\d) %", "%", s)
    return s.strip().lower()


class Doc:
    """A document's text with a searchable, cleaned-up copy that remembers which line each part came from."""

    def __init__(self, text, title="", url="", form="", filed="", period_end=None, date=None, fye=None, fiscal_quarter=""):
        self.text, self.title, self.url, self.form, self.filed = text, title, url, form, filed
        self.period_end, self.date = period_end, date
        self.fye = fye                        # month the company's fiscal year ends (1-12), for labels such as 'Q2 FY27'
        self.fiscal_quarter = fiscal_quarter  # e.g. 'Q2 FY2027' for a call transcript
        self.lines = text.split("\n")
        parts, self.line_starts, self.line_index = [], [], []
        pos = 0
        for i, line in enumerate(self.lines):
            n = norm(line)
            if not n:
                continue
            self.line_starts.append(pos)
            self.line_index.append(i)
            parts.append(n)
            pos += len(n) + 1
        self.flat = " ".join(parts)
        self._nospace = None

    # -- where is a fragment?
    def find(self, fragment, limit=25):
        """Offsets of the fragment in the cleaned-up text, and how it matched ('exact' or 'ignoring spaces')."""
        f = norm(fragment)
        if len(f) < 3:
            return [], None
        hits, start = [], 0
        while len(hits) < limit:
            k = self.flat.find(f, start)
            if k < 0:
                break
            hits.append(k)
            start = k + 1
        if hits:
            return hits, "exact"
        # Fallback: some filings split words ("a nd"). Match with all spaces removed, but say so.
        ns, where = self._nospace_index()
        g = f.replace(" ", "")
        if len(g) < 12:
            return [], None
        start = 0
        while len(hits) < limit:
            k = ns.find(g, start)
            if k < 0:
                break
            hits.append(where[k])
            start = k + 1
        return (hits, "ignoring spaces") if hits else ([], None)

    def _nospace_index(self):
        if self._nospace is None:
            chars, where = [], []
            for i, c in enumerate(self.flat):
                if c != " ":
                    chars.append(c)
                    where.append(i)
            self._nospace = ("".join(chars), where)
        return self._nospace

    def find_near(self, fragment, offset, before=20000, after=3000):
        """The occurrence of a fragment nearest before `offset` (or just after it), within the window.
        Returns (offset, how it matched) or (None, None). No limit on how often the fragment occurs elsewhere."""
        f = norm(fragment)
        if len(f) < 3:
            return None, None
        lo, hi = max(0, offset - before), min(len(self.flat), offset + after + len(f))
        k = self.flat.rfind(f, lo, min(len(self.flat), offset + len(f)))
        if k < 0:
            k = self.flat.find(f, offset, hi)
        if k >= 0:
            return k, "exact"
        g = f.replace(" ", "")
        if len(g) < 12:
            return None, None
        ns, where = self._nospace_index()
        i_lo, i_hi = bisect.bisect_left(where, lo), bisect.bisect_right(where, hi)
        k = ns.rfind(g, i_lo, i_hi)
        return (where[k], "ignoring spaces") if k >= 0 else (None, None)

    def line_of(self, offset):
        k = bisect.bisect_right(self.line_starts, offset) - 1
        return self.line_index[max(k, 0)] if self.line_index else 0

    def window(self, offset, before=800, after=200, length=0):
        return self.flat[max(0, offset - before): offset + length + after]


def doc_info(doc):
    """What a report needs to say about a document: title, link, form and dates."""
    return dict(title=doc.title, url=doc.url, form=doc.form, filed=doc.filed, period_end=doc.period_end, date=doc.date,
                fiscal_quarter=getattr(doc, "fiscal_quarter", ""))


def source_rank(form_or_title):
    """Filings first (10-K, 10-Q, 6-K), then results releases (8-K), then calls. Takes a form, or a document's title."""
    s = form_or_title or ""
    return 1 if re.match(r"(10-K|10-Q|6-K)\b", s) else 2 if "8-K" in s else 3


# ------------------------------------------------------------------------------------------
# Numbers
# ------------------------------------------------------------------------------------------
_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
          "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def numbers_in(s):
    """Numbers written in digits ('1,234.5', '(637.0)' = -637) and in words ('two', 'twenty-five')."""
    s = norm(s)
    out = []
    for m in re.finditer(r"\(?-?\d[\d,]*\.?\d*\)?", s):
        t = m.group(0)
        neg = t.startswith("(") and t.endswith(")")
        t = t.strip("()").rstrip(".").replace(",", "")
        try:
            v = float(t)
        except ValueError:
            continue
        out.append(-v if neg else v)
    for m in re.finditer(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(one|two|three|four|five|six|seven|eight|nine)\b", s):
        out.append(float(_WORDS[m.group(1)] + _WORDS[m.group(2)]))
    for w in re.findall(r"\b[a-z]+\b", s):
        if w in _WORDS:
            out.append(float(_WORDS[w]))
    return out


def number_supported(value, fragment):
    """Is the value visible in the fragment, allowing for thousands / millions / billions and % vs share?"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if v == 0:          # zero: only a printed zero, a dash or 'nil' counts (scaling a big number is not zero)
        return bool(re.search(r"(^|[\s(])(0|0\.0+|-|nil|none)([\s)]|$)", norm(fragment)))
    for n in numbers_in(fragment):
        for scale in (1, 1e-3, 1e-6, 1e3, 1e-9, 1e-2, 1e2):
            x = abs(n) * scale
            if abs(x - abs(v)) <= max(0.0051 * max(abs(v), 1e-9), 0.0005):
                return True
    return False


def decimals(v):
    """How many decimals a figure is given to ('89.023' has 3)."""
    s = str(v).strip()
    return len(s.split(".")[1]) if re.fullmatch(r"-?\d+\.\d+", s) else 0


def compare(paper, new):
    """'same' when the more precise figure lies inside the rounding of the less precise one ($89.0bn in a release
    = $89.023bn) AND they are within 2% of each other. The second test stops small whole numbers from matching
    loosely: 6 years is not 5.5 years, and 0 is not 0.4."""
    try:
        p, n = float(paper), float(new)
    except (TypeError, ValueError):
        return None
    dp = min(decimals(paper), decimals(new))
    gap = abs(n - p)
    if gap <= 1e-9:
        return "same"
    within_rounding = gap <= 0.5 * 10 ** (-dp) + 1e-9
    close = gap <= 0.02 * max(abs(p), abs(n))
    return "same" if within_rounding and close else "different"


def fmt(v, dp=None):
    """A figure as the reports print it: thousands separators, and no more decimals than it has."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if dp is None:
        dp = decimals(v) if isinstance(v, str) else (0 if x == int(x) else min(4, len(repr(x).split(".")[1])))
    return f"{x:,.{dp}f}"


# a table cell holding one number: '1,234', '$(5,855)', '20%', or a dash for nil
NUM_CELL = re.compile(r"^\(?-?\$?\s*\(?\s*\d[\d,]*(?:\.\d+)?\s*\)?%?$|^[-—–]$")


# ------------------------------------------------------------------------------------------
# Dates and periods
# ------------------------------------------------------------------------------------------
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
MONTH_RE = r"(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\b\.?"


def make_date(y, m, d):
    """A date, or None if the numbers are not a real date."""
    try:
        return dt.date(int(y), int(m), int(d))
    except ValueError:
        return None


def dates_in(s):
    """Full dates in any common form: 'June 30, 2026', '30 June 2026', 'July 29th, 2026', '2026-06-30', '26-August-2026'."""
    t = norm(s).replace("-", " ")
    out = []
    for m in re.finditer(r"\b(%s)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b" % MONTH_RE, t):
        out.append((m.start(), make_date(m.group(3), MONTHS[m.group(1).rstrip(".")], m.group(2))))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(%s)\s+(\d{4})\b" % MONTH_RE, t):
        out.append((m.start(), make_date(m.group(3), MONTHS[m.group(2).rstrip(".")], m.group(1))))
    for m in re.finditer(r"\b(\d{4})\s(\d{2})\s(\d{2})\b", t):
        out.append((m.start(), make_date(m.group(1), m.group(2), m.group(3))))
    return [d for _, d in sorted(out, key=lambda x: x[0]) if d]


def parse_date(s):
    """A date from '2026-06-30' or any date written in words; None if there is none."""
    if isinstance(s, dt.date):
        return s
    s = str(s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return make_date(*m.groups())
    d = dates_in(s)
    return d[0] if d else None


def month_key(d):
    """(year, month): two dates in the same month are the same period end."""
    return (d.year, d.month) if d else None


def month_end(y, m):
    """The last day of a month."""
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)) if y and m else None


def show_date(s):
    """A date as the reports print it: '30 Jun 2026' (or the text itself if it is not a date)."""
    d = parse_date(s)
    return f"{d:%d %b %Y}" if d else (s or "?")


def doc_period_end(text, filed=None):
    """The period a filing reports on: the latest 'quarter/months/year/period ended <date>' on or before the filing date."""
    t = norm(text)
    f = parse_date(filed)
    found = []
    for m in re.finditer(r"(?:period|quarter|months|year|weeks)\s+ended\s+((?:%s)\s+\d{1,2},?\s*\d{4})" % MONTH_RE, t):
        d = parse_date(m.group(1))
        if d and (not f or (d <= f and (f - d).days <= 200)):
            found.append(d)
    return max(found) if found else None


def doc_date(text, title="", url=""):
    """The date a transcript or release was given: the first full date in the URL, title or opening text."""
    for s in (url.replace("_", " "), title, text[:6000]):
        d = dates_in(s)
        if d:
            return d[0]
    return None


def period_evidence(doc, offset, length, period_end, period_type, header_dates=()):
    """Is the stated period visible near the figure? Returns the words found, or None."""
    d = parse_date(period_end)
    if not d:
        return None
    if d in header_dates:
        return f"column dated {d:%d %b %Y}"
    near = doc.window(offset, before=4000, after=300, length=length)
    for nd in dates_in(near):
        if nd == d:
            return f"'{d:%d %b %Y}' appears next to the figure"
    return None


# ------------------------------------------------------------------------------------------
# Which measure a passage names (whole words only)
# ------------------------------------------------------------------------------------------
def _phrase_re(p):
    """Whole words only ('rpo' is not in 'PowerPoint', 'atm' is not in 'treatment'). A trailing * allows any ending
    ('useful li*' matches 'useful life' and 'useful lives')."""
    stem = norm(p.rstrip("*"))
    tail = "" if p.endswith("*") else r"(?![a-z0-9])"
    return re.compile(r"(?<![a-z0-9])" + re.escape(stem) + tail)


def mentions(text, groups):
    """The groups NOT satisfied: each group needs at least one of its phrases, as whole words."""
    t = norm(text)
    return [g for g in (groups or []) if not any(_phrase_re(p).search(t) for p in g)]


# ------------------------------------------------------------------------------------------
# Figures in a passage (for sorting the AI's 'new facts': which amounts they state)
# ------------------------------------------------------------------------------------------
_FIG = re.compile(r"\$\(?(\d[\d,]*(?:\.\d+)?)\)?(?: ?(trillion|billion|million|bn|mn)\b)?"
                  r"|\b(\d[\d,]*(?:\.\d+)?) (trillion|billion|million)\b"
                  r"|\b(\d[\d,]*(?:\.\d+)?)%"
                  r"|\b(\d[\d,]*(?:\.\d+)?) ?(gigawatts?|gw|megawatts?|mw)\b")


def figures_in(text):
    """The amounts a passage states: [(kind, value, as printed)], kind 'money', 'pct' or 'qty' (gigawatts, megawatts).
    Numbers in a table row count as money. Years, dates and footnote marks are not figures."""
    t = norm(text)
    out, taken = [], []
    for m in _FIG.finditer(t):
        taken.append(m.span())
        if m.group(1) or m.group(3):
            s = m.group(1) or m.group(3)
            out.append(("money", s))
        elif m.group(5):
            out.append(("pct", m.group(5)))
        else:
            out.append(("qty", m.group(6)))
    # table rows: 'Supply and capacity | 92 | 87 | 279', 'Purchase commitments (d) 169,008 25,052 194,060', or a label
    # followed by one number per line
    for line in str(text or "").split("\n"):
        n = norm(line)
        cells = [c.strip() for c in n.split("|")][1:] if "|" in line else []
        if not cells and NUM_CELL.match(n.replace("$", "").strip() or "x"):
            cells = [n]
        elif not cells:
            toks = n.split(" ")
            while toks and NUM_CELL.match(toks[-1].replace("$", "")) and not toks[-1].endswith(","):
                cells.insert(0, toks.pop())
            if len(cells) < 2:
                cells = []
        for c in cells:
            if re.fullmatch(r"\(\d\)", c.strip()):          # a footnote mark such as '(3)'
                continue
            c2 = c.replace("$", "").strip("() ").rstrip("%")
            if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", c2) and not re.fullmatch(r"(19|20)\d{2}", c2) and c2 not in [x[1] for x in out]:
                out.append(("pct" if c.endswith("%") else "money", c2))
    res = []
    for kind, s in out:
        if kind == "money" and re.fullmatch(r"(19|20)\d{2}", s):
            continue
        try:
            res.append((kind, float(s.replace(",", "")), s))
        except ValueError:
            pass
    return res


def significant(printed):
    """At least three significant digits ('105', '4.25', '24,896'; not '25.0' or '20'): specific enough to tell
    one amount from another."""
    s = printed.replace(",", "")
    whole, _, frac = s.partition(".")
    digits = (whole.lstrip("0") + frac.rstrip("0")) if whole.strip("0") else frac.strip("0")
    return len(digits) >= 3


def round_number(printed):
    """A round number ('100.0', '750', '500'): fewer than three significant digits once a whole number's trailing zeros
    are left out. Many different facts share one."""
    s = re.sub(r"[^\d.]", "", printed)
    whole, _, frac = s.partition(".")
    frac = frac.rstrip("0")
    digits = whole.lstrip("0") + frac if frac else whole.lstrip("0").rstrip("0")
    return len(digits) < 3


def _decimals_printed(printed):
    s = re.sub(r"[^\d.]", "", str(printed))
    return len(s.split(".")[1]) if "." in s else 0


def same_amount(a, b):
    """Two printed amounts are the same figure: the more precise one, read in the other's units (thousands, millions,
    billions), rounds to the other ('35,802' million is '35.8' billion), and they are within 2% of each other (as in
    compare: '1' billion is not '746' million). Near is not the same: '66,594' million is not '66.9' billion, and
    '128,894' is not '128,320'."""
    (va, sa), (vb, sb) = a, b
    try:
        x, y = abs(float(va)), abs(float(vb))
    except (TypeError, ValueError):
        return False
    if not x or not y:
        return False
    for e in (0, 3, -3, 6, -6, 9, -9):
        xs = x * 10 ** -e                                   # a in b's units
        d = min(_decimals_printed(sa) + e, _decimals_printed(sb))   # the decimals of the less precise one
        gap = abs(xs - y)
        if gap <= 0.5 * 10 ** -d + 1e-9 * max(xs, y) and gap <= 0.02 * max(xs, y):
            return True
    return False
