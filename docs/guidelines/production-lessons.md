# Production Lessons

Lessons from production deployments. Each item reflects a real failure.

## Absolute paths for persistent host files

Dokploy wipes the compose `code/` directory on every redeploy — relative `./` mounts break.
Use absolute paths for files that must survive redeploy:
`/etc/dokploy/compose/<appName>/garage.toml` (not `./deploy/garage.toml`).

## Cookie domain must match serving domain

`SERVER_AUTH_COOKIE_DOMAIN` (and similar cookie domain settings) must match the actual
serving domain, not `localhost`. Mismatch causes silent auth failure — login succeeds
server-side but the browser discards the cookie.

## Never use :latest in production

`:latest` seems convenient but makes the registry a hard dependency for every container
restart, prevents rollbacks, and in Docker Compose may not recreate the container even after
pulling. Use SHA-pinned tags (`main-<sha>`): Docker pulls exactly once (never cached before),
then caches reliably.

## Dokploy env update is a full replacement

`POST /api/compose.update` with `env` replaces the entire env string — omitted vars are
wiped. When CI updates `IMAGE_TAG`, it must re-send all other env vars. Store all production
secrets as GitHub secrets so CI can reconstruct the full env payload.

## Named Docker volumes survive redeployments

Named volumes (defined in the `volumes:` top-level key) persist across Dokploy redeployments
automatically. They are **not** wiped like the compose `code/` directory. Prefer named
volumes over bind mounts for any persistent data.

## Worker graceful drain — stop_grace_period

Docker's default stop grace period (10s) is too short for in-flight tasks. Set
`stop_grace_period: 300s` on worker services. Raise to 600s if jobs routinely exceed 5 minutes.
