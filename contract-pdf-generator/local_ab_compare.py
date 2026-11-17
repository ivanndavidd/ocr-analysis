"""Compare two local_ab.py runs against the job's truth: per field right / wrong / missing, and every file that changed.
Usage: local_ab_compare.py <job.json> <before.json> <after.json>"""
import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
job = json.load(open(sys.argv[1], encoding="utf-8"))
before = json.load(open(sys.argv[2], encoding="utf-8"))
after = json.load(open(sys.argv[3], encoding="utf-8"))
truth = {t["file_name"]: t for t in job["truth"]}
FIELDS = {
    "name": "employee_name",
    "nik": "employee_nik",
    "position": "position",
    "start": "contract_start",
    "end": "contract_end",
    "signature": "has_employee_signature",
}


def verdict(field, got, want):
    if field == "signature":
        if got == "uncertain" or got is None:
            return "missing"
        return "right" if (got == "signed") == bool(want) else "WRONG"
    if got is None:
        return "missing"
    same = got.casefold() == str(want).casefold() if field in ("name", "position") else got == str(want)
    return "right" if same else "WRONG"


def table(run):
    counts = {f: Counter() for f in FIELDS}
    for name, row in run.items():
        for field, key in FIELDS.items():
            counts[field][verdict(field, row[field], truth[name][key])] += 1
    return counts


cb, ca = table(before), table(after)
print(f"{'field':10} {'before right/WRONG/missing':>28} {'after right/WRONG/missing':>28}")
for f in FIELDS:
    b, a = cb[f], ca[f]
    print(f"{f:10} {b['right']:>14}/{b['WRONG']}/{b['missing']:<10} {a['right']:>14}/{a['WRONG']}/{a['missing']}")

full = lambda run, n: all(verdict(f, run[n][f], truth[n][k]) == "right" for f, k in FIELDS.items())
print("files with every field right:", sum(full(before, n) for n in before), "->", sum(full(after, n) for n in after))
print("decisions before", Counter(r["decision"] for r in before.values()), "after", Counter(r["decision"] for r in after.values()))
added = Counter(x for n in after for x in set(after[n]["reasons"]) - set(before[n]["reasons"]))
dropped = Counter(x for n in after for x in set(before[n]["reasons"]) - set(after[n]["reasons"]))
print("reasons added", dict(added))
print("reasons dropped", dict(dropped))
erased = [n for n in after if any("rules_erased" in c for cs in after[n]["components"].values() for c in cs)]
erased_before = [n for n in before if any("rules_erased" in c for cs in before[n]["components"].values() for c in cs)]
print("pages read with borders erased: before", len(erased_before), "after", len(erased))

print("\nchanged values (field: before -> after, verdicts); names cut to 28 chars, NIK shown by verdict only")
for n in sorted(after):
    changes = []
    for f, k in FIELDS.items():
        b, a = before[n][f], after[n][f]
        if b != a:
            vb, va = verdict(f, b, truth[n][k]), verdict(f, a, truth[n][k])
            shown = "" if f == "nik" else f" {b!r} -> {a!r}"
            changes.append(f"{f}:{shown} [{vb} -> {va}]")
    if changes:
        print(f"  {n[:28]:28} | {truth[n]['meta']['form']:7} | " + "; ".join(changes))
