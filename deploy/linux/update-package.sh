#!/bin/sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CURRENT=$(tr -d '[:space:]' < "$PACKAGE_ROOT/VERSION.txt")
REPOSITORY='MoEternal/A-Cat-Girl'
API_URL="https://api.github.com/repos/$REPOSITORY/releases/latest"

command -v curl >/dev/null 2>&1 || { echo 'curl is required.' >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo 'sha256sum is required.' >&2; exit 1; }

RELEASE_JSON=$(curl -fsSL -H 'User-Agent: A-Cat-Girl-Updater' "$API_URL")
TAG=$(printf '%s' "$RELEASE_JSON" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1)
LATEST=${TAG#v}
[ -n "$LATEST" ] || { echo 'Unable to read the latest release version.' >&2; exit 1; }

NEWEST=$(printf '%s\n%s\n' "$CURRENT" "$LATEST" | sort -V | tail -n 1)
if [ "$NEWEST" = "$CURRENT" ]; then
    echo "Already up to date: v$CURRENT"
    exit 0
fi

ASSET="A-Cat-Girl-v$LATEST-linux.tar.gz"
BASE_URL="https://github.com/$REPOSITORY/releases/download/$TAG"
TEMPORARY=$(mktemp -d)
trap 'rm -rf -- "$TEMPORARY"' EXIT HUP INT TERM
curl -fL --retry 2 -o "$TEMPORARY/$ASSET" "$BASE_URL/$ASSET"
curl -fsSL -o "$TEMPORARY/SHA256SUMS.txt" "$BASE_URL/SHA256SUMS.txt"
EXPECTED=$(awk -v file="$ASSET" '$2 == file || $2 == "*" file { print toupper($1); exit }' "$TEMPORARY/SHA256SUMS.txt")
ACTUAL=$(sha256sum "$TEMPORARY/$ASSET" | awk '{ print toupper($1) }')
[ -n "$EXPECTED" ] && [ "$EXPECTED" = "$ACTUAL" ] || { echo 'SHA256 verification failed.' >&2; exit 1; }

tar -xzf "$TEMPORARY/$ASSET" -C "$TEMPORARY"
PAYLOAD="$TEMPORARY/A-Cat-Girl-v$LATEST-linux"
[ -d "$PAYLOAD" ] || { echo 'Invalid update archive layout.' >&2; exit 1; }

if command -v fuser >/dev/null 2>&1; then
    fuser -k 8732/tcp >/dev/null 2>&1 || true
elif command -v lsof >/dev/null 2>&1; then
    PIDS=$(lsof -ti tcp:8732 || true)
    [ -z "$PIDS" ] || kill $PIDS
fi

(cd "$PAYLOAD" && tar --exclude='./data' --exclude='./logs' --exclude='./backups' --exclude='./.venv' --exclude='./.env' -cf - .) |
    (cd "$PACKAGE_ROOT" && tar -xf -)
echo "Updated to v$LATEST"
nohup sh "$PACKAGE_ROOT/start.sh" > "$PACKAGE_ROOT/logs/update-start.log" 2>&1 &
