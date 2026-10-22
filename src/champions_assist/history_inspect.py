"""Read-only structural inspection for private XLSX history files.

This intentionally reports workbook structure only. It performs no battle-log
interpretation and creates no import records.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from typing import Any


MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return "".join(element.itertext())


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text(item) or "" for item in root.findall(f"{MAIN_NS}si")]


def _cell_summary(cell: ET.Element, shared_strings: list[str]) -> dict[str, str | None]:
    cell_type = cell.attrib.get("t", "n")
    formula = _text(cell.find(f"{MAIN_NS}f"))
    raw_value = _text(cell.find(f"{MAIN_NS}v"))
    if formula is not None:
        return {"cell": cell.attrib.get("r"), "kind": "formula", "value": None}
    if cell_type == "s" and raw_value is not None:
        index = int(raw_value)
        value = shared_strings[index] if 0 <= index < len(shared_strings) else None
        return {"cell": cell.attrib.get("r"), "kind": "shared_string", "value": value}
    if cell_type == "inlineStr":
        return {"cell": cell.attrib.get("r"), "kind": "inline_string", "value": _text(cell.find(f"{MAIN_NS}is"))}
    if cell_type == "b":
        return {"cell": cell.attrib.get("r"), "kind": "boolean", "value": raw_value}
    return {"cell": cell.attrib.get("r"), "kind": "value", "value": raw_value}


def inspect_workbook(path: str | Path) -> dict[str, Any]:
    """Return sheet names, dimensions, and first-row cells without changing XLSX data."""
    workbook_path = Path(path)
    digest = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(workbook_path) as archive:
        if "xl/workbook.xml" not in archive.namelist():
            raise ValueError("not an XLSX workbook: xl/workbook.xml is missing")
        shared_strings = _shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            relation.attrib["Id"]: relation.attrib["Target"]
            for relation in relations.findall(f"{PACKAGE_REL_NS}Relationship")
        }
        sheets: list[dict[str, Any]] = []
        for sheet in workbook.findall(f"{MAIN_NS}sheets/{MAIN_NS}sheet"):
            relation_id = sheet.attrib[f"{REL_NS}id"]
            target = targets[relation_id]
            sheet_path = posixpath.normpath(posixpath.join("xl", target))
            root = ET.fromstring(archive.read(sheet_path))
            first_row = root.find(f"{MAIN_NS}sheetData/{MAIN_NS}row")
            dimension = root.find(f"{MAIN_NS}dimension")
            sheets.append(
                {
                    "name": sheet.attrib["name"],
                    "sheet_id": sheet.attrib["sheetId"],
                    "dimension": None if dimension is None else dimension.attrib.get("ref"),
                    "first_row_cells": [] if first_row is None else [
                        _cell_summary(cell, shared_strings) for cell in first_row.findall(f"{MAIN_NS}c")
                    ],
                }
            )
    return {
        "source_path": str(workbook_path),
        "source_sha256": digest,
        "mode": "READ_ONLY_STRUCTURE_ONLY",
        "sheets": sheets,
        "semantic_mapping": "NOT_INFERRED",
    }
