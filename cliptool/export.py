"""Export a run's clip list (No., title, upload description, ...) as an Excel table or CSV.

The .xlsx is written directly (it is a zip of a few XML files), so no extra package is needed.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

COLUMNS = [("No.", 6), ("Title", 38), ("Description", 90), ("Video file", 44), ("Length (s)", 11)]


def rows(run_dir: Path) -> tuple[str, list[list]]:
    """(video title, rows) with each clip's final upload description."""
    from . import assets, description
    from .cache import Work
    from .pipeline import resolve_work_dir

    data = json.loads((run_dir / "clips.json").read_text(encoding="utf-8"))
    wd = resolve_work_dir(data)
    work = Work(wd.parent, wd.name)
    info = work.load("info.json") if work.has("info.json") else dict(data["video"])
    if work.has("info.json"):
        info = description.ensure_channel_details(info, work)
    settings = assets.load_settings()["description"]
    out = []
    for c in sorted(data["clips"], key=lambda c: c["rank"]):
        f = run_dir / (c.get("file") or "")
        length = round(_duration(f), 1) if c.get("file") and f.exists() else round(float(c["end"]) - float(c["start"]), 1)
        out.append([c["rank"], c.get("title", ""), description.for_clip(info, c, settings),
                    Path(c.get("file") or "").name, length])
    return data["video"].get("title") or run_dir.name, out


def _duration(path: Path) -> float:
    from .pipeline import _duration as probe

    return probe(path)


def to_csv(table: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([name for name, _ in COLUMNS])
    w.writerows(table)
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM: Excel opens emoji/Telugu text correctly


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value, style: int) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value)).replace("\r", "")
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def to_xlsx(table: list[list], sheet: str = "Clips") -> bytes:
    ncol, nrow = len(COLUMNS), len(table) + 1
    last = f"{_col(ncol - 1)}{nrow}"
    sheet_rows = ['<row r="1">' + "".join(_cell(f"{_col(i)}1", n, 1) for i, (n, _) in enumerate(COLUMNS)) + "</row>"]
    for r, row in enumerate(table, start=2):
        sheet_rows.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", v, 2) for i, v in enumerate(row)) + "</row>")
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, (_, w) in enumerate(COLUMNS))
    ws = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
          '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
          '</sheetView></sheetViews>'
          f'<cols>{cols}</cols><sheetData>{"".join(sheet_rows)}</sheetData>'
          f'<autoFilter ref="A1:{last}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF97316"/></patternFill></fill></fills>'
              '<borders count="2"><border/><border><left style="thin"><color rgb="FFD0D0D0"/></left>'
              '<right style="thin"><color rgb="FFD0D0D0"/></right><top style="thin"><color rgb="FFD0D0D0"/></top>'
              '<bottom style="thin"><color rgb="FFD0D0D0"/></bottom></border></borders>'
              '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="3"><xf/>'
              '<xf fontId="1" fillId="2" borderId="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
              '<alignment vertical="center"/></xf>'
              '<xf borderId="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
              '</cellXfs></styleSheet>')
    name = escape("".join(ch for ch in sheet if ch not in '[]:*?/\\')[:31] or "Clips")
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
        '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
        f"'{name}'!$A$1:${_col(ncol - 1)}${nrow}</definedName></definedNames></workbook>",
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>',
        "xl/worksheets/sheet1.xml": ws,
        "xl/styles.xml": styles,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, body in files.items():
            z.writestr(n, body)
    return buf.getvalue()
