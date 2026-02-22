FROM node:20-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV IS_SANDBOX=1

# System deps: jq (stop hook JSON), perl (promise extraction), git, docker CLI
RUN apt-get update && apt-get install -y \
    jq perl git curl ca-certificates gnupg sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI (for docker compose access via mounted socket)
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

# uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
RUN uv python install 3.12

# Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Autonomy scripts
COPY stop-hook.sh /opt/autonomy/stop-hook.sh
COPY entrypoint.sh /opt/autonomy/entrypoint.sh
COPY format-stream.py /opt/autonomy/format-stream.py
RUN chmod +x /opt/autonomy/stop-hook.sh /opt/autonomy/entrypoint.sh

WORKDIR /work
ENTRYPOINT ["/opt/autonomy/entrypoint.sh"]
