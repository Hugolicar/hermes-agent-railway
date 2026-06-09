#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────────────────────
# Subcommand: filebrowser (called by supervisord)
# ─────────────────────────────────────────────────────────────
if [ "$1" = "filebrowser" ]; then
    mkdir -p /root/.hermes/data /var/lib/filebrowser
    cd /var/lib/filebrowser

    FB_PASSWORD="${FILEBROWSER_PASSWORD:-}"

    # Always recreate database to ensure settings are applied
    rm -f filebrowser.db
    filebrowser config init --database filebrowser.db >/dev/null 2>&1 || true

    # Apply branding settings
    filebrowser config set --database filebrowser.db --branding.name "Hermes Files" >/dev/null 2>&1 || true
    filebrowser config set --database filebrowser.db --branding.theme "dark" >/dev/null 2>&1 || true
    filebrowser config set --database filebrowser.db --branding.color "#2dd4bf" >/dev/null 2>&1 || true
    filebrowser config set --database filebrowser.db --signup false >/dev/null 2>&1 || true
    filebrowser config set --database filebrowser.db --auth.method "noauth" >/dev/null 2>&1 || true

    if [ -n "$FB_PASSWORD" ]; then
        # Auth enabled: create user admin with the provided password
        filebrowser users add admin "$FB_PASSWORD" --perm.admin --database filebrowser.db >/dev/null 2>&1 || true
        # Switch to password auth
        filebrowser config set --database filebrowser.db --auth.method "json" >/dev/null 2>&1 || true
        echo "[filebrowser] Authentication enabled (user: admin)"
        exec filebrowser -r /root/.hermes -p 8081 -a 0.0.0.0 --database filebrowser.db
    else
        # No auth: run with --noauth
        echo "[filebrowser] Running without authentication (--noauth)"
        exec filebrowser -r /root/.hermes -p 8081 -a 0.0.0.0 --database filebrowser.db --noauth
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

# Apply cookie_patch.py over the upstream cookies.py BEFORE supervisord
# brings the dashboard up. This must run AFTER any auto-update git pull
# (the upstream cookies.py may have just been refreshed) and BEFORE
# the dashboard is started. The patch reads HERMES_AUTH_COOKIE_SAMESITE
# at import time, so it's a no-op if the env var isn't set.
patch_cookies() {
  local src="/cookie_patch.py"
  local dst="/opt/hermes-agent/hermes_cli/dashboard_auth/cookies.py"
  if [ ! -f "$src" ]; then
    echo "[patch_cookies] no patch source at $src; skipping"
    return 0
  fi
  if cmp -s "$src" "$dst" 2>/dev/null; then
    echo "[patch_cookies] already patched (identical to upstream install)"
    return 0
  fi
  echo "[patch_cookies] applying cookie_patch.py -> $dst"
  cp "$src" "$dst"
}
patch_cookies

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
