#!/usr/bin/env bash
set -euo pipefail

# Daily Postgres backup - runs pg_dump *inside* the db container via
# `docker compose exec`, not a host-installed pg_dump client hitting
# localhost:5432 - docker-compose.prod.yml deliberately does not publish
# Postgres' port to the host at all (see that file's own comments), so
# there is nothing on localhost:5432 to connect to from here in the
# first place.
#
# --- One-time setup on the server ---
#   1. chmod +x backup.sh
#   2. Edit PROJECT_DIR below to this repo's absolute path on the server.
#   3. Test it manually once: ./backup.sh
#      (confirm a .sql.gz file actually shows up under backups/)
#   4. Add it to root's (or whichever user owns the docker-compose stack)
#      crontab:
#         crontab -e
#      then add a line like, to run daily at 03:00 server time:
#         0 3 * * * /absolute/path/to/SafeBill/backup.sh >> /absolute/path/to/SafeBill/backups/cron.log 2>&1
#   5. Save and exit - `crontab -l` should now list it.
#
# Restoring from a backup (manual, deliberately not scripted - a restore
# is rare and destructive enough that it deserves a human actually
# looking at which file they're restoring):
#   gunzip -c backups/safebill_2026-01-15_03-00-01.sql.gz | \
#     docker compose -f docker-compose.prod.yml exec -T db \
#     psql -U "$POSTGRES_USER" "$POSTGRES_DB"

PROJECT_DIR="/path/to/SafeBill"   # <-- EDIT THIS to the real path on your server
BACKUP_DIR="${PROJECT_DIR}/backups"
KEEP_DAYS=14

cd "$PROJECT_DIR"

if [ ! -f .env ]; then
    echo "No .env found in ${PROJECT_DIR} - nothing to back up against." >&2
    exit 1
fi

# Sourced, not hardcoded here - one source of truth for credentials
# (the same .env docker-compose.prod.yml itself reads), so a rotated
# POSTGRES_PASSWORD never needs updating in two places.
set -a
source .env
set +a

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
BACKUP_FILE="${BACKUP_DIR}/safebill_${TIMESTAMP}.sql.gz"

echo "[$(date -Iseconds)] Starting backup -> ${BACKUP_FILE}"

# -T disables pseudo-TTY allocation - required for piping exec's stdout
# to gzip cleanly in a non-interactive (cron) context. PGPASSWORD is
# passed explicitly because pg_dump (a libpq client) looks for that exact
# variable name, not POSTGRES_PASSWORD (what the postgres image itself
# uses internally) - they're different names for the same value.
docker compose -f docker-compose.prod.yml exec -T \
  -e PGPASSWORD="${POSTGRES_PASSWORD}" \
  db pg_dump -U "${POSTGRES_USER:-safebill}" "${POSTGRES_DB:-safebill}" \
  | gzip > "$BACKUP_FILE"

echo "[$(date -Iseconds)] Backup written: ${BACKUP_FILE} ($(du -h "$BACKUP_FILE" | cut -f1))"

# Prune anything older than KEEP_DAYS - a personal project's disk isn't
# unlimited, and two weeks of daily backups is a reasonable recovery
# window for accidental deletes/bad edits without growing forever.
DELETED=$(find "$BACKUP_DIR" -name 'safebill_*.sql.gz' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    echo "[$(date -Iseconds)] Pruned ${DELETED} backup(s) older than ${KEEP_DAYS} days"
fi
