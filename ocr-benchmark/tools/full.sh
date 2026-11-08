#!/usr/bin/env bash
# Full benchmark: each engine on all 800 contracts, one after another (an engine never shares the CPU with another).
# Usage: tools/full.sh engine[:gray] ...     A stopped engine resumes where it stopped.
set -u
cd "$(dirname "$0")/.."
export PATH="/c/Users/ASUS/AppData/Local/Microsoft/WinGet/Links:$PATH"
for spec in "$@"; do
  engine="${spec%%:*}"; extra=""; key="$engine"
  if [[ "$spec" == *":gray" ]]; then extra="--gray --tag gray"; key="$engine-gray"; fi
  echo "=== $key $(date +%T)"
  .venv/Scripts/python -m bench.run "$engine" $extra 2>&1 | grep -vE "Warning|warn\(|it/s\]|^\s*$" | grep -E "files/h|done|Error|error" | tail -4
  bun bench/to_rows.ts "runs/$key/files.jsonl"
done
echo "=== all done $(date +%T)"
