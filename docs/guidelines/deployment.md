# Deployment — Dokploy Platform

> Canonical guideline. Copy to `docs/guidelines/deployment.md` in each project.
> Source of truth: `~/Documents/Knowledge/Researches/036-deployment-platform/guidelines/deployment.md`
> Keep this file updated when new production lessons are learned.

## Platform
| Item | Value |
|---|---|
| Dokploy UI | https://shen.iorlas.net |
| API docs | https://docs.dokploy.com/docs/api |
| Traefik dashboard | http://traefik.ts.shen.iorlas.net/dashboard/ (Tailscale-only) |
| Public domain | *.shen.iorlas.net (HTTPS, Let's Encrypt) |
| Private domain | *.ts.shen.iorlas.net (HTTP, Tailscale-only) |

## Local Dokploy Token (Agent Auth)

Coding agents (Claude Code, etc.) need the Dokploy API token to create projects,
trigger deploys, and fetch logs. Store it locally once so agents can read it
without requiring the user to paste it each session.

### Setup (one-time, human)
```sh
mkdir -p ~/.config/dokploy
echo "YOUR_TOKEN_HERE" > ~/.config/dokploy/token
chmod 600 ~/.config/dokploy/token
```

Token source: https://shen.iorlas.net → top-right menu → API Tokens

### Agent usage
```sh
DOKPLOY_TOKEN=$(cat ~/.config/dokploy/token)
curl -H "x-api-key: $DOKPLOY_TOKEN" https://shen.iorlas.net/api/...
```

Agents must always read from `~/.config/dokploy/token` before asking the user for the token.

---

## GitHub Integration vs GitHub Actions

Two distinct deployment models. Choose once per project at setup time.

### Model A — Dokploy GitHub Integration (build on server)
Dokploy pulls source from GitHub and builds the image on the shen server using Nixpacks or your Dockerfile.

**Use when:**
- Simple single-service app with no complex build steps
- No database migrations needed before deploy
- Prototyping / internal tools where build reproducibility is not critical
- No need for SHA-pinned images or rollback by image tag

**How it works:**
Dokploy → Compose/Application → Source → GitHub → select repo + branch → Deploy

**Limitations:**
- Builds consume shen server CPU/RAM during deploy
- No pre-deploy migration step (migrations must run as entrypoint or init container)
- Image not pushed to a registry — no SHA pinning, no rollback by tag
- No artifact attestation / provenance

### Model B — GitHub Actions + GHCR (recommended for production)
CI builds the image, pushes to GHCR, runs migrations, then triggers Dokploy via API.

**Use when:**
- Production workload
- Database migrations must run before new code goes live
- You need SHA-pinned images for rollback
- Multi-service compose with coordinated deploys
- Build reproducibility and audit trail matter

**Pipeline:** push to main → GHA builds → push to GHCR (`ghcr.io/user/app:main-<sha>`) → run migrations → `POST /api/compose.update` (set IMAGE_TAG) → dokploy-deploy-action triggers deploy

**Required secrets (GitHub):** `DOKPLOY_AUTH_TOKEN` (org-level), `DOKPLOY_COMPOSE_ID`, `DOKPLOY_URL` + all app secrets
**Required secrets (Dokploy env):** All `${VAR}` values including `IMAGE_TAG`

**Reference pipeline:** `aggre/.github/workflows/docker-publish.yml`

### Decision table

| Factor | Model A (GitHub Integration) | Model B (GitHub Actions + GHCR) |
|---|---|---|
| Build location | shen server | GitHub Actions runner |
| Registry | none | GHCR |
| Image pinning | no | yes (SHA tag) |
| Rollback | redeploy previous commit | redeploy previous image tag |
| Pre-deploy migrations | not supported cleanly | yes (CI step) |
| Setup complexity | low | medium |
| Recommended for | prototypes, internal tools | production |

---

## Deploy from Scratch

> Use this when onboarding a new project onto the shen Dokploy platform.

### Step 1 — Choose deployment method
See **GitHub Integration vs GitHub Actions** above to decide Model A or B before proceeding.

### Step 2 — Prepare project files
- `Dockerfile` per deployable service (or Nixpacks if no Dockerfile — Model A only)
- `docker-compose.prod.yml` — ALL services: app + DBs + Redis + workers + Traefik labels
- `docker-compose.yml` — local dev only; never used by Dokploy
- `.github/workflows/deploy.yml` — only if using Model B

### Step 3 — Create Compose app in Dokploy
Via UI: Dokploy → New Project → New Service → Compose

Via API:
```sh
curl -X POST https://shen.iorlas.net/api/project.create \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-project"}'

curl -X POST https://shen.iorlas.net/api/compose.create \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-app", "projectId": "<id>", "appName": "my-app"}'
```

Note the returned `composeId` — needed for all subsequent API calls.

### Step 4 — Configure secrets

In Dokploy UI → Compose → Environment: paste full env block with `${VAR}` placeholders.

In GitHub repo → Settings → Secrets: add `DOKPLOY_COMPOSE_ID`, `DOKPLOY_URL`, plus all
app secrets CI needs to reconstruct the Dokploy env payload.

**`DOKPLOY_AUTH_TOKEN`: set once at GitHub org level — never per-repo.**
The token does not expire. Set it as a GitHub org secret (org → Settings → Secrets →
Actions → New org secret) so every new repo inherits it automatically.

**ASK HUMAN** to perform this step — never commit actual secret values.

### Step 5 — Upload compose file
Via UI: Dokploy → Compose → paste `docker-compose.prod.yml` content.

Via API:
```sh
curl -X POST https://shen.iorlas.net/api/compose.update \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"composeId\": \"<id>\", \"dockerCompose\": \"$(cat docker-compose.prod.yml | jq -Rs .)\"}"
```

### Step 6 — First deploy
Push to main (Model B) or click Deploy in Dokploy UI (Model A).

**First-deploy 404 is expected** — Traefik returns 404 until image pull + container start completes.
Gap: ~11s for 50MB image, ~30–120s for 1GB image. Wait before troubleshooting.

### Step 7 — Verify
- Dokploy UI → Compose → Deployments tab → status: `done`
- `curl https://name.shen.iorlas.net` (public) or `curl http://name.ts.shen.iorlas.net` (private, via Tailscale)
- API: `GET /api/deployment.all?composeId=X` → check `status` field

---

## Non-HTTP Service Deployment

For services that don't speak HTTP (raw TCP, databases, message queues, etc.)
Traefik is not involved. Use direct port binding instead.

### Private (Tailscale-only)
Bind the container port to shen's Tailscale IP. Only Tailscale peers can reach it.

```yaml
# docker-compose.prod.yml
services:
  postgres:
    ports:
      - "100.65.108.29:5432:5432"   # Tailscale-only
```

- Tailscale IP: `100.65.108.29` (verify: `ssh shen "tailscale ip -4"`)
- Do **NOT** use `network_mode: service:tailscale` for this — that's for HTTP via traefik-ts
- Do **NOT** bind to `0.0.0.0` — Docker bypasses UFW; `0.0.0.0` is publicly reachable
- No Traefik labels needed

### Public (internet-accessible)
Bind to `0.0.0.0` with an explicit host port. Docker bypasses UFW — any port bound here is public.

```yaml
ports:
  - "0.0.0.0:9000:9000"   # intentionally public
```

**Port conflict avoidance — ports in use on shen:**
| Port | Service |
|---|---|
| 80 | Traefik (HTTP → HTTPS redirect) |
| 443 | Traefik (HTTPS, public services) |
| 2201 | SSH |
| 3000 | Dokploy UI (firewall-blocked externally) |

Before exposing a new port: `ssh shen "ss -tlnp | grep <PORT>"`

### Access pattern summary
| Access | Mechanism | Bind address |
|---|---|---|
| Private TCP (Tailscale) | Direct port binding | `100.65.108.29:PORT` |
| Public TCP (internet) | Direct port binding | `0.0.0.0:PORT` |
| Private HTTP | traefik-ts labels | (no port binding) |
| Public HTTP/HTTPS | Traefik public labels | (no port binding) |

---

## GitHub Actions Build Caching

Layer caching dramatically reduces build times for unchanged layers. Use GitHub
Actions cache backend (free, no external registry needed).

### Recommended workflow config
```yaml
- name: Build and push
  uses: docker/build-push-action@v6
  with:
    context: .
    push: true
    tags: ${{ steps.meta.outputs.tags }}
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

`mode=max` caches all intermediate layers, not just the final image.

### Dockerfile layer ordering
Put slow, rarely-changing layers first; put fast/frequent-changing layers last:

```dockerfile
# 1. Base image (rarely changes)
FROM python:3.12-slim

# 2. System dependencies (rarely changes)
RUN apt-get install -y curl build-essential

# 3. Dependency manifest only — cache bust only on dep changes, not code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# 4. Application code (changes every commit)
COPY src/ src/

# 5. Config files LAST — only invalidates layers below this line
COPY config/ config/
```

**Common mistake:** `COPY . .` before `pip install` busts the dependency cache on every code change.

### Cache limits
GitHub Actions cache: 10GB per repo, evicted by LRU. Multi-platform builds
(`linux/amd64` + `linux/arm64`) use separate cache keys — each platform gets its own slice.

---

## Config Files in Docker Images

Bake config files into the Docker image. Do not use host bind mounts or SSH-placed files —
they create host state that agents can't manage via API.

### Standard approach: COPY config last

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

### Worst case: config value must change without image rebuild

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
# Update env + redeploy via Dokploy API
curl -X POST https://shen.iorlas.net/api/compose.update \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -d '{"composeId": "...", "env": "KEY=new_value\nOTHER_KEY=..."}'

curl -X POST https://shen.iorlas.net/api/compose.redeploy \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -d '{"composeId": "..."}'
```

### Decision table
| Situation | Approach |
|---|---|
| Config known at build time | Bake into image, COPY last |
| Config value changes frequently | Env var + envsubst template in image |
| Config contains secrets | Always env var — never bake into image |

---

## Required Project Files
- `Dockerfile` per deployable service
- `docker-compose.prod.yml` — ALL infra: services + DBs + Redis + Traefik labels + volumes
- `.github/workflows/deploy.yml` — CI/CD pipeline (Model B)
- `docker-compose.yml` — local dev only (separate, not used by Dokploy)

## Compose Structure
- Application services: image from GHCR, Traefik labels, env_file reference
- Databases: official images, named volumes, ${SECRET} interpolation
- Public services: `Host('name.shen.iorlas.net')` + `certresolver=letsencrypt`
- Private HTTP services: labels `tailscale=true` + `traefik.enable=true` + `Host('name.ts.shen.iorlas.net')`, no TLS
- All services on `dokploy-network` (external: true)

## ALWAYS
- Define ALL services in docker-compose.prod.yml (DBs, Redis, app services)
- Use Traefik labels for domain routing (not Dokploy UI domain config)
- Use `${VAR}` interpolation for secrets (set values in Dokploy UI, not in repo)
- Run migrations in CI before triggering deploy
- Tag images with git SHA (`main-<sha>`) AND set `IMAGE_TAG` in Dokploy env via `compose.update` API before deploy. Never use `:latest` in production — it prevents rollbacks and makes container versions untraceable.
- Use FQDNs for Tailscale hosts in Docker/compose configs (`hostname.network.ts.net`). Short names don't resolve reliably inside containers — musl libc and Docker DNS search domains don't expand them.
- ASK HUMAN before first deploy and when new secrets are needed
- Check deployment status via API after deploy trigger
- Read Dokploy token from `~/.config/dokploy/token` (never ask user to paste it)

## NEVER
- Put actual secret values in the repo (use `${VAR}` + Dokploy env injection)
- Add .env files to git
- Build images on the Dokploy server
- Run migrations as container entrypoint
- Configure domains in Dokploy UI (use compose labels instead)
- Skip the human gate for secret setup
- Use host bind mounts or SSH-placed config files (bake into image instead)

## Troubleshooting
- Deploy status: Dokploy UI → Compose → Deployments tab
- Container logs: Dokploy UI → Compose → Logs tab
- API: `GET /api/deployment.all?composeId=X` (check `status` field: done/error)
- Common: wrong image tag → check GHCR; migration fail → check CI logs; no route → check Traefik labels
- Stale code running despite successful CI → `IMAGE_TAG` in Dokploy env is stale; check the "Set IMAGE_TAG" CI step; verify with `docker inspect <container> | grep Image`
- First-deploy 404 (transient): Traefik returns 404 until image is pulled and container starts. Expected gap: ~11s for 52MB, ~30–120s for 1GB. Not a bug — just wait.

## Container Logs

### Via Dokploy UI (easiest)
Dokploy → Project → Compose → Logs tab
- stdout/stderr for all containers in the compose app
- Filter by container name via dropdown
- Real-time streaming

### Via SSH + Docker CLI (most powerful)
```sh
ssh iorlas@shen.iorlas.net -p 2201

# List running containers
docker ps

# Tail logs for a specific container
docker logs <container-name> -f --tail 100

# All containers in a compose app (filter by label)
docker ps --filter "label=com.docker.compose.project=<appName>" -q \
  | xargs -I{} docker logs {} --tail 50

# With timestamps
docker logs <container-name> --timestamps --tail 200
```

### Via Dokploy API
```
GET /api/deployment.all?composeId=<id>
```
Returns deployment history with `status` (done/error) and log URL per deployment.

### Notes
- Container names follow pattern: `<appName>-<service>-<N>`
- Dokploy UI log buffer is limited; use SSH docker logs for large volumes
- Persistent log storage (external sink) not yet configured on shen

---

## Production Lessons

Lessons from production deployments. Each item reflects a real failure.

### Tailscale DNS
`*.ts.shen.iorlas.net` is a Cloudflare DNS-only A record pointing to shen's Tailscale IP.
If the IP changes (machine re-registers), update the record in Cloudflare.
Verify current IP: `ssh shen "tailscale ip -4"`.

### Absolute paths for persistent host files
Dokploy wipes the compose `code/` directory on every redeploy — relative `./` mounts break.
Use absolute paths for files that must survive redeploy:
`/etc/dokploy/compose/<appName>/garage.toml` (not `./deploy/garage.toml`).

### Hatchet cookie domain
`SERVER_AUTH_COOKIE_DOMAIN` must match the actual serving domain (`hatchet.ts.shen.iorlas.net`),
not `localhost`. Mismatch causes silent auth failure — login succeeds server-side but browser
discards the cookie.

### Tailscale FQDNs inside containers (musl DNS)
Short Tailscale hostnames fail inside Alpine/musl containers — `getaddrinfo()` doesn't expand
search domains. Always use FQDNs: `<host>.shrimp-boa.ts.net`. Applies to nginx async resolver
too — even with `resolver 127.0.0.11`, bare hostnames like `shen` won't resolve.

### Never use :latest in production
`pull_policy: always` seems like a fix but makes the registry a hard dependency for every
container restart, prevents rollbacks, and in Docker Compose may not recreate the container
even after pulling. Use SHA-pinned tags: Docker pulls `main-<sha>` exactly once (never cached
before), then caches reliably.

### Dokploy env update is a full replacement
`POST /api/compose.update` with `env` replaces the entire env string — omitted vars are
wiped. When CI updates `IMAGE_TAG`, it must re-send all other env vars. Store all production
secrets as GitHub secrets so CI can reconstruct the full env payload.

### Hatchet token and config volume
`HATCHET_CLIENT_TOKEN` must be generated before the first deploy:
`docker exec <hatchet-lite> /hatchet-admin token create --name prod-worker --config /config`
The `hatchet-config` volume is a named Docker volume (not a bind mount) and persists across
redeployments automatically.

### Hatchet graceful drain — stop_grace_period
Docker's default stop grace period (10s) is too short for in-flight tasks. Set
`stop_grace_period: 300s` on the worker service. Raise to 600s if jobs routinely exceed 5 minutes.

## References
- Dokploy Compose docs: https://docs.dokploy.com/docs/core/docker-compose
- Dokploy API: https://docs.dokploy.com/docs/api
- Deploy action: https://github.com/benbristow/dokploy-deploy-action
- Full platform details: R036 decisions.md §8
- Private Traefik compose: R036 traefik-ts-compose.yml
