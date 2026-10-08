"""Read the per-row answers the AI gave in the paid run of 26 Sep 2026 back out of that run's report
(reports/Part B 2026-09-26 17.25.41.md) into fixtures/paid_run_rows.json. The report is not changed."""
import json, os, re
HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.dirname(HERE)
rep = open(os.path.join(READER, "reports", "Part B 2026-09-26 17.25.41.md"), encoding="utf-8").read()
body = rep.split("## New facts")[0]
NAME = {"Nvidia": "NVDA", "Microsoft": "MSFT", "Amazon": "AMZN", "Alphabet": "GOOGL", "Meta": "META", "Oracle": "ORCL",
        "CoreWeave": "CRWV", "Nebius": "NBIS", "Digital Realty": "DLR", "Equinix": "EQIX"}
docs = {}
for f in os.listdir(os.path.join(READER, "documents")):
    if f.endswith(".txt"):
        docs[f[:-4]] = open(os.path.join(READER, "documents", f), encoding="utf-8").readline().strip()
recs = []
for e in re.split(r"\n(?=- \*\*F\d{3}\*\*)", body):
    m = re.match(r"- \*\*(F\d{3})\*\* (.+?) \((\w+)\): ", e)
    if not m:
        continue
    row, co, kind = m.group(1), NAME[m.group(2)], m.group(3)
    src = re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", e)
    title = src[-1][0] if src else ""
    stem = next(s for s, t in docs.items() if t == title and s.startswith(co + "_"))
    val = re.search(r"Document: \*\*(.+?)\*\* \((.*?)\)\n", e)
    quote = re.search(r"Quote: “(.*?)”\n  - (?:Note|Source)", e, re.S)
    note = re.search(r"Note: (.*?)\n  - Source", e, re.S)
    miss = re.search(r"\n  - (.*) · \[", e)
    pos = body.find(e)
    sec = body.rfind("\n## ", 0, pos)
    recs.append(dict(row=row, co=co, kind=kind, stem=stem, section=body[sec + 4: body.find("\n", sec + 4)],
                     value=val.group(1) if val else None, period=val.group(2) if val else None,
                     quote=quote.group(1) if quote else None, note=note.group(1).strip() if note else None,
                     comment=miss.group(1) if (miss and not val) else None))
json.dump(recs, open(os.path.join(HERE, "fixtures", "paid_run_rows.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(recs), "rows read from the paid run's report")
