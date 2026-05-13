#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${INSTAGRAM_REPO_URL:-https://github.com/josephtandle/instagram-agent}"
TARGET_DIR="${INSTAGRAM_TARGET_DIR:-$HOME/Tools/Instagram}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/instagram-agent.XXXXXX")"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

command -v git >/dev/null 2>&1 || { echo "Git is required."; exit 1; }
command -v node >/dev/null 2>&1 || { echo "Node.js is required."; exit 1; }

echo "Downloading Instagram agent..."
git clone --depth 1 "${REPO_URL}" "${TMP_DIR}/instagram-agent"

cd "${TMP_DIR}/instagram-agent"
node install/install-instagram.js --target "${TARGET_DIR}" "$@"
