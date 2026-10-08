#!/usr/bin/env bash
# Nightly database backup. cron: 0 3 * * * /srv/shop-orders/deploy/backup.sh
BACKUP_DIR=/var/backups/shop
STAMP=$(date +%Y%m%d)

pg_dump "$DATABASE_URL" | gzip > "$BACKUP_DIR/shop-$STAMP.sql.gz"
find "$BACKUP_DIR" -name 'shop-*.sql.gz' -mtime +7 -delete
echo "backup finished: $BACKUP_DIR/shop-$STAMP.sql.gz"
