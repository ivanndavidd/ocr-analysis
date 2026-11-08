from datetime import date

import pytest

from contract_extractor.common.dates import months_between
from contract_extractor.extractor_method.pattern.filename import (
    FLAG_NONSTANDARD,
    FLAG_UNPARSED,
    Match,
    ParsedFilename,
    compare_name,
    compare_period,
    normalize_name,
    parse_filename,
)


@pytest.mark.parametrize(
    ("filename", "name", "start", "end"),
    [
        ("Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf", "Budi Santoso", date(2026, 1, 1), date(2026, 12, 31)),
        ("Siti Nur-Aini - 01 Jan 2026 - 31 Des 2026.pdf", "Siti Nur-Aini", date(2026, 1, 1), date(2026, 12, 31)),
        ("Budi Santoso - 1 Januari 2026 sd 31 Desember 2026.pdf", "Budi Santoso", date(2026, 1, 1), date(2026, 12, 31)),
        ("Budi Santoso - 1jan-31mar24.pdf", "Budi Santoso", date(2024, 1, 1), date(2024, 3, 31)),
        ("Budi Santoso - 1nov-31jan25.pdf", "Budi Santoso", date(2024, 11, 1), date(2025, 1, 31)),
        ("Budi Santoso - 01-01-2026 - 31-12-2026.pdf", "Budi Santoso", date(2026, 1, 1), date(2026, 12, 31)),
        ("Budi Santoso - 1 July 2026 - 30 June 2027.PDF", "Budi Santoso", date(2026, 7, 1), date(2027, 6, 30)),
    ],
)
def test_parses_range_forms(filename, name, start, end):
    assert parse_filename(filename) == ParsedFilename(name=name, start=start, end=end)


@pytest.mark.parametrize(("filename", "months"), [("Budi Santoso - 12 bulan.pdf", 12), ("Budi Santoso - 1 tahun.pdf", 12)])
def test_parses_duration_forms(filename, months):
    assert parse_filename(filename) == ParsedFilename(name="Budi Santoso", duration_months=months)


@pytest.mark.parametrize(
    ("filename", "parsed"),
    [
        (
            "Budi Santoso - 1jan-31mar24 - SNA.pdf",
            ParsedFilename(name="Budi Santoso", start=date(2024, 1, 1), end=date(2024, 3, 31), entity="SNA"),
        ),
        (
            "Budi Santoso - 1 Januari 2026 - 31 Desember 2026 - PT Sinar Niaga.pdf",
            ParsedFilename(name="Budi Santoso", start=date(2026, 1, 1), end=date(2026, 12, 31), entity="PT Sinar Niaga"),
        ),
        (
            "Budi Santoso - 01-01-2026 - 31-12-2026 -  PT  Sinar Niaga .pdf",
            ParsedFilename(name="Budi Santoso", start=date(2026, 1, 1), end=date(2026, 12, 31), entity="PT Sinar Niaga"),
        ),
        ("Budi Santoso - 12 bulan - SNA.pdf", ParsedFilename(name="Budi Santoso", duration_months=12, entity="SNA")),
        (
            "Budi Santoso - 1jan-31mar24 - PT Sinar Niaga - Jakarta.pdf",
            ParsedFilename(
                name="Budi Santoso", start=date(2024, 1, 1), end=date(2024, 3, 31), entity="PT Sinar Niaga - Jakarta"
            ),
        ),
    ],
)
def test_parses_the_entity_after_the_period(filename, parsed):
    assert parse_filename(filename) == parsed


def test_entity_is_not_guessed_when_the_period_is_unreadable():
    parsed = parse_filename("Budi Santoso - kontrak baru - SNA.pdf")
    assert parsed.flags == (FLAG_UNPARSED,)
    assert parsed.entity is None


def test_missing_separator_is_nonstandard_and_whole_stem_is_the_name():
    parsed = parse_filename("Budi Santoso 1jan-31mar24.pdf")
    assert parsed.flags == (FLAG_NONSTANDARD,)
    assert parsed.name == "Budi Santoso 1jan-31mar24"


@pytest.mark.parametrize(
    "filename",
    [
        "Budi Santoso - kontrak baru.pdf",
        "Budi Santoso - 31 Februari 2026 - 1 Maret 2026.pdf",
        "Budi Santoso - 1 Desember 2026 - 1 Januari 2026.pdf",
        "Budi Santoso - 1 Foo 2026 - 1 Bar 2026.pdf",
    ],
)
def test_unparseable_period_is_flagged(filename):
    parsed = parse_filename(filename)
    assert parsed.flags == (FLAG_UNPARSED,)
    assert parsed.name == "Budi Santoso"


def test_normalize_name_drops_titles_and_punctuation():
    assert normalize_name("Sdr. Budi Santoso, S.Kom") == "budi santoso"
    assert normalize_name("Ibu Siti Nur-Aini, S.E.") == "siti nur aini"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Abd.karim", "ABD KARIM", Match.COCOK),
        ("Abdkarim", "Abd. Karim", Match.COCOK),
        ("Budi Santoso", "Sdr. BUDI SANTOSO", Match.COCOK),
        ("Muhammad Rizky", "Muhamad Rizki", Match.MIRIP),
        ("Budi Santoso", "Rina Wulandari", Match.TIDAK_COCOK),
    ],
)
def test_compare_name(a, b, expected):
    assert compare_name(a, b) is expected


def test_compare_period_exact_near_and_far():
    parsed = ParsedFilename(name="x", start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert compare_period(parsed, date(2026, 1, 1), date(2026, 12, 31)) is Match.COCOK
    assert compare_period(parsed, date(2026, 1, 2), date(2026, 12, 29)) is Match.MIRIP
    assert compare_period(parsed, date(2026, 2, 1), date(2026, 12, 31)) is Match.TIDAK_COCOK
    assert compare_period(parsed, None, date(2026, 12, 31)) is Match.TIDAK_COCOK


def test_compare_period_duration():
    parsed = ParsedFilename(name="x", duration_months=3)
    assert compare_period(parsed, date(2024, 1, 1), date(2024, 3, 31)) is Match.COCOK
    assert compare_period(parsed, None, None, duration_months=4) is Match.MIRIP
    assert compare_period(parsed, None, None, duration_months=6) is Match.TIDAK_COCOK


def test_compare_period_without_filename_period_is_none():
    assert compare_period(ParsedFilename(name="x", flags=(FLAG_NONSTANDARD,)), date(2026, 1, 1), None) is None


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (date(2024, 1, 1), date(2024, 3, 31), 3),
        (date(2024, 1, 15), date(2024, 4, 14), 3),
        (date(2026, 1, 1), date(2026, 12, 31), 12),
        (date(2023, 12, 29), date(2024, 3, 28), 3),
    ],
)
def test_months_between(start, end, months):
    assert months_between(start, end) == months
