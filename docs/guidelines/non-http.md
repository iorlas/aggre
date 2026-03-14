# Non-HTTP Service Deployment

For services that don't speak HTTP (raw TCP, databases, message queues, etc.)
Traefik is not involved. Use direct port binding instead.

## Private (Tailscale-only)

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

## Public (internet-accessible)

Bind to `0.0.0.0` with an explicit host port. Docker bypasses UFW — any port bound here is reachable from the internet.

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

## Access Pattern Summary

| Access | Mechanism | Bind address |
|---|---|---|
| Private TCP (Tailscale) | Direct port binding | `100.65.108.29:PORT` |
| Public TCP (internet) | Direct port binding | `0.0.0.0:PORT` |
| Private HTTP | traefik-ts labels | (no port binding) |
| Public HTTP/HTTPS | Traefik public labels | (no port binding) |
