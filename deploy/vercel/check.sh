#!/usr/bin/env bash
# Smoke-test a front URL (a Vercel preview or the final domain) in front of the docex origin.
# Usage: bash deploy/vercel/check.sh https://your-project.vercel.app
# Checks the three things Vercel's docs do not settle: an http://IP:port origin, a ~9.5 MB upload, and the
# cookie round trip. Uses approach A only, so it never calls a paid or quota-limited model.
set -euo pipefail

BASE="${1:?usage: check.sh https://front-url}"
BASE="${BASE%/}"
SAMPLE="${SAMPLE:-demo_samples/faktur_pajak-medium-0002.jpg}"
JAR="$(mktemp)"
BIG="$(mktemp)"
trap 'rm -f "$JAR" "$BIG"' EXIT

echo "== 1. /health through the front"
curl -fsS --max-time 20 "$BASE/health"
echo

echo "== 2. upload the sample, then read the result with the same cookie"
ID="$(curl -fsS --max-time 60 -c "$JAR" -b "$JAR" -F "file=@$SAMPLE" -F approach=A "$BASE/documents" |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("job_id") or d["id"])')"
echo "job $ID"
for _ in $(seq 1 30); do
  STATUS="$(curl -fsS -b "$JAR" "$BASE/documents/$ID" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status"))')"
  echo "status: $STATUS"
  [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] && break
  sleep 2
done
[ "$STATUS" = "done" ] || { echo "FAIL: result is '$STATUS', not done"; exit 1; }

echo "== 3. another visitor (no cookie) must not see it"
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$BASE/documents/$ID")"
[ "$CODE" = "404" ] && echo "ok: 404 without the cookie" || { echo "FAIL: got $CODE without the cookie"; exit 1; }

echo "== 4. ~9.5 MB upload (the app limit is 10 MB)"
cp "$SAMPLE" "$BIG"
head -c $((9500000 - $(wc -c < "$SAMPLE"))) /dev/zero >> "$BIG"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 120 -c "$JAR" -b "$JAR" -F "file=@$BIG;filename=big.jpg" -F approach=A "$BASE/documents")"
echo "HTTP $CODE (200 or 202 = the front passed the body; 413 from the app is also fine; 413/502 from Vercel is not)"
echo "== done"
