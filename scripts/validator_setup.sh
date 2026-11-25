#!/usr/bin/env bash
set -euo pipefail

# Never Play Alone validator setup.
# Creates a virtualenv and installs the subnet package. A light validator (the
# default) only copies the main validator's weights, so that is all it needs.
# A full validator also gets Docker and npabench, installed from GitHub into
# vendor/ at the pinned ref below.
#
# Usage: ./scripts/validator_setup.sh [--full]
#   --full   set up a full (evaluating) validator and record
#            NPA_VALIDATOR_MODE=full in .env. Full setup is also selected by
#            NPA_VALIDATOR_MODE=full in the environment or in .env.

BENCH_REPO_URL="https://github.com/neverplayalone/neverplayalone_bench"
# All validators must run the same bench code or scores diverge across the
# subnet — keep this pinned to a tag or commit SHA once rounds matter.
BENCH_REF="${NPA_BENCH_REF:-main}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BENCH_DIR="${ROOT_DIR}/vendor/neverplayalone_bench"
VENV_DIR="${ROOT_DIR}/.venv"

cd "$ROOT_DIR"

require() { command -v "$1" >/dev/null 2>&1; }

# Full setup only when asked for: by flag or by NPA_VALIDATOR_MODE (environment,
# else .env). A light validator never needs Docker or npabench, so an existing
# npabench checkout is left alone and cannot block or break its updates.
FULL_FLAG=0
for arg in "$@"; do
  case "$arg" in
    --full) FULL_FLAG=1 ;;
    -h|--help) sed -n '4,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown argument: $arg" >&2; exit 1 ;;
  esac
done
MODE="${NPA_VALIDATOR_MODE:-}"
if [[ -z "$MODE" && -f "${ROOT_DIR}/.env" ]]; then
  # Read .env the way the other scripts do (by sourcing it), so all of them agree
  # on duplicate lines, `export` prefixes and quoting.
  MODE="$(set +eu; . "${ROOT_DIR}/.env" >/dev/null 2>&1; printf '%s' "${NPA_VALIDATOR_MODE:-}")"
fi
MODE="$(printf '%s' "$MODE" | tr -d "\"' \r" | tr '[:upper:]' '[:lower:]')"
FULL_SETUP=0
case "$MODE" in
  full|heavy) FULL_SETUP=1 ;;
  light|"") ;;
  *)
    echo "error: unknown NPA_VALIDATOR_MODE='${MODE}'; use 'light' or 'full'" >&2
    exit 1
    ;;
esac
if [[ "$FULL_FLAG" -eq 1 ]]; then
  FULL_SETUP=1
fi

APT_UPDATED=0
apt_update_once() {
  if [[ "$APT_UPDATED" -eq 0 ]]; then
    sudo apt-get update -y
    APT_UPDATED=1
  fi
}

ensure_git() {
  if require git; then return; fi
  echo "installing git..."
  apt_update_once
  sudo apt-get install -y git
}

ensure_uv() {
  if require uv; then return; fi
  echo "installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  require uv || { echo "error: uv installed but not on PATH; add \$HOME/.local/bin to PATH" >&2; exit 1; }
}

ensure_node() {
  if require node; then
    local major
    major="$(node -v | sed 's/v//' | cut -d. -f1)"
    if [[ "$major" -ge 20 ]]; then return; fi
    echo "node $(node -v) is < 20; upgrading to 20 LTS..."
  fi
  echo "installing Node.js 20 LTS + npm..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
  sudo apt-get install -y nodejs
}

ensure_docker() {
  if ! require docker; then
    echo "installing Docker Engine..."
    curl -fsSL https://get.docker.com | sudo sh
  fi
  sudo systemctl enable --now docker 2>/dev/null || true
  getent group docker >/dev/null || sudo groupadd docker || true
  sudo usermod -aG docker "$(id -un)" || true
}

ensure_git
ensure_uv
ensure_node
if [[ "$FULL_SETUP" -eq 1 ]]; then
  ensure_docker

  docker info >/dev/null 2>&1 || \
    echo "note: docker is not usable as this user yet — log out and back in (or run 'newgrp docker') before starting the validator" >&2
fi

if [[ ! -d "$VENV_DIR" ]]; then
  uv venv "$VENV_DIR"
fi
export VIRTUAL_ENV="$VENV_DIR"

uv pip install -e "$ROOT_DIR"

if [[ "$FULL_SETUP" -eq 1 ]]; then
  mkdir -p "${ROOT_DIR}/vendor"
  if [[ ! -d "${BENCH_DIR}/.git" ]]; then
    git clone "$BENCH_REPO_URL" "$BENCH_DIR"
  fi
  git -C "$BENCH_DIR" fetch --tags origin
  # A branch pin is checked out from its remote-tracking ref so it picks up new
  # commits; tags and commit SHAs are checked out directly. Which one applies is
  # decided by whether the remote branch exists — never by a failed checkout,
  # which used to fall back to the stale local branch from the first clone and
  # silently roll the bench back to its install-time commit.
  # --force: this checkout is managed by this script, so local edits or stray
  # files must not block the sync (all validators must run the same bench).
  if git -C "$BENCH_DIR" rev-parse --verify --quiet "refs/remotes/origin/${BENCH_REF}" >/dev/null; then
    git -C "$BENCH_DIR" checkout --force --detach --quiet "origin/${BENCH_REF}"
  elif git -C "$BENCH_DIR" rev-parse --verify --quiet "refs/heads/${BENCH_REF}" >/dev/null; then
    echo "error: bench ref '${BENCH_REF}' is not on origin; only a stale local branch of that name exists" >&2
    exit 1
  else
    git -C "$BENCH_DIR" checkout --force --detach --quiet "$BENCH_REF"
  fi

  # npabench resolves its docker/ and tools/ directories relative to the repo
  # checkout, so it must be installed editable from the clone — a plain
  # `pip install git+URL` would strip those files and break evaluation.
  uv pip install -e "$BENCH_DIR"

  # The npabench recorder is a Node tool that needs its dependencies in place.
  (cd "${BENCH_DIR}/tools/recorder" && npm install)
fi

# pm2 runs the validator as a managed background process. Prefer a global
# install; fall back to a repo-local one if the global install lacks permission.
if require pm2; then
  PM2="pm2"
elif npm install -g pm2 >/dev/null 2>&1; then
  PM2="pm2"
else
  echo "global pm2 install failed; installing pm2 into node_modules/ instead" >&2
  npm install pm2 >/dev/null 2>&1
  PM2="npx pm2"
fi

ENV_CREATED=0
if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
  ENV_CREATED=1
  echo "created .env from .env.example — edit NPA_WALLET / NPA_HOTKEY before running"
fi
# The validator itself reads its mode from .env, so a full setup of a new .env, or
# an explicit --full on an existing one, has to be recorded there: otherwise the
# evaluation stack gets installed but the validator still starts in light mode.
if [[ "$FULL_FLAG" -eq 1 || ( "$ENV_CREATED" -eq 1 && "$FULL_SETUP" -eq 1 ) ]]; then
  if grep -qE '^[[:space:]]*(export[[:space:]]+)?NPA_VALIDATOR_MODE=' "${ROOT_DIR}/.env"; then
    sed -i -E 's/^[[:space:]]*(export[[:space:]]+)?NPA_VALIDATOR_MODE=.*/NPA_VALIDATOR_MODE=full/' "${ROOT_DIR}/.env"
  else
    printf '\nNPA_VALIDATOR_MODE=full\n' >> "${ROOT_DIR}/.env"
  fi
fi

echo
if [[ "$FULL_SETUP" -eq 1 ]]; then
  echo "Full validator setup complete (npabench @ $(git -C "$BENCH_DIR" rev-parse --short HEAD))."
  echo "Edit .env (NPA_WALLET / NPA_HOTKEY / OPENROUTER_API_KEY / CHUTES_API_KEY), then start the validator:"
else
  echo "Light validator setup complete (copies the main validator's weights; no Docker or npabench)."
  echo "To evaluate miners instead, re-run with --full."
  echo "Edit .env (NPA_WALLET / NPA_HOTKEY), then start the validator:"
fi
echo "  source .venv/bin/activate"
echo "  ${PM2} start validator/main.py --name validator"
echo "If it is already running, restart it so it picks up .env:"
echo "  set -a; source .env; set +a; ${PM2} restart <name> --update-env"
