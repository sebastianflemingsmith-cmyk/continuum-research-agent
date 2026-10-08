"""
A small .xlsx writer and reader (standard library only). The writer does text, numbers and links, a few styles, column
widths, a frozen corner, a filter on the header row and drop-down lists: enough for the approvals sheet, which Excel
and Numbers open. The reader reads back what Excel, Numbers or LibreOffice save (shared or inline text, formulas with
or without a saved value).
"""
import re
import xml.etree.ElementTree as ET
import zipfile
from xml.sax.saxutils import escape

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_TEXT = 32000                                    # Excel's limit for one cell is 32,767 characters

# the styles a cell can have (their order is their number in styles.xml)
STYLES = ["plain", "header", "wrap", "decision", "small", "link", "title", "top"]
_FONTS = ['<font><sz val="11"/><name val="Calibri"/></font>',
          '<font><b/><sz val="11"/><name val="Calibri"/></font>',
          '<font><sz val="9"/><color rgb="FF808080"/><name val="Calibri"/></font>',
          '<font><u/><sz val="11"/><color rgb="FF0563C1"/><name val="Calibri"/></font>',
          '<font><b/><sz val="14"/><name val="Calibri"/></font>']
_FILLS = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>',
          '<fill><patternFill patternType="solid"><fgColor rgb="FFE7E6E6"/><bgColor indexed="64"/></patternFill></fill>',
          '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill>']
_TOP, _WRAP = '<alignment vertical="top"/>', '<alignment vertical="top" wrapText="1"/>'
_XFS = [(0, 0, ""), (1, 2, _WRAP), (0, 0, _WRAP), (1, 3, _WRAP), (2, 0, _WRAP), (3, 0, _WRAP), (4, 0, ""), (0, 0, _TOP)]


class Link:
    """A cell that opens a web page."""

    def __init__(self, url, text):
        self.url, self.text = url, text


class Sheet:
    """rows: lists of cell values (str, int, float, Link or None); row 1 is the header when `header` is True.
    styles: one style name per column (the header row is always 'header'). validations: (first col, last col,
    first row, last row, [choices]). freeze: (columns, rows) kept in view."""

    def __init__(self, name, rows, widths=(), styles=(), header=True, validations=(), freeze=None, row_styles=None):
        self.name, self.rows, self.widths, self.styles = name, rows, list(widths), list(styles)
        self.header, self.validations, self.freeze, self.row_styles = header, list(validations), freeze, row_styles or {}


def col_letter(i):
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _clean(text):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]", "", str(text))
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"


def _cell(ref, value, style):
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        value = "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    if isinstance(value, Link):
        text = _clean(value.text)
        if value.url and len(value.url) <= 255 and len(text) <= 255 and '"' not in value.url:
            formula = f'HYPERLINK("{value.url}","{text.replace(chr(34), chr(34) * 2)}")'
            return f'<c r="{ref}"{s} t="str"><f>{escape(formula)}</f><v>{escape(text)}</v></c>'
        value = f"{text} {value.url or ''}".strip()
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(_clean(value))}</t></is></c>'


def _sheet_xml(sh, selected):
    n_cols = max([len(r) for r in sh.rows] + [len(sh.widths), 1])
    view = '<sheetView workbookViewId="0"' + (' tabSelected="1"' if selected else "") + ">"
    if sh.freeze:
        c, r = sh.freeze
        top = f"{col_letter(c)}{r + 1}"
        pane = "bottomRight" if c and r else ("bottomLeft" if r else "topRight")
        splits = (f' xSplit="{c}"' if c else "") + (f' ySplit="{r}"' if r else "")
        view += (f'<pane{splits} topLeftCell="{top}" activePane="{pane}" state="frozen"/>'
                 f'<selection pane="{pane}" activeCell="{top}" sqref="{top}"/>')
    view += "</sheetView>"
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(sh.widths))
    rows = []
    for r, row in enumerate(sh.rows, 1):
        cells = []
        for i in range(n_cols):
            v = row[i] if i < len(row) else None
            name = "header" if (sh.header and r == 1) else (sh.row_styles.get(r) or (sh.styles[i] if i < len(sh.styles) else "wrap"))
            cells.append(_cell(f"{col_letter(i)}{r}", v, STYLES.index(name)))
        rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    last = f"{col_letter(n_cols - 1)}{max(len(sh.rows), 1)}"
    filt = f'<autoFilter ref="A1:{last}"/>' if sh.header and len(sh.rows) > 1 else ""
    dv = ""
    if sh.validations:
        items = []
        for c1, c2, r1, r2, choices in sh.validations:
            ref = f"{col_letter(c1)}{r1}:{col_letter(c2)}{r2}"
            listed = escape('"' + ",".join(choices) + '"')
            items.append(f'<dataValidation type="list" errorStyle="warning" allowBlank="1" showInputMessage="1" showErrorMessage="1" '
                         f'errorTitle="Not one of the choices" error="{escape("Choose from the list: " + ", ".join(choices))}" '
                         f'sqref="{ref}"><formula1>{listed}</formula1></dataValidation>')
        dv = f'<dataValidations count="{len(items)}">{"".join(items)}</dataValidations>'
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{MAIN}" xmlns:r="{RELS}">'
            f'<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:{last}"/><sheetViews>{view}</sheetViews><sheetFormatPr defaultRowHeight="15"/>'
            f'{"<cols>" + cols + "</cols>" if cols else ""}<sheetData>{"".join(rows)}</sheetData>{filt}{dv}'
            f'<pageMargins left="0.4" right="0.4" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
            f'<pageSetup paperSize="9" orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')


def write(path, sheets):
    """Write the sheets to an .xlsx file (the first sheet opens first)."""
    fonts = f'<fonts count="{len(_FONTS)}">{"".join(_FONTS)}</fonts>'
    fills = f'<fills count="{len(_FILLS)}">{"".join(_FILLS)}</fills>'
    xfs = "".join(f'<xf numFmtId="0" fontId="{f}" fillId="{fl}" borderId="0" xfId="0"'
                  + (' applyFont="1"' if f else "") + (' applyFill="1"' if fl else "")
                  + (f' applyAlignment="1">{a}</xf>' if a else "/>") for f, fl, a in _XFS)
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="{MAIN}">{fonts}{fills}'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              f'<cellXfs count="{len(_XFS)}">{xfs}</cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    names = [escape(s.name) for s in sheets]
    filters = "".join(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                      f"'{names[i]}'!$A$1:${col_letter(max(len(r) for r in s.rows) - 1)}${len(s.rows)}</definedName>"
                      for i, s in enumerate(sheets) if s.header and len(s.rows) > 1)
    workbook = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{MAIN}" xmlns:r="{RELS}">'
                '<bookViews><workbookView activeTab="0"/></bookViews><sheets>'
                + "".join(f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
                + "</sheets>" + (f"<definedNames>{filters}</definedNames>" if filters else "") + "</workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i + 1}" Type="{RELS}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                         for i in range(len(sheets)))
               + f'<Relationship Id="rId{len(sheets) + 1}" Type="{RELS}/styles" Target="styles.xml"/></Relationships>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
             + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                       'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets)))
             + "</Types>")
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 f'<Relationship Id="rId1" Type="{RELS}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/styles.xml", styles)
        for i, sh in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sh, i == 0))


# ------------------------------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------------------------------
def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else ""):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text(el, ns):
    """The text of a shared or inline string: its <t>, or the <t> of each rich-text run (phonetic guides left out)."""
    out = []
    for child in el:
        if child.tag == f"{{{ns}}}t":
            out.append(child.text or "")
        elif child.tag == f"{{{ns}}}r":
            out += [t.text or "" for t in child.findall(f"{{{ns}}}t")]
        elif child.tag == f"{{{ns}}}is":
            out.append(_text(child, ns))
    return "".join(out)


def read(path):
    """{sheet name: list of rows (lists of values)}. Text stays text; numbers become int or float; an empty or
    formula-only cell is None (a link reads as its text when a value was saved)."""
    out = {}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_text(si, MAIN) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{{{MAIN}}}si")]
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        for sh in wb.find(f"{{{MAIN}}}sheets"):
            target = rels[sh.get(f"{{{RELS}}}id")]
            part = target.lstrip("/") if target.startswith("/") else "xl/" + target
            rows, r_auto = {}, 0
            for row in ET.fromstring(z.read(part)).iter(f"{{{MAIN}}}row"):
                r_auto = int(row.get("r") or r_auto + 1)
                cells, c_auto = {}, -1
                for c in row.findall(f"{{{MAIN}}}c"):
                    c_auto = _col_index(c.get("r")) if c.get("r") else c_auto + 1
                    t, v = c.get("t"), c.find(f"{{{MAIN}}}v")
                    raw = v.text if v is not None else None
                    if t == "inlineStr":
                        val = _text(c, MAIN)
                    elif raw is None:
                        val = None
                    elif t == "s":
                        val = shared[int(raw)]
                    elif t in ("str", "e"):
                        val = raw
                    elif t == "b":
                        val = raw == "1"
                    else:
                        try:
                            num = float(raw)
                            val = int(num) if num.is_integer() else num
                        except ValueError:
                            val = raw
                    cells[c_auto] = val
                if cells:
                    rows[r_auto] = [cells.get(i) for i in range(max(cells) + 1)]
            out[sh.get("name")] = [rows.get(k, []) for k in range(1, max(rows) + 1)] if rows else []
    return out
