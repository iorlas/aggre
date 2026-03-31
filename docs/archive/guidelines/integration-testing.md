# Integration Testing & New Service Checklist

Lessons from the Hatchet migration smoke test (2026-03-07). Each section links a root cause to a preventive practice.

## Lesson 1: Read vendor docs before writing docker-compose

**Root cause:** Composed docker-compose from intuition ("it's all-in-one, probably embeds postgres") instead of reading the hatchet self-hosting docs. Result: hatchet-lite silently failed with "goose: failed to open DB".

**Practice:** Before adding any new service to docker-compose, fetch the vendor's official docker-compose example. Extract: required env vars, companion services, volumes, healthcheck patterns.

## Lesson 2: Integration code needs integration tests

**Root cause:** `register()` and `start_worker()` wiring was in `pragma: no cover` code — never executed in tests. 385 unit tests passed but the worker crashed with "no actions registered".

**Practice:** For any framework integration layer, write at least one smoke test that exercises the real wiring: import the entry point, call it with a real (or stubbed) client, verify expected side effects. Even without the real framework, verify that `register()` returns the right type/count.

## Lesson 3: Validate .env contents match code expectations

**Root cause:** `.env` had `postgresql+psycopg2://` but the project uses `psycopg` (v3). The migrate container crashed with `ModuleNotFoundError: No module named 'psycopg2'`.

**Practice:** When changing database drivers or connection string formats, grep `.env*` and docker-compose files for the old format. Document the expected format in `.env.example`.

## Lesson 4: Docker networking — tokens encode hostnames

**Root cause:** `HATCHET_CLIENT_TOKEN` JWT encodes `grpc_broadcast_address: localhost:7077`. Inside Docker, the worker needs `hatchet-lite:7077`. Connection refused on `[::1]:7077`.

**Practice:** When a service uses tokens that encode connection info, check: (a) what's embedded in the token, (b) whether the SDK provides env var overrides for Docker/k8s. Search vendor docs for "docker" or "container" in the self-hosting section.

## Lesson 5: Set explicit timeouts on all external operations

**Root cause:** Default Hatchet task timeout is 60s. RSS collection with 27 feeds takes 15+ minutes. Tasks were repeatedly killed.

**Practice:** Always set explicit timeouts on framework task definitions. Estimate: count of items x per-item time x safety margin. For batch tasks, 30m is a reasonable start. For single-item tasks, 5-10m.

## Lesson 6: Test new services standalone before wiring

**Root cause:** All 50 webpage downloads failed with `400 Bad Request` from Browserless. The `/function` endpoint API may have changed between versions.

**Practice:** After adding or upgrading a service container, run a single manual test against it before wiring into the pipeline. E.g., `curl http://localhost:3001/json/version` to verify Browserless is up, then a simple page fetch to verify the API works.

## Pre-flight checklist for new services

Before deploying any new container service:

1. Read vendor's official docker-compose example
2. List required env vars, companion services, volumes
3. Test the service standalone (curl/CLI) before wiring into the app
4. Check if tokens/config encode hostnames that differ in Docker
5. Set explicit timeouts on all operations that interact with the service
6. Validate connection strings in `.env` match installed driver packages
