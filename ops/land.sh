#!/usr/bin/env bash
# [착지] 세션이 자기 작업을 정본(main)에 스스로 병합·검증한다. 통합 세션 불필요.
#   ops/land.sh <세션>     자기 worktree 안에서 실행 (예: bash ops/land.sh 변도진)
# 동시 착지는 flock으로 직렬화(2세션 race 방지). 충돌 나면 멈추고 수동 해결 안내.
set -uo pipefail
MS=/root/ClaudeCompany
S="${1:?사용: land.sh <세션>}"
W="/root/wt/$S"
[ -d "$W" ] || { echo "worktree 없음: $W"; exit 1; }

exec 9>"$MS/ops/.land.lock"
echo "착지 큐 대기(다른 세션 착지 중이면 기다림)…"
flock 9
echo "════ '$S' 착지 시작 ════"

# 1) 미커밋 변경 체크 — 있으면 먼저 그 worktree에서 커밋하라
for r in system organt guide murmur; do
  [ -d "$W/$r" ] || continue
  if [ -n "$(git -C "$W/$r" status --porcelain 2>/dev/null)" ]; then
    echo "⚠ $r 에 미커밋 변경 — 먼저 그 worktree에서 'git -C $r add -A && git -C $r commit' 하세요"; exit 1
  fi
done

# 2) 자기 브랜치(s/$S)를 정본 main에 병합
merged=0
for r in system organt guide murmur; do
  br="s/$S"
  git -C "$MS/$r" show-ref --verify --quiet "refs/heads/$br" 2>/dev/null || continue
  ahead=$(git -C "$MS/$r" rev-list --count "main..$br" 2>/dev/null || echo 0)
  if [ "$ahead" -gt 0 ]; then
    echo "  $r: $br → main 병합 ($ahead 커밋)"
    git -C "$MS/$r" checkout -q main
    if ! git -C "$MS/$r" merge --no-edit "$br"; then
      echo "  ⚠ $r 병합 충돌 — 'git -C $MS/$r status'로 수동 해결 후 다시 land.sh"; exit 1
    fi
    merged=$((merged+1))
  fi
done
[ "$merged" = 0 ] && { echo "병합할 커밋 없음(브랜치가 main과 같음)."; exit 0; }

# 3) STATE 스탬프 갱신(verify 신선도 게이트 통과용)
for r in system organt guide murmur; do
  a=$(git -C "$MS/$r" rev-parse --short HEAD)
  sed -i -E "s/^($r[[:space:]]+)[0-9a-f]+/\1$a/" "$MS/ops/STATE.md"
done
git -C "$MS" add ops/STATE.md 2>/dev/null && git -C "$MS" -c user.email=o@l -c user.name="$S" commit -q -m "state: $S 착지 스탬프" 2>/dev/null || true

# 4) 정본 전체 검증
echo "════ 정본 전체 검증 ════"
if MURMUR_ROOT="$MS" bash "$MS/ops/verify.sh"; then
  echo "✅ '$S' 착지 완료 — 정본 ALL_GREEN. 라이브 반영은 사용자 승인 후 서비스 재시작."
else
  echo "❌ 착지 후 검증 실패 — 위 확인. (정본이 깨졌으면 되돌리기: git -C $MS/<repo> reset --hard)"; exit 1
fi
