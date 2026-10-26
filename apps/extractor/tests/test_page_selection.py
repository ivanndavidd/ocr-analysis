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

from contract_extractor.models import Level
from contract_extractor.stages.page_selection import (
    SIGNATURE_THRESHOLD,
    _name_in,
    candidate_pages,
    closing_zone,
    select_pages,
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
    assert _name_in(name, "Andi Wijaya Budi Santoso")
    assert _name_in(name, "BUDI SANTOSO, S.Kom")
    assert not _name_in(name, "Budi")
    assert not _name_in(name, "Budi Hartono")
