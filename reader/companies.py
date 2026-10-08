"""
The companies the agent follows, in one place: name, SEC number (CIK) and the month each fiscal year ends.

The first ten are read by Part B (the text reader). The rest are the basket companies Part A checks.
The SEC numbers are the ones the reader has used since 25 Sep 2026.

Fiscal year ends are needed to read labels such as "Q2 FY27" (Nvidia's quarter to July 2026). They come from the
filings themselves (checked 28 Sep 2026): Nvidia's fiscal year ended January 25, 2026 (the last Sunday of January),
Microsoft's June 30, 2026, Oracle's May 31, 2026, and the other seven December 31, 2025. On a live run the reader also
compares them with the SEC's own record and says so in the report if they differ. The basket-only companies are left
blank because nothing reads their fiscal labels.
"""

COMPANIES = {
    # ticker: (name, SEC CIK, month the fiscal year ends)
    "NVDA": ("Nvidia", 1045810, 1),
    "MSFT": ("Microsoft", 789019, 6),
    "AMZN": ("Amazon", 1018724, 12),
    "GOOGL": ("Alphabet", 1652044, 12),
    "META": ("Meta", 1326801, 12),
    "ORCL": ("Oracle", 1341439, 5),
    "CRWV": ("CoreWeave", 1769628, 12),
    "NBIS": ("Nebius", 1513845, 12),
    "DLR": ("Digital Realty", 1297996, 12),
    "EQIX": ("Equinix", 1101239, 12),
    "TSM": ("TSMC", 1046179, None),
    "AVGO": ("Broadcom", 1730168, None),
    "MU": ("Micron", 723125, None),
    "AMD": ("AMD", 2488, None),
    "ASML": ("ASML", 937966, None),
    "DELL": ("Dell", 1571996, None),
    "ANET": ("Arista", 1596532, None),
    "ETN": ("Eaton", 1551182, None),
    "VRT": ("Vertiv", 1674101, None),
    "PLTR": ("Palantir", 1321655, None),
}

NAME = {t: v[0] for t, v in COMPANIES.items()}
CIK = {t: v[1] for t, v in COMPANIES.items()}
FYE = {t: v[2] for t, v in COMPANIES.items() if v[2]}
TICKER_BY_CIK = {v[1]: t for t, v in COMPANIES.items()}
