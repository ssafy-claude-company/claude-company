"""[Discord 이행 M0/M2] QueueStore·intake 계약 검증 — discord-free(토큰·discord.py 불필요)."""
import time
from guide.guide_queue import QueueStore
from guide.intake import intake


def _store(tmp_path):
    return QueueStore(tmp_path / "q.json")


def test_intake_정규화_첨부스테이징(tmp_path):
    r = intake(5, 10, 0, "auto-W", "카운터 앱", attachments=[("a.png", b"PNG")], staging_dir=str(tmp_path / "st"))
    assert r["msg_id"] == 5 and r["channel_id"] == 10 and r["kind"] == "W"
    assert len(r["attachments"]) == 1
    assert open(r["attachments"][0], "rb").read() == b"PNG"
    assert intake(6, 10, 0, "I", "질문")["kind"] == "I"   # Info


def test_add_멱등_dedup(tmp_path):
    q = _store(tmp_path)
    assert q.add(intake(1, 10, 0, "W", "x")) is True
    assert q.add(intake(1, 10, 0, "W", "x")) is False   # 같은 msg_id 재삽입 거부


def test_pending_pick_원자claim_재클레임패배(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x"))
    assert [r["msg_id"] for r in q.get_pending()] == [1]
    assert q.pick(1)["claimed"] is True
    assert q.pick(1)["already_picked"] is True   # 이중 claim 패배
    assert q.get_pending() == []                 # picked라 미노출
    assert q.done(1) is True
    assert q.get_pending() == []                 # done 제외


def test_stale_재노출_touch_로_방지(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x"))
    q.pick(1)
    # picked_ts를 과거로 강제(사망 시뮬)
    d = q._load(); d["requests"]["1"]["payload"]["picked_ts"] = time.time() - 500; q._save(d)
    assert [r["msg_id"] for r in q.get_pending(resume_after=180)] == [1]   # 정체 재노출
    q.pick(1, touch=True)                                                   # liveness 갱신
    assert q.get_pending(resume_after=180) == []                           # 다시 살아있음


def test_unpick_정체컷_재큐(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x")); q.pick(1)
    q.pick(1, unpick=True)
    assert [r["msg_id"] for r in q.get_pending()] == [1]   # 재노출
    assert q.pick(1)["claimed"] is True                    # 재claim 가능


def test_route_resolver_픽시점_해석(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x"))
    r = q.get_pending(resolve_route=lambda ch: 999 if ch == 10 else None)
    assert r[0]["route_to"] == 999   # 레코드에 안 굳히고 픽 시점 콜백으로


def test_stop_interject_heartbeat(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x"))
    q.stop_channel(10)
    assert 10 in q.all_stops()
    assert q.get_pending() == []          # stopped 제외
    q.add_interject(10, "이거 먼저 해")
    assert q.take_interjects(10) == ["이거 먼저 해"]
    assert q.take_interjects(10) == []    # 소거됨
    q.beat(); assert q._load()["heartbeat"] > 0


def test_자가치유_손상파일(tmp_path):
    p = tmp_path / "q.json"
    p.write_text("{corrupt not json")
    q = QueueStore(p)
    assert q.add(intake(1, 10, 0, "W", "x")) is True   # 손상 → 빈 스토어로 자가치유 후 정상
    assert [r["msg_id"] for r in q.get_pending()] == [1]


def test_prune_done_후_정리(tmp_path):
    q = _store(tmp_path)
    q.add(intake(1, 10, 0, "W", "x")); q.pick(1); q.done(1)
    d = q._load(); d["requests"]["1"]["payload"]["done_ts"] = time.time() - 5000; q._save(d)
    assert q.prune(keep_after=3600) == ["1"]
    assert q._load()["requests"] == {}
