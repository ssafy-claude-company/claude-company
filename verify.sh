#!/usr/bin/env bash
# murmur/Organt 단일 검증 — 전부 murmur-stack 실코드 대상(단일 진실원, PJT 미러 폐지).
# 사용: bash /root/murmur-stack/verify.sh   (--fast 로 빌드 생략)
set -uo pipefail
R=/root/murmur-stack
VENV=$R/.venv/bin
fail=0

echo "== 1) sns (Django) =="
( cd "$R/murmur/backend" && DJANGO_SETTINGS_MODULE=config.settings PYTHONPATH="$R" \
  "$VENV/python" manage.py test sns 2>&1 | grep -E "^Ran|^OK|FAILED|ERROR" ) || fail=1

echo "== 2) system unittest (레포 내장) =="
( cd "$R" && PYTHONPATH="$R" "$VENV/python" -m unittest discover -s system/tests -t "$R" 2>&1 | tail -2 ) || fail=1

echo "== 3) 브레인 pytest (murmur-stack/tests, 441 — 실 system/guide/organt 대상) =="
# organt_discord.main 은 organt_discord/main.py shim이 guide.discord_main+organt.builder로 재수출.
( cd "$R" && PYTHONPATH="$R" "$VENV/python" -m pytest tests/ -q 2>&1 | tail -1 ) || fail=1

if [ "${1:-}" != "--fast" ]; then
  echo "== 4) 프론트 빌드 =="
  ( cd "$R/murmur/frontend" && npm run build 2>&1 | tail -2 ) || fail=1
fi

echo "== 5) STATE.md 신선도 (heads 대조) =="
for r in system organt guide murmur; do
  actual=$(git -C "$R/$r" rev-parse --short HEAD 2>/dev/null)
  if grep -qE "^$r[[:space:]]+$actual" "$R/STATE.md"; then echo "  $r $actual  ✓"
  else echo "  $r $actual  ⚠ STATE.md 갱신 필요"; fail=1; fi
done

echo "======================================"
[ "$fail" = 0 ] && echo "ALL_GREEN" || echo "FAIL — 위 ⚠ 확인"
exit $fail
