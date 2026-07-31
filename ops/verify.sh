#!/usr/bin/env bash
# murmur/Organt 검증 — 단일 진실원. 계약 경계는 CONTRACTS.md.
# 사용:
#   bash verify.sh              전체(통합 게이트 — 병합 전 필수)
#   bash verify.sh --fast       빌드 생략
#   bash verify.sh --only system|guide|organt|murmur   세션 슬라이스(내부 루프, 빠름)
# [P1] --only는 그 레포 소유 테스트 + 계약(test_contracts)만 — 세션이 자기 부분만 빠르게 검증.
set -uo pipefail
# R = 검증 대상 트리. 기본 = 이 스크립트가 사는 트리(worktree에서 돌면 그 worktree — 세션이
# 자기 수정분을 검증하게). 정본 강제는 MURMUR_ROOT 명시(land.sh가 그렇게 부름).
R="${MURMUR_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV=$R/.venv/bin
fail=0
ONLY=""
FAST=""
for a in "$@"; do
  case "$a" in --only) NEXT=only;; --fast) FAST=1;; *) [ "${NEXT:-}" = only ] && ONLY="$a"; NEXT="";; esac
done

# 레포 소유 pytest 파일(2026-07-03 분류). 계약 테스트는 항상 포함.
GUIDE_T="test_channels test_guide_queue test_names test_roster test_discord_guide test_recovery"
ORGANT_T="test_organt test_persona"
_files() { for n in "$@" test_contracts; do echo "ops/tests/$n.py"; done; }

run_pytest() { ( cd "$R" && PYTHONPATH="$R:$R/ops" "$VENV/python" -m pytest "$@" -q 2>&1 | tail -1 ) || fail=1; }
run_sns() { ( cd "$R/murmur/backend" && DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH="$R" \
  "$VENV/python" manage.py test sns 2>&1 | grep -E "^Ran|^OK|FAILED|ERROR" ) || fail=1; }
run_sysunit() { ( cd "$R" && PYTHONPATH="$R" "$VENV/python" -m unittest discover -s system/tests -t "$R" 2>&1 | tail -2 ) || fail=1; }

if [ -n "$ONLY" ]; then
  echo "== 세션 슬라이스: $ONLY (내부 루프) + 계약 =="
  case "$ONLY" in
    system) run_sysunit; run_pytest $(cd "$R" && ls ops/tests/test_*.py | grep -vE "$(echo $GUIDE_T $ORGANT_T | tr ' ' '|')");;
    guide)  run_pytest $(_files $GUIDE_T);;
    organt) run_pytest $(_files $ORGANT_T);;
    murmur) run_sns; run_pytest ops/tests/test_contracts.py;;
    *) echo "  알 수 없는 레포: $ONLY (system|guide|organt|murmur)"; exit 2;;
  esac
  echo "======================================"
  [ "$fail" = 0 ] && echo "SLICE_GREEN ($ONLY)" || echo "FAIL"
  exit $fail
fi

# ── 전체(통합 게이트) ──
echo "== 1) sns (Django) =="; run_sns
echo "== 2) system unittest =="; run_sysunit
echo "== 3) 브레인 pytest (ops/tests/ — 실 system/guide/organt + 계약) =="; run_pytest ops/tests/
if [ -z "$FAST" ]; then
  echo "== 4) 프론트 빌드 =="; ( cd "$R/murmur/frontend" && npm run build 2>&1 | tail -2 ) || fail=1
  # [UI 정본 계약(2026-07-20)] 무리 표기·아바타 형태·공용 프리미티브 소유권 — 손 재구현이면 착지 실패
  ( cd "$R/murmur/frontend" && node tools/check_ui_contracts.mjs ) || fail=1
  # [흐름 평면 계약(2026-07-30)] 날짜선·사람 평문이 Task/과정 상자에 삼켜지는 회귀를 자로 잡는다
  ( cd "$R/murmur/frontend" && node tools/check_feed_flat.mjs ) || fail=1
  # [알파 계약(2026-07-30)] 반투명 필름을 면으로 쓰던 254곳·135값을 불투명 사다리와 토큰으로 정리 — 되돌아오지 못하게
  ( cd "$R/murmur/frontend" && node tools/check_alpha.mjs ) || fail=1
  # [줄 변경 계약(2026-07-31)] 내가 본 뒤 어느 줄이 바뀌었나 — 틀리면 멀쩡한 줄을 의심하게 된다
  ( cd "$R/murmur/frontend" && node tools/check_linediff.mjs ) || fail=1
fi
echo "== 5) STATE.md 신선도 (heads 대조) =="
# 2레포: claude-company(루트=병합, STATE가 이 안에 있어 순환→정보표시) + murmur(별도→강제)
echo "  claude-company $(git -C "$R" rev-parse --short HEAD 2>/dev/null)  (info — STATE 동거 레포)"
m=$(git -C "$R/murmur" rev-parse --short HEAD 2>/dev/null)
if grep -qE "^murmur[[:space:]]+$m" "$R/ops/STATE.md"; then echo "  murmur $m  ✓"
else echo "  murmur $m  ⚠ STATE.md 갱신 필요"; fail=1; fi
echo "== 6) 원격 백업 신선도 (미푸시 감지) =="
# [백업 누락 재발 방지(2026-07-28)] 7/3~7/28 3주간 브레인 레포가 원격에 한 번도 안 올라갔다
# (원격 미설정 — 7/6 병합 스냅샷 이후 단절). 커밋은 828개 정상이었으나 '사본'이 없어 디스크 한 장이
# 유일본이었다. 이력이 있다≠백업이 있다. 서버 이전 때야 발견돼, 검증이 상시로 잡게 한다.
# 차단 아닌 경고(fail 미설정) — 오프라인·원격 미연결 환경에서도 검증 자체는 돌아야 한다.
for _r in "$R" "$R/murmur"; do
  _n=$([ "$_r" = "$R" ] && echo claude-company || echo murmur)
  _u=$(git -C "$_r" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
  # [스냅샷 검증 대응 2026-07-30] land.sh는 정본이 아니라 detached 스냅샷에서 이 검증을 돌린다
  # (남의 동시 편집에 흔들리지 않게 — P2). detached HEAD엔 upstream이 없어 @{u}가 비고, 종전엔
  # 그걸 '원격 미연결(백업 없음)'로 읽어 실제 미푸시 상태를 가렸다. 백업 대상은 origin/main이니
  # 그 ref와 직접 대조한다 — 정본에서도 결과가 같고, 스냅샷에서도 참이 된다.
  if [ -z "$_u" ] && git -C "$_r" rev-parse --verify -q origin/main >/dev/null 2>&1; then
    _u=origin/main
  fi
  if [ -z "$_u" ]; then
    echo "  $_n  ⚠ 원격 미연결 — 이 디스크가 유일본(백업 없음)"
  else
    _c=$(git -C "$_r" rev-list --count "$_u"..HEAD 2>/dev/null || echo 0)
    if [ "${_c:-0}" -gt 0 ]; then echo "  $_n  ⚠ 미푸시 ${_c}개 — 원격에 백업 안 됨"
    else echo "  $_n  ✓ 원격 백업 최신"; fi
  fi
done
echo "== 6-1) 라이브 프로세스 (배포가 두 프로세스가 됐다 — 2026-07-28) =="
# 웹만 재시작하고 스트림(murmur-sse)을 잊으면, 화면은 멀쩡한데 실시간만 조용히 낡는다.
# 오류가 아니라 '아무 일도 안 일어남'으로 나타나는 종류라 사람이 못 잡는다 — 게이트가 잡는다.
for _u in murmur-web murmur-sse murmur-voice; do
  if systemctl is-active --quiet "$_u" 2>/dev/null; then echo "  $_u ✓"
  else echo "  $_u ⚠ 죽어 있음"; fail=1; fi
done
_sse=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://127.0.0.1:8002/api/stream/ 2>/dev/null)
if [ "$_sse" = "401" ]; then echo "  /api/stream/ 인증 게이트 ✓"
elif [ -z "$_sse" ] || [ "$_sse" = "000" ]; then echo "  /api/stream/ ⚠ 응답 없음(:8002)"; fail=1
else echo "  /api/stream/ ⚠ 예상 밖 응답 $_sse"; fail=1; fi
echo "== 6-2) 프로덕션 DB 마이그레이션 (2026-07-31 사고 재발 방지) =="
# 모델을 바꾸고 마이그레이션을 프로덕션에 안 걸면, 코드는 새 컬럼을 SELECT하는데 DB엔
# 없어서 그 모델을 건드리는 종단이 통째로 500이 된다. 실측으로 /api/agents/가 그렇게 죽었다.
# 화면 첫 페이지는 200이라 사람 눈엔 멀쩡해 보인다 - 게이트가 잡아야 하는 종류다.
# 개발용 sqlite가 아니라 프로덕션 env로 물어본다(엉뚱한 DB에 걸고 통과하는 것을 막는다).
if [ -r /etc/murmur-web.env ]; then
  _mig=$( set -a; . /etc/murmur-web.env; set +a
          cd "$R/murmur/backend" && PYTHONPATH="$R" "$VENV/python" manage.py migrate --check 2>&1 )
  if [ $? -eq 0 ]; then echo "  프로덕션 DB 최신 ✓"
  else
    echo "  ⚠ 프로덕션 DB에 미적용 마이그레이션이 있다 — 착지 전에 걸어라"
    echo "$_mig" | tail -3 | sed "s/^/     /"
    fail=1
  fi
else
  echo "  (프로덕션 env 없음 — 건너뜀)"
fi
echo "== 6-3) 러너 인제스트 생존 (2026-07-31 사고: 낡은 워커가 500을 뱉고 있었다) =="
# [조용한 실패가 가장 나쁘다] 마이그레이션은 걸었는데 웹을 재시작하지 않으면, 낡은 워커가 옛 모델을
# 들고 있어 /api/guide/ingest/가 통째로 500이 된다. 그동안 직원이 한 일은 murmur에 하나도 안 남는다.
# 화면은 멀쩡해 보이고(캐시된 타임라인) 아무도 모른다 — 실측: charge_usd로 3시간 동안 15건.
# 최근 10분 안에 인제스트 5xx가 있었으면 착지를 막는다.
_ing=$(journalctl -u murmur-web --since "10 min ago" --no-pager 2>/dev/null \
       | grep -c "Internal Server Error: /api/guide/ingest/" || true)
if [ "${_ing:-0}" -gt 0 ]; then
  echo "  ⚠ 최근 10분 인제스트 500이 ${_ing}건 — 직원이 한 일이 안 들어오고 있다"
  echo "     대개 '마이그레이션은 걸고 웹은 안 고침'이다: systemctl restart murmur-web"
  fail=1
else
  echo "  최근 10분 인제스트 오류 없음 ✓"
fi
echo "== 7) 비밀값 유출 검사 (원격 백업 = 외부 공개 가능성) =="
# [2026-07-28] ops/가 git에 올라가는 이유는 tests(41)·verify·land·wt·contracts가 '검증의 단일
# 진실원'이라 버전 관리가 필수이기 때문이다. 그러나 그 대가로 레포가 외부(원격)로 나가므로,
# 실키·비밀번호·개인키가 섞여 들어가면 그대로 유출된다. 감사 시점엔 실제 비밀값 0이었고
# (검출된 2건은 마스킹을 검증하는 테스트 픽스처), 이 게이트가 그 상태를 유지시킨다.
# 환경값(IP·도메인·경로)은 비밀이 아니라 차단하지 않는다 — 레포는 비공개 유지가 전제.
_leak=$( (cd "$R" && git grep -nIE \
  "(sk-ant-[A-Za-z0-9]|ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9]|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|\b45\.76\.226\.111\b|\b49\.142\.51\.40\b)" \
  -- . ':!ops/tests/' 2>/dev/null) | head -5 )
if [ -n "$_leak" ]; then
  echo "  ⚠ 비밀값으로 보이는 값이 추적 파일에 있다 — 푸시 전 제거·회전 필요:"
  echo "$_leak" | sed 's/^/    /'
  fail=1
else
  echo "  ✓ 실키·개인키 없음 (테스트 픽스처 제외)"
fi
echo "======================================"
[ "$fail" = 0 ] && echo "ALL_GREEN" || echo "FAIL — 위 ⚠ 확인"
exit $fail
