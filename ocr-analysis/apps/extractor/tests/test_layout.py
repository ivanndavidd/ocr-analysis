from contract_extractor.layout import group_lines
from contract_extractor.models import Word


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
