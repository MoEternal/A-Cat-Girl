#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return
    fi
    if [[ -x "$HOME/.local/bin/uv" ]]; then
        printf '%s\n' "$HOME/.local/bin/uv"
        return
    fi
    return 1
}

if ! UV="$(find_uv)"; then
    if ! command -v curl >/dev/null 2>&1; then
        echo "curl is required for the first launch." >&2
        exit 1
    fi
    echo "[1/4] Installing uv from the official Astral installer..."
    INSTALLER="$(mktemp)"
    trap 'rm -f "$INSTALLER"' EXIT
    curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$INSTALLER"
    sh "$INSTALLER"
    UV="$(find_uv)"
else
    echo "[1/4] uv is available."
fi

echo "[2/4] Installing Python 3.12..."
"$UV" python install 3.12

echo "[3/4] Synchronizing locked runtime dependencies..."
UV_LINK_MODE=copy "$UV" sync --frozen --no-dev --python 3.12

if [[ ! -f .env ]]; then
    cat > .env <<'EOF'
CATGIRL_HOST=127.0.0.1
CATGIRL_PORT=8732
CATGIRL_DATA_DIR=./data
CATGIRL_LOG_LEVEL=INFO
CATGIRL_MODEL_TIMEOUT_SECONDS=120
CATGIRL_MEDIA_DOWNLOAD_TIMEOUT_SECONDS=30
EOF
    echo "[4/4] Created .env."
else
    echo "[4/4] Existing .env was preserved."
fi

mkdir -p data logs backups
echo "A Cat Girl is starting at http://127.0.0.1:8732/"
echo "Create the administrator account on first access. Press Ctrl+C to stop."
exec .venv/bin/python -m catgirl
