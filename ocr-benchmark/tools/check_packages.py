"""Vulnerability (OSV) and license check of Python packages, before or after they are installed.

    python tools/check_packages.py rapidocr==3.10.0 onnxruntime==1.31.1      # named packages, from PyPI
    python tools/check_packages.py --installed                               # everything in this interpreter

Prints one line per package: version, release date, declared license, and the OSV advisories for that exact version.
Only package names and versions are sent (to pypi.org and api.osv.dev), never project files.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from importlib import metadata

OSV_BATCH = "https://api.osv.dev/v1/querybatch"


def _get(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"} if data else {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def pypi_info(name: str, version: str) -> tuple[str, str]:
    try:
        info = _get(f"https://pypi.org/pypi/{name}/{version}/json")
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        return "?", f"pypi error {exc}"
    meta = info["info"]
    license_text = meta.get("license_expression") or meta.get("license") or ""
    if not license_text or len(license_text) > 60:
        classifiers = [c.split(" :: ")[-1] for c in meta.get("classifiers", []) if c.startswith("License ::")]
        license_text = "; ".join(classifiers) or (license_text[:60] + "…" if license_text else "UNKNOWN")
    uploaded = min((f["upload_time"][:10] for f in info.get("urls", [])), default="?")
    return uploaded, license_text


def installed_license(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    text = meta.get("License-Expression") or meta.get("License") or ""
    if not text or len(text) > 60:
        classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        text = "; ".join(classifiers) or (text[:60] + "…" if text else "UNKNOWN")
    return text


def osv(packages: list[tuple[str, str]]) -> list[list[str]]:
    found: list[list[str]] = []
    for start in range(0, len(packages), 500):
        chunk = packages[start : start + 500]
        queries = [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in chunk]
        results = _get(OSV_BATCH, {"queries": queries})["results"]
        found.extend([v["id"] for v in r.get("vulns", [])] for r in results)
    return found


def main() -> None:
    args = sys.argv[1:]
    if args == ["--installed"]:
        dists = sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower())
        rows = [(d.metadata["Name"], d.version, "", installed_license(d)) for d in dists]
    else:
        rows = []
        for arg in args:
            name, version = arg.split("==")
            uploaded, license_text = pypi_info(name, version)
            rows.append((name, version, uploaded, license_text))
    advisories = osv([(n, v) for n, v, _, _ in rows])
    for (name, version, uploaded, license_text), ids in zip(rows, advisories):
        print(f"{name:32} {version:14} {uploaded:11} {license_text[:58]:58} {'OSV: ' + ', '.join(ids) if ids else 'OSV: none'}")


if __name__ == "__main__":
    main()
