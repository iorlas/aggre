# Deployment — Dokploy Platform

## Platform
| Item | Value |
|---|---|
| Dokploy UI | https://shen.iorlas.net |
| API docs | https://docs.dokploy.com/docs/api |
| Traefik dashboard | http://traefik.ts.shen.iorlas.net/dashboard/ (Tailscale-only) |
| Public domain | *.shen.iorlas.net (HTTPS, Let's Encrypt) |
| Private domain | *.ts.shen.iorlas.net (HTTP, Tailscale-only) |

## Golden Path

### First-time setup (agent + human)
1. Agent: create Compose app in Dokploy via API or ask human to create in UI
2. Agent: add `docker-compose.prod.yml` to repo with all services + Traefik labels
3. Agent: add `.github/workflows/deploy.yml` (build → push → migrate → trigger)
4. Agent: generate list of required env vars with descriptions
5. **ASK HUMAN**: "Set these env vars in Dokploy Compose app environment: [list]"
6. **ASK HUMAN**: "Add these GitHub secrets: DOKPLOY_AUTH_TOKEN, DOKPLOY_COMPOSE_ID, DOKPLOY_URL"
7. Push to main → first deploy

### Ongoing deploys
Push to main → GHA builds → pushes to GHCR → runs migrations → triggers Dokploy → deployed

## Required Project Files
- `Dockerfile` per deployable service
- `docker-compose.prod.yml` — ALL infra: services + DBs + Redis + Traefik labels + volumes
- `.github/workflows/deploy.yml` — CI/CD pipeline
- `docker-compose.yml` — local dev only (separate, not used by Dokploy)

## Compose Structure
- Application services: image from GHCR, Traefik labels, env_file reference
- Databases: official images, named volumes, ${SECRET} interpolation
- Public services: Host(`name.shen.iorlas.net`) + certresolver=letsencrypt
- Private services: labels `tailscale=true` + `traefik.enable=true` + Host(`name.ts.shen.iorlas.net`), no TLS (WireGuard encrypts)
- All services on dokploy-network (external: true)

## ALWAYS
- Define ALL services in docker-compose.prod.yml (DBs, Redis, app services)
- Use Traefik labels for domain routing (not Dokploy UI domain config)
- Use ${VAR} interpolation for secrets (set values in Dokploy UI, not in repo)
- Run migrations in CI before triggering deploy
- Tag images with git SHA (`main-<sha>`) AND set `IMAGE_TAG` in Dokploy env via `compose.update` API before deploy. Never use `:latest` in production — it prevents rollbacks and makes container versions untraceable.
- ASK HUMAN before first deploy and when new secrets are needed
- Check deployment status via API after deploy trigger

## NEVER
- Put actual secret values in the repo (use ${VAR} + Dokploy env injection)
- Add .env files to git
- Build images on the Dokploy server
- Run migrations as container entrypoint
- Configure domains in Dokploy UI (use compose labels instead)
- Skip the human gate for secret setup

## Troubleshooting
- Deploy status: Dokploy UI → Compose → Deployments tab
- Container logs: Dokploy UI → Compose → Logs tab
- API: GET /api/deployment.all?composeId=X (check status field: done/error)
- Common: wrong image tag → check GHCR; migration fail → check CI logs; no route → check Traefik labels
- Stale code running despite successful CI → `IMAGE_TAG` in Dokploy env is stale; check the "Set IMAGE_TAG" CI step; verify with `docker inspect <container> | grep Image`

## Production Lessons (shen)

Lessons from the first production deployment. Each item reflects a real failure.

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
`pull_policy: always` (K8s: `imagePullPolicy: Always`) seems like a fix but it makes the
registry a hard dependency for every container restart, prevents rollbacks, and in Docker
Compose may not recreate the container even after pulling. Use SHA-pinned tags instead:
Docker pulls `main-<sha>` exactly once (never cached before), then caches reliably.

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
`stop_grace_period: 300s` on the worker service. Raise to 600s if transcription jobs
routinely exceed 5 minutes.

## References
- Dokploy Compose docs: https://docs.dokploy.com/docs/core/docker-compose
- Dokploy API: https://docs.dokploy.com/docs/api
- Deploy action: https://github.com/benbristow/dokploy-deploy-action
- Full platform details: R036 decisions.md §8
- Private Traefik compose: R036 traefik-ts-compose.yml
- Canonical source: ~/Documents/Knowledge/Researches/036-deployment-platform/guidelines/deployment.md
