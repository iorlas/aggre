# Container Logs

## Via Dokploy UI (easiest)

Dokploy → Project → Compose → Logs tab

- stdout/stderr for all containers in the compose app
- Filter by container name via dropdown
- Real-time streaming

## Via SSH + Docker CLI (most powerful)

```sh
ssh iorlas@shen.iorlas.net -p 2201

# List running containers
docker ps

# Tail logs for a specific container
docker logs <container-name> -f --tail 100

# All containers in a compose app (filter by label)
docker ps --filter "label=com.docker.compose.project=<appName>" -q \
  | xargs -I{} docker logs {} --tail 50

# With timestamps
docker logs <container-name> --timestamps --tail 200
```

## Via Dokploy API

```
GET /api/deployment.all?composeId=<id>
```

Returns deployment history with `status` (done/error) and log URL per deployment.

## Notes

- Container names follow pattern: `<appName>-<service>-<N>`
- Dokploy UI log buffer is limited; use SSH docker logs for large volumes
- Persistent log storage (external sink) not yet configured on shen
