#!/usr/bin/env bash
# [ops] 옛 Render 웹서비스 정리 — "Render 종속 없애는 방향"(2026-07-08 사용자 결정)의 집행 도구.
#   기본: 옛 murmur 웹서비스를 suspend하고 전후 상태를 출력.
#   --delete: 영구 삭제(정지 후 별도 실행).
# 대상 id 고정 — 봇 배포 서비스(organt-p-*)는 절대 건드리지 않는다.
set -euo pipefail
SID="srv-d8tnrdog4nts73d4gcfg"   # 옛 murmur 웹(미사용) — ops/STATE.md "정지/삭제 가능" 기록분
KEY=$(psql "$(cat /root/ClaudeCompany/.dburl)" -t -A -c "SELECT value FROM sns_personsecret WHERE key ILIKE '%render%' LIMIT 1")
[ -n "$KEY" ] || { echo "Render 키를 금고(sns_personsecret)에서 못 찾음"; exit 1; }
st() { curl -s -H "Authorization: Bearer $KEY" "https://api.render.com/v1/services/$SID" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('name'), '| suspended:', d.get('suspended'))"; }
echo "before: $(st)"
if [ "${1:-}" = "--delete" ]; then
  curl -s -X DELETE -H "Authorization: Bearer $KEY" "https://api.render.com/v1/services/$SID" -o /dev/null -w "delete HTTP %{http_code}\n"
else
  curl -s -X POST -H "Authorization: Bearer $KEY" "https://api.render.com/v1/services/$SID/suspend" -o /dev/null -w "suspend HTTP %{http_code}\n"
  echo "after:  $(st)"
fi
