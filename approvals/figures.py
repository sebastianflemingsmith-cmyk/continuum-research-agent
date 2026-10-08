"""
Adds the Figures sheet to the watch list: every figure in the paper, one line each. The lines are in
`Figures 2026-10-04.csv` (made from the Ledger's "What the paper says" and "Value", and the reader's rules).
The tabular review (step 5) is to be built on this sheet.

    python3 Agent/approvals/figures.py            add the sheet (or replace it) in the watch list
    python3 Agent/approvals/figures.py --check    say what it would do; write nothing

It changes only two parts of the workbook: it adds the Figures sheet after the Ledger, and on the Ledger it sets
F017's Value to the paper's figure (30, not the release's 31.08). Every other part is copied unchanged. It works on
the workbook as openpyxl wrote it and as Excel saves it. Standard library only.
"""
import csv
import os
import re
import sys
import tempfile
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.dirname(HERE)
WATCH_LIST = os.path.join(AGENT, "Watch list - The AI Lose Lose Race.xlsx")
LINES = os.path.join(HERE, "Figures 2026-10-04.csv")
SHEET = "Figures"
AFTER = "Ledger"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WS_TYPE = RELS + "/worksheet"
WS_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"

# Ledger cells set to the paper's figure: row ID -> (column, the value it held, the paper's figure)
LEDGER_FIXES = {"F017": ("Value", "31.08", "30")}

TITLE = "The figures"
SUBTITLE = ("Every figure in the paper (last updated 23 September 2026), one line each, as the paper stands: no correction is "
            "applied. 'As the paper prints it' is copied word for word from the Ledger's 'What the paper says'; 'Value' is "
            "the exact figure behind it, and 'Value from' says where that is held. Built 4 October 2026.")
WIDTHS = [9, 8, 44, 30, 12, 16, 22, 14, 36, 50]
TEXT_UNITS = {"year", "date"}                  # values in these units stay text, even when they look like numbers
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


def read_lines(path=LINES):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _attr(tag, name):
    m = re.search(r'\s%s="([^"]*)"' % re.escape(name), tag)
    return m.group(1) if m else None


def _part(target):
    return target.lstrip("/") if target.startswith("/") else "xl/" + target


def _sheets(wb):
    """[(sheet element text, name, r:id)] in workbook order."""
    out = []
    for tag in re.findall(r"<(?:\w+:)?sheet\s[^>]*/>", wb):
        rid = re.search(r'\s(?:\w+:)?id="([^"]*)"', tag)          # r:id (sheetId does not match: no space before "id")
        out.append((tag, _attr(tag, "name"), rid.group(1)))
    return out


def _rels(rels):
    return {_attr(t, "Id"): t for t in re.findall(r"<(?:\w+:)?Relationship\s[^>]*/>", rels)}


def _shared(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    xml = z.read("xl/sharedStrings.xml").decode("utf-8")
    return ["".join(re.findall(r"<(?:\w+:)?t(?:\s[^>]*)?>([^<]*)</(?:\w+:)?t>", si))
            for si in re.findall(r"<(?:\w+:)?si>(.*?)</(?:\w+:)?si>", xml, re.S)]


def _cells(row_xml):
    """{column letters: (cell xml, attributes)} for one row."""
    out = {}
    for m in re.finditer(r"<(?:\w+:)?c\s([^>]*?)(?:/>|>(.*?)</(?:\w+:)?c>)", row_xml, re.S):
        ref = _attr(" " + m.group(1), "r")
        out[re.match(r"[A-Z]+", ref).group(0)] = (m.group(0), " " + m.group(1))
    return out


def _text(cell, attrs, shared):
    t = _attr(attrs, "t")
    if t == "s":
        return shared[int(re.search(r"<(?:\w+:)?v>([^<]*)<", cell).group(1))]
    if t == "inlineStr":
        return "".join(re.findall(r"<(?:\w+:)?t(?:\s[^>]*)?>([^<]*)<", cell))
    v = re.search(r"<(?:\w+:)?v>([^<]*)<", cell)
    return v.group(1) if v else None


def _ledger_rows(xml):
    """[(row number, row xml)]; an empty row written as <row .../> is kept apart from the next one."""
    return [(int(_attr(" " + m.group(1), "r")), m.group(0))
            for m in re.finditer(r"<(?:\w+:)?row\s([^>]*?)(?:/>|>.*?</(?:\w+:)?row>)", xml, re.S)]


def fix_ledger(xml, shared):
    """The Ledger with LEDGER_FIXES applied, and a list of what changed."""
    rows = _ledger_rows(xml)
    header = None
    for _, row in rows:
        cells = _cells(row)
        if "A" in cells and _text(*cells["A"], shared) == "ID":
            header = {_text(*c, shared): col for col, c in cells.items()}
            break
    if not header:
        raise SystemExit("The Ledger has no header row starting with 'ID'.")
    done = []
    for _, row in rows:
        cells = _cells(row)
        rid = _text(*cells["A"], shared) if "A" in cells else None
        if rid not in LEDGER_FIXES:
            continue
        column, old, new = LEDGER_FIXES[rid]
        cell, attrs = cells[header[column]]
        now = _text(cell, attrs, shared)
        if now == new:
            continue
        if re.search(r"<(?:\w+:)?f[\s>]", cell):
            raise SystemExit(f"Ledger {rid} {column} holds a formula: not changed. Check it by hand.")
        if now != old or _attr(attrs, "t") not in (None, "n"):
            raise SystemExit(f"Ledger {rid} {column} holds {now!r}, not {old!r}: not changed. Check it by hand.")
        fixed = re.sub(r"(<(?:\w+:)?v>)[^<]*(<)", r"\g<1>%s\2" % new, cell, count=1)
        xml = xml.replace(cell, fixed, 1)
        done.append(f"Ledger {rid} {column}: {old} -> {new}")
    return xml, done


def styles(xml, shared):
    """The Ledger's own styles for the title, the note under it, the header and a plain body cell."""
    rows = dict(_ledger_rows(xml))
    title = _attr(_cells(rows[1])["A"][1], "s") or "0"
    note = _attr(_cells(rows[2])["A"][1], "s") or "0"
    header = body = None
    counts = {}
    for n, row in sorted(rows.items()):
        cells = _cells(row)
        if "A" not in cells:
            continue
        if header is None and _text(*cells["A"], shared) == "ID":
            header = _attr(cells["A"][1], "s") or "0"
        elif header is not None:
            s = _attr(cells["A"][1], "s") or "0"
            counts[s] = counts.get(s, 0) + 1
    body = max(counts, key=counts.get)                       # the plain rows outnumber the coloured ones
    return title, note, header, body


def _cell(ref, value, style, as_number=False):
    if value in (None, ""):
        return ""
    if as_number:
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def sheet_xml(header, lines, st):
    title, note, head, body = st
    last = 4 + len(lines)
    unit_col, value_col = header.index("Unit"), header.index("Value")
    rows = [f'<row r="1" ht="24" customHeight="1">{_cell("A1", TITLE, title)}</row>',
            f'<row r="2">{_cell("A2", SUBTITLE, note)}</row>',
            '<row r="4" ht="30" customHeight="1">' + "".join(_cell(f"{col_letter(i)}4", h, head) for i, h in enumerate(header)) + "</row>"]
    for n, line in enumerate(lines, 5):
        cells = []
        for i, v in enumerate(line):
            number = i == value_col and bool(NUMBER.match(v)) and line[unit_col] not in TEXT_UNITS
            cells.append(_cell(f"{col_letter(i)}{n}", v, body, number))
        rows.append(f'<row r="{n}">' + "".join(cells) + "</row>")
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(WIDTHS, 1))
    end = col_letter(len(header) - 1)
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{MAIN}" xmlns:r="{RELS}">'
            f'<dimension ref="A1:{end}{last}"/>'
            '<sheetViews><sheetView workbookViewId="0"><pane xSplit="3" ySplit="4" topLeftCell="D5" activePane="bottomRight" '
            'state="frozen"/><selection pane="bottomRight" activeCell="D5" sqref="D5"/></sheetView></sheetViews>'
            f'<sheetFormatPr defaultRowHeight="15"/><cols>{cols}</cols><sheetData>{"".join(rows)}</sheetData>'
            f'<autoFilter ref="A4:{end}{last}"/>'
            '<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/></worksheet>')


def add_sheet(wb, rels, types, part_name, filter_ref):
    """workbook.xml, its rels and [Content_Types].xml with the new sheet after AFTER."""
    sheets = _sheets(wb)
    names = [s[1] for s in sheets]
    at = names.index(AFTER) + 1                               # the new sheet's position (0-based)
    ids = [int(re.sub(r"\D", "", i) or 0) for i in _rels(rels)]
    rid = f"rId{max(ids) + 1}"
    sheet_id = max(int(_attr(s[0], "sheetId")) for s in sheets) + 1
    new = f'<sheet xmlns:r="{RELS}" name="{SHEET}" sheetId="{sheet_id}" r:id="{rid}"/>'
    wb = wb.replace(sheets[at - 1][0], sheets[at - 1][0] + new, 1)

    def shift(m):                                             # sheets after the new one move up one place
        n = int(m.group(2))
        return f'{m.group(1)}"{n + 1 if n >= at else n}"'
    wb = re.sub(r'(<(?:\w+:)?definedName\s[^>]*?localSheetId=)"(\d+)"', shift, wb)
    wb = re.sub(r'(<(?:\w+:)?workbookView\s[^>]*?activeTab=)"(\d+)"', shift, wb)
    name = f'<definedName name="_xlnm._FilterDatabase" localSheetId="{at}" hidden="1">\'{SHEET}\'!{filter_ref}</definedName>'
    if re.search(r"</(?:\w+:)?definedNames>", wb):
        wb = re.sub(r"(</(?:\w+:)?definedNames>)", name + r"\1", wb, count=1)
    else:
        wb = re.sub(r"(</(?:\w+:)?sheets>)", r"\1<definedNames>" + name + "</definedNames>", wb, count=1)
    rels = re.sub(r"(</(?:\w+:)?Relationships>)",
                  f'<Relationship Type="{WS_TYPE}" Target="/{part_name}" Id="{rid}"/>' + r"\1", rels, count=1)
    types = re.sub(r"(</(?:\w+:)?Types>)", f'<Override PartName="/{part_name}" ContentType="{WS_CT}"/>' + r"\1", types, count=1)
    return wb, rels, types


def build(path, lines_path=LINES):
    """{part name: new bytes} for the workbook at `path`, and a list of what changes."""
    header, lines = read_lines(lines_path)
    with zipfile.ZipFile(path) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        types = z.read("[Content_Types].xml").decode("utf-8")
        shared = _shared(z)
        targets = {rid: _part(_attr(t, "Target")) for rid, t in _rels(rels).items()}
        by_name = {name: targets[rid] for _, name, rid in _sheets(wb)}
        if AFTER not in by_name:
            raise SystemExit(f"No {AFTER} sheet in {path}.")
        ledger_part = by_name[AFTER]
        ledger = z.read(ledger_part).decode("utf-8")
        names = z.namelist()
    new_ledger, done = fix_ledger(ledger, shared)
    st = styles(ledger, shared)
    end = col_letter(len(header) - 1)
    xml = sheet_xml(header, lines, st).encode("utf-8")
    parts = {}
    if SHEET in by_name:
        parts[by_name[SHEET]] = xml
        done.append(f"{SHEET} sheet replaced: {len(lines)} figures")
    else:
        n = 1
        while f"xl/worksheets/sheet{n}.xml" in names:
            n += 1
        part = f"xl/worksheets/sheet{n}.xml"
        wb, rels, types = add_sheet(wb, rels, types, part, f"$A$4:${end}${4 + len(lines)}")
        parts.update({part: xml, "xl/workbook.xml": wb.encode("utf-8"), "xl/_rels/workbook.xml.rels": rels.encode("utf-8"),
                      "[Content_Types].xml": types.encode("utf-8")})
        done.append(f"{SHEET} sheet added after the {AFTER}: {len(lines)} figures")
    if new_ledger != ledger:
        parts[ledger_part] = new_ledger.encode("utf-8")
    return parts, done


def write(path, parts):
    """Rewrite the workbook with `parts` replaced or added; every other part is copied as it was, in the same order."""
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w") as dst:
            for info in src.infolist():
                data = parts.pop(info.filename, None)
                dst.writestr(info, src.read(info.filename) if data is None else data)
            for name, data in parts.items():                 # new parts go at the end
                dst.writestr(zipfile.ZipInfo(name, date_time=src.infolist()[0].date_time), data, zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main(argv):
    check = "--check" in argv
    args = [a for a in argv if a != "--check"]
    path = args[0] if args else WATCH_LIST
    parts, done = build(path)
    for line in done:
        print(line)
    if check:
        print("(--check: nothing written)")
    else:
        write(path, parts)
        print(f"Written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
