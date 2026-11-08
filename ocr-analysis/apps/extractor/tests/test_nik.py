import contextlib
import dataclasses
import logging

import numpy as np
import pytest
from helpers import EMPLOYEE_NIK, IDENTITY_LINES, PERIOD_LINES, flow, page_text, signed_contract_pdf

from contract_extractor.common.config import Settings
from contract_extractor.common.layout import PageText
from contract_extractor import pipeline
from contract_extractor.common.models import FileResult, Level, NikCheck, NikLog, NikStatus
from contract_extractor.pipeline import (
    REASON_NIK_BIRTH_DATE_MISMATCH,
    REASON_NIK_DIGITS_REPAIRED,
    REASON_NIK_FROM_OCR,
    REASON_NIK_INVALID,
    process_file,
)
from contract_extractor.common.models import TokenUsage
from contract_extractor.extractor_method.llm.base import ExtractorError, ImageAnswer, ImageTask
from contract_extractor.extractor_method.pattern.nik import check_nik, decode, find_nik, mask_nik, nik_spot
from contract_extractor.scoring import nik_level

# Fictitious NIKs in the KTP layout. Regency and district 99 exist in no province, so no real person holds them.
WOMAN_NIK = "3299995504090002"  # born 15 April 2009: day 15 + 40
OTHER_NIK = "3599990202850003"  # a man born 2 February 1985


def pages(*lines: str) -> list[PageText]:
    return [page_text(1, flow(list(lines)))]


def scanned(page: PageText, conf: float) -> PageText:
    words = tuple(dataclasses.replace(w, conf=conf) for w in page.words)
    return dataclasses.replace(page, words=words, source="ocr:tesseract")


def test_key_value_block_gives_the_nik_and_both_checks_agree():
    read = find_nik(pages(*IDENTITY_LINES, *PERIOD_LINES))
    assert read.nik == EMPLOYEE_NIK
    assert read.log == NikLog(status=NikStatus.FOUND, tanggal_lahir=NikCheck.MATCH, jenis_kelamin=NikCheck.MATCH)
    assert read.confidence == 1.0


@pytest.mark.parametrize("label", ["NIK", "No. KTP", "Nomor KTP", "No. Induk Kependudukan", "Nomor Induk Kependudukan"])
def test_every_label_form_is_read(label):
    assert find_nik(pages("Nama : Budi Santoso", f"{label} : {EMPLOYEE_NIK}")).nik == EMPLOYEE_NIK


def test_nik_inside_a_sentence_is_checked_against_the_sentence():
    read = find_nik(
        pages(
            "2. Siti Aminah, perempuan, lahir di Kota Administrasi Jakarta Selatan pada tanggal 15 April 2009,",
            f"pemegang NIK {WOMAN_NIK}, bertempat tinggal di Jl. Mawar No. 1, Bandung.",
        )
    )
    assert read.nik == WOMAN_NIK
    assert (read.log.tanggal_lahir, read.log.jenis_kelamin) == (NikCheck.MATCH, NikCheck.MATCH)


def test_value_wrapped_onto_the_next_line_is_read():
    read = find_nik(pages("Siti Aminah, perempuan, pemegang NIK", f"| {WOMAN_NIK}, bertempat tinggal di Bandung."))
    assert read.nik == WOMAN_NIK


@pytest.mark.parametrize(
    "value, picked",
    [
        ("3199 9901 0190 0001", False),  # spaced in groups
        ("3199.9901.0190.0001", False),
        ("1 3199990101900001", False),  # the colon read as `1`, still a separate token: 16 digits stand alone
        ("13199990101900001", True),  # the colon read as `1` and joined to the number
        ("3199990IO1900001", False),  # letters for digits
    ],
)
def test_ocr_damage_around_the_digits_is_repaired(value, picked):
    read = find_nik(pages("Nama : Budi Santoso", f"NIK {value}"))
    # 17 digits leave a choice of window, which the checks cannot fully confirm (see the crop tests below).
    assert (read.nik, read.log.deret_panjang) == (EMPLOYEE_NIK, picked)


def test_number_that_is_not_a_valid_nik_is_reported_invalid():
    read = find_nik(pages("Nama : Budi Santoso", "NIK : 3171012345670001"))  # month 45
    assert (read.nik, read.log.status, read.confidence) == (None, NikStatus.INVALID, None)


def test_no_label_means_no_nik_even_for_a_valid_number_elsewhere():
    read = find_nik(pages("Nama : Budi Santoso", f"No. KK : {EMPLOYEE_NIK}", "Alamat : Jl. Mawar"))
    assert (read.nik, read.log.status) == (None, NikStatus.MISSING)


def test_a_line_of_border_scraps_between_the_label_and_the_number_is_skipped():
    page = pages(
        "Siti Aminah, perempuan, lahir di Bandung pada tanggal 15 April 2009, pemegang NIK",
        "|",
        "| |",
        f"{WOMAN_NIK}, bertempat tinggal di Jl. Mawar No. 1, Bandung.",
    )
    read = find_nik(page)
    assert (read.nik, read.log.tanggal_lahir, read.log.jenis_kelamin) == (WOMAN_NIK, NikCheck.MATCH, NikCheck.MATCH)
    spot, number = nik_spot(page), next(w for w in page[0].words if w.text.startswith(WOMAN_NIK))
    assert spot is not None and spot.box[1] * page[0].height <= number.bbox[1] < number.bbox[3] <= spot.box[3] * page[0].height


def test_number_further_down_than_the_next_line_is_not_taken():
    read = find_nik(pages("NIK :", "Jenis Kelamin : Laki-laki", f"No. KK : {EMPLOYEE_NIK}"))
    assert read.log.status is NikStatus.MISSING


def test_two_different_niks_conflict_and_neither_is_reported():
    read = find_nik(pages(f"NIK : {EMPLOYEE_NIK}", "Nama : Andi Wijaya", f"NIK : {OTHER_NIK}"))
    assert (read.nik, read.log.status) == (None, NikStatus.CONFLICT)


def test_the_same_nik_printed_twice_is_not_a_conflict():
    read = find_nik(pages(f"NIK : {EMPLOYEE_NIK}", f"pemegang NIK {EMPLOYEE_NIK}, bertempat tinggal di Bandung"))
    assert read.log.status is NikStatus.FOUND


def test_printed_birth_date_or_gender_that_disagrees_is_a_mismatch():
    birth = find_nik(pages(f"NIK : {EMPLOYEE_NIK}", "Tempat/Tanggal Lahir : Bandung, 2 Januari 1990"))
    gender = find_nik(pages(f"NIK : {EMPLOYEE_NIK}", "Jenis Kelamin : Perempuan"))
    assert birth.log.tanggal_lahir is NikCheck.MISMATCH and birth.log.jenis_kelamin is NikCheck.ABSENT
    assert gender.log.jenis_kelamin is NikCheck.MISMATCH and gender.log.tanggal_lahir is NikCheck.ABSENT


def test_confidence_comes_from_the_ocr_words_of_the_nik():
    page = scanned(pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}")[0], conf=0.62)
    assert find_nik([page]).confidence == 0.62


@pytest.mark.parametrize(
    "nik, valid",
    [
        (EMPLOYEE_NIK, True),
        (WOMAN_NIK, True),
        ("9999990101900001", False),  # no such province
        ("3100990101900001", False),  # regency 00
        ("3199990101900000", False),  # serial 0000
        ("3199993102900001", False),  # 31 February
        ("3199993502900001", False),  # day 35: neither a man's nor a woman's day
        ("319999010190001", False),  # 15 digits
    ],
)
def test_structure_check(nik, valid):
    assert (decode(nik) is not None) is valid


def test_masked_form_keeps_only_the_last_four_digits():
    assert mask_nik(EMPLOYEE_NIK) == "************0001"


@pytest.mark.parametrize(
    "log, text_layer, level",
    [
        (NikLog(status=NikStatus.FOUND), True, Level.GREEN),
        (NikLog(status=NikStatus.FOUND, tanggal_lahir=NikCheck.MATCH), False, Level.YELLOW),  # OCR: a person checks
        (NikLog(status=NikStatus.MISSING), True, Level.YELLOW),
        (NikLog(status=NikStatus.INVALID), True, Level.YELLOW),
        (NikLog(status=NikStatus.CONFLICT), True, Level.RED),
        (NikLog(status=NikStatus.FOUND, tanggal_lahir=NikCheck.MISMATCH), True, Level.RED),
        (NikLog(status=NikStatus.FOUND, jenis_kelamin=NikCheck.MISMATCH), True, Level.RED),
    ],
)
def test_level(log, text_layer, level):
    assert nik_level(log, text_layer) is level


def test_pipeline_reports_the_nik_but_never_logs_it(tmp_path, caplog):
    pdf = signed_contract_pdf(tmp_path / "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf")
    with caplog.at_level(logging.DEBUG):
        result = process_file(pdf, Settings())
    assert result.nik == EMPLOYEE_NIK
    assert EMPLOYEE_NIK not in caplog.text
    assert EMPLOYEE_NIK not in result.log.model_dump_json()
    assert not {REASON_NIK_INVALID, REASON_NIK_FROM_OCR, REASON_NIK_BIRTH_DATE_MISMATCH} & set(result.log.alasan)


@pytest.mark.parametrize(
    "lines, text_layer, reasons",
    [
        (["NIK : 3171012345670001"], True, {REASON_NIK_INVALID}),
        ([f"NIK : {EMPLOYEE_NIK}", "Tempat/Tanggal Lahir : Bandung, 2 Januari 1990"], True, {REASON_NIK_BIRTH_DATE_MISMATCH}),
        ([f"NIK : {EMPLOYEE_NIK}"], False, {REASON_NIK_FROM_OCR}),
        ([f"NIK : {EMPLOYEE_NIK}"], True, set()),
    ],
)
def test_pipeline_explains_every_nik_that_needs_a_check(lines, text_layer, reasons):
    page = pages(*lines)[0]
    result = FileResult(file="x.pdf")
    pipeline._read_nik(result, [page if text_layer else scanned(page, conf=0.9)])
    assert {r for r in result.log.alasan if r.startswith("nik_")} == reasons


def test_without_identity_pages_the_nik_adds_no_reason_of_its_own():
    result = FileResult(file="x.pdf")
    assert pipeline._read_nik(result, []) is Level.YELLOW
    assert result.log.alasan == [] and result.log.nik == NikLog()


# --- the crop of the NIK line, for a second reading by the extractor ---------------------------------------------------

BIRTH_AND_GENDER = ("Tempat/Tanggal Lahir : Bandung, 1 Januari 1990", "Jenis Kelamin : Laki-laki")


def test_the_spot_is_the_number_alone_on_its_page():
    read = pages("Nama : Budi Santoso", f"NIK : {EMPLOYEE_NIK}", "Alamat : Jl. Mawar")
    spot = nik_spot(read)
    assert spot is not None and spot.page == 0
    x0, top, x1, bottom = spot.box
    number = next(w for w in read[0].words if w.text == EMPLOYEE_NIK)
    assert x0 * read[0].width < number.bbox[0] and x1 * read[0].width > number.bbox[2]
    label = next(w for w in read[0].words if w.text == "NIK")
    assert x0 * read[0].width > label.bbox[0]  # the number, not the label or the line
    address = next(w for w in read[0].words if w.text == "Alamat")
    assert bottom * read[0].height < address.bbox[1] + 1  # not the next line


def test_there_is_no_spot_for_two_people_or_no_label():
    assert nik_spot(pages(f"NIK : {EMPLOYEE_NIK}", "Nama : Andi Wijaya", f"NIK : {OTHER_NIK}")) is None
    assert nik_spot(pages("Nama : Budi Santoso", "Alamat : Jl. Mawar")) is None
    assert nik_spot(pages(f"NIK : {EMPLOYEE_NIK}", f"pemegang NIK {EMPLOYEE_NIK}")) is not None  # one number twice


def test_an_unreadable_number_after_the_label_is_still_a_spot():
    assert nik_spot(pages("Nama : Budi Santoso", "NIK : 3171012345670001")) is not None  # month 45


def test_a_crop_reading_gets_the_same_checks_as_a_local_one():
    read = pages(f"NIK : {EMPLOYEE_NIK}", *BIRTH_AND_GENDER)
    spot = nik_spot(read)
    assert check_nik(EMPLOYEE_NIK, read, spot) == NikLog(
        status=NikStatus.FOUND, tanggal_lahir=NikCheck.MATCH, jenis_kelamin=NikCheck.MATCH
    )
    assert check_nik(WOMAN_NIK, read, spot).jenis_kelamin is NikCheck.MISMATCH
    assert check_nik("3171012345670001", read, spot).status is NikStatus.INVALID


class CropReader:
    name = "fake-llm"

    def __init__(self, reply):
        self.reply, self.crops = reply, []

    def read_image(self, task, png):
        self.crops.append(task)
        if isinstance(reply := self.reply, ExtractorError):
            raise reply
        return reply


def _nik_crop(monkeypatch, read, reply):
    """`_read_nik_crop` on these P_id pages, with a blank page image instead of a rendered PDF."""
    monkeypatch.setattr(pipeline, "PageRenderer", lambda data, turns: contextlib.nullcontext())
    monkeypatch.setattr(pipeline, "_page_image", lambda renderer, page, ocr: np.full((1100, 850), 255, np.uint8))
    result = FileResult(file="x.pdf")
    level = pipeline._read_nik(result, read)
    reader = CropReader(reply)
    pages_read = pipeline._PagesRead({}, read, True)
    return pipeline._read_nik_crop(b"", result, pages_read, reader, None, level), result, reader


def test_a_text_layer_nik_is_not_sent(monkeypatch):
    level, result, reader = _nik_crop(monkeypatch, pages(f"NIK : {EMPLOYEE_NIK}"), ImageAnswer(nik=EMPLOYEE_NIK))
    assert reader.crops == [] and result.log.nik.llm is None and level is Level.GREEN


def test_the_same_digits_from_the_crop_make_an_ocr_nik_green(monkeypatch):
    read = [scanned(pages(f"NIK : {EMPLOYEE_NIK}")[0], 0.9)]
    level, result, reader = _nik_crop(monkeypatch, read, ImageAnswer(nik=EMPLOYEE_NIK, usage=TokenUsage(masuk=5)))
    assert reader.crops == [ImageTask.NIK] and result.log.nik.llm == "sama" and level is Level.GREEN
    assert REASON_NIK_FROM_OCR not in result.log.alasan
    assert result.log.panggilan_llm == ["nik"] and result.log.token_llm == TokenUsage(masuk=5)
    assert result.log.komponen_halaman == {1: ["fake-llm:potongan_nik"]}


def test_a_nik_picked_from_a_longer_run_goes_to_a_crop_even_when_both_checks_agree(monkeypatch):
    # The region code (digits 1 to 6) is outside both checks: a wrong window that keeps digits 7 to 12 still passes.
    read = [scanned(pages(f"NIK : 1{EMPLOYEE_NIK}", *BIRTH_AND_GENDER)[0], 0.9)]
    result = FileResult(file="Budi Santoso - 12 bulan.pdf")
    assert pipeline._read_nik(result, read) is Level.YELLOW
    assert result.log.nik.tanggal_lahir is NikCheck.MATCH and REASON_NIK_DIGITS_REPAIRED in result.log.alasan
    level, result, reader = _nik_crop(monkeypatch, read, ImageAnswer(nik=EMPLOYEE_NIK))
    assert reader.crops == [ImageTask.NIK] and (result.log.nik.llm, level) == ("sama", Level.GREEN)
    assert REASON_NIK_DIGITS_REPAIRED not in result.log.alasan  # two readers agree on every digit


def test_an_unreadable_local_nik_is_replaced_by_a_checked_crop_reading(monkeypatch):
    read = pages("NIK : 3171012345670001", *BIRTH_AND_GENDER)
    level, result, _ = _nik_crop(monkeypatch, read, ImageAnswer(nik=EMPLOYEE_NIK, confidence=0.93))
    assert (result.nik, result.log.nik.llm, level) == (EMPLOYEE_NIK, "dipakai", Level.GREEN)
    assert result.log.alasan == ["nik_from_llm"] and result.confidence["nik"] == 0.93

    unchecked = pages("NIK : 3171012345670001")  # nothing printed to confirm it: used, but a person looks
    level, result, _ = _nik_crop(monkeypatch, unchecked, ImageAnswer(nik=EMPLOYEE_NIK))
    assert (result.nik, result.log.nik.llm, level) == (EMPLOYEE_NIK, "dipakai", Level.YELLOW)


def test_a_crop_reading_the_printed_details_contradict_is_not_used(monkeypatch):
    read = pages("NIK : 3171012345670001", *BIRTH_AND_GENDER)
    level, result, _ = _nik_crop(monkeypatch, read, ImageAnswer(nik=WOMAN_NIK))
    assert (result.nik, result.log.nik.llm, level) == (None, "beda", Level.YELLOW)
    assert {REASON_NIK_INVALID, "nik_llm_differs"} <= set(result.log.alasan)


def test_a_contradicted_local_nik_gives_way_only_to_a_confirmed_one(monkeypatch):
    read = pages(f"NIK : {OTHER_NIK}", *BIRTH_AND_GENDER)  # born 1985: the printed 1990 contradicts it
    level, result, _ = _nik_crop(monkeypatch, read, ImageAnswer(nik=EMPLOYEE_NIK))
    assert (result.nik, result.log.nik.llm, level) == (EMPLOYEE_NIK, "dipakai", Level.GREEN)
    assert REASON_NIK_BIRTH_DATE_MISMATCH not in result.log.alasan

    level, result, _ = _nik_crop(monkeypatch, read, ImageAnswer(nik=OTHER_NIK))  # the same digits: still contradicted
    assert (result.nik, result.log.nik.llm, level) == (OTHER_NIK, "sama", Level.RED)


def test_no_reading_or_a_failed_call_keeps_the_local_answer(monkeypatch):
    read = pages("NIK : 3171012345670001")
    level, result, _ = _nik_crop(monkeypatch, read, ImageAnswer())
    assert (result.nik, result.log.nik.llm, level) == (None, "tidak_terbaca", Level.YELLOW)

    level, result, _ = _nik_crop(monkeypatch, read, ExtractorError("timeout"))
    assert (result.log.nik.llm, level) == (None, Level.YELLOW) and "extractor_error:timeout" in result.log.alasan
    assert result.log.panggilan_llm == ["nik"]
