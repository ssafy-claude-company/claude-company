#!/usr/bin/env bash
# [P2 — 세션 파일 점유 보드] 두 세션의 파일 중첩을 *사전* 가시화한다(Fable: 6 공존파일의 범용 답).
# 계약(CONTRACTS.md)이 seam 충돌을 막는다면, 이건 *내부 파일* 충돌을 병합 전에 보이게 한다.
#
#   claim.sh add <세션> <glob...>   내 점유 선언(예: claim.sh brain-A 'system/rule/comm_*' )
#   claim.sh release <세션>          내 점유 전부 해제
#   claim.sh list                    보드 출력
#   claim.sh check <세션>            내 git 변경이 *남의* 점유와 겹치나 확인(겹치면 경고)
set -uo pipefail
R=/root/murmur-stack
BOARD="$R/CLAIMS.md"
CMD="${1:-list}"; SES="${2:-}"

_lines() { grep -E "^\| [^|]+ \| " "$BOARD" 2>/dev/null | grep -v "세션 | 점유"; }

case "$CMD" in
  add)
    shift 2 || { echo "사용: claim.sh add <세션> <glob...>"; exit 2; }
    for g in "$@"; do
      grep -qF "| $SES | $g |" "$BOARD" 2>/dev/null || echo "| $SES | $g | $(date +%Y-%m-%d\ %H:%M) |" >> "$BOARD"
    done
    echo "점유 추가: $SES → $*" ;;
  release)
    [ -n "$SES" ] || { echo "사용: claim.sh release <세션>"; exit 2; }
    tmp=$(mktemp); grep -vE "^\| $SES \| " "$BOARD" > "$tmp" && mv "$tmp" "$BOARD"
    echo "점유 해제: $SES" ;;
  list)
    echo "== 세션 점유 보드 =="; _lines | sed 's/^/  /' || echo "  (비어있음)" ;;
  check)
    [ -n "$SES" ] || { echo "사용: claim.sh check <세션>"; exit 2; }
    # 내 git 변경 파일(4레포+메타, repo 상대→워크스페이스 상대)
    changed=$(mktemp)
    for r in system organt guide murmur .; do
      git -C "$R/$r" status --porcelain 2>/dev/null | awk '{print $2}' | while read f; do
        [ "$r" = "." ] && echo "$f" || echo "$r/$f"
      done
    done | sort -u > "$changed"
    conflict=0
    while IFS='|' read -r _ s g _; do
      s=$(echo "$s"|xargs); g=$(echo "$g"|xargs)
      [ "$s" = "$SES" ] && continue          # 내 점유는 건너뜀
      while read cf; do
        case "$cf" in $g) echo "  ⚠ 충돌: '$cf' 는 세션 '$s' 가 점유중('$g')"; conflict=1;; esac
      done < "$changed"
    done < <(_lines)
    rm -f "$changed"
    [ "$conflict" = 0 ] && echo "  ✓ 충돌 없음 — 내 변경이 남의 점유와 안 겹침" || echo "  → 조율 필요(같은 파일)."
    exit $conflict ;;
  *) echo "명령: add|release|list|check"; exit 2 ;;
esac
