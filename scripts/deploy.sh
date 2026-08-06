#!/usr/bin/env bash
# Deploy = smoke-test the built container image: run it, hit /health, tear
# it down. Replace this with your real deploy commands (kubectl apply,
# ECS/Cloud Run deploy, etc) — the container is already built and loaded by
# this point in ci-pipeline.yml.
set -euo pipefail

IMAGE="${IMAGE_TAG:?IMAGE_TAG env var required, e.g. demo-app:<sha>}"
CONTAINER_NAME="demo-app-smoketest"

cleanup() {
  docker logs "$CONTAINER_NAME" 2>/dev/null || true
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Starting container from image: $IMAGE"
docker run -d --name "$CONTAINER_NAME" -p 8000:8000 "$IMAGE"

echo "Waiting for health check..."
ATTEMPTS=10
for i in $(seq 1 "$ATTEMPTS"); do
  # Set FORCE_DEPLOY_FAILURE=true as a repo/workflow variable to demo the
  # failure-analysis path without touching real infra.
  if [ "${FORCE_DEPLOY_FAILURE:-false}" = "true" ]; then
    echo "ERROR: health check target unreachable — DEPLOY_TARGET_URL misconfigured" >&2
    exit 1
  fi

  if curl -sf http://localhost:8000/health > /dev/null; then
    echo "Health check passed."
    echo "Deployment successful."
    exit 0
  fi

  echo "Attempt $i/$ATTEMPTS: not ready yet, retrying..."
  sleep 2
done

echo "ERROR: health check failed after $ATTEMPTS attempts" >&2
exit 1
