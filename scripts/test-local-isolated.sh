#!/usr/bin/env bash
# Run the complete MooBee IPTV pytest suite in an isolated, disposable Docker container.
# Requires Docker and an existing local checkout. Does not touch running services.
set -euo pipefail

cd "$(dirname "$0")/.."
APP_IMAGE="moobee-revocation-test:v0.8"
TEST_IMAGE="moobee-revocation-pytest:v0.8"

command -v docker >/dev/null || { echo "Docker required" >&2; exit 1; }
test -f Dockerfile && test -d tests && test -d scripts || {
  echo "Run this script from a complete MooBee IPTV checkout" >&2
  exit 1
}

echo "Building isolated application image..."
docker build -t "$APP_IMAGE" .

echo "Preparing disposable Python test image (pytest installation needs package access)..."
docker build -t "$TEST_IMAGE" - <<EOF
FROM $APP_IMAGE
USER root
RUN python -m pip install --no-cache-dir pytest
USER 10001
EOF

echo "Running tests without network access or writable repo mounts..."
docker run --rm \
  --network none \
  --read-only \
  --tmpfs /tmp:rw,nosuid,size=256m,mode=1777 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  -v "$PWD/tests:/srv/tests:ro" \
  -v "$PWD/scripts:/srv/scripts:ro" \
  --entrypoint python "$TEST_IMAGE" \
  -B -m pytest -q -p no:cacheprovider /srv/tests

echo "PASS: all isolated tests passed"
