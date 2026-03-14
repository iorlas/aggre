# Deployment Model Selection

Two distinct deployment models. Choose once per project at setup time.

## Model A — Dokploy GitHub Integration (build on server)

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

---

## Model B — GitHub Actions + GHCR (recommended for production)

CI builds the image, pushes to GHCR, runs migrations, then triggers Dokploy via API.

**Use when:**
- Production workload
- Database migrations must run before new code goes live
- You need SHA-pinned images for rollback
- Multi-service compose with coordinated deploys
- Build reproducibility and audit trail matter

**Pipeline:** push to main → GHA builds → push to GHCR (`ghcr.io/user/app:main-<sha>`) → run migrations → `POST /api/compose.update` (set IMAGE_TAG) → deploy action triggers deploy

**Required secrets (GitHub):** `DOKPLOY_AUTH_TOKEN` (org-level), `DOKPLOY_COMPOSE_ID`, `DOKPLOY_URL` + all app secrets that CI needs to reconstruct the full Dokploy env payload

**Required secrets (Dokploy env):** All `${VAR}` values including `IMAGE_TAG`

**Reference:** see [reference-pipeline.yml](reference-pipeline.yml) for a template workflow

---

## Decision Table

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

## DOKPLOY_AUTH_TOKEN: org-level GitHub secret

The Dokploy API token does not expire. Set it once as a GitHub org-level secret
(GitHub org → Settings → Secrets → Actions → New org secret) so every new repo inherits
it automatically — no per-repo setup needed.

Token source: https://shen.iorlas.net → top-right menu → API Tokens
