# Production Lessons

Lessons from production deployments. Each item reflects a real failure.

## Tailscale DNS

`*.ts.shen.iorlas.net` is a Cloudflare DNS-only A record pointing to shen's Tailscale IP.
If the IP changes (machine re-registers), update the record in Cloudflare.
Verify current IP: `ssh shen "tailscale ip -4"`.

## Avoid Tailscale Magic DNS

Do not rely on Tailscale Magic DNS for service addressing. Use the public DNS prefix
(`*.ts.shen.iorlas.net`) instead — it resolves via Cloudflare to shen's Tailscale IP and
works consistently from any context (containers, CI, local machine). Magic DNS requires a
Tailscale-registered client and is unpredictable inside Docker containers.

## Absolute paths for persistent host files

Dokploy wipes the compose `code/` directory on every redeploy — relative `./` mounts break.
Use absolute paths for files that must survive redeploy:
`/etc/dokploy/compose/<appName>/garage.toml` (not `./deploy/garage.toml`).

## Cookie domain must match serving domain

`SERVER_AUTH_COOKIE_DOMAIN` (and similar cookie domain settings) must match the actual
serving domain, not `localhost`. Mismatch causes silent auth failure — login succeeds
server-side but the browser discards the cookie.

## Tailscale FQDNs inside containers (musl DNS)

Short Tailscale hostnames fail inside Alpine/musl containers — `getaddrinfo()` doesn't expand
search domains. Always use FQDNs: `<host>.shrimp-boa.ts.net`. Applies to nginx async resolver
too — even with `resolver 127.0.0.11`, bare hostnames like `shen` won't resolve.

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
