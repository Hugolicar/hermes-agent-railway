#!/usr/bin/env bash
set -e

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
