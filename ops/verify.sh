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
fi
echo "== 5) STATE.md 신선도 (heads 대조) =="
# 2레포: claude-company(루트=병합, STATE가 이 안에 있어 순환→정보표시) + murmur(별도→강제)
echo "  claude-company $(git -C "$R" rev-parse --short HEAD 2>/dev/null)  (info — STATE 동거 레포)"
m=$(git -C "$R/murmur" rev-parse --short HEAD 2>/dev/null)
if grep -qE "^murmur[[:space:]]+$m" "$R/ops/STATE.md"; then echo "  murmur $m  ✓"
else echo "  murmur $m  ⚠ STATE.md 갱신 필요"; fail=1; fi
echo "======================================"
[ "$fail" = 0 ] && echo "ALL_GREEN" || echo "FAIL — 위 ⚠ 확인"
exit $fail
