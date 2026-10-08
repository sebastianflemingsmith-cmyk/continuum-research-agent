"""
The AI step: what DeepSeek is asked (the prompt), the call itself, the rough cost of a run, and reading the answer.
Saved answers can be replayed instead of calling DeepSeek (--answers), at no cost.
"""

import glob
import json
import os
import re
import time
import urllib.error
import urllib.request

import companies as C
import watcher as W

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
# deepseek-flash prices per 1M tokens, cache miss, from api-docs.deepseek.com/quick_start/pricing (checked 26 Sep 2026)
PRICE_IN = (0.15, 0.30)    # off-peak, peak
PRICE_OUT = (0.60, 1.20)


# ------------------------------------------------------------------------------------------
# The prompt
# ------------------------------------------------------------------------------------------
SYSTEM = """You check a research paper against one company document. You answer in JSON only.

You get a list of ITEMS. Each item is one number (or one short statement) for one company. For each item:

1. Look for exactly the measure in "measure". If the document gives a similar but different measure (total RPO
   instead of commercial RPO, remaining performance obligations instead of construction in progress, a balance
   instead of a cash-flow line), the item is NOT found: say what you saw in "comment". Never substitute.
2. Type "track": report the figure for the LATEST period in the document (for a table, the most recent column),
   even when the paper's figure is for an earlier date. If the item has "disclosed_as", report that figure for
   that kind of period and do not annualise or add anything up: the code does all arithmetic.
   If the item says "comparative", report the prior-year comparative column for the same period instead.
3. Evidence comes as FRAGMENTS. Each fragment is copied character for character from ONE place in the document.
   Never join text from different places into one fragment: give several fragments instead (at most 4):
   - role "value": the sentence or the table row that contains the number, including the row's label;
   - role "column": for a table, the column heading that shows the date or period of the column you used;
   - role "units": for a table, the line that gives its units, e.g. "(in millions)";
   - role "context": anything else needed, e.g. the sentence before that names the measure.
   Keep each fragment under 400 characters. No ellipses, no paraphrase.
4. "value": the number in the item's unit ($bn: a table in millions showing 137,214 is 137.214; share: 45% is 0.45).
   "value_as_printed": the number exactly as printed. "period_end": the date the figure refers to, as YYYY-MM-DD
   (the balance date, or the last day of the period). "period_type": instant, 3m, 6m, 9m, 12m, ttm, said, event or policy.
   For a year-to-date figure give its length (3m, 6m, 9m or 12m), never just "ytd".
   "line_item": the document's own words for the measure.
5. Type "absence": the paper says this is NOT disclosed. If the document does disclose it, set found true and give
   fragments. If not, set found false and give one or two fragments with role "evidence" showing the closest thing
   the document does disclose (for example how it breaks revenue down), so a reader can see where you looked.
6. Type "historical": a dated statement that a newer document cannot change. Do not look for the statement itself.
   If the document has a newer statement on the same subject, list it under "related" (each with fragments and a
   one-line "summary"); otherwise give "related": [].
7. Items with unit "text": put the words in "value_text" and the sentence in a "value" fragment.
8. If an item is not in the document, set found false and say why in "comment". Do not guess and do not compute.

Separately, under "new_facts", list AT MOST 3 facts from this document that the paper does not have and that bear on
these items or on AI infrastructure financing: new commitments, guarantees, leases, borrowing or equity raised,
customer concentration, or a change of definition or accounting. Rules:
   - each must state a figure (an amount, a percentage or a capacity), unless it is a change of definition or accounting;
   - prefer figures from the financial statements and their notes over management's commentary;
   - do NOT list: a figure that is one of the ITEMS above; definitions of non-GAAP measures, reconciliations or where to
     find them; forward-looking-statement warnings; opinions; product or partnership news without a figure;
   - if nothing qualifies, give "new_facts": [].
Each has "topic", "related_rows" (ledger IDs such as "F152"), "fragments" (as above) and "why_it_matters": one line
that contains no number that is not in the fragments.

Output format (json):
{"items": [{"item": "F152:CRWV", "found": true, "value": 35.5, "value_as_printed": "$35.5 billion", "unit": "$bn",
            "period_end": "2026-06-30", "period_type": "instant", "line_item": "leases that had not yet commenced",
            "fragments": [{"role": "context", "text": "exact words"}, {"role": "value", "text": "exact words"}],
            "comment": "short note"}],
 "new_facts": [{"topic": "short label", "related_rows": ["F092"], "fragments": [{"role": "value", "text": "exact words"}],
                "why_it_matters": "one line"}]}"""


def build_user_prompt(ticker, meta, items, ledger, text):
    """The message for one document: the items to check (with the paper's figures) and the document's text."""
    lines = [f"Company: {C.NAME.get(ticker, ticker)}", f"Document: {meta['title']}", f"Link: {meta['url']}"]
    if meta.get("period_end"):
        lines.append(f"This document reports on the period ending {meta['period_end']}.")
    lines += ["", "Items to check:"]
    for it in items:
        c, row = it["comp"], ledger.get(it["row"], {})
        d = {"item": it["id"], "type": it["type"], "what_the_paper_says": row.get("What the paper says"),
             "this_item": c["label"], "measure": c["measure"], "unit": c["unit"],
             "paper_value": c["value"], "paper_period_end": c["period_end"], "paper_period_type": c["period_type"]}
        if c.get("disclosed"):
            d["disclosed_as"] = (f"report the {c['disclosed']['period_type']} figure as printed (the paper's model used "
                                 f"{c['disclosed']['value']} for the {c['disclosed']['period_type']} to {c['period_end']}); do not annualise")
        if c.get("comparative"):
            d["comparative"] = "report the prior-year comparative column for the same period"
        if c.get("note"):
            d["note"] = c["note"]
        lines.append(json.dumps(d, ensure_ascii=False, default=str))
    lines += ["", "Document text:", "<<<", text, ">>>", "", "Answer in json with one entry per item."]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------
# The call, and its rough cost
# ------------------------------------------------------------------------------------------
def deepseek(cfg, system, user):
    """Ask DeepSeek (three tries). Returns (answer, token usage); raises FetchError if it never answers."""
    body = {"model": cfg.get("model", "deepseek-flash"), "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}, "max_tokens": int(cfg.get("max_tokens", 32000)),
            "thinking": {"type": cfg.get("thinking", "enabled")}}
    if cfg.get("thinking", "enabled") == "enabled":
        body["reasoning_effort"] = cfg.get("reasoning_effort", "high")
    req = urllib.request.Request(DEEPSEEK_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['deepseek_api_key']}"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=int(cfg.get("timeout", 600))) as r:
                out = json.loads(r.read().decode("utf-8"))
            content = (out["choices"][0]["message"].get("content") or "").strip()
            if not content:
                last = "empty answer"
                time.sleep(3)
                continue
            return json.loads(content), out.get("usage", {})
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")[:300]
            if e.code in (401, 403):
                raise SystemExit(f"DeepSeek refused the key (HTTP {e.code}). Check deepseek_api_key in Agent/reader/config.json.")
            if e.code == 402:
                raise SystemExit("DeepSeek says the account balance is too low (HTTP 402). Top up at platform.deepseek.com.")
            last = f"HTTP {e.code}: {msg}"
            time.sleep(5 * (attempt + 1))
        except (json.JSONDecodeError, KeyError) as e:
            last = f"unreadable answer ({e})"
            time.sleep(3)
        except Exception as e:
            last = f"{e.__class__.__name__}: {e}"
            time.sleep(5 * (attempt + 1))
    raise W.FetchError(f"DeepSeek did not answer ({last})")


def cost_line(jobs):
    """The plan's one-line estimate of the tokens and cost of sending these documents."""
    tokens_in = sum(len(j["text"]) for j in jobs) / 4 + 2500 * len(jobs)
    tokens_out = 7000 * len(jobs)
    lo = tokens_in / 1e6 * PRICE_IN[0] + tokens_out / 1e6 * PRICE_OUT[0]
    hi = tokens_in / 1e6 * PRICE_IN[1] + tokens_out / 1e6 * PRICE_OUT[1]
    return (f"{len(jobs)} documents, about {tokens_in / 1e6:.2f}M tokens in. Rough cost with deepseek-flash: "
            f"${lo:.2f} off-peak to ${hi:.2f} peak (prices checked 26 Sep 2026; output assumed ~7k tokens a document).")


# ------------------------------------------------------------------------------------------
# Reading the answer
# ------------------------------------------------------------------------------------------
def answers_by_item(answer, ask):
    """The AI's answer for each item asked ({item id: answer}), whichever of the usual shapes the answer came in."""
    answer = answer if isinstance(answer, dict) else {}
    items_in = answer.get("items") or answer.get("rows") or []
    if isinstance(items_in, dict):                         # {"F152:CRWV": {...}} instead of a list
        items_in = [dict(v, item=k) if isinstance(v, dict) else {} for k, v in items_in.items()]
    items_in = items_in if isinstance(items_in, list) else []
    by_id = {str(a.get("item") or a.get("id") or ""): a for a in items_in if isinstance(a, dict)}
    out = {}
    for it in ask:
        ans = by_id.get(it["id"])
        if ans is None and ":" in it["id"] and len([x for x in ask if x["row"] == it["row"]]) == 1:
            ans = by_id.get(it["row"])          # an answer keyed by the row alone, when the row has one component here
        out[it["id"]] = ans
    return out


def new_facts_in(answer):
    """The AI's 'new facts' as a list (at most five are read), whatever shape they came in."""
    nf = (answer if isinstance(answer, dict) else {}).get("new_facts") or []
    nf = [nf] if isinstance(nf, dict) else nf if isinstance(nf, list) else []
    return [f for f in nf[:5] if isinstance(f, dict)]


def load_answers(path):
    """Saved AI answers keyed by document stem: one JSON file {stem: answer}, or a folder of '<stem>.answer ….json'
    files as a live run saves them (the latest per document is used)."""
    if os.path.isdir(path):
        out, when = {}, {}
        for p in glob.glob(os.path.join(path, "*.answer*.json")):
            m = re.match(r"(.+?)\.answer(?: (.+))?\.json$", os.path.basename(p))
            if m and (m.group(2) or "") >= when.get(m.group(1), ""):
                out[m.group(1)], when[m.group(1)] = json.load(open(p, encoding="utf-8")), m.group(2) or ""
        return out
    return json.load(open(path, encoding="utf-8"))
