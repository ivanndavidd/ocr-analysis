"""Evaluasi keluaran `run` terhadap label manual Fase 0: angka gate Bagian 5 dan 14 PRD.

File label memuat nama karyawan asli, jadi hanya disimpan di folder `data/` (di-gitignore) di lingkungan yang
disetujui untuk data asli. Laporan hanya berisi angka agregat, jenis PDF, dan nama vendor; nama karyawan, tanggal,
dan nama file tidak pernah dicetak. Hanya file detail opsional (`--detail`) yang memuat nama file.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from contract_extractor.export import neutralize_formula
from contract_extractor.models import Decision, FileResult, SignatureStatus
from contract_extractor.pipeline import FLAG_AUTO_DISABLED
from contract_extractor.stages.filename import normalize_name

LABEL_COLUMNS = (
    "file",
    "kelompok",
    "vendor",
    "jenis_pdf",
    "nama_karyawan",
    "tanggal_mulai",
    "tanggal_selesai",
    "status_ttd",
    "halaman_identitas",
    "halaman_periode",
    "halaman_ttd",
    "kasus",
)
# Components that run on this machine. Anything else on a page counts as leaving it, so a new provider whose
# component name is not listed here shows up as a leak instead of hiding one.
LOCAL_COMPONENTS = ("text_layer", "tesseract@", "signature@")
MASKED_SUFFIX = ":masked"  # the masked P_id payload, the only text allowed to leave the machine


class EvaluationError(Exception):
    """Input that cannot be evaluated. Messages name rows and columns, never cell values."""


class Kelompok(str, Enum):
    KALIBRASI = "kalibrasi"
    UAT = "uat"  # set aside from the start and never used to calibrate thresholds (PRD Bagian 14)


class KelompokFilter(str, Enum):
    SEMUA = "semua"
    KALIBRASI = "kalibrasi"
    UAT = "uat"


class LabelPdfKind(str, Enum):
    NATIVE = "native"
    SCAN = "scan"
    HYBRID = "hybrid"
    CAMPURAN = "campuran"


class LabelCase(str, Enum):
    STEMPEL = "stempel"
    TULISAN_TANGAN = "tulisan_tangan"
    FOTO_BURAM = "foto_buram"
    NAMA_FILE_SALAH = "nama_file_salah"
    LAMPIRAN = "lampiran"


class LabelSignature(str, Enum):
    SIGNED = "signed"
    UNSIGNED = "unsigned"


class Label(BaseModel):
    """One labeled contract. Ground truth has no `uncertain`: a person decides signed or unsigned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(min_length=1)  # path relative to the input folder, as in the `file` column of results
    kelompok: Kelompok
    vendor: str = Field(min_length=1)
    jenis_pdf: LabelPdfKind
    nama_karyawan: str = Field(min_length=1)
    tanggal_mulai: date
    tanggal_selesai: date
    status_ttd: LabelSignature
    halaman_identitas: int = Field(ge=1)
    halaman_periode: int = Field(ge=1)
    halaman_ttd: int = Field(ge=1)
    kasus: frozenset[LabelCase] = frozenset()

    @field_validator("file", "vendor", "nama_karyawan", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip().replace("\\", "/") if isinstance(value, str) else value

    @field_validator("kasus", mode="before")
    @classmethod
    def _split_cases(cls, value: object) -> object:
        return {part.strip() for part in value.split(";") if part.strip()} if isinstance(value, str) else value


def load_labels(path: Path) -> list[Label]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [column for column in LABEL_COLUMNS if column not in (reader.fieldnames or [])]
        if missing:
            raise EvaluationError(f"kolom label tidak ada: {', '.join(missing)}")
        labels: list[Label] = []
        seen: set[str] = set()
        for row_number, row in enumerate(reader, start=2):  # row 1 is the header
            try:
                label = Label.model_validate({column: row[column] for column in LABEL_COLUMNS})
            except ValidationError as exc:
                columns = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
                raise EvaluationError(f"baris {row_number}: kolom {', '.join(columns)} tidak valid") from None
            if label.file in seen:
                raise EvaluationError(f"baris {row_number}: file yang sama sudah dilabeli")
            seen.add(label.file)
            labels.append(label)
    if not labels:
        raise EvaluationError("file label tidak berisi baris")
    return labels


def load_results(path: Path) -> list[FileResult]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError
        return [FileResult.model_validate(item) for item in payload]
    except ValueError:  # JSONDecodeError and pydantic's ValidationError are both ValueErrors
        raise EvaluationError("results.json tidak valid: bukan keluaran `contract-extractor run`") from None


@dataclass(frozen=True)
class FileCheck:
    label: Label
    result: FileResult | None  # None: the labeled file has no row in results.json

    @property
    def page_ok(self) -> bool:
        r, label = self.result, self.label
        return (
            r is not None
            and {label.halaman_identitas, label.halaman_periode} <= set(r.log.p_id)
            and r.log.p_ttd == label.halaman_ttd
        )

    @property
    def name_ok(self) -> bool:
        r = self.result
        return r is not None and bool(r.nama_karyawan) and normalize_name(r.nama_karyawan) == normalize_name(self.label.nama_karyawan)

    @property
    def period_ok(self) -> bool:
        r = self.result
        return r is not None and (r.tanggal_mulai, r.tanggal_selesai) == (self.label.tanggal_mulai, self.label.tanggal_selesai)

    @property
    def predicted_signature(self) -> str | None:
        return self.result.status_ttd.value if self.result and self.result.status_ttd else None

    @property
    def auto_candidate(self) -> bool:
        """Auto, or auto held back only by `allow_auto: false`; the gate is measured before auto is switched on."""
        r = self.result
        return r is not None and (r.keputusan is Decision.AUTO or FLAG_AUTO_DISABLED in r.flags)

    @property
    def auto_wrong(self) -> bool:
        correct = self.name_ok and self.period_ok and self.predicted_signature == self.label.status_ttd.value
        return self.auto_candidate and not correct


@dataclass(frozen=True)
class Target:
    name: str  # "Fase 1", "Bagian 5", or "kill"
    op: str  # ">=" or "<="
    threshold: float

    def check(self, value: float | None) -> bool | None:
        if value is None:
            return None
        value = round(value, 9)  # 57/60 must meet 0.95
        return value >= self.threshold if self.op == ">=" else value <= self.threshold


def _kill(threshold: float) -> Target:
    return Target("kill", ">=", threshold)  # safe while the value stays at or above the kill line


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    hit: int
    total: int
    targets: tuple[Target, ...]
    unit: str = "file"

    @property
    def value(self) -> float | None:
        return self.hit / self.total if self.total else None


@dataclass(frozen=True)
class GroupRow:
    name: str
    files: int
    page: tuple[int, int]
    name_acc: tuple[int, int]
    period: tuple[int, int]
    recall_unsigned: tuple[int, int]
    ocr_conf: float | None  # mean confidence over the pages that went through OCR


@dataclass(frozen=True)
class CompositionCheck:
    label: str
    detail: str
    passed: bool | None  # None: informational only


@dataclass
class Report:
    kelompok: KelompokFilter
    checks: list[FileCheck]
    n_missing: int
    n_unlabeled: int
    metrics: list[Metric]
    by_kind: list[GroupRow]
    by_vendor: list[GroupRow]
    composition: list[CompositionCheck]


def _count(checks: Iterable[FileCheck], test: Callable[[FileCheck], bool]) -> int:
    return sum(1 for check in checks if test(check))


def _not_signed(check: FileCheck) -> bool:
    return check.result is not None and check.predicted_signature != SignatureStatus.SIGNED.value


def leak_counts(results: Iterable[FileResult]) -> tuple[int, int]:
    """(leaked pages, pages with any component). A page leaks when a non-local component received it, unless it
    is a P_id page that only left as the masked payload."""
    leaked = pages = 0
    for result in results:
        for number, components in result.log.komponen_halaman.items():
            pages += 1
            outside = [c for c in components if not c.startswith(LOCAL_COMPONENTS)]
            allowed = number in result.log.p_id and all(c.endswith(MASKED_SUFFIX) for c in outside)
            if outside and not allowed:
                leaked += 1
    return leaked, pages


def _metrics(checks: list[FileCheck], results: list[FileResult]) -> list[Metric]:
    unsigned = [c for c in checks if c.label.status_ttd is LabelSignature.UNSIGNED]
    predicted_signed = [c for c in checks if c.predicted_signature == SignatureStatus.SIGNED.value]
    auto = [c for c in checks if c.auto_candidate]
    leaked, pages = leak_counts(results)
    return [
        Metric(
            "page_selection",
            "Page selection recall",
            _count(checks, lambda c: c.page_ok),
            len(checks),
            (Target("Fase 1", ">=", 0.95), Target("Bagian 5", ">=", 0.95), _kill(0.90)),
        ),
        Metric(
            "nama",
            "Akurasi nama",
            _count(checks, lambda c: c.name_ok),
            len(checks),
            (Target("Fase 2", ">=", 0.90), Target("Bagian 5", ">=", 0.95), _kill(0.85)),
        ),
        Metric(
            "periode",
            "Akurasi periode",
            _count(checks, lambda c: c.period_ok),
            len(checks),
            (Target("Fase 2", ">=", 0.90), Target("Bagian 5", ">=", 0.95), _kill(0.85)),
        ),
        Metric(
            "recall_unsigned",
            "Recall unsigned",
            _count(unsigned, _not_signed),
            len(unsigned),
            (Target("Fase 3", ">=", 0.98), Target("Bagian 5", ">=", 0.98), _kill(0.95)),
        ),
        Metric(
            "precision_signed",
            "Precision signed",
            _count(predicted_signed, lambda c: c.label.status_ttd is LabelSignature.SIGNED),
            len(predicted_signed),
            (Target("Bagian 5", ">=", 0.98),),
        ),
        Metric("auto_pass", "Auto-pass rate (auto + auto_disabled)", len(auto), len(checks), (Target("Bagian 5", ">=", 0.80),)),
        Metric("auto_error", "Kesalahan pada auto-pass", _count(auto, lambda c: c.auto_wrong), len(auto), (Target("Bagian 5", "<=", 0.01),)),
        Metric("leak", "Kebocoran halaman", leaked, pages, (Target("Bagian 5", "<=", 0.0),), unit="halaman"),
    ]


def _group_row(name: str, checks: list[FileCheck]) -> GroupRow:
    unsigned = [c for c in checks if c.label.status_ttd is LabelSignature.UNSIGNED]
    confidences = [conf for c in checks if c.result for conf in c.result.log.ocr_conf.values()]
    return GroupRow(
        name=name,
        files=len(checks),
        page=(_count(checks, lambda c: c.page_ok), len(checks)),
        name_acc=(_count(checks, lambda c: c.name_ok), len(checks)),
        period=(_count(checks, lambda c: c.period_ok), len(checks)),
        recall_unsigned=(_count(unsigned, _not_signed), len(unsigned)),
        ocr_conf=sum(confidences) / len(confidences) if confidences else None,
    )


def _groups(checks: list[FileCheck], key: Callable[[FileCheck], str]) -> list[GroupRow]:
    grouped: dict[str, list[FileCheck]] = {}
    for check in checks:
        grouped.setdefault(key(check), []).append(check)
    return [_group_row(name, members) for name, members in sorted(grouped.items())]


def _composition(labels: list[Label]) -> list[CompositionCheck]:
    """The minimum test-set composition from PRD Bagian 14, checked before trusting any metric."""
    n = len(labels)
    kinds = Counter(label.jenis_pdf for label in labels)
    vendors = Counter(label.vendor for label in labels)
    cases = Counter(case for label in labels for case in label.kasus)
    signed = sum(1 for label in labels if label.status_ttd is LabelSignature.SIGNED)
    checks = [CompositionCheck("Jumlah file", f"{n} (target 50-100)", 50 <= n <= 100)]
    checks += [CompositionCheck(f"Vendor {vendor}", f"{count} file (target >= 5)", count >= 5) for vendor, count in sorted(vendors.items())]
    for kind in (LabelPdfKind.NATIVE, LabelPdfKind.SCAN, LabelPdfKind.HYBRID):
        checks.append(CompositionCheck(f"Jenis {kind.value}", f"{kinds[kind]} file (target >= 15)", kinds[kind] >= 15))
    for name, count in (("signed", signed), ("unsigned", n - signed)):
        checks.append(CompositionCheck(f"Status {name}", f"{_pct(count / n)} (target >= 30%)", count / n >= 0.30))
    for case in (LabelCase.STEMPEL, LabelCase.TULISAN_TANGAN):
        checks.append(CompositionCheck(f"Kasus {case.value}", f"{cases[case]} file (target >= 1)", cases[case] >= 1))
    checks.append(
        CompositionCheck("Kasus nama_file_salah", f"{cases[LabelCase.NAMA_FILE_SALAH]} file (target >= 10)", cases[LabelCase.NAMA_FILE_SALAH] >= 10)
    )
    checks.append(CompositionCheck("Kasus lampiran", f"{cases[LabelCase.LAMPIRAN]} file (target >= 5 bila ada di data nyata)", None))
    return checks


def evaluate(results_path: Path, labels_path: Path, kelompok: KelompokFilter = KelompokFilter.SEMUA) -> Report:
    labels = load_labels(labels_path)
    results = load_results(results_path)
    by_file = {result.file: result for result in results}
    chosen = [label for label in labels if kelompok is KelompokFilter.SEMUA or label.kelompok.value == kelompok.value]
    if not chosen:
        raise EvaluationError(f"tidak ada label di kelompok {kelompok.value}")
    checks = [FileCheck(label, by_file.get(label.file)) for label in chosen]
    labeled = {label.file for label in labels}
    return Report(
        kelompok=kelompok,
        checks=checks,
        n_missing=sum(1 for check in checks if check.result is None),
        n_unlabeled=sum(1 for result in results if result.file not in labeled),
        metrics=_metrics(checks, results),
        by_kind=_groups(checks, lambda c: c.label.jenis_pdf.value),
        by_vendor=_groups(checks, lambda c: c.label.vendor),
        composition=_composition(chosen),
    )


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%".replace(".", ",")


def _ratio(hit: int, total: int, unit: str = "file") -> str:
    return f"{hit}/{total} {unit} ({_pct(hit / total)})" if total else f"n/a (0 {unit})"


def _target_text(target: Target, value: float | None) -> str:
    outcome = target.check(value)
    if target.name == "kill":
        state = {True: "aman", False: "TERSENTUH", None: "n/a"}[outcome]
        return f"kill < {_pct(target.threshold)}: {state}"
    state = {True: "lulus", False: "GAGAL", None: "n/a"}[outcome]
    if target.op == "<=" and target.threshold == 0:
        return f"{target.name} = 0: {state}"
    return f"{target.name} {target.op} {_pct(target.threshold)}: {state}"


def format_report(report: Report) -> str:
    lines = [
        f"Evaluasi {len(report.checks)} file berlabel (kelompok: {report.kelompok.value}); "
        f"{report.n_missing} tanpa hasil dihitung gagal, {report.n_unlabeled} hasil tanpa label diabaikan.",
        "",
        "Metrik:",
    ]
    for metric in report.metrics:
        targets = "; ".join(_target_text(target, metric.value) for target in metric.targets)
        lines.append(f"- {metric.label}: {_ratio(metric.hit, metric.total, metric.unit)}. {targets}")
    lines.append("- Waktu per file: tidak diukur, karena results.json tidak mencatat waktu proses (Bagian 5: median <= 30 detik).")

    for title, rows in (("Per jenis PDF", report.by_kind), ("Per vendor", report.by_vendor)):
        lines += ["", f"{title}:"]
        for row in rows:
            conf = f", confidence OCR rata-rata {row.ocr_conf:.2f}".replace(".", ",") if row.ocr_conf is not None else ""
            lines.append(
                f"- {row.name} ({row.files} file): page selection {_ratio(*row.page)}, nama {_ratio(*row.name_acc)}, "
                f"periode {_ratio(*row.period)}, recall unsigned {_ratio(*row.recall_unsigned)}{conf}"
            )

    lines += ["", "Komposisi test set (Bagian 14):"]
    for item in report.composition:
        state = {True: "lulus", False: "KURANG", None: "info"}[item.passed]
        lines.append(f"- {item.label}: {item.detail}: {state}")
    return "\n".join(lines)


def report_json(report: Report) -> dict:
    def group(row: GroupRow) -> dict:
        return {
            "nama": row.name,
            "file": row.files,
            "page_selection": list(row.page),
            "nama_karyawan": list(row.name_acc),
            "periode": list(row.period),
            "recall_unsigned": list(row.recall_unsigned),
            "confidence_ocr": row.ocr_conf,
        }

    return {
        "kelompok": report.kelompok.value,
        "file_berlabel": len(report.checks),
        "tanpa_hasil": report.n_missing,
        "hasil_tanpa_label": report.n_unlabeled,
        "metrik": [
            {
                "kunci": m.key,
                "nama": m.label,
                "hit": m.hit,
                "total": m.total,
                "nilai": m.value,
                "target": [{"nama": t.name, "operator": t.op, "ambang": t.threshold, "lulus": t.check(m.value)} for t in m.targets],
            }
            for m in report.metrics
        ],
        "per_jenis_pdf": [group(row) for row in report.by_kind],
        "per_vendor": [group(row) for row in report.by_vendor],
        "komposisi": [{"nama": c.label, "detail": c.detail, "lulus": c.passed} for c in report.composition],
    }


DETAIL_COLUMNS = (
    "file",
    "kelompok",
    "vendor",
    "jenis_pdf",
    "diproses",
    "page_selection",
    "nama",
    "periode",
    "ttd_label",
    "ttd_prediksi",
    "keputusan",
    "auto_salah",
)


def write_detail(report: Report, path: Path) -> None:
    """Per-file outcomes for calibration. It names files, so it belongs in the ignored output/ or data/ folder."""

    def yes(flag: bool) -> str:
        return "ya" if flag else "tidak"

    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=DETAIL_COLUMNS)
        writer.writeheader()
        for c in report.checks:
            row = {
                "file": c.label.file,
                "kelompok": c.label.kelompok.value,
                "vendor": c.label.vendor,
                "jenis_pdf": c.label.jenis_pdf.value,
                "diproses": yes(c.result is not None),
                "page_selection": yes(c.page_ok),
                "nama": yes(c.name_ok),
                "periode": yes(c.period_ok),
                "ttd_label": c.label.status_ttd.value,
                "ttd_prediksi": c.predicted_signature or "",
                "keputusan": c.result.keputusan.value if c.result else "",
                "auto_salah": yes(c.auto_wrong),
            }
            writer.writerow({k: neutralize_formula(v) for k, v in row.items()})
