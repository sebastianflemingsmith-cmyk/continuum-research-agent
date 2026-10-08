"""Turn the answers the AI gave in the paid run of 26 Sep 2026 (as printed in that run's report and proposals)
into the item format the new reader uses, without improving them: same values, same quotes (one fragment each),
same periods. Where the old answer covered several numbers in one row, it is given to each matching item with
the same quote. This lets the new checks be tested on exactly what the AI said.

A one-off: it made fixtures/answers_paid_run.json on 26 Sep 2026 and is kept to show how. Do not run it again: it
would overwrite that fixture with one built from whatever documents are saved now (it reproduces the fixture exactly
only when documents/ holds just the documents of 26 Sep)."""
import json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.dirname(HERE)
sys.path[:0] = [READER, os.path.join(os.path.dirname(READER), "watcher")]
import claims as R
import watcher as W
from lib import checks as K, documents as D

recs = json.load(open(os.path.join(HERE, "fixtures", "paid_run_rows.json"), encoding="utf-8"))
MULTI = {  # (row, company) -> [(item key, value taken from the old answer)]
    ("F006", "NVDA"): [("NVDA", 40.313)],
    ("F036", "GOOGL"): [("GOOGL_tokens", 22)],
    ("F077", "MSFT"): [("MSFT_min", 2), ("MSFT_max", 6)],
    ("F087", "MSFT"): [("MSFT", 678)],
    ("F089", "MSFT"): [("MSFT", 84)],
    ("F098", "NVDA"): [("NVDA_rubin", 40)],
    ("F099", "MSFT"): [("MSFT_rev", 24.1)],
    ("F103", "AMZN"): [("AMZN_h1", 50.0)],
    ("F108", "AMZN"): [("AMZN_g", 10.0)],
    ("F141", "CRWV"): [("CRWV_te", 20.903)],
    ("F150", "ORCL"): [("ORCL_ytd", 11.4)],
    ("F154", "ORCL"): [("ORCL_atm", 19.9)],
    ("F154", "NBIS"): [("NBIS_facility", 0.775)],
    ("F154", "CRWV"): [("CRWV_revolver", 1.2), ("CRWV_new_draw", 1.2)],
    ("F065", "ORCL"): [("ORCL_start", "second quarter of fiscal 2027"), ("ORCL_term_min", 15), ("ORCL_term_max", 19)],
    ("F046", "META"): [("META_total", 27)],
}

def ptype(p):
    p = (p or "").lower()
    if "six months" in p or "six-month" in p: return "6m"
    if "three months" in p or "first quarter" in p or re.search(r"\bq\d", p): return "3m"
    if "ttm" in p or "trailing" in p: return "ttm"
    if "as of" in p or re.match(r"\d{1,2} \w+ \d{4}$", p) or "dec" in p: return "instant"
    return ""

out = {}
for r in recs:
    claim = R.CLAIMS.get(r["row"])
    comps = [c for c in claim["components"] if c["company"] == r["co"]]
    ans = out.setdefault(r["stem"], {"items": [], "new_facts": []})
    frag = [{"role": "value", "text": r["quote"]}] if r["quote"] else []
    if r["value"] is None:                      # the AI said: not in this document
        for c in comps:
            if claim["type"] == "historical":
                ans["items"].append({"item": f"{r['row']}:{c['key']}", "related": [], "comment": r["comment"]})
            else:
                ans["items"].append({"item": f"{r['row']}:{c['key']}", "found": False, "comment": r["comment"]})
        continue
    pairs = MULTI.get((r["row"], r["co"]))
    if not pairs:
        pairs = [(comps[0]["key"], r["value"])]
    for key, val in pairs:
        c = next(c for c in comps if c["key"] == key)
        d = K.dates_in(r["period"] or "")
        item = {"item": f"{r['row']}:{key}", "found": True, "unit": c["unit"], "fragments": frag, "comment": r["note"] or "",
                "period_end": d[-1].isoformat() if d else "", "period_type": ptype(r["period"])}
        if c.get("text"):
            item["value_text"] = val
        else:
            try:
                item["value"] = float(str(val).replace(",", ""))
            except ValueError:
                item["value"] = val
        ans["items"].append(item)

# the new facts the AI gave in that run (from proposed.jsonl), one quote each
titles = {}
for f in os.listdir(os.path.join(os.path.dirname(HERE), "documents")):
    if f.endswith(".txt"):
        titles[f[:-4]] = open(os.path.join(os.path.dirname(HERE), "documents", f), encoding="utf-8").readline().strip()
for line in open(os.path.join(os.path.dirname(HERE), "proposed.jsonl"), encoding="utf-8"):
    p = json.loads(line)
    if p.get("row") != "NEW FACT" or not p["found_at"].startswith("2026-09-26T17:25"):
        continue
    stem = next((s_ for s_, t_ in titles.items() if p["document"] in t_ and s_.startswith(p["company"] + "_")), None)
    if stem:
        out.setdefault(stem, {"items": [], "new_facts": []})["new_facts"].append(
            {"topic": p["topic"], "related_rows": p.get("related_rows") or [], "fragments": [{"role": "value", "text": p["quote"]}],
             "why_it_matters": p.get("why") or ""})
# Items the old report does not show for a document: that run dropped a miss when another document of the same
# company had the row. Record them as 'not found' with a note saying so (never as a confirmation).
ledger = {r["ID"]: r for r in W.table(W.read_workbook(os.path.join(os.path.dirname(os.path.dirname(HERE)), "Watch list - The AI Lose Lose Race.xlsx"))["Ledger"])}
for (t, kind) in R.PLAN:
    for stem, meta, text in D.cache_read_latest(t, kind):
        meta = D.describe_doc(meta, text)
        ask, _ = R.items_for(t, kind, meta, ledger)
        ans = out.setdefault(stem, {"items": [], "new_facts": []})
        have = {a["item"] for a in ans["items"]}
        for it in ask:
            if it["id"] not in have:
                ans["items"].append({"item": it["id"], "found": False,
                                     "comment": "not recorded: the old report dropped this miss because another document had the row"}
                                    if it["type"] != "historical" else {"item": it["id"], "related": []})
json.dump(out, open(os.path.join(HERE, "fixtures", "answers_paid_run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), "documents,", sum(len(a["items"]) for a in out.values()), "items,", sum(len(a["new_facts"]) for a in out.values()), "new facts")
