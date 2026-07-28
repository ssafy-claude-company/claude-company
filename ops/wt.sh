#!/usr/bin/env bash
# [세션 worktree 격리] 워커별 독립 스택 (2레포: claude-company + murmur).
#   wt.sh new <task>    claude-company(system/organt/guide/ops) + murmur 를 s/<task> 브랜치로
#   wt.sh rm  <task>    워크트리 제거(브랜치는 남김)
#   wt.sh list
# [개정 2026-07-28] 기본 = 세션당 worktree. 정본 트리 직접 작업 금지(pre-commit 훅이 막음).
#   근거: 정본=라이브 import 소스인데 07-04 "정본 직접+claim" 전환 후 claim이 무등재로 형해화,
#   07-28 타 세션 미완성 파일이 통커밋에 섞이는 실사고(d7f3556). 상세: STATE.md '세션 격리 재실효화'.
# 워커는:  MURMUR_ROOT=/root/wt/<task> bash /root/wt/<task>/ops/verify.sh --only <레포>
set -uo pipefail
MS=/root/ClaudeCompany
CMD="${1:-list}"; S="${2:-}"

case "$CMD" in
  new)
    [ -n "$S" ] || { echo "사용: wt.sh new <task>"; exit 2; }
    W="/root/wt/$S"
    [ -e "$W" ] && { echo "이미 존재: $W"; exit 1; }
    # 브랜치가 이미 있으면(rm은 브랜치를 남긴다) 재사용, 없으면 생성. 실패는 즉시 중단 —
    # 종전엔 -b 고정 + set -e 부재로 이름 재사용 시 조용히 실패하고 ✓를 찍었다(2026-07-28 수선).
    _add() { # _add <레포경로> <워크트리경로>
      if git -C "$1" show-ref --verify --quiet "refs/heads/s/$S"; then
        git -C "$1" worktree add "$2" "s/$S" >/dev/null
      else
        git -C "$1" worktree add "$2" -b "s/$S" >/dev/null
      fi
    }
    _add "$MS" "$W"            || { echo "✗ claude-company worktree 생성 실패"; exit 1; }
    echo "  [브랜치] claude-company (system/organt/guide/ops)"
    _add "$MS/murmur" "$W/murmur" || { echo "✗ murmur worktree 생성 실패"; exit 1; }
    echo "  [브랜치] murmur"
    ln -s "$MS/.venv" "$W/.venv"
    ln -sfn "$MS/murmur/frontend/node_modules" "$W/murmur/frontend/node_modules" 2>/dev/null || true
    echo "✓ 스택: $W"
    echo "  워커 진입:  MURMUR_ROOT=$W bash $W/ops/verify.sh --only <레포>" ;;
  rm)
    [ -n "$S" ] || { echo "사용: wt.sh rm <task>"; exit 2; }
    W="/root/wt/$S"
    git -C "$MS/murmur" worktree remove --force "$W/murmur" 2>/dev/null || true
    git -C "$MS" worktree remove --force "$W" 2>/dev/null || true
    rm -rf "$W"
    echo "✓ 제거: $W (브랜치 s/$S 는 남김 — 병합 후 정리)" ;;
  list)
    echo "== 활성 세션 스택 =="; ls -d /root/wt/*/ 2>/dev/null | sed 's/^/  /' || echo "  (없음)"
    echo "== claude-company worktrees =="; git -C "$MS" worktree list | sed 's/^/  /'
    echo "== murmur worktrees =="; git -C "$MS/murmur" worktree list | sed 's/^/  /' ;;
  *) echo "명령: new|rm|list"; exit 2 ;;
esac
