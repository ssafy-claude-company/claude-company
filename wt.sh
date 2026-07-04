#!/usr/bin/env bash
# [P3 — 세션 worktree 격리] 워커별 독립 스택. 소유 레포만 브랜치(편집), 나머지는 main detached
# (last-green 기준 검증 → 남의 WIP 오염 차단·결정성). 오브젝트 공유라 디스크·fetch 비용 낮음.
#
#   wt.sh new <세션> <소유레포...>   예: wt.sh new s-edge guide organt
#   wt.sh rm  <세션>                 워크트리 제거(브랜치는 남김)
#   wt.sh list
# 워커는:  MURMUR_ROOT=/root/wt/<세션> bash /root/wt/<세션>/verify.sh --only <레포>
set -uo pipefail
MS=/root/ClaudeCompany
CMD="${1:-list}"; S="${2:-}"

case "$CMD" in
  new)
    [ -n "$S" ] || { echo "사용: wt.sh new <세션> <소유레포...>"; exit 2; }
    shift 2; OWNED="$*"; W="/root/wt/$S"
    [ -e "$W" ] && { echo "이미 존재: $W"; exit 1; }
    git -C "$MS" worktree add "$W" -b "s/$S" >/dev/null    # 메타레포(tests·verify·계약)
    for r in system organt guide murmur; do
      case " $OWNED " in
        *" $r "*) git -C "$MS/$r" worktree add "$W/$r" -b "s/$S" >/dev/null; echo "  [소유·브랜치] $r";;
        *)        git -C "$MS/$r" worktree add --detach "$W/$r" main >/dev/null; echo "  [고정·detached] $r";;
      esac
    done
    ln -s "$MS/.venv" "$W/.venv"
    ln -sfn "$MS/murmur/frontend/node_modules" "$W/murmur/frontend/node_modules" 2>/dev/null || true
    echo "✓ 스택: $W  (소유: ${OWNED:-없음})"
    echo "  워커 진입:  MURMUR_ROOT=$W bash $W/verify.sh --only <레포>" ;;
  rm)
    [ -n "$S" ] || { echo "사용: wt.sh rm <세션>"; exit 2; }
    W="/root/wt/$S"
    for r in system organt guide murmur; do git -C "$MS/$r" worktree remove --force "$W/$r" 2>/dev/null || true; done
    git -C "$MS" worktree remove --force "$W" 2>/dev/null || true
    rm -rf "$W"
    echo "✓ 제거: $W (브랜치 s/$S 는 남김 — 병합 후 정리)" ;;
  list)
    echo "== 활성 세션 스택 =="; ls -d /root/wt/*/ 2>/dev/null | sed 's/^/  /' || echo "  (없음)"
    echo "== 메타레포 worktrees =="; git -C "$MS" worktree list | sed 's/^/  /' ;;
  *) echo "명령: new|rm|list"; exit 2 ;;
esac
