#!/usr/bin/env bash
# murmur Postgres 야간 백업 — 로컬 pg_dump 로테이션(14일 보관).
#
# 배경(2026-07-18 안정성 점검): DB(계정·결제·판 기록)가 이 VPS 로컬 Postgres 유일본인데
# 백업이 0개였다 — 디스크 사고 = 전량 소실. 이 스크립트는 최소층(로컬 스냅샷)이다.
# 오프사이트(외부 스토리지) 복제는 자격증명·비용 결정이 필요해 별도 결정 대기.
#
# 설치(사용자 승인 후 — 라이브 인프라):
#   crontab -l | { cat; echo '17 3 * * * /root/ClaudeCompany/ops/backup_db.sh >> /var/log/murmur-backup.log 2>&1'; } | crontab -
# 복구:
#   psql "$(cat /root/ClaudeCompany/.dburl)" < /root/backups/murmur/murmur-<날짜>.sql
set -euo pipefail
DBURL=$(cat /root/ClaudeCompany/.dburl)
DEST=/root/backups/murmur
KEEP_DAYS=14
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M)
OUT="$DEST/murmur-$STAMP.sql.gz"
pg_dump "$DBURL" | gzip > "$OUT"
SIZE=$(du -h "$OUT" | cut -f1)
find "$DEST" -name "murmur-*.sql.gz" -mtime +"$KEEP_DAYS" -delete
echo "[backup] $(date -u +%F\ %T) UTC  $OUT ($SIZE) — 보관 $(ls "$DEST" | wc -l)개"
