FROM python:3.11-slim

# Install system dependencies + supervisord + nodejs
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep ffmpeg supervisor \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

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
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY filebrowser.json /filebrowser.json
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create File Browser database directory
RUN mkdir -p /app/data /var/lib/filebrowser

ENTRYPOINT ["/entrypoint.sh"]
