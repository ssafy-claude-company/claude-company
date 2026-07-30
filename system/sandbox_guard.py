# -*- coding: utf-8 -*-
"""판별 격리 도우미의 검증부 — 여기가 신뢰 경계다(2026-07-30, 현준-4).

특권 도우미(ops/sandbox/organt-sandbox)가 이 모듈만 쓰도록 떼어냈다. 도우미는 root로 돌고
호출자(러너)는 비특권이며 침해됐을 수 있다고 가정한다. 그래서 검증을 테스트 가능한 순수
함수로 두고, 도우미 본체는 얇게 유지한다 — 셸 스크립트에 검증을 묻으면 시험할 수 없다.

지키는 것 넷:
  · 작업공간은 정해진 뿌리 밑이어야 한다(realpath 기준 — 심링크 탈출 차단)
  · **uid는 호출자가 고르지 못한다** — 작업공간에서 도출한다. 고를 수 있으면 판 A의 트리를
    판 B의 uid로 넘겨 교차 오염을 만들 수 있다.
  · uid는 정해진 범위 안이고 0(root)이 될 수 없다
  · 판 대장은 root 전용이라 호출자가 재배정할 수 없다(충돌 = 판 경계 붕괴)
"""
import json
import os

UID_MIN, UID_MAX = 300000, 300999      # 이 호스트에서 다른 용도로 쓰지 않는 범위


class GuardError(Exception):
    """검증 실패 — 도우미는 이걸 받으면 아무것도 실행하지 않고 끝낸다."""


def validated_workspace(raw, ws_root):
    """작업공간을 검증해 (실경로, 뿌리실경로)를 준다. 뿌리 밖·비디렉터리는 거부."""
    if not raw or not str(raw).startswith("/"):
        raise GuardError("작업공간은 절대경로여야 한다")
    real = os.path.realpath(str(raw))
    root = os.path.realpath(str(ws_root))
    if real != root and not real.startswith(root + os.sep):
        raise GuardError(f"작업공간이 허용 뿌리 밖이다: {real}")
    if not os.path.isdir(real):
        raise GuardError("작업공간이 디렉터리가 아니다")
    return real, root


def project_key(real, root):
    """판을 가리키는 첫 단계 폴더 이름. 하위 폴더로 불려도 같은 판으로 묶는다.
    뿌리 자체는 판이 아니다 — 거기서 돌면 전 판이 한 uid를 공유하게 된다."""
    rel = os.path.relpath(real, root)
    if rel in (".", "") or rel.startswith(".."):
        raise GuardError("판 폴더를 지정해야 한다(뿌리 자체는 안 된다)")
    return rel.split(os.sep)[0]


def uid_for(key, uidmap_path):
    """판별 uid를 대장에서 찾거나 새로 배정한다(원자 교체). 대장은 root 전용."""
    d = os.path.dirname(uidmap_path)
    if d:
        os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        with open(uidmap_path, encoding="utf-8") as fp:
            m = json.load(fp)
        if not isinstance(m, dict):
            m = {}
    except (OSError, ValueError):
        m = {}
    if key in m:
        uid = int(m[key])
        if not (UID_MIN <= uid <= UID_MAX):
            raise GuardError(f"대장의 uid가 범위 밖이다: {key}={uid}")
        return uid
    used = set()
    for v in m.values():
        try:
            used.add(int(v))
        except (TypeError, ValueError):
            continue
    for uid in range(UID_MIN, UID_MAX + 1):
        if uid not in used:
            m[key] = uid
            tmp = uidmap_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(m, fp, ensure_ascii=False, indent=1, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, uidmap_path)
            return uid
    raise GuardError("배정할 uid가 없다(범위 소진)")


def parse_args(argv):
    """--workspace/--command만 받는다. uid는 인자로 받지 않는다(도출한다)."""
    ws = cmd = None
    it = iter(argv)
    for a in it:
        if a == "--workspace":
            ws = next(it, None)
        elif a == "--command":
            cmd = next(it, None)
        else:
            raise GuardError(f"모르는 인자: {a}")
    if not ws or cmd is None:
        raise GuardError("사용법: organt-sandbox --workspace <경로> --command <명령>")
    return ws, cmd
