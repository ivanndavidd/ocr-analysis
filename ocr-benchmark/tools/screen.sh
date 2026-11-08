#!/usr/bin/env bash
# Screening: every engine variant on the same 60 random scans and photos, one after another.
set -u
cd "$(dirname "$0")/.."
export PATH="/c/Users/ASUS/AppData/Local/Microsoft/WinGet/Links:$PATH"
for spec in "$@"; do
  engine="${spec%%:*}"; extra=""; tag="screen"
  if [[ "$spec" == *":gray" ]]; then extra="--gray"; tag="screen-gray"; fi
  echo "=== $engine $extra $(date +%T)"
  .venv/Scripts/python -m bench.run "$engine" --form scan --form photo --sample 60 --tag "$tag" $extra 2>&1 | grep -vE "Warning|warn\(|it/s\]|^\s*$" | tail -3
  bun bench/to_rows.ts "runs/$engine-$tag/files.jsonl"
done
echo "=== all done $(date +%T)"
