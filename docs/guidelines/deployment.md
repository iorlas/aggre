# Deployment — Dokploy Platform

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

## Required Project Files
- `Dockerfile` per deployable service
- `docker-compose.prod.yml` — ALL infra: services + DBs + Redis + Traefik labels + volumes
- `.github/workflows/deploy.yml` — CI/CD pipeline (Model B only)
- `docker-compose.yml` — local dev only (never used by Dokploy)

## Compose Structure
- Application services: image from GHCR, Traefik labels, env_file reference
- Databases: official images, named volumes, `${SECRET}` interpolation
- Public services: `Host('name.shen.iorlas.net')` + `certresolver=letsencrypt`
- Private HTTP services: labels `tailscale=true` + `traefik.enable=true` + `Host('name.ts.shen.iorlas.net')`, no TLS
- All services on `dokploy-network` (external: true)

## ALWAYS
- Define ALL services in `docker-compose.prod.yml` (DBs, Redis, app services)
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
- Use `:latest` image tags in production

## Troubleshooting
- Deploy status: Dokploy UI → Compose → Deployments tab
- Container logs: Dokploy UI → Compose → Logs tab (or see [container-logs.md](container-logs.md))
- API: `GET /api/deployment.all?composeId=X` (check `status` field: done/error)
- Common: wrong image tag → check GHCR; migration fail → check CI logs; no route → check Traefik labels
- Stale code despite successful CI → `IMAGE_TAG` in Dokploy env is stale; check the "Set IMAGE_TAG" CI step; verify with `docker inspect <container> | grep Image`
- First-deploy 404 (transient): Traefik returns 404 until image is pulled and container starts. Expected gap: ~11s for 52MB, ~30–120s for 1GB. Not a bug — wait.

---

## Detail Guides

| Topic | File |
|---|---|
| Choose deployment model (GitHub Integration vs GHA) | [model-selection.md](model-selection.md) |
| Deploy from scratch (step-by-step) | [deploy-from-scratch.md](deploy-from-scratch.md) |
| Non-HTTP service deployment (TCP, databases) | [non-http.md](non-http.md) |
| GitHub Actions build caching | [build-caching.md](build-caching.md) |
| Config files in Docker images | [config-files.md](config-files.md) |
| Container logs (all methods) | [container-logs.md](container-logs.md) |
| Production lessons (real failures) | [production-lessons.md](production-lessons.md) |

## Reference Files

| File | Purpose |
|---|---|
| [traefik-ts-compose.yml](traefik-ts-compose.yml) | Private Traefik + Tailscale compose (deploy once per platform) |
| [reference-pipeline.yml](reference-pipeline.yml) | Model B GitHub Actions template (adapt per project) |
