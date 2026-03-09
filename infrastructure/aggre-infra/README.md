# Aggre Infrastructure — Contabo VPS

Remote infrastructure stack for Aggre: Tailscale mesh networking, PostgreSQL databases (app + Dagster), and Garage S3-compatible object storage.

All services share the Tailscale network namespace, so they are only accessible from your tailnet — nothing is exposed to the public internet.

## Prerequisites

- Docker and Docker Compose on the VPS
- A Tailscale account

## Initial Setup

### 1. Clone and configure

```bash
cd /opt
git clone <repo-url> aggre-infra   # or scp this directory
cd aggre-infra
cp .env.example .env
```

### 2. Set environment variables

Edit `.env`:

- **TS_AUTHKEY** — Generate a reusable auth key at https://login.tailscale.com/admin/settings/keys (enable "Reusable" so the container can rejoin after restarts).
- **POSTGRES_PASSWORD** — Set a strong password.
- **GARAGE_ADMIN_TOKEN** — Leave empty for now; you'll fill it in after first Garage start.

### 3. Start the stack

```bash
docker compose up -d
```

Verify Tailscale connected:

```bash
docker compose exec tailscale tailscale status
```

The node should appear as `aggre` in your tailnet. Note its Tailscale IP (e.g. `100.x.y.z`).

## Garage S3 Setup

### 4. Get the Garage node ID

```bash
docker compose exec garage /garage status
```

### 5. Assign a layout

```bash
docker compose exec garage /garage layout assign -z dc1 -c 1G <NODE_ID>
docker compose exec garage /garage layout apply --version 1
```

### 6. Create a bucket and access key

```bash
docker compose exec garage /garage bucket create aggre-bronze
docker compose exec garage /garage key create aggre-app-key
```

Note the **Key ID** and **Secret Key** from the output.

### 7. Grant bucket permissions

```bash
docker compose exec garage /garage bucket allow --read --write --owner aggre-bronze --key aggre-app-key
```

## Connecting from Your Dev Machine

### Install Tailscale

Install Tailscale on your dev machine (https://tailscale.com/download) and join the same tailnet.

### PostgreSQL

Two PostgreSQL instances run on the Tailscale network:

- **App database** — port 5432, database `aggre` (Alembic migrations, application data)
- **Dagster database** — port 5433, database `dagster` (Dagster run storage, event log, schedule state)

```bash
export AGGRE_DATABASE_URL="postgresql+psycopg://aggre:<password>@aggre-shen:5432/aggre"
export DAGSTER_PG_URL="postgresql://aggre:<password>@aggre-shen:5433/dagster"
```

Test the connections:

```bash
psql "postgresql://aggre:<password>@aggre-shen:5432/aggre"
psql "postgresql://aggre:<password>@aggre-shen:5433/dagster"
```

### Garage S3

The S3 API listens on port 3900:

```bash
export AGGRE_BRONZE_BACKEND=s3
export AGGRE_S3_ENDPOINT=http://aggre:3900
export AGGRE_S3_ACCESS_KEY=<key-id>
export AGGRE_S3_SECRET_KEY=<secret-key>
export AGGRE_S3_BUCKET=aggre-bronze
export AGGRE_S3_REGION=garage
```

### Run migrations

```bash
AGGRE_DATABASE_URL="postgresql://aggre:<password>@aggre:5432/aggre" alembic upgrade head
```

## Maintenance

### View logs

```bash
docker compose logs -f              # all services
docker compose logs -f postgres     # single service
```

### Backups

PostgreSQL:

```bash
docker compose exec postgres pg_dump -U aggre aggre > backup_$(date +%Y%m%d).sql
```

Garage data lives in `./data/garage/` — back up the entire directory, or use `garage bucket export` if available.

### Restart a single service

```bash
docker compose restart postgres
```

### Full stack restart

```bash
docker compose down && docker compose up -d
```
