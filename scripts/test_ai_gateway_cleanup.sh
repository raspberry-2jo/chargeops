#!/usr/bin/env bash
# Run manually on a Docker-capable test Agent checkout. Never pushes or deploys.
set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
IMAGE="chargeops-ai-gateway:trivy-cleanup-test"
TEST_CONTAINER=""

cleanup() {
  result=$?
  trap - EXIT
  if [ -n "$TEST_CONTAINER" ]; then
    if ! docker rm --force "$TEST_CONTAINER" >/dev/null; then
      echo "Could not remove test container: $TEST_CONTAINER" >&2
      result=1
    fi
  fi
  exit "$result"
}
trap cleanup EXIT

cd "$(git rev-parse --show-toplevel)"
printf 'Testing source: %s\n' "$(git rev-parse HEAD)"
"$PYTHON_BIN" -m pytest -q tests/
"$PYTHON_BIN" scripts/ci.py validate

docker build --pull --no-cache \
  -f apps/ai-gateway/Dockerfile \
  -t "$IMAGE" apps

# Actual final-runtime check: no pip/setuptools/wheel, dependencies and app import.
docker run --rm --network none --entrypoint python "$IMAGE" \
  -m ai_gateway.runtime_check

# Boot the real image CMD. No DB/LLM secrets, external network or published port.
TEST_CONTAINER="$(docker run --detach --network none "$IMAGE")"
healthy=false
for attempt in {1..30}; do
  if docker exec "$TEST_CONTAINER" python -c '
import json
from urllib.request import urlopen
for path in ("/healthz", "/cicd-healthz"):
    with urlopen("http://127.0.0.1:8000" + path, timeout=2) as response:
        assert response.status == 200
        body = json.load(response)
        if path == "/healthz": assert body.get("ok") is True
        else: assert body.get("service") == "ai-gateway" and body.get("status") == "ok"
' >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 2
done
if [ "$healthy" != true ]; then
  docker logs "$TEST_CONTAINER"
  echo 'AI Gateway HTTP startup checks failed' >&2
  exit 1
fi
echo 'PASS: final-runtime imports and actual HTTP health endpoints; DB/LLM not verified'
