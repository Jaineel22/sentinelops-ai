#!/usr/bin/env bash
set -euo pipefail

output="${1:-artifacts/live-rca-demo.txt}"
mkdir -p "$(dirname "$output")"
{
  echo "SentinelOps AI — live RCA demo"
  echo "Recorded: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo
  bash scripts/live_rca_demo.sh
} | tee "$output"
echo "Saved formatted demo output to $output"
