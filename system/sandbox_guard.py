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


def validated_workspace(raw, ws_roots):
    """작업공간을 검증해 (실경로, 맞은 뿌리, 뿌리종류)를 준다. 허용 뿌리 밖·비디렉터리는 거부.

    ws_roots는 (경로, 종류) 목록이다. 종류를 둘로 나눈 이유는 실측에서 나왔다.
      "parent" — 그 밑 첫 단계 폴더가 각각 하나의 판이다(판별 작업공간 뿌리)
      "leaf"   — 그 폴더 자체가 하나의 단위다(증류 워커의 격리 빈 cwd 같은 고정 경로)

    leaf를 두지 않으면 그런 흐름이 '뿌리 자체'로 거부돼 조용히 실패한다 — 봇은 run이 막힌 것을
    오류로만 보고 원인을 모른 채 약한 명령으로 우회한다(가장 나쁜 실패 형태).
    """
    if not raw or not str(raw).startswith("/"):
        raise GuardError("작업공간은 절대경로여야 한다")
    real = os.path.realpath(str(raw))
    for root_raw, kind in ws_roots:
        root = os.path.realpath(str(root_raw))
        if real == root or real.startswith(root + os.sep):
            if not os.path.isdir(real):
                raise GuardError("작업공간이 디렉터리가 아니다")
            return real, root, kind
    raise GuardError(f"작업공간이 허용 뿌리 밖이다: {real}")


def project_key(real, root, kind="parent"):
    """이 작업공간이 속한 단위의 이름. 같은 단위는 항상 같은 uid를 받는다.

    parent 뿌리에서는 첫 단계 폴더가 단위다(하위에서 불려도 같은 판으로 묶는다). 뿌리 자체는
    단위가 아니다 — 거기서 돌면 전 판이 한 uid를 공유해 경계가 사라진다.
    leaf 뿌리에서는 그 폴더 자체가 단위다(판이 아니므로 이름 앞에 _를 붙여 판과 섞이지 않게)."""
    if kind == "leaf":
        return "_" + os.path.basename(root.rstrip(os.sep))
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
