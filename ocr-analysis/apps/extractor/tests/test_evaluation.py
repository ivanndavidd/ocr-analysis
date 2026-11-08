"""Gate evaluation on synthetic labels and results. Names, dates and files here are fictional."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest
from helpers import llm_answer, signed_contract_pdf
from typer.testing import CliRunner

from contract_extractor.cli import app
from contract_extractor.common.config import Settings
from contract_extractor.evaluation import (
    LABEL_COLUMNS,
    EvaluationError,
    KelompokFilter,
    evaluate,
    format_report,
    leak_counts,
    write_detail,
)
from contract_extractor.output.export import write_json
from contract_extractor.extractor_method.llm.base import ExtractionResult
from contract_extractor.common.models import Decision, FileLog, FileResult, SignatureStatus
from contract_extractor.pipeline import FLAG_AUTO_DISABLED, process_path

TEMPLATE = Path(__file__).resolve().parents[1] / "label-template.csv"
START, END = date(2026, 1, 1), date(2026, 12, 31)


def label(file: str, **overrides: str) -> dict[str, str]:
    row = {
        "file": file,
        "kelompok": "kalibrasi",
        "vendor": "PT Vendor Satu",
        "jenis_pdf": "native",
        "nama_karyawan": "Budi Santoso",
        "tanggal_mulai": "2026-01-01",
        "tanggal_selesai": "2026-12-31",
        "status_ttd": "signed",
        "halaman_identitas": "1",
        "halaman_periode": "1",
        "halaman_ttd": "5",
        "kasus": "",
    }
    return {**row, **overrides}


def result(file: str, status=SignatureStatus.SIGNED, name="Budi Santoso", p_id=(1,), p_ttd=5, auto=True, **log) -> FileResult:
    return FileResult(
        file=file,
        nama_karyawan=name,
        tanggal_mulai=START,
        tanggal_selesai=END,
        status_ttd=status,
        keputusan=Decision.REVIEW if auto else Decision.REVIEW_PRIORITY,
        flags=[FLAG_AUTO_DISABLED] if auto else [],
        log=FileLog(p_id=list(p_id), p_ttd=p_ttd, **log),
    )


def write_labels(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def run_eval(tmp_path: Path, labels: list[dict[str, str]], results: list[FileResult], kelompok=KelompokFilter.SEMUA):
    write_json(results, tmp_path / "results.json")
    return evaluate(tmp_path / "results.json", write_labels(tmp_path / "labels.csv", labels), kelompok)


def metric(report, key):
    [found] = [m for m in report.metrics if m.key == key]
    return found


def test_template_header_is_the_label_schema():
    assert TEMPLATE.read_text(encoding="utf-8").strip().split(",") == list(LABEL_COLUMNS)


def test_metrics_follow_the_prd_definitions(tmp_path):
    labels = [
        label("a.pdf"),
        label("b.pdf", status_ttd="unsigned", halaman_periode="2"),
        label("c.pdf", status_ttd="unsigned", nama_karyawan="Sdr. Rina Wulandari, S.Kom"),
        label("d.pdf", status_ttd="unsigned"),
        label("e.pdf"),  # never processed
    ]
    results = [
        result("a.pdf"),
        result("b.pdf", status=SignatureStatus.UNCERTAIN, p_id=(1, 2), auto=False),  # uncertain is "not signed"
        result("c.pdf", status=SignatureStatus.SIGNED, name="RINA WULANDARI", p_ttd=4),  # false signed, wrong page
        result("d.pdf", status=SignatureStatus.UNSIGNED, name="Budi"),  # first name only is not a match
        result("z.pdf"),  # unlabeled, ignored
    ]
    report = run_eval(tmp_path, labels, results)

    assert (report.n_missing, report.n_unlabeled) == (1, 1)
    assert (metric(report, "page_selection").hit, metric(report, "page_selection").total) == (3, 5)  # a, b, d
    assert (metric(report, "nama").hit, metric(report, "nama").total) == (3, 5)  # titles and case are normalized
    assert (metric(report, "periode").hit, metric(report, "periode").total) == (4, 5)
    assert (metric(report, "recall_unsigned").hit, metric(report, "recall_unsigned").total) == (2, 3)
    assert (metric(report, "precision_signed").hit, metric(report, "precision_signed").total) == (1, 2)
    assert (metric(report, "auto_pass").hit, metric(report, "auto_pass").total) == (3, 5)
    assert (metric(report, "auto_error").hit, metric(report, "auto_error").total) == (2, 3)  # c and d are wrong


def test_targets_and_kill_lines(tmp_path):
    labels = [label(f"{i}.pdf", status_ttd="unsigned") for i in range(20)]
    results = [result(f"{i}.pdf", status=SignatureStatus.UNSIGNED, p_ttd=5 if i else 4) for i in range(20)]
    page = metric(run_eval(tmp_path, labels, results), "page_selection")

    assert page.value == 0.95
    assert [t.check(page.value) for t in page.targets] == [True, True, True]  # 19/20 meets 95% exactly
    unsigned = metric(run_eval(tmp_path, labels, results[:19] + [result("19.pdf")]), "recall_unsigned")
    assert [t.check(unsigned.value) for t in unsigned.targets] == [False, False, True]  # 95% is below 98%, at kill


def test_leaks_are_pages_that_left_beyond_the_masked_p_id(tmp_path):
    clean = result("a.pdf", komponen_halaman={1: ["text_layer", "claude:masked"], 5: ["text_layer", "signature@300dpi"]})
    leaky = result(
        "b.pdf",
        komponen_halaman={
            1: ["tesseract@150dpi", "tesseract@300dpi", "claude:masked"],
            2: ["claude:masked"],  # outside P_id
            5: ["tesseract@300dpi", "cloud-ocr@300dpi"],  # the signature page must never leave
        },
    )
    assert leak_counts([clean, leaky]) == (2, 5)
    report = run_eval(tmp_path, [label("a.pdf"), label("b.pdf")], [clean, leaky])
    leak = metric(report, "leak")
    assert (leak.hit, leak.total, leak.targets[0].check(leak.value)) == (2, 5, False)


def test_report_prints_on_a_windows_console(tmp_path):
    report = run_eval(tmp_path, [label("a.pdf"), label("b.pdf", status_ttd="unsigned")], [result("a.pdf")])
    format_report(report).encode("cp1252")  # the default Windows console encoding has no ≥ or –


def test_kelompok_keeps_uat_apart(tmp_path):
    labels = [label("a.pdf"), label("b.pdf", kelompok="uat")]
    report = run_eval(tmp_path, labels, [result("a.pdf"), result("b.pdf")], KelompokFilter.UAT)
    assert [c.label.file for c in report.checks] == ["b.pdf"]
    assert report.n_unlabeled == 0  # a.pdf is labeled, just in the other group


def test_composition_reports_what_the_test_set_lacks(tmp_path):
    labels = [label(f"{i}.pdf", kasus="stempel;nama_file_salah" if i == 0 else "") for i in range(3)]
    report = run_eval(tmp_path, labels, [])
    status = {c.label: c.passed for c in report.composition}
    assert status["Jumlah file"] is False and status["Vendor PT Vendor Satu"] is False
    assert (status["Jenis native"], status["Jenis hybrid"]) == (False, False)
    assert (status["Status signed"], status["Status unsigned"]) == (True, False)
    assert (status["Kasus stempel"], status["Kasus tulisan_tangan"], status["Kasus nama_file_salah"]) == (True, False, False)
    assert status["Kasus lampiran"] is None


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([label("a.pdf", tanggal_mulai="31-12-2026")], "baris 2: kolom tanggal_mulai tidak valid"),
        ([label("a.pdf"), label("a.pdf")], "baris 3: file yang sama sudah dilabeli"),
        ([label("a.pdf", status_ttd="uncertain")], "baris 2: kolom status_ttd tidak valid"),
        ([label("a.pdf", kasus="stempel;coretan")], "baris 2: kolom kasus tidak valid"),
        ([], "file label tidak berisi baris"),
    ],
)
def test_label_errors_name_rows_and_columns_but_never_values(tmp_path, rows, message):
    with pytest.raises(EvaluationError) as caught:
        run_eval(tmp_path, rows, [])
    assert str(caught.value) == message
    assert "Budi" not in str(caught.value) and "31-12" not in str(caught.value)


def test_missing_label_column_is_reported(tmp_path):
    (tmp_path / "labels.csv").write_text("file,vendor\na.pdf,PT Vendor Satu\n", encoding="utf-8")
    write_json([], tmp_path / "results.json")
    with pytest.raises(EvaluationError, match="kolom label tidak ada: kelompok, jenis_pdf"):
        evaluate(tmp_path / "results.json", tmp_path / "labels.csv")


# Fictitious NIKs: regency and district 99 exist nowhere.
NIK_A, NIK_B = "3199990101900001", "3299995504090002"


def run_nik_eval(tmp_path: Path, labels: list[dict[str, str]], results: list[FileResult], full_nik: bool = True):
    write_json(results, tmp_path / "results.json", full_nik)
    with (tmp_path / "labels.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*LABEL_COLUMNS, "nik"])
        writer.writeheader()
        writer.writerows({"nik": "", **row} for row in labels)
    return evaluate(tmp_path / "results.json", tmp_path / "labels.csv")


def with_nik(r: FileResult, nik: str | None) -> FileResult:
    return r.model_copy(update={"nik": nik})


def test_without_a_nik_column_nik_is_not_measured(tmp_path):
    report = run_eval(tmp_path, [label("a.pdf")], [with_nik(result("a.pdf"), NIK_A)])
    assert not [m for m in report.metrics if m.key.startswith("nik")]
    assert report.by_kind[0].nik is None and "NIK" not in format_report(report)


def test_nik_accuracy_counts_a_wrong_nik_apart_from_a_missing_one(tmp_path):
    labels = [
        label("a.pdf", nik=NIK_A),
        label("b.pdf", nik=NIK_A),
        label("c.pdf", nik=NIK_A),
        label("d.pdf"),  # the contract prints no NIK
        label("e.pdf"),
    ]
    results = [
        with_nik(result("a.pdf"), NIK_A),
        with_nik(result("b.pdf"), NIK_B),  # wrong: worse than none
        with_nik(result("c.pdf"), None),
        with_nik(result("d.pdf"), None),  # correctly none
        with_nik(result("e.pdf"), NIK_B),  # a NIK the contract does not have
    ]
    report = run_nik_eval(tmp_path, labels, results)

    assert (metric(report, "nik").hit, metric(report, "nik").total) == (2, 5)  # a and d
    assert (metric(report, "nik_salah").hit, metric(report, "nik_salah").total) == (2, 3)  # b and e, of a, b, e
    assert report.by_kind[0].nik == (2, 5)
    # Auto-pass with a wrong NIK is an auto-pass error once NIK is labelled.
    assert metric(report, "auto_error").hit == 3  # b, c and e
    text = format_report(report)
    assert NIK_A not in text and NIK_B not in text and "NIK 2/5" in text


def test_masked_niks_are_not_measured_and_the_report_says_why(tmp_path):
    report = run_nik_eval(tmp_path, [label("a.pdf", nik=NIK_A)], [with_nik(result("a.pdf"), NIK_A)], full_nik=False)
    assert report.nik_masked and metric(report, "nik").value is None
    assert "output.nik: full" in format_report(report)
    write_detail(report, tmp_path / "detail.csv")
    with (tmp_path / "detail.csv").open(encoding="utf-8-sig", newline="") as fh:
        assert next(csv.DictReader(fh))["nik"] == ""


def test_a_nik_label_that_is_not_16_digits_is_refused_without_echoing_it(tmp_path):
    with pytest.raises(EvaluationError) as caught:
        run_nik_eval(tmp_path, [label("a.pdf", nik=NIK_A[:15])], [])
    assert str(caught.value) == "baris 2: kolom nik tidak valid"


def test_results_that_are_not_pipeline_output_are_refused(tmp_path):
    (tmp_path / "results.json").write_text('{"file": "a.pdf"}', encoding="utf-8")
    with pytest.raises(EvaluationError, match="results.json tidak valid"):
        evaluate(tmp_path / "results.json", write_labels(tmp_path / "labels.csv", [label("a.pdf")]))


class FakeExtractor:
    name = "fake-llm"

    def extract(self, masked_text: str) -> ExtractionResult:
        return llm_answer(start=START, end=END)


def test_cli_evaluates_a_real_pipeline_run_and_prints_no_names(tmp_path):
    filename = "Budi Santoso - 1 Januari 2026 - 31 Desember 2026.pdf"
    folder = tmp_path / "in"
    for sub in ("signed", "unsigned"):
        (folder / sub).mkdir(parents=True)
    signed_contract_pdf(folder / "signed" / filename, "annotation")
    signed_contract_pdf(folder / "unsigned" / filename)
    write_json(process_path(folder, Settings(), recursive=True, extractor=FakeExtractor()), tmp_path / "results.json")
    write_labels(
        tmp_path / "labels.csv",
        [label(f"signed/{filename}"), label(f"unsigned/{filename}", status_ttd="unsigned", vendor="PT Vendor Dua")],
    )

    out = CliRunner().invoke(
        app,
        [
            "evaluate",
            str(tmp_path / "results.json"),
            str(tmp_path / "labels.csv"),
            "--json",
            str(tmp_path / "report.json"),
            "--detail",
            str(tmp_path / "detail.csv"),
        ],
    )
    assert out.exit_code == 0, out.output
    assert "Page selection recall: 2/2 file (100,0%)" in out.output
    assert "Recall unsigned: 1/1 file (100,0%)" in out.output
    assert "Kebocoran halaman: 0/" in out.output
    assert "Budi" not in out.output and "Desember" not in out.output and "2026-" not in out.output

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    values = {m["kunci"]: (m["hit"], m["total"]) for m in report["metrik"]}
    assert values["nama"] == values["periode"] == values["page_selection"] == (2, 2)
    assert values["precision_signed"] == (1, 1) and values["auto_error"] == (0, 2)
    assert "Budi" not in json.dumps(report)

    with (tmp_path / "detail.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [(r["ttd_label"], r["ttd_prediksi"], r["page_selection"]) for r in rows] == [
        ("signed", "signed", "ya"),
        ("unsigned", "unsigned", "ya"),
    ]


def test_cli_rejects_a_broken_label_file(tmp_path):
    write_json([], tmp_path / "results.json")
    write_labels(tmp_path / "labels.csv", [label("a.pdf", halaman_ttd="lima")])
    out = CliRunner().invoke(app, ["evaluate", str(tmp_path / "results.json"), str(tmp_path / "labels.csv")])
    assert out.exit_code == 2
    assert "baris 2: kolom halaman_ttd tidak valid" in out.output
