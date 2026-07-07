#!/usr/bin/env bash
# prune_state.sh — 종료 세션 상태포인터(organt_state_*.json) 청소 (관측계약 §회전·알림·ops)
#
#   organt_state_{프로젝트태그}_{봇id}.json 은 organt/builder.py가 봇 세션 재개용으로 쓰는
#   포인터(세션id+cwd 한 줄)다. 프로젝트가 끝나면 아무도 안 지워 무한 누적된다(실측 25개 stale).
#
#   [살아있음 판정 = mtime] 활성 세션 포인터는 러너가 작업마다 다시 쓴다(라이브 프로젝트 파일은
#   mtime이 분 단위로 갱신됨 — 실측). 따라서 mtime이 N일(기본 7) 넘은 것만 종료분으로 보고 지운다.
#   flow/audit/jobs.json/personas.json/projects.json/role_profiles.json 은 패턴 밖 — 절대 안 건드림.
#
# 사용:  ops/prune_state.sh [--days N] [--apply]
#   기본 = dry-run(목록만 출력, 삭제 없음). 실제 삭제는 --apply 명시. 멱등.
#   env: ORGANT_PJT(기본 /root/ClaudeCompany) · STATE_DIR
set -euo pipefail

PJT="${ORGANT_PJT:-/root/ClaudeCompany}"
STATE_DIR="${STATE_DIR:-$PJT/ops/var/organt_sns_state}"
DAYS=7
APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --days=*) DAYS="${1#*=}"; shift ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done
case "$DAYS" in (''|*[!0-9]*) echo "--days 는 정수: $DAYS" >&2; exit 2;; esac
[ -d "$STATE_DIR" ] || { echo "상태 디렉터리 없음: $STATE_DIR" >&2; exit 1; }

# -mtime +N = N*24h '초과' 경과분만. -maxdepth 1 — 하위(작업공간 등) 침범 금지.
mapfile -t stale < <(find "$STATE_DIR" -maxdepth 1 -type f -name 'organt_state_*.json' -mtime +"$DAYS" | sort)
total=$(find "$STATE_DIR" -maxdepth 1 -type f -name 'organt_state_*.json' | wc -l)

if [ "${#stale[@]}" -eq 0 ]; then
  echo "정리 대상 없음 (organt_state_* 총 ${total}개, ${DAYS}일 초과분 0)."
  exit 0
fi

echo "organt_state_* 총 ${total}개 중 ${DAYS}일 초과 ${#stale[@]}개:"
for f in "${stale[@]}"; do
  printf "  %s  (mtime %s)\n" "$f" "$(date -r "$f" '+%F %H:%M')"
done

if [ "$APPLY" -eq 1 ]; then
  rm -f -- "${stale[@]}"
  echo "삭제 완료: ${#stale[@]}개 (보존 $((total - ${#stale[@]}))개)."
else
  echo "[dry-run] 삭제 안 함 — 실제 삭제는 --apply."
fi
