# GitHub Actions Build Caching

Layer caching dramatically reduces build times for unchanged layers. Use GitHub
Actions cache backend (free, no external registry needed).

## Recommended workflow config

```yaml
- name: Build and push
  uses: docker/build-push-action@v6
  with:
    context: .
    push: true
    tags: ${{ steps.meta.outputs.tags }}
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

`mode=max` caches all intermediate layers, not just the final image.

## Dockerfile layer ordering

Put slow, rarely-changing layers first; put fast/frequent-changing layers last:

```dockerfile
# 1. Base image (rarely changes)
FROM python:3.12-slim

# 2. System dependencies (rarely changes)
RUN apt-get install -y curl build-essential

# 3. Dependency manifest only — cache bust only on dep changes, not code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

# 4. Application code (changes every commit)
COPY src/ src/

# 5. Config files LAST — only invalidates layers below this line
COPY config/ config/
```

**Common mistake:** `COPY . .` before `pip install` busts the dependency cache on every code change.

## Cache limits

GitHub Actions cache: 10GB per repo, evicted by LRU. Multi-platform builds
(`linux/amd64` + `linux/arm64`) use separate cache keys — each platform gets its own slice.
