#!/usr/bin/env python3
"""
Reader, Part A: the paper's labelled SEC numbers.

For every figure in the paper that the SEC publishes with a label (revenue, capex,
cash and so on), this fetches the latest value, works out the period correctly
(quarter, year to date, full year or a single date), and compares it with the
paper. It also checks whether the 20 basket companies have reported a newer
quarter than the basket model uses.

It changes nothing. It writes a list of proposed changes for you to approve.

Run it from the project folder, in Antigravity's terminal:

    python3 Agent/reader/reader_sec.py

It uses the name and email in Agent/watcher/config.json.
Standard library only: nothing to install.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(AGENT, "watcher"))
import watcher as W  # reuse the workbook reader and the fetch function
from companies import CIK  # the SEC numbers, with the names and fiscal year ends, are in companies.py

REPORTS_DIR = os.path.join(HERE, "reports")
PROPOSED_PATH = os.path.join(HERE, "proposed.jsonl")

OCF = "NetCashProvidedByUsedInOperatingActivities"
CAPEX = "PaymentsToAcquirePropertyPlantAndEquipment"
REV_C = "RevenueFromContractWithCustomerExcludingAssessedTax"

# ------------------------------------------------------------------------------------------
# The paper's labelled figures. Labels and period types checked against live SEC data on
# 26 Sep 2026. "paper" is the value in the paper ($bn unless unit says otherwise); "end" is the
# date the paper's figure covers; "dp" is how many decimals the paper gives (used to compare).
#   ("q", concept)    one quarter (worked out from year-to-date figures if needed)
#   ("ytd", concept)  year to date, as reported
#   ("fy", concept)   full fiscal year
#   ("i", concept)    a balance on one date
# ------------------------------------------------------------------------------------------
def Q(c): return ("q", c)
def YTD(c): return ("ytd", c)
def FY(c): return ("fy", c)
def I(c): return ("i", c)

SPECS = [
    dict(id="F002", co="NVDA", what="Nvidia revenue, quarter", f=lambda v: v[0], parts=[Q("Revenues")], paper=96.221, end="2026-07-26", dp=3),
    dict(id="F007", co="GOOGL", what="Alphabet operating cash flow, quarter", f=lambda v: v[0], parts=[Q(OCF)], paper=39.069, end="2026-06-30", dp=3),
    dict(id="F008", co="GOOGL", what="Alphabet capex, quarter", f=lambda v: v[0], parts=[Q(CAPEX)], paper=44.924, end="2026-06-30", dp=3),
    dict(id="F009", co="GOOGL", what="Alphabet free cash flow, quarter", f=lambda v: v[0] - v[1], parts=[Q(OCF), Q(CAPEX)], paper=-5.855, end="2026-06-30", dp=3),
    dict(id="F012", co="ORCL", what="Oracle free cash flow, fiscal year", f=lambda v: v[0] - v[1], parts=[FY(OCF), FY(CAPEX)], paper=-23.686, end="2026-05-31", dp=3),
    dict(id="F013", co="ORCL", what="Oracle operating cash flow, fiscal year", f=lambda v: v[0], parts=[FY(OCF)], paper=31.977, end="2026-05-31", dp=3),
    dict(id="F014", co="ORCL", what="Oracle capex, fiscal year", f=lambda v: v[0], parts=[FY(CAPEX)], paper=55.663, end="2026-05-31", dp=3),
    dict(id="F015", co="ORCL", what="Oracle cash and cash equivalents", f=lambda v: v[0], parts=[I("CashAndCashEquivalentsAtCarryingValue")], paper=31.289, end="2026-05-31", dp=3,
         note="The paper deliberately quotes the year-end balance and says so."),
    dict(id="F016", co="META", what="Meta free cash flow, quarter (Meta's definition)", f=lambda v: v[0] - v[1] - v[2],
         parts=[Q(OCF), Q(CAPEX), Q("FinanceLeasePrincipalPayments")], paper=0.784, end="2026-06-30", dp=3),
    dict(id="F017", co="META", what="Meta capex incl. finance-lease principal, quarter", f=lambda v: v[0] + v[1],
         parts=[Q(CAPEX), Q("FinanceLeasePrincipalPayments")], paper=30, end="2026-06-30", dp=0,
         note="Known issue: the paper says '$30 billions'; the release gives $31.08bn."),
    dict(id="F047", co="MSFT", what="Microsoft assets obtained under finance leases, fiscal year", f=lambda v: v[0],
         parts=[FY("RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability")], paper=24.608, end="2026-06-30", dp=3),
    dict(id="F048", co="MSFT", what="Microsoft capex, fiscal year", f=lambda v: v[0], parts=[FY(CAPEX)], paper=115.948, end="2026-06-30", dp=3),
    dict(id="F049", co="CRWV", what="CoreWeave capex, year to date", f=lambda v: v[0], parts=[YTD(CAPEX)], paper=14.117, end="2026-06-30", dp=3),
    dict(id="F060", co="META", what="Meta leases not yet commenced", f=lambda v: v[0],
         parts=[I("UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount")], paper=103.77, end="2025-12-31", dp=2,
         note="Meta files this amount under a general 'purchase obligation' label; confirm in the 10-Q text that it is still the leases figure."),
    dict(id="F087", co="MSFT", what="Microsoft total remaining performance obligations", f=lambda v: v[0],
         parts=[I("RevenueRemainingPerformanceObligation")], paper=684, end="2026-06-30", dp=0,
         note="The paper quotes commercial RPO ($678bn) from the call; this is total RPO from the filings. A newer date means a new RPO figure to read."),
    dict(id="F100", co="MSFT", what="Microsoft days sales outstanding", unit="days", f=lambda v: v[0] / v[1] * 365,
         parts=[I("AccountsReceivableNetCurrent"), FY(REV_C)], paper=89, end="2026-06-30", dp=0),
    dict(id="F111", co="MSFT", what="Microsoft operating cash flow, fiscal year", f=lambda v: v[0], parts=[FY(OCF)], paper=182.935, end="2026-06-30", dp=3),
    dict(id="F113", co="CRWV", what="CoreWeave operating cash flow, year to date", f=lambda v: v[0], parts=[YTD(OCF)], paper=3.663, end="2026-06-30", dp=3),
    dict(id="F120", co="NVDA", what="Nvidia cost of revenue, quarter", f=lambda v: v[0], parts=[Q("CostOfRevenue")], paper=24.079, end="2026-07-26", dp=3),
    dict(id="F120", co="NVDA", what="Nvidia operating expenses, quarter", f=lambda v: v[0], parts=[Q("OperatingExpenses")], paper=8.408, end="2026-07-26", dp=3),
    dict(id="F120", co="NVDA", what="Nvidia operating income, quarter", f=lambda v: v[0], parts=[Q("OperatingIncomeLoss")], paper=63.734, end="2026-07-26", dp=3),
    dict(id="F120", co="NVDA", what="Nvidia net income, quarter", f=lambda v: v[0], parts=[Q("NetIncomeLoss")], paper=59.688, end="2026-07-26", dp=3),
    dict(id="F121", co="NVDA", what="Nvidia diluted shares, quarter (billions)", unit="bn shares", f=lambda v: v[0],
         parts=[Q("WeightedAverageNumberOfDilutedSharesOutstanding")], paper=24.285, end="2026-07-26", dp=3),
    dict(id="F121", co="NVDA", what="Nvidia effective tax rate, quarter", unit="%", f=lambda v: v[0] / v[1] * 100,
         parts=[Q("IncomeTaxExpenseBenefit"), Q("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")],
         paper=16.53, end="2026-07-26", dp=2),
    dict(id="F122", co="NVDA", what="Nvidia gains on investments, quarter", f=lambda v: v[0], parts=[Q("GainLossOnInvestments")], paper=7.771, end="2026-07-26", dp=3),
    dict(id="F134", co="ORCL", what="Oracle construction in progress", f=lambda v: v[0], parts=[I("ConstructionInProgressGross")], paper=48.546, end="2026-08-31", dp=3),
    dict(id="F141", co="CRWV", what="CoreWeave revenue, latest quarter x 4", f=lambda v: v[0] * 4, parts=[Q(REV_C)], paper=10.3, end="2026-06-30", dp=1),
    dict(id="F143", co="ORCL", what="Oracle revenue, latest quarter x 4", f=lambda v: v[0] * 4, parts=[Q(REV_C)], paper=77.4, end="2026-08-31", dp=1),
    dict(id="F143", co="GOOGL", what="Alphabet revenue, latest quarter x 4", f=lambda v: v[0] * 4, parts=[Q("Revenues")], paper=479, end="2026-06-30", dp=0),
    dict(id="F148", co="ORCL", what="Oracle operating cash flow, quarter", f=lambda v: v[0], parts=[Q(OCF)], paper=23.103, end="2026-08-31", dp=3),
    dict(id="F149", co="ORCL", what="Oracle capex, latest quarter x 4", f=lambda v: v[0] * 4, parts=[Q(CAPEX)], paper=114, end="2026-08-31", dp=0),
    dict(id="F149", co="ORCL", what="Oracle dividends paid, latest quarter x 4", f=lambda v: v[0] * 4, parts=[Q("PaymentsOfDividendsCommonStock")], paper=6.3, end="2026-08-31", dp=1),
    dict(id="F150", co="CRWV", what="CoreWeave customer prepayments, year to date annualised", f=lambda v: v[0], parts=[("ytd_ann", "IncreaseDecreaseInContractWithCustomerLiability")],
         paper=2.7, end="2026-06-30", dp=1),
]

# ------------------------------------------------------------------------------------------
# Working with SEC company facts
# ------------------------------------------------------------------------------------------
def days(start, end):
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


class Facts:
    """All values for one company and one label, with the period arithmetic."""

    def __init__(self, raw_units):
        unit = "USD" if "USD" in raw_units else ("shares" if "shares" in raw_units else next(iter(raw_units)))
        self.unit = unit
        best = {}
        for x in raw_units[unit]:
            key = (x.get("start"), x["end"])
            if key not in best or x.get("filed", "") > best[key].get("filed", ""):
                best[key] = x  # keep the most recently filed version of each period (restatements win)
        self.rows = list(best.values())

    def instant(self, end=None):
        rows = [r for r in self.rows if not r.get("start")]
        if end:
            rows = [r for r in rows if r["end"] == end]
        return max(rows, key=lambda r: r["end"]) if rows else None

    def _dur(self, lo, hi, end=None):
        rows = [r for r in self.rows if r.get("start") and lo <= days(r["start"], r["end"]) <= hi]
        if end:
            rows = [r for r in rows if r["end"] == end]
        return rows

    def fy(self, end=None):
        rows = self._dur(350, 380, end)
        return max(rows, key=lambda r: r["end"]) if rows else None

    def ytd(self, end=None):
        """The year-to-date figure ending on `end` (the longest one up to a year)."""
        rows = self._dur(80, 380, end)
        if not rows:
            return None
        top = max(r["end"] for r in rows)
        rows = [r for r in rows if r["end"] == top]
        return max(rows, key=lambda r: days(r["start"], r["end"]))

    def quarter(self, end=None):
        """One quarter: the three-month figure if filed, otherwise year-to-date minus the previous year-to-date."""
        ends = sorted({r["end"] for r in self._dur(80, 380)}, reverse=True)
        for e in ([end] if end else ends):
            q = self._dur(80, 100, e)
            if q:
                r = max(q, key=lambda r: r.get("filed", ""))
                return dict(r, how="three-month figure")
            longest = self.ytd(e)
            if not longest:
                continue
            prior = [r for r in self._dur(80, 300) if r["start"] == longest["start"] and r["end"] < e]
            if prior:
                p = max(prior, key=lambda r: r["end"])
                return dict(longest, val=longest["val"] - p["val"], start=p["end"],
                            how=f"year to date to {e} minus year to date to {p['end']}")
        return None

    def latest_end(self, kind):
        if kind == "i":
            r = self.instant()
        elif kind == "fy":
            r = self.fy()
        else:
            r = self.ytd()
        return r["end"] if r else None

    def get(self, kind, end=None):
        if kind == "i":
            return self.instant(end)
        if kind == "fy":
            return self.fy(end)
        if kind == "ytd":
            return self.ytd(end)
        if kind == "q":
            return self.quarter(end)
        if kind == "ytd_ann":
            r = self.ytd(end)
            if r:
                months = round(days(r["start"], r["end"]) / 30.44)
                return dict(r, val=r["val"] * 12 / months, how=f"{months} months to {r['end']}, annualised")
            return None
        raise ValueError(kind)


def bn(val):
    return val / 1e9  # dollars -> $bn, shares -> billions


def evaluate(spec, facts, end):
    """Value of a spec at a period end (None if any part is missing)."""
    vals, used = [], []
    for kind, concept in spec["parts"]:
        f = facts.get(concept)
        if f is None:
            return None, [], f"label {concept} not in the SEC data"
        r = f.get(kind, end)
        if r is None:
            return None, [], None
        vals.append(r["val"] if spec.get("unit") in ("days", "%") else bn(r["val"]))
        used.append(r)
    return spec["f"](vals), used, None


def latest_common_end(spec, facts):
    ends = []
    for kind, concept in spec["parts"]:
        f = facts.get(concept)
        if f is None:
            return None
        ends.append(f.latest_end("ytd" if kind in ("q", "ytd", "ytd_ann") else kind))
    if any(e is None for e in ends):
        return None
    return min(ends)


def filing_link(cik, accn):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/" if accn else ""


def fmt(v, dp, unit=None):
    if v is None:
        return "—"
    if unit == "%":
        return f"{v:.{dp}f}%"
    if unit == "days":
        return f"{v:.{dp}f} days"
    if unit == "bn shares":
        return f"{v:,.{dp}f}bn shares"
    return ("-" if v < 0 else "") + f"${abs(v):,.{dp}f}bn"


# ------------------------------------------------------------------------------------------
# The steps of a run
# ------------------------------------------------------------------------------------------
BASKET_REVENUE = (REV_C, "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax")  # tried in this order
BASKET_LABELS = {"UP TO DATE": "up to date", "NOT IN SEC DATA": "not in the SEC's labelled data", "SEC DATA BEHIND": "SEC data is behind",
                 "DIFFERS FROM FILINGS": "the model's revenue differs from the filings"}


def sec_user_agent(cfg):
    """The SEC asks every program to give a name and email."""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cfg.get("contact_email") or ""):
        sys.exit("Put your email in Agent/watcher/config.json first (the SEC asks for it).")
    return f"{cfg.get('contact_name') or 'Reader'} {cfg['contact_email']}"


def read_watch_list(cfg):
    """The ledger (for each row's paper section) and the basket model's inputs."""
    path = os.path.normpath(os.path.join(W.HERE, cfg.get("watch_list", "../Watch list - The AI Lose Lose Race.xlsx")))
    book = W.read_workbook(path)
    ledger = {r["ID"]: r for r in W.table(book.get("Ledger", []))}
    basket = W.table(book.get("Basket inputs", []), first_header="Ticker")
    return ledger, basket


def load_company_facts(tickers, fetcher, ua):
    """The SEC's labelled data for each company, as {ticker: {label: Facts}}, and what could not be loaded."""
    company_facts, errors = {}, []
    print(f"Reading SEC data for {len(tickers)} companies…")
    for n, t in enumerate(tickers, 1):
        print(f"  {n}/{len(tickers)} {t}", flush=True)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK[t]:010d}.json"
        try:
            status, body = fetcher(url, ua, "application/json", 60)
            if status != 200:
                raise W.FetchError(f"HTTP {status}")
            data = json.loads(body)
            company_facts[t] = {c: Facts(v["units"]) for c, v in (data.get("facts", {}).get("us-gaap") or {}).items() if v.get("units")}
        except (W.FetchError, ValueError) as e:
            errors.append(f"{t}: {e}")
        time.sleep(0.15)
    return company_facts, errors


def check_figure(s, facts, ledger):
    """One of the paper's figures against the SEC data: newer figure, differs, up to date, or could not check."""
    row = dict(id=s["id"], what=s["what"], co=s["co"], paper=s["paper"], paper_end=s["end"], dp=s["dp"],
               unit=s.get("unit"), note=s.get("note", ""), section=(ledger.get(s["id"]) or {}).get("Paper section", ""))
    if facts is None:
        return dict(row, status="COULD NOT CHECK", why="SEC data not loaded")
    at_paper, used_p, err = evaluate(s, facts, s["end"])
    if err:
        return dict(row, status="COULD NOT CHECK", why=err)
    latest_end = latest_common_end(s, facts)
    latest, used_l, _ = evaluate(s, facts, latest_end) if latest_end else (None, [], None)
    tol = 0.5 * 10 ** (-s["dp"]) + 1e-9
    row["filings_value"] = at_paper
    row["matches_filings"] = (at_paper is not None and abs(round(at_paper, s["dp"]) - s["paper"]) <= tol)
    if latest_end and latest_end > s["end"] and latest is not None:
        src = max(used_l, key=lambda r: r.get("filed", ""))
        row.update(status="NEWER FIGURE", new=latest, new_end=latest_end, form=src.get("form"), filed=src.get("filed"),
                   link=filing_link(CIK[s["co"]], src.get("accn")), how="; ".join(r.get("how", "") for r in used_l if r.get("how")))
    elif at_paper is None:
        row.update(status="COULD NOT CHECK", why=f"no SEC value for the period ending {s['end']}")
    elif row["matches_filings"]:
        row.update(status="UP TO DATE")
    else:
        src = max(used_p, key=lambda r: r.get("filed", ""))
        row.update(status="DIFFERS FROM FILINGS", form=src.get("form"), filed=src.get("filed"), link=filing_link(CIK[s["co"]], src.get("accn")))
    return row


def check_basket_company(b, facts):
    """Has a basket company reported a newer quarter than the model uses?"""
    t = str(b["Ticker"]).upper()
    best = None
    for c in BASKET_REVENUE:
        f = facts.get(c)
        if not f or f.unit != "USD":
            continue
        q = f.quarter()
        if q and (best is None or q["end"] > best[1]["end"]):
            best = (c, q)
    model_end = str(b.get("Quarter ended") or "")[:10]
    model_rev = b.get("Quarter revenue")
    entry = dict(t=t, model_end=model_end, model_rev=model_rev, currency=b.get("Currency"))
    if best is not None and best[1]["end"] < "2025-09-01":
        best = None  # only old or annual figures: treat as not available
    if best is None:
        entry.update(status="NOT IN SEC DATA", why="Foreign filer: its quarterly figures come on Form 6-K, which the SEC does not label. Read the release")
        return entry
    q = best[1]
    op = facts.get("OperatingIncomeLoss")
    opq = op.quarter(q["end"]) if op else None
    entry.update(sec_end=q["end"], sec_rev=bn(q["val"]), sec_op=bn(opq["val"]) if opq else None,
                 form=q.get("form"), filed=q.get("filed"), link=filing_link(CIK[t], q.get("accn")))
    if q["end"] > model_end:
        entry["status"] = "NEWER QUARTER"
    elif q["end"] < model_end:
        entry.update(status="SEC DATA BEHIND", why="The SEC's labelled data has not caught up with the latest filing: read the filing")
    else:
        same = model_rev is not None and abs(entry["sec_rev"] - float(model_rev)) <= 0.0015
        entry["status"] = "UP TO DATE" if same else "DIFFERS FROM FILINGS"
    return entry


def by_status(rows, status):
    return [r for r in rows if r["status"] == status]


def paper_value(r):
    return fmt(r["paper"], r["dp"], r.get("unit"))


def filing_cell(r):
    return f"[{r.get('form')} filed {r.get('filed')}]({r.get('link')})"


def build_report(started, results, basket_rows, errors):
    newer, differ = by_status(results, "NEWER FIGURE"), by_status(results, "DIFFERS FROM FILINGS")
    ok, bad = by_status(results, "UP TO DATE"), by_status(results, "COULD NOT CHECK")
    bnew = by_status(basket_rows, "NEWER QUARTER")
    other_b = [b for b in basket_rows if b["status"] != "NEWER QUARTER"]
    L = [f"# Reader Part A: the paper's labelled SEC figures, {started.strftime('%d %b %Y %H:%M')}", "",
         f"**Paper figures checked: {len(results)}.** Newer figure published: {len(newer)}. Differs from the filings: {len(differ)}. "
         f"Up to date: {len(ok)}. Could not check: {len(bad)}.",
         f"**Basket companies: {len(basket_rows)}.** Newer quarter than the model uses: {len(bnew)}.", "",
         "Nothing has been changed. Each line below is a proposal for you to approve or reject."]
    if newer:
        L += ["", "## Newer figure published since the paper's figure", "",
              "| Row | Figure | Paper | Paper's date | Latest | Latest date | Filing |", "|---|---|---|---|---|---|---|"]
        for r in newer:
            L.append(f"| {r['id']} | {r['what']} | {paper_value(r)} | {r['paper_end']} | **{fmt(r['new'], r['dp'], r.get('unit'))}** | {r['new_end']} | "
                     f"{filing_cell(r)} |")
        for r in newer:
            if r["note"] or not r["matches_filings"]:
                extra = [] if r["matches_filings"] else [f"the paper's own period gives {fmt(r['filings_value'], r['dp'], r.get('unit'))} in the filings"]
                L.append(f"- {r['id']}: " + "; ".join(([r["note"]] if r["note"] else []) + extra))
    if differ:
        L += ["", "## Same period, but the paper's figure differs from the filings", "",
              "| Row | Figure | Paper | Filings | Period | Filing |", "|---|---|---|---|---|---|"]
        for r in differ:
            L.append(f"| {r['id']} | {r['what']} | {paper_value(r)} | **{fmt(r['filings_value'], min(r['dp'] + 2, 3), r.get('unit'))}** | {r['paper_end']} | "
                     f"{filing_cell(r)} |")
        L += [f"- {r['id']}: {r['note']}" for r in differ if r["note"]]
    if bnew:
        L += ["", "## Basket companies with a newer quarter", "",
              "| Company | Model's quarter | Newer quarter | Revenue | Operating profit | Filing |", "|---|---|---|---|---|---|"]
        for b in bnew:
            L.append(f"| {b['t']} | {b['model_end']} | {b['sec_end']} | {fmt(b['sec_rev'], 3)} | {fmt(b['sec_op'], 3)} | {filing_cell(b)} |")
    if ok:
        L += ["", "## Up to date (the paper matches the latest filing)", ""]
        L += [f"- {r['id']} {r['what']}: {paper_value(r)} at {r['paper_end']}" for r in ok]
    if other_b:
        L += ["", "## Basket: other companies", ""]
        for b in other_b:
            tail = f" (SEC: quarter to {b['sec_end']}, revenue {fmt(b['sec_rev'], 3)})" if b.get("sec_end") else ""
            label = BASKET_LABELS.get(b["status"], b["status"].lower())
            L.append(f"- {b['t']}: {label}{tail}" + (f". {b['why']}" if b.get("why") else ""))
    if bad or errors:
        L += ["", "## Could not check", ""]
        L += [f"- {r['id']} {r['what']}: {r.get('why')}" for r in bad]
        L += [f"- {e}" for e in errors]
    L += ["", "---", "Figures are in billions unless marked. Quarters that the SEC only publishes as year-to-date totals are worked out as the difference between two year-to-date figures."]
    return "\n".join(L) + "\n"


def proposals(started, results, basket_rows):
    """The lines for proposed.jsonl: newer figures, then figures that differ, then basket companies with a newer quarter."""
    out = []
    for r in by_status(results, "NEWER FIGURE") + by_status(results, "DIFFERS FROM FILINGS"):
        out.append({"found_at": started.isoformat(), "part": "A", "row": r["id"], "figure": r["what"],
                    "paper_value": r["paper"], "paper_period_end": r["paper_end"],
                    "new_value": round(r["new"], 6) if r["status"] == "NEWER FIGURE" else round(r["filings_value"], 6),
                    "new_period_end": r.get("new_end", r["paper_end"]), "status": r["status"],
                    "unit": r.get("unit") or "$bn", "filing": r.get("link"), "form": r.get("form"), "filed": r.get("filed"),
                    "note": r["note"], "decision": "pending"})
    for b in by_status(basket_rows, "NEWER QUARTER"):
        out.append({"found_at": started.isoformat(), "part": "A", "row": f"Basket:{b['t']}", "figure": f"{b['t']} quarterly revenue",
                    "paper_value": b["model_rev"], "paper_period_end": b["model_end"], "new_value": round(b["sec_rev"], 6),
                    "new_operating_profit": round(b["sec_op"], 6) if b.get("sec_op") is not None else None,
                    "new_period_end": b["sec_end"], "status": "NEWER QUARTER", "unit": "$bn", "filing": b.get("link"),
                    "form": b.get("form"), "filed": b.get("filed"), "decision": "pending"})
    return out


def save(report, started, lines):
    """The report goes to reports/; the proposals are added to the end of proposed.jsonl (earlier lines never change)."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    rpath = os.path.join(REPORTS_DIR, "Part A " + started.strftime("%Y-%m-%d %H.%M.%S") + ".md")
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(report)
    with open(PROPOSED_PATH, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return rpath


# ------------------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------------------
def main(fetcher=W.fetch, argv=None):
    argparse.ArgumentParser(description="Compare the paper's labelled SEC figures with the latest filings (no options).").parse_args(argv)
    cfg = W.load_json(W.CONFIG_PATH, {})
    ua = sec_user_agent(cfg)
    ledger, basket = read_watch_list(cfg)
    started = W.now_local()
    tickers = sorted({s["co"] for s in SPECS} | {str(b["Ticker"]).upper() for b in basket})
    company_facts, errors = load_company_facts(tickers, fetcher, ua)
    results = [check_figure(s, company_facts.get(s["co"]), ledger) for s in SPECS]
    basket_rows = [check_basket_company(b, company_facts.get(str(b["Ticker"]).upper()) or {}) for b in basket]
    report = build_report(started, results, basket_rows, errors)
    rpath = save(report, started, proposals(started, results, basket_rows))
    print()
    print(report)
    print(f"Report saved: {os.path.relpath(rpath)}")
    return results, basket_rows


if __name__ == "__main__":
    main()
