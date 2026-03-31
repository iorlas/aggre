# PostgreSQL as a Task Queue

Using `FOR UPDATE SKIP LOCKED` + `LISTEN/NOTIFY` to implement a durable task queue directly in PostgreSQL.

## Core Pattern

### Claiming tasks

```sql
UPDATE tasks SET status = 'processing', locked_at = now()
WHERE id IN (
  SELECT id FROM tasks
  WHERE status = 'pending' AND attempts < 5
  ORDER BY created_at
  FOR UPDATE SKIP LOCKED
  LIMIT 10
)
RETURNING *;
```

- `FOR UPDATE SKIP LOCKED` — atomic claiming, no two workers process the same row, no blocking.
- Transaction safety — if a worker crashes, the lock releases and the task becomes available again.

### Retries

On failure, increment attempts and reset status:

```sql
UPDATE tasks SET status = 'pending', attempts = attempts + 1, last_error = '...'
WHERE id = $1;
```

Retry support is natural — just track an `attempts` column and filter on `attempts < max_attempts` when claiming.

### Push notifications

`LISTEN/NOTIFY` provides push-based wake-up so workers react immediately when tasks arrive, avoiding constant polling.

## Caveats

- **`NOTIFY` is not durable** — if no one is listening, the notification is lost. A polling fallback (e.g., every 30s) is required to catch missed notifications or tasks from crashed workers.
- **Long-running locks** — if processing takes minutes, the row stays locked. Use a `locked_at` timestamp + a reaper query that reclaims stale locks (e.g., `locked_at < now() - interval '5 minutes'`).
- **Table bloat** — completed tasks accumulate. Needs a cleanup strategy: archive table, partitioning by date, or periodic `DELETE`.
- **No backpressure** — unlike RabbitMQ/Redis Streams, there's no built-in mechanism to limit queue depth or consumer rate.
- **Connection cost** — each `LISTEN` requires a persistent connection. Fine for a few workers, problematic at scale.

## When it fits

- PostgreSQL is already in the stack
- Throughput is moderate (hundreds/sec, not millions)
- Transactional guarantees are valuable (task + side-effect in same transaction)
- No desire to operate another piece of infrastructure

## When to reach for something else

- Very high throughput or fan-out needs — Redis Streams, RabbitMQ
- Complex delayed/scheduled jobs — dedicated job system
- Many independent consumers at scale — connection-per-listener cost adds up
