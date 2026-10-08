"""
Documents: finding the latest filing, results release, call transcript or 6-K for a company, turning it into text,
working out the period it reports on, and keeping a copy (with the AI's answer) so any run can be checked or replayed.
"""

import glob
import html
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

import companies as C
import watcher as W

from . import checks as K
from . import tables as T

READER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(READER, "documents")        # the saved documents and answers (the tests point this elsewhere)


# ------------------------------------------------------------------------------------------
# Fetching and turning documents into text
# ------------------------------------------------------------------------------------------
def fetch_bytes(url, ua, timeout=90):
    """Download a file (raises FetchError on failure)."""
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), (r.headers.get_content_type() or "")
    except urllib.error.HTTPError as e:
        raise W.FetchError(f"HTTP {e.code}")
    except Exception as e:
        raise W.FetchError(f"could not connect ({e.__class__.__name__}: {e})")


def _flatten_cell(m):
    inner = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6])\s*>", " ", m.group(2))
    return f"<t{m.group(1)}>{inner}</t{m.group(1)}>"


def html_to_text(raw):
    """An HTML filing as plain text, one table row per line with cells separated by ' | '."""
    t = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)           # hidden XBRL header
    t = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", t)
    t = re.sub(r"(?is)<t([dh])\b[^>]*>(.*?)</t\1\s*>", _flatten_cell, t)  # paragraphs inside a cell stay in the cell
    t = re.sub(r"(?i)</t[dh]\s*>", " | ", t)                           # table cells
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)\s*>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ").replace("​", "").replace(" ", " ")
    lines = []
    for line in t.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip(" |")
        line = re.sub(r"(\|\s*){2,}", "| ", line)                       # empty cells
        line = re.sub(r"\$\s*\|\s*", "$", line)                         # "$ | 1,234" -> "$1,234"
        line = re.sub(r"\(\s*\|\s*", "(", line).replace(" | )", ")")
        if line:
            lines.append(line)
    return "\n".join(lines)


def pdf_to_text(data):
    """A PDF transcript as plain text (needs the pypdf package)."""
    try:
        import pypdf
    except ImportError:
        raise W.FetchError("this transcript is a PDF: install pypdf first (python3 -m pip install --user pypdf)")
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def get_text(url, ua):
    """A document's text, from HTML or PDF."""
    data, ctype = fetch_bytes(url, ua)
    if url.lower().endswith(".pdf") or "pdf" in ctype:
        return pdf_to_text(data)
    return html_to_text(data.decode("utf-8", errors="replace"))


# ------------------------------------------------------------------------------------------
# Finding the latest document of each kind
# ------------------------------------------------------------------------------------------
def sec_recent(cik, ua):
    """A company's recent SEC filings, with the period each reports on and the SEC's fiscal year end."""
    status, body = W.fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", ua, "application/json")
    sub = json.loads(body)
    rows = W.sec_rows(sub)
    report = sub["filings"]["recent"].get("reportDate") or [""] * len(rows)
    fye = sub.get("fiscalYearEnd") or ""              # e.g. "0125": the SEC's record of the fiscal year end
    for r, d in zip(rows, report):
        r["report_date"] = d or ""
        r["sec_fye"] = fye
    return rows


def filing_files(cik, acc, ua):
    """The files in one SEC filing (to find a release's Ex 99 exhibits)."""
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
    status, body = W.fetch(base + "index.json", ua, "application/json")
    return base, [i["name"] for i in json.loads(body)["directory"]["item"]]


def exhibit_documents(ticker, kind, row, ua):
    """Financial exhibits from this exact filing, shared by the normal search and watcher handoff."""
    base, names = filing_files(C.CIK[ticker], row["acc"], ua)
    if kind == "results":
        ex = [n for n in names if re.search(r"\.html?$", n, re.I) and re.search(r"ex[-_]?99|ex99|exhibit99|99[-_.]?[12]|cfocommentary|pressrelease|pr\.htm", n, re.I)]
        return [dict(title=f"Results release (8-K filed {row['filed']}): {n}", url=base + n,
                     filed=row["filed"], form="8-K", report_date="", sec_fye=row.get("sec_fye", "")) for n in ex]
    hit = [n for n in names if re.search(r"ex99[-_.]?d?2|ex-?99[-_.]2", n, re.I) and re.search(r"\.html?$", n, re.I)]
    return [dict(title=f"6-K filed {row['filed']}: {hit[0]}", url=base + hit[0], filed=row["filed"],
                 form="6-K", report_date="", sec_fye=row.get("sec_fye", ""))] if hit else []


def find_doc(ticker, kind, ua):
    """Return a list of dicts (title, url, filed, form, report_date) for the latest document of this kind."""
    cik = C.CIK[ticker]
    if kind in ("periodic", "results", "6k"):
        rows = sec_recent(cik, ua)
        time.sleep(0.15)
        if kind == "periodic":
            r = next((r for r in rows if r["form"] in ("10-Q", "10-K")), None)
            if not r:
                return []
            return [dict(title=f"{r['form']} filed {r['filed']}", url=W.filing_url(cik, r["acc"], r["doc"]), filed=r["filed"],
                         form=r["form"], report_date=r.get("report_date", ""), sec_fye=r.get("sec_fye", ""))]
        if kind == "results":
            r = next((r for r in rows if r["form"] == "8-K" and "2.02" in r["items"]), None)
            if not r:
                return []
            return exhibit_documents(ticker, kind, r, ua)
        if kind == "6k":
            for r in rows:
                if r["form"] != "6-K":
                    continue
                found = exhibit_documents(ticker, kind, r, ua)
                time.sleep(0.15)
                if found:
                    return found
            return []
    if kind == "transcript":
        rule = W.WEB_RULES.get(f"TR-{ticker}") or {}
        if rule.get("type") == "q4":
            status, body = W.fetch(rule["base"] + W.Q4_FEED, W.BROWSER_UA, "application/json")
            order = {"First Quarter": 1, "Second Quarter": 2, "Third Quarter": 3, "Fourth Quarter": 4}
            best = None
            for rep in json.loads(body).get("GetFinancialReportListResult") or []:
                for d in rep.get("Documents") or []:
                    if re.search(r"transcript", f"{d.get('DocumentTitle')} {d.get('DocumentCategory')}", re.I) and not re.search(r"follow", d.get("DocumentTitle", ""), re.I):
                        key = (rep.get("ReportYear") or 0, order.get(rep.get("ReportSubType"), 0))
                        if best is None or key > best[0]:
                            best = (key, f"{rep.get('ReportTitle')}: {d.get('DocumentTitle')}", d.get("DocumentPath"))
            return [dict(title=best[1], url=best[2], filed="", form="transcript", report_date="")] if best else []
        if rule.get("site") == "msft":
            state = W.load_json(W.STATE_PATH, {}).get("web", {}).get("TR-MSFT", {})
            current = state.get("current", rule["url"])
            for url in (W.next_quarter_url(current, "msft"), current):
                status, body = W.fetch(url, W.BROWSER_UA, "text/html")
                if status == 200 and re.search(r"\bOperator\b|Amy Hood", W.page_text(body)):
                    return [dict(title="Microsoft earnings call transcript", url=url, filed="", form="transcript", report_date="")]
            return []
    return []


# ------------------------------------------------------------------------------------------
# What a document reports on
# ------------------------------------------------------------------------------------------
def describe_doc(meta, text, ticker=None):
    """Fill in the dates the checks need: the period the document reports on, and the date it was given.
    A call transcript's period is its fiscal quarter, read from its title or link ('Second Quarter 2027',
    '.../earnings-fy-2026-q4') with the company's fiscal year end, and kept only if the call was held within
    four months after that quarter ended."""
    fye = C.FYE.get(ticker) if ticker else None
    form = meta.get("form") or ""
    if not form:
        t = meta.get("title", "")
        form = ("10-K" if t.startswith("10-K") else "10-Q" if t.startswith("10-Q") else "8-K" if "8-K" in t
                else "6-K" if t.startswith("6-K") else "transcript")
    filed = meta.get("filed") or (re.search(r"filed (\d{4}-\d{2}-\d{2})", meta.get("title", "")) or [None, ""])[1]
    period = K.parse_date(meta.get("report_date")) or K.doc_period_end(text, filed or None)
    if not period and form in ("10-Q", "10-K"):
        m = re.search(r"-(20\d{2})(\d{2})(\d{2})\.htm", meta.get("url", ""))
        period = K.make_date(*m.groups()) if m else None
    date = K.parse_date(filed) if filed else K.doc_date(text, meta.get("title", ""), meta.get("url", ""))
    fq = ""
    if form == "transcript" and fye:
        q = T.fiscal_quarter_in(f"{meta.get('title', '')} {meta.get('url', '')}", fye)
        qend = K.month_end(*q["end"]) if q else None
        if q and date and qend <= date and (date - qend).days <= 120:
            fq = q["label"]
            if not (period and K.month_key(period) == q["end"]):
                period = qend                      # the quarter's month (the day may differ for 52/53-week years)
        elif period and date and not (period <= date and (date - period).days <= 120):
            period = None
    return dict(meta, form=form, filed=filed or "", period_end=period.isoformat() if period else "",
                date=date.isoformat() if date else "", fiscal_quarter=fq, fye=fye or "")


def fye_problem(meta, ticker):
    """A warning if the SEC's record of the fiscal year end differs from the one in companies.py, or None."""
    sec_fye = str(meta.get("sec_fye") or "")
    fye = C.FYE.get(ticker)
    if re.fullmatch(r"\d{4}", sec_fye) and int(sec_fye[:2]) != fye and \
            not (fye == 1 and sec_fye[:2] == "02" and int(sec_fye[2:]) <= 7):
        return (f"{C.NAME[ticker]}: the SEC records the fiscal year end as {sec_fye[:2]}-{sec_fye[2:]}, but the reader "
                f"assumes month {fye}: fiscal labels such as 'Q2 FY27' may be read wrongly (update its fiscal year end in companies.py)")
    return None


def fill_release_periods(jobs):
    """A release without its own 'quarter ended' takes its period from the other exhibits of the same filing."""
    for j in jobs:
        if not j["meta"]["period_end"] and j["meta"]["form"] in ("8-K", "6-K"):
            acc = re.search(r"/data/\d+/(\d+)/", j["meta"]["url"] or "")
            sib = [k for k in jobs if k is not j and acc and acc.group(1) in (k["meta"]["url"] or "") and k["meta"]["period_end"]]
            if sib:
                j["meta"]["period_end"] = sib[0]["meta"]["period_end"]


# ------------------------------------------------------------------------------------------
# Saved copies (documents read, and the AI's raw answers)
# ------------------------------------------------------------------------------------------
def cache_write(stem, meta, text):
    """Keep the document's text and its dates, so the run can be checked or replayed later."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, stem + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"{meta['title']}\n{meta['url']}\n\n{text}")
    with open(os.path.join(CACHE_DIR, stem + ".meta.json"), "w", encoding="utf-8") as f:
        json.dump({k: meta.get(k, "") for k in ("title", "url", "filed", "form", "report_date", "period_end", "date")}, f, indent=1)


def save_answer(stem, started, answer):
    """Keep the AI's raw answer next to its document. A new file every run: earlier answers are never overwritten."""
    with open(os.path.join(CACHE_DIR, f"{stem}.answer {started.strftime('%Y-%m-%d %H.%M.%S')}.json"), "w", encoding="utf-8") as f:
        json.dump(answer, f, ensure_ascii=False, indent=1)


def cache_read_latest(ticker, kind):
    """The most recent cached documents for this company and kind: [(stem, meta, text)]."""
    pat = os.path.join(CACHE_DIR, f"{ticker}_{kind}_*.txt")
    files = sorted(glob.glob(pat))
    if not files:
        return []
    date_of = lambda p: re.search(r"_(\d{4}-\d{2}-\d{2})_\d+\.txt$", p).group(1) if re.search(r"_(\d{4}-\d{2}-\d{2})_\d+\.txt$", p) else ""
    latest = max(date_of(p) for p in files)
    out = []
    for p in sorted((p for p in files if date_of(p) == latest), key=lambda p: int(re.search(r"_(\d+)\.txt$", p).group(1))):
        stem = os.path.basename(p)[:-4]
        raw = open(p, encoding="utf-8").read()
        title, url, _, text = (raw.split("\n", 3) + ["", "", "", ""])[:4]
        meta = dict(title=title, url=url)
        mp = os.path.join(CACHE_DIR, stem + ".meta.json")
        if os.path.exists(mp):
            meta.update(json.load(open(mp, encoding="utf-8")))
        out.append((stem, meta, text))
    return out
