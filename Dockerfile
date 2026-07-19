FROM python:3.11-slim

# Install system dependencies + supervisord + nodejs + tini
#
# `tini` is registered as PID 1 and forwards signals to children + reaps
# zombies. supervisord (PID 2) still orchestrates the long-lived services
# (hermes-dashboard, auth-proxy, filebrowser), but tini sits in front and
# fixes the memory leak where subprocesses spawned by the hermes agent loop
# were reparented to supervisord without a SIGCHLD handler, accumulating as
# "reaped unknown pid" rows in supervisord logs and holding RAM until the
# container restarted. Reference: https://github.com/krallin/tini
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep ffmpeg supervisor tini \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Install Tailscale (userspace networking mode - no /dev/net/tun needed,
# no privileged container required). Official install script from
# tailscale.com. We pin to a recent stable version; bump as needed.
ARG TAILSCALE_VERSION=1.78.1
RUN curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" -o /tmp/tailscale.tgz \
    && tar -xzf /tmp/tailscale.tgz -C /tmp \
    && mv /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled /usr/local/bin/tailscaled \
    && mv /tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale /usr/local/bin/tailscale \
    && rm -rf /tmp/tailscale.tgz /tmp/tailscale_${TAILSCALE_VERSION}_amd64 \
    && chmod +x /usr/local/bin/tailscale /usr/local/bin/tailscaled \
    && tailscale version

# Install File Browser (official binary)
RUN curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash \
    && filebrowser version

# Clone Hermes Agent
RUN git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent

WORKDIR /opt/hermes-agent
RUN uv venv venv --python 3.11 \
    && VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install -e ".[all]"

ENV PATH="/opt/hermes-agent/venv/bin:$PATH"

# Create Hermes directories
RUN mkdir -p /root/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache} \
    && cp cli-config.yaml.example /root/.hermes/config.yaml \
    && touch /root/.hermes/.env

# Copy app files
COPY auth_proxy.py /auth_proxy.py
COPY profile_gateway_control.py /profile_gateway_control.py
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY filebrowser.json /filebrowser.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create File Browser database directory
RUN mkdir -p /app/data /var/lib/filebrowser

# Tini as PID 1: forwards signals (SIGTERM/SIGINT) to supervisord, reaps
# zombie processes spawned by the hermes agent loop. Without this the
# container PID 1 is supervisord, which doesn't always wait() on children
# reparented to it, leading to leaked subprocesses + memory growth.
ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
