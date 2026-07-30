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
        validated_workspace(str(outside), root)


def test_심링크로_뿌리를_벗어나면_거부한다(tmp_path):
    """realpath로 판정한다 — 뿌리 안에 심링크를 놓고 밖을 가리키는 우회를 막는다."""
    root = _root(tmp_path)
    secret = tmp_path / "secret"
    secret.mkdir()
    link = os.path.join(root, "escape")
    os.symlink(str(secret), link)
    with pytest.raises(GuardError):
        validated_workspace(link, root)


def test_상대경로는_거부한다(tmp_path):
    with pytest.raises(GuardError):
        validated_workspace("ops/var/x", _root(tmp_path))


def test_없는_디렉터리는_거부한다(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(GuardError):
        validated_workspace(os.path.join(root, "nope"), root)


def test_뿌리_자체는_판이_아니다(tmp_path):
    """뿌리에서 돌면 전 판이 한 uid를 공유해 경계가 사라진다."""
    root = _root(tmp_path)
    real, r = validated_workspace(root, root)
    with pytest.raises(GuardError):
        project_key(real, r)


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
    real, r = validated_workspace(proj, root)
    assert project_key(real, r) == "p-001-작품"
    real2, r2 = validated_workspace(os.path.join(proj, "src"), root)
    assert project_key(real2, r2) == "p-001-작품"   # 하위에서 불려도 같은 판


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
