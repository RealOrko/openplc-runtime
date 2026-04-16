# Docker Deployment

## Overview

OpenPLC Runtime v4 provides official Docker images for easy deployment across multiple platforms. The containerized runtime includes all dependencies and can be deployed with a single command.

## Official Image

**Registry:** GitHub Container Registry (GHCR)

**Image:** `ghcr.io/autonomy-logic/openplc-runtime:latest`

**Supported Architectures:**
- `linux/amd64` - x86_64 systems
- `linux/arm64` - ARM 64-bit (Raspberry Pi 4, etc.)
- `linux/arm/v7` - ARM 32-bit (Raspberry Pi 3, etc.)

## Quick Start

### Pull and Run

```bash
docker pull ghcr.io/autonomy-logic/openplc-runtime:latest

docker run -d \
  --name openplc-runtime \
  -p 8443:8443 \
  -p 5020:5020 \
  -p 4840:4840 \
  --cap-add=SYS_NICE \
  --cap-add=IPC_LOCK \
  --ulimit memlock=-1 \
  --ulimit rtprio=99 \
  -v openplc-runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

**Note:** Port 8443 is for the OpenPLC Editor connection and has no browser UI. Ports 5020 (Modbus TCP Slave) and 4840 (OPC UA) are the fieldbus protocols exposed by the built-in plugins, and are only open when those plugins are enabled in `plugins.conf`. See [Enabling Plugins](#enabling-plugins) below.

For most users, `docker-compose` is the recommended path — see [Docker Compose](#docker-compose).

### Stop and Remove

```bash
docker stop openplc-runtime
docker rm openplc-runtime
```

## Volume Management

### Persistent Data

The runtime stores important data in `/var/run/runtime/`:

**Contents:**
- `.env` - Environment variables (JWT secret, database URI, pepper)
- `restapi.db` - SQLite database with user accounts
- Socket files (created at runtime, ephemeral)

**Volume Mount:**
```bash
-v openplc-runtime-data:/var/run/runtime
```

### Named Volume (Recommended)

Using a named volume provides persistence and easy management:

```bash
# Create named volume
docker volume create openplc-runtime-data

# Run with named volume
docker run -d \
  --name openplc-runtime \
  -p 8443:8443 \
  -v openplc-runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

**Benefits:**
- Data persists across container restarts
- Easy backup and restore
- Docker manages volume location

### Bind Mount (Alternative)

For direct access to runtime data:

```bash
mkdir -p /path/to/runtime-data

docker run -d \
  --name openplc-runtime \
  -p 8443:8443 \
  -v /path/to/runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

**Benefits:**
- Direct file system access
- Easy inspection and backup
- Can use existing directory

**Considerations:**
- Requires proper permissions
- Host path must exist
- Less portable across systems

## Port Mapping

### Exposed Ports

| Port | Protocol | Purpose | Plugin required |
|------|----------|---------|-----------------|
| 8443 | HTTPS    | REST API and WebSocket debug for the OpenPLC Editor | Always on |
| 5020 | Modbus TCP | Modbus Slave | `modbus_slave` enabled |
| 4840 | OPC UA   | OPC UA Server | `opcua` enabled |

Ports for fieldbus protocols only start listening once the corresponding plugin is enabled — see [Enabling Plugins](#enabling-plugins).

### Using the Standard Modbus Port

To expose Modbus TCP on the standard port 502 on the host while the container continues to listen on 5020:

```bash
-p 502:5020
```

### Custom Host Port

To use a different host port for the Editor:

```bash
-p 9443:8443  # Editor connects to https://localhost:9443
```

### Localhost Only

To restrict access to localhost:

```bash
-p 127.0.0.1:8443:8443
```

### Multiple Interfaces

To bind to a specific interface:

```bash
-p 192.168.1.100:8443:8443
```

## Enabling Plugins

By default every plugin in `plugins.conf` ships disabled (the third field is `0`). That means ports 5020 (Modbus) and 4840 (OPC UA) are exposed by the container but nothing is listening on them until a plugin is turned on.

There are two ways to enable plugins:

### Option A — Mount a pre-configured plugins.conf

The repo ships a sample `plugins.conf.docker` with `modbus_slave` and `opcua` pre-enabled. Mount it over the default:

```bash
docker run -d \
  --name openplc-runtime \
  -p 8443:8443 -p 5020:5020 -p 4840:4840 \
  --cap-add=SYS_NICE --cap-add=IPC_LOCK \
  --ulimit memlock=-1 --ulimit rtprio=99 \
  -v openplc-runtime-data:/var/run/runtime \
  -v $(pwd)/plugins.conf.docker:/workdir/plugins.conf:ro \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

The equivalent line is already present (commented out) in `docker-compose.yml`.

### Option B — Enable through the REST API

Use the OpenPLC Editor (or the `/api/plugins/*` endpoints) to toggle plugins. Because `plugins.conf` lives inside the image at `/workdir/plugins.conf`, these changes are lost when the container is recreated unless you bind-mount the file as in Option A.

## Environment Variables

### Database URI

Override the default database location:

```bash
docker run -d \
  --name openplc-runtime \
  -p 8443:8443 \
  -e SQLALCHEMY_DATABASE_URI=sqlite:////var/run/runtime/custom.db \
  -v openplc-runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

### JWT Secret

Provide a custom JWT secret (not recommended, auto-generated is secure):

```bash
-e JWT_SECRET_KEY=your_64_character_hex_string_here
```

### Pepper

Provide a custom pepper for password hashing:

```bash
-e PEPPER=your_64_character_hex_string_here
```

## Real-Time Scheduling

The runtime core runs the PLC scan cycle with `SCHED_FIFO` priority and calls `mlockall(MCL_CURRENT|MCL_FUTURE)` to keep memory pinned. Both require extra privileges the default Docker sandbox does not grant.

**Recommended (least privilege):**

```bash
docker run -d \
  --name openplc-runtime \
  --cap-add=SYS_NICE \
  --cap-add=IPC_LOCK \
  --ulimit memlock=-1 \
  --ulimit rtprio=99 \
  -p 8443:8443 -p 5020:5020 -p 4840:4840 \
  -v openplc-runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

**Capabilities:**
- `SYS_NICE` — Required for `sched_setscheduler(SCHED_FIFO)` real-time scheduling
- `IPC_LOCK` — Required for `mlockall()` to pin the runtime's memory

**Ulimits:**
- `memlock=-1` — Lets the process lock unlimited memory (paired with `IPC_LOCK`)
- `rtprio=99` — Allows the process to request real-time scheduling priorities

Without these, the runtime still starts but logs errors from `sched_setscheduler` and `mlockall` on boot, and scan-cycle jitter will be higher.

**Fallback (full privilege):**

```bash
docker run -d \
  --name openplc-runtime \
  --privileged \
  -p 8443:8443 -p 5020:5020 -p 4840:4840 \
  -v openplc-runtime-data:/var/run/runtime \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

## Docker Compose

### Recommended Setup

A ready-to-use `docker-compose.yml` is included at the repository root. It publishes the Editor, Modbus, and OPC UA ports, adds the capabilities and ulimits needed for real-time scheduling, wires up a persistent volume, and runs a TLS handshake healthcheck.

**Start:**
```bash
docker compose up -d
```

**Stop:**
```bash
docker compose down
```

**Tail logs:**
```bash
docker compose logs -f
```

### Enabling Plugins at Boot

By default the bundled compose file starts with all plugins disabled (so only port 8443 is actually listening). To have Modbus Slave and OPC UA running on first start, uncomment the plugins.conf bind mount in `docker-compose.yml`:

```yaml
    volumes:
      - openplc-runtime-data:/var/run/runtime
      - ./plugins.conf.docker:/workdir/plugins.conf:ro
```

`plugins.conf.docker` ships with `modbus_slave` and `opcua` enabled. Copy it and edit to suit before mounting if you want a different combination.

### Localhost-Only Variant

To restrict access to the host machine, change the `ports` list to bind to `127.0.0.1`:

```yaml
    ports:
      - "127.0.0.1:8443:8443"
      - "127.0.0.1:5020:5020"
      - "127.0.0.1:4840:4840"
```

## Building Custom Images

### From Source

Clone the repository and build:

```bash
git clone https://github.com/Autonomy-Logic/openplc-runtime.git
cd openplc-runtime
git checkout development

docker build -t my-openplc-runtime .
```

### Using Build Script

```bash
bash scripts/build-docker-image.sh
```

### Multi-Architecture Build

For building images that support multiple architectures:

```bash
docker buildx create --use
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t my-openplc-runtime:latest \
  --push \
  .
```

## Development Container

For development and testing, use the development image:

```bash
bash scripts/build-docker-image-dev.sh
bash scripts/run-image-dev.sh
```

**Features:**
- Includes test dependencies
- Development tools installed
- Source code mounted as volume
- Interactive shell access

## Networking

### Bridge Network (Default)

Containers use Docker's default bridge network:

```bash
docker run -d \
  --name openplc-runtime \
  -p 8443:8443 \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

### Host Network

For direct host network access (Linux only):

```bash
docker run -d \
  --name openplc-runtime \
  --network host \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

**Note:** Port mapping is not needed with host network.

### Custom Network

Create and use a custom network:

```bash
docker network create openplc-net

docker run -d \
  --name openplc-runtime \
  --network openplc-net \
  -p 8443:8443 \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

## Logging

### View Logs

```bash
docker logs openplc-runtime
```

### Follow Logs

```bash
docker logs -f openplc-runtime
```

### Limit Log Output

```bash
docker logs --tail 100 openplc-runtime
```

### Log Drivers

Configure log driver in docker-compose.yml:

```yaml
services:
  openplc-runtime:
    image: ghcr.io/autonomy-logic/openplc-runtime:latest
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## Backup and Restore

### Backup Volume

```bash
# Create backup
docker run --rm \
  -v openplc-runtime-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/openplc-backup.tar.gz -C /data .
```

### Restore Volume

```bash
# Restore backup
docker run --rm \
  -v openplc-runtime-data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/openplc-backup.tar.gz -C /data
```

### Export Container

```bash
docker export openplc-runtime > openplc-container.tar
```

### Import Container

```bash
docker import openplc-container.tar my-openplc-runtime:backup
```

## Security Considerations

### Container Isolation

**Benefits:**
- Process isolation from host
- File system isolation
- Network isolation (unless host network used)

**Limitations:**
- Privileged mode reduces isolation
- Volume mounts expose host directories
- Port exposure creates network access

### Image Verification

Verify image authenticity:

```bash
docker pull ghcr.io/autonomy-logic/openplc-runtime:latest
docker inspect ghcr.io/autonomy-logic/openplc-runtime:latest
```

### Secrets Management

**Do Not:**
- Hard-code secrets in docker-compose.yml
- Commit .env files to version control
- Use default passwords in production

**Do:**
- Use Docker secrets (Swarm mode)
- Use environment files (.env) with proper permissions
- Rotate secrets regularly

### Network Security

**Recommendations:**
- Use firewall rules to restrict access
- Bind to localhost for local-only access
- Use reverse proxy for additional security
- Enable TLS certificate validation

## Troubleshooting

### Container Won't Start

**Check logs:**
```bash
docker logs openplc-runtime
```

**Common issues:**
- Port already in use
- Volume permission errors
- Insufficient resources

### Cannot Connect from OpenPLC Editor

**Verify container is running:**
```bash
docker ps | grep openplc-runtime
```

**Check port mapping:**
```bash
docker port openplc-runtime
```

**Test connectivity:**
```bash
curl -k https://localhost:8443/api/ping
```

### Real-Time Performance Issues

**Solutions:**
- Use `--privileged`, or the full capability set: `--cap-add=SYS_NICE --cap-add=IPC_LOCK --ulimit memlock=-1 --ulimit rtprio=99`
- Increase container resources
- Use host network mode
- Check host system load

### Cannot Reach Modbus or OPC UA From Another Host

This is the most common connectivity complaint. Run through:

1. **Ports published?** `docker port openplc-runtime` should show 5020 and 4840 mapped. If only 8443 is listed, your run command or compose file is missing them.
2. **Plugin enabled?** A published port does nothing if the plugin is not loaded. Check `plugins.conf` — the third field must be `1` for `modbus_slave` and/or `opcua`. Either mount `plugins.conf.docker` (see [Enabling Plugins](#enabling-plugins)) or toggle via the REST API.
3. **Endpoint bound to the right address?** If you are running a stack built before this fix, the OPC UA template defaulted to `opc.tcp://localhost:4840/...` and the Modbus slave defaulted to a developer LAN IP. Both now default to `0.0.0.0`. If you have an old config in `plugins/python/opcua/opcua.json` or a saved Modbus config, edit it to bind to `0.0.0.0`.
4. **Host firewall.** Confirm the host's firewall allows the mapped ports in.

### Volume Permission Errors

**Fix permissions:**
```bash
docker run --rm \
  -v openplc-runtime-data:/data \
  alpine chown -R 1000:1000 /data
```

### Out of Disk Space

**Clean up:**
```bash
docker system prune -a
docker volume prune
```

## Performance Optimization

### Resource Limits

Limit container resources:

```yaml
services:
  openplc-runtime:
    image: ghcr.io/autonomy-logic/openplc-runtime:latest
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 1G
        reservations:
          cpus: '1'
          memory: 512M
```

### CPU Pinning

Pin container to specific CPU cores:

```bash
docker run -d \
  --name openplc-runtime \
  --cpuset-cpus="0,1" \
  -p 8443:8443 \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

### Memory Optimization

```bash
docker run -d \
  --name openplc-runtime \
  --memory="1g" \
  --memory-swap="2g" \
  -p 8443:8443 \
  ghcr.io/autonomy-logic/openplc-runtime:latest
```

## CI/CD Integration

### GitHub Actions

The official images are built automatically via GitHub Actions:

**Workflow:** `.github/workflows/docker.yml`

**Triggers:**
- Push to development branch
- Pull request to development
- Manual workflow dispatch

**Process:**
1. Build multi-architecture images
2. Run tests
3. Push to GHCR
4. Tag with commit SHA and latest

### Using in CI/CD

Example GitHub Actions workflow:

```yaml
name: Test with OpenPLC Runtime

on: [push]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      openplc:
        image: ghcr.io/autonomy-logic/openplc-runtime:latest
        ports:
          - 8443:8443
        volumes:
          - openplc-data:/var/run/runtime
    steps:
      - uses: actions/checkout@v2
      - name: Test API
        run: |
          curl -k https://localhost:8443/api/ping
```

## Related Documentation

- [Editor Integration](EDITOR_INTEGRATION.md) - How OpenPLC Editor connects to runtime
- [Architecture](ARCHITECTURE.md) - System overview
- [Security](SECURITY.md) - Security considerations
- [Troubleshooting](TROUBLESHOOTING.md) - Common issues
- [API Reference](API.md) - REST endpoints
