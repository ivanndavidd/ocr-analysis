from contract_extractor.common.layout import PageText, group_lines
from contract_extractor.common.models import Word


def _w(text, x, y, h=11.0):
    return Word(text=text, bbox=(x, y, x + 5.5 * len(text), y + h), conf=1.0)


def test_words_with_small_vertical_jitter_share_a_line_sorted_by_x():
    lines = group_lines([_w("Santoso", 90, 101), _w("Budi", 50, 100), _w("Nama", 10, 99.5)])
    assert [line.text for line in lines] == ["Nama Budi Santoso"]


def test_distinct_rows_become_separate_lines_top_to_bottom():
    lines = group_lines([_w("kedua", 50, 130), _w("pertama", 50, 100)])
    assert [line.text for line in lines] == ["pertama", "kedua"]
    assert lines[0].top == 100 and lines[1].bottom == 141


def test_two_columns_on_the_same_row_merge_into_one_line():
    lines = group_lines([_w("PIHAK", 380, 360), _w("KEDUA", 420, 360), _w("PIHAK", 80, 360), _w("PERTAMA", 120, 360)])
    assert [line.text for line in lines] == ["PIHAK PERTAMA PIHAK KEDUA"]


def test_empty_input():
    assert group_lines([]) == []


def test_lines_of_border_scraps_stay_in_the_layout_but_not_in_the_text():
    page = PageText(
        number=1,
        width=600.0,
        height=800.0,
        words=(_w("sampai", 50, 100), _w("|", 52, 115), _w("|", 90, 115), _w("dengan", 50, 130), _w("|", 40, 145)),
        source="ocr:tesseract",
    )
    assert [line.text for line in page.lines] == ["sampai", "| |", "dengan", "|"]
    assert [line.is_debris for line in page.lines] == [False, True, False, True]
    assert page.text == "sampai\ndengan"
