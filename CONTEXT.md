# CONTEXT.md

Living state of this project: progress tracker and AI context. Agents: read this before non-trivial work; update the affected sections when changes are material (structure, plan, milestones, decisions). Keep it current-state, not a changelog — git history records history. If this file contradicts the code, the code wins: fix this file. Go-live and review information does not live here; it lives in PROJECT.md, the review dossier.

## Overview

Batch pipeline yang membaca PDF kontrak karyawan dari berbagai vendor (native, scan, hybrid) dan mengeluarkan nama karyawan, masa kontrak, dan status tanda tangan karyawan (signed/unsigned/uncertain), dengan keputusan auto-pass atau review untuk HR. Halaman berisi gaji tidak pernah keluar dari mesin lokal; satu-satunya data yang keluar adalah teks halaman identitas yang sudah di-mask, dikirim ke LLM cloud. Spesifikasi lengkap ada di `PRD & RFC Ekstraksi Kontrak Karyawan dari PDF.md`.

## Structure

- `PRD & RFC Ekstraksi Kontrak Karyawan dari PDF.md` — PRD dan RFC, sumber requirement dan desain pipeline
- `sample/` — 4 PDF kontrak asli dari tim client; berisi PII, di-gitignore, tidak pernah jadi fixture test; hanya untuk uji asap lokal (lihat Key Decisions)
- `output/` — keluaran run lokal, termasuk uji asap pada `sample/`; di-gitignore
- `AGENTS.md`, `CLAUDE.md` — instruksi agent (salinan karena symlink tidak tersedia di Windows ini; edit keduanya); `## Build and run` memuat perintah venv, test, CLI, dan image
- `PROJECT.md` — skeleton dossier go-live, diisi lewat `project-documenter`
- `apps/extractor/` — paket Python `contract-extractor` (CLI batch)
  - `pyproject.toml` — dependency langsung di-pin; `requirements.lock` — set lengkap hasil `uv pip compile` untuk Python 3.11
  - `config.example.yaml` — contoh konfigurasi; `ocr.cloud_fallback`, `extractor.enabled`, dan `decision.allow_auto` default false
  - `.env.example` — placeholder environment (Tesseract, Anthropic, Vertex AI); nilai asli tidak pernah di-commit
  - `Dockerfile`, `.dockerignore` — image batch CLI: Python 3.11.15 slim trixie, Tesseract 5.5.0 + ind/eng di-pin, dependency runtime saja, UID 10001; context allow-list (tanpa PDF, `.env`, test)
  - `src/contract_extractor/`
    - `cli.py` — Typer, perintah `run <file|folder> [--config] [--out] [--recursive]`
    - `config.py` — Settings pydantic dari YAML (extra key ditolak)
    - `models.py` — `Word`, enum status, `FileLog`, `FileResult` (skema keluaran Bagian 12)
    - `dates.py` — tabel bulan ID/EN, pola bulan dan tanggal angka bersama, tahun dua digit, `months_between`
    - `layout.py` — `PageText` (kata berkoordinat per halaman) dan `group_lines`, dipakai text layer maupun OCR
    - `pipeline.py` — validasi input, baca dokumen, OCR kasar kandidat scan/hybrid, page selection, OCR penuh P_id/P_ttd, regex periode, payload P_id ter-mask, kirim ke extractor, verifikasi, validasi nama file, deteksi tanda tangan di P_ttd, keputusan dengan gate `allow_auto`, isolasi error per file (FR-1); kolom `file` = path relatif terhadap folder input
    - `export.py` — JSON, CSV, XLSX dengan netralisasi formula injection
    - `pdf/reader.py` — validasi file (ekstensi, magic, ukuran, jumlah halaman); `read_document` mengklasifikasi halaman dan membaca kata berkoordinat halaman native via pdfplumber; economy mode hanya membuka halaman kandidat
    - `pdf/render.py` — render halaman ke grayscale di memori via pypdfium2, anotasi ikut digambar
    - `pdf/objects.py` — objek halaman native yang bisa jadi tanda tangan via pdfplumber: widget `/FT /Sig` bernilai, anotasi Ink/Stamp/FreeText/FileAttachment, gambar non-latar, path vektor
    - `ocr/` — `preprocess` (hapus bayangan, `straighten` untuk deskew, adaptive threshold), `tesseract` (provider lokal), `engine` (`OcrRunner`: skoring 150 dpi dan tangga baca 300 dpi dengan uji kualitas; `build_runner` dari config)
    - `stages/` — `page_classify`, `masking` (termasuk `mask_pages`: mask per halaman lalu potong), `filename`, `scoring`, `page_selection` (skor, `closing_zone` dengan footer band, `signature_block`), `period_regex`, `extraction` (verifikasi lokal jawaban LLM), `ink` (analisis tinta ROI: buang garis, teks cetak, stempel bulat; fitur dan verdict), `signature` (jangkar nama atau sebutan fuzzy, ROI, gabungan objek native dan tinta)
    - `extractors/` — `base` (interface, skema hasil, error), `prompt` (prompt dan skema JSON bersama), `claude` (Claude API, default `claude-sonnet-5-5`), `gemini` (Vertex AI saja), `factory` (`build_extractor`, mati secara default)
  - `tests/` — 260 test pytest, termasuk `test_cli_e2e` (batch sintetis lewat CLI dengan extractor palsu) (extractor diuji dengan client palsu, tanpa jaringan); `helpers.py` membangun PDF native dan scan sintetis (nama dan angka fiktif, tanda tangan berupa coretan gambar sendiri: vektor, anotasi Ink, gambar, field digital); test bertanda `tesseract` butuh Tesseract dengan data ind+eng

## Plan And Milestones

- [x] Revisi PRD: privasi, masking, Gemini via Vertex, deteksi tanda tangan native, temuan sampel, konvensi nama file
- [x] Init repo (AGENTS.md, CONTEXT.md, PROJECT.md, .gitignore, git init)
- [x] Scaffold paket: CLI, config, model, pipeline, export, interface OCR/extractor
- [x] Modul murni + test: masking dengan korpus kebocoran, parser nama file, scoring, klasifikasi halaman
- [ ] Fase 0: test set berlabel 50–100 file di lingkungan yang disetujui untuk data asli, definisi signed, persetujuan kebijakan cloud
- [x] Fase 1a: reader kata berkoordinat untuk halaman native, page selection dengan zona penutup
- [x] Fase 1b: render pypdfium2, preprocessing OpenCV, OCR lokal Tesseract (skoring 150 dpi, baca 300 dpi, uji kualitas)
- [ ] Fase 1c: ukur page selection recall dan confidence OCR per jenis PDF di test set; PaddleOCR bila Tesseract tidak cukup
- [x] Fase 2a: regex periode berjangkar dan masking teks P_id di pipeline, semuanya lokal
- [x] Fase 2b: extractor Claude API dan Gemini Vertex, verifikasi bukti, kesepakatan regex dan LLM, level ekstraksi, validasi nama file
- [ ] Fase 2c: ukur akurasi nama dan periode per extractor di test set (butuh persetujuan kebijakan cloud dan data Fase 0)
- [x] Fase 3: deteksi tanda tangan lokal (objek native via pdfplumber + analisis tinta OpenCV), gate `allow_auto`
- [ ] Fase 3b: kalibrasi ambang tanda tangan di test set Fase 0 (gate recall unsigned ≥ 98%), uji stempel berwarna, tulisan tangan, foto buram
- [x] Fase 4a: Dockerfile, uji end-to-end CLI pada batch sintetis, uji asap lokal pada `sample/` (perbaikan zona penutup dan blok tanda tangan dari temuan sampel)
- [ ] Fase 4b: build image di platform (atau Apple Container), UAT bersama HR pada 20 file baru

## Key Decisions

- Stack Python 3.11, menyimpang dari allowedStack (Bun/TS). Approval Tech Architecture Group diurus langsung oleh owner; baris `## Stack Exceptions` di PROJECT.md baru ditambahkan setelah ada approver dan tanggal.
- Minimal Python 3.11: Pillow < 13 punya 41 advisory OSV (5 critical), dan Pillow 13 butuh 3.11. Python 3.11 lokal dipasang lewat uv.
- PyMuPDF tidak dipakai karena AGPL-3.0 (dilarang kebijakan lisensi). Penggantinya pypdfium2 (render, text layer) dan pdfplumber (kata, gambar, kurva, anotasi, widget field tanda tangan). pypdf tidak ditambahkan karena pdfplumber sudah mencakup semua pemeriksaan objek Fase 3.
- Dependency hanya ditambahkan saat modul yang memakainya dibangun. Belum ada dateparser dan PaddleOCR. SDK resmi `anthropic==1.13.0` dan `google-genai==2.29.0` dipin ke rilis berumur minimal dua minggu; relock tanpa `--exclude-newer` karena opsi itu menurunkan Pillow ke versi yang punya advisory critical.
- PaddleOCR ditunda sampai kualitas Tesseract diukur di test set; `build_runner` menolak `fallback: paddle` dan `cloud_fallback: true` dengan pesan jelas, bukan diam-diam diabaikan.
- Data bahasa Tesseract memakai tessdata_fast (`ind` dan `eng`), sama dengan paket Debian `tesseract-ocr-ind` untuk Docker nanti.
- CLI menolak jalan bila OCR lokal tidak tersedia (exit 2). Fungsi `process_file` tanpa OCR menandai halaman scan dengan `ocr_unavailable`, tidak menebak.
- Halaman hybrid diperlakukan seperti scan juga di Tahap 1; text layer-nya (biasanya header/footer) diabaikan.
- Logger `pdfminer` dan `pdfplumber` dikunci ke WARNING, karena di DEBUG keduanya mencatat isi content stream (nama, NIK). Log aplikasi merujuk file lewat hash nama file, bukan nama file.
- Nama file produksi berpola `Nama Karyawan - Masa Kontrak`, dipisah di ` - ` pertama. Nama file sampel tidak mengikuti pola ini.
- Development dan test hanya memakai data sintetis; PDF asli tidak pernah jadi fixture atau di-commit. Pada 2026-10-26 owner menyatakan `sample/` diberikan tim untuk pengujian, jadi uji asap lokal di laptop ini diizinkan dengan syarat: extractor mati (tidak ada teks keluar), keluaran di `output/` yang di-gitignore, dan agent hanya membaca ringkasan tanpa PII (nomor urut file, halaman, status, kode alasan). Ini konfirmasi owner, bukan keputusan GRC tercatat. Test set Fase 0 tetap butuh lingkungan dan persetujuan terpisah.
- Regex periode adalah pembanding, bukan sumber field keluaran: hasilnya hanya masuk `log.periode_regex`, dan field `tanggal_mulai`/`tanggal_selesai` baru diisi extractor di Fase 2b. Kandidat yang bertentangan membuat field null dan `ambigu` true, tidak memilih salah satu. Jangkar `sejak`, `terhitung`, `hingga` dan bentuk rentang `X s/d Y` ditambahkan di luar daftar awal PRD; PRD sudah diperbarui.
- Extractor cloud mati secara default dan baru jalan bila `extractor.enabled: true`. Bila aktif tetapi kredensial atau model tidak valid, CLI berhenti (exit 2) sebelum membaca file. Gemini hanya lewat Vertex AI dengan ADC; jalur API key Google AI Studio tidak ada di kode.
- Model Claude default `claude-sonnet-5-5` mengikuti PRD Bagian 13 (Sonnet), effort `high`, structured output, fallback sisi server untuk penolakan classifier (`refusal_fallback`). Model dan effort dikalibrasi di Fase 0.
- Jawaban LLM diverifikasi lokal terhadap payload yang dikirim; field yang tidak terbukti dibuang jadi null. Teks P_id dari OCR yang gagal uji kualitas tidak dikirim, dan nilai baris berlabel NIK/KTP/NPWP/rekening di-mask utuh, karena OCR bisa memecah digit sehingga pola deret digit tidak menangkapnya.
- Payload P_id di-mask per halaman lalu dipotong ke `extractor.max_chars_per_page`, karena memotong dulu bisa menyisakan potongan NIK di bawah 10 digit. `teks_keluar_karakter` tetap 0 sampai ada extractor yang benar-benar mengirim.
- Deteksi tanda tangan sepenuhnya lokal; halaman P_ttd dan crop-nya tidak pernah keluar. ROI selalu diikat ke nama karyawan tercetak (nama terverifikasi dari extractor, atau nama dari nama file standar bila extractor mati) atau ke sebutan karyawan; jangkar tidak ditemukan berarti `uncertain`, tidak pernah menebak kolom kanan. Pada PDF native, objek (field digital, anotasi, gambar, vektor) dan analisis tinta harus sepakat; tinta tanpa objek atau objek tanpa tinta berarti `uncertain`. Jangkar sebutan tidak pernah memberi margin jelas. Segmentasi warna HSV untuk stempel ditunda sampai test set menunjukkan perlu.
- Keputusan `auto` ditahan oleh `decision.allow_auto: false` (default): file yang semua komponennya hijau masuk `review` dengan flag `auto_disabled` sampai ambang dikalibrasi di Fase 0.
- Page selection menghitung kehadiran kata kunci, bukan frekuensi. Kata kunci tanda tangan hanya dihitung di zona penutup, dan halaman identitas tidak boleh jadi P_ttd kecuali kontraknya satu halaman. Bobot dan ambang (identitas 6, periode 3, tanda tangan 5, margin 3) adalah nilai awal untuk dikalibrasi di Fase 0.
- Zona penutup dibuka oleh paragraf penutup yang dikenali dari beberapa frasa (`demikian`, `ditandatangani`, `keadaan sadar`, `rangkap`, …) dan diikuti paling banyak enam baris isi; baris di bawah 92% tinggi halaman adalah footer. Jangkar tanda tangan hanya dicari di blok tanda tangan (zona tanpa paragraf dan tanpa ekor kalimat satu kolom). Ketiga aturan ini lahir dari uji asap `sample/`: tanpa itu P_ttd tidak terpilih di keempat sampel, dan ekor kalimat paragraf penutup yang memuat `PIHAK KEDUA` sempat memberi unsigned palsu di kolom kiri.
- Image container: versi Tesseract dan data bahasa di-pin karena ambang dikalibrasi pada keluarannya. Build lokal hanya dengan Apple Container (build contract melarang Docker Desktop dan shim `docker`), jadi di laptop Windows ini image belum pernah di-build; pemeriksaan statis kontrak (patch OS, dependency runtime saja, base di-pin, UID ≥ 10000) terpenuhi.

## Current State

- `contract-extractor run` memilih P_id dan P_ttd untuk PDF native maupun scan. OCR kasar 150 dpi hanya menyentuh halaman kandidat; OCR penuh 300 dpi hanya P_id dan P_ttd. Log `komponen_halaman` mencatat komponen yang menerima tiap halaman, dan `ocr_conf` mencatat confidence. Setelah itu, regex periode berjalan di teks P_id mentah (`log.periode_regex`) dan payload P_id di-mask (`log.pola_dimask`); `mask_leak` membatalkan payload (fail closed). Bila extractor aktif, payload dikirim, jawabannya diverifikasi, field hasil terisi, dan nama file dibandingkan (`log.validasi_nama_file`). Tanda tangan dideteksi lokal di P_ttd (`status_ttd`, `confidence.ttd`, `log.tanda_tangan`), lalu keputusan dihitung; karena `allow_auto` false, keputusan terbaik saat ini `review` + `auto_disabled`. Dengan extractor mati (default), ekstraksi merah sehingga file `review_priority`. Dengan `allow_auto: true` dan extractor aktif, kontrak native yang semua komponennya hijau keluar `auto` (diuji end to end lewat CLI dengan extractor palsu).
- Verifikasi: 260 test lulus di Python 3.11.15. Tesseract asli pada scan sintetis miring 1,5°: P_id 1, P_ttd 5, confidence ≥ 0,8, periode regex benar dari teks OCR, tanda tangan coretan terdeteksi signed dari piksel dan kontrak tanpa tanda tangan unsigned; sekitar 10 detik untuk 5 halaman end to end (target PRD ≤ 30 detik). Varian native (vektor, anotasi Ink, gambar, field digital, hanya tanda tangan perusahaan, paraf di pojok) diuji pada PDF sintetis. OSV 0 di 54 package. Extractor belum pernah memanggil API sungguhan (tidak ada kredensial dan kebijakan cloud belum disetujui); request diuji lewat client palsu.
- Lokal: Tesseract 5.4.0 ada di `%LOCALAPPDATA%\Programs\Tesseract-OCR` (tidak di PATH; set `TESSERACT_CMD` atau `ocr.tesseract_cmd`), dengan `ind.traineddata` dari tessdata_fast.
- Review lisensi terbuka: `certifi` (dependency `requests`/`httpx`) berlisensi MPL-2.0, copyleft lemah tingkat file; perlu dikonfirmasi diizinkan kebijakan lisensi.
- Review lisensi terbuka: wheel `opencv-python-headless` membawa DLL FFmpeg (LGPL-2.1) untuk video I/O yang tidak dipakai. Lisensi yang dideklarasikan Apache-2.0, tapi salah satu varian kebijakan perusahaan melarang LGPL. Perlu keputusan: terima, atau ganti preprocessing ke library lain.
- pytesseract menulis citra halaman ke file sementara di folder temp OS selama satu panggilan, lalu menghapusnya. Kalau proses crash di tengah panggilan, file bisa tertinggal; perlu dipertimbangkan untuk Docker (temp di tmpfs).
- Belum pernah dijalankan pada PDF asli, jadi ambang belum terkalibrasi.
- Perbandingan periode presisi bulan-tahun (baris "Mirip" di PRD) belum diimplementasikan, menunggu konfirmasi format masa kontrak di nama file.
- Risk level belum dicatat di PROJECT.md. Kemungkinan besar High karena memproses NIK dan data gaji; perlu `/assess-risk` oleh owner.
- PROJECT.md belum mendeskripsikan aliran data keluar (payload P_id ter-mask ke Anthropic atau Vertex AI). Field Design Overview, Data Used, dan Risk Level perlu diisi lewat `project-documenter` dan `/assess-risk` sebelum dipakai pada data asli.
- Uji asap lokal `sample/` (extractor mati): P_id 1 dan P_ttd 5 di keempat file; tanda tangan karyawan signed di kolom kanan untuk semua (A anotasi, B gambar, C tinta piksel dari scan, D anotasi), sesuai temuan struktur di PRD; tanggal selesai tidak ditemukan regex di scan C; sekitar 1 detik per file native, 15 detik untuk scan. Semua file `review_priority` karena extractor mati, dan jangkar memakai sebutan (nama file sampel nonstandar) sehingga margin tidak pernah jelas. Ini bukan pengukuran akurasi: tidak ada label dan tidak ada sampel unsigned.
- Ambang tanda tangan (rasio tinta, tinggi komponen, rasio kontur, ambang vektor) baru dilihat pada coretan sintetis dan empat sampel signed; kontrak unsigned asli, stempel berwarna, dan foto buram belum pernah dilihat detektor.
- Image Docker belum pernah di-build (lihat Key Decisions); verifikasi build dan isi image (pytest tidak ada, Tesseract jalan sebagai UID 10001) terjadi saat platform atau Apple Container mem-build-nya.
- Langkah berikutnya: uji extractor Gemini via Vertex AI menunggu project ID, region, dan id model dari owner (ADC sudah ada; tidak ada API key), serta keputusan owner untuk mengirim teks P_id ter-mask sampel asli ke cloud. Kalibrasi tanda tangan dan pengaktifan `allow_auto` menunggu test set Fase 0.
