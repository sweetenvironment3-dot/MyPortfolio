#!/bin/sh
# Starts as root only to fix the ownership of the data volume (hosts often
# mount a fresh volume owned by root), then drops to the unprivileged user.
set -e

DATA_DIR="$(dirname "${DATABASE_PATH:-/data/portfolio.db}")"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R appuser:appuser "$DATA_DIR" 2>/dev/null || true
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

exec "$@"
