import pytest

from contract_extractor.common.models import Decision, Level, SignatureStatus
from contract_extractor.extractor_method.pattern.filename import FLAG_NONSTANDARD, FLAG_UNPARSED, Match
from contract_extractor.scoring import (
    COMPONENT_FILENAME,
    FLAG_FILENAME_MISMATCH,
    decide,
    filename_level,
    ocr_level,
    page_selection_level,
    signature_level,
)

G, Y, R = Level.GREEN, Level.YELLOW, Level.RED


@pytest.mark.parametrize(
    ("levels", "decision"),
    [
        ({"a": G, "b": G}, Decision.AUTO),
        ({"a": G, "b": Y}, Decision.REVIEW),
        ({"a": Y, "b": R}, Decision.REVIEW_PRIORITY),
        ({}, Decision.REVIEW_PRIORITY),
    ],
)
def test_decide(levels, decision):
    assert decide(levels)[0] is decision


def test_filename_red_with_everything_else_green_is_flagged_as_mismatch():
    decision, flags = decide({"ocr": G, "ekstraksi": G, "ttd": G, COMPONENT_FILENAME: R})
    assert decision is Decision.REVIEW_PRIORITY
    assert flags == [FLAG_FILENAME_MISMATCH]


def test_filename_red_alongside_other_problems_is_not_labelled_mismatch():
    assert decide({"ocr": Y, COMPONENT_FILENAME: R})[1] == []


@pytest.mark.parametrize(
    ("name", "period", "flags", "level"),
    [
        (Match.COCOK, Match.COCOK, (), G),
        (Match.MIRIP, Match.COCOK, (), Y),
        (Match.COCOK, None, (FLAG_NONSTANDARD,), Y),
        (Match.COCOK, None, (FLAG_UNPARSED,), Y),
        (Match.TIDAK_COCOK, Match.COCOK, (), R),
        (Match.COCOK, Match.TIDAK_COCOK, (), R),
    ],
)
def test_filename_level(name, period, flags, level):
    assert filename_level(name, period, flags) is level


@pytest.mark.parametrize(("conf", "level"), [(0.85, G), (0.84, Y), (0.70, Y), (0.69, R)])
def test_ocr_level(conf, level):
    assert ocr_level(conf) is level


def test_signature_level():
    assert signature_level(SignatureStatus.SIGNED, anchor_found=True, clear_margin=True) is G
    assert signature_level(SignatureStatus.UNSIGNED, anchor_found=True, clear_margin=True) is G
    assert signature_level(SignatureStatus.UNCERTAIN, anchor_found=True, clear_margin=True) is Y
    assert signature_level(SignatureStatus.SIGNED, anchor_found=True, clear_margin=False) is Y
    assert signature_level(SignatureStatus.SIGNED, anchor_found=False, clear_margin=True) is R


def test_page_selection_level():
    assert page_selection_level(10, threshold=5, margin=3) is G
    assert page_selection_level(6, threshold=5, margin=3) is Y
    assert page_selection_level(4, threshold=5, margin=3) is R
