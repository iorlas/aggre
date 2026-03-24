#!/usr/bin/env bash
"true" '''\'
exec uv run --script "$0" "$@"
'''
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28,<1.0", "websockets>=15", "click>=8"]
# ///
# ruff: noqa: BLE001, C901, PLR0911, PLR0912, PLR0915, E402, ARG002, SIM103
"""dokctl — thin CLI over the Dokploy API.

Encodes known API workarounds so AI agents (and humans) don't need to.
"""

import asyncio
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
from pathlib import Path

import click
import httpx
import websockets

CONFIG_DIR = Path.home() / ".config" / "dokploy"
TIMEOUT = 30.0


# ── Config & HTTP ──


def load_config() -> tuple[str, str]:
    """Return (base_url, token). Exit with clear error if missing."""
    token_path = CONFIG_DIR / "token"
    url_path = CONFIG_DIR / "url"

    errors = []
    if not token_path.exists():
        errors.append(f"Missing token file: {token_path}")
    if not url_path.exists():
        errors.append(f"Missing URL file: {url_path}")
    if errors:
        for e in errors:
            click.echo(f"error: {e}", err=True)
        click.echo(
            f"\nSetup:\n  mkdir -p {CONFIG_DIR}\n  echo 'YOUR_TOKEN' > {token_path}"
            f"\n  echo 'https://your-dokploy-url' > {url_path}", err=True,
        )
        sys.exit(1)

    token = token_path.read_text().strip()
    url = url_path.read_text().strip().rstrip("/")
    return url, token


def make_client(url: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=url,
        headers={"x-api-key": token, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )


def api_call(client: httpx.Client, method: str, endpoint: str, data: dict | None = None) -> httpx.Response:
    """Make an API call. Endpoint is like 'compose.one' (no /api/ prefix)."""
    url = f"/api/{endpoint}"
    if method.upper() == "GET":
        return client.get(url, params=data)
    return client.post(url, json=data)


def _err(msg: str) -> None:
    """Print to stderr with stdout flush (prevents CI interleaving)."""
    sys.stdout.flush()
    click.echo(msg, err=True)


def print_response(resp: httpx.Response) -> None:
    """Print response JSON. Exit 1 on HTTP error."""
    try:
        click.echo(json.dumps(resp.json(), indent=2))
    except Exception:
        click.echo(resp.text)
    if resp.is_error:
        _err(f"\nerror: HTTP {resp.status_code}")
        sys.exit(1)


# ── Env helpers ──


def extract_env_vars(compose_content: str) -> list[str]:
    """Find all ${VAR} references in a compose file."""
    return sorted(set(re.findall(r'\$\{(\w+)\}', compose_content)))


def build_env_from_compose(compose_content: str) -> str:
    """Read ${VAR} refs from compose, resolve from os.environ, validate."""
    var_names = extract_env_vars(compose_content)
    if not var_names:
        return ""

    missing = [v for v in var_names if not os.environ.get(v)]
    if missing:
        _err("error: Missing environment variables referenced in compose file:")
        for v in missing:
            _err(f"  ${{{v}}}")
        _err("\nSet them in the environment before running dokctl.")
        sys.exit(1)

    lines = [f"{v}={os.environ[v]}" for v in var_names]
    click.echo(f"Env: {len(var_names)} vars resolved from compose: {', '.join(var_names)}")
    return "\n".join(lines)


def resolve_env(env_file: str | None, compose_content: str) -> str | None:
    """Resolve env from --env-file or auto-detect from compose ${VAR} refs."""
    if env_file:
        return Path(env_file).read_text()
    env_vars = extract_env_vars(compose_content)
    if env_vars:
        return build_env_from_compose(compose_content)
    return None


# ── WebSocket helpers ──


def _ws_url(base_url: str) -> str:
    return base_url.replace("https://", "wss://").replace("http://", "ws://")


def _fetch_ws(url: str, token: str, recv_timeout: float = 5.0) -> list[str]:
    """Connect to WebSocket, collect all messages, return as lines."""
    async def _inner() -> list[str]:
        lines: list[str] = []
        ssl_ctx = ssl.create_default_context()
        try:
            async with websockets.connect(
                url, ssl=ssl_ctx,
                additional_headers={"x-api-key": token},
                open_timeout=10, close_timeout=3,
            ) as ws:
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                        text = msg if isinstance(msg, str) else msg.decode("utf-8", errors="replace")
                        lines.append(text)
                    except asyncio.TimeoutError:
                        break
                    except websockets.exceptions.ConnectionClosed:
                        break
        except Exception as e:
            _err(f"warning: WebSocket error: {e}")
        return lines

    return asyncio.run(_inner())


def fetch_container_logs(base_url: str, token: str, container_id: str,
                         tail: int = 50, since: str = "5m", recv_timeout: float = 5.0) -> list[str]:
    ws_base = _ws_url(base_url)
    url = f"{ws_base}/docker-container-logs?containerId={container_id}&tail={tail}&since={since}"
    return _fetch_ws(url, token, recv_timeout)


def fetch_deploy_log(base_url: str, token: str, log_path: str, recv_timeout: float = 5.0) -> list[str]:
    ws_base = _ws_url(base_url)
    url = f"{ws_base}/listen-deployment?logPath={urllib.parse.quote(log_path)}"
    return _fetch_ws(url, token, recv_timeout)


# ── Container health helpers ──


def get_containers(client: httpx.Client, app_name: str) -> list[dict]:
    resp = api_call(client, "GET", "docker.getContainers")
    if resp.is_error:
        return []
    containers = resp.json()
    if not isinstance(containers, list):
        return []
    return [c for c in containers if app_name in c.get("name", "")]


def _is_one_shot(c: dict) -> bool:
    """Exited with code 0 = successful migration/init task."""
    return c.get("state") == "exited" and "Exited (0)" in c.get("status", "")


def _container_ok(c: dict) -> bool:
    if _is_one_shot(c):
        return True
    state = c.get("state", "")
    status = c.get("status", "")
    if state == "running" and "(healthy)" in status:
        return True
    if state == "running" and "(health:" not in status.lower():
        return True  # running without healthcheck defined
    return False


def _container_converging(c: dict) -> bool:
    state = c.get("state", "")
    status = c.get("status", "")
    if state == "running" and "(health: starting)" in status.lower():
        return True
    if state == "restarting":
        return True
    return False


def _container_label(c: dict, app_name: str) -> str:
    name = c.get("name", "?").replace(f"{app_name}-", "").rstrip("-1234567890")
    status = c.get("status", "")
    state = c.get("state", "?")
    if _is_one_shot(c):
        return ""  # skip in output
    if "(healthy)" in status:
        return f"{name}=ok"
    if "(health: starting)" in status.lower():
        return f"{name}=starting"
    if state == "restarting":
        return f"{name}=restarting"
    return f"{name}={state}"


def show_problem_logs(base_url: str, token: str, containers: list[dict], app_name: str) -> None:
    problem = [c for c in sorted(containers, key=lambda c: (
        0 if c.get("state") in ("exited", "dead", "created") else
        1 if "(unhealthy)" in c.get("status", "") else 2
    )) if not _container_ok(c) and not _is_one_shot(c)]

    if not problem:
        return

    _err("\nLogs for problem containers:")
    for c in problem:
        cid = c.get("containerId", "")
        if not cid:
            continue
        short = c.get("name", "?").replace(f"{app_name}-", "").rstrip("-1234567890")
        _err(f"\n--- {short} ({c.get('state', '?')}, {c.get('status', '')}) ---")
        for line in fetch_container_logs(base_url, token, cid, tail=50, since="5m", recv_timeout=3):
            _err(f"  {line.rstrip()[:200]}")


def show_deploy_log(base_url: str, token: str, log_path: str) -> None:
    if not log_path:
        return
    _err("\nDeploy build log:")
    lines = fetch_deploy_log(base_url, token, log_path, recv_timeout=5)
    if not lines:
        _err("  (no log content — file may have been cleaned up)")
        return
    for line in lines:
        _err(f"  {line.rstrip()[:200]}")


def verify_container_health(client: httpx.Client, app_name: str, timeout: int = 120) -> bool:
    max_attempts = timeout // 5
    for i in range(1, max_attempts + 1):
        containers = get_containers(client, app_name)
        if not containers:
            click.echo(f"  [health {i}/{max_attempts}] No containers found for {app_name}")
            time.sleep(5)
            continue

        all_ok = all(_container_ok(c) for c in containers)
        still_converging = any(_container_converging(c) for c in containers)

        parts = [_container_label(c, app_name) for c in containers]
        parts = [p for p in parts if p]  # filter out one-shot empties
        click.echo(f"  [health {i}/{max_attempts}] {', '.join(parts)}")

        if all_ok and containers:
            return True
        if not still_converging:
            return False

        time.sleep(5)

    return False


# ── CLI ──


class DokployID(click.ParamType):
    """Click type that accepts Dokploy IDs, including those starting with '-'."""
    name = "id"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        return value


DOKPLOY_ID = DokployID()


@click.group(context_settings={"ignore_unknown_options": True})
def cli() -> None:
    """dokctl — thin CLI over the Dokploy API."""
    pass


@cli.command()
@click.argument("endpoint")
@click.option("--data", "-d", default=None, help="JSON body (POST) or query params (GET with -X GET)")
@click.option("--method", "-X", default=None, help="HTTP method (default: POST if --data, GET otherwise)")
def api(endpoint: str, data: str | None, method: str | None) -> None:
    """Raw API call (like gh api)."""
    url, token = load_config()
    client = make_client(url, token)
    parsed = json.loads(data) if data else None
    m = (method or ("POST" if parsed else "GET")).upper()
    resp = api_call(client, m, endpoint, parsed)
    print_response(resp)


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("compose_id")
@click.option("--live", "-l", is_flag=True, help="Show live container health")
def status(compose_id: str, live: bool) -> None:
    """Show compose app status."""
    url, token = load_config()
    client = make_client(url, token)

    resp = api_call(client, "GET", "compose.one", {"composeId": compose_id})
    if resp.is_error:
        print_response(resp)
        return

    data = resp.json()
    app_name = data.get("appName", "?")
    click.echo(f"Name:         {data.get('name', '?')}")
    click.echo(f"App name:     {app_name}")
    click.echo(f"Status:       {data.get('composeStatus', '?')}")
    click.echo(f"Source type:   {data.get('sourceType', '?')}")
    click.echo(f"Compose type:  {data.get('composeType', '?')}")
    compose_file = data.get("composeFile", "")
    click.echo(f"Compose len:  {len(compose_file)} chars")
    env = data.get("env", "")
    env_keys = [line.split("=")[0] for line in env.strip().splitlines() if "=" in line]
    click.echo(f"Env keys:     {', '.join(env_keys) if env_keys else '(none)'}")

    deployments = data.get("deployments", [])
    if deployments:
        latest = deployments[0]
        click.echo(f"\nLast deploy:  {latest.get('title', '?')} ({latest.get('status', '?')})")
        click.echo(f"  at:         {latest.get('createdAt', '?')}")
        if latest.get("errorMessage"):
            click.echo(f"  error:      {latest['errorMessage']}")

    if live:
        click.echo("\nContainers:")
        containers = get_containers(client, app_name)
        if not containers:
            click.echo("  (none found)")
        for c in containers:
            short = c.get("name", "?").replace(f"{app_name}-", "").rstrip("-1234567890")
            click.echo(f"  {short:30} {c.get('state', '?'):10} {c.get('status', '')}")


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("compose_id")
@click.argument("compose_file")
@click.option("--env-file", "-e", default=None, help="Path to .env file (if omitted, auto-resolves from compose)")
def sync(compose_id: str, compose_file: str, env_file: str | None) -> None:
    """Sync compose file + env to Dokploy."""
    url, token = load_config()
    client = make_client(url, token)
    _do_sync(client, compose_id, compose_file, env_file)


def _do_sync(client: httpx.Client, compose_id: str, compose_file: str, env_file: str | None) -> None:
    """Shared sync logic used by both sync and deploy commands."""
    compose_content = Path(compose_file).read_text()

    payload: dict = {
        "composeId": compose_id,
        "composeFile": compose_content,
        "sourceType": "raw",
        "composePath": "./docker-compose.yml",
    }

    env_content = resolve_env(env_file, compose_content)
    if env_content is not None:
        payload["env"] = env_content

    resp = api_call(client, "POST", "compose.update", payload)
    if resp.is_error:
        print_response(resp)
        sys.exit(1)

    result = resp.json()
    stored_len = len(result.get("composeFile", ""))

    if stored_len < 10:
        _err(f"error: compose.update did not persist composeFile (got {stored_len} chars, sent {len(compose_content)})")
        sys.exit(1)

    click.echo(f"Synced: {stored_len} chars persisted, sourceType={result.get('sourceType', '?')}")

    if env_content is not None:
        click.echo(f"Env: {len(result.get('env', ''))} chars persisted")


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("compose_id")
@click.argument("compose_file")
@click.option("--env-file", "-e", default=None, help="Path to .env file (if omitted, auto-resolves from compose)")
@click.option("--timeout", "-t", default=300, help="Deploy timeout in seconds (default: 300)")
def deploy(compose_id: str, compose_file: str, env_file: str | None, timeout: int) -> None:
    """Sync + deploy + poll + verify container health."""
    url, token = load_config()
    client = make_client(url, token)

    # Step 1: sync
    _do_sync(client, compose_id, compose_file, env_file)

    # Step 2: snapshot previous deployment ID
    pre_resp = api_call(client, "GET", "deployment.allByCompose", {"composeId": compose_id})
    prev_deploy_id = None
    if not pre_resp.is_error:
        pre_deps = pre_resp.json()
        if pre_deps and isinstance(pre_deps, list):
            prev_deploy_id = pre_deps[0].get("deploymentId")

    # Step 3: trigger deploy
    image_tag = os.environ.get("IMAGE_TAG", "")
    title = f"Deploy {image_tag}" if image_tag else "Deploy via dokctl"

    sys.stdout.flush()
    click.echo(f"\nTriggering deploy ({title})...")
    deploy_resp = api_call(client, "POST", "compose.deploy", {
        "composeId": compose_id,
        "title": title,
    })
    if deploy_resp.is_error:
        print_response(deploy_resp)
        return

    click.echo("Deploy triggered. Polling status...")

    # Step 4: poll for NEW deployment
    max_attempts = timeout // 5
    for i in range(1, max_attempts + 1):
        time.sleep(5)
        status_resp = api_call(client, "GET", "deployment.allByCompose", {"composeId": compose_id})
        if status_resp.is_error:
            status_resp = api_call(client, "GET", "deployment.all", {"composeId": compose_id})

        if status_resp.is_error:
            click.echo(f"  [{i}/{max_attempts}] Failed to fetch status (HTTP {status_resp.status_code})")
            continue

        deployments = status_resp.json()
        if not deployments:
            click.echo(f"  [{i}/{max_attempts}] No deployments found")
            continue

        latest = deployments[0] if isinstance(deployments, list) else deployments

        if prev_deploy_id and latest.get("deploymentId") == prev_deploy_id:
            click.echo(f"  [{i}/{max_attempts}] Waiting for new deployment to appear...")
            continue

        dep_status = latest.get("status", "unknown")
        click.echo(f"  [{i}/{max_attempts}] status={dep_status}")

        if dep_status == "done":
            sys.stdout.flush()
            click.echo("\nDokploy reports deploy done.")
            break
        if dep_status == "error":
            _err("\nerror: Deploy failed")
            if latest.get("errorMessage"):
                _err(latest["errorMessage"])
            show_deploy_log(url, token, latest.get("logPath", ""))
            app_resp = api_call(client, "GET", "compose.one", {"composeId": compose_id})
            if not app_resp.is_error:
                app_name = app_resp.json().get("appName", "")
                containers = get_containers(client, app_name)
                if containers:
                    show_problem_logs(url, token, containers, app_name)
            sys.exit(1)
    else:
        _err(f"\nerror: Deploy timed out after {timeout}s")
        sys.exit(1)

    # Step 5: verify container health
    click.echo("Verifying container health...")
    app_resp = api_call(client, "GET", "compose.one", {"composeId": compose_id})
    if app_resp.is_error:
        _err("warning: could not fetch app info for health check")
        return

    app_name = app_resp.json().get("appName", "")
    if not app_name:
        _err("warning: no appName found, skipping health check")
        return

    healthy = verify_container_health(client, app_name, timeout=120)
    if healthy:
        sys.stdout.flush()
        click.echo("\nDeploy succeeded. All containers healthy.")
    else:
        _err("\nwarning: Deploy done but not all containers healthy.")
        containers = get_containers(client, app_name)
        show_problem_logs(url, token, containers, app_name)
        sys.exit(1)


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("compose_id")
@click.option("--service", "-s", default=None, help="Filter to a specific service name")
@click.option("--tail", "-n", default=100, help="Number of lines (default: 100)")
@click.option("--since", default="all", help="Time filter: 30s, 5m, 1h, all (default: all)")
@click.option("--deploy", "-D", "show_deploy", is_flag=True, help="Show deploy build log instead")
def logs(compose_id: str, service: str | None, tail: int, since: str, show_deploy: bool) -> None:
    """Show container runtime logs (or deploy build log with -D)."""
    url, token = load_config()
    client = make_client(url, token)

    resp = api_call(client, "GET", "compose.one", {"composeId": compose_id})
    if resp.is_error:
        print_response(resp)
        return

    data = resp.json()
    app_name = data.get("appName", "")

    if show_deploy:
        deployments = data.get("deployments", [])
        if not deployments:
            click.echo("No deployments found.")
            return
        latest = deployments[0]
        log_path = latest.get("logPath", "")
        click.echo(f"Deploy: {latest.get('title', '?')} ({latest.get('status', '?')})")
        click.echo(f"  at:   {latest.get('createdAt', '?')}")
        if not log_path:
            click.echo("  (no log path)")
            return
        lines = fetch_deploy_log(url, token, log_path, recv_timeout=5)
        if not lines:
            click.echo("  (no log content — file may have been cleaned up)")
            return
        for line in lines:
            click.echo(line.rstrip())
        return

    containers = get_containers(client, app_name)
    if not containers:
        click.echo("No running containers found.")
        return

    if service:
        containers = [c for c in containers if service in c.get("name", "")]
        if not containers:
            click.echo(f"No container found matching service '{service}'")
            available = get_containers(client, app_name)
            if available:
                click.echo("Available services:")
                for c in available:
                    name = c.get("name", "?").replace(f"{app_name}-", "").rstrip("-1234567890")
                    click.echo(f"  {name}")
            return

    for c in containers:
        cid = c.get("containerId", "")
        if not cid:
            continue
        short = c.get("name", "?").replace(f"{app_name}-", "").rstrip("-1234567890")
        fetched = fetch_container_logs(url, token, cid, tail=tail, since=since)
        if len(containers) > 1:
            click.echo(f"--- {short} ---")
        for line in fetched:
            click.echo(line.rstrip())
        if len(containers) > 1:
            click.echo()


@cli.command()
@click.argument("project_id")
@click.argument("app_name")
def init(project_id: str, app_name: str) -> None:
    """Create new compose app (with sourceType fix for I0071)."""
    url, token = load_config()
    client = make_client(url, token)

    resp = api_call(client, "POST", "compose.create", {
        "name": app_name,
        "projectId": project_id,
    })
    if resp.is_error:
        print_response(resp)
        return

    result = resp.json()
    compose_id = result.get("composeId")
    if not compose_id:
        _err("error: compose.create returned no composeId")
        click.echo(json.dumps(result, indent=2))
        sys.exit(1)

    click.echo(f"Created compose app: {compose_id}")

    fix_resp = api_call(client, "POST", "compose.update", {
        "composeId": compose_id,
        "sourceType": "raw",
    })
    if fix_resp.is_error:
        _err(f"warning: failed to fix sourceType (HTTP {fix_resp.status_code})")
    else:
        click.echo("Fixed sourceType to 'raw'")

    click.echo(f"\nCompose ID: {compose_id}")
    click.echo(f"Use in CI: scripts/dokctl.py deploy {compose_id} docker-compose.prod.yml")


if __name__ == "__main__":
    cli()
