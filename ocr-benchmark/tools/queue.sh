#!/usr/bin/env bash
# Run benchmark steps one after another. Each argument: "<engine> <extra bench.run args...>" (quoted).
set -u
cd "$(dirname "$0")/.."
export PATH="/c/Users/ASUS/AppData/Local/Microsoft/WinGet/Links:$PATH"
for step in "$@"; do
  read -r engine rest <<< "$step"
  tag=$(sed -n 's/.*--tag \([^ ]*\).*/\1/p' <<< "$rest")
  key="$engine${tag:+-$tag}"
  echo "=== $key $(date +%T)"
  .venv/Scripts/python -m bench.run $engine $rest 2>&1 | grep -E "files/h|done in|Error|Traceback" | tail -3
  bun bench/to_rows.ts "runs/$key/files.jsonl"
done
echo "=== queue done $(date +%T)"
