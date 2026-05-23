#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────────────────────
# Subcommand: filebrowser (called by supervisord)
# ─────────────────────────────────────────────────────────────
if [ "$1" = "filebrowser" ]; then
    mkdir -p /app/data /var/lib/filebrowser
    cd /var/lib/filebrowser

    FB_PASSWORD="${FILEBROWSER_PASSWORD:-}"

    if [ -n "$FB_PASSWORD" ]; then
        # Auth enabled: create user admin with the provided password
        rm -f filebrowser.db
        filebrowser config init --database filebrowser.db >/dev/null 2>&1 || true
        filebrowser users add admin "$FB_PASSWORD" --perm.admin --database filebrowser.db >/dev/null 2>&1 || true
        echo "[filebrowser] Authentication enabled (user: admin)"
        exec filebrowser -r /app/data -p 8080 -a 0.0.0.0 --database filebrowser.db
    else
        # No auth: run with --noauth
        echo "[filebrowser] Running without authentication (--noauth)"
        exec filebrowser -r /app/data -p 8080 -a 0.0.0.0 --noauth
    fi
fi

# ─────────────────────────────────────────────────────────────
# Main entrypoint (default)
# ─────────────────────────────────────────────────────────────
AUTO_UPDATE="${AUTO_UPDATE:-true}"

if [ "$AUTO_UPDATE" = "true" ]; then
  echo "Checking for Hermes updates..."
  cd /opt/hermes-agent
  if git pull --recurse-submodules 2>&1 | grep -v 'Already up to date'; then
    echo "Updating dependencies..."
    VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install -e ".[all]" --quiet
    echo "Update complete."
  else
    echo "Already up to date."
  fi
fi

# Ensure data directory exists (for File Browser + volume mount)
mkdir -p /app/data

# Start supervisord which manages all processes:
# - hermes dashboard (port 9119)
# - auth proxy (port from $PORT env)
# - filebrowser (port 8080)
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
