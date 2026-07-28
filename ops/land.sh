#!/usr/bin/env bash
# [착지] 세션이 자기 작업을 정본(main)에 스스로 병합·검증한다. 통합 세션 불필요.
#   ops/land.sh <task>     자기 worktree 안에서 실행 (예: bash ops/land.sh 변도진)
# 2레포(claude-company=루트, murmur). 동시 착지는 flock으로 직렬화(race 방지).
# 정본 직접 커밋은 pre-commit 훅(ops/hooks)이 막는다 — 수동 충돌 해결 커밋은 ALLOW_CANON_COMMIT=1.
set -uo pipefail
MS=/root/ClaudeCompany
S="${1:?사용: land.sh <task>}"
W="/root/wt/$S"
[ -d "$W" ] || { echo "worktree 없음: $W"; exit 1; }
# 레포 = "이름:경로(정본):worktree경로"
REPOS="claude-company:$MS:$W  murmur:$MS/murmur:$W/murmur"

exec 9>"$MS/ops/.land.lock"
echo "착지 큐 대기(다른 세션 착지 중이면 기다림)…"
flock 9
echo "════ '$S' 착지 시작 ════"

# 1) 미커밋 변경 체크
for spec in $REPOS; do
  name="${spec%%:*}"; rest="${spec#*:}"; wt="${rest#*:}"
  [ -d "$wt" ] || continue
  if [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]; then
    echo "⚠ $name 에 미커밋 변경 — 먼저 그 worktree에서 커밋하세요 ($wt)"; exit 1
  fi
done

# 2) 자기 브랜치(s/$S)를 정본에 병합 — 정본 브랜치는 그 체크아웃이 실제로 서 있는 브랜치.
#    (claude-company 정본은 main이 아니라 **master**다 — 하드코딩 'main'이 rev-list 실패를
#     '0 커밋'으로 삼켜 조용히 병합 0건이 되던 결함 수선, 2026-07-09 이현준-3.)
merged=0
for spec in $REPOS; do
  name="${spec%%:*}"; rest="${spec#*:}"; main="${rest%%:*}"
  br="s/$S"
  git -C "$main" show-ref --verify --quiet "refs/heads/$br" 2>/dev/null || continue
  base=$(git -C "$main" symbolic-ref --short HEAD 2>/dev/null || echo main)
  ahead=$(git -C "$main" rev-list --count "$base..$br" 2>/dev/null || echo 0)
  if [ "$ahead" -gt 0 ]; then
    # [안전] 정본 체크아웃이 더러우면(타 세션이 그 트리에서 작업 중) 브랜치 전환·병합 금지 —
    # 여긴 라이브 러너의 import 소스다. 남의 미커밋 위에서 checkout/merge 하지 않는다.
    if [ -n "$(git -C "$main" status --porcelain 2>/dev/null)" ]; then
      echo "  ⚠ $name 정본 체크아웃($main)에 미커밋 변경(타 세션 작업 중일 수 있음) — 병합 보류."
      echo "     그 트리가 정리된 뒤 다시 land.sh 하거나, 통합 담당에게 브랜치($br)를 맡기세요."
      exit 1
    fi
    echo "  $name: $br → $base 병합 ($ahead 커밋)"
    git -C "$main" checkout -q "$base"
    if ! git -C "$main" merge --no-edit "$br"; then
      echo "  ⚠ $name 병합 충돌 — 'git -C $main status'로 수동 해결 후"
      echo "     ALLOW_CANON_COMMIT=1 git -C $main commit  (사유를 메시지에) 하고 다시 land.sh"; exit 1
    fi
    merged=$((merged+1))
  fi
done
[ "$merged" = 0 ] && { echo "병합할 커밋 없음(브랜치가 정본과 같음)."; exit 0; }

# 3) murmur 스탬프 갱신(claude-company는 STATE 동거라 신선도 info)
#    LAND_OK=1: 정본 직접 커밋을 막는 pre-commit 훅의 착지 전용 통과로.
m=$(git -C "$MS/murmur" rev-parse --short HEAD)
sed -i -E "s/^(murmur[[:space:]]+)[0-9a-f]+/\1$m/" "$MS/ops/STATE.md"
git -C "$MS" add ops/STATE.md 2>/dev/null && LAND_OK=1 git -C "$MS" -c user.email=o@l -c user.name="$S" commit -q -m "state: $S 착지 스탬프" 2>/dev/null || true

# 4) 정본 전체 검증
echo "════ 정본 전체 검증 ════"
if MURMUR_ROOT="$MS" bash "$MS/ops/verify.sh"; then
  echo "✅ '$S' 착지 완료 — 정본 ALL_GREEN. 라이브 반영은 사용자 승인 후 서비스 재시작."
else
  echo "❌ 착지 후 검증 실패 — 위 확인. (되돌리기: git -C $MS reset --hard / git -C $MS/murmur reset --hard)"; exit 1
fi
