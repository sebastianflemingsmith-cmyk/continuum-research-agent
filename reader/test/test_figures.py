"""The Figures sheet of the watch list: every figure in the paper, one line each, as the paper stands. The tabular review
is to be built on it, so it must cover every ledger row, quote the paper as the ledger does, and agree with the copies
of the figures that the code still holds (claims.py and Part A's SPECS)."""
import re
import unittest
from decimal import ROUND_HALF_UP, Decimal

from support import LEDGER, R, S, W, WATCH_LIST

FIGURES = W.table(W.read_workbook(WATCH_LIST).get("Figures", []), first_header="Figure ID")

# figures the code checks that are not the paper's figures, and why
NOT_THE_PAPERS = {
    "Part A: Microsoft total remaining performance obligations":
        "F087: the paper quotes commercial RPO ($678bn, from the call); Part A checks total RPO ($684bn, in the 10-K).",
}
# printed figures that the exact value does not round to, and why
ROUNDING_EXPLAINED = {
    "F064-1": "the paper adds the six rounded figures (329 + 260 + 104 + 106 + 104 + 59 = 962); the exact sum, "
              "961.485, rounds to 961",
}
WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NUMBER = re.compile(r"([−+-]?)\s?\$?(\d[\d,]*(?:\.\d+)?)")
SCALES = [Decimal(1), Decimal(100), Decimal("0.01"), Decimal(1000), Decimal("0.001")]   # %, points, m/bn, bn/tn


def norm(s):
    return " ".join(str(s or "").split())


def number(v):
    """The value as a Decimal, or None for text."""
    if isinstance(v, bool) or v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return Decimal(repr(v))
    return Decimal(v) if re.fullmatch(r"-?\d+(?:\.\d+)?", str(v)) else None


def printed_numbers(text):
    """(value, decimals) for each number in the printed words: digits with their sign, fractions, and numbers in words."""
    out = []
    for sign, digits in NUMBER.findall(text):
        d = Decimal(digits.replace(",", ""))
        out.append((-d if sign in "−-" and sign else d, len(digits.split(".")[1]) if "." in digits else 0))
    for a, b in re.findall(r"(\d[\d,]*)/(\d[\d,]*)", text):
        out.append((Decimal(a.replace(",", "")) / Decimal(b.replace(",", "")), None))
    for word, n in WORDS.items():
        if re.search(r"\b%s\b" % word, text, re.I):
            out.append((Decimal(n), 0))
    return out


def rounds_to(value, text):
    for p, places in printed_numbers(text):
        for k in SCALES:
            x = value * k
            if places is None:                                   # a fraction: the value is given to its own precision
                exp = x.as_tuple().exponent
                if p.quantize(Decimal(1).scaleb(exp), ROUND_HALF_UP) == x:
                    return True
            elif x.quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP) == p:
                return True
    return False


def code_figures():
    """{name as the Figures sheet gives it: (value, what it is)} for every figure the code holds."""
    out = {}
    for row, claim in R.CLAIMS.items():
        for c in claim["components"]:
            out[f"claims.py {row} {c['key']}"] = (c["value"], c["label"])
        for f in claim.get("formulas", []):
            out[f"claims.py {row} formula {f['name']}"] = (f["paper"], f["label"])
    for s in S.SPECS:
        name = f"Part A: {s['what']}"
        assert name not in out, name
        out[name] = (s["paper"], s["id"])
    return out


class FiguresSheet(unittest.TestCase):
    def test_sheet_covers_the_ledger(self):
        """the Figures sheet has a line for every ledger row, and its IDs run F001-1, F001-2 ... in ledger order"""
        self.assertTrue(FIGURES, "the watch list has no Figures sheet")
        self.assertEqual(sorted({f["Ledger ID"] for f in FIGURES}), sorted(LEDGER))
        seen = {}
        for f in FIGURES:
            seen[f["Ledger ID"]] = seen.get(f["Ledger ID"], 0) + 1
            self.assertEqual(f["Figure ID"], f"{f['Ledger ID']}-{seen[f['Ledger ID']]}")
        self.assertEqual(list(dict.fromkeys(f["Ledger ID"] for f in FIGURES)), list(LEDGER))

    def test_printed_words_are_the_ledgers(self):
        """'As the paper prints it' is copied word for word from the ledger's 'What the paper says'"""
        for f in FIGURES:
            if f["As the paper prints it"]:
                with self.subTest(f["Figure ID"]):
                    self.assertIn(norm(f["As the paper prints it"]), norm(LEDGER[f["Ledger ID"]]["What the paper says"]))

    def test_value_rounds_to_the_printed_figure(self):
        """each exact value rounds to the figure the paper prints (or a reason says why not)"""
        for f in FIGURES:
            v, text = number(f["Value"]), f["As the paper prints it"]
            if v is None or not text or not printed_numbers(text):
                continue
            with self.subTest(f["Figure ID"]):
                if f["Figure ID"] in ROUNDING_EXPLAINED:
                    self.assertFalse(rounds_to(v, text), "now rounds: drop it from ROUNDING_EXPLAINED")
                else:
                    self.assertTrue(rounds_to(v, text), f"{v} is not the printed {text!r}")

    def test_ledger_value_is_the_papers_figure(self):
        """every ledger row whose Value is one number has a line holding that number, taken from the ledger"""
        for rid, row in LEDGER.items():
            v = number(row["Value"])
            if v is None:
                continue
            with self.subTest(rid):
                held = [number(f["Value"]) for f in FIGURES if f["Ledger ID"] == rid and f["Value from"] == "Ledger"]
                self.assertIn(v, held, f"the ledger's Value {row['Value']} is not on the Figures sheet")

    def test_code_agrees_with_the_sheet(self):
        """every figure in claims.py and Part A is on one line of the Figures sheet, with the same value"""
        code = code_figures()
        named = {}
        for f in FIGURES:
            for name in filter(None, (n.strip() for n in str(f["Checked by code as"] or "").split(";"))):
                with self.subTest(name):
                    self.assertIn(name, code, f"{f['Figure ID']} names a figure the code does not hold")
                    self.assertNotIn(name, named, f"named on {named.get(name)} and {f['Figure ID']}")
                    named[name] = f["Figure ID"]
                    value = code[name][0]
                    if value is None:                            # an absence: the paper says there is none
                        self.assertEqual(f["Value"], "none")
                    elif number(value) is None:
                        self.assertEqual(f["Value"], value)
                    else:
                        self.assertEqual(number(f["Value"]), Decimal(str(value)), f["Figure ID"])
        missing = sorted(set(code) - set(named) - set(NOT_THE_PAPERS))
        self.assertEqual(missing, [], "figures in the code with no line on the Figures sheet")
        self.assertEqual(sorted(set(NOT_THE_PAPERS) - set(code)), [], "NOT_THE_PAPERS names a figure the code no longer holds")


if __name__ == "__main__":
    unittest.main()
