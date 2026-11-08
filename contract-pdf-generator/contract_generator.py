"""Synthetic PKWT contract generator.

Generates 2-page synthetic Indonesian fixed-term employment contracts (Perjanjian Kerja Waktu Tertentu) as PDF,
with ground truth for what the contract OCR has to read:

    Field                       Ground-truth key                    Where it is
    Employee name               employee_name (exactly as printed)  page 1 identity block, page 2 signature block
    Employee NIK                employee_nik (16 digits, always     page 1 identity block (key-value row or
                                printed, as in real contracts)      "pemegang NIK ..." in a sentence)
    Employee position           position (always printed, as in     page 1: Jabatan/Posisi row, placement or duties
                                real contracts)                     clause, or the period clause
    Contract period             contract_start, contract_end (ISO)  page 1, inside one pasal
    Employee signature present  has_employee_signature              page 2, employee's signature block only
    ENTITAS                     entitas (or null when the filename  the filename only: <name> - <period> - <ENTITAS>
                                has none)                           (the company the employee works at, also the
                                                                    contract's Klien; the vendor is the outsourcing
                                                                    company that recruits and employs them)

The NIK follows the KTP layout (province, regency, district, birth date with +40 on the day for women, serial),
so it can be checked against the birth date and gender; meta.birth_date and meta.gender hold them when printed.
Every document randomises layout, wording, fonts, date formats, distractors (HR name, birth date, signing date,
HR signature, company stamp, paraf, meterai), signature style and form (digital PDF, scan, phone photo). Scans vary
in colour mode (colour, grey, 1-bit black and white), resolution and skew; phone photos in distance (paper share of
the frame), perspective, rotation, desk clutter and focus (sideways photos are off; see p_photo_turned). Photos stay within
what someone photographing a contract to upload would send: the paper filling most of the frame, at most 1.5
degrees of tilt and 2.5% perspective, in focus but for a few slightly soft ones, never upside down. Scans stay within what an
office scanner or copier gives: 150-300 dpi and at most 1.5 degrees of skew.
All personal data is fake (Faker id_ID); NIKs are random digits in the KTP layout.

Run it with the contract-ocr venv (from the contract-ocr folder):
    .venv/Scripts/python contract-pdf-generator/contract_generator.py
    .venv/Scripts/python contract-pdf-generator/contract_generator.py -n 200 --seed 7 --out D:/synthetic

Output folder (default: output/ next to this script; a previous run there is replaced):
    pdf/<name> - <period> - <ENTITAS>.pdf    (or "<name> <period> - <ENTITAS>.pdf", "<name> - <ENTITAS>.pdf" when
                                             the dash or the period is switched off in CONFIG)
    ground_truth/<same stem>.json
    ground_truth.json            all records in one array
    labels.csv                   the same truth in contract-extractor's label-template.csv format

Contracts come in vendor uploads (batch_size): each batch shares one vendor and one period, so
copy_to_onedrive.py can lay them out like the contract SharePoint, <vendor>/<Januari 2026 - Maret 2026>/<pdf>.

Score contract-extractor on it:
    contract-extractor run output/pdf --out results
    contract-extractor evaluate results/results.json output/labels.csv

Everything else is in CONFIG below. Fonts are downloaded once into fonts/ next to this script; without internet
the script still runs with built-in fonts and drawn signatures.
"""


# --------------------------------------------------------------------------------------------------------------
# Config
# Weights are relative (they don't need to sum to 1).
# --------------------------------------------------------------------------------------------------------------


CONFIG = {
    # --- run ---
    "n_docs": 50,
    "seed": 42,
    "output_dir": None,        # None -> output/ next to this script (or pass --out)
    "clean_output": True,      # replace a previous run in the output folder
    "zip_output": False,       # also write output.zip next to the output folder
    "download_fonts": True,    # fetch open fonts from Google Fonts on first run

    # --- contract period ---
    "date_range": ("2020-01-01", "2050-12-31"),   # start and end dates both fall inside this range
    "duration_months": {1: 0.05, 2: 0.05, 3: 0.30, 6: 0.30, 12: 0.20, 24: 0.10},
    "p_start_first_of_month": 0.70,
    "batch_size": (5, 15),     # contracts per vendor upload sharing one vendor and one period; None -> each its own

    # --- labels and variation mix ---
    "p_employee_signed": 0.70,
    "form_weights": {"digital": 0.40, "scan": 0.30, "photo": 0.30},
    "signature_style_weights": {"ink": 0.50, "script_font": 0.35, "esign": 0.15},
    "filename_style_weights": {"short_id": 0.55, "long_id": 0.20, "abbr_id": 0.10,
                               "english": 0.10, "name_only": 0},  # name_only has no period, so the extractor reads none
    "p_filename_dash": 1.0,    # "<name> - <period>" (contract-extractor's standard) instead of "<name> <period>"
    "p_filename_entitas": 1.0,  # "<name> - <period> - <ENTITAS>": production names always end in ENTITAS
                                # (owner, 2026-11-03); lower it to test names without one
    "entitas_style_weights": {"code": 0.6, "name": 0.4},  # ENTITAS written as "SNA" or as "PT Sinar Niaga Abadi"
    "body_date_style_weights": {"d_month_id": 0.30, "dd_month_id": 0.30, "dd-mm-yyyy": 0.10,
                                "dd/mm/yyyy": 0.10, "month_id_words": 0.07, "d_month_en": 0.08,
                                "dd_mon_id": 0.05},

    # --- identity block ---
    "p_identity_grid": 0.15,   # identity rows in a ruled table; OCR reads the borders as `|` or `I` (`INo.KTP`)
    "p_nik_grouped": 0.10,     # NIK row printed in groups of four, "3171 0123 4567 8901"

    # --- scans ---
    "scan_dpi": (150, 300),
    "scan_rotation_deg": 1.5,  # skew up to this much either way
    "scan_mode_weights": {"color": 0.35, "gray": 0.45, "bw": 0.20},  # bw: 1-bit black and white, as office copiers

    # --- phone photos ---
    "photo_fill": (0.86, 0.97),  # the paper's share of the frame; low means photographed from further away
    "photo_page_px": (2000, 3200),  # the paper's height in pixels in the final photo (8 to 12 MP phone cameras)
    "photo_keystone": 0.025,   # perspective: the far edge up to this much shorter than the near one, both ways
    "photo_rotation_deg": 1.5,
    # The whole photo saved sideways (90 or 270 degrees), as a phone held landscape. Off: the owner's contracts never
    # come in sideways (2026-11-04). Still drawn, so every other document stays the same for its seed.
    "p_photo_turned": 0.0,
    "p_photo_clutter": 0.40,   # a pen, a phone or another sheet beside the contract
    "p_photo_blurry": 0.03,    # slightly soft: focus a little off, still readable

    # --- labels.csv (contract-extractor evaluate) ---
    "n_vendors": 6,            # contracts share a small pool of employers (vendors); None -> a new company each time
    "n_entitas": 4,            # ENTITAS: companies the employees work at (the contract's Klien), shared by all vendors
    "p_other_entitas": 0.15,   # contracts in an upload placed at another entity than the upload's own
    "uat_fraction": 0.20,      # share of files labelled kelompok=uat, held out from threshold calibration
}


# --------------------------------------------------------------------------------------------------------------
# Imports and paths
# --------------------------------------------------------------------------------------------------------------


import argparse
import calendar
import csv
import datetime as dt
import io
import json
import math
import random
import re
import shutil
import statistics
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

import cv2
import numpy as np
import pypdfium2 as pdfium
from faker import Faker
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

SCRIPT_DIR = Path(__file__).resolve().parent
FONT_DIR = SCRIPT_DIR / "fonts"
PAGE_W, PAGE_H = A4


# --------------------------------------------------------------------------------------------------------------
# Fonts
# Body fonts need a regular and a bold file. Downloaded Google Fonts are open look-alikes of common office fonts;
# installed system fonts (Arial, Calibri, Liberation, DejaVu, ...) are picked up too. Helvetica and Times are
# always available as ReportLab built-ins. Script fonts are used for handwritten-looking signatures.
# --------------------------------------------------------------------------------------------------------------


GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/main/"
BODY_FONT_FILES = {  # family -> (regular, bold), paths in the google/fonts repo
    "Tinos": ("ofl/tinos/Tinos-Regular.ttf", "ofl/tinos/Tinos-Bold.ttf"),              # ~ Times New Roman
    "Carlito": ("ofl/carlito/Carlito-Regular.ttf", "ofl/carlito/Carlito-Bold.ttf"),    # ~ Calibri
    "Caladea": ("ofl/caladea/Caladea-Regular.ttf", "ofl/caladea/Caladea-Bold.ttf"),    # ~ Cambria
    "Lato": ("ofl/lato/Lato-Regular.ttf", "ofl/lato/Lato-Bold.ttf"),
    "PTSans": ("ofl/ptsans/PT_Sans-Web-Regular.ttf", "ofl/ptsans/PT_Sans-Web-Bold.ttf"),
    "PTSerif": ("ofl/ptserif/PT_Serif-Web-Regular.ttf", "ofl/ptserif/PT_Serif-Web-Bold.ttf"),
}
SYSTEM_BODY_FONTS = {  # family -> (regular, bold) file names, if installed
    "Arial": ("arial.ttf", "arialbd.ttf"), "Calibri": ("calibri.ttf", "calibrib.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf"), "Verdana": ("verdana.ttf", "verdanab.ttf"),
    "TimesNewRoman": ("times.ttf", "timesbd.ttf"), "Georgia": ("georgia.ttf", "georgiab.ttf"),
    "LiberationSans": ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    "LiberationSerif": ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf"),
    "DejaVuSans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
}
SCRIPT_FONT_FILES = [
    "ofl/greatvibes/GreatVibes-Regular.ttf", "ofl/allura/Allura-Regular.ttf",
    "ofl/alexbrush/AlexBrush-Regular.ttf", "ofl/sacramento/Sacramento-Regular.ttf",
    "ofl/mrdafoe/MrDafoe-Regular.ttf", "ofl/herrvonmuellerhoff/HerrVonMuellerhoff-Regular.ttf",
    "apache/homemadeapple/HomemadeApple-Regular.ttf", "apache/yellowtail/Yellowtail-Regular.ttf",
    "ofl/monsieurladoulaise/MonsieurLaDoulaise-Regular.ttf", "ofl/labelleaurore/LaBelleAurore.ttf",
    "ofl/meddon/Meddon.ttf", "ofl/dancingscript/DancingScript[wght].ttf",
    "ofl/qwigley/Qwigley-Regular.ttf", "ofl/missfajardose/MissFajardose-Regular.ttf",
    "ofl/rougescript/RougeScript-Regular.ttf",
]


def download_font(rel_path):
    dest = FONT_DIR / Path(rel_path).name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    url = GOOGLE_FONTS + rel_path.replace("[", "%5B").replace("]", "%5D")
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
    except Exception as exc:
        print(f"  could not download {Path(rel_path).name}: {exc}")
        return None
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def index_font_files():
    dirs = [FONT_DIR, Path("C:/Windows/Fonts"), Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
            Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
            Path("/Library/Fonts")]
    try:
        import matplotlib
        dirs.append(Path(matplotlib.get_data_path()) / "fonts/ttf")
    except ImportError:
        pass
    index = {}
    for d in dirs:
        if d.is_dir():
            for p in d.rglob("*.ttf"):
                index.setdefault(p.name.lower(), p)
    return index


BODY_FONTS = {"Helvetica": ("Helvetica", "Helvetica-Bold"), "Times": ("Times-Roman", "Times-Bold")}
FONT_INDEX, SCRIPT_FONTS, _PIL_REGULAR, _PIL_BOLD = {}, [], [], []  # filled by setup_fonts()


def setup_fonts():
    """Download the open fonts (once), register body fonts with ReportLab, and find the signature fonts."""
    global FONT_INDEX, SCRIPT_FONTS, _PIL_REGULAR, _PIL_BOLD
    if CONFIG["download_fonts"]:
        for reg, bold in BODY_FONT_FILES.values():
            download_font(reg), download_font(bold)
        for p in SCRIPT_FONT_FILES:
            download_font(p)
    FONT_INDEX = index_font_files()

    candidates = {fam: (Path(r).name, Path(b).name) for fam, (r, b) in BODY_FONT_FILES.items()}
    candidates.update(SYSTEM_BODY_FONTS)
    for fam, (r, b) in candidates.items():
        rp, bp = FONT_INDEX.get(r.lower()), FONT_INDEX.get(b.lower())
        if not (rp and bp):
            continue
        try:
            pdfmetrics.registerFont(TTFont(fam, str(rp)))
            pdfmetrics.registerFont(TTFont(fam + "-Bold", str(bp)))
            pdfmetrics.registerFontFamily(fam, normal=fam, bold=fam + "-Bold", italic=fam, boldItalic=fam + "-Bold")
            BODY_FONTS[fam] = (fam, fam + "-Bold")
        except Exception as exc:
            print(f"  skip font {fam}: {exc}")

    SCRIPT_FONTS = [FONT_INDEX[Path(p).name.lower()] for p in SCRIPT_FONT_FILES
                    if Path(p).name.lower() in FONT_INDEX]
    _PIL_REGULAR = [n for n in ("arial.ttf", "liberationsans-regular.ttf", "dejavusans.ttf",
                                "carlito-regular.ttf", "lato-regular.ttf") if n in FONT_INDEX]
    _PIL_BOLD = [n for n in ("arialbd.ttf", "liberationsans-bold.ttf", "dejavusans-bold.ttf",
                             "carlito-bold.ttf", "lato-bold.ttf") if n in FONT_INDEX]
    print(f"Body fonts ({len(BODY_FONTS)}): {', '.join(sorted(BODY_FONTS))}")
    print(f"Signature fonts: {len(SCRIPT_FONTS)}")


def pil_font(size, bold=False):
    names = _PIL_BOLD if bold else _PIL_REGULAR
    if names:
        return ImageFont.truetype(str(FONT_INDEX[names[0]]), int(size))
    return ImageFont.load_default(size=int(size))


# --------------------------------------------------------------------------------------------------------------
# Helpers: randomness, dates, Indonesian number words
# --------------------------------------------------------------------------------------------------------------


BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]
BULAN_ABBR = ["jan", "feb", "mar", "apr", "mei", "jun", "jul", "agu", "sep", "okt", "nov", "des"]
MONTH_EN = ["January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December"]
MONTH_EN_ABBR = [m[:3].lower() for m in MONTH_EN]
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
DATE_STYLES = ["d_month_id", "dd_month_id", "dd-mm-yyyy", "dd/mm/yyyy", "month_id_words",
               "d_month_en", "dd_mon_id"]


def wchoice(rng, weights):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def chance(rng, p):
    return rng.random() < p


def np_rng(rng):
    return np.random.default_rng(rng.getrandbits(32))


def terbilang(n):
    """Indonesian number words, e.g. 2024 -> 'dua ribu dua puluh empat'."""
    units = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan",
             "sepuluh", "sebelas"]

    def join(head, rest):
        return head + (" " + terbilang(rest) if rest else "")

    if n < 12:
        return units[n]
    if n < 20:
        return terbilang(n - 10) + " belas"
    if n < 100:
        return join(terbilang(n // 10) + " puluh", n % 10)
    if n < 200:
        return join("seratus", n - 100)
    if n < 1000:
        return join(terbilang(n // 100) + " ratus", n % 100)
    if n < 2000:
        return join("seribu", n - 1000)
    if n < 1_000_000:
        return join(terbilang(n // 1000) + " ribu", n % 1000)
    return join(terbilang(n // 1_000_000) + " juta", n % 1_000_000)


def title_words(s):
    return " ".join(w.capitalize() for w in s.split())


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def fmt_date(d, style):
    if style == "d_month_id":
        return f"{d.day} {BULAN[d.month - 1]} {d.year}"
    if style == "dd_month_id":
        return f"{d.day:02d} {BULAN[d.month - 1]} {d.year}"
    if style == "dd-mm-yyyy":
        return f"{d.day:02d}-{d.month:02d}-{d.year}"
    if style == "dd/mm/yyyy":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "month_id_words":
        return (f"{d.day} {BULAN[d.month - 1]} {d.year} "
                f"({terbilang(d.day)} {BULAN[d.month - 1].lower()} {terbilang(d.year)})")
    if style == "d_month_en":
        return f"{d.day} {MONTH_EN[d.month - 1]} {d.year}"
    if style == "dd_mon_id":
        return f"{d.day:02d} {BULAN_ABBR[d.month - 1].capitalize()} {d.year}"
    raise ValueError(style)


def rupiah(n):
    return f"{n:,}".replace(",", ".")


# --------------------------------------------------------------------------------------------------------------
# Fake contract data
# --------------------------------------------------------------------------------------------------------------


fake = Faker("id_ID")

ROLES = ["Frontliner", "Store Leader", "Admin QC Return", "Kasir", "Staff Gudang", "Customer Service",
         "Sales Promotor", "Driver", "Admin Gudang", "Picker", "Packer", "Helper", "Staff Administrasi",
         "Teknisi", "Operator Produksi", "Telemarketing", "Data Entry", "Kurir", "Admin Inbound",
         "Staff Outbound", "Merchandiser", "Customer Care Agent", "Admin Payroll", "Checker"]
HR_TITLES = ["HRD dan Hukum", "Senior Manager HRD", "HR Manager", "Manajer Personalia",
             "Kepala Divisi Human Capital", "Direktur", "Direktur Operasional", "HR & GA Manager",
             "HR Business Partner"]
TITLE_SUFFIX = ["S.Kom", "S.E.", "S.H.", "S.T.", "S.Pd.", "S.Sos.", "A.Md.", "S.I.Kom", "M.M.", "S.Ak."]
MUSLIM_PREFIX = ["Muhammad", "Muhamad", "Mohammad", "Moch.", "Muh.", "M.", "Moh."]
ABD_NAMES = ["Rahman", "Rahim", "Aziz", "Malik", "Karim", "Latif", "Gani", "Halim", "Hakim", "Wahab",
             "Rasyid", "Majid"]
CO_A = ["Mitra", "Karya", "Sinar", "Prima", "Global", "Nusa", "Cipta", "Bina", "Graha", "Daya", "Tunas",
        "Bumi", "Mega", "Surya", "Artha", "Indo", "Tri", "Panca", "Sarana", "Putra", "Duta", "Inti"]
CO_B = ["Teknindo", "Ekasarana", "Persada", "Sejahtera", "Mandiri", "Utama", "Abadi", "Sentosa", "Makmur",
        "Perkasa", "Solusi", "Jaya", "Lestari", "Nusantara", "Semesta", "Kreasi", "Sinergi", "Gemilang",
        "Talenta", "Dinamika"]
CO_C = ["Indonesia", "Services", "Outsourcing", "Resources", "Group", "Internasional"]
BUILDINGS = ["Gedung {} Tower", "Gd. {} Plaza", "Menara {}", "Wisma {}", "{} Office Tower", "Graha {}"]
HQ_CITIES = ["Jakarta Selatan", "Jakarta Pusat", "Jakarta Barat", "Jakarta Utara", "Tangerang", "Bekasi",
             "Bandung", "Surabaya", "Semarang", "Medan", "Makassar", "Bogor", "Depok", "Yogyakarta"]
PROVINCE_CODES = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 31, 32, 33, 34, 35, 36, 51, 52, 53, 61, 62,
                  63, 64, 65, 71, 72, 73, 74, 75, 76, 81, 82, 91, 92, 94]
PHONE_PREFIX = ["0811", "0812", "0813", "0821", "0822", "0852", "0853", "0851", "0857", "0856", "0858",
                "0877", "0878", "0817", "0818", "0819", "0859", "0895", "0896", "0897", "0898", "0881"]


def fake_city():
    return re.sub(r"^Kota Administrasi ", "", fake.city())


def person_name_parts(rng, gender):
    first = fake.first_name_male if gender == "L" else fake.first_name_female
    last = fake.last_name_male if gender == "L" else fake.last_name_female
    target = wchoice(rng, {1: 0.08, 2: 0.55, 3: 0.30, 4: 0.07})
    parts = [first()]
    for _ in range(10):
        if len(parts) >= target:
            break
        nxt = last() if chance(rng, 0.6) else first()
        if nxt not in parts:
            parts.append(nxt)
    return parts


def make_employee_name(rng, gender):
    parts = person_name_parts(rng, gender)
    if gender == "L" and chance(rng, 0.10):
        parts = [rng.choice(MUSLIM_PREFIX)] + parts[:3]
    elif gender == "L" and chance(rng, 0.05):
        abd = rng.choice(ABD_NAMES)
        parts = [rng.choice([f"Abd.{abd.lower()}", f"Abd. {abd}", f"Abdul {abd}"])] + parts[1:3]
    name = " ".join(parts)
    if chance(rng, 0.08):
        name += ", " + rng.choice(TITLE_SUFFIX)
    if chance(rng, 0.15):
        name = name.upper()
    return name


def make_nik(rng, birth, gender):
    day = birth.day + (40 if gender == "P" else 0)
    return (f"{rng.choice(PROVINCE_CODES):02d}{rng.randint(1, 79):02d}{rng.randint(1, 40):02d}"
            f"{day:02d}{birth.month:02d}{birth.year % 100:02d}{rng.randint(1, 999):04d}")


def make_company(rng):
    words = [rng.choice(CO_A), rng.choice(CO_B)] + ([rng.choice(CO_C)] if chance(rng, 0.25) else [])
    name = f"{rng.choice(['PT', 'PT', 'PT.'])} {' '.join(words)}"
    acronym = "".join(w[0] for w in words).upper()
    if len(acronym) < 3:
        acronym = (words[0][:2] + words[1][0]).upper()
    return name, acronym, words[0].upper()


def make_contract_no(rng, sign, company_acr, client_acr):
    seq, r, y = rng.randint(1, 9999), ROMAN[sign.month - 1], sign.year
    cl = client_acr or rng.choice(["HO", "OPS", "JKT", "SBY"])
    return rng.choice([
        f"{seq:04d}/PKWT/{company_acr}-HR/{cl}/{r}/{y}",
        f"{seq}/K{rng.randint(1, 3)}/{company_acr}-HR-Admin/{cl}/{r}/{y}",
        f"{seq:03d}/{company_acr}/PKWT/{r}/{y}",
        f"{company_acr}/HRD/PKWT/{y}/{seq:05d}",
        f"PKWT-{y}{sign.month:02d}-{seq:04d}",
    ])


def make_vendor(rng):
    """One employer (vendor) with a fixed name and office address."""
    company, company_acr, company_short = make_company(rng)
    city = rng.choice(HQ_CITIES)
    building = rng.choice(BUILDINGS).format(rng.choice(CO_A + CO_B))
    address = f"{building} Lantai {rng.randint(1, 30)}, {fake.street_address()}, {city} {fake.postcode()}"
    return {"company": company, "company_acr": company_acr, "company_short": company_short,
            "company_address": address, "company_city": city}


def make_vendor_pool(n, seed):
    rng = random.Random(seed)
    fake.seed_instance(seed)
    pool = {}
    for _ in range(n * 50):
        if len(pool) == n:
            break
        v = make_vendor(rng)
        pool.setdefault(v["company"], v)
    return list(pool.values())


VENDORS = []  # filled by main() once the seed is known


def make_entitas_pool(n, seed, taken):
    """Placement entities (ENTITAS): fictional companies with a name and a short code, none sharing a vendor's name."""
    rng = random.Random(seed * 31 + 5)
    pool, codes = [], set()
    for _ in range(n * 50):
        if len(pool) == n:
            break
        name, acr, _ = make_company(rng)
        name = name.replace("PT. ", "PT ")
        if name not in taken and acr not in codes:
            pool.append({"name": name, "code": acr})
            codes.add(acr)
            taken.add(name)
    return pool


ENTITAS = []  # filled by main() once the seed is known


def pick_entitas(rng, batch):
    """The upload's entity, now and then another one; a fresh company when the pool is empty."""
    if batch and batch.get("entitas") and not chance(rng, CONFIG["p_other_entitas"]):
        return batch["entitas"]
    if ENTITAS:
        return rng.choice(ENTITAS)
    name, acr, _ = make_company(rng)
    return {"name": name, "code": acr}


def sample_period(rng):
    """A random contract period inside date_range: (start, months, end)."""
    d0, d1 = (dt.date.fromisoformat(s) for s in CONFIG["date_range"])
    months = wchoice(rng, CONFIG["duration_months"])
    latest_start = add_months(d1 + dt.timedelta(days=1), -months)  # keeps the end date inside the range
    start = d0 + dt.timedelta(days=rng.randint(0, max((latest_start - d0).days, 0)))
    if chance(rng, CONFIG["p_start_first_of_month"]):
        start = max(start.replace(day=1), d0)
    return start, months, add_months(start, months) - dt.timedelta(days=1)


def period_folder(start, end):
    """The SharePoint folder name for a contract period, e.g. `Januari 2026 - Maret 2026`."""
    return f"{BULAN[start.month - 1]} {start.year} - {BULAN[end.month - 1]} {end.year}"


def plan_batches(rng, n_docs):
    """One entry per contract: the vendor upload (batch) it belongs to, or None when batches are off."""
    if not CONFIG["batch_size"]:
        return [None] * n_docs
    lo, hi = CONFIG["batch_size"]
    plan = []
    while len(plan) < n_docs:
        vendor = rng.choice(VENDORS) if VENDORS else make_vendor(rng)
        batch = {"index": plan[-1]["index"] + 1 if plan else 0, "vendor": vendor, "period": sample_period(rng),
                 "entitas": rng.choice(ENTITAS) if ENTITAS else None}
        plan += [batch] * rng.randint(lo, hi)
    return plan[:n_docs]


def sample_contract(rng, batch=None):
    gender = rng.choice("LP")
    start, months, end = batch["period"] if batch else sample_period(rng)
    sign = start - dt.timedelta(days=rng.choice([0, 0, 1, 2, 3, 5, 7, 10, 14]))
    if sign.weekday() == 6 or (sign.weekday() == 5 and chance(rng, 0.7)):  # contracts are rarely signed on weekends
        sign -= dt.timedelta(days=sign.weekday() - rng.randint(1, 4))  # back to Tuesday..Friday
    birth = start - dt.timedelta(days=rng.randint(18 * 365, 55 * 365))

    vendor = batch["vendor"] if batch else (rng.choice(VENDORS) if VENDORS else make_vendor(rng))
    hq_city = vendor["company_city"]
    entitas = pick_entitas(rng, batch)
    # The contract names the entity as its Klien in most uploads; the filename always can.
    client, client_acr = (entitas["name"], entitas["code"]) if chance(rng, 0.6) else (None, None)
    rt, rw = rng.randint(1, 20), rng.randint(1, 30)
    address = rng.choice([
        f"{fake.street_address()} RT {rt:03d}/RW {rw:03d}, {fake_city()}",
        f"{fake.street_address()} Rt {rt:03d} Rw {rw:03d}",
        f"{fake.street_address()}, RT.{rt:02d}/RW.{rw:02d}, Kel. {fake.last_name()}, {fake_city()}",
    ])
    hr_gender = rng.choice("LP")
    return {
        "gender": gender,
        "employee_name": make_employee_name(rng, gender),
        "birth_place": fake_city(),
        "birth_date": birth,
        "nik": make_nik(rng, birth, gender),
        "address": address,
        "phone": rng.choice(PHONE_PREFIX) + "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(6, 8))),
        "role": rng.choice(ROLES),
        **vendor,
        "client": client, "client_acr": client_acr, "entitas": entitas,
        "hr_name": " ".join(person_name_parts(rng, hr_gender)[:3]),
        "hr_title": rng.choice(HR_TITLES),
        "start": start, "end": end, "months": months,
        "sign_date": sign, "sign_city": hq_city.split()[0] if hq_city.startswith("Jakarta") else hq_city,
        "contract_no": make_contract_no(rng, sign, vendor["company_acr"], client_acr),
        "salary": rng.randrange(2_500_000, 9_500_000, 50),
    }


# --------------------------------------------------------------------------------------------------------------
# Contract wording
# Each clause builder returns `{"title", "items", "numbered"}`. An item is a string or `(string, [sub-items])`.
# Text is ReportLab paragraph markup, so data values are escaped with `X()`.
# --------------------------------------------------------------------------------------------------------------


X = escape
PARTY_LABELS = [
    {"co": "PIHAK PERTAMA", "emp": "PIHAK KEDUA", "emp_title": "Karyawan"},
    {"co": "PERUSAHAAN", "emp": "KARYAWAN", "emp_title": "Karyawan"},
    {"co": "PERUSAHAAN", "emp": "TENAGA KERJA", "emp_title": "Tenaga Kerja"},
    {"co": "PENGUSAHA", "emp": "PEKERJA", "emp_title": "Pekerja"},
    {"co": "{short}", "emp": "TENAGA KERJA", "emp_title": "Tenaga Kerja"},
]
TITLES = ["PERJANJIAN KERJA WAKTU TERTENTU", "PERJANJIAN KERJA JANGKA WAKTU TERTENTU",
          "PERJANJIAN KERJA WAKTU TERTENTU (PKWT)", "KONTRAK KERJA KARYAWAN", "SURAT PERJANJIAN KERJA"]


def _client_suffix(d, text=" dan/atau Klien"):
    return text if d["client"] else ""


def c_definisi(d, L, rng):
    sub = [
        f"<b>Perjanjian</b> adalah Perjanjian Kerja Waktu Tertentu (PKWT) yang dibuat antara {L['co']} dan "
        f"{L['emp']} untuk jangka waktu tertentu.",
        f"<b>{L['co']}</b> adalah {X(d['company'])} selaku pemberi kerja.",
        f"<b>{L['emp']}</b> adalah pekerja {L['co']} yang ditempatkan pada fungsi, bagian, atau lokasi kerja "
        f"yang ditetapkan oleh {L['co']}.",
    ]
    if d["client"]:
        sub.append(f"<b>Klien</b> adalah perusahaan pengguna jasa tempat {L['emp']} ditempatkan, "
                   f"yaitu {X(d['client'])}.")
    return {"title": rng.choice(["ISTILAH DAN DEFINISI", "DEFINISI", "PENGERTIAN"]), "items": [
        ("Kecuali ditentukan lain secara tegas, istilah-istilah dalam Perjanjian ini memiliki arti "
         "sebagai berikut:", sub),
        "Judul pasal dalam Perjanjian ini hanya dimaksudkan untuk memudahkan pembacaan dan tidak "
        "mempengaruhi penafsiran isi Perjanjian.",
    ]}


def c_penempatan(d, L, rng):
    placed = f" yang ditempatkan pada {X(d['client'])}" if d["client"] else ""
    return {"title": rng.choice(["PENEMPATAN", "JABATAN DAN PENEMPATAN", "LINGKUP PEKERJAAN"]), "items": [
        f"{L['co']} mempekerjakan {L['emp']} sebagai <b>{X(d['role'])}</b>{placed}.",
        f"{L['emp']} bersedia ditempatkan maupun dipindahtugaskan ke bagian atau lokasi kerja lain sesuai "
        f"kebutuhan operasional {L['co']}{_client_suffix(d)}.",
        f"Selama Perjanjian ini berlangsung, {L['emp']} wajib mematuhi peraturan perusahaan, tata tertib, "
        "serta standar prosedur kerja yang berlaku.",
    ]}


def c_tugas(d, L, rng):
    return {"title": rng.choice(["TUGAS DAN TANGGUNG JAWAB", f"KEWAJIBAN {L['emp']}"]), "items": [
        f"{L['emp']} wajib melaksanakan tugas sebagai {X(d['role'])} dengan penuh tanggung jawab serta tugas "
        "lain yang diberikan oleh atasan.",
        f"{L['emp']} wajib mencurahkan waktu dan kemampuannya untuk kepentingan pekerjaan dan tidak menerima "
        f"pekerjaan lain yang bertentangan dengan kepentingan {L['co']} tanpa izin tertulis.",
        f"{L['emp']} wajib menjaga dan memelihara barang milik {L['co']}{_client_suffix(d, ' maupun Klien')} "
        "yang dipercayakan kepadanya.",
        f"Hari dan jam kerja {L['emp']} mengikuti ketentuan yang berlaku di lokasi penempatan.",
    ]}


def c_persyaratan(d, L, rng):
    return {"title": rng.choice(["PERSYARATAN PEKERJAAN", "EVALUASI KINERJA", "PENILAIAN KINERJA"]), "items": [
        f"{L['emp']} bersedia dievaluasi kinerjanya secara berkala berdasarkan target yang telah ditetapkan.",
        f"Apabila {L['emp']} tidak memenuhi target yang ditetapkan, {L['co']} berhak memberikan Surat "
        "Peringatan pertama, kedua, dan ketiga sesuai peraturan yang berlaku.",
        f"{L['emp']} bersedia menerima jadwal kerja yang ditetapkan oleh {L['co']}{_client_suffix(d)}.",
    ]}


def c_waktu_kerja(d, L, rng):
    hours = rng.choice(["40 (empat puluh) jam dalam 1 (satu) minggu",
                        "7 (tujuh) jam per hari untuk 6 (enam) hari kerja",
                        "8 (delapan) jam per hari untuk 5 (lima) hari kerja"])
    return {"title": rng.choice(["WAKTU KERJA", "HARI DAN JAM KERJA"]), "items": [
        f"Waktu kerja {L['emp']} adalah {hours} sesuai jadwal yang ditetapkan {L['co']}.",
        "Kelebihan jam kerja diperhitungkan sebagai lembur sesuai ketentuan peraturan perundang-undangan.",
        f"Dalam hal tidak masuk kerja karena sakit, {L['emp']} wajib menyerahkan surat keterangan dokter.",
    ]}


def c_upah(d, L, rng):
    meal = rng.randrange(15_000, 40_000, 1000)
    return {"title": rng.choice(["UPAH", "UPAH DAN TUNJANGAN", "KOMPENSASI DAN BENEFIT"]), "items": [
        (f"{L['co']} memberikan upah kepada {L['emp']} dengan rincian sebagai berikut:", [
            f"Gaji pokok sebesar Rp {rupiah(d['salary'])},- per bulan;",
            f"Tunjangan makan sebesar Rp {rupiah(meal)},- per kehadiran;",
            "Tunjangan Hari Raya sesuai ketentuan yang berlaku.",
        ]),
        f"Upah dibayarkan setiap tanggal {rng.choice([25, 27, 28])} setiap bulan melalui transfer ke rekening "
        f"atas nama {L['emp']}.",
        "Upah dikenakan potongan iuran BPJS Kesehatan, BPJS Ketenagakerjaan, dan PPh Pasal 21 sesuai "
        "ketentuan yang berlaku.",
    ]}


def c_period(d, L, rng, lay):
    s, e = fmt_date(d["start"], lay["date_style"]), fmt_date(d["end"], lay["date_style"])
    if lay["bold_dates"]:
        s, e = f"<b>{s}</b>", f"<b>{e}</b>"
    n = f"{d['months']} ({terbilang(d['months'])})"
    co, emp, pid = L["co"], L["emp"], lay["period_phrase"]
    if pid == 0:
        return {"title": rng.choice(["JANGKA WAKTU", "JANGKA WAKTU PERJANJIAN"]), "items": [
            (f"Perjanjian ini berlaku efektif mulai tanggal {s} sampai dengan {e}, kecuali:", [
                f"{emp} mengundurkan diri dari {co};",
                f"{emp} diberhentikan oleh {co} karena melakukan pelanggaran sebagaimana diatur dalam "
                "Perjanjian ini;",
                f"{emp} diangkat sebagai karyawan tetap{_client_suffix(d, ' oleh Klien')} dengan "
                "pemberitahuan sebelumnya.",
            ]),
            f"Selama Perjanjian berlangsung, kinerja {emp} akan dievaluasi dan hasil evaluasi tersebut "
            "menjadi dasar kelanjutan hubungan kerja.",
        ]}
    if pid == 1:  # role and period in one clause
        placed = f" yang ditempatkan di {X(d['client'])}" if d["client"] else ""
        return {"title": rng.choice(["PEKERJAAN", "PEKERJAAN DAN JANGKA WAKTU"]), "items": [
            f"{co} memberikan pekerjaan kepada {emp} sebagai <b>{X(d['role'])}</b>{placed}, dimulai sejak "
            f"tanggal {s} dan akan berakhir dengan sendirinya pada tanggal {e} tanpa suatu syarat apapun.",
            f"{emp} bersedia menerima pekerjaan yang diberikan {co} dan mematuhi seluruh peraturan serta "
            "ketentuan yang berlaku.",
            f"Dalam menjalankan pekerjaannya, {emp} bersedia sewaktu-waktu dipindahtugaskan ke tempat lain "
            f"yang ditentukan oleh {co}.",
        ]}
    if pid == 2:
        return {"title": "JANGKA WAKTU", "items": [
            f"Perjanjian ini dibuat untuk jangka waktu {n} bulan, terhitung sejak tanggal {s} hingga "
            f"tanggal {e}.",
            "Perjanjian ini dapat diperpanjang atau diperbaharui atas kesepakatan tertulis PARA PIHAK sesuai "
            "peraturan perundang-undangan yang berlaku.",
        ]}
    if pid == 3:
        return {"title": rng.choice(["MASA BERLAKU PERJANJIAN", "MASA KONTRAK"]), "items": [
            f"Masa berlaku Perjanjian Kerja Waktu Tertentu ini adalah terhitung mulai tanggal {s} sampai "
            f"dengan tanggal {e}.",
            "Perjanjian ini berakhir dengan sendirinya pada tanggal sebagaimana dimaksud pada ayat (1) tanpa "
            f"memerlukan pemberitahuan terlebih dahulu dari {co}.",
        ]}
    if pid == 4:
        return {"title": "JANGKA WAKTU PERJANJIAN", "items": [
            f"Jangka waktu Perjanjian ini adalah selama {n} bulan, yaitu mulai tanggal {s} s.d. {e}.",
            f"Selambat-lambatnya 7 (tujuh) hari sebelum Perjanjian berakhir, {co} akan memberitahukan kepada "
            f"{emp} mengenai perpanjangan atau berakhirnya Perjanjian.",
        ]}
    return {"title": rng.choice(["PERIODE KERJA", "JANGKA WAKTU"]), "items": [
        f"Hubungan kerja berdasarkan Perjanjian ini berlangsung untuk periode {s} - {e}.",
        "Perpanjangan hubungan kerja hanya dapat dilakukan berdasarkan kesepakatan tertulis PARA PIHAK.",
    ]}


def c_kerahasiaan(d, L, rng):
    return {"title": rng.choice(["KERAHASIAAN", "INFORMASI RAHASIA"]), "items": [
        f"{L['emp']} wajib menjaga kerahasiaan seluruh informasi, data, dan dokumen milik "
        f"{L['co']}{_client_suffix(d)} yang diperoleh selama masa kerja.",
        "Kewajiban menjaga kerahasiaan tetap berlaku meskipun Perjanjian ini telah berakhir, dan "
        "pelanggaran atas ketentuan ini dapat diproses sesuai hukum yang berlaku.",
    ]}


def c_perselisihan(d, L, rng):
    city = d["sign_city"]
    forum = rng.choice(["Pengadilan Hubungan Industrial", f"Pengadilan Negeri {city}",
                        f"Pengadilan Hubungan Industrial pada Pengadilan Negeri {city}"])
    return {"title": rng.choice(["PENYELESAIAN PERSELISIHAN", "PERSELISIHAN"]), "items": [
        "Perselisihan yang timbul dari pelaksanaan Perjanjian ini akan diselesaikan terlebih dahulu secara "
        "musyawarah untuk mufakat.",
        f"Apabila musyawarah tidak mencapai kesepakatan, PARA PIHAK sepakat menyelesaikannya melalui {forum}.",
    ]}


def c_force_majeure(d, L, rng):
    return {"title": rng.choice(["KEADAAN MEMAKSA", "FORCE MAJEURE"]), "numbered": False, "items": [
        "Masing-masing pihak dibebaskan dari tanggung jawab atas keterlambatan atau kegagalan melaksanakan "
        "kewajiban yang disebabkan oleh keadaan di luar kendali PARA PIHAK, termasuk namun tidak terbatas "
        "pada bencana alam, kebakaran, banjir, epidemi, huru-hara, perang, atau perubahan peraturan pemerintah.",
    ]}


def c_hukum(d, L, rng):
    return {"title": rng.choice(["HUKUM YANG BERLAKU", "UNDANG-UNDANG YANG BERLAKU"]), "items": [
        "Perjanjian ini dibuat dan tunduk pada peraturan perundang-undangan ketenagakerjaan yang berlaku di "
        "Negara Republik Indonesia.",
        "Hal-hal yang belum diatur dalam Perjanjian ini mengacu pada Peraturan Perusahaan dan peraturan "
        "perundang-undangan yang berlaku.",
    ]}


def c_lain(d, L, rng):
    return {"title": rng.choice(["LAIN-LAIN", "ADDENDUM", "ADDENDUM/AMANDEMEN"]), "numbered": False, "items": [
        "Hal-hal yang belum diatur dalam Perjanjian ini akan diatur kemudian dalam addendum yang disepakati "
        "secara tertulis oleh PARA PIHAK dan merupakan bagian yang tidak terpisahkan dari Perjanjian ini.",
    ]}


def c_phk(d, L, rng):
    notice = rng.choice(["30 (tiga puluh) hari", "1 (satu) bulan", "14 (empat belas) hari"])
    return {"title": rng.choice(["PEMUTUSAN HUBUNGAN KERJA", "BERAKHIRNYA PERJANJIAN"]), "items": [
        (f"{L['co']} dapat mengakhiri Perjanjian ini sebelum jangka waktunya berakhir apabila {L['emp']}:", [
            "melakukan pelanggaran berat terhadap peraturan perusahaan;",
            "tidak masuk kerja selama 5 (lima) hari berturut-turut tanpa keterangan yang sah;",
            "tidak memenuhi target kinerja setelah diberikan surat peringatan ketiga.",
        ]),
        f"Apabila {L['emp']} mengundurkan diri, {L['emp']} wajib mengajukan permohonan secara tertulis "
        f"paling lambat {notice} sebelumnya.",
    ]}


def c_penutup(d, L, rng):
    stamp = rng.choice([" bermeterai cukup", "", " di atas meterai"])
    return {"title": "PENUTUP", "items": [
        "PARA PIHAK menyatakan telah membaca, memahami, dan menyetujui seluruh isi Perjanjian ini.",
        f"Perjanjian ini dibuat dalam rangkap 2 (dua){stamp}, masing-masing mempunyai kekuatan hukum yang sama, "
        "dan ditandatangani oleh PARA PIHAK dalam keadaan sadar tanpa paksaan dari pihak manapun.",
    ]}


# --------------------------------------------------------------------------------------------------------------
# Graphics: signatures, paraf, company stamp, meterai, logo
# All drawn as transparent PNGs with PIL, then placed on the page.
# --------------------------------------------------------------------------------------------------------------


INK = {"black": [(15, 15, 20), (30, 30, 38), (5, 5, 12)],
       "blue": [(20, 40, 150), (25, 35, 110), (10, 60, 170), (35, 45, 125)]}
STAMP_COLORS = [(40, 60, 170), (90, 40, 150), (170, 30, 45), (30, 90, 160)]


def pick_ink(rng):
    base = rng.choice(INK[rng.choice(["black", "blue"])])
    return tuple(int(np.clip(c + rng.randint(-12, 12), 0, 255)) for c in base)


def catmull_rom(points, n=16):
    p = np.asarray(points, float)
    p = np.vstack([p[0], p, p[-1]])
    t = np.linspace(0, 1, n, endpoint=False)[:, None]
    out = [0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)
           for p0, p1, p2, p3 in zip(p[:-3], p[1:-2], p[2:-1], p[3:])]
    return np.vstack(out + [p[-2][None]])


def smooth_noise(nrng, h, w, cells=8):
    gh, gw = max(2, int(cells * h / max(h, w)) + 1), max(2, int(cells * w / max(h, w)) + 1)
    return cv2.resize(nrng.random((gh, gw)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)


def procedural_strokes(rng, n_letters, flourish=True):
    """Cursive-looking scribble: trochoid loops + capital loop + optional flourish, in unit coordinates."""
    loops = max(1, n_letters)
    t = np.linspace(0, 2 * np.pi * loops, 60 * loops)
    knots = np.linspace(t[0], t[-1], loops + 2)
    heights = [rng.uniform(0.08, 0.32) for _ in knots]
    if chance(rng, 0.6):
        heights[rng.randrange(1, len(heights))] = rng.uniform(0.45, 0.7)  # an ascender
    ay = np.interp(t, knots, heights)
    v, ax = rng.uniform(0.026, 0.038), rng.uniform(0.035, 0.07)
    x = v * t - ax * np.sin(t)
    y = ay * np.cos(t) + rng.uniform(-0.08, 0.08) * t / t[-1]
    main = np.column_stack([x, y])
    if chance(rng, 0.8):
        h = rng.uniform(0.5, 0.95)
        cap = np.array([[-0.02, -0.1], [0.0, h * 0.6], [0.06, h], [0.13, h * 0.75], [0.05, h * 0.25],
                        [-0.04, 0.0], main[0]])
        cap[:-1] += np.array([[rng.uniform(-0.02, 0.02), rng.uniform(-0.04, 0.04)] for _ in cap[:-1]])
        main = np.vstack([catmull_rom(cap, 14), main])
    strokes = [main]
    if chance(rng, 0.35) and len(main) > 100:
        k = rng.randrange(40, len(main) - 40)
        strokes = [main[:k], main[k + rng.randrange(3, 8):]]
    end = strokes[-1][-1]
    if flourish and chance(rng, 0.6):
        kind = rng.choice(["under", "through", "tail"])
        if kind == "under":
            pts = [end, (end[0] + rng.uniform(0.02, 0.1), -0.25), (end[0] * 0.5, -rng.uniform(0.3, 0.5)),
                   (-rng.uniform(0, 0.1), -rng.uniform(0.15, 0.35))]
            strokes[-1] = np.vstack([strokes[-1], catmull_rom(pts, 24)])
        elif kind == "through":
            pts = [(-0.05, rng.uniform(-0.1, 0.1)), (end[0] * 0.5, rng.uniform(-0.05, 0.15)),
                   (end[0] + 0.1, rng.uniform(0, 0.2))]
            strokes.append(catmull_rom(pts, 24))
        else:
            pts = [end, (end[0] + rng.uniform(0.05, 0.15), end[1] + rng.uniform(0.1, 0.3)),
                   (end[0] + rng.uniform(0.15, 0.3), end[1] + rng.uniform(-0.1, 0.4))]
            strokes[-1] = np.vstack([strokes[-1], catmull_rom(pts, 16)])
    if chance(rng, 0.35):
        px, py = rng.uniform(0.2, 0.8) * end[0], rng.uniform(0.35, 0.6)
        strokes.append(np.array([[px, py], [px + 0.012, py + 0.006]]))
    slant = rng.uniform(0.1, 0.45)
    return [np.column_stack([s[:, 0] + slant * s[:, 1], s[:, 1]]) for s in strokes]


def render_strokes(strokes, rng, width_px=1000, clean=False, ss=2):
    """Draw strokes with pen-pressure width into an L-mode alpha image."""
    allp = np.vstack(strokes)
    mn, span = allp.min(0), np.maximum(np.ptp(allp, 0), 1e-3)
    W = width_px * ss
    s = W * 0.9 / span[0]
    H = int(np.clip(span[1] * s / 0.85, W * 0.18, W * 0.9))
    s = min(s, H * 0.85 / span[1])
    off = np.array([(W - span[0] * s) / 2, (H - span[1] * s) / 2])
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    bw = rng.uniform(6, 12) * ss * width_px / 1000
    for st in strokes:
        P = (st - mn) * s + off
        P[:, 1] = H - P[:, 1]
        n = len(P)
        if clean:
            wid = np.full(n, bw * 0.75)
        else:
            prof = np.sin(np.linspace(0.15, np.pi - 0.15, n)) ** 0.5
            jit = 1 + 0.25 * np.interp(np.arange(n), np.linspace(0, n - 1, 6), [rng.uniform(-1, 1) for _ in range(6)])
            wid = bw * (0.45 + 0.55 * prof) * jit
        for i in range(n - 1):
            w_i = max(1.0, wid[i])
            draw.line([tuple(P[i]), tuple(P[i + 1])], fill=255, width=int(round(w_i)))
            r = w_i / 2
            draw.ellipse([P[i][0] - r, P[i][1] - r, P[i][0] + r, P[i][1] + r], fill=255)
    return img.resize((W // ss, H // ss), Image.Resampling.LANCZOS)


def signature_text(rng, name):
    base = re.split(r",\s*", name)[0]
    words = [w.capitalize() for w in re.split(r"[\s.]+", base) if w]
    if len(words) == 1:
        return words[0]
    return rng.choice([" ".join(words[:2]), words[0], f"{words[0][0]}. {words[-1]}",
                       f"{words[0]} {words[-1][0]}.", " ".join(words)])


def script_alpha(rng, text):
    font = ImageFont.truetype(str(rng.choice(SCRIPT_FONTS)), 180)
    l, t, r, b = ImageDraw.Draw(Image.new("L", (8, 8))).textbbox((0, 0), text, font=font)
    pad = 80
    img = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad), 0)
    draw = ImageDraw.Draw(img)
    draw.text((pad - l, pad - t), text, font=font, fill=255, stroke_width=rng.choice([0, 0, 1, 2]), stroke_fill=255)
    w, h = img.size
    if chance(rng, 0.45):  # underline flourish
        y0 = pad + (b - t) * rng.uniform(0.85, 1.05)
        pts = catmull_rom([(pad * 0.5, y0 + rng.uniform(-10, 10)), (w * 0.5, y0 + rng.uniform(5, 30)),
                           (w - pad * 0.5, y0 - rng.uniform(10, 50))], 30)
        lw = rng.randint(4, 8)
        draw.line([tuple(p) for p in pts], fill=255, width=lw, joint="curve")
    shear = rng.uniform(-0.1, 0.2)
    img = img.transform((int(w + abs(shear) * h), h), Image.Transform.AFFINE,
                        (1, shear, -shear * h if shear > 0 else 0, 0, 1, 0), resample=Image.Resampling.BICUBIC)
    return img.rotate(rng.uniform(-8, 8), resample=Image.Resampling.BICUBIC, expand=True)


def ink_rgba(alpha, color, rng, clean=False, faint=False):
    a = np.asarray(alpha, np.float32) / 255
    if not clean:
        a = cv2.GaussianBlur(a, (0, 0), rng.uniform(0.4, 0.9))
        a *= np.clip(0.65 + 0.45 * smooth_noise(np_rng(rng), *a.shape, cells=rng.randint(6, 16)), 0, 1)
        a *= rng.uniform(0.82, 1.0)
    if faint:
        a *= rng.uniform(0.35, 0.55)
    ys, xs = np.nonzero(a > 0.02)
    if len(ys):
        a = a[max(ys.min() - 4, 0):ys.max() + 5, max(xs.min() - 4, 0):xs.max() + 5]
    rgba = np.zeros(a.shape + (4,), np.uint8)
    rgba[..., :3] = color
    rgba[..., 3] = np.clip(a * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def make_signature(rng, name, style, faint=False):
    clean = style == "esign"
    color = rng.choice([(0, 0, 0), (10, 20, 90), (25, 25, 25)]) if clean else pick_ink(rng)
    use_font = SCRIPT_FONTS and (style == "script_font" or (clean and chance(rng, 0.5)))
    if use_font:
        alpha = script_alpha(rng, signature_text(rng, name))
    else:
        alpha = render_strokes(procedural_strokes(rng, rng.randint(3, 8)), rng, clean=clean)
    return ink_rgba(alpha, color, rng, clean=clean, faint=faint)


def make_paraf(rng, name):
    if SCRIPT_FONTS and chance(rng, 0.35):
        words = [w for w in re.split(r"[\s.,]+", name) if w]
        alpha = script_alpha(rng, "".join(w[0].upper() for w in words[:2]))
    else:
        alpha = render_strokes(procedural_strokes(rng, rng.randint(1, 2), flourish=chance(rng, 0.5)),
                               rng, width_px=500)
    return ink_rgba(alpha, pick_ink(rng), rng)


def make_stamp(rng, d):
    S = 600
    img = Image.new("L", (S, S), 0)
    draw = ImageDraw.Draw(img)
    c0 = S / 2
    if chance(rng, 0.75):  # round stamp with text around the ring
        r1, r2 = S * 0.47, S * 0.31
        draw.ellipse([c0 - r1, c0 - r1, c0 + r1, c0 + r1], outline=255, width=10)
        if chance(rng, 0.5):
            draw.ellipse([c0 - r1 + 18, c0 - r1 + 18, c0 + r1 - 18, c0 + r1 - 18], outline=255, width=3)
        draw.ellipse([c0 - r2, c0 - r2, c0 + r2, c0 + r2], outline=255, width=5)
        text = f"{d['company'].upper()} * {d['company_city'].upper()} * "
        r_text = (r1 + r2) / 2 + 4
        fsz = int(min(52, 2 * math.pi * r_text / len(text) * 1.3))
        font = pil_font(fsz, bold=True)
        for i, ch in enumerate(text):
            ang = 360 * i / len(text)
            rad = math.radians(ang - 90)
            cx, cy = c0 + r_text * math.cos(rad), c0 + r_text * math.sin(rad)
            glyph = Image.new("L", (fsz * 2, fsz * 2), 0)
            ImageDraw.Draw(glyph).text((fsz, fsz), ch, font=font, fill=255, anchor="mm")
            glyph = glyph.rotate(-ang, resample=Image.Resampling.BICUBIC)
            x0, y0 = int(cx - fsz), int(cy - fsz)
            img.paste(255, (x0, y0, x0 + glyph.width, y0 + glyph.height), glyph)
        draw.text((c0, c0 - 18), d["company_acr"], font=pil_font(int(r2 * 0.55), bold=True), fill=255, anchor="mm")
        draw.text((c0, c0 + r2 * 0.45), rng.choice(["HRD", "HR DEPT", "PERSONALIA"]),
                  font=pil_font(int(r2 * 0.22), bold=True), fill=255, anchor="mm")
        angle = rng.uniform(0, 360)
    else:  # rectangular stamp
        x0, y0, x1, y1 = S * 0.05, S * 0.3, S * 0.95, S * 0.7
        draw.rounded_rectangle([x0, y0, x1, y1], radius=20, outline=255, width=9)
        draw.rounded_rectangle([x0 + 16, y0 + 16, x1 - 16, y1 - 16], radius=12, outline=255, width=3)
        name = d["company"].upper()
        fsz = int(min(48, (x1 - x0 - 60) / max(len(name), 1) * 1.8))
        draw.text((c0, c0 - 22), name, font=pil_font(fsz, bold=True), fill=255, anchor="mm")
        draw.text((c0, c0 + 34), d["company_city"].upper(), font=pil_font(30, bold=True), fill=255, anchor="mm")
        angle = rng.uniform(-12, 12)
    img = img.rotate(angle, resample=Image.Resampling.BICUBIC)
    a = np.asarray(img, np.float32) / 255
    nrng = np_rng(rng)
    a *= np.clip(0.45 + 0.75 * smooth_noise(nrng, S, S, cells=rng.randint(5, 12)), 0, 1)
    a *= (nrng.random((S, S)) > rng.uniform(0.02, 0.12))  # ink dropouts
    a = cv2.GaussianBlur(a, (0, 0), 0.8) * rng.uniform(0.7, 0.95)
    rgba = np.zeros((S, S, 4), np.uint8)
    rgba[..., :3] = rng.choice(STAMP_COLORS)
    rgba[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def make_meterai(rng):
    W, H = 420, 250
    nrng = np_rng(rng)
    base = np.array([rng.uniform(225, 245), rng.uniform(170, 200), rng.uniform(190, 215)], np.float32)
    arr = np.ones((H, W, 3), np.float32) * base
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    for k in range(6):  # guilloche-like wave pattern
        wave = np.sin(xx / rng.uniform(8, 20) + np.sin(yy / rng.uniform(10, 30)) * 3 + k)
        arr -= (wave > 0.96)[..., None] * np.array([40, 70, 40], np.float32)
    arr += nrng.normal(0, 4, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    draw = ImageDraw.Draw(img)
    ink = (120, 30, 70)
    draw.rectangle([4, 4, W - 5, H - 5], outline=ink, width=4)
    draw.text((W * 0.3, H * 0.22), "METERAI", font=pil_font(40, bold=True), fill=ink, anchor="mm")
    draw.text((W * 0.3, H * 0.4), "TEMPEL", font=pil_font(26, bold=True), fill=ink, anchor="mm")
    draw.text((W * 0.72, H * 0.55), "10000", font=pil_font(64, bold=True), fill=ink, anchor="mm")
    draw.text((W * 0.5, H * 0.85), "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(16)),
              font=pil_font(20), fill=ink, anchor="mm")
    return img.convert("RGBA").rotate(rng.uniform(-5, 5), resample=Image.Resampling.BICUBIC, expand=True)


def make_logo(rng, acr):
    S = 400
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = tuple(int(c) for c in rng.choice([(200, 30, 45), (20, 70, 160), (10, 120, 90), (230, 120, 20),
                                              (90, 40, 140), (30, 30, 30), (0, 140, 190)]))
    shape = rng.choice(["circle", "square", "diamond", "ring", "hexagon"])
    if shape == "circle":
        draw.ellipse([10, 10, S - 10, S - 10], fill=color)
    elif shape == "square":
        draw.rounded_rectangle([10, 10, S - 10, S - 10], radius=60, fill=color)
    elif shape == "diamond":
        draw.polygon([(S / 2, 5), (S - 5, S / 2), (S / 2, S - 5), (5, S / 2)], fill=color)
    elif shape == "ring":
        draw.ellipse([10, 10, S - 10, S - 10], outline=color, width=36)
    else:
        draw.polygon([(S / 2 + (S / 2 - 8) * math.cos(math.radians(60 * k + 30)),
                       S / 2 + (S / 2 - 8) * math.sin(math.radians(60 * k + 30))) for k in range(6)], fill=color)
    text_color = color if shape == "ring" else (255, 255, 255)
    text = acr[:3]
    draw.text((S / 2, S / 2), text, font=pil_font(S * (0.42 if len(text) < 3 else 0.32), bold=True),
              fill=text_color, anchor="mm")
    return img


# --------------------------------------------------------------------------------------------------------------
# Page layout and rendering (ReportLab)
# Page 1: header, title, parties, identity block, clauses up to and including the contract period.
# Page 2: closing clauses, *Penutup*, signature block. Text fit is measured so both pages always fit.
# --------------------------------------------------------------------------------------------------------------


class Styles:
    def __init__(self, lay, fs):
        self.fs = fs
        self.reg, self.bold = BODY_FONTS[lay["font"]]
        self.width = PAGE_W - 2 * lay["margin_x"]
        lead = fs * lay["leading"]
        self.body = ParagraphStyle("body", fontName=self.reg, fontSize=fs, leading=lead, alignment=lay["align"])
        self.left = ParagraphStyle("left", parent=self.body, alignment=TA_LEFT)
        self.right = ParagraphStyle("right", parent=self.body, alignment=TA_RIGHT)
        self.center = ParagraphStyle("center", parent=self.body, alignment=TA_CENTER)
        self.item = ParagraphStyle("item", parent=self.body, leftIndent=fs * 2.0, bulletIndent=fs * 0.3,
                                   bulletFontName=self.reg, bulletFontSize=fs)
        self.sub = ParagraphStyle("sub", parent=self.item, leftIndent=fs * 3.8, bulletIndent=fs * 2.2)
        self.heading = ParagraphStyle("heading", parent=self.body, fontName=self.bold,
                                      alignment=TA_LEFT if lay["heading_style"] == "left" else TA_CENTER)
        tfs = fs + lay["title_size_add"]
        self.title = ParagraphStyle("title", parent=self.center, fontName=self.bold, fontSize=tfs, leading=tfs * 1.25)


def fit_font(text, font, size, max_w, min_size=6):
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


def build_items(w, lay, blocks, start_num):
    """Turn content blocks into (flowable, gap_after, indent) items. Returns items, next pasal number, period pasal."""
    items, num, period_num = [], start_num, None
    for b in blocks:
        kind = b["kind"]
        if kind == "title":
            items.append((Paragraph(b["text"], w.title), w.fs * 0.25, 0))
        elif kind == "center":
            items.append((Paragraph(b["text"], w.center), 0, 0))
        elif kind == "gap":
            items.append((Spacer(1, w.fs * b["h"]), 0, 0))
        elif kind == "para":
            items.append((Paragraph(b["text"], w.body), w.fs * 0.5, 0))
        elif kind == "place_date":
            items.append((Paragraph(b["text"], w.right if b["align"] == "right" else w.left), 0, 0))
        elif kind == "numbered_para":
            items.append((Paragraph(b["text"], w.item, bulletText=b["bullet"]), w.fs * 0.4, 0))
        elif kind == "kv":
            indent = b["indent"] * cm
            label_w = max(pdfmetrics.stringWidth(k, w.reg, w.fs) for k, _ in b["rows"]) + w.fs * 0.8
            data = [[Paragraph(X(k), w.left), Paragraph(":", w.left), Paragraph(v, w.left)] for k, v in b["rows"]]
            table = Table(data, colWidths=[label_w, w.fs * 0.9, w.width - indent - label_w - w.fs * 0.9])
            style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 0.6),
                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6)]
            if b.get("grid"):
                style += [("GRID", (0, 0), (-1, -1), 0.5, (0.3, 0.3, 0.3)), ("LEFTPADDING", (0, 0), (-1, -1), 3),
                          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
            table.setStyle(TableStyle(style))
            items.append((table, w.fs * 0.6, indent))
        elif kind == "clause":
            if b.get("is_period"):
                period_num = num
            title = X(b["title"])
            if lay["heading_underline"]:
                title = f"<u>{title}</u>"
            head = {"center2": f"PASAL {num}<br/>{title}", "center1": f"Pasal {num} - {title}",
                    "left": f"Pasal {num}. {title}"}[lay["heading_style"]]
            items.append((Spacer(1, w.fs * 0.7), 0, 0))
            items.append((Paragraph(head, w.heading), w.fs * 0.35, 0))
            if not b.get("numbered", True):
                items += [(Paragraph(t, w.body), w.fs * 0.3, 0) for t in b["items"]]
            else:
                for i, it in enumerate(b["items"], 1):
                    text, subs = (it, []) if isinstance(it, str) else it
                    items.append((Paragraph(text, w.item, bulletText=f"{i}."), w.fs * 0.2, 0))
                    for j, s in enumerate(subs):
                        items.append((Paragraph(s, w.sub, bulletText=f"{'abcdefgh'[j]}."), w.fs * 0.1, 0))
            num += 1
    return items, num, period_num


def flow_height(items, width):
    return sum(f.wrap(width - ind, PAGE_H)[1] + gap for f, gap, ind in items)


def draw_flow(c, items, x, y, width):
    for f, gap, ind in items:
        _, h = f.wrap(width - ind, PAGE_H)
        f.drawOn(c, x + ind, y - h)
        y -= h + gap
    return y


def font_sizes(start):
    fs = start
    while fs >= 7.5:
        yield fs
        fs -= 0.5


def sample_layout(rng, d):
    labels = dict(rng.choice(PARTY_LABELS))
    labels["co"] = labels["co"].format(short=d["company_short"] if chance(rng, 0.5) else d["company_acr"])
    return {
        "font": rng.choice(sorted(BODY_FONTS)),
        "font_size": rng.choice([9, 9.5, 10, 10, 10.5, 11, 11, 11.5, 12]),
        "leading": rng.uniform(1.15, 1.4),
        "align": rng.choice([TA_JUSTIFY, TA_JUSTIFY, TA_LEFT]),
        "margin_x": rng.uniform(1.6, 2.8) * cm,
        "margin_top": rng.uniform(1.4, 2.6) * cm,
        "margin_bottom": rng.uniform(1.6, 2.5) * cm,
        "header": wchoice(rng, {"letterhead": 0.35, "plain": 0.45, "logo_right": 0.20}),
        "letterhead_all": chance(rng, 0.4),
        "logo_size": rng.uniform(1.3, 2.0) * cm,
        "confidential": chance(rng, 0.25),
        "footer": chance(rng, 0.6),
        "footer_style": rng.choice(["three", "center", "right"]),
        "total_pages": rng.randint(2, 6),
        "title": rng.choice(TITLES),
        "title_underline": chance(rng, 0.5),
        "title_size_add": rng.choice([0, 1, 2, 3]),
        "heading_style": rng.choice(["center2", "center2", "center1", "left"]),
        "heading_underline": chance(rng, 0.4),
        "opening": wchoice(rng, {"tanggal_words": 0.35, "hari_tanggal": 0.35, "prose": 0.30}),
        "labels": labels,
        "identity_bold_name": chance(rng, 0.6),
        "date_style": wchoice(rng, CONFIG["body_date_style_weights"]),
        "bold_dates": chance(rng, 0.5),
        "period_phrase": rng.randrange(6),
        "period_index": rng.choice([0, 1, 2]),
    }


def employee_rows(rng, d, lay, shown, role_row):
    """The employee's identity rows. Notes in `shown` which of NIK, birth date and gender get printed; `role_row`
    always prints the Jabatan/Posisi row."""
    name = X(d["employee_name"])
    name = f"<b>{name}</b>" if lay["identity_bold_name"] else name
    gender = {"L": rng.choice(["Laki-laki", "Laki-laki", "Pria"]),
              "P": rng.choice(["Perempuan", "Perempuan", "Wanita"])}[d["gender"]]
    birth_style = rng.choice([s for s in DATE_STYLES if s != "month_id_words"])
    others = []
    if chance(rng, 0.85):
        others.append((rng.choice(["Tempat/Tgl. Lahir", "Tempat, Tanggal Lahir", "Tempat dan Tanggal Lahir", "TTL"]),
                       f"{X(d['birth_place'])}, {fmt_date(d['birth_date'], birth_style)}"))
        shown["birth_date"] = True
    # Real contracts always print the employee's NIK, like the position (owner, 2026-11-03)
    shown["nik_label"] = rng.choice(["NIK", "NIK", "No. KTP", "Nomor KTP", "No. Induk Kependudukan", "NIK/No. KTP",
                                     "No. KTP/NIK"])
    shown["nik_grouped"] = chance(rng, CONFIG["p_nik_grouped"])
    nik = " ".join(d["nik"][i:i + 4] for i in range(0, 16, 4)) if shown["nik_grouped"] else d["nik"]
    others.append((shown["nik_label"], nik))
    if chance(rng, 0.7):
        others.append(("Jenis Kelamin", gender))
        shown["gender"] = True
    if chance(rng, 0.9):
        others.append((rng.choice(["Alamat", "Alamat Tempat Tinggal", "Alamat Domisili", "Alamat sesuai KTP"]),
                       X(d["address"])))
    if chance(rng, 0.4):
        others.append((rng.choice(["Telepon", "No. HP", "No. Telepon"]), d["phone"]))
    if role_row or chance(rng, 0.25):
        others.append((rng.choice(["Jabatan", "Posisi"]), X(d["role"])))
    if chance(rng, 0.3):
        rng.shuffle(others)
    rows = [(rng.choice(["Nama", "Nama", "Nama Lengkap", "Nama Karyawan", "Nama Pekerja"]), name)] + others
    if chance(rng, 0.1):  # name is not the first row
        rows.insert(0, (rng.choice(["No. Karyawan", "NIP", "ID Karyawan"]), f"{rng.randint(10000, 999999)}"))
    return rows


def plan_page1(rng, d, lay):
    """Returns (mandatory blocks, optional blocks, identity fields shown). Mandatory ends with the contract-period
    clause and always holds the employee's identity, so whatever `shown` lists is printed."""
    shown = {"nik_label": None, "nik_grouped": False, "birth_date": False, "gender": False}
    L = lay["labels"]
    co, emp = L["co"], L["emp"]
    # Real contracts always name the employee's position on page 1 (owner, 2026-11-03): in the period clause, in the
    # identity rows, or in a placement or duties clause before the period clause. The prose opening has no rows.
    role_in_period = lay["period_phrase"] == 1
    role_at = "period" if role_in_period else (
        "clause" if lay["opening"] == "prose" else wchoice(rng, {"identity": 0.5, "clause": 0.5}))
    sign = d["sign_date"]
    title = f"<u>{lay['title']}</u>" if lay["title_underline"] else lay["title"]
    no = rng.choice([X(d["contract_no"]), f"No. {X(d['contract_no'])}", f"Nomor: {X(d['contract_no'])}"])
    mand = [{"kind": "title", "text": title},
            {"kind": "center", "text": f"<b>{no}</b>" if chance(rng, 0.6) else no},
            {"kind": "gap", "h": rng.uniform(0.6, 1.4)}]
    kv = lambda rows: {"kind": "kv", "rows": rows, "indent": rng.uniform(0, 1.5),
                       "grid": chance(rng, CONFIG["p_identity_grid"])}
    day_w, year_w = title_words(terbilang(sign.day)), title_words(terbilang(sign.year))
    if lay["opening"] == "tanggal_words":
        mand += [
            {"kind": "para", "text":
                f"Pada tanggal <b>{day_w}</b> bulan <b>{BULAN[sign.month - 1]}</b> tahun <b>{year_w}</b> di "
                f"{X(d['sign_city'])} dengan ini diperjanjikan antara <b>{X(d['hr_name'])}</b> sebagai "
                f"{X(d['hr_title'])} yang bertindak untuk dan atas nama <b>{X(d['company'])}</b>, suatu perseroan "
                f"terbatas yang didirikan menurut hukum Negara Republik Indonesia, berkedudukan di "
                f"{X(d['company_address'])} (selanjutnya disebut <b>\"{co}\"</b>) dengan perseorangan yaitu:"},
            kv(employee_rows(rng, d, lay, shown, role_at == "identity")),
            {"kind": "para", "text":
                f"(selanjutnya disebut <b>\"{emp}\"</b>). {co} dan {emp} sepakat untuk mengikatkan diri dalam "
                "Perjanjian Kerja Waktu Tertentu dengan syarat dan ketentuan sebagai berikut:"},
        ]
    elif lay["opening"] == "hari_tanggal":
        mand += [
            {"kind": "para", "text":
                f"Perjanjian Kerja Waktu Tertentu ini (selanjutnya disebut \"PERJANJIAN\") dibuat dan ditandatangani "
                f"di <b>{X(d['sign_city'])}</b> pada hari <b>{HARI[sign.weekday()]}</b>, tanggal <b>{day_w}</b>, "
                f"bulan <b>{BULAN[sign.month - 1]}</b>, tahun <b>{year_w}</b> ({fmt_date(sign, 'dd-mm-yyyy')}) "
                "oleh dan antara:"},
            kv([("Nama", f"<b>{X(d['hr_name'])}</b>"),
                ("Jabatan", X(d["hr_title"]) + (f" {X(d['company'])}" if chance(rng, 0.5) else "")),
                (rng.choice(["Alamat", "Alamat kedudukan / kantor"]), X(d["company_address"]))]),
            {"kind": "para", "text":
                f"Dalam hal ini bertindak untuk dan atas nama {X(d['company'])}, perseroan terbatas yang didirikan "
                f"menurut hukum Indonesia, selanjutnya disebut sebagai <b>{co}</b>;"},
            kv(employee_rows(rng, d, lay, shown, role_at == "identity")),
            {"kind": "para", "text":
                f"Dalam hal ini bertindak untuk dan atas nama diri sendiri, selanjutnya disebut sebagai <b>{emp}</b>."},
            {"kind": "para", "text":
                f"<b>{co}</b> dan <b>{emp}</b> (selanjutnya secara bersama-sama disebut \"PARA PIHAK\") telah "
                "bermufakat untuk saling mengikatkan diri dalam Perjanjian ini dengan ketentuan sebagai berikut:"},
        ]
    else:  # prose: the name sits inside a sentence
        gender = "laki-laki" if d["gender"] == "L" else "perempuan"
        nm = X(d["employee_name"])
        nm = f"<b>{nm}</b>" if lay["identity_bold_name"] else nm
        dob = fmt_date(d["birth_date"], rng.choice(["d_month_id", "dd_month_id", "dd-mm-yyyy"]))
        shown.update(nik_label="pemegang NIK", birth_date=True, gender=True)
        mand += [
            {"kind": "para", "text":
                f"Pada hari ini {HARI[sign.weekday()]} tanggal {fmt_date(sign, rng.choice(['d_month_id', 'dd-mm-yyyy']))}, "
                f"bertempat di {X(d['sign_city'])}, yang bertanda tangan di bawah ini:"},
            {"kind": "numbered_para", "bullet": "1.", "text":
                f"<b>{X(d['hr_name'])}</b>, selaku {X(d['hr_title'])}, dalam hal ini bertindak untuk dan atas nama "
                f"<b>{X(d['company'])}</b>, berkedudukan di {X(d['company_address'])}, selanjutnya disebut "
                f"<b>{co}</b>."},
            {"kind": "numbered_para", "bullet": "2.", "text":
                f"{nm}, {gender}, lahir di {X(d['birth_place'])} pada tanggal {dob}, pemegang NIK {d['nik']}, "
                f"bertempat tinggal di {X(d['address'])}, dalam hal ini bertindak untuk dan atas nama diri sendiri, "
                f"selanjutnya disebut <b>{emp}</b>."},
            {"kind": "para", "text":
                f"{co} dan {emp} secara bersama-sama disebut PARA PIHAK, sepakat untuk membuat Perjanjian Kerja "
                "Waktu Tertentu dengan ketentuan sebagai berikut:"},
        ]

    pool = [c_definisi] + ([] if role_in_period else [c_penempatan]) + [c_tugas, c_persyaratan]
    k = min(lay["period_index"], len(pool))
    pre = ([c_definisi] + rng.sample(pool[1:], k - 1)) if k and chance(rng, 0.7) else rng.sample(pool, k)
    role_clause = next((f for f in pre if f in (c_penempatan, c_tugas)), None) if role_at == "clause" else None
    if role_at == "clause" and role_clause is None:
        role_clause = rng.choice([c_penempatan, c_tugas])
        pre.append(role_clause)
    post = [f for f in pool + [c_waktu_kerja, c_upah] if f not in pre]
    rng.shuffle(post)
    # `keep`: the clause naming the position stays when page 1 is too full for every clause before the period
    mand += [{"kind": "clause", "pre": True, "keep": f is role_clause, **f(d, L, rng)} for f in pre]
    mand.append({"kind": "clause", "is_period": True, **c_period(d, L, rng, lay)})
    return mand, [{"kind": "clause", **f(d, L, rng)} for f in post], shown


def plan_page2(rng, d, lay):
    L = lay["labels"]
    pool = [c_kerahasiaan, c_perselisihan, c_force_majeure, c_hukum, c_lain, c_phk]
    rng.shuffle(pool)
    mand = [{"kind": "clause", **c_penutup(d, L, rng)}]
    if chance(rng, 0.45):
        mand.append({"kind": "para", "text": rng.choice([
            "Demikian Perjanjian ini dibuat untuk dapat dipergunakan sebagaimana mestinya.",
            "Demikian Perjanjian ini dibuat dan ditandatangani oleh PARA PIHAK pada tanggal sebagaimana tersebut "
            "di atas."])})
    if chance(rng, 0.45):  # another date on the last page (distractor)
        style = rng.choice(["d_month_id", "dd_month_id", "dd-mm-yyyy"])
        mand.append({"kind": "place_date", "align": rng.choice(["left", "right"]),
                     "text": f"{X(d['sign_city'])}, {fmt_date(d['sign_date'], style)}"})
    return [{"kind": "clause", **f(d, L, rng)} for f in pool], mand


def plan_signature_layout(rng, d, lay):
    L = lay["labels"]
    pihak = L["co"] == "PIHAK PERTAMA"
    co_hdr = rng.choice([[f"{L['co']},", d["company"]], ["Ditandatangani untuk dan atas nama", d["company"]],
                         ["Untuk dan atas nama", d["company"]], [d["company"]]])
    emp_hdr = rng.choice([[f"{L['emp']},"], ["Diterima dan dimengerti oleh", L["emp_title"]],
                          ["Yang menerima,"], [f"{L['emp_title']},"]])
    if pihak and chance(rng, 0.6):
        co_hdr, emp_hdr = ["PIHAK PERTAMA,", d["company"]], ["PIHAK KEDUA,"]
    return {
        "co_hdr": co_hdr, "emp_hdr": emp_hdr, "swap": chance(rng, 0.2),
        "align": rng.choice(["center", "center", "left"]),
        "area_h": rng.uniform(2.0, 3.0) * cm, "inset": rng.uniform(0, 1.0) * cm,
        "hdr_bold": chance(rng, 0.6), "name_bold": chance(rng, 0.7), "name_underline": chance(rng, 0.5),
        "name_paren": chance(rng, 0.12),
        "co_title": d["hr_title"] if chance(rng, 0.85) else None,
        "emp_title": rng.choice([L["emp_title"], d["role"], None]),
        "sig_line": chance(rng, 0.2),
    }


def signature_block_height(w, sl):
    lead = w.fs * 1.3
    rows = max(len(sl["co_hdr"]), len(sl["emp_hdr"]))
    titles = 1 if (sl["co_title"] or sl["emp_title"]) else 0
    return rows * lead + sl["area_h"] + w.fs * 1.2 + titles * lead + w.fs


def sample_signature_plan(rng):
    signed = chance(rng, CONFIG["p_employee_signed"])
    plan = {
        "employee_signed": signed,
        "style": wchoice(rng, CONFIG["signature_style_weights"]) if signed else None,
        "faint": signed and chance(rng, 0.1),
        "hr_signed": chance(rng, 0.92),
        "hr_style": wchoice(rng, CONFIG["signature_style_weights"]),
        "stamp": chance(rng, 0.55),
        "meterai": chance(rng, 0.25),
        "paraf_page1": chance(rng, 0.3),
        "paraf_page2": chance(rng, 0.1),
        "esign_caption": chance(rng, 0.3),
        "unsigned_variant": None,
    }
    if not signed:
        plan["unsigned_variant"] = wchoice(rng, {"blank": 0.5, "line_only": 0.25, "meterai_only": 0.25})
        plan["meterai"] = plan["unsigned_variant"] == "meterai_only"
    return plan


def draw_header(c, lay, d, page_no, logo):
    fs = lay["font_size"]
    reg, bold = BODY_FONTS[lay["font"]]
    mx, y = lay["margin_x"], PAGE_H - lay["margin_top"]
    if lay["confidential"]:
        bw, bh = 3.0 * cm, 0.65 * cm
        x0, y0 = PAGE_W - mx - bw, PAGE_H - lay["margin_top"] * 0.5 - bh / 2
        c.setLineWidth(0.6)
        c.setStrokeGray(0.4)
        c.setFillGray(0.35)
        c.rect(x0, y0, bw, bh)
        c.setFont(bold, 8.5)
        c.drawCentredString(x0 + bw / 2, y0 + bh / 2 - 3, "CONFIDENTIAL")
        c.setStrokeGray(0)
        c.setFillGray(0)
    if lay["header"] == "letterhead" and (page_no == 1 or lay["letterhead_all"]):
        lh = lay["logo_size"]
        c.drawImage(ImageReader(logo), mx, y - lh, lh, lh, mask="auto")
        tx, max_w = mx + lh + 0.4 * cm, PAGE_W - 2 * mx - lh - 0.4 * cm
        name_fs = fit_font(d["company"].upper(), bold, fs + 4, max_w)
        c.setFont(bold, name_fs)
        c.drawString(tx, y - name_fs, d["company"].upper())
        addr_fs = fit_font(d["company_address"], reg, fs - 1.5, max_w)
        c.setFont(reg, addr_fs)
        c.drawString(tx, y - name_fs - addr_fs - 4, d["company_address"])
        y -= lh + 0.2 * cm
        c.setLineWidth(1.2)
        c.line(mx, y, PAGE_W - mx, y)
        if lay["letterhead_all"]:
            c.setLineWidth(0.4)
            c.line(mx, y - 2.5, PAGE_W - mx, y - 2.5)
        y -= 0.5 * cm
    elif lay["header"] == "logo_right" and page_no == 1:
        lh = lay["logo_size"] * 0.8
        c.drawImage(ImageReader(logo), PAGE_W - mx - lh, y - lh, lh, lh, mask="auto")
        y -= lh + 0.2 * cm
    return y


def draw_footer(c, lay, d, page_no, w):
    if not lay["footer"]:
        return
    n = lay["total_pages"]
    pg = 1 if page_no == 1 else n
    mx, y = lay["margin_x"], lay["margin_bottom"] * 0.45
    fsz = max(6.5, w.fs - 2.5)
    c.setFont(w.reg, fsz)
    c.setFillGray(0.25)
    if lay["footer_style"] == "three":
        c.setLineWidth(0.5)
        c.line(mx, y + fsz + 3, PAGE_W - mx, y + fsz + 3)
        if pdfmetrics.stringWidth(d["company"].upper(), w.reg, fsz) < (PAGE_W - 2 * mx) / 2 - 40:
            c.drawString(mx, y, d["company"].upper())
        c.drawCentredString(PAGE_W / 2 + 1.5 * cm, y, f"{pg} dari {n}")
        c.drawRightString(PAGE_W - mx, y, "Perjanjian Kerja Waktu Tertentu")
    elif lay["footer_style"] == "center":
        c.drawCentredString(PAGE_W / 2, y, f"Halaman {pg} dari {n}")
    else:
        c.drawRightString(PAGE_W - mx, y, f"{pg}/{n}")
    c.setFillGray(0)


def draw_image(c, img, x, y, w, h):
    c.drawImage(ImageReader(img), x, y, w, h, mask="auto")


def draw_paraf(c, rng, lay, name, content_bottom):
    """Initials in the bottom corner, kept below the page content so they never cover the text."""
    img = make_paraf(rng, name)
    y0 = lay["margin_bottom"] * rng.uniform(0.3, 0.5)
    pw = rng.uniform(1.0, 1.8) * cm
    ph = pw * img.height / img.width
    max_h = max(content_bottom - 4 - y0, 0.5 * cm)
    if ph > max_h:
        ph, pw = max_h, max_h * img.width / img.height
    x = PAGE_W - lay["margin_x"] - pw - rng.uniform(0, 1.2) * cm if chance(rng, 0.7) else lay["margin_x"] + rng.uniform(0, 1) * cm
    draw_image(c, img, x, y0, pw, ph)


def draw_signature_block(c, rng, w, d, lay, sig, sl, y_top):
    """Two-column signature block. Only the employee column decides `has_employee_signature`."""
    mx = lay["margin_x"]
    colw = (PAGE_W - 2 * mx) / 2 - sl["inset"]
    xs = {"co": mx + sl["inset"], "emp": mx + (PAGE_W - 2 * mx) / 2 + sl["inset"] * 0.5}
    if sl["swap"]:
        xs = {"co": xs["emp"], "emp": xs["co"]}
    lead = w.fs * 1.3
    center = sl["align"] == "center"

    def text_at(x, y, s, font, size=None):
        size = fit_font(s, font, size or w.fs, colw - 4)
        c.setFont(font, size)
        if center:
            c.drawCentredString(x + colw / 2, y, s)
        else:
            c.drawString(x, y, s)
        sw = pdfmetrics.stringWidth(s, font, size)
        return (x + (colw - sw) / 2 if center else x), sw

    rows = max(len(sl["co_hdr"]), len(sl["emp_hdr"]))
    for side, hdr in (("co", sl["co_hdr"]), ("emp", sl["emp_hdr"])):
        for i, line in enumerate(hdr):
            bold = sl["hdr_bold"] and (line.isupper() or i == len(hdr) - 1)
            text_at(xs[side], y_top - (i + 1) * lead + (lead - w.fs), line, w.bold if bold else w.reg)
    area_top = y_top - rows * lead
    name_y = area_top - sl["area_h"]
    area_bottom = name_y + w.fs * 0.9
    centers = {}
    for side, nm, title in (("co", d["hr_name"], sl["co_title"]), ("emp", d["employee_name"], sl["emp_title"])):
        s = f"( {nm} )" if sl["name_paren"] else nm
        x0, sw = text_at(xs[side], name_y, s, w.bold if sl["name_bold"] else w.reg)
        if sl["name_underline"]:
            c.setLineWidth(0.6)
            c.line(x0, name_y - 2, x0 + sw, name_y - 2)
        if sl["sig_line"]:
            c.setLineWidth(0.5)
            c.line(x0 - 0.3 * cm, area_bottom + 2, x0 + sw + 0.3 * cm, area_bottom + 2)
        if title:
            text_at(xs[side], name_y - lead, title, w.reg)
        centers[side] = x0 + sw / 2 if center else xs[side] + rng.uniform(1.5, 2.5) * cm

    def place(img, side, width_cm):
        iw = width_cm * cm
        ih = iw * img.height / img.width
        max_h = (area_top - area_bottom) * 1.15
        if ih > max_h:
            ih, iw = max_h, max_h * img.width / img.height
        cx = centers[side] + rng.uniform(-0.6, 0.6) * cm
        y0 = area_bottom - rng.uniform(0, 0.5) * w.fs + rng.uniform(0, max(0.0, area_top - area_bottom - ih))
        draw_image(c, img, cx - iw / 2, y0, iw, ih)
        return cx, y0, iw, ih

    if sig["meterai"]:
        m = make_meterai(rng)
        mw = rng.uniform(2.3, 2.8) * cm
        mh = mw * m.height / m.width
        mx0 = centers["emp"] - mw / 2 - rng.uniform(0.3, 1.3) * cm
        draw_image(c, m, mx0, area_bottom + rng.uniform(0.05, 0.4) * (area_top - area_bottom - mh), mw, mh)

    def draw_stamp(sx, sy):
        sd = rng.uniform(2.4, 3.3) * cm
        draw_image(c, make_stamp(rng, d), sx - sd / 2 - rng.uniform(0.3, 1.2) * cm,
                   sy - sd / 2 + rng.uniform(-0.4, 0.4) * cm, sd, sd)

    co_mid = (centers["co"], area_bottom + (area_top - area_bottom) / 2)
    stamp_under = sig["stamp"] and chance(rng, 0.4)  # stamp drawn first, signature on top of it
    if stamp_under:
        draw_stamp(*co_mid)
    if sig["hr_signed"]:
        cx, y0, iw, ih = place(make_signature(rng, d["hr_name"], sig["hr_style"]), "co", rng.uniform(2.8, 4.8))
        co_mid = (cx, y0 + ih / 2)
    if sig["stamp"] and not stamp_under:
        draw_stamp(*co_mid)

    if sig["employee_signed"]:
        cx, y0, iw, ih = place(make_signature(rng, d["employee_name"], sig["style"], sig["faint"]), "emp",
                               rng.uniform(2.6, 5.0))
        if sig["style"] == "esign" and sig["esign_caption"]:
            c.setFont(w.reg, 6)
            c.setFillGray(0.35)
            # Under the signature but never on the printed name: a caption over the name merged with it into one
            # garbled line in the text layer, which no real e-sign stamp does.
            c.drawCentredString(cx, max(y0 - 7, area_bottom + 2),
                                rng.choice(["Ditandatangani secara elektronik", "Digitally signed"])
                                + f" {fmt_date(d['sign_date'], 'dd/mm/yyyy')}")
            c.setFillGray(0)
    elif sig["unsigned_variant"] == "line_only":
        c.setLineWidth(0.6)
        c.setDash(1, 2)
        c.line(centers["emp"] - 2 * cm, area_bottom + 4, centers["emp"] + 2 * cm, area_bottom + 4)
        c.setDash()
    return name_y - lead - w.fs * 0.3  # bottom of the block


def render_contract(rng, d, lay, sig):
    """Draw the 2-page digital PDF. Returns (pdf bytes, layout info)."""
    logo = make_logo(rng, d["company_acr"]) if lay["header"] != "plain" else None
    p1_mand, p1_opt, shown = plan_page1(rng, d, lay)
    p2_opt, p2_mand = plan_page2(rng, d, lay)
    sl = plan_signature_layout(rng, d, lay)
    fill1, n2_target = rng.uniform(0.7, 1.0), rng.randint(0, len(p2_opt))

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(lay["title"].title())
    c.setAuthor(d["company"])
    footer_space = 0.35 * cm if lay["footer"] else 0
    bottom = lay["margin_bottom"] + footer_space

    # ---- page 1: shrink font until the mandatory part fits, then add optional clauses up to a fill target
    top = draw_header(c, lay, d, 1, logo)
    avail = top - bottom
    mand = list(p1_mand)
    while True:
        for fs in font_sizes(lay["font_size"]):
            w = Styles(lay, fs)
            if flow_height(build_items(w, lay, mand, 1)[0], w.width) <= avail:
                break
        else:
            pre_idx = [i for i, b in enumerate(mand) if b.get("pre") and not b.get("keep")]
            if not pre_idx:
                raise RuntimeError("page 1 does not fit")
            mand.pop(pre_idx[-1])
            continue
        break
    chosen = []
    h_mand = flow_height(build_items(w, lay, mand, 1)[0], w.width)
    for b in p1_opt:
        if flow_height(build_items(w, lay, mand + chosen + [b], 1)[0], w.width) > min(avail, max(avail * fill1, h_mand)):
            break
        chosen.append(b)
    items, next_num, period_num = build_items(w, lay, mand + chosen, 1)
    # The employee's position counts as printed when page 1 names it anywhere (identity row, placement, duties or the
    # period clause); the company signatory's title is a different list, so it never matches.
    role = X(d["role"])
    position_page1 = any(role in json.dumps(b, ensure_ascii=False, default=str) for b in mand + chosen)
    y1 = draw_flow(c, items, lay["margin_x"], top, w.width)
    draw_footer(c, lay, d, 1, w)
    if sig["paraf_page1"]:
        draw_paraf(c, rng, lay, d["employee_name"], y1)
    c.showPage()

    # ---- page 2: closing clauses + signature block
    top2 = draw_header(c, lay, d, 2, logo)
    gap_pages = lay["total_pages"] - 2 if lay["footer"] else rng.randint(0, 3)
    start2 = next_num + sum(rng.randint(2, 4) for _ in range(gap_pages))
    for fs in font_sizes(w.fs):
        w2 = Styles(lay, fs)
        sig_h = signature_block_height(w2, sl)
        fitted = None
        for n in range(n2_target, -1, -1):
            items2 = build_items(w2, lay, p2_opt[:n] + p2_mand, start2)[0]
            if flow_height(items2, w2.width) + sig_h + w2.fs * 1.5 <= top2 - bottom:
                fitted = items2
                break
        if fitted:
            break
    else:
        raise RuntimeError("page 2 does not fit")
    y = draw_flow(c, fitted, lay["margin_x"], top2, w2.width) - w2.fs * 1.5
    slack = max(0.0, y - sig_h - bottom)
    y2 = draw_signature_block(c, rng, w2, d, lay, sig, sl, y - rng.uniform(0, 0.4) * slack)
    draw_footer(c, lay, d, 2, w2)
    if sig["paraf_page2"]:
        draw_paraf(c, rng, lay, d["employee_name"], y2)
    c.showPage()
    c.save()
    return buf.getvalue(), {"font_size": w.fs, "period_pasal": period_num, "identity": shown,
                            "position_page1": position_page1}


# --------------------------------------------------------------------------------------------------------------
# Form: digital, scan, phone photo
# Scan and photo pages are rasterised and saved as image-only PDFs (no text layer).
# --------------------------------------------------------------------------------------------------------------


def rasterize(pdf_bytes, dpi):
    doc = pdfium.PdfDocument(pdf_bytes)
    pages = [doc[i].render(scale=dpi / 72).to_pil().convert("RGB") for i in range(len(doc))]
    doc.close()
    return pages


def jpeg_roundtrip(arr, quality):
    ok, enc = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    return cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)


def scan_profile(rng):
    """Settings shared by all pages of one scanned document."""
    mode = wchoice(rng, CONFIG["scan_mode_weights"])
    gray = mode != "color"
    tint = [rng.uniform(0.93, 1.0)] * 3 if gray else [rng.uniform(0.95, 1.0), rng.uniform(0.94, 1.0),
                                                       rng.uniform(0.88, 0.98)]
    return {"mode": mode, "gray": gray, "tint": np.array(tint, np.float32),
            "bw_threshold": rng.uniform(150, 190),  # 1-bit scans: lighter grey than this turns white
            "bed": rng.uniform(200, 250) if chance(rng, 0.6) else rng.uniform(30, 90),  # scanner lid colour
            "contrast": rng.uniform(0.9, 1.25), "brightness": rng.uniform(-10, 12),
            "blur": rng.uniform(0.2, 0.6) if chance(rng, 0.6) else 0, "noise": rng.uniform(1, 5),
            "jpeg_q": rng.randint(70, 92)}


def to_scan(img, rng, prof):
    nrng = np_rng(rng)
    a = np.asarray(img, np.float32)
    h, w = a.shape[:2]
    if prof["gray"]:
        a = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY)[..., None].repeat(3, axis=2)
    a *= prof["tint"]
    skew = CONFIG["scan_rotation_deg"]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rng.uniform(-skew, skew), rng.uniform(0.985, 1.0))
    M[:, 2] += (rng.uniform(-0.01, 0.01) * w, rng.uniform(-0.01, 0.01) * h)
    bed = prof["bed"]
    a = cv2.warpAffine(a, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(bed, bed, bed))
    ramp = np.linspace(0, 1, w if chance(rng, 0.5) else h, dtype=np.float32)
    shade = 1 - rng.uniform(0, 0.08) * (ramp[None, :] if len(ramp) == w else ramp[:, None])
    a *= shade[..., None]
    a = (a - 128) * prof["contrast"] + 128 + prof["brightness"]
    if prof["blur"]:
        a = cv2.GaussianBlur(a, (0, 0), prof["blur"])
    a += nrng.normal(0, prof["noise"], (h, w, 1) if prof["gray"] else a.shape).astype(np.float32)
    n = int(rng.uniform(0, 6e-5) * h * w)
    a[nrng.integers(0, h, n), nrng.integers(0, w, n)] = rng.uniform(0, 80)
    a = jpeg_roundtrip(np.clip(a, 0, 255).astype(np.uint8), prof["jpeg_q"])
    out = Image.fromarray(a)
    if prof["mode"] == "bw":  # the copier thresholds its own grey scan: thin strokes break, grey stamps vanish
        bw = np.asarray(out.convert("L")) >= prof["bw_threshold"]
        return Image.fromarray(bw.astype(np.uint8) * 255).convert("1", dither=Image.Dither.NONE)
    return out.convert("L") if prof["gray"] else out


def make_background(rng, nrng, H, W):
    kind = wchoice(rng, {"wood": 0.3, "solid": 0.3, "fabric": 0.2, "dark": 0.2})
    if kind == "wood":
        base = np.array([rng.uniform(110, 170), rng.uniform(70, 110), rng.uniform(35, 70)], np.float32)
        xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
        warp = smooth_noise(nrng, H, W, cells=4) * rng.uniform(8, 20)
        grain = 0.5 + 0.5 * np.sin(xx * rng.uniform(40, 120) + warp)
        bg = base * (0.75 + 0.35 * grain[..., None])
    elif kind == "solid":
        base = np.array([rng.uniform(150, 235)] * 3, np.float32) * np.array(
            [rng.uniform(0.9, 1.05), rng.uniform(0.9, 1.05), rng.uniform(0.9, 1.05)], np.float32)
        bg = np.ones((H, W, 3), np.float32) * base
    elif kind == "fabric":
        base = np.array([rng.uniform(40, 200), rng.uniform(40, 200), rng.uniform(40, 200)], np.float32)
        bg = base * (0.85 + 0.3 * nrng.random((H, W, 1)).astype(np.float32))
    else:
        bg = np.ones((H, W, 3), np.float32) * rng.uniform(20, 60)
    bg *= (0.85 + 0.3 * smooth_noise(nrng, H, W, cells=6))[..., None]
    return bg


def photo_profile(rng):
    """Settings shared by all pages of one photographed document (same paper, desk, camera, person)."""
    turned = chance(rng, CONFIG["p_photo_turned"])
    return {"tint": np.array([rng.uniform(0.96, 1.0), rng.uniform(0.95, 1.0), rng.uniform(0.9, 0.98)], np.float32),
            "bg_seed": rng.getrandbits(32),
            "cast": np.array([1 + rng.uniform(-0.05, 0.05) for _ in range(3)], np.float32),
            "fill": rng.uniform(*CONFIG["photo_fill"]), "page_px": rng.randint(*CONFIG["photo_page_px"]),
            "turn": rng.choice([90, 270]) if turned else 0,
            "clutter": chance(rng, CONFIG["p_photo_clutter"]), "blurry": chance(rng, CONFIG["p_photo_blurry"]),
            "jpeg_q": rng.randint(72, 92), "pages": []}


def draw_clutter(rng, W, H):
    """An RGBA layer with a pen, a phone or another printed sheet lying on the desk."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    def rotated_box(cx, cy, bw, bh, ang):
        c, s = math.cos(ang), math.sin(ang)
        return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in
                ((-bw / 2, -bh / 2), (bw / 2, -bh / 2), (bw / 2, bh / 2), (-bw / 2, bh / 2))]

    for _ in range(rng.randint(1, 2)):
        kind = rng.choice(["pen", "phone", "sheet"])
        cx, cy, ang = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0, math.pi)
        if kind == "pen":
            col = rng.choice([(25, 35, 90), (20, 20, 25), (150, 30, 30)])
            draw.polygon(rotated_box(cx, cy, W * rng.uniform(0.25, 0.4), W * 0.012, ang), fill=col + (255,))
        elif kind == "phone":
            g = rng.randint(15, 45)
            draw.polygon(rotated_box(cx, cy, W * 0.22, W * 0.45, ang), fill=(g, g, g + 5, 255))
        else:  # another printed sheet: rows of word-like grey strokes, text-like enough to tempt OCR
            sw, sh, tilt = W * rng.uniform(0.5, 0.8), H * rng.uniform(0.5, 0.8), ang * 0.1
            draw.polygon(rotated_box(cx, cy, sw, sh, tilt), fill=(238, 238, 232, 255))
            c, s = math.cos(tilt), math.sin(tilt)
            at = lambda x, y: (cx + x * c - y * s, cy + x * s + y * c)
            ink, width = (95, 95, 95, 255), max(1, int(H * 0.0025))
            for k in range(int(sh * 0.84 / (H * 0.022))):
                y, x = -sh / 2 + sh * 0.08 + k * H * 0.022, -sw / 2 + sw * 0.1
                end = sw / 2 - sw * rng.uniform(0.1, 0.35)
                while x < end:
                    word = min(W * rng.uniform(0.015, 0.06), end - x)
                    draw.line([at(x, y), at(x + word, y)], fill=ink, width=width)
                    x += word + W * 0.008
    return np.asarray(layer, np.float32)


def to_photo(img, rng, prof):
    nrng = np_rng(rng)
    page = np.asarray(img, np.float32) * prof["tint"]
    ph, pw = page.shape[:2]
    if chance(rng, 0.6):  # slight paper curl
        yy, xx = np.mgrid[0:ph, 0:pw].astype(np.float32)
        amp, per = rng.uniform(2, 8) * pw / 1000, rng.uniform(0.6, 1.6) * ph
        map_x = xx + amp * np.sin(2 * np.pi * yy / per + rng.uniform(0, 6.28))
        map_y = yy + amp * 0.5 * np.sin(2 * np.pi * xx / (per * 0.7) + rng.uniform(0, 6.28))
        page = cv2.remap(page, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    H = int(ph * rng.uniform(1.08, 1.3))
    W = max(int(H * rng.uniform(0.71, 0.76)), int(pw * 1.04))  # a 3:4 phone frame, near A4
    bg = make_background(random.Random(prof["bg_seed"]), np.random.default_rng(prof["bg_seed"]), H, W)
    # Place the page fully inside the frame with perspective (both ways) and rotation; a placement that does not fit
    # is retried a little smaller, so a near photo stays near instead of falling back to a straight one.
    fill = min(0.97, prof["fill"] * rng.uniform(0.95, 1.05))
    kmax, rmax = CONFIG["photo_keystone"], CONFIG["photo_rotation_deg"]
    for _ in range(60):
        s = min(W * fill / pw, H * fill / ph)
        cw, ch = pw * s / 2, ph * s / 2
        kx, ky = rng.uniform(-kmax, kmax), rng.uniform(-kmax, kmax) / 2  # top vs bottom, left vs right edge
        corners = np.array([[-cw * (1 - kx), -ch * (1 - ky)], [cw * (1 - kx), -ch * (1 + ky)],
                            [cw * (1 + kx), ch * (1 + ky)], [-cw * (1 + kx), ch * (1 - ky)]])
        deg = rng.uniform(-rmax, rmax)
        ang = math.radians(deg)
        R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
        corners = corners @ R.T + nrng.uniform(-0.01, 0.01, (4, 2)) * [cw, ch]
        room = max(0.03, (1 - fill) * 0.4)
        dst = corners + [W / 2 + rng.uniform(-room, room) * W, H / 2 + rng.uniform(-room, room) * H]
        if dst[:, 0].min() > 2 and dst[:, 1].min() > 2 and dst[:, 0].max() < W - 2 and dst[:, 1].max() < H - 2:
            break
        fill *= 0.97
    else:  # no random placement fitted: centre the page straight
        s, kx, ky, deg = 0.85 * min(W / pw, H / ph), 0.0, 0.0, 0.0
        dst = np.array([[-pw, -ph], [pw, -ph], [pw, ph], [-pw, ph]]) * s / 2 + [W / 2, H / 2]
    prof["pages"].append({"fill": round(s * ph / H, 3), "keystone": round(max(abs(kx), abs(ky)), 3),
                          "rotation_deg": round(deg, 1), "turn": prof["turn"]})
    src = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    Mp = cv2.getPerspectiveTransform(src, dst.astype(np.float32))
    warped = cv2.warpPerspective(page, Mp, (W, H), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones((ph, pw), np.float32), Mp, (W, H))
    shadow = cv2.GaussianBlur(mask, (0, 0), rng.uniform(8, 25) * W / 1500)
    shadow = np.roll(shadow, (rng.randint(-15, 25), rng.randint(-15, 25)), axis=(0, 1))
    bg *= (1 - rng.uniform(0.25, 0.55) * shadow)[..., None]
    out = bg * (1 - mask[..., None]) + warped * mask[..., None]
    if prof["clutter"]:  # beside the contract only: nothing covers its text
        layer = draw_clutter(rng, W, H)
        alpha = layer[..., 3:] / 255 * (1 - mask[..., None])
        out = out * (1 - alpha) + layer[..., :3] * alpha
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ang = rng.uniform(0, 2 * np.pi)
    lin = ((xx / W - 0.5) * math.cos(ang) + (yy / H - 0.5) * math.sin(ang))
    vign = ((xx / W - 0.5) ** 2 + (yy / H - 0.5) ** 2)
    light = 1 - rng.uniform(0.02, 0.10) * (lin + 0.5) - rng.uniform(0.03, 0.15) * vign
    if chance(rng, 0.25):  # soft shadow of the phone / hand
        poly = Image.new("L", (W, H), 0)
        cx0 = rng.choice([0, W])
        pts = [(cx0, rng.uniform(0, H)), (cx0, rng.uniform(0, H)),
               (rng.uniform(0.2, 0.8) * W, rng.uniform(0, H)), (rng.uniform(0.2, 0.8) * W, rng.uniform(0, H))]
        ImageDraw.Draw(poly).polygon(pts, fill=255)
        sh = cv2.GaussianBlur(np.asarray(poly, np.float32) / 255, (0, 0), W * 0.04)
        light *= 1 - rng.uniform(0.08, 0.2) * sh
    out *= light[..., None]
    out *= prof["cast"]
    if chance(rng, 0.2):  # slight hand shake
        k = rng.randint(3, 5)
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1 / k
        kernel = cv2.warpAffine(kernel, cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), rng.uniform(0, 180), 1), (k, k))
        out = cv2.filter2D(out, -1, kernel / max(kernel.sum(), 1e-6))
    else:
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.3, 0.8))
    if prof["blurry"]:  # focus a little off
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(1.0, 1.5))
    out += nrng.normal(0, rng.uniform(1.5, 5), out.shape).astype(np.float32)
    out = np.clip(out, 0, 255).astype(np.uint8)
    # The camera's resolution: the paper ends up about page_px tall, so a far photo gets a larger frame.
    target_h = int(min(4200, max(900, prof["page_px"] * H / (ph * s))))
    out = cv2.resize(out, (int(W * target_h / H), target_h),
                     interpolation=cv2.INTER_AREA if target_h < H else cv2.INTER_CUBIC)
    if prof["turn"]:
        out = np.ascontiguousarray(np.rot90(out, prof["turn"] // 90))
    return Image.fromarray(jpeg_roundtrip(out, prof["jpeg_q"]))


def images_to_pdf(images, dpi):
    buf = io.BytesIO()
    images[0].save(buf, "PDF", save_all=True, append_images=images[1:], resolution=dpi)
    return buf.getvalue()


# --------------------------------------------------------------------------------------------------------------
# File name and generation loop
# --------------------------------------------------------------------------------------------------------------


def make_filename(rng, d, used):
    style = wchoice(rng, CONFIG["filename_style_weights"])
    s, e = d["start"], d["end"]
    same_year = s.year == e.year
    if style == "short_id" or (style == "english" and chance(rng, 0.5)):
        ab = BULAN_ABBR if style == "short_id" else MONTH_EN_ABBR
        sy = "" if same_year else f"{s.year % 100:02d}"
        period = f"{s.day}{ab[s.month - 1]}{sy}-{e.day}{ab[e.month - 1]}{e.year % 100:02d}"
    elif style == "english":
        sy = "" if same_year else f" {s.year}"
        period = f"{s.day} {MONTH_EN[s.month - 1]}{sy} - {e.day} {MONTH_EN[e.month - 1]} {e.year}"
    elif style == "abbr_id":
        sy = "" if same_year else f" {s.year}"
        period = f"{s.day} {BULAN_ABBR[s.month - 1].capitalize()}{sy} - {e.day} {BULAN_ABBR[e.month - 1].capitalize()} {e.year}"
    elif style == "long_id":
        sy = "" if same_year else f" {s.year}"
        period = f"{s.day} {BULAN[s.month - 1]}{sy} - {e.day} {BULAN[e.month - 1]} {e.year}"
    else:
        period = ""
    sep = " - " if period and chance(rng, CONFIG["p_filename_dash"]) else " "
    stem = f"{d['employee_name']}{sep}{period}".strip()
    entitas = None
    if chance(rng, CONFIG["p_filename_entitas"]):  # "<name> - <period> - <ENTITAS>"
        entitas = d["entitas"][wchoice(rng, CONFIG["entitas_style_weights"])]
        stem = f"{stem} - {entitas}"
    stem = re.sub(r'[<>:"/\\|?*]', "", stem)
    final, k = stem, 2
    while final.lower() in used:
        final, k = f"{stem} ({k})", k + 1
    used.add(final.lower())
    if entitas and final != stem:  # ENTITAS is whatever ends the filename, the " (2)" of a duplicate included
        entitas = final.rsplit(" - ", 1)[1]
    return final + ".pdf", style, sep == " - ", entitas


def generate_one(idx, used_names, batch=None):
    seed = CONFIG["seed"] * 1_000_003 + idx
    rng = random.Random(seed)
    fake.seed_instance(seed)
    d = sample_contract(rng, batch)
    lay = sample_layout(rng, d)
    sig = sample_signature_plan(rng)
    pdf_bytes, info = render_contract(rng, d, lay, sig)
    form = wchoice(rng, CONFIG["form_weights"])
    prof = None
    if form != "digital":
        dpi = rng.randint(*CONFIG["scan_dpi"]) if form == "scan" else rng.randint(200, 250)
        prof = scan_profile(rng) if form == "scan" else photo_profile(rng)
        convert = to_scan if form == "scan" else to_photo
        pages = [convert(p, rng, prof) for p in rasterize(pdf_bytes, dpi)]
        pdf_bytes = images_to_pdf(pages, dpi if form == "scan" else max(pages[0].size) / 11.69)
    file_name, fn_style, fn_dash, entitas = make_filename(rng, d, used_names)
    kelompok = "uat" if chance(rng, CONFIG["uat_fraction"]) else "kalibrasi"
    shown = info["identity"]
    record = {
        "file_name": file_name,
        "employee_name": d["employee_name"],
        "employee_nik": d["nik"] if shown["nik_label"] else None,
        "position": d["role"] if info["position_page1"] else None,
        "contract_start": d["start"].isoformat(),
        "contract_end": d["end"].isoformat(),
        "has_employee_signature": sig["employee_signed"],
        "entitas": entitas,
        "meta": {
            "doc_index": idx, "seed": seed, "form": form, "page_count": 2,
            "scan_mode": prof["mode"] if form == "scan" else None,
            "photo_pages": prof["pages"] if form == "photo" else None,  # fill, keystone, rotation_deg, turn
            "photo_clutter": prof["clutter"] if form == "photo" else None,
            "photo_blurry": prof["blurry"] if form == "photo" else None,
            "entitas_name": d["entitas"]["name"], "entitas_code": d["entitas"]["code"],
            "client_in_contract": d["client"],
            "font": lay["font"], "font_size": info["font_size"],
            "opening_style": lay["opening"], "party_labels": [lay["labels"]["co"], lay["labels"]["emp"]],
            "period_pasal": info["period_pasal"], "period_phrase": lay["period_phrase"],
            "date_style": lay["date_style"],
            "start_text": fmt_date(d["start"], lay["date_style"]), "end_text": fmt_date(d["end"], lay["date_style"]),
            "duration_months": d["months"], "filename_style": fn_style, "filename_dash": fn_dash,
            "kelompok": kelompok,
            "batch": batch["index"] if batch else None, "period_folder": period_folder(d["start"], d["end"]),
            "signature_style": sig["style"], "signature_faint": sig["faint"],
            "unsigned_variant": sig["unsigned_variant"],
            "has_hr_signature": sig["hr_signed"], "has_company_stamp": sig["stamp"],
            "has_meterai": sig["meterai"], "has_paraf_page1": sig["paraf_page1"], "has_paraf_page2": sig["paraf_page2"],
            "signing_date": d["sign_date"].isoformat(), "contract_number": d["contract_no"], "company": d["company"],
            "nik_label": shown["nik_label"], "nik_grouped": shown["nik_grouped"],
            "role": d["role"],
            "birth_date": d["birth_date"].isoformat() if shown["birth_date"] else None,
            "gender": d["gender"] if shown["gender"] else None,
        },
    }
    return pdf_bytes, record


# Columns of contract-extractor's label-template.csv, plus columns the template does not have yet, which `evaluate`
# ignores: `nik` and `posisi` (both always printed on page 1), `entitas`
# (empty when the filename has none) and `bentuk` (digital, scan or photo). Every document is 2 pages: identity and
# period on page 1, signatures on page 2. `kasus` only marks what the generator draws: `stempel` (company stamp) and
# `foto_buram` (an out-of-focus photo); `tulisan_tangan`, `nama_file_salah` and `lampiran` are never generated.
LABEL_COLUMNS = ["file", "kelompok", "vendor", "jenis_pdf", "nama_karyawan", "nik", "posisi", "entitas",
                 "tanggal_mulai", "tanggal_selesai", "status_ttd", "halaman_identitas", "halaman_periode",
                 "halaman_ttd", "kasus", "bentuk"]


def label_row(rec):
    m = rec["meta"]
    return {
        "file": rec["file_name"], "kelompok": m["kelompok"], "vendor": m["company"],
        "jenis_pdf": "native" if m["form"] == "digital" else "scan",
        "nama_karyawan": rec["employee_name"], "nik": rec["employee_nik"] or "",
        "posisi": rec["position"] or "", "entitas": rec["entitas"] or "",
        "tanggal_mulai": rec["contract_start"], "tanggal_selesai": rec["contract_end"],
        "status_ttd": "signed" if rec["has_employee_signature"] else "unsigned",
        "halaman_identitas": 1, "halaman_periode": 1, "halaman_ttd": 2,
        "kasus": ";".join(k for k, on in (("stempel", m["has_company_stamp"]), ("foto_buram", m["photo_blurry"])) if on),
        "bentuk": m["form"],
    }


def write_labels(records, path):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(label_row(r) for r in records)


# --------------------------------------------------------------------------------------------------------------
# Generate
# --------------------------------------------------------------------------------------------------------------


GENERATED_NAMES = {"pdf", "ground_truth", "ground_truth.json", "labels.csv"}


def prepare_output(out_dir):
    """Create out_dir. A previous run there is replaced; a folder holding anything else is never touched."""
    if out_dir.exists():
        if not out_dir.is_dir():
            sys.exit(f"{out_dir} is a file, not a folder")
        other = sorted(p.name for p in out_dir.iterdir() if p.name not in GENERATED_NAMES)
        if other:
            sys.exit(f"{out_dir} holds files this script did not make ({', '.join(other[:5])}); "
                     "pick an empty or new folder with --out")
        if CONFIG["clean_output"]:
            shutil.rmtree(out_dir)
    (out_dir / "pdf").mkdir(parents=True, exist_ok=True)
    (out_dir / "ground_truth").mkdir(parents=True, exist_ok=True)


def generate(out_dir):
    prepare_output(out_dir)
    started, started_at = time.perf_counter(), dt.datetime.now()
    records, used_names, seconds = [], set(), []  # seconds: each contract's, kept out of the ground truth
    batches = plan_batches(random.Random(CONFIG["seed"] * 7_919 + 1), CONFIG["n_docs"])
    for i in range(CONFIG["n_docs"]):
        one = time.perf_counter()
        pdf_bytes, rec = generate_one(i, used_names, batches[i])
        (out_dir / "pdf" / rec["file_name"]).write_bytes(pdf_bytes)
        stem = rec["file_name"][:-4]
        (out_dir / "ground_truth" / f"{stem}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2),
                                                              encoding="utf-8")
        records.append(rec)
        seconds.append(time.perf_counter() - one)
        if (i + 1) % 10 == 0 or i + 1 == CONFIG["n_docs"]:
            elapsed = time.perf_counter() - started
            left = elapsed / (i + 1) * (CONFIG["n_docs"] - i - 1)
            print(f"{i + 1}/{CONFIG['n_docs']} done, {duration(elapsed)}" + (f", about {duration(left)} left" if left else ""))

    (out_dir / "ground_truth.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    write_labels(records, out_dir / "labels.csv")
    if CONFIG["zip_output"]:
        print("zip:", shutil.make_archive(str(out_dir), "zip", out_dir))

    print(f"\n{len(records)} contracts in {time.perf_counter() - started:.0f} s -> {out_dir}")
    print("forms:", dict(Counter(r["meta"]["form"] for r in records)))
    print("employee signed:", dict(Counter(r["has_employee_signature"] for r in records)))
    print("signature styles:", dict(Counter(r["meta"]["signature_style"] for r in records)))
    print("filename styles:", dict(Counter(r["meta"]["filename_style"] for r in records)))
    print("filename ' - ' separator:", dict(Counter(r["meta"]["filename_dash"] for r in records)))
    print("ENTITAS in filename:", dict(Counter(r["entitas"] is not None for r in records)))
    print("entities:", dict(Counter(r["meta"]["entitas_code"] for r in records)))
    print("position printed on page 1:", dict(Counter(r["position"] is not None for r in records)))
    print("NIK printed on page 1:", dict(Counter(r["employee_nik"] is not None for r in records)))
    print("scan modes:", dict(Counter(r["meta"]["scan_mode"] for r in records if r["meta"]["scan_mode"])))
    photo_pages = [p for r in records for p in (r["meta"]["photo_pages"] or [])]
    if photo_pages:
        fills = sorted(p["fill"] for p in photo_pages)
        keys = sorted(p["keystone"] for p in photo_pages)
        print(f"photo pages: {len(photo_pages)}; paper share of frame {fills[0]:.2f}-{fills[-1]:.2f} "
              f"(median {fills[len(fills) // 2]:.2f}); keystone up to {keys[-1]:.2f} (median {keys[len(keys) // 2]:.2f}); "
              f"turned {sum(1 for p in photo_pages if p['turn'])}; "
              f"clutter {sum(1 for r in records if r['meta']['photo_clutter'])} docs, "
              f"blurry {sum(1 for r in records if r['meta']['photo_blurry'])} docs")
    print("kelompok:", dict(Counter(r["meta"]["kelompok"] for r in records)))
    print("vendors:", dict(Counter(r["meta"]["company"] for r in records)))
    folders = Counter((r["meta"]["company"], r["meta"]["period_folder"]) for r in records)
    print(f"vendor/period folders: {len(folders)}, contracts per folder: {sorted(folders.values())}")
    print_timing(records, seconds, started_at, time.perf_counter() - started)


def duration(seconds):
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{seconds:.1f} s"


def print_timing(records, seconds, started_at, total):
    """How long the run and each contract took, overall and per form (digital, scan, photo)."""
    finished_at = started_at + dt.timedelta(seconds=total)
    print(f"\nTime\n  total          {duration(total)} for {len(records)} contracts "
          f"({started_at:%H:%M:%S} to {finished_at:%H:%M:%S})")
    print(f"  per contract   {statistics.mean(seconds):.1f} s on average, median {statistics.median(seconds):.1f} s, "
          f"fastest {min(seconds):.1f} s, slowest {max(seconds):.1f} s")
    for form in ("digital", "scan", "photo"):
        took = [s for s, r in zip(seconds, records) if r["meta"]["form"] == form]
        if took:
            print(f"  {form:<14} {statistics.mean(took):.1f} s on average, slowest {max(took):.1f} s "
                  f"({len(took)} contracts, {sum(took) / total:.0%} of the time)")
    print("  slowest contracts:")
    for s, r in sorted(zip(seconds, records), key=lambda x: -x[0])[:3]:
        print(f"    {s:5.1f} s  {r['meta']['form']:<8} {r['file_name']}")


def main():
    global VENDORS, ENTITAS
    default_out = Path(CONFIG["output_dir"]) if CONFIG["output_dir"] else SCRIPT_DIR / "output"
    parser = argparse.ArgumentParser(description="Generate synthetic 2-page PKWT contract PDFs with ground truth.")
    parser.add_argument("-n", "--n-docs", type=int, default=CONFIG["n_docs"],
                        help=f"number of contracts (default {CONFIG['n_docs']})")
    parser.add_argument("--seed", type=int, default=CONFIG["seed"],
                        help=f"random seed; the same seed gives the same contracts (default {CONFIG['seed']})")
    parser.add_argument("-o", "--out", type=Path, default=default_out, help=f"output folder (default {default_out})")
    args = parser.parse_args()
    if args.n_docs < 1:
        parser.error("--n-docs must be at least 1")
    CONFIG.update(n_docs=args.n_docs, seed=args.seed)

    setup_fonts()
    VENDORS = make_vendor_pool(CONFIG["n_vendors"] or 0, CONFIG["seed"])
    ENTITAS = make_entitas_pool(CONFIG["n_entitas"] or 0, CONFIG["seed"], {v["company"] for v in VENDORS})
    generate(args.out.resolve())


if __name__ == "__main__":
    main()
