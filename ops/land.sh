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
# [큐 가시성·중복 방지 2026-08-06] 대기가 불투명해 세션들이 "멈췄나?" 하고 같은 착지를
# 두 번 큐잉했다(실측: 현준-2 중복 대기, 꼬리 30분+). 보유자를 파일로 공개하고,
# 같은 세션의 중복 진입은 거부한다.
HOLDER_F="$MS/ops/.land.holder"
# 같은 세션의 land.sh가 이미 떠 있으면(보유 중이든 대기 중이든) 중복 진입 거부.
if [ "$(pgrep -cf "ops/land\.sh $S\$")" -gt 1 ]; then
  echo "⛔ '$S' 착지가 이미 진행/대기 중 — 중복 실행 안 함. 기존 것을 기다리세요."; exit 1
fi
if ! flock -n 9; then
  h="$(cat "$HOLDER_F" 2>/dev/null || echo '?')"
  echo "착지 큐 대기 — 현재 착지 중: ${h:-?}"
  echo "  (착지 1건 ≈ 8분. 재실행하지 말 것 — 이 프로세스가 순서대로 진행됩니다)"
  flock 9
fi
printf '%s (since %s)\n' "$S" "$(date '+%H:%M:%S')" > "$HOLDER_F"
trap 'rm -f "$HOLDER_F"' EXIT
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
    dirty="$(git -C "$main" status --porcelain 2>/dev/null)"
    # [반복 차단 해소 2026-07-30, 현준-4] 더러운 것이 ops/STATE.md 하나뿐이면 그것만 커밋하고
    #   나아간다. 세션이 착지 뒤 정본 STATE.md에 기록을 덧쓰는데 훅이 정본 직접 커밋을 막아
    #   더러운 채 남고, 그게 다음 세션의 착지를 전부 막았다(하루 네 번 손으로 대신 커밋했다).
    #   STATE.md는 착지 기록 자체라 커밋이 정상 귀결이다 - 남의 '작업 중 코드'가 아니다.
    if [ -n "$dirty" ] && [ -z "$(echo "$dirty" | sed -E 's/^.{3}//' | grep -v '^ops/STATE\.md$')" ]; then
      echo "  $name 정본에 STATE.md 기록만 미커밋 - 착지 기록으로 함께 커밋합니다."
      git -C "$main" add ops/STATE.md 2>/dev/null \
        && LAND_OK=1 git -C "$main" -c user.email=o@l -c user.name="$S" \
           commit -q -m "state: 착지 기록 미커밋분 커밋(land.sh, $S 착지 중)" 2>/dev/null || true
      dirty="$(git -C "$main" status --porcelain 2>/dev/null)"
    fi
    if [ -n "$dirty" ]; then
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
trap '_snap_drop; rm -f "$HOLDER_F"' EXIT   # holder 정리 유지(trap 덮어쓰기 주의 — 08-06 수선)
if _snap_make; then
  snap_ok=1; VR="$snap"
  echo "════ 검증(스냅샷 — 남의 동시 편집에 흔들리지 않게) ════"
else
  _snap_drop; snap=""; VR="$MS"
  echo "════ 검증 (⚠ 스냅샷 실패 — 동시 편집에 흔들릴 수 있음) ════"
fi
# [처리량 A안 2026-08-06, 사용자 승인] 착지 검증은 **변경 영역 슬라이스**만 돈다.
#   전체 검증(1368+ 브레인 ≈8분)이 flock 직렬과 겹쳐 대기열 30분+를 만들었다(실측 4건 적체).
#   전체 게이트는 시간당 타이머(murmur-verify-full.timer)가 캐치넷으로 돌고, red면
#   ops/.FULL_VERIFY_RED 마커로 다음 착지들에 경고한다. 전체를 원하면 LAND_FULL=1.
_changed() { git -C "$1" diff --name-only "$2..HEAD" 2>/dev/null; }
slices=""
if [ "${LAND_FULL:-}" = "1" ]; then
  slices="FULL"
else
  ch_cc="$(_changed "$MS" "$pre_cc")"; ch_mm="$(_changed "$MS/murmur" "$pre_mm")"
  # murmur: .md 뿐이면 스킵, 아니면 murmur 슬라이스
  if echo "$ch_mm" | grep -qvE '(^$|\.md$)'; then slices="$slices murmur"; fi
  # 루트 레포: 디렉터리별 매핑(.md 뿐이면 스킵). ops/기타 → system(가장 넓은 브레인 슬라이스)
  if echo "$ch_cc" | grep -qvE '(^$|\.md$)'; then
    echo "$ch_cc" | grep -qE '^guide/'  && slices="$slices guide"
    echo "$ch_cc" | grep -qE '^organt/' && slices="$slices organt"
    echo "$ch_cc" | grep -qvE '(^$|\.md$|^guide/|^organt/)' && slices="$slices system"
  fi
  [ -z "$slices" ] && echo "  문서만 변경 — 테스트 슬라이스 생략(전체 게이트는 시간당 타이머가 커버)"
fi
_verify_run() {
  if [ "$slices" = "FULL" ]; then MURMUR_ROOT="$VR" bash "$VR/ops/verify.sh"; return; fi
  for sl in $slices; do
    echo "── 슬라이스: $sl ──"
    MURMUR_ROOT="$VR" bash "$VR/ops/verify.sh" --only "$sl" || return 1
  done
}
if [ -f "$MS/ops/.FULL_VERIFY_RED" ]; then
  echo "⚠⚠ 전체 검증(시간당)이 현재 RED 상태다 — $(head -1 "$MS/ops/.FULL_VERIFY_RED" 2>/dev/null)"
  echo "    이 착지의 슬라이스와 무관할 수 있으나, 원인 미상이면 LAND_FULL=1로 재확인 권장."
fi
if ! _verify_run; then
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
  # [옛 자산은 두 주를 산다(2026-08-03)] 빌드는 dist를 비우지 않는다(vite emptyOutDir:false) —
  # 착지 순간에 열려 있던 탭이 부르는 옛 css·js가 404가 되면 화면이 정본 스타일 없이 그려진다.
  # 대신 아무도 안 부르는 낡은 것은 여기서 걷어낸다. 지금 빌드가 쓴 파일은 mtime이 방금이라 남는다.
  gone=$(find "$MS/murmur/frontend/dist/assets" -type f -mtime +14 -print -delete 2>/dev/null | wc -l)
  [ "${gone:-0}" -gt 0 ] && echo "  옛 자산 ${gone}개 정리(2주 지난 것)"
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
    # [재시작이 돌던 턴을 죽인다(2026-08-02 실사고)] 착지가 러너를 무조건 재시작해, U-478의 배포/인프라
    # 봇이 1시간 54분째 돌던 턴이 통째로 사라졌다(01:48:44 재시작 = 세션 마지막 기록 시각). 그 턴은
    # turn_done을 못 남겨 **누계 5,578만 토큰이 장부에도 안 잡혔고**, 잃은 작업 때문에 판이 파킹됐다.
    # 실행 중인 codex 턴이 없을 때까지 기다렸다가 재시작한다(최대 20분 — 그 뒤엔 알리고 진행).
    # [기다릴 것은 '턴 0'이 아니라 '잃을 것이 적은 순간'이다(2026-08-02 재교정)] 두 판이 돌면 도는 턴은
    # 거의 항상 있다(실측: 13개 동시) — '전부 끝날 때까지'는 영영 오지 않아 20분을 버리고 강제 재시작했다.
    # 아픈 것은 **오래 돈 턴**을 죽이는 것이다(01:48 사고: 114분짜리 유실, 누계 5,578만 토큰 미계상).
    # 가장 오래된 턴이 5분 미만이 되는 순간까지만 기다린다 — 그때 잃는 것은 몇 분어치뿐이다.
    _oldest_turn() {
      ps -eo etimes,args | awk '/[c]odex exec/ { if ($1 > m) m = $1 } END { print m + 0 }'
    }
    # [기다림은 손실의 대가였다 — 손실을 고쳤으니 대가도 줄인다(2026-08-05, 현준-1)] 위 20분 대기는
    # '죽은 턴은 통째로 잃는다'는 전제 위에 있었다. 그 전제의 뿌리는 세션 손잡이(codex thread_id)를
    # **턴이 끝난 뒤에야** 영속시킨 것이다 — 중간에 죽으면 이어붙일 id가 없어 처음부터 다시 돌았다.
    # 이제 thread.started에서 즉시 저장하므로(codex_mcp_bridge on_session) 죽은 턴은 다음 턴에서
    # 같은 세션으로 이어진다. 남는 손실은 '그 턴의 남은 몇 분'뿐이라, 판 4개가 도는 지금 착지마다
    # 20분씩 전역 락을 쥐는 비용이 더 크다. 3분만 예의로 기다리고 진행한다.
    _w=0
    while [ "$_w" -lt 180 ] && [ "$(_oldest_turn)" -ge 300 ]; do
      if [ "$_w" = 0 ]; then
        echo "  ⏳ 오래 돈 턴이 있어 잠깐 기다립니다(최대 3분 — 죽어도 세션은 이어집니다) — 가장 오래된 턴 $(_oldest_turn)초"
      fi
      sleep 15
      _w=$((_w + 15))
    done
    if [ "$(_oldest_turn)" -ge 300 ]; then
      echo "  ⚠ 3분 뒤에도 오래 돈 턴이 있습니다($(_oldest_turn)초) — 그대로 재시작합니다(그 턴은 다음 턴에서 세션 재개로 이어집니다)"
    else
      echo "  ✓ 가장 오래된 턴 $(_oldest_turn)초 — 잃을 것이 적은 순간에 재시작합니다"
    fi
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
