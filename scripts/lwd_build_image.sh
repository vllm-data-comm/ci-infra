#!/usr/bin/env bash
# Build the LWD images on the internal builder machine and push them to
# the internal registry (docker/registry/docker-compose.yml). Deployment
# and operation are documented in docs/design/lwd_image_internal.md.
#
# Semantics mirror .github/workflows/lwd_image.yml on purpose: same base
# tag mapping, same tag scheme, same Dockerfile. When the mapping below
# changes, keep the workflow's resolve step and Dockerfile.lwd's BASE_TAG
# default in sync (grep BASE_TAG across the repo).
#
# Configuration: copy scripts/lwd_image_builder.conf.example to
# scripts/lwd_image_builder.conf (gitignored) and adjust per site.
# Command line flags override the conf file.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
CONF_FILE="$SCRIPT_DIR/lwd_image_builder.conf"

# Site defaults; the conf file overrides these, flags override the conf.
WORK_DIR=/data/lwd-image-builder
REGISTRY=
REGISTRY_SCHEME=http
REGISTRY_USER=
REGISTRY_PASSWORD=
VLLM_REMOTE=https://github.com/vllm-data-comm/vllm.git
VLLM_ASCEND_REMOTE=https://github.com/vllm-data-comm/vllm-ascend.git
KEEP_DAYS=14
MIN_FREE_GB=40

# Run defaults.
SOC_LIST="a2 a3"
VLLM_REF=main
VLLM_ASCEND_REF=main
BASE_TAG_OVERRIDE=
PUSH=true
MOVE_NIGHTLY=false
IMAGE_NAME=lwd

log() { printf '[LWD] %(%F %T)T %s\n' -1 "$*"; }
die() { log "ERROR: $*"; exit 1; }

usage() {
    cat <<'EOF'
usage: lwd_build_image.sh [flags]

Build the LWD images (docker/Dockerfile.lwd) and push them to the
internal registry. Nightly cron passes --nightly; a manual run without
it never moves the rolling nightly tag.

flags:
  --soc a2|a3|both      SoC variants to build (default: both)
  --vllm-ref REF        vllm fork ref: branch, tag or sha (default: main)
  --vllm-ascend-ref REF vllm-ascend fork ref (default: main)
  --base-tag TAG        override the pinned official base tag for all
                        variants (same semantics as the workflow input)
  --registry HOST:PORT  push target; default from the conf file
  --no-push             build and load locally, do not touch the registry
  --nightly             also move the rolling nightly[-a3] tag
  --keep-days N         prune registry manifests older than N days
                        (default from the conf file, 14)
  --min-free-gb N       fail fast when work dir or docker data root has
                        less than N GB free (default from conf, 40)
  -h, --help            show this help
EOF
}

# soc -> tag suffix and soc -> pinned official base tag. Keep in sync
# with the workflow's resolve step and Dockerfile.lwd's BASE_TAG default.
variant_suffix() { case "$1" in a2) echo "" ;; a3) echo "-a3" ;; *) return 1 ;; esac; }
variant_base_tag() { case "$1" in a2) echo "v0.30.0rc1" ;; a3) echo "v0.30.0rc1-a3" ;; *) return 1 ;; esac; }

if [ -f "$CONF_FILE" ]; then
    # shellcheck disable=SC1090
    . "$CONF_FILE"
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --soc)
            case "${2:-}" in
                both) SOC_LIST="a2 a3" ;;
                a2 | a3) SOC_LIST="$2" ;;
                *) die "--soc expects a2, a3 or both" ;;
            esac
            shift 2 ;;
        --vllm-ref) VLLM_REF=${2:?}; shift 2 ;;
        --vllm-ascend-ref) VLLM_ASCEND_REF=${2:?}; shift 2 ;;
        --base-tag) BASE_TAG_OVERRIDE=${2:?}; shift 2 ;;
        --registry) REGISTRY=${2:?}; shift 2 ;;
        --no-push) PUSH=false; shift ;;
        --nightly) MOVE_NIGHTLY=true; shift ;;
        --keep-days) KEEP_DAYS=${2:?}; shift 2 ;;
        --min-free-gb) MIN_FREE_GB=${2:?}; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) usage; die "unknown flag: $1" ;;
    esac
done

for cmd in git docker curl flock df; do
    command -v "$cmd" >/dev/null || die "$cmd is required but not found in PATH"
done

mkdir -p "$WORK_DIR/logs" "$WORK_DIR/state"
exec 9>"$WORK_DIR/state/build.lock"
flock -n 9 || die "another build holds $WORK_DIR/state/build.lock"

LOG_FILE="$WORK_DIR/logs/build-$(date -u +%Y%m%dT%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
log "build start: soc='$SOC_LIST' vllm='$VLLM_REF' vllm-ascend='$VLLM_ASCEND_REF' push=$PUSH"

JOURNAL="$WORK_DIR/state/journal.log"

# registry_api METHOD PATH: call the registry v2 API, with basic auth
# when the conf file carries credentials.
registry_api() {
    local method=$1 path=$2
    local auth=()
    [ -n "$REGISTRY_USER" ] && auth=(-u "$REGISTRY_USER:$REGISTRY_PASSWORD")
    curl -fsS "${auth[@]}" -X "$method" "$REGISTRY_SCHEME://$REGISTRY$path"
}

# manifest_digest TAG: the Docker-Content-Digest of a pushed tag, via HEAD.
manifest_digest() {
    local auth=()
    [ -n "$REGISTRY_USER" ] && auth=(-u "$REGISTRY_USER:$REGISTRY_PASSWORD")
    curl -fsSI "${auth[@]}" "$REGISTRY_SCHEME://$REGISTRY/v2/$IMAGE_NAME/manifests/$1" \
        | awk 'tolower($1) == "docker-content-digest:" { gsub(/\r/, ""); print $2 }'
}

check_free_gb() {
    local path=$1 label=$2 avail
    avail=$(df -BG --output=avail "$path" | tail -1 | grep -o '[0-9]*')
    [ "$avail" -ge "$MIN_FREE_GB" ] \
        || die "only ${avail}G free on $label (<${MIN_FREE_GB}G); free space or lower --min-free-gb"
}

# ensure_repo DEST REMOTE REF: fetch a persistent clone and detach it at
# REF (branch, tag or sha); echoes the short sha actually checked out.
ensure_repo() {
    local dest=$1 remote=$2 ref=$3 commit
    if [ ! -d "$dest/.git" ]; then
        log "cloning $remote"
        git clone --quiet "$remote" "$dest"
    fi
    git -C "$dest" fetch --quiet --prune --tags origin
    commit=$(git -C "$dest" rev-parse --verify -q "refs/remotes/origin/$ref^{commit}" \
        || git -C "$dest" rev-parse --verify -q "refs/tags/$ref^{commit}" \
        || git -C "$dest" rev-parse --verify -q "$ref^{commit}") \
        || die "cannot resolve ref '$ref' of $remote (fetched, but not found)"
    git -C "$dest" checkout --quiet --detach "$commit"
    git -C "$dest" rev-parse --short HEAD
}

prepare_context() {
    local ctx="$WORK_DIR/ctx"
    mkdir -p "$ctx/ci"
    # Checkouts must live INSIDE the build context: COPY resolves against
    # the context root, and .dockerignore keeps .git out of it.
    ensure_repo "$ctx/vllm" "$VLLM_REMOTE" "$VLLM_REF" >/dev/null
    ensure_repo "$ctx/vllm-ascend" "$VLLM_ASCEND_REMOTE" "$VLLM_ASCEND_REF" >/dev/null
    (cd "$REPO_ROOT" && tar \
        --exclude=.git --exclude=.claude --exclude=.venv --exclude=./vllm \
        --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache \
        -cf - .) | (cd "$ctx/ci" && tar -xf -)
    printf '**/.git\n' >"$ctx/.dockerignore"
}

# build_one SOC: resolve tags, build, push (or load), journal the digest.
build_one() {
    local soc=$1 suffix base_tag short_sha va_short_sha
    local tags=() build_tags=() repo_name output digest

    suffix=$(variant_suffix "$soc")
    if [ -n "$BASE_TAG_OVERRIDE" ]; then
        base_tag=$BASE_TAG_OVERRIDE
    else
        base_tag=$(variant_base_tag "$soc")
    fi
    short_sha=$(git -C "$WORK_DIR/ctx/vllm" rev-parse --short HEAD)
    va_short_sha=$(git -C "$WORK_DIR/ctx/vllm-ascend" rev-parse --short HEAD)

    tags+=("sha-${short_sha}${suffix}")
    if $PUSH; then
        tags+=("nightly-$(date -u +%Y%m%d)${suffix}")
        if $MOVE_NIGHTLY; then
            tags+=("nightly${suffix}")
        fi
    fi

    if $PUSH; then
        repo_name="$REGISTRY/$IMAGE_NAME"
        output=(--push)
    else
        repo_name="$IMAGE_NAME"
        output=(--load)
    fi
    local tag
    for tag in "${tags[@]}"; do
        build_tags+=(--tag "$repo_name:$tag")
    done

    log "building $soc: base=$base_tag vllm=${short_sha} vllm-ascend=${va_short_sha} tags=${tags[*]}"
    docker buildx build \
        --file "$WORK_DIR/ctx/ci/docker/Dockerfile.lwd" \
        --platform linux/arm64 \
        --build-arg BASE_TAG="$base_tag" \
        "${build_tags[@]}" \
        "${output[@]}" \
        "$WORK_DIR/ctx"

    if ! $PUSH; then
        log "$soc built locally as $repo_name:${tags[0]} (no push)"
        return 0
    fi

    digest=$(docker buildx imagetools inspect "$repo_name:${tags[0]}" \
        | awk '/^Digest:/ { print $2 }')
    [ -n "$digest" ] || die "cannot read the pushed digest of ${tags[0]}"
    echo "$(date -u +%s) $soc $short_sha $va_short_sha $digest" >>"$JOURNAL"
    log "$soc pushed: $repo_name:${tags[0]} ($digest)"
}

# prune_old_manifests: delete journaled manifests older than KEEP_DAYS.
# Deleting a manifest removes every tag on it, including its sha- tag;
# anything needing longer survival must be re-pinned or docker-saved.
# A digest still referenced by a rolling nightly tag is kept: someone
# rolled nightly back to it on purpose.
prune_old_manifests() {
    [ -s "$JOURNAL" ] || return 0
    local cutoff=$(( $(date -u +%s) - KEEP_DAYS * 86400 ))
    local -A pinned=()
    local soc suffix digest
    # Protect every variant's rolling nightly digest, not only the ones
    # built tonight: an unbuilt variant's nightly is necessarily older
    # than KEEP_DAYS and would otherwise be pruned out from under its tag.
    for soc in a2 a3; do
        suffix=$(variant_suffix "$soc")
        digest=$(manifest_digest "nightly${suffix}" || true)
        [ -n "$digest" ] && pinned[$soc]=$digest
    done

    local tmp="$JOURNAL.tmp" epoch v_sha va_sha kept=0 pruned=0
    : >"$tmp"
    while read -r epoch soc v_sha va_sha digest; do
        [ -n "${digest:-}" ] || continue
        if [ "$epoch" -ge "$cutoff" ] || [ "$digest" = "${pinned[$soc]:-}" ]; then
            echo "$epoch $soc $v_sha $va_sha $digest" >>"$tmp"
            kept=$((kept + 1))
            continue
        fi
        if registry_api DELETE "/v2/$IMAGE_NAME/manifests/$digest" >/dev/null 2>&1; then
            log "pruned $soc $digest (built $(date -u -d "@$epoch" +%F))"
            pruned=$((pruned + 1))
        else
            log "WARN: could not delete $digest, keeping the journal entry"
            echo "$epoch $soc $v_sha $va_sha $digest" >>"$tmp"
            kept=$((kept + 1))
        fi
    done <"$JOURNAL"
    mv "$tmp" "$JOURNAL"
    log "retention: kept $kept, pruned $pruned (keep-days=$KEEP_DAYS)"
}

main() {
    # The base tag mapping lives in three places; warn when the Dockerfile
    # default and this script drift apart.
    local dockerfile_default
    dockerfile_default=$(sed -n 's/^ARG BASE_TAG="\(.*\)"$/\1/p' \
        "$REPO_ROOT/docker/Dockerfile.lwd")
    [ "$dockerfile_default" = "$(variant_base_tag a2)" ] \
        || log "WARN: Dockerfile BASE_TAG default '$dockerfile_default' != script mapping '$(variant_base_tag a2)'; sync them"

    local docker_root
    docker_root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null) \
        || die "docker daemon unreachable"
    check_free_gb "$WORK_DIR" "work dir $WORK_DIR"
    check_free_gb "$docker_root" "docker data root $docker_root"

    if $PUSH; then
        [ -n "$REGISTRY" ] || die "no registry: set LWD_REGISTRY in $CONF_FILE (copy from the .example) or pass --registry"
        registry_api GET /v2/ >/dev/null \
            || die "registry $REGISTRY_SCHEME://$REGISTRY is unreachable"
    fi

    docker buildx inspect lwd-image-builder >/dev/null 2>&1 \
        || docker buildx create --name lwd-image-builder --driver docker-container
    docker buildx use lwd-image-builder >/dev/null

    prepare_context
    local soc
    for soc in $SOC_LIST; do
        build_one "$soc"
    done
    if $PUSH; then
        prune_old_manifests
    fi
    log "build done: log at $LOG_FILE"
}

main
