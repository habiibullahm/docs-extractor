#!/usr/bin/env bash
# Build and (re)start the single-container demo on a VPS.
# Usage: bash deploy/vps.sh            (port 7860)
#        PORT=8080 bash deploy/vps.sh
# Uses sudo for docker when the current user cannot reach the docker socket.
set -euo pipefail

PORT="${PORT:-7860}"
NAME="${NAME:-docex}"
APPROACH="${DOCEX_APPROACH:-A}"
ENV_FILE="${ENV_FILE:-.env}"

cd "$(dirname "$0")/.."

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
  echo "docker needs sudo for this user; using: $DOCKER"
fi

echo "== git pull"
git pull --ff-only || echo "git pull skipped (local changes or no upstream)"

echo "== docker build (first build takes a few minutes)"
$DOCKER build -t "$NAME" .

echo "== restart container $NAME on port $PORT"
$DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
env_args=()
if [ -f "$ENV_FILE" ]; then
  env_args=(--env-file "$ENV_FILE")
  echo "using env file $ENV_FILE (values not printed)"
fi
$DOCKER run -d --name "$NAME" --restart unless-stopped \
  -p "$PORT:7860" -v docex-data:/app/data \
  -e DOCEX_APPROACH="$APPROACH" "${env_args[@]}" "$NAME" >/dev/null

echo "== waiting for /health"
for _ in $(seq 1 30); do
  if curl -fs "localhost:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fs "localhost:$PORT/health" || { echo "health check failed; logs:"; $DOCKER logs --tail 50 "$NAME"; exit 1; }
echo

IP="$(curl -fs --max-time 5 https://ifconfig.me || hostname -I | awk '{print $1}')"
echo "== up: http://$IP:$PORT"
echo "If that URL does not open from your laptop, allow TCP $PORT in the VPS firewall / cloud security group."
