"""판별 격리 도우미의 검증부 — 여기가 신뢰 경계라 통과·거부를 자로 잡는다(2026-07-30).

호출자(러너)는 비특권이고 침해됐을 수 있다고 가정한다. 그래서 '거부해야 하는 것'을 먼저 본다.
"""
import json
import os

import pytest

from system.sandbox_guard import (UID_MAX, UID_MIN, GuardError, parse_args,
                                  project_key, uid_for, validated_workspace)


def _root(tmp_path):
    r = tmp_path / "ws"
    r.mkdir()
    return str(r)


# ── 거부해야 하는 것 ──────────────────────────────────────────────────────
def test_뿌리_밖_경로는_거부한다(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GuardError):
        validated_workspace(str(outside), [(root, "parent")])


def test_심링크로_뿌리를_벗어나면_거부한다(tmp_path):
    """realpath로 판정한다 — 뿌리 안에 심링크를 놓고 밖을 가리키는 우회를 막는다."""
    root = _root(tmp_path)
    secret = tmp_path / "secret"
    secret.mkdir()
    link = os.path.join(root, "escape")
    os.symlink(str(secret), link)
    with pytest.raises(GuardError):
        validated_workspace(link, [(root, "parent")])


def test_상대경로는_거부한다(tmp_path):
    with pytest.raises(GuardError):
        validated_workspace("ops/var/x", [(_root(tmp_path), "parent")])


def test_없는_디렉터리는_거부한다(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(GuardError):
        validated_workspace(os.path.join(root, "nope"), [(root, "parent")])


def test_뿌리_자체는_판이_아니다(tmp_path):
    """뿌리에서 돌면 전 판이 한 uid를 공유해 경계가 사라진다."""
    root = _root(tmp_path)
    real, r, k = validated_workspace(root, [(root, "parent")])
    with pytest.raises(GuardError):
        project_key(real, r, k)


def test_uid는_인자로_받지_않는다(tmp_path):
    """호출자가 uid를 고르면 판 A의 트리를 판 B의 uid로 넘길 수 있다."""
    with pytest.raises(GuardError):
        parse_args(["--workspace", "/x", "--command", "ls", "--uid", "0"])


def test_모르는_인자는_거부한다():
    with pytest.raises(GuardError):
        parse_args(["--workspace", "/x", "--command", "ls", "--extra"])


def test_인자가_모자라면_거부한다():
    with pytest.raises(GuardError):
        parse_args(["--workspace", "/x"])


# ── 통과해야 하는 것 ──────────────────────────────────────────────────────
def test_판_폴더는_통과하고_하위폴더도_같은_판이다(tmp_path):
    root = _root(tmp_path)
    proj = os.path.join(root, "p-001-작품")
    os.makedirs(os.path.join(proj, "src"))
    real, r, k = validated_workspace(proj, [(root, "parent")])
    assert project_key(real, r, k) == "p-001-작품"
    real2, r2, k2 = validated_workspace(os.path.join(proj, "src"), [(root, "parent")])
    assert project_key(real2, r2, k2) == "p-001-작품"   # 하위에서 불려도 같은 판


def test_판마다_다른_uid가_배정된다(tmp_path):
    m = str(tmp_path / "uidmap.json")
    a, b = uid_for("p-001", m), uid_for("p-002", m)
    assert a != b
    assert UID_MIN <= a <= UID_MAX and UID_MIN <= b <= UID_MAX


def test_같은_판은_항상_같은_uid다(tmp_path):
    """판이 다시 불릴 때 uid가 바뀌면 그 판의 파일을 자기가 못 읽는다."""
    m = str(tmp_path / "uidmap.json")
    assert uid_for("p-001", m) == uid_for("p-001", m)


def test_uid는_0이_될_수_없다(tmp_path):
    m = str(tmp_path / "uidmap.json")
    assert uid_for("p-001", m) > 0


def test_대장이_깨져_있으면_새로_시작한다(tmp_path):
    """대장이 손상돼도 도우미가 죽지 않는다 — 다만 배정은 다시 시작된다."""
    m = str(tmp_path / "uidmap.json")
    with open(m, "w", encoding="utf-8") as fp:
        fp.write("{깨진 json")
    assert UID_MIN <= uid_for("p-001", m) <= UID_MAX


def test_대장의_uid가_범위_밖이면_거부한다(tmp_path):
    """대장이 조작돼 root(0)를 가리키면 실행하지 않는다."""
    m = str(tmp_path / "uidmap.json")
    with open(m, "w", encoding="utf-8") as fp:
        json.dump({"p-001": 0}, fp)
    with pytest.raises(GuardError):
        uid_for("p-001", m)


def test_대장은_소유자만_읽게_저장된다(tmp_path):
    m = str(tmp_path / "uidmap.json")
    uid_for("p-001", m)
    assert oct(os.stat(m).st_mode & 0o777) == "0o600"


# ── leaf 뿌리 (증류·수면 흐름의 고정 빈 cwd) ─────────────────────────────
# 실측에서 나온 필요다: sys_core._distill_workspace가 판 폴더가 아닌 고정 폴더를 cwd로 준다.
# leaf를 안 두면 그 흐름의 run이 '뿌리 밖'으로 거부돼 조용히 실패한다 — 봇은 원인을 모른다.
def test_leaf_뿌리는_그_폴더_자체가_한_단위다(tmp_path):
    leaf = tmp_path / "state" / ".distill_cwd"
    leaf.mkdir(parents=True)
    real, root, kind = validated_workspace(str(leaf), [(str(leaf), "leaf")])
    assert kind == "leaf"
    assert project_key(real, root, kind) == "_.distill_cwd"   # 뿌리 자체여도 거부되지 않는다


def test_leaf_단위는_판과_다른_uid를_받는다(tmp_path):
    """증류 cwd가 어느 판의 uid도 물려받지 않는다."""
    m = str(tmp_path / "uidmap.json")
    assert uid_for("_.distill_cwd", m) != uid_for("p-001", m)


def test_여러_뿌리_중_맞는_것을_고른다(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    proj = ws / "p-001"; proj.mkdir()
    leaf = tmp_path / ".distill_cwd"; leaf.mkdir()
    roots = [(str(ws), "parent"), (str(leaf), "leaf")]
    r1, root1, k1 = validated_workspace(str(proj), roots)
    assert (k1, project_key(r1, root1, k1)) == ("parent", "p-001")
    r2, root2, k2 = validated_workspace(str(leaf), roots)
    assert (k2, project_key(r2, root2, k2)) == ("leaf", "_.distill_cwd")


def test_어느_뿌리에도_없으면_여전히_거부한다(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    leaf = tmp_path / ".distill_cwd"; leaf.mkdir()
    other = tmp_path / "other"; other.mkdir()
    with pytest.raises(GuardError):
        validated_workspace(str(other), [(str(ws), "parent"), (str(leaf), "leaf")])


def test_동시등록이_서로의_항목을_덮지_않는다(tmp_path):
    """[경합 결함 수선] 잠금이 없으면 둘 다 같은 빈 uid를 골라 하나가 사라진다.

    실측 근거: p-078이 파일 29,828개를 300002로 소유하는데 대장에 항목이 없었다.
    """
    import concurrent.futures as cf
    import json
    from system.sandbox_guard import uid_for

    m = str(tmp_path / "uidmap.json")
    keys = [f"p-{i:03d}" for i in range(24)]
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        uids = list(ex.map(lambda k: uid_for(k, m), keys))

    got = json.load(open(m, encoding="utf-8"))
    assert set(got) == set(keys), "잃어버린 항목이 있다"
    assert len(set(uids)) == len(keys), "같은 uid가 두 판에 배정됐다"
    assert sorted(got.values()) == sorted(uids)


def test_대장이_항목을_잃어도_쓰던_uid를_되찾는다(tmp_path):
    """디스크가 사실을 안다 - 살아 있는 uid를 다른 판에 다시 주지 않는다."""
    import json
    from system.sandbox_guard import UID_MIN, uid_for

    m = str(tmp_path / "uidmap.json")
    json.dump({"다른판": UID_MIN}, open(m, "w", encoding="utf-8"))
    got = uid_for("잃어버린판", m, observed_uid=UID_MIN + 2)
    assert got == UID_MIN + 2                       # 가장 낮은 빈 uid(+1)가 아니라 쓰던 것
    assert json.load(open(m, encoding="utf-8"))["잃어버린판"] == UID_MIN + 2


def test_남이_쓰는_uid는_되찾지_않는다(tmp_path):
    """소유자가 이미 대장에 잡힌 uid면 회수하지 않는다 - 두 판이 겹치면 격리가 뚫린다."""
    import json
    from system.sandbox_guard import UID_MIN, uid_for

    m = str(tmp_path / "uidmap.json")
    json.dump({"다른판": UID_MIN}, open(m, "w", encoding="utf-8"))
    got = uid_for("새판", m, observed_uid=UID_MIN)   # 남이 쓰는 uid를 들고 왔다
    assert got != UID_MIN


def test_범위_밖_소유자는_무시한다(tmp_path):
    """65534(nobody) 같은 옛 산출물 소유자를 판별 uid로 삼지 않는다."""
    from system.sandbox_guard import UID_MIN, uid_for

    m = str(tmp_path / "uidmap.json")
    assert uid_for("판", m, observed_uid=65534) == UID_MIN
