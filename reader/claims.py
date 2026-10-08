"""
What each ledger row means, in a form the code can check. The claim IDs are the ledger's IDs
(Watch list, Ledger sheet); the paper section, footnote and "also used in" links are read from
the ledger at run time, so nothing here replaces them.

Each claim has a TYPE:
  track       a figure meant to show the latest position (leases, RPO, run rates, a current policy).
              A newer document can update it.
  historical  a dated statement (who said what on which day). A newer document cannot update it;
              the reader only notes related newer statements, for context.
  absence     a claim that something is NOT disclosed. The reader looks for the figure and, if it
              is not there, must show verified evidence of what the document does disclose instead.

Each claim is split into COMPONENTS, one per company and per number, each with its own value,
unit, period end and period type, and the words the document must use for that measure. A claim
that combines companies (F152, F135, F150, F154, F155, F078, F035) is compared company by company.

FORMULAS turn components into the figures the paper prints (sums, annualising, trailing years,
ratios). The code evaluates them; the AI never does arithmetic. Every formula is printed in the
report with its inputs, so it can be checked by hand.

Period types: instant (a balance at a date), 3m / 6m / 9m / 12m (a period ending on period_end),
ttm, said (a statement made on that date), event (something that happened on that date),
policy (an accounting policy in force at that date).

PLAN (at the end of this file) says which rows are checked in which kind of document.
"""

import ast
import operator

MODEL_T5 = "Test 5 - equipment earns for fewer years.xlsx (Assumptions sheet)"
MODEL_T6 = "Test 6 - reconciled v3.xlsx (Inputs sheet)"


def C(company, key, label, value, unit, period_end, period_type, measure, must=None, must_not=None,
      forms=None, disclosed=None, derive=None, note="", min_doc_period=None, text=False, comparative=False, months=None):
    """One component. `value` is what the paper (or its model) uses, as a string so its precision is kept.
    `must`: groups of phrases; each group needs one of its phrases near the figure (wrong-metric guard).
    `must_not`: phrases that must not be in the value fragment. `forms`: only these forms carry it
    (e.g. ['10-K']). `disclosed` + `derive`: the document gives a different period (e.g. six months);
    the code applies `derive` to reach the paper's figure: 'annualise' (x * 12 / months in the period) or a
    formula in x. `comparative`: the figure is the prior-year comparative column (same period a year earlier)."""
    return dict(company=company, key=key, label=label, value=value, unit=unit, period_end=period_end,
                period_type=period_type, measure=measure, must=must or [], must_not=must_not or [],
                forms=forms, disclosed=disclosed, derive=derive, note=note, min_doc_period=min_doc_period, text=text,
                comparative=comparative, months=months)


def F(name, expr, paper, unit, label="", periods=None):
    """A formula over component keys. `periods`: rules its inputs' periods must meet before the code will combine
    them: ("same", [keys]) same end date; ("year_apart", a, b) b is the same period a year before a;
    ("follows", fy, ytd) the year to date starts straight after the fiscal year."""
    return dict(name=name, expr=expr, paper=paper, unit=unit, label=label or name, periods=periods or [])


LEASES_NC = [["not yet commenced", "have not commenced", "had not yet commenced", "not commenced"]]
CIP = [["construction in progress"]]

CLAIMS = {
    # ---------------- Intro
    "F003": dict(type="track", components=[
        C("NVDA", "NVDA", "Nvidia data center revenue", "89.023", "$bn", "2026-07-26", "3m",
          "total Data Center revenue for the quarter (not compute or networking alone)", must=[["data center"]])]),
    "F005": dict(type="track", components=[
        C("NVDA", "NVDA", "Nvidia hyperscale data center revenue", "48.71", "$bn", "2026-07-26", "3m",
          "Hyperscale customers' share of Data Center revenue, in dollars", must=[["hyperscale*"]])]),
    "F006": dict(type="track", components=[
        C("NVDA", "NVDA", "Nvidia AI clouds, industrial and enterprise revenue", "40.313", "$bn", "2026-07-26", "3m",
          "ACIE (AI clouds, industrial and enterprise) data center revenue, in dollars", must=[["ai clouds", "acie"]]),
        C("NVDA", "NVDA_growth", "ACIE growth from a year ago", "138", "%", "2026-07-26", "3m",
          "year-on-year growth of ACIE revenue", must=[["ai clouds", "acie"]])]),
    "F011": dict(type="track", components=[
        C("AMZN", "AMZN", "Amazon free cash flow, trailing twelve months", "-7.6", "$bn", "2026-06-30", "ttm",
          "free cash flow for the trailing twelve months (an outflow is negative)", must=[["free cash flow"]])]),
    # ---------------- How much should this industry be worth?
    "F019": dict(type="track", components=[
        C("NVDA", "NVDA", "Kress: top-5 hyperscaler capex in 2026", "800", "$bn", "2026-08-26", "said",
          "forecast of the top hyperscalers' capital expenditure for calendar 2026", must=[["capex", "capital expenditure*", "capital spending"], ["2026", "this year"]])]),
    "F020": dict(type="track", components=[
        C("NVDA", "NVDA", "Kress: top-5 hyperscaler capex in 2027", "1300", "$bn", "2026-08-26", "said",
          "forecast of the top hyperscalers' capital expenditure for calendar 2027", must=[["2027", "next year"]])]),
    # ---------------- What is AI earning now?
    "F031": dict(type="track", components=[
        C("MSFT", "MSFT", "Microsoft AI business annual run rate", "37", "$bn", "2026-04-29", "said",
          "Microsoft's AI business annual revenue run rate", must=[["run rate", "run-rate"], ["ai"]]),
        C("MSFT", "MSFT_growth", "growth of that run rate", "123", "%", "2026-04-29", "said",
          "year-on-year growth of the AI business run rate", must=[["run rate", "run-rate"]])]),
    "F032": dict(type="track", components=[
        C("MSFT", "MSFT", "Microsoft 365 Copilot paid seats", "30", "million", "2026-07-29", "said",
          "number of paid Microsoft 365 Copilot seats", must=[["copilot"], ["seat*"]])]),
    "F034": dict(type="track", components=[
        C("AMZN", "AMZN", "AWS AI business annual run rate", "25", "$bn", "2026-07-30", "said",
          "annual revenue run rate of AWS's AI business (not the Chips business)", must=[["run rate"], ["ai business", "ai and chips"]])]),
    "F035": dict(type="absence", components=[
        C(t, t, f"{n} publishes no separate AI revenue figure", None, "", None, "absence",
          "a separately disclosed revenue figure for AI (revenue attributed to AI products or services). "
          "Bookings, backlog, run rates of other businesses and cloud revenue that merely includes AI do not count.")
        for t, n in (("GOOGL", "Alphabet"), ("META", "Meta"), ("ORCL", "Oracle"))]),
    "F036": dict(type="track", components=[
        C("GOOGL", "GOOGL_tokens", "Gemini API tokens per minute", "22", "billion", "2026-07-22", "said",
          "Gemini API tokens processed per minute", must=[["token*"]]),
        C("GOOGL", "GOOGL_users", "Gemini app monthly active users", "950", "million", "2026-07-22", "said",
          "Gemini App monthly active users", must=[["monthly active"]]),
        C("GOOGL", "GOOGL_f100", "share of the Fortune 100 using Gemini Enterprise", "90", "%", "2026-07-22", "said",
          "share of Fortune 100 companies using Gemini Enterprise", must=[["fortune 100"]])]),
    "F037": dict(type="track", components=[
        C("ORCL", "ORCL", "Oracle prepaid and customer-supplied hardware in AI deals", "75", "$bn", "2026-06-10", "said",
          "total of customer prepayments and customer-supplied hardware in Oracle's AI contracts",
          must=[["prepa*"], ["hardware", "customer-supplied", "bring their own", "supplied"]],
          note="Said with the Q4 FY2026 results. Oracle posts no call transcript, so the reader cannot check the call.")]),
    "F039": dict(type="track", components=[
        C("MSFT", "MSFT", "Azure revenue for the quarter (restated)", "29.417", "$bn", "2026-06-30", "3m",
          "Azure revenue in dollars for the quarter (not a growth rate)", must=[["azure"]], min_doc_period="2026-09-30",
          note="The paper's figure is from Microsoft's 'FY27 Segments and Investor Metrics' 8-K of 2 Sep 2026, whose tables "
               "are images the reader cannot read. That filing says quarterly Azure revenue will be reported from Q1 FY2027, "
               "so the first document that can update this is the Q1 FY2027 results release (quarter to 30 Sep 2026).")]),
    "F040": dict(type="track", components=[
        C("AMZN", "AMZN", "AWS net sales", "42.232", "$bn", "2026-06-30", "3m", "AWS segment net sales for the quarter", must=[["aws"]])]),
    "F041": dict(type="track", components=[
        C("GOOGL", "GOOGL", "Google Cloud revenue", "24.768", "$bn", "2026-06-30", "3m", "Google Cloud segment revenue for the quarter", must=[["google cloud"]])]),
    "F042": dict(type="track", components=[
        C("ORCL", "ORCL", "Oracle cloud infrastructure revenue", "7.388", "$bn", "2026-08-31", "3m",
          "cloud infrastructure (OCI) revenue for the quarter", must=[["infrastructure"]])]),
    "F045": dict(type="track", components=[
        C("META", "META", "Advantage+ annual run rate", "75", "$bn", "2026-07-29", "said",
          "annual revenue run rate of Meta's Advantage+ products", must=[["advantage+"], ["run rate", "run-rate"]])]),
    # ---------------- Hidden figures
    "F046": dict(type="track", components=[
        C("META", "META_total", "Louisiana data centre venture (Hyperion): total project cost", "30", "$bn", "2026-02-19", "instant",
          "total estimated development cost of the Louisiana data center campus venture",
          must=[["development cost*", "total estimated", "project cost*"]],
          note="The paper's figure is secondary (Man Group, 19 Feb 2026). Meta's filings call it the Louisiana venture, not Hyperion."),
        C("META", "META_share", "Meta's share of the venture", "20", "%", "2026-02-19", "instant",
          "Meta's membership (ownership) interest in the Louisiana venture", must=[["membership interest", "interest"]],
          note="The paper says 'about 1/5 sits on Meta's own balance sheet'.")]),
    "F050": dict(type="track", components=[
        C("NBIS", "NBIS", "Nebius purchases of property, equipment and intangibles", "8.1303", "$bn", "2026-06-30", "6m",
          "cash paid for property and equipment and intangible assets (cash-flow statement)", must=[["purchases of property and equipment"]])]),
    "F056": dict(type="track", components=[
        C("CRWV", "CRWV", "share of CoreWeave revenue from committed contracts", "0.98", "share", "2026-06-30", "3m",
          "share of revenue from committed (take-or-pay) contracts", must=[["commit*"]])]),
    # ---------------- Contractual obligation
    "F058": dict(type="track", components=[
        C("MSFT", "MSFT", "Microsoft leases signed, not yet commenced", "329.1", "$bn", "2026-06-30", "instant",
          "leases signed that have not yet commenced", must=LEASES_NC)]),
    "F059": dict(type="track", components=[
        C("ORCL", "ORCL", "Oracle additional lease commitments", "260", "$bn", "2026-05-31", "instant",
          "additional lease commitments not yet on the balance sheet", must=[["additional lease commitments"]])]),
    "F060": dict(type="track", components=[
        C("META", "META", "Meta leases not yet commenced", "103.77", "$bn", "2025-12-31", "instant",
          "operating and finance leases that have not yet commenced", must=LEASES_NC)]),
    "F061": dict(type="track", components=[
        C("AMZN", "AMZN", "Amazon leases not yet commenced", "106.347", "$bn", "2026-03-31", "instant",
          "the 'Leases not yet commenced' total in the commitments table", must=[["leases not yet commenced"]])]),
    "F062": dict(type="track", components=[
        C("AMZN", "AMZN", "Amazon unconditional purchase obligations", "103.768", "$bn", "2026-03-31", "instant",
          "the 'Unconditional purchase obligations' total in the commitments table", must=[["unconditional purchase obligations"]])]),
    "F063": dict(type="track", components=[
        C("GOOGL", "GOOGL", "Alphabet leases not yet commenced", "58.5", "$bn", "2025-12-31", "instant",
          "future lease payments for leases that have not yet commenced", must=LEASES_NC,
          note="The paper's 58.5 = 52.7 long-term + 5.8 short-term (FY2025 10-K). The Q2 2026 10-Q gives 85.2 plus a separate "
               "~5.8 short-term lease: check which total is like for like before updating.")]),
    "F065": dict(type="track", components=[
        C("ORCL", "ORCL_start", "first quarter in which the leases commence", "first quarter of fiscal 2027", "text", "2026-05-31", "instant",
          "when the additional leases are expected to start commencing", must=[["commenc*"]], text=True),
        C("ORCL", "ORCL_end", "last year in which they commence", "fiscal 2029", "text", "2026-05-31", "instant",
          "when the last of them is expected to commence", must=[["commenc*"]], text=True),
        C("ORCL", "ORCL_term_min", "shortest lease term", "15", "years", "2026-05-31", "instant",
          "shortest lease term of the additional leases", must=[["term*"]]),
        C("ORCL", "ORCL_term_max", "longest lease term", "19", "years", "2026-05-31", "instant",
          "longest lease term of the additional leases", must=[["term*"]])]),
    # ---------------- Life of the equipment (accounting policies: usually only in the 10-K)
    "F077": dict(type="track", components=[
        C("MSFT", "MSFT_min", "servers and network equipment, shortest life", "2", "years", "2026-06-30", "policy",
          "estimated useful life of servers and network equipment (lower end)", must=[["useful li*"], ["server*"]]),
        C("MSFT", "MSFT_max", "servers and network equipment, longest life", "6", "years", "2026-06-30", "policy",
          "estimated useful life of servers and network equipment (upper end)", must=[["useful li*"], ["server*"]])]),
    "F078": dict(type="track", components=[
        C("GOOGL", "GOOGL", "Alphabet servers' useful life", "6", "years", "2025-12-31", "policy",
          "estimated useful life of servers (technical infrastructure)", must=[["useful li*"], ["server*"]]),
        C("META", "META_min", "Meta servers and network assets, shortest life", "5", "years", "2025-12-31", "policy",
          "estimated useful life of servers and network assets (lower end)", must=[["useful li*"], ["server*", "network"]]),
        C("META", "META_max", "Meta servers and network assets, longest life", "5.5", "years", "2025-12-31", "policy",
          "estimated useful life of servers and network assets (upper end)", must=[["useful li*"], ["server*", "network"]]),
        C("CRWV", "CRWV", "CoreWeave technology equipment useful life", "6", "years", "2025-12-31", "policy",
          "estimated useful life of technology equipment", must=[["useful li*"], ["technology equipment"]])]),
    "F079": dict(type="track", components=[
        C("AMZN", "AMZN_from", "Amazon server life before the change", "6", "years", "2025-01-01", "policy",
          "useful life of servers before the change effective 1 January 2025", must=[["useful li*"], ["server*"]]),
        C("AMZN", "AMZN_to", "Amazon server life after the change", "5", "years", "2025-01-01", "policy",
          "useful life of servers after the change effective 1 January 2025", must=[["useful li*"], ["server*"]])]),
    # ---------------- The revenue
    "F085": dict(type="historical", components=[
        C("MSFT", "MSFT", "Moerdler's question: RPO duration 2.5 years, up from 2", "2.5", "years", "2026-01-28", "said",
          "the average duration of Microsoft's remaining performance obligations, as put in an analyst's question")]),
    "F086": dict(type="historical", components=[
        C("MSFT", "MSFT", "Hood: GPUs 'sold for the entire useful life'", "sold for the entire useful life", "text", "2026-01-28", "said",
          "Microsoft's statement that GPU capacity is contracted for its entire useful life", text=True)]),
    "F087": dict(type="track", components=[
        C("MSFT", "MSFT", "Microsoft commercial RPO", "678", "$bn", "2026-06-30", "instant",
          "commercial remaining performance obligation (not total RPO)", must=[["commercial"], ["remaining performance obligation*", "rpo"]]),
        C("MSFT", "MSFT_duration", "weighted average duration of commercial RPO", "2.3", "years", "2026-06-30", "instant",
          "weighted average duration of commercial RPO", must=[["duration"]]),
        C("MSFT", "MSFT_12m", "share recognised within 12 months", "30", "%", "2026-06-30", "instant",
          "share of commercial RPO to be recognised as revenue in the next 12 months", must=[["12 months", "twelve months"]])]),
    "F089": dict(type="track", components=[
        C("MSFT", "MSFT", "commercial RPO growth", "84", "%", "2026-06-30", "instant",
          "year-on-year growth of commercial remaining performance obligation", must=[["remaining performance obligation*", "rpo"]]),
        C("MSFT", "MSFT_ex", "growth excluding OpenAI", "25", "%", "2026-06-30", "instant",
          "growth of commercial RPO excluding OpenAI", must=[["openai"]])]),
    "F090": dict(type="track", components=[
        C("MSFT", "MSFT", "OpenAI's share of Microsoft commercial RPO", "0.45", "share", "2025-12-31", "instant",
          "share of commercial RPO that comes from OpenAI", must=[["openai"]],
          note="The balance ($625bn), 110% and 28% in the same sentence are the December 2025 context of this share.")]),
    # ---------------- Circular financing
    "F092": dict(type="track", components=[
        C("NVDA", "NVDA", "Nvidia's intended investment in OpenAI (up to)", "100", "$bn", "2025-09-22", "event",
          "the amount Nvidia intends or has agreed to invest in OpenAI (letter of intent or definitive agreement)",
          must=[["openai"], ["invest*"]], must_not=["guarantee"],
          note="From OpenAI's press release of 22 Sep 2025. Nvidia's FY2026 10-K says only that it is 'finalizing an investment "
               "and partnership agreement with OpenAI' (see the source register).")]),
    "F098": dict(type="track", components=[
        C("NVDA", "NVDA_hopper", "revenue opportunity per GW, Hopper", "18", "$bn per GW", "2026-08-26", "said",
          "Nvidia revenue opportunity per gigawatt with Hopper", must=[["gigawatt*", "gw"]]),
        C("NVDA", "NVDA_blackwell", "revenue opportunity per GW, Blackwell", "25", "$bn per GW", "2026-08-26", "said",
          "Nvidia revenue opportunity per gigawatt with Blackwell", must=[["gigawatt*", "gw"], ["blackwell"]]),
        C("NVDA", "NVDA_rubin", "revenue opportunity per GW, Vera Rubin", "40", "$bn per GW", "2026-08-26", "said",
          "Nvidia revenue opportunity per gigawatt with Vera Rubin", must=[["gigawatt*", "gw"], ["rubin"]])],
        formulas=[F("ten_gw", "NVDA_rubin * 10", "400", "$bn", "10 GW at the Vera Rubin figure")]),
    "F099": dict(type="track", components=[
        C("MSFT", "MSFT_rev", "Microsoft revenue from OpenAI", "24.1", "$bn", "2026-06-30", "12m",
          "revenue Microsoft recognised from OpenAI in the fiscal year (related-party note)", must=[["openai"]]),
        C("MSFT", "MSFT_recv", "receivable from OpenAI", "6.0", "$bn", "2026-06-30", "instant",
          "amount receivable from OpenAI at the year end", must=[["openai"], ["receivable*"]])],
        formulas=[F("dso", "MSFT_recv / MSFT_rev * 365", "91", "days", "days of sales outstanding",
                    periods=[("same", ["MSFT_recv", "MSFT_rev"])])]),
    # ---------------- Funding
    "F103": dict(type="track", components=[
        C("AMZN", "AMZN_h1", "Amazon invested in OpenAI, six months", "28.7", "$bn", "2026-06-30", "6m",
          "amount Amazon invested in OpenAI preferred stock in the six months", must=[["openai"]]),
        C("AMZN", "AMZN_q2", "of which in Q2", "13.7", "$bn", "2026-06-30", "3m",
          "amount invested in OpenAI in the second quarter", must=[["openai"]]),
        C("AMZN", "AMZN_after", "funded after the quarter", "21.3", "$bn", "2026-06-30", "event",
          "remaining commitment amount funded after the quarter end", must=[["openai", "commitment amount"]])],
        formulas=[F("q1", "AMZN_h1 - AMZN_q2", "15", "$bn", "Q1 payment", periods=[("same", ["AMZN_h1", "AMZN_q2"])]),
                  F("total", "AMZN_h1 + AMZN_after", "50", "$bn", "total paid")]),
    "F108": dict(type="track", components=[
        C("AMZN", "AMZN_g", "Anthropic Series G", "5.0", "$bn", "2026-06-30", "3m", "amount invested in Anthropic Series G preferred stock", must=[["series g"]]),
        C("AMZN", "AMZN_h", "Anthropic Series H", "5.0", "$bn", "2026-06-30", "3m", "amount invested in Anthropic Series H preferred stock", must=[["series h"]]),
        C("AMZN", "AMZN_left", "facility still available", "15.0", "$bn", "2026-06-30", "instant",
          "amount still available under the Anthropic financing facility", must=[["facility"]])],
        formulas=[F("paid", "AMZN_g + AMZN_h", "10", "$bn", "paid in Q2", periods=[("same", ["AMZN_g", "AMZN_h"])])]),
    # ---------------- Who holds the exposure?
    "F131": dict(type="track", components=[
        C("GOOGL", "GOOGL", "Alphabet assets not yet in service", "122.814", "$bn", "2026-06-30", "instant",
          "property and equipment not yet placed in service", must=[["not yet in service", "not yet placed in service"]])]),
    "F132": dict(type="track", components=[
        C("META", "META", "Meta construction in progress", "80.345", "$bn", "2026-06-30", "instant", "construction in progress", must=CIP)]),
    "F133": dict(type="track", components=[
        C("AMZN", "AMZN", "Amazon construction in progress", "71.745", "$bn", "2025-12-31", "instant", "construction in progress",
          must=CIP, forms=["10-K"], note="Amazon gives construction in progress only in its 10-K.")]),
    "F135": dict(type="track", components=[
        C("CRWV", "CRWV", "CoreWeave construction in progress", "11.918", "$bn", "2026-06-30", "instant", "construction in progress", must=CIP),
        C("DLR", "DLR", "Digital Realty construction in progress", "9.770", "$bn", "2026-06-30", "instant",
          "construction in progress and space held for development", must=CIP),
        C("NBIS", "NBIS", "Nebius assets not yet in use", "7.836", "$bn", "2026-06-30", "instant",
          "property and equipment not yet in use (Nebius's equivalent of construction in progress)", must=[["not yet in use"]]),
        C("EQIX", "EQIX", "Equinix construction in progress", "2.827", "$bn", "2025-12-31", "instant", "construction in progress",
          must=CIP, must_not=["performance obligation"], forms=["10-K"],
          note="Equinix gives construction in progress only in its 10-K; its 10-Q does not.")]),
    "F141": dict(type="track", components=[
        C("CRWV", "CRWV_te", "CoreWeave gross technology equipment", "20.903", "$bn", "2025-12-31", "instant",
          "gross technology equipment (property and equipment note)", must=[["technology equipment"]],
          note="Test 5 takes every company's equipment from its latest 10-K (31 Dec 2025 here)."),
        C("CRWV", "CRWV_rev", "CoreWeave revenue for the quarter", "2.575", "$bn", "2026-06-30", "3m",
          "total revenue for the three months", must=[["revenue"]])],
        formulas=[F("annual_revenue", "CRWV_rev * 4", "10.3", "$bn", "annualised revenue (quarter x 4)"),
                  F("replacement_bill", "CRWV_te * (1/4 - 1/6) / (CRWV_rev * 4) * 100", "16.9", "% of revenue",
                    "extra replacement bill if the life falls from six years to four", periods=[("same", ["CRWV_te", "CRWV_rev"])])],
        model=MODEL_T5),
    "F150": dict(type="track", components=[
        C("CRWV", "CRWV", "CoreWeave customer prepayments, a year", "2.7", "$bn", "2026-06-30", "ttm",
          "the cash-flow line for the change in deferred revenue (contract liabilities), six months",
          must=[["deferred revenue*", "contract liabilit*"]], disclosed=dict(value="1.365", period_type="6m"), derive="annualise"),
        C("NBIS", "NBIS", "Nebius customer prepayments, a year", "8.8", "$bn", "2026-06-30", "ttm",
          "the cash-flow statement line 'Deferred revenue' under changes in operating assets and liabilities, six months "
          "(not the note's increase in the deferred revenue balance)", must=[["deferred revenue"]],
          disclosed=dict(value="4.395", period_type="6m"), derive="annualise"),
        C("ORCL", "ORCL_fy", "Oracle significant-financing prepayments, fiscal 2026", "4.592", "$bn", "2026-05-31", "12m",
          "increase in deferred revenues from customer prepayments with significant financing component, full fiscal year",
          must=[["significant financing"]], forms=["10-K"]),
        C("ORCL", "ORCL_ytd", "same line, fiscal 2027 to date", "11.363", "$bn", "2026-08-31", "ytd", months=3,
          measure=
          "increase in deferred revenues from customer prepayments with significant financing component, fiscal year to date",
          must=[["significant financing"]]),
        C("ORCL", "ORCL_prior_ytd", "same line, same period a year earlier", "0", "$bn", "2025-08-31", "ytd",
          "the same line for the same period of the prior fiscal year (the comparative column)", must=[["significant financing"]],
          comparative=True, months=3)],
        formulas=[F("ORCL_year", "ORCL_fy + ORCL_ytd - ORCL_prior_ytd", "16.0", "$bn",
                    "Oracle: trailing year = fiscal 2026 + fiscal 2027 to date - same period of fiscal 2026",
                    periods=[("year_apart", "ORCL_ytd", "ORCL_prior_ytd"), ("follows", "ORCL_fy", "ORCL_ytd")])],
        model=MODEL_T6 + ", rows 14-16"),
    "F152": dict(type="track", components=[
        C("CRWV", "CRWV", "CoreWeave leases signed, not commenced", "35.5", "$bn", "2026-06-30", "instant",
          "estimated future undiscounted payments on leases signed but not yet commenced", must=LEASES_NC),
        C("NBIS", "NBIS", "Nebius leases signed, not commenced", "12.0541", "$bn", "2026-06-30", "instant",
          "estimated future undiscounted payments on lease agreements executed but not yet commenced", must=LEASES_NC),
        C("ORCL", "ORCL", "Oracle additional lease commitments", "288", "$bn", "2026-08-31", "instant",
          "additional lease commitments not yet on the balance sheet", must=[["additional lease commitments"]])],
        formulas=[F("total", "CRWV + NBIS + ORCL", "335.6", "$bn", "three companies together")],
        model=MODEL_T6),
    "F154": dict(type="track", components=[
        C("CRWV", "CRWV_revolver", "CoreWeave revolving credit facility draw, Aug 2026", "1.2", "$bn", "2026-08-31", "event",
          "amount drawn on the Revolving Credit Facility after the quarter", must=[["revolving credit facility"]]),
        C("CRWV", "CRWV_new_draw", "draw on the new facility", "1.2", "$bn", "2026-08-31", "event",
          "amount drawn on the new delayed draw facility entered after the quarter", must=[["drawn", "draw*"]]),
        C("CRWV", "CRWV_new_size", "size of the new facility", "2.6", "$bn", "2026-08-31", "event",
          "total size of that new facility", must=[["facility"]]),
        C("NBIS", "NBIS_facility", "Nebius senior secured facility, 10 Jul 2026", "0.775", "$bn", "2026-07-10", "event",
          "principal amount of the senior secured debt facility", must=[["facility"]]),
        C("NBIS", "NBIS_converts", "Nebius convertible notes, Aug 2026", "5.75", "$bn", "2026-08-24", "event",
          "aggregate principal of convertible notes issued in August 2026", must=[["convertible"]],
          note="Source: Nebius 6-K of 24 Aug 2026, Ex 99.1 (closing release). The quarterly 6-K of 12 Aug predates it."),
        C("ORCL", "ORCL_atm", "Oracle at-the-market stock sales", "19.9", "$bn", "2026-08-31", "3m",
          "net proceeds of common stock sold through the ATM program in the quarter", must=[["atm", "at-the-market"]]),
        C("ORCL", "ORCL_debt", "Oracle issued no new debt in the quarter", None, "", None, "absence",
          "cash received from new borrowings or debt issuance in the quarter: a 'proceeds from borrowings' or 'issuance of "
          "notes' line in the financing section of the cash-flow statement, or a note saying new debt was issued. "
          "Repayments do not count.",
          note="The paper says Oracle 'issued no debt at all' in the quarter to 31 Aug 2026.")],
        model=MODEL_T6),
    "F155": dict(type="track", components=[
        C("CRWV", "CRWV_tests", "CoreWeave financial covenant tests", "6", "count", "2026-06-30", "instant",
          "number of financial maintenance covenant tests across CoreWeave's facilities", must=[["covenant*"]], forms=["8-K"],
          note="The paper counts the tests from the facilities' credit agreements (the DDTL 3.0, 4.0, 5.0 and 5.5 8-Ks listed "
               "in the Test 6 source register). The 10-Q does not list them, so a 10-Q cannot confirm or update the count."),
        C("CRWV", "CRWV_ratio", "CoreWeave discloses no actual covenant ratio", None, "", None, "absence",
          "an actual value of any covenant ratio for CoreWeave (the measured ratio, not the threshold)"),
        C("ORCL", "ORCL", "Oracle EBITDA to interest covenant", "3.0", "x", "2026-05-31", "instant",
          "minimum ratio of EBITDA to interest in Oracle's revolving credit agreement", must=[["interest"]], forms=["10-K"],
          note="From the credit agreement described in the 10-K (Note 6)."),
        C("NBIS", "NBIS_none", "Nebius discloses no financial covenants", None, "", None, "absence",
          "any financial maintenance covenant in Nebius's debt (a ratio test)")],
        model=MODEL_T6),
}


# ------------------------------------------------------------------------------------------
# Formulas: + - * / and parentheses over component keys and numbers. Nothing else is allowed.
# ------------------------------------------------------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def evaluate(expr, values):
    """Evaluate `expr` with component values. Raises KeyError if an input is missing."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name):
            return float(values[n.id])
        raise ValueError(f"not allowed in a formula: {ast.dump(n)}")
    return ev(ast.parse(expr, mode="eval"))


def names_in(expr):
    """The component names a formula uses."""
    return sorted({n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)})


def claim_for(row_id, ledger_row=None):
    """The rule for a ledger row. Rows without a written rule get a single automatic component (marked as such)."""
    if row_id in CLAIMS:
        return CLAIMS[row_id]
    t = (ledger_row or {}).get("Type") or ""
    kind = "historical" if t == "Quote" else "absence" if t == "Absence claim" else "track"
    return dict(type=kind, auto=True, components=[])


# ------------------------------------------------------------------------------------------
# Which ledger rows to check in which kind of document (the catch-up plan).
#   results    = the latest earnings release filed with the SEC (8-K Item 2.02, its Ex 99 files)
#   periodic   = the latest 10-Q or 10-K
#   transcript = the latest earnings-call transcript on the company's own site
#   6k         = the latest Form 6-K carrying quarterly financial statements (Nebius)
# Only the components of a row that belong to the document's company are asked.
# ------------------------------------------------------------------------------------------
PLAN = {
    ("NVDA", "results"): ["F003", "F005", "F006"],
    ("NVDA", "periodic"): ["F003", "F092"],
    ("NVDA", "transcript"): ["F019", "F020", "F098"],
    ("MSFT", "results"): ["F031", "F039"],
    ("MSFT", "periodic"): ["F058", "F077", "F087", "F099"],
    ("MSFT", "transcript"): ["F031", "F032", "F085", "F086", "F087", "F089", "F090"],
    ("AMZN", "results"): ["F011", "F034", "F040"],
    ("AMZN", "periodic"): ["F061", "F062", "F079", "F103", "F108", "F133"],
    ("GOOGL", "results"): ["F035", "F036", "F041"],
    ("GOOGL", "periodic"): ["F035", "F063", "F078", "F131"],
    ("META", "periodic"): ["F035", "F046", "F060", "F078", "F132"],
    ("META", "transcript"): ["F045"],
    ("ORCL", "results"): ["F035", "F037", "F042"],
    ("ORCL", "periodic"): ["F035", "F059", "F065", "F150", "F152", "F154", "F155"],
    ("CRWV", "periodic"): ["F056", "F078", "F135", "F141", "F150", "F152", "F154", "F155"],
    ("NBIS", "6k"): ["F050", "F135", "F150", "F152", "F154", "F155"],
    ("DLR", "periodic"): ["F135"],
    ("EQIX", "periodic"): ["F135"],
}


def items_for(ticker, kind, doc_meta, ledger):
    """(items to ask, items not applicable to this document with the reason)."""
    ask, skip = [], []
    for row_id in PLAN.get((ticker, kind), []):
        claim = claim_for(row_id, ledger.get(row_id))
        for comp in claim["components"]:
            if comp["company"] != ticker:
                continue
            typ = "absence" if comp["period_type"] == "absence" else claim["type"]
            item = dict(id=f"{row_id}:{comp['key']}", row=row_id, claim=claim, comp=comp, type=typ)
            if comp.get("forms") and doc_meta.get("form") not in comp["forms"]:
                skip.append((item, f"{doc_meta.get('form') or 'this document'} does not carry it; it is given in the {'/'.join(comp['forms'])}"))
            elif comp.get("min_doc_period") and doc_meta.get("period_end") and doc_meta["period_end"] < comp["min_doc_period"]:
                skip.append((item, f"this document is for the period to {doc_meta['period_end']}, before the first one expected to carry it"))
            else:
                ask.append(item)
    return ask, skip
