# Config Files in Docker Images

Bake config files into the Docker image. Do not use host bind mounts or SSH-placed files —
they create host state that agents can't manage via API.

## Standard approach: COPY config last

Place config `COPY` after all dependency installation so dependency cache layers are not
invalidated by config changes:

```dockerfile
FROM python:3.12-slim
RUN apt-get install -y ...
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/ src/
# Config last — only layers below here rebuild on config change
COPY config/nginx.conf /etc/nginx/nginx.conf
COPY config/otel.yml /app/otel.yml
```

A config-only change rebuilds only from the last COPY — all dependency layers stay cached.

## Worst case: config value must change without image rebuild

Use **env var templating**: ship a config template in the image, generate the final config
at container startup from environment variables. No rebuild, no SSH.

```sh
# entrypoint.sh
#!/bin/sh
envsubst < /app/config.template.yml > /app/config.yml
exec "$@"
```

```dockerfile
COPY config/config.template.yml /app/config.template.yml
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["myapp"]
```

To change a config value: update env var via API, then redeploy — no image rebuild needed.

```sh
DOKPLOY_TOKEN=$(cat ~/.config/dokploy/token)

# Update env (full replacement — include ALL vars)
curl -X POST https://shen.iorlas.net/api/compose.update \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"composeId": "...", "env": "KEY=new_value\nOTHER_KEY=..."}'

# Trigger redeploy
curl -X POST https://shen.iorlas.net/api/compose.redeploy \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"composeId": "..."}'
```

> **Note:** `compose.update` with `env` replaces the entire env string. Omitted vars are
> wiped. Always re-send all vars when updating.

## Decision table

| Situation | Approach |
|---|---|
| Config known at build time | Bake into image, COPY last |
| Config value changes frequently | Env var + envsubst template in image |
| Config contains secrets | Always env var — never bake into image |
