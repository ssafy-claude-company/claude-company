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

# 0) 정본 브랜치 고정 가드 (2026-07-29 추가)
#    세션이 정본 체크아웃을 feature 브랜치로 돌려세우면 이후 모든 착지가 그 브랜치로
#    흘러들고 main이 뒤처진다(07-29 실사고: feat/tenant-usage 전환 후 착지 5건 유실 위기).
#    정본은 항상 main이어야 한다 — 브랜치 작업은 자기 worktree에서.
for spec in "claude-company:$MS" "murmur:$MS/murmur"; do
  name="${spec%%:*}"; path="${spec#*:}"
  cur=$(git -C "$path" branch --show-current 2>/dev/null)
  if [ "$cur" != "main" ]; then
    echo "⛔ $name 정본이 '$cur' 브랜치에 서 있음 — 착지 중단."
    echo "   정본 브랜치를 바꾼 세션이 정리(자기 worktree로 이동) 후 main 복귀해야 함:"
    echo "   git -C $path checkout main   (미커밋·미병합 확인 후!)"
    exit 1
  fi
done

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
# [P1 수선 2026-07-30] 병합 전 두 정본의 HEAD를 기록 — verify 실패 시 자동 롤백해
# "정본은 새 코드·라이브는 옛 코드"인 반쪽 상태를 사람 손에 남기지 않는다(현준-4 실측).
# [수선 2026-07-30 2차] 롤백은 ①이번에 병합한 레포만 ②HEAD가 아직 우리 병합 결과일 때만.
# 무조건 pre-HEAD 리셋은 검증 중 끼어든 남의 커밋을 고아로 만든다(459a87a 실사고 — 복구됨).
pre_cc=$(git -C "$MS" rev-parse HEAD)
pre_mm=$(git -C "$MS/murmur" rev-parse HEAD)
merged_cc=0; merged_mm=0

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
    [ "$name" = "claude-company" ] && merged_cc=1 || merged_mm=1
  fi
done
[ "$merged" = 0 ] && { echo "병합할 커밋 없음(브랜치가 정본과 같음)."; exit 0; }

# 3) murmur 스탬프 갱신(claude-company는 STATE 동거라 신선도 info)
#    LAND_OK=1: 정본 직접 커밋을 막는 pre-commit 훅의 착지 전용 통과로.
#    [P0 수선 2026-07-30] 첫 매치(← HEAD 줄)만 — 종전엔 모든 'murmur <해시>' 줄을 덮어
#    과거 착지 기록의 해시가 매번 최신으로 밀렸다(현준-4 실측, 9e3b108 diff).
m=$(git -C "$MS/murmur" rev-parse --short HEAD)
sed -i -E "0,/^(murmur[[:space:]]+)[0-9a-f]+/s//\1$m/" "$MS/ops/STATE.md"
git -C "$MS" add ops/STATE.md 2>/dev/null && LAND_OK=1 git -C "$MS" -c user.email=o@l -c user.name="$S" commit -q -m "state: $S 착지 스탬프" 2>/dev/null || true

# 4) 정본 전체 검증 — 실패 시 자동 롤백. 단 ①이번에 병합한 레포만 ②HEAD가 아직 우리
#    결과(post_*)일 때만 되돌린다. 검증 중 끼어든 남의 커밋은 절대 리셋하지 않는다.
post_cc=$(git -C "$MS" rev-parse HEAD)          # 스탬프 커밋 포함 시점
post_mm=$(git -C "$MS/murmur" rev-parse HEAD)
# [P2 수선 2026-07-30] 검증은 정본 트리가 아니라 **그 순간의 스냅샷**에서 돈다.
#   왜: verify가 정본을 대상으로 도는데 다른 세션이 같은 트리를 계속 고친다(STATE.md 쓰기·자기
#   착지). flock은 착지끼리만 직렬화하고 일반 편집은 막지 않아, 검증이 무작위로 red가 됐다
#   (현준-4 실측 2건: 브레인 테스트 1건이 떴다가 같은 트리 3회 재실행에서 965/965 통과 /
#   게이트 5가 STATE 갱신 필요로 떴다가 즉시 재판정에서 통과). red면 배포가 멈추므로 남의 편집이
#   내 착지를 떨어뜨렸다. 스냅샷은 우리가 병합한 커밋에 고정돼 검증이 결정론이 된다.
#   .venv·node_modules는 트리 밖 자산이라 심링크로 빌려준다(복사는 수 GB·수 분).
#   스냅샷을 못 만들면 종전대로 정본에서 검증한다(검증 자체를 못 하게 만들지 않는다).
snap=""; snap_ok=""
_snap_make() {
  snap="$(mktemp -d /tmp/land-verify-XXXXXX)/t"
  git -C "$MS" worktree add --detach -q "$snap" "$post_cc" 2>/dev/null || return 1
  git -C "$MS/murmur" worktree add --detach -q "$snap/murmur" "$post_mm" 2>/dev/null || return 1
  ln -s "$MS/.venv" "$snap/.venv" || return 1
  ln -s "$MS/murmur/frontend/node_modules" "$snap/murmur/frontend/node_modules" || return 1
  # dist는 git 밖(무시 대상)이라 스냅샷에 없다. 그런데 sns 테스트(게이트 1)가 dist가 있는 상태를
  # 전제로 응답 경로를 재는데 프론트 빌드는 게이트 4다 — 빈 스냅샷에서는 게이트 1이 먼저 깨졌다
  # (실측: CSP 검사 2건). 심링크가 아니라 복사다(1.7MB) — 게이트 4의 빌드가 정본 dist를 반쯤
  # 덮어써 라이브가 낡은 조각을 섞어 서비스하는 일을 만들지 않는다.
  [ -d "$MS/murmur/frontend/dist" ] && cp -a "$MS/murmur/frontend/dist" "$snap/murmur/frontend/dist"
  return 0
}
_snap_drop() {
  [ -n "$snap" ] || return 0
  git -C "$MS/murmur" worktree remove --force "$snap/murmur" 2>/dev/null
  git -C "$MS" worktree remove --force "$snap" 2>/dev/null
  rm -rf "$(dirname "$snap")" 2>/dev/null
  git -C "$MS" worktree prune 2>/dev/null; git -C "$MS/murmur" worktree prune 2>/dev/null
}
trap _snap_drop EXIT
if _snap_make; then
  snap_ok=1; VR="$snap"
  echo "════ 검증(스냅샷 — 남의 동시 편집에 흔들리지 않게) ════"
else
  _snap_drop; snap=""; VR="$MS"
  echo "════ 정본 전체 검증 (⚠ 스냅샷 실패 — 동시 편집에 흔들릴 수 있음) ════"
fi
if ! MURMUR_ROOT="$VR" bash "$VR/ops/verify.sh"; then
  echo "❌ 검증 실패 — 자동 롤백 판단(브랜치 s/$S 는 그대로 남음)."
  rollback_one() { # <이름> <경로> <merged?> <pre> <post>
    [ "$3" = 1 ] || { echo "   $1: 이번 착지에서 병합 없음 — 손대지 않음"; return; }
    cur=$(git -C "$2" rev-parse HEAD)
    if [ "$cur" = "$5" ]; then
      git -C "$2" reset --hard -q "$4"; echo "   $1: 롤백 완료 → $4"
    else
      echo "   $1: ⚠ 검증 중 HEAD가 또 움직임($cur) — 자동 롤백 불가, 수동 확인 필요"
    fi
  }
  rollback_one claude-company "$MS"        "$merged_cc" "$pre_cc" "$post_cc"
  rollback_one murmur         "$MS/murmur" "$merged_mm" "$pre_mm" "$post_mm"
  echo "   원인 확인 후 다시 land.sh — verify가 비결정 의심이면 같은 트리에서 재실행해 볼 것."
  exit 1
fi

# 5) 라이브 반영 — 착지의 마지막 단계 (2026-07-28 추가, 현준-1 스큐 분석)
#    프론트 dist는 디스크 서빙이라 착지 즉시 라이브인데 백엔드는 gunicorn 메모리라 재시작
#    전까지 옛 상태 — "새 화면이 부르는 주소를 서버만 모르는" 스큐 창을 여기서 닫는다.
#    웹·러너 모두 상시 허가(STATE 2026-07-21 웹 / 2026-07-18 러너, 2026-07-28 사용자 재확인
#    "둘다 해도 되겠는데") + 판 진행 중 웹 재시작은 guide 3회 재시도가 흡수(현준-1 실측).
#    판 한복판임을 아는 세션은 LAND_SKIP_RUNNER=1 로 러너만 건너뛸 수 있다(사후 직접 재시작).
echo "════ 라이브 반영 ════"
# [P2 수선의 짝] 검증이 스냅샷에서 돌면 그 빌드 결과도 스냅샷에 남는다 — 라이브가 읽는 dist는
# 정본에 있으므로 여기서 정본을 빌드한다. 이 단계는 게이트가 아니라 산출물 만들기다(검증은 이미
# 끝났다). 실패하면 옛 dist가 그대로 서비스되어 화면만 낡으므로, 경고를 크게 남긴다.
_snap_drop; snap=""              # 검증 끝 — 스냅샷은 여기서 치운다(디스크·worktree 목록 정리)
if ( cd "$MS/murmur/frontend" && npm run build >/tmp/land-build.log 2>&1 ); then
  echo "  프론트 dist 재빌드 — 정본 반영"
else
  echo "  ⚠ 프론트 빌드 실패 — 화면이 낡은 채 남는다. /tmp/land-build.log 확인 후 수동 빌드:"
  echo "     cd $MS/murmur/frontend && npm run build"
fi
# 2026-07-28 SSE 도입으로 웹은 2프로세스(murmur-web :8000 + murmur-sse :8002) — 둘 다 재시작.
for svc in murmur-web murmur-sse; do
  systemctl restart "$svc" && echo "  $svc 재시작 — 반영 완료" \
    || echo "  ⚠ $svc 재시작 실패 — systemctl status $svc 확인"
done
# 브레인 변경 감지는 병합 전 HEAD(pre_cc) 기준 — HEAD@{1}은 스탬프 커밋에 가려
# 병합분을 못 본다(2026-07-30 실사고: 브레인 착지에 러너 재시작 누락).
if git -C "$MS" diff --name-only "$pre_cc..HEAD" 2>/dev/null | grep -qE '^(system|organt|guide)/'; then
  if [ "${LAND_SKIP_RUNNER:-}" = "1" ]; then
    echo "  ⚠ 러너 재시작 건너뜀(LAND_SKIP_RUNNER=1) — 브레인 반영 전. 잊지 말 것:"
    echo "     systemctl restart organt-runner"
  else
    systemctl restart organt-runner && echo "  organt-runner 재시작 — 브레인 반영 완료" \
      || echo "  ⚠ organt-runner 재시작 실패 — systemctl status organt-runner 확인"
  fi
fi
# 6) 원격 백업 — 푸시는 land.sh만 한다(세션 직접 푸시 금지: 정본-원격 갈라짐 방지, P3).
#    실패는 경고만 — 착지·반영은 이미 끝난 상태라 되돌릴 이유가 없다.
for spec in "claude-company:$MS" "murmur:$MS/murmur"; do
  name="${spec%%:*}"; path="${spec#*:}"
  git -C "$path" push origin main -q 2>/dev/null && echo "  $name → origin 푸시" \
    || echo "  ⚠ $name origin 푸시 실패(다음 착지나 자정 백업이 재시도)"
done
echo "✅ '$S' 착지 완료 — 정본 ALL_GREEN + 라이브 반영 + 원격 백업."
