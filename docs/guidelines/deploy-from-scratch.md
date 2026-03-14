# Deploy from Scratch

> Use this when onboarding a new project onto the shen Dokploy platform.

## Step 1 — Choose deployment method

See [model-selection.md](model-selection.md) to decide Model A or B before proceeding.

## Step 2 — Prepare project files

- `Dockerfile` per deployable service (or Nixpacks if no Dockerfile — Model A only)
- `docker-compose.prod.yml` — ALL services: app + DBs + Redis + workers + Traefik labels
- `docker-compose.yml` — local dev only; never used by Dokploy
- `.github/workflows/deploy.yml` — only if using Model B (see [reference-pipeline.yml](reference-pipeline.yml))

## Step 3 — Create Compose app in Dokploy

Via UI: Dokploy → New Project → New Service → Compose

Via API:
```sh
DOKPLOY_TOKEN=$(cat ~/.config/dokploy/token)

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

## Step 4 — Configure secrets

In Dokploy UI → Compose → Environment: paste full env block with `${VAR}` placeholders.

In GitHub repo → Settings → Secrets: add `DOKPLOY_COMPOSE_ID`, `DOKPLOY_URL`, plus all
app secrets CI needs to reconstruct the Dokploy env payload.

**`DOKPLOY_AUTH_TOKEN`: set once at GitHub org level — never per-repo.**
The token does not expire. Set it as a GitHub org secret (org → Settings → Secrets →
Actions → New org secret) so every new repo inherits it automatically.

**ASK HUMAN** to perform this step — never commit actual secret values.

## Step 5 — Upload compose file

Via UI: Dokploy → Compose → paste `docker-compose.prod.yml` content.

Via API:
```sh
curl -X POST https://shen.iorlas.net/api/compose.update \
  -H "x-api-key: $DOKPLOY_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"composeId\": \"<id>\", \"dockerCompose\": $(cat docker-compose.prod.yml | jq -Rs .)}"
```

## Step 6 — First deploy

Push to main (Model B) or click Deploy in Dokploy UI (Model A).

**First-deploy 404 is expected** — Traefik returns 404 until image pull + container start completes.
Gap: ~11s for 50MB image, ~30–120s for 1GB image. Wait before troubleshooting.

## Step 7 — Verify

- Dokploy UI → Compose → Deployments tab → status: `done`
- `curl https://name.shen.iorlas.net` (public) or `curl http://name.ts.shen.iorlas.net` (private, via Tailscale)
- API: `GET /api/deployment.all?composeId=X` → check `status` field
