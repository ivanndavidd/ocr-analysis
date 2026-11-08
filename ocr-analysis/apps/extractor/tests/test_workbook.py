import json
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError
from typer.testing import CliRunner

from contract_extractor import cli
from contract_extractor.output.workbook import CHECK_FILL, MAX_WIDTH, WorkbookSpec, build_workbook

COLUMNS = [
    {"header": "NIK KTP"},
    {"header": "NAMA KARYAWAN"},
    {"header": "START PERIODE", "type": "date"},
    {"header": "MONTH", "type": "number"},
]


def spec(*sheets: dict) -> dict:
    return {"columns": COLUMNS, "sheets": list(sheets) or [{"name": "ALL", "rows": [row()]}]}


def row(nik="************0001", name="Budi Santoso", start="2026-01-01", month=12, check=None) -> list[dict]:
    cells = [{"value": nik}, {"value": name}, {"value": start}, {"value": month}]
    if check:
        cells[check[0]]["check"] = check[1]
    return cells


def workbook(data: dict):
    return load_workbook(BytesIO(build_workbook(WorkbookSpec.model_validate(data))))


def test_each_sheet_has_a_bold_frozen_filterable_header():
    wb = workbook(spec({"name": "ALL", "rows": [row(), row()]}, {"name": "IMECON", "rows": [row()]}))
    assert wb.sheetnames == ["ALL", "IMECON"]
    for ws, last in ((wb["ALL"], 3), (wb["IMECON"], 2)):
        assert [c.value for c in ws[1]] == [c["header"] for c in COLUMNS]
        assert all(c.font.bold for c in ws[1])
        assert ws.freeze_panes == "A2"
        assert ws.auto_filter.ref == f"A1:D{last}"


def test_values_keep_their_type():
    ws = workbook(spec())["ALL"]
    nik, name, start, month = (c for c in ws[2])
    assert nik.value == "************0001" and nik.data_type == "s" and nik.number_format == "@"
    assert start.value == datetime(2026, 1, 1) and "mmmm" in start.number_format
    assert month.value == 12 and month.data_type == "n"
    assert name.fill.fgColor.rgb != CHECK_FILL.fgColor.rgb


def test_a_full_nik_stays_text_and_is_never_shown_as_a_number():
    ws = workbook(spec({"name": "ALL", "rows": [row(nik="3199990101900001")]}))["ALL"]
    assert ws["A2"].value == "3199990101900001" and ws["A2"].data_type == "s"


def test_a_cell_to_check_is_coloured_and_says_why_and_may_be_empty():
    ws = workbook(spec({"name": "ALL", "rows": [row(start=None, check=(2, "Periode tidak ditemukan di dokumen"))]}))["ALL"]
    assert ws["C2"].value is None
    assert ws["C2"].fill.fgColor.rgb == CHECK_FILL.fgColor.rgb
    assert ws["C2"].comment.text == "Periode tidak ditemukan di dokumen"


def test_columns_are_wide_enough_for_their_longest_value_and_long_text_wraps():
    long_name = "Raden Mas Muhammad Abdurrahman Wicaksono Hadiningrat Putra Mahkota"
    huge = "x" * (MAX_WIDTH + 40)
    ws = workbook(spec({"name": "ALL", "rows": [row(name=long_name), row(nik=huge, start="2026-09-30")]}))["ALL"]
    assert ws.column_dimensions["B"].width >= len(long_name)
    assert ws.column_dimensions["C"].width >= len("30 September 2026")
    assert ws.column_dimensions["D"].width >= len("MONTH") + 3  # the filter arrow must not cover the header
    assert ws.column_dimensions["A"].width <= MAX_WIDTH + 2 and ws["A3"].alignment.wrap_text


@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://x\")", "+1+1", "-2", "@SUM(A1)"])
def test_text_that_looks_like_a_formula_stays_text(value):
    ws = workbook(spec({"name": "ALL", "rows": [row(name=value)]}))["ALL"]
    assert ws["B2"].value == value and ws["B2"].data_type == "s"


@pytest.mark.parametrize(
    "data",
    [
        spec({"name": "A/B", "rows": []}),
        spec({"name": "x" * 32, "rows": []}),
        spec({"name": "ALL", "rows": []}, {"name": "all", "rows": []}),
        spec({"name": "ALL", "rows": [row()[:3]]}),
        spec({"name": "ALL", "rows": [row(start="1 Januari 2026")]}),
        spec({"name": "ALL", "rows": [row(month="12")]}),
        {**spec(), "extra": 1},
    ],
)
def test_a_malformed_spec_is_refused(data):
    with pytest.raises(ValidationError):
        WorkbookSpec.model_validate(data)


def test_cli_reads_the_spec_on_stdin_and_writes_the_workbook_on_stdout():
    outcome = CliRunner().invoke(cli.app, ["xlsx"], input=json.dumps(spec()))
    assert outcome.exit_code == 0
    assert load_workbook(BytesIO(outcome.stdout_bytes))["ALL"]["B2"].value == "Budi Santoso"


def test_cli_names_what_is_wrong_but_never_echoes_a_value():
    data = spec({"name": "ALL", "rows": [row(name="Budi Santoso", month="dua belas")]})
    outcome = CliRunner().invoke(cli.app, ["xlsx"], input=json.dumps(data))
    assert outcome.exit_code == 2
    assert "tidak valid" in outcome.output
    assert "Budi" not in outcome.output and "dua belas" not in outcome.output
