"""Workbook "data output" untuk website: lembar dan baris disusun backend, modul ini hanya menulis Excel-nya.

Spesifikasi masuk sebagai JSON (`contract-extractor xlsx` lewat stdin) dan sudah memuat nilai akhir, jadi tidak ada
aturan bisnis di sini: header tebal dengan filter dan urut, baris pertama dibekukan, kolom selebar isinya, tanggal
sebagai tanggal Excel, dan sel yang perlu dicek diberi warna serta komentar alasannya.
"""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO
from typing import Literal

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contract_extractor.output.export import FORMULA_PREFIXES

MAX_SHEETS = 100
MAX_ROWS = 100_000
MAX_TEXT = 1_000
MAX_WIDTH = 100  # characters; longer text wraps inside the cell instead of widening the column further
FILTER_BUTTON = 3  # room for the autofilter arrow next to the header text
CHECK_FILL = PatternFill("solid", fgColor="FFF2CC")  # light yellow: read but doubtful, or not found
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
DATE_FORMAT = "[$-421]d mmmm yyyy"  # Indonesian month names, e.g. 1 Januari 2026
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
         "November", "Desember"]
_SHEET_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    header: str = Field(min_length=1, max_length=60)
    type: Literal["text", "date", "number"] = "text"


class Cell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | int | None = None
    check: str | None = Field(default=None, max_length=MAX_TEXT)  # why the cell needs a look; colours it

    @field_validator("value")
    @classmethod
    def _short(cls, value: str | int | None) -> str | int | None:
        if isinstance(value, str) and len(value) > MAX_TEXT:
            raise ValueError("value too long")
        return value


class Sheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=31)
    rows: list[list[Cell]] = Field(max_length=MAX_ROWS)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        if _SHEET_FORBIDDEN.search(name) or name.startswith("'") or name.endswith("'"):
            raise ValueError("sheet name has a character Excel refuses")
        return name


class WorkbookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[Column] = Field(min_length=1, max_length=50)
    sheets: list[Sheet] = Field(min_length=1, max_length=MAX_SHEETS)

    @model_validator(mode="after")
    def _consistent(self) -> WorkbookSpec:
        names = [s.name.casefold() for s in self.sheets]
        if len(set(names)) != len(names):
            raise ValueError("sheet names must be unique")  # Excel compares them without case
        width = len(self.columns)
        for sheet in self.sheets:
            for row in sheet.rows:
                if len(row) != width:
                    raise ValueError("every row needs one cell per column")
                for cell, column in zip(row, self.columns):
                    if cell.value is None:
                        continue
                    if column.type == "date" and not (isinstance(cell.value, str) and _iso_date(cell.value)):
                        raise ValueError("date cells take YYYY-MM-DD")
                    if column.type == "number" and not isinstance(cell.value, int):
                        raise ValueError("number cells take whole numbers")
        return self


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if len(value) == 10 else None
    except ValueError:
        return None


def _shown(value: str | int | None, column: Column) -> str:
    """The text Excel displays, to size the column."""
    if value is None:
        return ""
    if column.type == "date" and isinstance(value, str) and (day := _iso_date(value)):
        return f"{day.day} {BULAN[day.month - 1]} {day.year}"
    return str(value)


def _write_sheet(ws, spec: WorkbookSpec, sheet: Sheet) -> None:
    ws.append([c.header for c in spec.columns])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    widths = [len(c.header) + FILTER_BUTTON for c in spec.columns]

    for r, row in enumerate(sheet.rows, start=2):
        for i, (spec_cell, column) in enumerate(zip(row, spec.columns), start=1):
            value = spec_cell.value
            cell = ws.cell(row=r, column=i)
            if column.type == "date" and isinstance(value, str):
                cell.value = _iso_date(value)
                cell.number_format = DATE_FORMAT
            elif isinstance(value, str):
                cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)
                cell.number_format = "@"  # a NIK stays text, never a number in scientific notation
                # openpyxl turns any string starting with "=" into a formula; force plain text instead.
                if cell.value.startswith(FORMULA_PREFIXES):
                    cell.data_type = "s"
            else:
                cell.value = value
            if spec_cell.check:
                cell.fill = CHECK_FILL
                cell.comment = Comment(ILLEGAL_CHARACTERS_RE.sub("", spec_cell.check), "contract-ocr")
            text = _shown(value, column)
            widths[i - 1] = max(widths[i - 1], min(len(text), MAX_WIDTH))
            if len(text) > MAX_WIDTH:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width + 2
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(spec.columns))}{max(ws.max_row, 1)}"


def build_workbook(spec: WorkbookSpec) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for sheet in spec.sheets:
        _write_sheet(wb.create_sheet(sheet.name), spec, sheet)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
