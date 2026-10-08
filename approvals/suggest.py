"""
The tool's own suggestion for each item: plain rules, no AI. In the first round they are NOT shown on the sheet: you
decide everything yourself, and `record` then scores these rules against your decisions (Decided <date>.md lists
every disagreement). The rules are tuned from those disagreements. A suggestion never decides anything.

suggest(item) -> (decision or None, reason). None means "no suggestion: needs your judgment".
"""
import re

FILINGS = re.compile(r"^(10-K|10-Q|20-F|6-K)\b")
K_CALL = re.compile(r"call|transcript|remarks", re.I)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _wrong_scale(p):
    """A percentage off by exactly 100 times (0.2 for 20%): the reader misread the scale."""
    paper, new = _num(p.get("paper_value")), _num(p.get("new_value"))
    if "%" not in str(p.get("unit") or "") or not paper or not new:
        return False
    ratio = new / paper
    return any(abs(ratio / f - 1) < 0.005 for f in (0.01, 100))


def suggest(item):
    kind, p = item["type"], item["proposal"]
    note = (p.get("note") or "").lower()
    doc = p.get("document") or p.get("form") or ""
    if kind == "Old item":
        return "dismiss", "from the first version (26 Sep), before the checks were fixed"
    if kind == "No longer found":
        return "dismiss", "a later run found the paper agrees"
    if kind == "To do":
        return None, "your earlier decision"
    if kind == "Lead":
        if K_CALL.search(doc):
            return "dismiss", "management's words on a call, not a filed number"
        if p.get("related_rows") and (FILINGS.match(doc) or "8-K" in doc):
            return "use", "a figure from a filing or results release, linked to rows of the paper"
        return None, "a lead not linked to a row of the paper"
    # figures: Part B, Part A and arithmetic
    if _wrong_scale(p):
        return "reader wrong", "a percentage exactly 100 times off: the reader misread the scale"
    if "deliberately" in note:
        return "keep paper", "the paper quotes this date on purpose"
    if "confirm" in note:
        return None, "the reader asks for a check first"
    if kind == "Part A figure":
        if p.get("status") == "DIFFERS FROM FILINGS":
            return "approve", "the SEC's labelled data gives a different figure for the same period"
        return "approve", "a newer figure in the SEC's labelled data"
    if kind == "Arithmetic":
        return "approve", "the paper's own formula with newer inputs"
    if (p.get("kind") or p.get("paper_check")) == "newer figure" and FILINGS.match(doc):
        return "approve", "a newer figure, quoted word for word from a filing"
    return None, "needs your judgment (" + (p.get("paper_check") or p.get("kind") or "other") + ")"

