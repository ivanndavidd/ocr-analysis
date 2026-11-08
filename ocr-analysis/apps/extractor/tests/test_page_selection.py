import pytest
from helpers import (
    IDENTITY_LINES,
    PERIOD_LINES,
    SALARY_LINES,
    body_page,
    flow,
    page_text,
    signature_rows,
)

from contract_extractor.common.models import Level
from contract_extractor.extractor_method.pattern.filename import name_in_text
from contract_extractor.document.pages.page_selection import (
    SIGNATURE_THRESHOLD,
    candidate_pages,
    closing_zone,
    select_pages,
    signature_block,
)

EMPLOYEE = "Budi Santoso"


def identity_page(with_period: bool = True):
    lines = IDENTITY_LINES + (PERIOD_LINES if with_period else [])
    return body_page(1, lines)


def signature_page(number: int, block_top: float, employee: str = EMPLOYEE):
    body = flow(["Pasal 10 Penutup", *(["Hal-hal yang belum diatur akan ditetapkan kemudian oleh Pihak Pertama dan Pihak Kedua."] * 6)])
    return page_text(number, body + signature_rows(block_top, employee))


def five_page_contract(block_top: float = 350.0):
    return [
        identity_page(),
        body_page(2, SALARY_LINES),
        body_page(3),
        body_page(4),
        signature_page(5, block_top),
    ]


def test_mid_page_signature_block_like_vendor_a():
    selection = select_pages(five_page_contract(block_top=350.0), 5, EMPLOYEE)
    assert selection.p_id == [1]
    assert selection.p_ttd == 5
    assert not selection.ambiguous
    assert selection.level is Level.GREEN


def test_lower_signature_block_like_vendor_b():
    assert select_pages(five_page_contract(block_top=510.0), 5, EMPLOYEE).p_ttd == 5


def test_party_labels_repeated_in_body_pages_do_not_win_signature():
    selection = select_pages(five_page_contract(), 5, EMPLOYEE)
    for page in (1, 2, 3, 4):
        assert selection.scores[page].signature < SIGNATURE_THRESHOLD


def test_opening_dibuat_dan_ditandatangani_does_not_open_a_closing_zone_on_page_one():
    page = identity_page()
    zone = closing_zone(page)
    assert all(line.top > 600 for line in zone)


def _closing_page(number: int, closing: list[str], footer: str | None = None):
    """A last page with a multi-line closing paragraph, a block without place/date, and optionally a footer."""
    body = flow(["Pasal 10 Penutup", *(["Hal-hal yang belum diatur akan ditetapkan kemudian oleh Pihak Pertama dan Pihak Kedua."] * 6), *closing])
    block = [(470.0, 80.0, "PIHAK PERTAMA"), (470.0, 380.0, "PIHAK KEDUA"), (550.0, 80.0, "Andi Wijaya"), (550.0, 380.0, EMPLOYEE)]
    rows = body + block + ([(745.0, 50.0, footer)] if footer else [])
    return page_text(number, rows)


# Closing paragraphs worded like the real samples: no "demikian perjanjian", several lines long.
SIGNED_IN_SOUND_MIND = [
    "Perjanjian ini dibuat dalam dua salinan yang masing-masing mempunyai kekuatan hukum yang sama.",
    "Para pihak menandatangani perjanjian ini dalam keadaan sadar dan tanpa paksaan dari pihak manapun",
    "serta telah membaca dan memahami seluruh isi perjanjian ini sebelum dibubuhi tanda tangan para pihak.",
]
RUNNING_FOOTER = "PT Contoh Sejahtera Gedung Contoh Lantai 5 Jalan Contoh Raya Nomor 1 Jakarta Indonesia"


def test_multi_line_closing_paragraph_without_demikian_finds_the_signature_page():
    pages = [identity_page(), body_page(2, SALARY_LINES), body_page(3), body_page(4), _closing_page(5, SIGNED_IN_SOUND_MIND)]
    assert select_pages(pages, 5, None).p_ttd == 5


def test_running_footer_does_not_push_the_closing_zone_off_the_page():
    page = _closing_page(5, SIGNED_IN_SOUND_MIND, footer=RUNNING_FOOTER)
    zone = closing_zone(page)
    assert zone and zone[0].text.startswith("Para pihak menandatangani")
    assert all(line.top < 745.0 for line in zone)
    pages = [identity_page(), body_page(2, SALARY_LINES), body_page(3), body_page(4), page]
    assert select_pages(pages, 5, None).p_ttd == 5


def test_signature_block_leaves_out_the_closing_paragraph():
    block = signature_block(_closing_page(5, SIGNED_IN_SOUND_MIND, footer=RUNNING_FOOTER))
    assert [line.text for line in block] == ["PIHAK PERTAMA PIHAK KEDUA", f"Andi Wijaya {EMPLOYEE}"]


def test_two_column_label_line_right_under_the_closing_paragraph_stays_in_the_block():
    # Vendor 1: the party labels follow the last paragraph line at normal line spacing.
    rows = flow(["Pasal 10 Penutup", *SIGNED_IN_SOUND_MIND])
    label_y = rows[-1][0] + 14.0
    rows += [(label_y, 80.0, "PIHAK PERTAMA"), (label_y, 380.0, "PIHAK KEDUA"), (label_y + 90.0, 380.0, EMPLOYEE)]
    block = signature_block(page_text(5, rows))
    assert [line.text for line in block] == ["PIHAK PERTAMA PIHAK KEDUA", EMPLOYEE]


def test_long_two_column_name_line_is_not_taken_for_a_paragraph():
    # Both printed names on one line make six words spanning the page, like a paragraph line; the gap between the
    # columns shows it is not one, so the employee name stays in the block for the signature anchor.
    rows = flow(["Pasal 10 Penutup", *SIGNED_IN_SOUND_MIND])
    top = rows[-1][0] + 40.0
    rows += [
        (top, 80.0, "PERUSAHAAN,"),
        (top, 380.0, "Yang menerima,"),
        (top + 90.0, 80.0, "Gatra Karta"),
        (top + 90.0, 380.0, "Abd. Karim Pratama Tarihoran"),
        (top + 104.0, 380.0, "Tenaga Kerja"),
    ]
    block = [line.text for line in signature_block(page_text(5, rows))]
    assert "Gatra Karta Abd. Karim Pratama Tarihoran" in block
    assert block[-1] == "Tenaga Kerja"


def test_signing_word_in_the_middle_of_a_body_page_opens_no_zone():
    page = body_page(3, ["Dokumen yang telah ditandatangani para pihak berlaku sebagai bukti yang sah bagi Pihak Pertama dan Pihak Kedua."])
    assert all(line.top > 600 for line in closing_zone(page))


def test_period_continuing_on_page_two_becomes_second_p_id():
    pages = [identity_page(with_period=False), body_page(2, PERIOD_LINES), body_page(3), signature_page(4, 350.0)]
    assert select_pages(pages, 4, EMPLOYEE).p_id == [1, 2]


def test_signature_found_without_filename_name():
    assert select_pages(five_page_contract(), 5, None).p_ttd == 5


def test_short_identity_page_is_not_taken_as_signature_page():
    short_identity = page_text(
        1,
        flow(
            [
                "PERJANJIAN KERJA WAKTU TERTENTU",
                "Pada hari ini telah disepakati perjanjian kerja oleh dan antara para pihak berikut ini:",
                "Nama : Budi Santoso",
                "NIK : 3171012345670001",
                "PIHAK KEDUA",
                "Nama : Andi Wijaya",
                "PIHAK PERTAMA",
            ]
        ),
    )
    selection = select_pages([short_identity], 2, EMPLOYEE)  # page 2 is a scan without text yet
    assert selection.p_id == [1]
    assert selection.p_ttd is None
    assert selection.ambiguous
    assert selection.ttd_guess is None  # the identity page is never the guess in a contract of several pages


def test_signature_page_that_lost_the_employee_column_is_only_a_guess():
    # A phone photo whose OCR lost the right-hand column: no PIHAK KEDUA, no employee name, so no role pair.
    rows = [row for row in signature_rows(350.0) if row[1] < 300]
    body = flow(["Pasal 10 Penutup", *(["Hal-hal yang belum diatur akan ditetapkan kemudian oleh para pihak."] * 6)])
    pages = [identity_page(), body_page(2), page_text(3, body + rows)]
    selection = select_pages(pages, 3, EMPLOYEE)
    assert 0 < selection.scores[3].signature < SIGNATURE_THRESHOLD
    assert (selection.p_ttd, selection.ttd_guess, selection.level) == (None, 3, Level.RED)
    assert select_pages(five_page_contract(), 5, EMPLOYEE).ttd_guess is None  # found: no guess


def test_one_page_contract_uses_the_same_page_for_both():
    rows = flow(IDENTITY_LINES + PERIOD_LINES) + signature_rows(560.0)
    selection = select_pages([page_text(1, rows)], 1, EMPLOYEE)
    assert selection.p_id == [1]
    assert selection.p_ttd == 1


def test_no_text_at_all_is_ambiguous_and_red():
    selection = select_pages([], 5, EMPLOYEE)
    assert (selection.p_id, selection.p_ttd, selection.level) == ([], None, Level.RED)


def test_sensitive_pages_are_flagged():
    selection = select_pages(five_page_contract(), 5, EMPLOYEE)
    assert 2 in selection.sensitive_pages  # gaji, tunjangan, Rp
    assert 1 in selection.sensitive_pages  # NIK digit run
    assert 3 not in selection.sensitive_pages


@pytest.mark.parametrize(
    ("n_pages", "pages"),
    [(1, [1]), (4, [1, 2, 3, 4]), (8, [1, 2, 3, 4, 5, 6, 7, 8]), (12, [1, 2, 3, 8, 9, 10, 11, 12])],
)
def test_candidate_pages(n_pages, pages):
    assert candidate_pages(n_pages) == pages


def test_name_match_rejects_a_bare_first_name_and_other_people():
    name = "budi santoso"
    assert name_in_text(name, "Andi Wijaya Budi Santoso")
    assert name_in_text(name, "BUDI SANTOSO, S.Kom")
    assert not name_in_text(name, "Budi")
    assert not name_in_text(name, "Budi Hartono")
