# Tailscale

## DNS — use public prefix, not Magic DNS

Always address shen services via `*.ts.shen.iorlas.net` (Cloudflare DNS → Tailscale IP).
Do **not** rely on Tailscale Magic DNS — it requires a Tailscale-registered client and is
unpredictable inside Docker containers.

`*.ts.shen.iorlas.net` is a Cloudflare DNS-only A record pointing to shen's Tailscale IP
(`100.65.108.29`). If the IP changes (machine re-registers), update the record in Cloudflare.
Verify current IP: `ssh shen "tailscale ip -4"`.

## FQDNs inside containers

Always use FQDNs for Tailscale hosts in Docker/compose configs (`hostname.network.ts.net`).
Short names don't resolve inside containers — musl libc (Alpine) and Docker DNS search domains
don't expand bare hostnames. This applies to nginx `resolver 127.0.0.11` too.

## Private service access

Private HTTP services are reachable at `http://name.ts.shen.iorlas.net` from any Tailscale peer.
Private TCP services bind to `100.65.108.29:PORT` — see [non-http.md](non-http.md).
