#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────────────────────
# Subcommand: filebrowser (called by supervisord)
# ─────────────────────────────────────────────────────────────
if [ "$1" = "filebrowser" ]; then
    mkdir -p /root/.hermes/data /var/lib/filebrowser
    cd /var/lib/filebrowser

    FB_PASSWORD="${FILEBROWSER_PASSWORD:-}"

    if [ -n "$FB_PASSWORD" ]; then
        # Auth enabled: create user admin with the provided password
        rm -f filebrowser.db
        filebrowser config init --database filebrowser.db > /dev/null 2>&1 || true
        filebrowser users add admin "$FB_PASSWORD" --perm.admin --database filebrowser.db > /dev/null 2>&1 || true
        echo "[filebrowser] Authentication enabled (user: admin)"
        exec filebrowser -c /filebrowser.json
    else
        # No auth: use config file with noauth
        echo "[filebrowser] Running without authentication (--noauth)"
        exec filebrowser -c /filebrowser.json
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

# Single volume setup: everything under /root/.hermes
# Create data subdir for File Browser and set as working directory
mkdir -p /root/.hermes/data
cd /root/.hermes/data

# Symlink /app/data to the actual data directory for compatibility
rm -rf /app/data
ln -sf /root/.hermes/data /app/data

# Start supervisord which manages all processes:
# - hermes dashboard (port 9119)
# - auth proxy (port 8080)
# - filebrowser (port 8081)
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
