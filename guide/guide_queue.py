"""[Discord 이행 M0/M2 — DISCORD_MIGRATION_PLAN_2026-07-03 §2.2]
QueueStore: Discord push(on_message)를 *claim 가능한 영속 저장소*로 물질화하는 매체측 어댑터.
이게 있으면 SYS.run 배달계약 8동사(get_pending·pick claim/done/touch·unpick 정체컷·stops·interjects·heartbeat)가
Discord에서도 성립 — 정체컷·재개·프로세스 사망 인계를 SYS.run에서 얻는다.

*discord-free*: 순수 파일+json+flock. discord 미설치 환경(테스트)에서 import·검증 가능.
SnsGuide payload 필드명(picked·picked_ts·done_ts·stopped·idle_s)과 의도적 동형(파리티).
"""
import fcntl
import json
import os
import time
from contextlib import contextmanager

_RESUME_AFTER = 180.0   # 초 — picked 후 touch 누락 이 시간 넘으면 사망으로 보고 재노출


@contextmanager
def _locked(path):
    """flock — 구·신 프로세스 겹침 창의 이중 claim 방지(§2.2 검증반영)."""
    lock = path + ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class QueueStore:
    """채널=진실원, 스토어=재구축 가능한 claim 캐시(§2.3). 손상 시 자가치유."""

    def __init__(self, path):
        self.path = str(path)

    # ── 영속 ────────────────────────────────────────────────
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("root not object")
        except FileNotFoundError:
            d = {}
        except Exception:
            # [자가치유 §2.2] 손상 파일을 옆으로 치우고 빈 스토어 — 부팅 백필이 재구성
            try:
                os.rename(self.path, f"{self.path}.corrupt-{int(time.time())}")
            except OSError:
                pass
            d = {}
        d.setdefault("requests", {})    # msg_id(str) → record
        d.setdefault("stops", [])       # channel_id 목록(중지)
        d.setdefault("interjects", {})  # channel_id(str) → [텍스트]
        d.setdefault("heartbeat", 0.0)
        return d

    def _save(self, d):
        tmp = f"{self.path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, self.path)   # 원자 rename

    # ── 배달계약: intake·get_pending·pick·done·touch·unpick ──
    def add(self, record):
        """[intake] 새 요청(또는 재개 백필) 물질화. record는 intake()가 만든 dict."""
        with _locked(self.path):
            d = self._load()
            mid = str(record["msg_id"])
            if mid in d["requests"]:
                return False   # 멱등 — dedup(인메모리 seen 대체, 영속)
            rec = dict(record)
            rec.setdefault("payload", {})
            rec["payload"].setdefault("picked", False)
            d["requests"][mid] = rec
            self._save(d)
            return True

    def get_pending(self, resolve_route=None, resume_after=_RESUME_AFTER):
        """미픽 + 정체(picked인데 touch가 resume_after 초과=사망) 재노출.
        route_to는 *픽 시점* resolve_route(channel_id)로 해석(§2.2 역행버그 방지 — 레코드에 안 굳힘)."""
        now = time.time()
        out = []
        with _locked(self.path):
            d = self._load()
            for mid, rec in sorted(d["requests"].items(), key=lambda kv: int(kv[0])):
                p = rec.get("payload", {})
                if p.get("done_ts") or p.get("stopped"):
                    continue
                picked = p.get("picked")
                stale = picked and p.get("picked_ts") and (now - p["picked_ts"]) > resume_after
                if picked and not stale:
                    continue
                r = dict(rec)
                if resolve_route is not None:
                    r["route_to"] = resolve_route(rec["channel_id"])   # 픽 시점 해석
                out.append(r)
        return out

    def pick(self, msg_id, unpick=False, touch=False):
        """원자 claim/재큐/liveness. 반환 {ok, claimed, already_picked}."""
        now = time.time()
        with _locked(self.path):
            d = self._load()
            mid = str(msg_id)
            rec = d["requests"].get(mid)
            if not rec:
                return {"ok": False, "reason": "no such msg"}
            p = rec.setdefault("payload", {})
            if unpick:                     # 정체컷 재큐 — picked 소거
                p["picked"] = False
                p.pop("picked_ts", None)
                self._save(d)
                return {"ok": True, "claimed": False, "unpicked": True}
            if touch:                      # liveness — picked_ts 갱신(8초 간격)
                if p.get("picked"):
                    p["picked_ts"] = now
                    self._save(d)
                return {"ok": True, "touched": True}
            if p.get("picked"):            # 재클레임 패배
                return {"ok": True, "claimed": False, "already_picked": True}
            p["picked"] = True
            p["picked_ts"] = now
            p.setdefault("idle_s", 0)
            self._save(d)
            return {"ok": True, "claimed": True}

    def done(self, msg_id):
        with _locked(self.path):
            d = self._load()
            rec = d["requests"].get(str(msg_id))
            if rec:
                rec.setdefault("payload", {})["done_ts"] = time.time()
                self._save(d)
                return True
            return False

    # ── 병렬 키: 중지·개입·하트비트 ─────────────────────────
    def stop_channel(self, channel_id):
        with _locked(self.path):
            d = self._load()
            if channel_id not in d["stops"]:
                d["stops"].append(channel_id)
            # 해당 채널 미완 요청 stopped 표기
            for rec in d["requests"].values():
                if rec.get("channel_id") == channel_id and not rec.get("payload", {}).get("done_ts"):
                    rec.setdefault("payload", {})["stopped"] = True
            self._save(d)

    def all_stops(self):
        with _locked(self.path):
            return list(self._load()["stops"])

    def add_interject(self, channel_id, text):
        with _locked(self.path):
            d = self._load()
            d["interjects"].setdefault(str(channel_id), []).append(text)
            self._save(d)

    def take_interjects(self, channel_id):
        with _locked(self.path):
            d = self._load()
            got = d["interjects"].pop(str(channel_id), [])
            if got:
                self._save(d)
            return got

    def beat(self):
        with _locked(self.path):
            d = self._load()
            d["heartbeat"] = time.time()
            self._save(d)

    def prune(self, keep_after=3600.0):
        """done 후 유예 지난 레코드·첨부 스테이징 정리(§2.2)."""
        now = time.time()
        removed = []
        with _locked(self.path):
            d = self._load()
            for mid in list(d["requests"]):
                p = d["requests"][mid].get("payload", {})
                if p.get("done_ts") and (now - p["done_ts"]) > keep_after:
                    for a in d["requests"][mid].get("attachments", []):
                        try:
                            os.remove(a)
                        except OSError:
                            pass
                    del d["requests"][mid]
                    removed.append(mid)
            if removed:
                self._save(d)
        return removed
