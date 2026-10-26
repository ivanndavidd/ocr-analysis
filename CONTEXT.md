# CONTEXT.md

Living state of this project: progress tracker and AI context. Agents: read this before non-trivial work; update the affected sections when changes are material (structure, plan, milestones, decisions). Keep it current-state, not a changelog — git history records history. If this file contradicts the code, the code wins: fix this file. Go-live and review information does not live here; it lives in PROJECT.md, the review dossier.

## Overview

Batch pipeline yang membaca PDF kontrak karyawan dari berbagai vendor (native, scan, hybrid) dan mengeluarkan nama karyawan, masa kontrak, dan status tanda tangan karyawan (signed/unsigned/uncertain), dengan keputusan auto-pass atau review untuk HR. Halaman berisi gaji tidak pernah keluar dari mesin lokal; satu-satunya data yang keluar adalah teks halaman identitas yang sudah di-mask, dikirim ke LLM cloud. Spesifikasi lengkap ada di `PRD & RFC Ekstraksi Kontrak Karyawan dari PDF.md`.

## Structure

- `PRD & RFC Ekstraksi Kontrak Karyawan dari PDF.md` — PRD dan RFC, sumber requirement dan desain pipeline
- `sample/` — 4 PDF kontrak asli; berisi PII, di-gitignore, jangan dipakai di development atau test
- `AGENTS.md`, `CLAUDE.md` — instruksi agent (salinan karena symlink tidak tersedia di Windows ini; edit keduanya)
- `PROJECT.md` — skeleton dossier go-live, diisi lewat `project-documenter`
- `apps/extractor/` — paket Python `contract-extractor` (CLI batch)
  - `pyproject.toml` — dependency langsung di-pin; `requirements.lock` — set lengkap hasil `uv pip compile` untuk Python 3.11
  - `config.example.yaml` — contoh konfigurasi; `ocr.cloud_fallback` default false
  - `src/contract_extractor/`
    - `cli.py` — Typer, perintah `run <file|folder> [--config] [--out] [--recursive]`
    - `config.py` — Settings pydantic dari YAML (extra key ditolak)
    - `models.py` — `Word`, enum status, `FileLog`, `FileResult` (skema keluaran Bagian 12)
    - `dates.py` — tabel bulan ID/EN, tahun dua digit, `months_between`
    - `pipeline.py` — validasi input, statistik halaman, isolasi error per file (FR-1), daftar `PENDING_STAGES`
    - `export.py` — JSON, CSV, XLSX dengan netralisasi formula injection
    - `pdf/reader.py` — validasi file (ekstensi, magic, ukuran, jumlah halaman) dan statistik halaman via pdfplumber
    - `stages/` — `page_classify` (jadi), `masking` (jadi), `filename` (jadi), `scoring` (jadi), `page_selection` dan `signature` (stub)
    - `ocr/base.py`, `extractors/base.py` — interface `OCRProvider` dan `Extractor` (belum ada implementasi)
  - `tests/` — 95 test pytest, semuanya memakai PDF dan data sintetis

## Plan And Milestones

- [x] Revisi PRD: privasi, masking, Gemini via Vertex, deteksi tanda tangan native, temuan sampel, konvensi nama file
- [x] Init repo (AGENTS.md, CONTEXT.md, PROJECT.md, .gitignore, git init)
- [x] Scaffold paket: CLI, config, model, pipeline, export, interface OCR/extractor
- [x] Modul murni + test: masking dengan korpus kebocoran, parser nama file, scoring, klasifikasi halaman
- [ ] Fase 0: test set berlabel 50–100 file di lingkungan yang disetujui untuk data asli, definisi signed, persetujuan kebijakan cloud
- [ ] Fase 1: page selection (zona penutup), OCR lokal Tesseract → PaddleOCR, render pypdfium2
- [ ] Fase 2: regex periode, extractor Claude API dan Gemini Vertex, verifikasi bukti
- [ ] Fase 3: deteksi tanda tangan (objek native via pypdf + analisis tinta OpenCV)
- [ ] Fase 4: scoring end-to-end, Dockerfile (python 3.11 + tesseract-ocr-ind), UAT

## Key Decisions

- Stack Python 3.11, menyimpang dari allowedStack (Bun/TS). Approval Tech Architecture Group diurus langsung oleh owner; baris `## Stack Exceptions` di PROJECT.md baru ditambahkan setelah ada approver dan tanggal.
- Minimal Python 3.11: Pillow < 13 punya 41 advisory OSV (5 critical), dan Pillow 13 butuh 3.11. Python 3.11 lokal dipasang lewat uv.
- PyMuPDF tidak dipakai karena AGPL-3.0 (dilarang kebijakan lisensi). Penggantinya pypdfium2 (render, text layer), pdfplumber (kata, gambar, kurva), dan pypdf (anotasi, field tanda tangan; ditambahkan di Fase 3).
- Dependency hanya ditambahkan saat modul yang memakainya dibangun. Belum ada dateparser, OpenCV, pytesseract, PaddleOCR, anthropic, google-genai.
- Logger `pdfminer` dan `pdfplumber` dikunci ke WARNING, karena di DEBUG keduanya mencatat isi content stream (nama, NIK). Log aplikasi merujuk file lewat hash nama file, bukan nama file.
- Nama file produksi berpola `Nama Karyawan - Masa Kontrak`, dipisah di ` - ` pertama. Nama file sampel tidak mengikuti pola ini.
- Development dan test hanya memakai data sintetis. Menjalankan pipeline pada PDF asli (`sample/`, test set Fase 0) butuh lingkungan dan persetujuan terpisah.

## Current State

- `contract-extractor run` sudah berjalan end-to-end pada PDF sintetis. Tahap yang belum dibangun dicatat di `PENDING_STAGES`, sehingga setiap file keluar dengan `pipeline_incomplete` dan `review_priority`, tidak pernah `auto`.
- Verifikasi: 95 test lulus di Python 3.11.15; OSV 0 kerentanan di 29 package pada lock; semua lisensi permisif (MIT, BSD, Apache-2.0, ISC, PSF-2.0, MIT-CMU).
- Belum pernah dijalankan pada PDF asli. Tesseract belum terpasang di mesin ini. Belum ada commit.
- Perbandingan periode presisi bulan-tahun (baris "Mirip" di PRD) belum diimplementasikan, menunggu konfirmasi format masa kontrak di nama file.
- Risk level belum dicatat di PROJECT.md. Kemungkinan besar High karena memproses NIK dan data gaji; perlu `/assess-risk` oleh owner.
- Langkah berikutnya: Fase 1, dimulai dari page selection dan reader halaman (teks + kata berkoordinat) untuk native.
