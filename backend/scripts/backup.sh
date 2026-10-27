#!/bin/sh
set -eu
mkdir -p /backups
while true; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/backups/money-extra-$stamp.dump"
  find /backups -type f -name 'money-extra-*.dump' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
  sleep 86400
done
