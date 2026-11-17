"""Vendor: the outsourcing company that recruits and employs the employee. It is the first folder under the root
(`root/<vendor>/<masa kontrak>/<pdf>`) and always printed in the contract, so the folder's name is looked up in the
document text instead of being read out of it.

Legal forms (PT, CV, Tbk, Persero), punctuation, case and spaces are ignored: `PT. Sarana Perkasa` is found in
`PT SARANA PERKASA` and in OCR's `SARANAPERKASA`. A folder named by an abbreviation the contract never prints is not
found and goes to review.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# The vendor's words are compared with every run of as many words in the text (one fewer or more, for OCR splitting
# or joining a word), spaces dropped. On 236 synthetic contracts with a read identity page the folder's vendor scored
# at least 95.6, and another vendor at most 88.9 ("Sarana Ekasarana" against "Sarana Perkasa"). 90 still lets one
# wrong letter in a vendor name of about ten letters through.
VENDOR_IN_TEXT_MIN = 90
_LEGAL_FORM = re.compile(r"\b(pt|cv|tbk|persero)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_vendor(text: str) -> str:
    return " ".join(_LEGAL_FORM.sub(" ", _NON_ALNUM.sub(" ", text.casefold())).split())


def vendor_in_text(vendor: str, text: str) -> bool:
    """Whether the folder's vendor is in the text. The text may be a whole page or a company name read from the
    document, which may carry extra words (`PT Sarana Perkasa Indonesia`)."""
    name, words = normalize_vendor(vendor).split(), normalize_vendor(text).split()
    if not name or not words:
        return False
    target = "".join(name)
    for size in {max(len(name) - 1, 1), len(name), len(name) + 1}:
        for start in range(max(len(words) - size, 0) + 1):
            if fuzz.ratio(target, "".join(words[start : start + size])) >= VENDOR_IN_TEXT_MIN:
                return True
    return False
