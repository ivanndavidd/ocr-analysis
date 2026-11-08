"""The vendor folder's name looked up in the document text. Fictional company names only."""

import pytest

from contract_extractor.extractor_method.pattern.vendor import normalize_vendor, vendor_in_text


def test_legal_form_punctuation_and_case_are_ignored():
    assert normalize_vendor("PT. Sarana Perkasa, Tbk") == "sarana perkasa"
    assert normalize_vendor("CV Maju-Jaya") == "maju jaya"


@pytest.mark.parametrize(
    "vendor, text, found",
    [
        ("PT. Sarana Perkasa", "Dalam hal ini bertindak untuk dan atas nama PT SARANA PERKASA, selanjutnya", True),
        ("PT Sarana Perkasa", "PT. Sarana Perkasa Indonesia", True),  # a longer name read from the document
        ("PT Bina Semesta", "atas nama PT Bina Semesla", True),  # one wrong letter
        ("PT Bina Semesta", "atas nama PTBINASEMESTA,", True),  # OCR joined the words
        ("PT Sinar Sentosa Resources", "PT Sinar Sentosa Re sources", True),  # and split one
        ("PT. Sarana Perkasa", "atas nama PT Sarana Ekasarana", False),  # another vendor sharing a word
        ("PT Bumi Dinamika", "atas nama PT Tri Dinamika", False),
        ("PT Bina Semesta", "Nama : Budi Santoso", False),
        ("PT", "PT Bina Semesta", False),  # nothing left to look for
        ("PT Bina Semesta", "", False),
    ],
)
def test_vendor_in_text(vendor, text, found):
    assert vendor_in_text(vendor, text) is found
