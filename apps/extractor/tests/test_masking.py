import re

import pytest

from contract_extractor.stages.masking import (
    PAGE_SEPARATOR,
    REDACTED_LINE,
    MaskLeakError,
    assert_no_leak,
    mask_pages,
    mask_text,
)

# Leak corpus: synthetic NIK, account, phone, and salary values in many separator formats.
LEAK_CORPUS = [
    "NIK : 3171012345670001",
    "NIK : 3171 0123 4567 0001",
    "NIK : 3171.0123.4567.0001",
    "NIK : 3171-0123-4567-0001",
    "No. KTP 3201234567890123",
    "No. Rekening 1234567890",
    "Rekening BCA 123-456-7890",
    "Hubungi 0812-3456-7890",
    "Hubungi +62 812 3456 7890",
    "dibayar Rp 7.500.000,-",
    "dibayar Rp7.500.000",
    "dibayar Rp. 7.500.000",
    "dibayar Rp  7.500.000",
    "dibayar IDR 7,500,000",
]


@pytest.mark.parametrize("line", LEAK_CORPUS)
def test_leak_corpus_leaves_no_long_digit_run_or_amount(line):
    masked = mask_text(line).text
    assert not re.search(r"\d{5,}", masked.replace(" ", "").replace(".", "").replace("-", ""))
    assert_no_leak(masked)


@pytest.mark.parametrize(
    "line",
    ["Gaji pokok sebesar tujuh juta rupiah", "Tunjangan transport diberikan", "THR dibayarkan", "Bonus tahunan"],
)
def test_sensitive_keyword_lines_are_replaced_whole(line):
    assert mask_text(line).text == REDACTED_LINE


@pytest.mark.parametrize(
    "line",
    [
        "Alamat : Jl. Mawar No. 1, Bandung",
        "Tempat/Tgl Lahir : Bandung, 1 Januari 1990",
        "Tempat dan tanggal lahir : Bandung, 1 Januari 1990",
        "Tempat, Tanggal Lahir : Bandung, 01-01-1990",
        "TTL : Bandung, 1 Januari 1990",
        "Telepon : 022-1234567",
    ],
)
def test_unneeded_identity_lines_are_replaced_whole(line):
    assert mask_text(line).text == REDACTED_LINE


def test_labels_needed_to_tell_parties_apart_are_kept():
    masked = mask_text("Nama : Budi Santoso\nJabatan : Staff Gudang\nNIK : 3171012345670001").text
    assert masked == "Nama : Budi Santoso\nJabatan : Staff Gudang\nNIK : [REDACTED-ID]"


@pytest.mark.parametrize(
    "line",
    [
        "berlaku efektif mulai tanggal 1 Januari 2026 sampai dengan tanggal 31 Desember 2026",
        "periode 01-01-2024 - 31-03-2024",
        "periode 01.01.2024 31.03.2024",
        "dimulai sejak tanggal 29-12-2023",
        "Dua Puluh Sembilan bulan Desember tahun Dua Ribu Dua Puluh Tiga",
    ],
)
def test_dates_survive_masking(line):
    assert mask_text(line).text == line


def test_money_words_are_masked():
    assert mask_text("sebesar 7,5 juta per bulan").text == "sebesar [REDACTED] per bulan"
    assert mask_text("sejumlah tujuh juta lima ratus ribu rupiah").text == "sejumlah [REDACTED]"


def test_counts_are_reported_for_the_log():
    result = mask_text("NIK : 3171012345670001\nAlamat : Jl. Mawar\nGaji Rp 5.000.000")
    assert result.counts["deret_angka"] == 1
    assert result.counts["baris_identitas"] == 1
    assert result.counts["baris_sensitif"] == 1
    assert result.total == 3


@pytest.mark.parametrize("leaky", ["NIK 3171012345670001", "sebesar Rp 5.000.000", "HP 0812 3456 7890"])
def test_recheck_fails_closed_on_unmasked_text(leaky):
    with pytest.raises(MaskLeakError):
        assert_no_leak(leaky)


def test_input_nul_bytes_cannot_smuggle_digits_through_date_placeholders():
    masked = mask_text("NIK 3171\x00012345670001").text
    assert "3171" not in masked


def test_pages_are_masked_before_the_cut_so_no_partial_nik_survives():
    page = "x" * 3990 + "NIK 3171012345670001"  # a cut-first approach would keep "317101"
    masked = mask_pages([page], max_chars_per_page=4000).text
    assert len(masked) == 4000
    assert "317101" not in masked


def test_each_page_is_cut_separately_and_counts_add_up():
    pages = ["NIK : 3171012345670001\n" + "a" * 5000, "Alamat : Jl. Mawar\n" + "b" * 5000]
    result = mask_pages(pages, max_chars_per_page=1000)
    first, second = result.text.split(PAGE_SEPARATOR)
    assert len(first) == len(second) == 1000
    assert first.startswith("NIK : [REDACTED-ID]") and second.startswith(REDACTED_LINE)
    assert result.counts["deret_angka"] == 1 and result.counts["baris_identitas"] == 1


def test_digits_at_page_edges_do_not_join_across_the_separator():
    result = mask_pages(["kode 12345", "67890 lanjut"], max_chars_per_page=4000)
    assert result.text == "kode 12345" + PAGE_SEPARATOR + "67890 lanjut"
