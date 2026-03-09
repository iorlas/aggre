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
- Tag images with git SHA (ghcr.io/user/app:sha-abc123)
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

## References
- Dokploy Compose docs: https://docs.dokploy.com/docs/core/docker-compose
- Dokploy API: https://docs.dokploy.com/docs/api
- Deploy action: https://github.com/benbristow/dokploy-deploy-action
- Full platform details: ~/Projects/resarches/researches-cowork/researches/036-deployment-platform/decisions.md §8
- Private Traefik compose: ~/Projects/resarches/researches-cowork/researches/036-deployment-platform/traefik-ts-compose.yml
