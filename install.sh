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

# Verify Python 3.10+
PYTHON_OK=false
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    if "$PY" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
      PYTHON_OK=true
      break
    fi
  fi
done
if [ "$PYTHON_OK" = "false" ]; then
  echo "Python 3.10 or higher is required."
  echo "Install it via pyenv, Homebrew (brew install python@3.11), or python.org."
  exit 1
fi

echo "Downloading Instagram agent..."
git clone --depth 1 "${REPO_URL}" "${TMP_DIR}/instagram-agent"

cd "${TMP_DIR}/instagram-agent"
node install/install-instagram.js --target "${TARGET_DIR}" "$@"
