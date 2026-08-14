#!/usr/bin/env bash
#
# Set up the DeepLabCut webapp stack on a fresh machine.
#
# The two repositories are coupled by RELATIVE PATH, not by submodule or
# registry: the main repo's docker-compose.yml reaches its sibling with
# `../deeplabcut-webapp-docker-supports/...` for both build contexts and ~20
# bind mounts. So the only layout that works is
#
#     <parent>/
#     ├── deeplabcut-webapp-docker/           <- compose lives here
#     └── deeplabcut-webapp-docker-supports/  <- name is hard-coded
#
# Rename either directory and the build fails with a confusing "not found" on a
# path nobody typed. This script exists mostly to make that layout automatic.
#
# The parent directory also matters for a second reason: the dlc-3d service
# builds with `context: ../`, so EVERYTHING beside these two repos is tarred up
# and handed to the Docker daemon. Keep the parent dedicated to this stack. On
# the machine this script was written for, a shared parent meant a 162 GB build
# context for 4.5 GB of repository.
#
# Usage:
#     ./install.sh fetch                 clone or update both repositories
#     ./install.sh build [component...]  build images (default: all)
#     ./install.sh up    [component...]  start services (default: all)
#     ./install.sh status                what is running
#     ./install.sh doctor                check prerequisites and host paths
#
# Components: core (flask + workers + redis), dlc-3d, sam-training
set -euo pipefail

MAIN_REPO="https://github.com/samsam2610/deeplabcut-webapp-docker.git"
SUPPORTS_REPO="https://github.com/samsam2610/deeplabcut-webapp-docker-supports.git"
MAIN_DIR="deeplabcut-webapp-docker"
SUPPORTS_DIR="deeplabcut-webapp-docker-supports"

# The stack root is the PARENT of this script's repository, so running the
# script from a clone puts the sibling in the right place automatically.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${DLC_STACK_ROOT:-$(dirname "$HERE")}"

# Service names as docker compose knows them, grouped so a component can be
# built or started on its own — the GPU images are large and slow, and there is
# no reason to rebuild the TensorFlow worker to try a UI change.
CORE_SERVICES="redis data-init flask worker worker-tf"
DLC3D_SERVICES="dlc-3d dlc-3d-worker"
SAM_SERVICES="sam-training"

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
step()   { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()    { c_red "error: $*"; exit 1; }

services_for() {
    case "${1:-all}" in
        core)         echo "$CORE_SERVICES" ;;
        dlc-3d|dlc3d) echo "$DLC3D_SERVICES" ;;
        sam|sam-training) echo "$SAM_SERVICES" ;;
        all)          echo "" ;;          # empty = every service in the file
        *)            die "unknown component '$1' (core | dlc-3d | sam-training | all)" ;;
    esac
}

compose() {
    ( cd "$ROOT/$MAIN_DIR" && docker compose "$@" )
}

# ── prerequisites ───────────────────────────────────────────────────────────

cmd_doctor() {
    step "Prerequisites"
    local ok=1
    for c in git docker; do
        if command -v "$c" >/dev/null; then c_grn "  $c: $(command -v "$c")"
        else c_red "  $c: MISSING"; ok=0; fi
    done
    if docker compose version >/dev/null 2>&1; then
        c_grn "  docker compose: $(docker compose version --short 2>/dev/null)"
    else
        c_red "  docker compose: MISSING (this stack needs Compose v2, not docker-compose)"
        ok=0
    fi
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
        c_grn "  nvidia container runtime: present"
    else
        c_ylw "  nvidia container runtime: not detected — the workers will start but"
        c_ylw "    training and analysis need a GPU"
    fi

    step "Layout"
    printf '  stack root: %s\n' "$ROOT"
    for d in "$MAIN_DIR" "$SUPPORTS_DIR"; do
        if [ -d "$ROOT/$d" ]; then c_grn "  $d: present"
        else c_ylw "  $d: missing — run './install.sh fetch'"; fi
    done
    # A shared parent is the difference between a 4.5 GB and a 162 GB build
    # context, and nothing warns you at build time.
    local extra
    extra=$(find "$ROOT" -maxdepth 1 -mindepth 1 ! -name "$MAIN_DIR" ! -name "$SUPPORTS_DIR" \
                 ! -name '.*' -printf '%f\n' 2>/dev/null | head -5)
    if [ -n "$extra" ]; then
        c_ylw "  other entries beside the repos (these enter the dlc-3d build context):"
        printf '    %s\n' $extra
        c_ylw "    consider giving this stack a parent directory of its own"
    fi

    step "Host paths in docker-compose.yml"
    c_ylw "  These are absolute and machine-specific. Edit them before first start:"
    if [ -f "$ROOT/$MAIN_DIR/docker-compose.yml" ]; then
        # Strip only the leading "  - " list marker. `tr -d ' -'` would also eat
        # the hyphens inside the paths themselves, turning data-disk/Parra-Data
        # into datadisk/ParraData — a path that looks plausible and does not exist.
        grep -oE '^[[:space:]]+- /home/[^:]+' "$ROOT/$MAIN_DIR/docker-compose.yml" \
            | sed -E 's/^[[:space:]]*-[[:space:]]*//' | sort -u | sed 's/^/    /'
    fi
    c_ylw "  Also: the sam-training service mounts a Hugging Face token read-only."
    c_ylw "  SAM 3 and DINOv3 are gated repos; without it that service cannot fetch weights."

    [ "$ok" = 1 ] || die "install prerequisites first"
}

# ── fetch ───────────────────────────────────────────────────────────────────

clone_or_update() {
    local url="$1" dir="$2"
    if [ -d "$ROOT/$dir/.git" ]; then
        step "Updating $dir"
        # Never clobber local work: fetch and report, let the operator merge.
        ( cd "$ROOT/$dir" && git fetch --all --prune && git status -sb | head -3 )
    else
        step "Cloning $dir"
        git clone "$url" "$ROOT/$dir"
    fi
}

cmd_fetch() {
    mkdir -p "$ROOT"
    clone_or_update "$MAIN_REPO" "$MAIN_DIR"
    clone_or_update "$SUPPORTS_REPO" "$SUPPORTS_DIR"
    step "Layout check"
    [ -d "$ROOT/$MAIN_DIR" ] && [ -d "$ROOT/$SUPPORTS_DIR" ] \
        || die "both repositories must sit side by side under $ROOT"
    c_grn "  ok: $ROOT/{$MAIN_DIR,$SUPPORTS_DIR}"
    c_ylw "  next: ./install.sh doctor   (check prerequisites and host paths)"
}

# ── build / up ──────────────────────────────────────────────────────────────

cmd_build() {
    [ -d "$ROOT/$SUPPORTS_DIR" ] || die "$SUPPORTS_DIR is missing — run './install.sh fetch' first"
    local comps=("$@"); [ ${#comps[@]} -eq 0 ] && comps=(all)
    for comp in "${comps[@]}"; do
        step "Building $comp"
        # Unquoted on purpose: an empty string must expand to no arguments,
        # which is how compose is told "every service".
        compose build $(services_for "$comp")
    done
}

cmd_up() {
    local comps=("$@"); [ ${#comps[@]} -eq 0 ] && comps=(all)
    for comp in "${comps[@]}"; do
        step "Starting $comp"
        compose up -d $(services_for "$comp")
    done
    step "Status"
    compose ps
    c_grn "  UI: http://localhost:5000"
}

cmd_status() { compose ps; }

case "${1:-}" in
    fetch)  shift; cmd_fetch "$@" ;;
    build)  shift; cmd_build "$@" ;;
    up)     shift; cmd_up "$@" ;;
    status) shift; cmd_status "$@" ;;
    doctor) shift; cmd_doctor "$@" ;;
    *)      sed -n '3,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ; exit 1 ;;
esac
