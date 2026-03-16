"""Cancel queued process-webpage tasks for already-processed content.

1. Reads processed content_ids from /tmp/processed_content_ids.json
2. Paginates Hatchet API for queued webpage tasks
3. Cross-references to find redundant tasks
4. Cancels redundant tasks via SSH to shen (admin login)

Usage:
    # First: generate processed IDs via MCP or DB query
    uv run python scripts/cancel_redundant_webpage_tasks.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import httpx
from tqdm import tqdm

# -- Config --
TENANT = "707d0855-80ab-4e1f-a156-f1c4546cbf52"
WEBPAGE_WF = "f34fb364-b71e-4733-924f-4ca407517b40"
HATCHET_URL = "http://hatchet.ts.shen.iorlas.net"
HATCHET_TOKEN = (
    "eyJhbGciOiJFUzI1NiIsICJraWQiOiJBcVBVSWcifQ."
    "eyJhdWQiOiJodHRwOi8vaGF0Y2hldC50cy5zaGVuLmlvcmxhcy5uZXQiLCAiZXhwIjo0OTI3MjA0ODk4LCAi"
    "Z3JwY19icm9hZGNhc3RfYWRkcmVzcyI6ImhhdGNoZXQtbGl0ZTo3MDc3IiwgImlhdCI6MTc3MzYwNDg5OCwg"
    "ImlzcyI6Imh0dHA6Ly9oYXRjaGV0LnRzLnNoZW4uaW9ybGFzLm5ldCIsICJzZXJ2ZXJfdXJsIjoiaHR0cDov"
    "L2hhdGNoZXQudHMuc2hlbi5pb3JsYXMubmV0IiwgInN1YiI6IjcwN2QwODU1LTgwYWItNGUxZi1hMTU2LWYx"
    "YzQ1NDZjYmY1MiIsICJ0b2tlbl9pZCI6ImYyNGM3YWJiLTE5ZTgtNGJiNS05ZjFkLThhZDgxZTJiZTY5NSJ9."
    "alCWbSgjDSTo34xknUfgPRysb0CTI0_G7dd4-lq48oRzRAh2ECgU_BY3VwzDKtHnGEaoEg8mnsMhkRdyrYot0g"
)
SSH_CMD = ["ssh", "iorlas@shen.iorlas.net", "-p", "2201"]
PAGE_SIZE = 100
PROCESSED_IDS_FILE = "/tmp/processed_content_ids.json"


def load_processed_content_ids() -> set[int]:
    """Load processed content_ids from pre-generated JSON file."""
    with open(PROCESSED_IDS_FILE) as f:
        return set(json.load(f))


def fetch_queued_webpage_tasks(client: httpx.Client) -> list[dict]:
    """Paginate Hatchet API for all queued process-webpage tasks."""
    url = f"{HATCHET_URL}/api/v1/stable/tenants/{TENANT}/workflow-runs"
    headers = {"Authorization": f"Bearer {HATCHET_TOKEN}"}
    params: dict[str, str | int] = {
        "statuses": "QUEUED",
        "workflow_ids": WEBPAGE_WF,
        "limit": PAGE_SIZE,
        "only_tasks": "false",
        "since": "2026-03-15T00:00:00Z",
        "include_payloads": "true",
    }

    resp = client.get(url, headers=headers, params=params)
    resp.raise_for_status()
    data = resp.json()
    total_pages = data.get("pagination", {}).get("num_pages", 1)

    all_tasks: list[dict] = list(data.get("rows", []))

    for page in tqdm(range(2, total_pages + 1), desc="Fetching queued tasks", initial=1, total=total_pages):
        params["offset"] = (page - 1) * PAGE_SIZE
        resp = client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        if not rows:
            break
        all_tasks.extend(rows)

    return all_tasks


def classify_tasks(
    tasks: list[dict], processed_ids: set[int]
) -> tuple[list[str], list[str]]:
    """Split tasks into (to_cancel, to_keep) by content_id processing state."""
    to_cancel: list[str] = []
    to_keep: list[str] = []

    for task in tasks:
        task_id = task.get("taskExternalId") or task.get("metadata", {}).get("id")
        if not task_id:
            continue

        content_id = None
        inp = task.get("input")
        if isinstance(inp, dict):
            # Queued tasks: {"content_id": ..., "domain": ...}
            # Completed tasks: {"input": {"content_id": ...}, ...}
            content_id = inp.get("content_id")
            if content_id is None:
                inner = inp.get("input")
                if isinstance(inner, dict):
                    content_id = inner.get("content_id")

        if content_id is not None and content_id in processed_ids:
            to_cancel.append(task_id)
        else:
            to_keep.append(task_id)

    return to_cancel, to_keep


def get_hatchet_admin_password() -> str:
    """Get the Hatchet admin password from the container on shen."""
    result = subprocess.run(
        SSH_CMD + [
            "docker exec $(docker ps --filter name=hatchet-lite -q | head -1)"
            " printenv SEED_DEFAULT_ADMIN_PASSWORD"
        ],
        capture_output=True, text=True,
    )
    pwd = result.stdout.strip()
    if not pwd:
        print(f"ERROR: Could not get Hatchet admin password. stderr={result.stderr.strip()}")
        sys.exit(1)
    return pwd


def cancel_tasks_on_shen(task_ids: list[str], password: str) -> tuple[int, int]:
    """Cancel tasks via SSH to shen using admin login cookie."""
    remote_script = textwrap.dedent("""\
        import json, sys, urllib.request, http.cookiejar

        HATCHET = "http://hatchet.ts.shen.iorlas.net"
        task_ids = json.loads(sys.stdin.read())

        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

        login_data = json.dumps({
            "email": "admin@example.com",
            "password": __PASSWORD__
        }).encode()
        req = urllib.request.Request(
            f"{HATCHET}/api/v1/users/login",
            data=login_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener.open(req)

        cancelled = 0
        errors = 0
        for tid in task_ids:
            try:
                req = urllib.request.Request(
                    f"{HATCHET}/api/v1/stable/workflow-runs/{tid}/cancel",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                opener.open(req)
                cancelled += 1
            except Exception as e:
                errors += 1
        print(json.dumps({"cancelled": cancelled, "errors": errors}))
    """).replace("__PASSWORD__", repr(password))

    total_cancelled = 0
    total_errors = 0
    batch_size = 50

    pbar = tqdm(total=len(task_ids), desc="Cancelling")
    for i in range(0, len(task_ids), batch_size):
        batch = task_ids[i : i + batch_size]

        result = subprocess.run(
            SSH_CMD + ["python3", "-c", remote_script],
            input=json.dumps(batch),
            capture_output=True, text=True,
        )

        if result.returncode != 0:
            tqdm.write(f"  Batch error: {result.stderr[:200]}")
            total_errors += len(batch)
        else:
            try:
                info = json.loads(result.stdout)
                batch_ok = info.get("cancelled", 0)
                batch_err = info.get("errors", 0)
                total_cancelled += batch_ok
                total_errors += batch_err
                if batch_err:
                    tqdm.write(f"  Batch had {batch_err} cancel errors")
            except json.JSONDecodeError:
                tqdm.write(f"  Unexpected: {result.stdout[:200]}")
                total_errors += len(batch)

        pbar.update(len(batch))

    pbar.close()
    return total_cancelled, total_errors


def main() -> None:
    print("=== Redundant Webpage Task Cancellation ===\n")

    # Step 1: Load processed content IDs
    print(f"Step 1: Loading processed content_ids from {PROCESSED_IDS_FILE}...")
    processed_ids = load_processed_content_ids()
    print(f"  {len(processed_ids):,} content items with text already extracted\n")

    # Step 2: Fetch queued webpage tasks from Hatchet
    print("Step 2: Fetching queued process-webpage tasks from Hatchet...")
    with httpx.Client(timeout=30.0) as client:
        tasks = fetch_queued_webpage_tasks(client)
    print(f"  {len(tasks):,} queued tasks fetched\n")

    # Step 3: Classify
    print("Step 3: Cross-referencing...")
    to_cancel, to_keep = classify_tasks(tasks, processed_ids)
    print(f"  To cancel (already processed): {len(to_cancel):,}")
    print(f"  To keep   (real work):         {len(to_keep):,}")
    print()

    if not to_cancel:
        print("Nothing to cancel!")
        return

    # Step 4: Get admin password
    print("Step 4: Getting Hatchet admin password from shen...")
    password = get_hatchet_admin_password()
    print("  Got it.\n")

    # Step 5: Cancel
    print(f"Step 5: Cancelling {len(to_cancel):,} redundant tasks...")
    cancelled, errors = cancel_tasks_on_shen(to_cancel, password)
    print(f"\n  Cancelled: {cancelled:,}")
    print(f"  Errors:    {errors:,}")
    print(f"  Kept:      {len(to_keep):,}")
    print("\nDone!")


if __name__ == "__main__":
    main()
