#!/usr/bin/env bash
# log_rotate.sh — flow.jsonl·audit.jsonl 크기 기반 회전 (관측계약 §회전·알림·ops)
#
#   임계(기본 5MB) 초과 파일을 .1~.N(기본 5)으로 밀어 회전하고 원경로에 새 파일을 만든다.
#   가장 오래된 .N은 밀림으로 덮여 삭제된다. 멱등 — 임계 미만이면 아무것도 안 한다.
#
# [방식 택일 — rename+새파일] 왜 copytruncate가 아니라 rename인가:
#   러너의 두 기록자 모두 append마다 open("a")→write→close 한다
#   (flow: system/sys_core.py:347, audit: system/audit.py:81 AuditLog.record) — fd를 계속
#   들고 있지 않으므로, mv 후 다음 append의 open("a")이 원경로에 새 파일을 만든다. 무손실.
#   copytruncate는 '복사~truncate 사이'의 append가 유실되는 근본적인 창이 있어 택하지 않았다.
# [한계 — 명시] 미래에 기록자가 fd를 계속 들고 있게 바뀌면, 회전 후에도 옛 fd(= .1 파일)에
#   계속 쓴다(기록 유실은 아니고 .1이 자람 — 다음 회전 주기에 다시 잡힌다). 그 경우 이 스크립트
#   대신 동봉 logrotate 설정(ops/organt-logs.logrotate, copytruncate)을 쓰거나 여기를 고칠 것.
#   ※ 두 메커니즘(이 스크립트의 timer vs logrotate.d)은 하나만 스케줄한다 — 중복 회전 방지.
#
# 사용:  ops/log_rotate.sh [--dry-run] [파일...]
#   파일 미지정 시 $STATE_DIR/{flow.jsonl,audit.jsonl}.
#   env: ORGANT_PJT(기본 /root/ClaudeCompany) · STATE_DIR · MAX_BYTES(5242880) · KEEP(5)
set -euo pipefail

PJT="${ORGANT_PJT:-/root/ClaudeCompany}"
STATE_DIR="${STATE_DIR:-$PJT/ops/var/organt_sns_state}"
MAX_BYTES="${MAX_BYTES:-5242880}"     # 5MB
KEEP="${KEEP:-5}"
DRY=0

args=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) args+=("$a") ;;
  esac
done
if [ "${#args[@]}" -gt 0 ]; then
  files=("${args[@]}")
else
  files=("$STATE_DIR/flow.jsonl" "$STATE_DIR/audit.jsonl")
fi

rotate_one() {
  local f="$1" size
  [ -f "$f" ] || { echo "skip  $f (없음)"; return 0; }
  size=$(stat -c%s "$f")
  if [ "$size" -le "$MAX_BYTES" ]; then
    echo "ok    $f (${size}B ≤ ${MAX_BYTES}B — 회전 불요)"
    return 0
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "would $f (${size}B > ${MAX_BYTES}B) → .1~.${KEEP} 밀고 새 파일 생성"
    return 0
  fi
  # .N-1→.N … .1→.2 순으로 민다(.N에 덮이는 게 최고령 삭제). 그 다음 라이브를 .1로.
  local i
  for ((i = KEEP - 1; i >= 1; i--)); do
    [ -f "$f.$i" ] && mv -f "$f.$i" "$f.$((i + 1))"
  done
  mv "$f" "$f.1"
  # 원경로에 새 파일 — 러너의 다음 open("a")이 이걸 잡는다. 권한은 옛 파일 기준 보존.
  touch "$f"
  chmod --reference="$f.1" "$f" 2>/dev/null || true
  chown --reference="$f.1" "$f" 2>/dev/null || true
  echo "rot   $f (${size}B) → $f.1"
}

# 동시 실행 방지(타이머+수동 겹침) — 락 못 잡으면 조용히 종료(다른 인스턴스가 하는 중).
mkdir -p "$STATE_DIR"
exec 9>"$STATE_DIR/.rotate.lock"
if ! flock -n 9; then
  echo "다른 회전 인스턴스 실행 중 — 종료."
  exit 0
fi

for f in "${files[@]}"; do
  rotate_one "$f"
done
