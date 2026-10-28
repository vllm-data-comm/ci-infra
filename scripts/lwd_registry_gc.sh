#!/usr/bin/env bash
# Reclaim the blobs of manifests deleted by lwd_build_image.sh's
# retention. Registry garbage collection requires a quiet registry, so
# this stops the service first and refuses to run during a build.
#
# Schedule weekly, outside the nightly build window, e.g. crontab:
#   20 4 * * 0  cd /path/to/ci-infra && scripts/lwd_registry_gc.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
COMPOSE_FILE="$REPO_ROOT/docker/registry/docker-compose.yml"
CONF_FILE="$SCRIPT_DIR/lwd_image_builder.conf"

# Defaults; the conf file overrides these.
WORK_DIR=/data/lwd-image-builder

# Exported sourcing: docker compose reads LWD_REGISTRY_DATA (+ optional
# LWD_REGISTRY_PORT) from the environment.
set -a
# shellcheck disable=SC1090
[ -f "$CONF_FILE" ] && . "$CONF_FILE"
set +a

log() { printf '[LWD] %(%F %T)T %s\n' -1 "$*"; }
die() { log "ERROR: $*"; exit 1; }

docker info >/dev/null 2>&1 \
    || die "docker daemon unreachable (run as root or with the docker group)"

mkdir -p "$WORK_DIR/logs" "$WORK_DIR/state"
exec 9>"$WORK_DIR/state/build.lock"
flock -n 9 || die "a build is running; garbage collection needs a quiet registry"

log "stopping the registry for garbage collection"
docker compose -f "$COMPOSE_FILE" stop registry

# GC reads the mounted blob store only; the default config inside the
# image points at /var/lib/registry, which is where we mount it.
docker run --rm \
    -v "${LWD_REGISTRY_DATA:?set LWD_REGISTRY_DATA}:/var/lib/registry" \
    registry:2.8.3 garbage-collect /etc/docker/registry/config.yml \
    | tee "$WORK_DIR/logs/registry-gc-$(date -u +%Y%m%dT%H%M%S).log"

docker compose -f "$COMPOSE_FILE" up -d registry
# REGISTRY already carries the port (host:port), like everywhere else.
curl -fsS "${REGISTRY_SCHEME:-http}://${REGISTRY:-localhost:5000}/v2/" >/dev/null \
    || die "registry did not come back after GC"
log "registry garbage collection done"
