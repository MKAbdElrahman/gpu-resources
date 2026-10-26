#!/usr/bin/env bash
# Live smoke against dstack. Replace DIGEST with a real image digest before running.
# This spends cloud money; it verifies allocate → run → artifacts → release.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$ROOT/results/smoke-$(date +%Y%m%d-%H%M%S)}"
# Public CUDA runtime example — pin by digest (docker buildx imagetools inspect ...).
IMAGE="${IMAGE:-pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime}"

if [[ "$IMAGE" != *@sha256:* ]]; then
  echo "Set IMAGE to a digest-pinned reference, e.g.:" >&2
  echo "  IMAGE=pytorch/pytorch@sha256:... $0" >&2
  echo "Current IMAGE=$IMAGE" >&2
  exit 2
fi

mkdir -p "$OUT"
gpu-resources run \
  --image "$IMAGE" \
  --max-duration "${MAX_DURATION:-10m}" \
  --command 'nvidia-smi && mkdir -p "$ARTIFACTS_DIR" && echo PASS > "$ARTIFACTS_DIR/smoke.txt" && cat "$ARTIFACTS_DIR/smoke.txt"' \
  --out "$OUT"

echo "Evidence: $OUT"
test -f "$OUT/summary.json"
python3 -c "import json,sys; s=json.load(open(sys.argv[1])); assert s['resource_release_verified']; assert s['result']=='PASS'" "$OUT/summary.json"
test -f "$OUT/artifacts/smoke.txt"
echo "Smoke OK"
