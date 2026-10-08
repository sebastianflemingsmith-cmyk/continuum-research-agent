#!/usr/bin/env python3
"""
Watcher: step 1 of the research agent for "The AI Lose Lose Race".

It checks every source on the watch list for anything NEW since the last run,
drops the noise, and writes a short report. It does not read the documents or
judge whether they change the paper. That is step 2.

Run it from the project folder, in Antigravity's terminal:

    python3 Agent/watcher/watcher.py

Options:
    --dry-run          check and report, but do not move the bookmarks
    --since 2026-09-23 list SEC filings from that date, ignoring the bookmarks
                       (a test: nothing is saved)
    --only sec         check only the SEC feeds
    --only web         check only the web pages (transcripts, newsrooms)

Standard library only: nothing to install.
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")
LOG_PATH = os.path.join(HERE, "log.jsonl")
REPORTS_DIR = os.path.join(HERE, "reports")
HANDOFF_PATH = os.path.join(HERE, "handoff.json")

# --------------------------------------------------------------------------------------
# Filing rules (mirrors the "Filing rules" sheet of the watch list)
# --------------------------------------------------------------------------------------
HIGH, NORMAL, LOW, IGNORE = "High", "Normal", "Low", "Ignore"
RANK = {HIGH: 3, NORMAL: 2, LOW: 1, IGNORE: 0}

IGNORED_FORMS = {
    "3", "4", "5", "3/A", "4/A", "5/A", "144", "144/A", "SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
    "11-K", "S-8", "S-8 POS", "N-PX", "DEF 14A", "DEFA14A", "PRE 14A", "ARS", "CERT", "SD", "PX14A6G", "DFAN14A",
    "25-NSE", "IRANNOTICE", "10-D", "ABS-15G",
}
FORM_RULES = {
    "10-K": (HIGH, "Annual report"), "10-Q": (HIGH, "Quarterly report"),
    "10-K/A": (HIGH, "Annual report (amended)"), "10-Q/A": (HIGH, "Quarterly report (amended)"),
    "20-F": (HIGH, "Annual report (foreign filer)"), "20-F/A": (HIGH, "Annual report (foreign filer, amended)"),
    "40-F": (HIGH, "Annual report (foreign filer)"),
    "6-K": (NORMAL, "Foreign-filer report: read the exhibits"),
    "NT 10-Q": (HIGH, "Late quarterly report: a warning sign"), "NT 10-K": (HIGH, "Late annual report: a warning sign"),
    "NT 20-F": (HIGH, "Late annual report: a warning sign"),
    "424B2": (NORMAL, "Bond or share issue: final terms"), "424B5": (NORMAL, "Bond or share issue: final terms"),
    "FWP": (NORMAL, "Pricing term sheet (look for the spread to Treasuries)"),
    "424B3": (LOW, "Prospectus"), "424B4": (LOW, "Prospectus"), "424B7": (LOW, "Prospectus (resale)"),
    "8-A12B": (LOW, "New securities listed: points to a debt issue"),
    "S-1": (NORMAL, "Registration statement (IPO)"), "S-1/A": (NORMAL, "Registration statement (IPO), amended"),
    "F-1": (NORMAL, "Registration statement (IPO)"), "F-1/A": (NORMAL, "Registration statement (IPO), amended"),
    "S-3": (NORMAL, "Shelf registration"), "S-3ASR": (NORMAL, "Shelf registration"),
    "SC 13D": (LOW, "Strategic stake"), "SC 13D/A": (LOW, "Strategic stake (amended)"),
    "SCHEDULE 13D": (LOW, "Strategic stake"), "SCHEDULE 13D/A": (LOW, "Strategic stake (amended)"),
    "CORRESP": (LOW, "Letter to the SEC"), "UPLOAD": (LOW, "SEC comment letter"),
}
ITEMS_8K = {
    "2.02": (HIGH, "Results"), "1.01": (HIGH, "Material agreement"), "1.02": (HIGH, "Agreement ended"),
    "2.03": (HIGH, "New debt or off-balance-sheet obligation"), "4.02": (HIGH, "Past statements no longer reliable"),
    "2.06": (HIGH, "Impairment"), "2.04": (HIGH, "Debt accelerated"),
    "2.01": (NORMAL, "Acquisition or sale completed"), "2.05": (NORMAL, "Restructuring costs"),
    "3.02": (NORMAL, "Unregistered share sale"), "1.05": (NORMAL, "Cybersecurity incident"),
    "7.01": (NORMAL, "Investor material (Reg FD)"), "8.01": (NORMAL, "Other events"),
    "5.02": (LOW, "Directors or officers changed"), "5.03": (LOW, "Articles or by-laws changed"),
    "5.07": (LOW, "Shareholder vote results"), "3.03": (LOW, "Shareholder rights changed"),
    "9.01": (LOW, "Exhibits"),
}


def classify(form, items):
    """Return (priority, reason) for an EDGAR filing."""
    f = (form or "").strip().upper()
    if f in IGNORED_FORMS:
        return IGNORE, "Filtered out (insider, ownership or proxy form)"
    if f in ("8-K", "8-K/A"):
        codes = [c.strip() for c in (items or "").split(",") if c.strip()]
        best, reasons = LOW, []
        for c in codes:
            p, why = ITEMS_8K.get(c, (LOW, "Item " + c))
            if c == "9.01" and len(codes) > 1:
                continue
            reasons.append(f"{why} ({c})")
            if RANK[p] > RANK[best]:
                best = p
        return best, "; ".join(reasons) or "8-K"
    if f in FORM_RULES:
        return FORM_RULES[f]
    return LOW, "Form not in the rules: check what it is"


# --------------------------------------------------------------------------------------
# Web rules: how each page is watched. Patterns tested in a browser on 26 Sep 2026.
#   q4     = the investor site's own document feed (Q4 platform), JSON
#   links  = links on the page whose address (or text) matches a pattern
#   next   = the next quarter's page, whose address is predictable
#   newsroom: True = a company newsroom. Its headlines are ranked against the watch list (rank_and_match):
#   High when a headline matches a Topics search term or an Events row, otherwise Low. None is dropped.
# Pages that need a full browser (Palantir, Nebius investor hub) are not watched here;
# their results still arrive through the SEC feed or the newsroom.
# --------------------------------------------------------------------------------------
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
Q4_FEED = "/feed/FinancialReport.svc/GetFinancialReportList?LanguageId=1&pageSize=-1&pageNumber=0&year=-1"
TEXT_DOCS = re.compile(r"transcript|prepared remarks|remarks|letter", re.I)
# AMD's page links each document through a folder named "_" and 32 characters (0-9, a-f), and that name changes between
# visits (…/_2cc0365…/amd/db/841/9237/… became …/_340ae27…/amd/db/841/9237/… on 30 Sep 2026): the same document at a
# new address. Bookmarks compare addresses without that folder, so an old document is not reported as new.
CDN_TOKEN = re.compile(r"/_[0-9a-f]{32}(?=/)")


def seen_key(url):
    """An address as the bookmarks compare it: without the changing folder (CDN_TOKEN)."""
    return CDN_TOKEN.sub("", url)


WEB_RULES = {
    # Transcript pages (Transcripts sheet)
    "TR-NVDA": {"type": "q4", "base": "https://investor.nvidia.com"},
    "TR-GOOGL": {"type": "q4", "base": "https://abc.xyz"},
    "TR-META": {"type": "q4", "base": "https://investor.atmeta.com"},
    "TR-AMZN": {"type": "q4", "base": "https://ir.aboutamazon.com"},
    "TR-ORCL": {"type": "q4", "base": "https://investor.oracle.com"},
    "TR-CRWV": {"type": "q4", "base": "https://investors.coreweave.com"},
    "TR-MU": {"type": "q4", "base": "https://investors.micron.com"},
    "TR-VRT": {"type": "q4", "base": "https://investors.vertiv.com"},
    "TR-ANET": {"type": "q4", "base": "https://investors.arista.com"},
    "TR-AMD": {"type": "links", "url": "https://ir.amd.com/financial-information/financial-results", "pattern": r"transcript", "on": "both"},
    "TR-DELL": {"type": "skip", "why": "Site does not answer scripts (26 Sep 2026); results arrive through the SEC feed"},
    "TR-GEV": {"type": "links", "url": "https://www.gevernova.com/investors", "pattern": r"transcript|\.mp3", "on": "both"},
    "TR-TSM": {"type": "skip", "why": "Site blocks scripts (26 Sep 2026); results arrive through the SEC feed (Form 6-K)"},
    # ASML's site stopped answering after 26 Sep 2026: on 4 Oct, Seb's Mac timed out with the plain and the browser user
    # agent, at 30 and 90 seconds. Its former rule, to restore if it answers again: {"type": "next", "url":
    # "https://www.asml.com/en/investors/financial-results/q2-2026", "pattern": r"transcript|prepared remarks", "site": "asml"}
    "TR-ASML": {"type": "skip", "why": "Site does not answer scripts (4 Oct 2026); results arrive through the SEC feed (Form 6-K): check its results page by hand"},
    "TR-MSFT": {"type": "next", "url": "https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q4", "pattern": None, "site": "msft"},
    "TR-NBIS": {"type": "skip", "why": "Page needs a full browser; watched through the Nebius newsroom instead"},
    "TR-PLTR": {"type": "skip", "why": "Page needs a full browser; results arrive through the SEC feed"},
    "TR-AVGO": {"type": "skip", "why": "Posts no transcript; results arrive through the SEC feed"},
    "TR-ETN": {"type": "skip", "why": "Posts no transcript; results arrive through the SEC feed"},
    "TR-EQIX": {"type": "skip", "why": "Posts no transcript; results arrive through the SEC feed"},
    "TR-DLR": {"type": "skip", "why": "Posts no transcript; results arrive through the SEC feed"},
    # Newsrooms and official pages (Sources sheet)
    "SOFTBANK": {"type": "links", "url": "https://group.softbank/en/news/press", "pattern": r"group\.softbank/en/news/press/\d{8}", "on": "href", "newsroom": True},
    "OPENAI": {"type": "rss", "url": "https://openai.com/news/rss.xml", "newsroom": True},  # the news page blocks scripts; its RSS feed is made for them
    "ANTHROPIC": {"type": "links", "url": "https://www.anthropic.com/news", "pattern": r"anthropic\.com/news/[^/?#]+/?$", "on": "href", "newsroom": True},
    "NVIDIA-NEWS": {"type": "links", "url": "https://nvidianews.nvidia.com/", "pattern": r"nvidianews\.nvidia\.com/news/[^/?#]+/?$", "on": "href", "newsroom": True},
    "MSFT-BLOG": {"type": "links", "url": "https://blogs.microsoft.com/", "pattern": r"blogs\.microsoft\.com/blog/\d{4}/\d{2}/\d{2}/[^/?#]+/?$", "on": "href", "newsroom": True},
    "AMZN-NEWS": {"type": "links", "url": "https://www.aboutamazon.com/news", "pattern": r"aboutamazon\.com/news/(aws|company-news)/[^/?#]+/?$", "on": "href", "newsroom": True},
    "NBIS-NEWS": {"type": "links", "url": "https://nebius.com/newsroom", "pattern": r"nebius\.com/newsroom/[^/?#]+/?$", "on": "href", "name": "Nebius newsroom", "newsroom": True},
    "BIS": {"type": "links", "url": "https://www.bis.org/publications/qr", "pattern": r"bis\.org/publications/qr-\d{6}", "on": "href"},
    "IMF": {"type": "skip", "why": "Site blocks scripts (26 Sep 2026); report comes out in April and October: check by hand (Events sheet)"},
}


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------
def now_local():
    return dt.datetime.now().replace(microsecond=0)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def append_jsonl(path, records):
    """Append without joining a new record onto a partial last line left by an interrupted write."""
    newline = False
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            newline = f.read(1) != b"\n"
    with open(path, "a", encoding="utf-8") as f:
        if newline:
            f.write("\n")
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class FetchError(Exception):
    pass


def fetch(url, user_agent, accept="*/*", timeout=30):
    """Return (status, text). Raises FetchError with a plain-English reason."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, ""
        raise FetchError(f"HTTP {e.code} ({'blocked' if e.code in (401, 403, 429) else 'error'})")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            raise FetchError("Python cannot check SSL certificates on this Mac. If Python came from python.org, "
                             "open Applications > Python 3.x and run 'Install Certificates.command', then try again")
        raise FetchError(f"could not connect ({reason})")
    except Exception as e:  # timeouts and the like
        raise FetchError(f"could not connect ({e.__class__.__name__}: {e})")


class LinkParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self._href, self._text = base, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href, self._text = urllib.parse.urljoin(self.base, href.strip()), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            # Pieces are joined with spaces: Anthropic's <time>Oct 2, 2026</time><span>Announcements</span> read as
            # "Oct 2, 2026Announcements" when joined with "" (4 Oct 2026).
            text = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            self.links.append((self._href, text))
            self._href = None


def page_links(html_text, base):
    p = LinkParser(base)
    try:
        p.feed(html_text)
    except Exception:
        pass
    return p.links


def page_text(html_text):
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", html.unescape(t))


# --------------------------------------------------------------------------------------
# Dates. A web page's item has no filing date. The report shows the publication date where the source gives one,
# and says where it came from; otherwise it says when the watcher found the item ("found 2026-10-04").
# --------------------------------------------------------------------------------------
MONTH = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
TEXT_DATES = (  # (pattern, order of year, month, day in its groups)
    (re.compile(r"\b" + MONTH + r"\.?\s+(\d{1,2}),?\s+(20\d{2})(?!\d)", re.I), (2, 0, 1)),   # Oct. 1, 2026
    (re.compile(r"(?<!\d)(\d{1,2})\s+" + MONTH + r"\.?,?\s+(20\d{2})(?!\d)", re.I), (2, 1, 0)),  # 1 October 2026
    (re.compile(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)"), (0, 1, 2)),                        # 2026-10-01
)
ADDRESS_DATES = (
    re.compile(r"/(20\d{2})(\d{2})(\d{2})(?=/|$)"),          # SoftBank: …/news/press/20261001
    re.compile(r"/(20\d{2})/(\d{2})/(\d{2})(?=/|$)"),        # Microsoft blog: …/blog/2026/10/01/…
)


def as_date(y, m, d):
    """ISO date, or None if the parts are not a real date. A month may be a name ('Oct', 'October')."""
    try:
        month = MONTHS.index(m[:3].lower()) + 1 if not str(m).isdigit() else int(m)
        return dt.date(int(y), month, int(d)).isoformat()
    except ValueError:
        return None


def text_date(text):
    """The first date written in the text ('Oct. 1, 2026', '1 October 2026', '2026-10-01'), or None."""
    found = []
    for pat, (y, m, d) in TEXT_DATES:
        for g in pat.finditer(str(text or "")):
            day = as_date(g.group(y + 1), g.group(m + 1), g.group(d + 1))
            if day:
                found.append((g.start(), day))
    return min(found)[1] if found else None


def feed_date(text):
    """An RSS pubDate ('Thu, 01 Oct 2026 12:00:00 GMT') or ISO date as an ISO date, or None."""
    try:
        return email.utils.parsedate_to_datetime(str(text or "").strip()).date().isoformat()
    except (TypeError, ValueError, IndexError):  # Python 3.9 raises TypeError for text it cannot read
        return text_date(text)


def publication_date(url, title, from_feed=None):
    """(date, where it came from): the feed's own date, a date in the address, or a date in the link text, in that
    order. (None, None) when the source gives none."""
    if from_feed:
        return from_feed, "the feed"
    path = urllib.parse.urlsplit(url).path
    for pat in ADDRESS_DATES:
        for g in pat.finditer(path):
            day = as_date(*g.groups())
            if day:
                return day, "the address"
    day = text_date(title)
    return (day, "the link text") if day else (None, None)


def when(x):
    """The date column of the report: an SEC filing date, a web item's publication date, or the day it was found."""
    if x.get("published"):
        return f"published {x['published']} (date from {x['date_from']})"
    if x.get("channel") == "web":  # reports before 4 Oct 2026 kept the day found in "filed"
        return f"found {x.get('found') or x.get('filed')}"
    return f"filed {x['filed']}"


# --------------------------------------------------------------------------------------
# Reading the watch list (.xlsx) with the standard library
# --------------------------------------------------------------------------------------
NS_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _col(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_workbook(path):
    """Return {sheet name: list of rows (lists)}."""
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{{{NS_M}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS_M}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for s in wb.find(f"{{{NS_M}}}sheets"):
            target = rels[s.get(f"{{{NS_R}}}id")]
            part = target[1:] if target.startswith("/") else "xl/" + target
            rows = {}
            for row in ET.fromstring(z.read(part)).iter(f"{{{NS_M}}}row"):
                cells = {}
                for c in row.findall(f"{{{NS_M}}}c"):
                    t, v = c.get("t"), c.find(f"{{{NS_M}}}v")
                    if t == "s" and v is not None:
                        val = shared[int(v.text)]
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{{{NS_M}}}t"))
                    elif v is None:
                        val = None
                    elif t in ("str", "e"):
                        val = v.text
                    elif t == "b":
                        val = v.text == "1"
                    else:
                        num = float(v.text)
                        val = int(num) if num.is_integer() else num
                    cells[_col(c.get("r"))] = val
                if cells:
                    width = max(cells) + 1
                    rows[int(row.get("r"))] = [cells.get(i) for i in range(width)]
            out[s.get("name")] = [rows.get(k, []) for k in range(1, max(rows) + 1)] if rows else []  # keep blank rows: they end tables
    return out


def table(rows, first_header="ID"):
    """Find the header row (first cell == 'ID') and return a list of dicts until the first blank ID."""
    for i, r in enumerate(rows):
        if r and r[0] == first_header:
            hdr = [str(h) if h is not None else "" for h in r]
            out = []
            for row in rows[i + 1:]:
                if not row or row[0] in (None, ""):
                    break
                out.append({hdr[j]: (row[j] if j < len(row) else None) for j in range(len(hdr))})
            return out
    return []


# --------------------------------------------------------------------------------------
# What a new item is checked against on the watch list (Ledger "Built from", Events, Topics "Search terms")
# --------------------------------------------------------------------------------------
EVENT_SLACK = dt.timedelta(days=7)  # "near the date" of an Events row
QUOTED = re.compile(r"(?:^|(?<=[\s(]))['‘“\"]([^'‘’“”\"]{3,}?)['’”\"](?![A-Za-z])")  # 'Third Tranche'
ROW_ID = re.compile(r"\b[FT]\d{3}\b")


def watch_rules(book):
    built_from = {}
    for row in table(book.get("Ledger", [])):
        parts = ROW_ID.findall(str(row.get("Built from") or ""))
        if parts:
            built_from[row["ID"]] = parts
    topics = []
    for t in table(book.get("Topics", [])):
        terms = [s.strip() for s in str(t.get("Search terms") or "").split(";") if s.strip()]
        topics.append((t["ID"], t.get("Topic") or "", terms))
    return {"built_from": built_from, "events": table(book.get("Events", []), first_header="Date"), "topics": topics}


def recalculate(rows, built_from):
    """Rows built from these rows, directly or through another built row, with the moved rows each one uses.
    F106 (paid by Amazon and SoftBank) is built from F102, F103, F104: a SoftBank item moves F104, so F106 is listed."""
    moved, order = set(rows), []
    while True:
        wave = [rid for rid, parts in built_from.items() if rid not in moved and moved.intersection(parts)]
        if not wave:
            return [{"id": rid, "uses": [p for p in built_from[rid] if p in moved]} for rid in order]
        moved.update(wave)
        order += wave


def term_pattern(term):
    """A search term as a whole word or phrase, in any case, plural allowed: 'tranche' finds '(Third Tranche)'."""
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, term.split())) + r"(?:e?s)?(?![A-Za-z0-9])", re.I)


def words(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def excel_day(value):
    """A date cell typed as a date in Excel (a day number, e.g. 46296) as a date; None for text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
    return None


def event_window(text):
    """(first day, last day) of an Events date: '1 Oct 2026 (Japan time)', 'Late Oct 2026', 'October 2026',
    'Late Oct–early Nov 2026'. None when it has no month ('Undated', 'Mid-2027', 'Q2 FY2027 – FY2029')."""
    if excel_day(text):
        return excel_day(text), excel_day(text)
    day = text_date(text)
    if day:
        day = dt.date.fromisoformat(day)
        return day, day
    year = re.search(r"\b(20\d{2})\b", str(text or ""))
    parts = re.findall(r"(?:\b(early|mid|late)[\s-]*)?\b" + MONTH + r"\b", str(text or ""), re.I)
    if not (year and parts):
        return None

    def span(part):
        y, m = int(year.group(1)), MONTHS.index(part[1][:3].lower()) + 1
        last = ((dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)).day
        lo, hi = {"early": (1, 10), "mid": (11, 20), "late": (21, last)}.get(part[0].lower(), (1, last))
        return dt.date(y, m, lo), dt.date(y, m, hi)
    return span(parts[0])[0], span(parts[-1])[1]


def event_match(x, ev):
    """What ties a new item to an Events row, or None. Same Source ID, within EVENT_SLACK of the event's date, and
    the headline has a phrase the row quotes ('Third Tranche') or two words of the event's name."""
    source = str(x.get("source") or "").upper()
    ids = {source, re.sub(r"^TR-|-(NEWS|BLOG)$", "", source)}  # TR-MU and MU; NBIS-NEWS and NBIS
    if not ids.intersection(t.upper() for t in re.split(r"[;,\s]+", str(ev.get("Source ID") or "")) if t):
        return None
    window = event_window(ev.get("Date"))
    try:
        day = dt.date.fromisoformat(str(x.get("published") or x.get("filed") or x.get("found"))[:10])
    except ValueError:
        return None
    if not window or not window[0] - EVENT_SLACK <= day <= window[1] + EVENT_SLACK:
        return None
    head = x.get("description") or ""
    for phrase in QUOTED.findall(f"{ev.get('Note') or ''} {ev.get('Event') or ''}"):
        if term_pattern(phrase).search(head):
            return f"'{phrase}'"
    own = words(x.get("company")) | words(" ".join(ids))
    hits = [w for w in sorted(words(ev.get("Event")) - own) if len(w) >= 4 and w in words(head)]
    return ", ".join(f"'{w}'" for w in hits) if len(hits) >= 2 else None


def rank_and_match(new, rules):
    """For every new item: the ledger rows to recalculate, any Events row it matches (with the watch list's own
    note), and its rank. A headline that matches a Topics search term or an Events row is High, and the match is
    named. Other newsroom headlines are Low. Nothing is dropped: every item stays in the report and the log."""
    for x in new:
        moved = recalculate(x.get("ledger_rows") or [], rules["built_from"])
        if moved:
            x["recalculate"] = moved
        head = x.get("description") or ""
        reasons = []
        for tid, topic, terms in rules["topics"]:
            hit = [t for t in terms if term_pattern(t).search(head)]
            if hit:
                reasons.append(f"{tid} {topic}: " + ", ".join(f"'{t}'" for t in hit))
        events = []
        for ev in rules["events"]:
            why = event_match(x, ev)
            if why:
                date = excel_day(ev.get("Date")) or ev.get("Date")
                events.append({"date": str(date), "event": ev.get("Event"), "status": ev.get("Status of the date"),
                               "rows": ev.get("Ledger rows affected"), "note": ev.get("Note"), "matched": why})
                reasons.append(f"Events {date}, {ev.get('Event')}: {why}")
        if events:
            x["events"] = events
        newsroom = x.get("channel") == "web" and WEB_RULES.get(x.get("source"), {}).get("newsroom")
        if reasons:
            x["matches"] = reasons
            x["priority"] = HIGH
            x["why"] = ("Headline matches " if newsroom else x["why"] + "; headline matches ") + "; ".join(reasons)
        elif newsroom:
            x["priority"] = LOW
            x["why"] = "Newsroom headline: no topic or event match"
    return new


# --------------------------------------------------------------------------------------
# SEC
# --------------------------------------------------------------------------------------
ACC = re.compile(r"\d{10}-\d{2}-\d{6}")


def sec_rows(sub):
    f = sub["filings"]["recent"]
    n = len(f["accessionNumber"])
    rows = []
    for i in range(n):
        rows.append({
            "acc": f["accessionNumber"][i], "filed": f["filingDate"][i],
            "accepted": (f.get("acceptanceDateTime") or [""] * n)[i][:19],
            "form": f["form"][i], "items": (f.get("items") or [""] * n)[i] or "",
            "doc": (f.get("primaryDocument") or [""] * n)[i] or "",
            "desc": (f.get("primaryDocDescription") or [""] * n)[i] or "",
        })
    return rows


def filing_url(cik, acc, doc):
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
    return base + doc if doc else base


def check_sec(sources, state, cfg, args, ledger_by_source, feeds_models, out, fetcher):
    ua = f"{cfg.get('contact_name') or 'Watcher'} {cfg['contact_email']}"
    baseline_date = cfg.get("baseline_date", "2026-09-25")
    for s in sources:
        tick, cik = s["ID"], int(s["CIK"])
        url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
        try:
            status, body = fetcher(url, ua, "application/json")
            if status != 200:
                raise FetchError(f"HTTP {status}")
            rows = sec_rows(json.loads(body))
        except (FetchError, ValueError, KeyError) as e:
            out["errors"].append({"source": tick, "what": "SEC filings", "url": url, "why": str(e)})
            continue
        finally:
            time.sleep(0.15)  # the SEC allows at most 10 requests a second

        st = state["sec"].get(tick)
        if args.since:
            new = [r for r in rows if r["filed"] >= args.since]
        elif st is None:
            base_acc = ACC.search(str(s.get("Last filing seen (baseline)") or ""))
            idx = next((i for i, r in enumerate(rows) if base_acc and r["acc"] == base_acc.group(0)), None)
            if idx is not None:
                new = rows[:idx]
            else:
                new = [r for r in rows if r["filed"] > baseline_date]
        else:
            last = st.get("last_accepted", "")
            seen = set(st.get("recent", []))
            new = [r for r in rows if r["accepted"] > last or (r["accepted"] == last and r["acc"] not in seen)]

        for r in reversed(new):  # oldest first
            pri, why = classify(r["form"], r["items"])
            item = {
                "channel": "sec", "source": tick, "company": s.get("Source") or tick, "form": r["form"],
                "items": r["items"], "filed": r["filed"], "accession": r["acc"], "description": r["desc"],
                "url": filing_url(cik, r["acc"], r["doc"]), "index": filing_url(cik, r["acc"], ""),
                "priority": pri, "why": why,
            }
            if pri == IGNORE:
                out["ignored"].append(item)
            else:
                if pri in (HIGH, NORMAL):
                    item["ledger_rows"] = ledger_by_source.get(tick, [])
                    item["models"] = feeds_models.get(tick, [])
                out["new"].append(item)

        if rows and not args.since and not args.dry_run:
            top = max(r["accepted"] for r in rows)
            state["sec"][tick] = {
                "last_accepted": top,
                "recent": [r["acc"] for r in rows if r["accepted"] == top] + [r["acc"] for r in rows[:20]],
                "last_checked": now_local().isoformat(),
            }
        out["checked"].append(f"SEC {tick}")


# --------------------------------------------------------------------------------------
# Web pages
# --------------------------------------------------------------------------------------
def next_quarter_url(url, site):
    if site == "tsmc":
        m = re.search(r"/(\d{4})/q(\d)$", url)
        y, q = int(m.group(1)), int(m.group(2))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
        return url[:m.start()] + f"/{y}/q{q}"
    if site == "asml":
        m = re.search(r"/q(\d)-(\d{4})$", url)
        q, y = int(m.group(1)), int(m.group(2))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
        return url[:m.start()] + f"/q{q}-{y}"
    if site == "msft":
        m = re.search(r"/fy-(\d{4})/earnings-fy-(\d{4})-q(\d)$", url)
        fy, q = int(m.group(1)), int(m.group(3))
        fy, q = (fy, q + 1) if q < 4 else (fy + 1, 1)
        return url[:m.start()] + f"/fy-{fy}/earnings-fy-{fy}-q{q}"
    raise ValueError(site)


def web_items(rule, ua, fetcher):
    """Return (items, note). items = {key: {"title":..., "url":..., "kind":...}}. Raises FetchError."""
    t = rule["type"]
    if t == "q4":
        status, body = fetcher(rule["base"] + Q4_FEED, ua, "application/json")
        if status != 200:
            raise FetchError(f"HTTP {status}")
        items = {}
        for rep in json.loads(body).get("GetFinancialReportListResult") or []:
            for d in rep.get("Documents") or []:
                path = d.get("DocumentPath") or ""
                if not path:
                    continue
                path = urllib.parse.urljoin(rule["base"] + "/", path)
                title = f"{rep.get('ReportTitle', '').strip()}: {d.get('DocumentTitle', '').strip()}"
                kind = d.get("DocumentCategory") or ""
                items[path] = {"title": title, "url": path, "kind": kind}
        return items, ""
    if t == "links":
        status, body = fetcher(rule["url"], ua, "text/html")
        if status != 200:
            raise FetchError(f"HTTP {status}")
        pat = re.compile(rule["pattern"], re.I)
        items = {}
        for href, text in page_links(body, rule["url"]):
            target = href if rule.get("on") == "href" else f"{text} {href}"
            if pat.search(target):
                key = href.split("#")[0]
                if key not in items or (text and not items[key]["title"]):
                    items[key] = {"title": text, "url": key, "kind": ""}
        return items, ""
    if t == "rss":
        status, body = fetcher(rule["url"], ua, "application/rss+xml, application/xml, text/xml")
        if status != 200:
            raise FetchError(f"HTTP {status}")
        items = {}
        try:
            for it in ET.fromstring(body.encode("utf-8")).iter("item"):
                link = (it.findtext("link") or "").strip()
                if link:
                    items[link] = {"title": (it.findtext("title") or "").strip(), "url": link, "kind": "",
                                   "published": feed_date(it.findtext("pubDate"))}
        except ET.ParseError:
            for block in re.findall(r"(?s)<item\b.*?</item>", body):
                m = re.search(r"(?s)<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>", block)
                tt = re.search(r"(?s)<title>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</title>", block)
                pd = re.search(r"(?s)<pubDate>\s*(.*?)\s*</pubDate>", block)
                if m:
                    link = m.group(1).strip()
                    items[link] = {"title": html.unescape(tt.group(1).strip()) if tt else "", "url": link, "kind": "",
                                   "published": feed_date(pd.group(1)) if pd else None}
        return items, ""
    raise ValueError(t)


def check_next_page(rid, rule, st, ua, fetcher, out, args, name):
    """Pages with a predictable address for the next quarter (Microsoft, TSMC, ASML)."""
    current = (st or {}).get("current", rule["url"])
    nxt = next_quarter_url(current, rule["site"])
    status, body = fetcher(nxt, ua, "text/html")
    flags = set((st or {}).get("flags", []))
    found = []
    if status == 200 and rule["site"] == "msft":
        text = page_text(body)
        m = re.search(r"fy-(\d{4})-q(\d)$", nxt)
        label = f"Fiscal Year {m.group(1)} {['First', 'Second', 'Third', 'Fourth'][int(m.group(2)) - 1]} Quarter"
        if label.lower() not in text.lower():
            status = 404  # the address answered, but not with that quarter's call page
        else:
            has_tx = bool(re.search(r"\bOperator\b|Question-and-answer|Amy Hood|Satya Nadella", text))
            if "page_up" not in flags:
                found.append({"title": "Next results call page is up" + (" with the transcript" if has_tx else " (no transcript yet)"),
                              "url": nxt, "kind": "transcript" if has_tx else "event"})
                flags.add("page_up")
            elif has_tx and "transcript" not in flags:
                found.append({"title": "Transcript posted", "url": nxt, "kind": "transcript"})
            if has_tx:
                flags.add("transcript")
    elif status == 200:
        pat = re.compile(rule["pattern"], re.I)
        hits = [(h, t) for h, t in page_links(body, nxt) if pat.search(f"{t} {h}")]
        if hits and "transcript" not in flags:
            for h, t in hits:
                found.append({"title": t or "Transcript", "url": h, "kind": "transcript"})
            flags.add("transcript")
    new_state = {"current": current, "flags": sorted(flags), "last_checked": now_local().isoformat()}
    if "transcript" in flags:  # this quarter is done: move on to the next one
        new_state = {"current": nxt, "flags": [], "last_checked": now_local().isoformat()}
    return found, new_state, nxt, status


def web_discovery(rid, name, it, ledger_rows, priority, why):
    """A new web item. It has no filing date: "published" is the source's own date (None if it gives none) and
    "found" is the day the watcher found it."""
    published, origin = publication_date(it["url"], it["title"], it.get("published"))
    return {"channel": "web", "source": rid, "company": name, "form": it["kind"] or "page",
            "published": published, "date_from": origin, "found": now_local().date().isoformat(),
            "description": it["title"], "url": it["url"], "priority": priority, "why": why, "ledger_rows": ledger_rows}


def check_web(web_sources, state, cfg, args, out, fetcher):
    plain_ua = cfg.get("web_user_agent") or "Mozilla/5.0 (compatible; ContinuumWatcher/1.0)"
    for rid, name, ledger_rows in web_sources:
        rule = WEB_RULES.get(rid)
        if not rule:
            continue
        # A few sites refuse anything that does not look like a browser (TSMC, Dell and the IMF on 26 Sep 2026).
        ua = BROWSER_UA if rule.get("ua") == "browser" else plain_ua
        timeout = rule.get("timeout", 30)
        tried = []  # the addresses actually fetched: an error names the last one, not the rule's starting address

        def page_fetch(u, a, acc="*/*", _t=timeout, _tried=tried):
            _tried.append(u)
            return fetcher(u, a, acc, _t)

        if rule["type"] == "skip":
            out["skipped"].append({"source": rid, "name": name, "why": rule["why"]})
            continue
        st = state["web"].get(rid)
        try:
            if rule["type"] == "next":
                found, new_state, nxt, status = check_next_page(rid, rule, st, ua, page_fetch, out, args, name)
                for f in found:
                    out["new"].append(web_discovery(rid, name, f, ledger_rows, HIGH if f["kind"] == "transcript" else NORMAL,
                                                    "Call transcript" if f["kind"] == "transcript" else "New page"))
                if not args.dry_run:
                    state["web"][rid] = new_state
                out["checked"].append(f"Web {rid} (watching {nxt}: {'not up yet' if status == 404 else 'up'})")
                continue

            items, _ = web_items(rule, ua, page_fetch)
            if not items:
                raise FetchError("page loaded but no matching links were found (the page may need a full browser, or its layout changed)")
            if st is None:
                out["baselined"].append({"source": rid, "name": name, "count": len(items)})
                new_keys = []
            else:
                seen = {seen_key(k) for k in st.get("seen", [])}
                new_keys = [k for k in items if seen_key(k) not in seen]
            for k in new_keys:
                it = items[k]
                is_text = bool(TEXT_DOCS.search(f"{it['title']} {it['kind']}")) or it["kind"] in ("transcript", "remarks")
                out["new"].append(web_discovery(rid, name, it, ledger_rows, HIGH if (is_text and rid.startswith("TR-")) else NORMAL,
                                                "Call transcript or remarks" if (is_text and rid.startswith("TR-")) else "New item on the page"))
            if not args.dry_run:
                prev = (st or {}).get("seen", [])
                merged = list(dict.fromkeys(list(items.keys()) + prev))[:2000]
                state["web"][rid] = {"seen": merged, "last_checked": now_local().isoformat()}
            out["checked"].append(f"Web {rid}")
        except FetchError as e:
            # ASML timed out on …/q3-2026 (30 Sep and 4 Oct 2026) but the report named the rule's …/q2-2026.
            out["errors"].append({"source": rid, "what": name, "url": tried[-1] if tried else (rule.get("url") or rule.get("base")),
                                  "why": str(e), "last_checked": (st or {}).get("last_checked")})
        finally:
            time.sleep(0.5)


# --------------------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------------------
def write_report(out, started, args):
    lines = []
    new = out["new"]
    by = {p: [x for x in new if x["priority"] == p] for p in (HIGH, NORMAL, LOW)}
    mode = " (test: SEC filings since " + args.since + "; web pages not checked; nothing saved)" if args.since else (" (dry run: nothing saved)" if args.dry_run else "")
    lines.append(f"# Watcher report, {started.strftime('%d %b %Y %H:%M')}{mode}")
    lines.append("")
    lines.append(f"**New: {len(new)}** ({len(by[HIGH])} high, {len(by[NORMAL])} normal, {len(by[LOW])} low). "
                 f"Filtered out: {len(out['ignored'])}. Could not check: {len(out['errors'])}.")
    lines.append("")
    lines.append("New means *released since the last run*. Whether it changes the paper is step 2.")
    for p in (HIGH, NORMAL, LOW):
        if not by[p]:
            continue
        lines.append("")
        lines.append(f"## {p}")
        for x in by[p]:
            what = x["form"] + (f" (Items {x['items']})" if x.get("items") else "")
            desc = f" · {x['description']}" if x.get("description") else ""
            lines.append(f"- **{x['company']}**: {what}: {x['why']}{desc} · {when(x)} · [open]({x['url']})")
            rows = x.get("ledger_rows") or []
            if rows:
                shown = ", ".join(rows[:15]) + (f" and {len(rows) - 15} more" if len(rows) > 15 else "")
                lines.append(f"  - Ledger rows from this source: {shown}")
            if x.get("recalculate"):
                built = ", ".join(f"{r['id']} (uses {', '.join(r['uses'])})" for r in x["recalculate"])
                lines.append(f"  - Recalculate, built from those rows: {built}")
            for ev in x.get("events") or []:
                lines.append(f"  - Watch list event: {ev['date']}, {ev['event']} ({ev['status']}). Rows: {ev['rows']}. Matched {ev['matched']}")
                if ev.get("note"):
                    lines.append(f"  - The watch list's own note, not a sourced figure: \"{ev['note']}\"")
            if x.get("models"):
                lines.append(f"  - Also feeds: {'; '.join(x['models'])}")
    if out["baselined"]:
        lines.append("")
        lines.append("## Pages seen for the first time")
        lines.append("Bookmarks set; changes will show from the next run.")
        for b in out["baselined"]:
            lines.append(f"- {b['name']}: {b['count']} items recorded")
    if out["errors"]:
        lines.append("")
        lines.append("## Could not check (look at these by hand)")
        for e in out["errors"]:
            last = f" · last checked {e['last_checked'][:10]}" if e.get("last_checked") else ""
            lines.append(f"- {e['what']} ({e['source']}): {e['why']} · {e['url']}{last}")
    if out["ignored"]:
        lines.append("")
        lines.append("## Filtered out")
        counts = {}
        for x in out["ignored"]:
            counts.setdefault(x["form"], []).append(x["source"])
        for form, who in sorted(counts.items()):
            lines.append(f"- Form {form}: {len(who)} ({', '.join(sorted(set(who)))})")
    if out["skipped"]:
        lines.append("")
        lines.append("## Not watched here")
        for s in out["skipped"]:
            lines.append(f"- {s['name']}: {s['why']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main(argv=None, fetcher=fetch):
    ap = argparse.ArgumentParser(description="Check the watch list's sources for anything new.")
    ap.add_argument("--dry-run", action="store_true", help="check and report, but do not move the bookmarks")
    ap.add_argument("--since", help="list SEC filings from this date (YYYY-MM-DD), ignoring bookmarks; saves nothing")
    ap.add_argument("--only", choices=["sec", "web"], help="check only one kind of source")
    args = ap.parse_args(argv)
    if args.since:
        args.dry_run = True
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
            sys.exit("--since needs a date like 2026-09-23")

    cfg = load_json(CONFIG_PATH, {})
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cfg.get("contact_email") or ""):
        sys.exit("Open Agent/watcher/config.json and put your email in \"contact_email\" "
                 "(the SEC asks every program that reads its data to say who is running it). Then run this again.")
    wl_path = os.path.normpath(os.path.join(HERE, cfg.get("watch_list", "../Watch list - The AI Lose Lose Race.xlsx")))
    if not os.path.exists(wl_path):
        sys.exit(f"Cannot find the watch list at {wl_path}")

    started = now_local()
    book = read_workbook(wl_path)
    sources = table(book.get("Sources", []))
    transcripts = table(book.get("Transcripts", []))
    ledger = table(book.get("Ledger", []))

    ledger_by_source = {}
    for row in ledger:
        for tok in re.split(r"[;,\s]+", str(row.get("Source ID") or "")):
            if tok:
                ledger_by_source.setdefault(tok.upper(), []).append(row["ID"])

    feeds_models = {}
    for row in table(book.get("Basket inputs", []), first_header="Ticker"):
        feeds_models.setdefault(str(row["Ticker"]).upper(), []).append("Basket inputs (Tests 1, 2, 3, 5)")
    for tick in ("CRWV", "NBIS", "ORCL"):
        feeds_models.setdefault(tick, []).append("Test 6 inputs")
    for tick in ("NVDA",):
        feeds_models.setdefault(tick, []).append("Test 4 inputs")

    sec_sources = [s for s in sources if str(s.get("Feed / page") or "").startswith("https://data.sec.gov/submissions/")]
    web_sources = []
    for t in transcripts:
        rows = re.findall(r"\b[FT]\d{3}\b", str(t.get("Ledger rows / topics the AI reads it against") or ""))
        web_sources.append((t["ID"], f"{t.get('Company')} (call documents)", rows))
    for s in sources:
        if s["ID"] in WEB_RULES:
            web_sources.append((s["ID"], s.get("Source") or s["ID"], ledger_by_source.get(s["ID"], [])))
    web_sources.append(("NBIS-NEWS", "Nebius newsroom", ledger_by_source.get("NBIS", [])))

    state = load_json(STATE_PATH, {"sec": {}, "web": {}})
    state.setdefault("sec", {}); state.setdefault("web", {})
    out = {"new": [], "ignored": [], "errors": [], "baselined": [], "skipped": [], "checked": []}

    print(f"Watch list: {os.path.basename(wl_path)} | {len(sec_sources)} SEC feeds, {len(web_sources)} web pages")
    if args.only != "web":
        print("Checking the SEC…")
        check_sec(sec_sources, state, cfg, args, ledger_by_source, feeds_models, out, fetcher)
    if args.only != "sec" and not args.since:
        print("Checking web pages…")
        check_web(web_sources, state, cfg, args, out, fetcher)
    rank_and_match(out["new"], watch_rules(book))

    report = write_report(out, started, args)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    rpath = os.path.join(REPORTS_DIR, started.strftime("%Y-%m-%d %H.%M.%S") + (" test" if args.dry_run else "") + ".md")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(report)
    if not args.dry_run:
        # Persist discoveries before moving bookmarks, so an interrupted save can only repeat an item,
        # never move past one that was not handed over. The reader merges repeated URLs.
        append_jsonl(LOG_PATH, (dict(x, found_at=started.isoformat()) for x in out["new"]))
        handoff = dict(schema_version=1, run_id=started.isoformat(), report=os.path.basename(rpath),
                       scope=args.only or "all", **out)
        save_json(os.path.splitext(rpath)[0] + ".json", handoff)
        save_json(HANDOFF_PATH, handoff)
        state["last_run"] = started.isoformat()
        save_json(STATE_PATH, state)
    print()
    print(report)
    print(f"Report saved: {os.path.relpath(rpath)}")
    return out


if __name__ == "__main__":
    main()
