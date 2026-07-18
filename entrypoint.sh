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

# ─────────────────────────────────────────────────────────────
# Tailscale (opt-in via TS_AUTHKEY env var)
# ─────────────────────────────────────────────────────────────
# If TS_AUTHKEY is set, start tailscaled in userspace mode and bring
# the node up. This is the recommended way to expose the dashboard
# to a Hermes Desktop client without going through the public HTTPS
# proxy (which means: no OAuth, no SameSite, no cross-site cookies,
# just Basic Auth on the dashboard's local bind).
#
# Requirements:
#   - TS_AUTHKEY: a Tailscale auth key from
#     https://login.tailscale.com/admin/settings/keys. Mark it
#     "reusable" and set an expiry (default 90d, max 180d). The
#     key gives anyone who has it the ability to register a node
#     in your tailnet, so keep it in Railway env vars, not in
#     committed files.
#   - TS_HOSTNAME: optional, defaults to "hugoloc-railway". The
#     hostname that shows up in `tailscale status`.
#
# Idempotency:
#   - `tailscaled` is a long-running daemon; supervisord-style
#     behaviour means the container restart re-runs this and the
#     daemon gets a new PID. That's fine — Tailscale handles
#     re-registration automatically.
#   - `tailscale up` is a client command. If the node is already
#     registered it no-ops. We pass `--accept-routes=false` so
#     the container doesn't accidentally start routing other
#     traffic through the tailnet.
start_tailscale() {
  if [ -z "$TS_AUTHKEY" ]; then
    echo "[tailscale] TS_AUTHKEY not set, skipping (dashboard will only be reachable via the public auth_proxy on hugoloc.click)"
    return 0
  fi
  TS_HOSTNAME="${TS_HOSTNAME:-hugoloc-railway}"
  if ! command -v tailscaled >/dev/null 2>&1; then
    echo "[tailscale] FATAL: tailscaled binary not found in image. Check the Dockerfile Tailscale install step." >&2
    return 1
  fi
  echo "[tailscale] starting tailscaled (userspace mode)..."
  # --tun=userspace-networking avoids needing /dev/net/tun or
  # privileged mode (which Railway doesn't grant by default).
  # --state and --socket are kept under /var/lib/tailscale so
  # they survive supervisord restarts within the same container
  # (note: NOT across container rebuilds — state is regenerated
  # on each deploy, which is fine for a node, not for an exit
  # node).
  mkdir -p /var/lib/tailscale
  tailscaled --tun=userspace-networking \
             --state=/var/lib/tailscale/tailscaled.state \
             --socket=/var/run/tailscale/tailscaled.sock \
             --port=41641 \
             >> /var/log/tailscaled.log 2>&1 &
  TAILSCALED_PID=$!
  echo "[tailscale] tailscaled PID: $TAILSCALED_PID"
  # Wait for the socket to be ready before `tailscale up`.
  for i in $(seq 1 30); do
    if [ -S /var/run/tailscale/tailscaled.sock ]; then
      break
    fi
    sleep 0.5
  done
  if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
    echo "[tailscale] FATAL: tailscaled did not create its socket within 15s" >&2
    return 1
  fi
  echo "[tailscale] bringing node '$TS_HOSTNAME' up..."
  # --accept-routes=false: don't accept subnet routes from the
  # tailnet. We're a leaf node, not a router.
  # --ssh=false: don't expose SSH over Tailscale on this node.
  # --operator=$USER: Tailscale ACLs default-deny; this lets the
  # container's own root user invoke `tailscale` CLI without
  # needing a separate `tailscale set` for the operator.
  tailscale up --authkey="$TS_AUTHKEY" \
               --hostname="$TS_HOSTNAME" \
               --accept-routes=false \
               --ssh=false \
               --operator="$USER" \
               2>&1 | tee -a /var/log/tailscaled.log
  if tailscale status --json >/dev/null 2>&1; then
    local ip
    ip=$(tailscale ip -4 2>/dev/null || echo "(unknown)")
    echo "[tailscale] online. Tailnet IPv4: $ip"
    echo "[tailscale] Desktop can now connect to: ws://$ip:9119/api/ws"
  else
    echo "[tailscale] WARNING: tailscale up returned but status check failed; check /var/log/tailscaled.log" >&2
  fi
}
start_tailscale

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
#
# IMPORTANT: do NOT use `exec` here. With `exec`, the supervisord binary
# replaces the shell (PID 2) and becomes PID 1 itself, which defeats
# tini (PID 1, registered via ENTRYPOINT in the Dockerfile) — tini loses
# its position as the init/signal-handler/reaper. Running supervisord
# WITHOUT `exec` keeps it as a child of the shell, which is itself a
# child of tini — tini stays PID 1, forwards SIGTERM on shutdown, and
# reaps orphan subprocesses from the hermes agent loop.

# Start supervisord (NOT via `exec`) so tini stays PID 1.
/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
