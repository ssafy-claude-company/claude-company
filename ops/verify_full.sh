#!/usr/bin/env bash
# [시간당 전체 검증 — 착지를 막지 않는다 (2026-08-07 감사, 현준-4)]
#
# 종전엔 이 검증이 착지 잠금을 **끝까지** 쥐었다. 검증은 25~30분이 걸리고 타이머는 매시간
# 도니, 대략 시간의 절반은 어떤 세션도 착지하지 못했다(실측: 두 번 연속 큐에서 14분·23분 대기).
# 급한 보안 수정을 못 올리는 창이 매시간 열리는 셈이다.
#
# 잠금을 쥔 이유 자체는 옳다: 검증 도중에 착지가 파일을 바꾸면 무엇을 잰 것인지 알 수 없고,
# 게이트 4(프론트 빌드)는 **정본 dist에 쓴다** — 라이브가 보는 그 파일이다.
#
# 그래서 잠금을 없애는 대신 **창을 좁힌다**: 잠금을 쥐고 스냅샷을 뜬 뒤 곧바로 놓고,
# 검증은 스냅샷에서 돈다(수초 → 이후 착지는 자유). land.sh가 착지마다 하는 것과 같은 방식이라
# verify.sh가 스냅샷 뿌리에서 도는 것은 이미 증명돼 있다. 덤으로 시간당 검증이 라이브 dist를
# 더는 건드리지 않는다 — 캐치넷이 라이브 자산을 다시 쓰는 것은 원래 이상한 일이었다.
#
# 스냅샷을 못 뜨면 종전대로 정본에서 잠금을 쥔 채 돈다(무회귀).
set -uo pipefail
R="${MURMUR_ROOT:-/root/ClaudeCompany}"
snap=""
_drop() {
  [ -n "$snap" ] || return 0
  git -C "$R/murmur" worktree remove --force "$snap/murmur" 2>/dev/null
  git -C "$R" worktree remove --force "$snap" 2>/dev/null
  rm -rf "$(dirname "$snap")" 2>/dev/null
  git -C "$R" worktree prune 2>/dev/null; git -C "$R/murmur" worktree prune 2>/dev/null
  snap=""
}
trap _drop EXIT

exec 9>"$R/ops/.land.lock"
flock 9                                  # ── 여기부터 착지가 멈춘다(스냅샷 뜨는 동안만)
cc="$(git -C "$R" rev-parse HEAD)"
mm="$(git -C "$R/murmur" rev-parse HEAD)"
if snap="$(mktemp -d /tmp/verify-full-XXXXXX)/t" \
   && git -C "$R" worktree add --detach -q "$snap" "$cc" 2>/dev/null \
   && git -C "$R/murmur" worktree add --detach -q "$snap/murmur" "$mm" 2>/dev/null \
   && ln -s "$R/.venv" "$snap/.venv" \
   && ln -s "$R/murmur/frontend/node_modules" "$snap/murmur/frontend/node_modules"; then
  # dist는 git 밖이라 스냅샷에 없다 — 게이트 1이 dist 있는 상태를 전제로 응답을 재므로 복사한다.
  # 심링크가 아니라 복사여야 게이트 4의 빌드가 라이브 dist를 덮어쓰지 않는다(land.sh와 같은 규율).
  [ -d "$R/murmur/frontend/dist" ] && cp -a "$R/murmur/frontend/dist" "$snap/murmur/frontend/dist"
  VR="$snap"
  flock -u 9                             # ── 여기서 놓는다. 이후 착지는 자유롭게 흐른다
  echo "스냅샷에서 검증 (cc=${cc:0:8} mm=${mm:0:8}) — 착지 잠금 해제됨"
else
  _drop
  VR="$R"
  echo "⚠ 스냅샷 실패 — 정본에서 잠금을 쥔 채 검증(종전 동작)"
fi

MURMUR_ROOT="$VR" bash "$VR/ops/verify.sh"
rc=$?
if [ $rc -eq 0 ]; then
  rm -f "$R/ops/.FULL_VERIFY_RED"
else
  echo "FULL RED since $(date '+%m-%d %H:%M') — journalctl -u murmur-verify-full 확인" \
    > "$R/ops/.FULL_VERIFY_RED"
fi
exit $rc
