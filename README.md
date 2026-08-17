# SafeBill - Smart Financial Management

## Docker Commands

### Start the system in the background (Detached mode)
```bash
docker compose up -d
```

# Rebuild and start the system
## Use this instead of the regular start command only if you added new dependencies/libraries or ### changed the Docker configuration.

```bash
docker compose up --build -d
```

# Stop and remove containers
## Gracefully shuts down the database, worker, and server.
```bash
docker compose down
```