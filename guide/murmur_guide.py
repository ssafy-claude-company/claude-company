"""MurmurGuide — SnsGuide의 HTTPS 클라이언트판(Phase 2 라이브).

  egress가 HTTPS 전용이라 러너는 원격 SNS DB를 직접 못 만진다. DiscordGuide가 디스코드에 HTTPS로
  말하듯, 이건 *guide_bridge API*로 말한다. SnsGuide와 같은 계약(post/send_request/send_response/
  open_task/update_status/edit_message/read_thread/…)을 그대로 구현 — Sys/Flow에 드롭인 가능.

  무상태 서버를 위해 스레드→채널 매핑·id 생성은 여기(클라)서 쥔다(ORM SnsGuide와 동일 로직).
  동기 requests 호출은 asyncio.to_thread로 감싸 이벤트 루프를 막지 않는다.
"""
import asyncio
import contextvars
import itertools
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import requests

# protocol 객체(Request/Response/Kind) — read_thread 재구성용.
# 기본값 = 이 레포의 부모(멀티레포 루트, 예: /root/murmur-stack) — 하드코딩 경로는 신규
# 체크아웃에서 부재 경로라 protocol import가 침묵 실패(None 열화)하던 문제 교정.
_PJT = os.environ.get("ORGANT_PJT", str(Path(__file__).resolve().parents[1]))
if _PJT not in sys.path:
    sys.path.insert(0, _PJT)
try:
    from system.protocol import Request, Response, Kind  # noqa
except Exception:
    Request = Response = Kind = None

# [동시 흐름 라우팅 안전] 진행 중 요청의 origin 채널을 task-로컬로 — 공유 속성(self._origin_channel)은
# 여러 흐름이 동시에 돌면 서로 덮어써 출력이 엉뚱한 채널로 샌다. contextvar는 asyncio task가 생성 시
# context를 복사하므로 각 동시 흐름이 자기 채널만 본다(시그니처·두뇌 무변경).
ORIGIN_CHANNEL = contextvars.ContextVar("organt_origin_channel", default=None)

def _pipe_payload():
    """[소속 태깅] SYS가 채운 파이프라인 컨텍스트(protocol.PIPELINE_CTX) → 게시 payload."""
    try:
        from system.protocol import PIPELINE_CTX
        ctx = PIPELINE_CTX.get()
        return {"pipeline": dict(ctx)} if ctx else {}
    except Exception:
        return {}


_WS_ROOTS = ("/root/murmur-stack/ops/var/organt_sns_workspace/",
             "/root/ClaudeCompany/ops/var/organt_sns_workspace/")


def strip_server_paths(text) -> str:
    """[사용자 화면에 서버 내부 경로가 뜬다(2026-08-02, 피드 전수 스캔)] 채널 발언 45건(ch267)·8건(ch303)에
    `/root/murmur-stack/ops/var/organt_sns_workspace/p-078-…/scripts/verify.py` 같은 절대경로가 그대로
    실렸다. 링크로 렌더된 것도 있었다(`[verifier](</root/…>)`). 사용자에게는 의미 없는 문자열이고 서버
    구조 노출이다. 봇끼리는 절대경로가 필요하니 **표시 직전에만** 작업공간 뿌리를 떼어 상대경로로 만든다
    — 파일 이름과 위치는 그대로 남아 무엇을 가리키는지는 잃지 않는다."""
    t = str(text or "")
    for root in _WS_ROOTS:
        i = 0
        while True:
            i = t.find(root, i)
            if i < 0:
                break
            j = t.find("/", i + len(root))          # 판 폴더 다음 구분자까지가 뿌리
            if j < 0:
                t = t[:i] + "(작업공간)" + t[i + len(root):]
                break
            t = t[:i] + t[j + 1:]
            i = i
    return t


class MurmurGuide:
    """Rule ↔ 원격 SNS(guide_bridge) 전송기. 러너 프로세스 1개 안에서 Sys/Flow에 주입된다."""

    def __init__(self, base_url, token, timeout=30):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self._ids = itertools.count(int(time.time() * 1000))
        self._thread_channel = {}                          # thread_id → channel_id (클라 보유)
        self._origin_channel = None                        # 이 요청의 채널 — 협업을 여기로 라우팅(러너가 세팅)
        self._last_msg = {}                                # channel_id → 마지막 기록 msg_id — [댓글형 시각화] 관찰([의견])을 직전 결과에 reply_to로 붙이는 근거
        # HTTP는 응답 유실 시 같은 POST를 재전송한다. claim 세대와 unpick 연산 id를 요청 생명주기
        # 동안 보존해, 서버가 이미 적용한 첫 POST를 두 번째 POST가 새 연산으로 오인하지 않게 한다.
        self._claim_tokens = {}                             # msg_id → 현재 claim 세대
        self._unpick_ops = {}                              # msg_id → (claim 세대, 연산 id)

    def _new_id(self):
        return next(self._ids)

    async def set_leader(self, channel_id, bot_id):
        """[리더십 안정] 흐름 리더를 원격 DB Project.leader에 반영 — _route_to bouncing 차단. best-effort."""
        try:
            await self._post("/api/guide/ingest/", {"op": "set_leader",
                                                     "channel_id": int(channel_id), "bot_id": int(bot_id)})
        except Exception:
            pass

    async def create_agent(self, channel_id, role, recruiter=None):
        """[예비 폐지 → recruit genesis] 새 직군 전문가를 원격 생성(예비 dead-end 대체 — 리더가 넘길
        전문가가 없어 교착하던 것 해소). recruiter=채용 요청 봇 id — 신입의 모델·effort를 채용자와
        '같은 선'으로 복사 생성(사용자 규칙 2026-07-08, 상향은 스튜디오 직접 선택만). 신규 bot_id 반환(실패 None)."""
        try:
            r = await self._post("/api/guide/ingest/", {"op": "create_agent",
                                                        "channel_id": int(channel_id), "role": str(role),
                                                        "recruiter": int(recruiter or 0)})
            return (r or {}).get("bot_id")
        except Exception:
            return None

    # [매체 속성] murmur는 채널=프로젝트(create_project_channel이 origin 반환) — SYS가 흐름 시작 시
    # 자동 등록해도 안전(디스코드처럼 새 채널을 만드는 매체는 미선언 → 자동 등록 안 함).
    autoproject = True

    def work_url(self, project_id, channel_id=None):
        """[열리는 주소만 준다(2026-07-31, 사용자 제보 '이거 안 켜지는데')] 종전엔 **러너의 판 id**
        (P-078)를 그대로 끼워 주소를 지어냈다 — murmur는 자기 pid(U-442)로만 찾으므로 무조건 404였고,
        서빙할 산출물이 없어도 링크를 붙였다. 이제 채널로 판을 찾아 매체가 '열린다'(has_work)고
        답할 때만 그 pid로 주소를 만든다(아니면 빈 값 — 보고는 '완성작' 버튼 안내로 떨어진다).
        """
        pub = (os.environ.get("MURMUR_PUBLIC_URL") or "https://murmur.dojin-mini.shop").rstrip("/")
        ch = channel_id if channel_id is not None else getattr(self, "_origin_channel", None)
        if ch is None:
            return ""
        # [죽은 조회였다(2026-08-05, 사용자: '마일스톤 보고에 링크가 보고가 안되는 느낌')] 종전
        # /projects/ 목록 조회는 ①/api 누락(SPA HTML 수신) ②익명이라 비공개 판 미노출 ③anon
        # 스로틀 429 — 셋 다에 걸려 링크가 늘 빈 값으로 떨어졌다. 가이드 토큰 인증 엔드포인트가
        # 서버 지식(앱 풀 정본 우선)으로 직접 답한다.
        try:
            u = str((self._get_sync("/api/guide/work_link/", {"channel": int(ch)}) or {}).get("url") or "")
            return f"{pub}{u}" if u.startswith("/") else u
        except Exception:
            return ""

    def _work_url_legacy(self, project_id):
        """[확인 링크(2026-07-20, 사용자: '마일스톤 끝날 때마다 확인할 수 있는 자료')] 이 판 산출물의
        사용자-열람 주소(완성작 인앱 실행 엔드포인트). 공개 도메인은 env(MURMUR_PUBLIC_URL) 우선 —
        러너의 base(로컬 직결 127.0.0.1)는 사용자용 주소가 아니다. SYS는 duck-typing으로만 묻는다."""
        # [서버 이전(2026-07-28)] 기본값이 구 VPS(murmur-ai.duckdns.org)라 봇 보고의 산출물 링크가
        # 은퇴한 호스트를 가리켰다(사용자 제보). env 미설정 시 현 라이브 도메인을 쓴다.
        pub = (os.environ.get("MURMUR_PUBLIC_URL") or "https://murmur.dojin-mini.shop").rstrip("/")
        return f"{pub}/api/projects/{project_id}/works/"

    def set_origin(self, channel_id):
        """[배달 계약] 이 요청의 origin 채널을 task-로컬로 — 뒤이어 create_task되는 흐름이 이 채널로 라우팅(동시 안전)."""
        ORIGIN_CHANNEL.set(int(channel_id))
        self._origin_channel = int(channel_id)

    # ── HTTP 헬퍼(동기, 재시도) ────────────────────────────────────
    def _post_sync(self, path, payload):
        last = None
        for i in range(3):
            try:
                r = self._s.post(f"{self.base}{path}", json=payload, timeout=self.timeout)
                r.raise_for_status()
                return r.json() if r.content else {}
            except Exception as e:
                last = e
                time.sleep(1.5 * (i + 1))
        raise last

    def _get_sync(self, path, params=None):
        last = None
        for i in range(3):
            try:
                r = self._s.get(f"{self.base}{path}", params=params or {}, timeout=self.timeout)
                r.raise_for_status()
                return r.json() if r.content else {}
            except Exception as e:
                last = e
                time.sleep(1.5 * (i + 1))
        raise last

    async def _post(self, path, payload):
        return await asyncio.to_thread(self._post_sync, path, payload)

    async def _get(self, path, params=None):
        return await asyncio.to_thread(self._get_sync, path, params)

    # ── 채널/스레드 ────────────────────────────────────────────────
    async def create_project_channel(self, guild_id, name):
        # SNS-네이티브: 프로젝트 협업을 '요청이 온 채널'에 그대로 — 디스코드처럼 새 채널을 따로 안 만들고
        # 사용자가 보는 채널에 위임·작업·완료가 라이브로 뜨게 한다. origin 없으면 합성 id(폴백).
        oc = ORIGIN_CHANNEL.get() or self._origin_channel   # task-로컬 우선(동시 흐름 안전), 없으면 구 호환 속성
        if oc:
            return int(oc)
        return self._new_id()

    async def open_task(self, channel_id, status):
        tid = self._new_id()
        self._thread_channel[tid] = int(channel_id)
        if len(self._thread_channel) > 2000:           # 장수 러너 메모리 누수 방지(HANDOFF §10 MED) — 오래된
            self._thread_channel.pop(next(iter(self._thread_channel)))   # 항목 축출(라우팅 폴백 있어 안전)
        res = await self._post("/api/guide/ingest/", {
            "op": "open_task", "channel_id": int(channel_id), "thread_id": int(channel_id),
            "sender_id": 0, "msg_type": "status",
            "body": f"[Task-{getattr(status,'task_id','?')}]",
            "payload": {"task_id": getattr(status, "task_id", None)}})
        return str(res.get("msg_id")), tid

    async def update_status(self, channel_id, status_msg_id, status):
        await self._post("/api/guide/ingest/", {
            "op": "update_status", "status_msg_id": int(status_msg_id),
            "body": f"[Task-{getattr(status,'task_id','?')}] {getattr(status,'state','')}",
            "payload": {"task_id": getattr(status, "task_id", None), "state": getattr(status, "state", None)}})
        return status_msg_id

    # ── 메시지 ─────────────────────────────────────────────────────
    async def post(self, channel_id, sender_id, content, reply_to=None, meta=None):
        # 스레드→채널 해석(send_request/response와 동일) — _say(회의·표결·병렬)가 합성 thread_id로
        # 호출돼도 사용자가 보는 실제 채널에 뜨게 한다. 안 그러면 협업 토의가 유령 채널로 새서
        # 흐름이 '리더 혼자' 중앙집권적으로 보인다. [고스트 라우팅 수정] 재기동으로 _thread_channel이
        # 비거나(맵 유실) 재개가 옛 task-thread를 복원하면 미등록 → 흐름의 ORIGIN_CHANNEL로 라우팅(재기동 생존).
        ch = self._thread_channel.get(int(channel_id)) or ORIGIN_CHANNEL.get() or self._origin_channel or int(channel_id)
        # [종류는 데이터로 실린다(2026-08-07, 사용자: '체계에 맞지 않게 해서 깨트리는 이상한 구조가
        # 나오지 않도록')] 종전엔 라벨이 붙은 자유 텍스트만 보내고 화면이 문자열로 종류를 되짚었다 —
        # 화면이 모르는 라벨은 조용히 사라졌다(실측 7종 전량 증발). 게시 지점에서 한 번 도출해 싣는다.
        from .msgkind import kind_of as _kind_of       # noqa: PLC0415  (guide 패키지 leaf)
        _body = strip_server_paths(content)
        _pl = dict(_pipe_payload() or {})
        _mk = _kind_of(_body)
        if _mk:
            _pl["mk"] = _mk
        if isinstance(meta, dict) and meta:
            _pl.update(meta)          # 구조 필드(meet 제목·결론 등) — 화면이 본문을 파싱하지 않게

        res = await self._post("/api/guide/ingest/", {
            "op": "post", "channel_id": int(ch), "thread_id": int(channel_id),
            "sender_id": int(sender_id or 0), "msg_type": "plain",
            "body": _body,
            "reply_to": (int(reply_to) if reply_to else None), "payload": _pl})
        if int(sender_id or 0) != 0:
            self._track_last(ch, channel_id, res.get("msg_id"))   # 앵커=마지막 '봇' 발화 — SYS 게시([배포 결과] 등)에 의견이 달리는 어색함 방지(사용자)
        return str(res.get("msg_id"))

    async def put_state(self, channel_id, kind, data):
        """[스케일아웃 상태 저장(2026-07-18, HA 설계)] 채널 런타임 상태를 웹 DB로 upsert(HTTP) —
        로컬 파일 미러의 다중머신 대체. data=None이면 삭제. 실패는 무해(파일 폴백이 있음)."""
        try:
            await self._post("/api/guide/ingest/", {
                "op": "put_state", "channel_id": int(channel_id), "kind": str(kind), "data": data})
        except Exception:
            pass

    async def resolve_engine(self, bot_id=None, channel_id=None):
        """[제3자 엔진 2026-07-31, 현준-4] 이 봇을 이 판에서 어떻게 돌리나를 웹에 묻는다.

        러너는 금고 열쇠를 갖지 않는다 - 자격증명은 웹이 열어 이 응답에 한 번 실어 준다.
        실패하면 None이고, 호출자는 None을 '우리 기본으로 간다'로 읽는다. 조용히 남의
        주소로 보내는 것보다 우리 엔진으로 도는 편이 안전하다.
        """
        try:
            return await self._post("/api/guide/ingest/", {
                "op": "engine",
                "bot_id": int(bot_id) if bot_id else None,
                "channel_id": int(channel_id) if channel_id else None,
            })
        except Exception:
            return None

    async def economy(self, channel_id=None):
        """[봇의 경제 조회(2026-08-04, 사용자: '봇이 경제를 활용할 줄 알아야')] 읽기 전용 —
        요율·시장 상위·이 판 주인의 예산. 실패는 None(경제를 몰라도 협업은 계속된다)."""
        try:
            return await self._post("/api/guide/ingest/", {
                "op": "economy",
                "channel_id": int(channel_id) if channel_id else 0})
        except Exception:
            return None

    async def report_usage(self, channel_id, cost_usd, tokens_out,
                           tokens_in=0, tokens_cached=0, purpose="", bot_id=None):
        """[사용량 귀속(2026-07-18, 운영/과금)] 봇 턴 비용을 채널 단위로 웹에 보고(HTTP) → 웹이 보드 주인
        원장에 적립. 실패는 무해(flow.jsonl에 원본 남아 후속 대사 가능). 비용 0이면 스킵.
        [판 크레딧 캡(2026-07-20)] 웹 응답({over, enforce, remaining_credits})을 그대로 반환 —
        호출자(builder)가 한도 초과를 흐름에 전달해 턴 단위 우아한 정지가 가능해진다."""
        if not cost_usd:
            return None
        try:
            return await self._post("/api/guide/ingest/", {
                "op": "usage", "channel_id": int(channel_id),
                "cost_usd": float(cost_usd), "tokens_out": int(tokens_out or 0),
                # [원장 셋째 축 2026-07-31] 누구 엔진으로 돌았나 - 봇에 붙은 실행 설정을
                # 보려면 봇 id가 여기까지 와야 한다. 없으면 전역 기본으로 적힌다.
                "bot_id": int(bot_id) if bot_id else None,
                # [원가 지도(2026-07-30)] 입력·캐시·목적까지 보내 원장이 "어디서 나갔는가"를 답하게.
                "tokens_in": int(tokens_in or 0), "tokens_cached": int(tokens_cached or 0),
                "purpose": str(purpose or "")[:24]})
        except Exception:
            return None

    def get_state_sync(self, channel_id, kind):
        """[부팅 복원용 — sync(이벤트루프 전)] 공유 DB에서 채널 상태를 되읽는다. 없으면/실패면 None
        (호출부가 파일 폴백). 러너 부팅은 async 루프 전이라 sync HTTP를 쓴다.
        단발·짧은 타임아웃(5s) — _get_sync의 3회×30s 재시도를 타면 '웹이 죽어 있는 페일오버 부팅'이
        최악 ~99초 정지한다(2026-07-18 검수). 부팅 복원은 즉시 파일 폴백이 맞다."""
        try:
            r = self._s.get(f"{self.base}/api/guide/state/",
                            params={"channel_id": int(channel_id), "kind": str(kind)}, timeout=5)
            r.raise_for_status()
            return ((r.json() if r.content else {}) or {}).get("data")
        except Exception:
            return None

    def _track_last(self, ch, thread_id, msg_id):
        """[댓글형 시각화] 채널·스레드별 마지막 기록 msg_id — 관찰([의견])을 직전 결과에 붙일 근거."""
        try:
            if msg_id:
                self._last_msg[int(ch)] = int(msg_id)
                self._last_msg[int(thread_id)] = int(msg_id)
        except (TypeError, ValueError):
            pass

    def last_message_id(self, channel_id):
        """이 Guide가 그 채널/스레드에 마지막으로 기록한 msg_id(없으면 None) — reply_to 대상."""
        return self._last_msg.get(int(channel_id))

    async def send_request(self, thread_id, sender_id, to_id, kind, body):
        ch = self._thread_channel.get(int(thread_id)) or ORIGIN_CHANNEL.get() or self._origin_channel or int(thread_id)
        k = "W" if (str(getattr(kind, "value", kind)).lower().startswith("w")) else "I"
        res = await self._post("/api/guide/ingest/", {
            "op": "send_request", "channel_id": int(ch), "thread_id": int(thread_id),
            "sender_id": int(sender_id), "msg_type": "request",
            "to_id": (int(to_id) if to_id else None), "kind": k,
            "body": strip_server_paths(body), "payload": _pipe_payload()})
        self._track_last(ch, thread_id, res.get("msg_id"))
        return str(res.get("msg_id"))

    async def send_response(self, thread_id, sender_id, request_msg_id, body):
        ch = self._thread_channel.get(int(thread_id)) or ORIGIN_CHANNEL.get() or self._origin_channel or int(thread_id)
        res = await self._post("/api/guide/ingest/", {
            "op": "send_response", "channel_id": int(ch), "thread_id": int(thread_id),
            "sender_id": int(sender_id), "msg_type": "response",
            "reply_to": (int(request_msg_id) if request_msg_id else None),
            "body": strip_server_paths(body), "payload": _pipe_payload()})
        self._track_last(ch, thread_id, res.get("msg_id"))
        return str(res.get("msg_id"))

    async def read_thread(self, thread_id, limit=50, include_plain=False):
        data = await self._get("/api/guide/thread/", {"thread_id": int(thread_id), "limit": limit})
        out = []
        for m in data.get("rows", []):
            if m["msg_type"] == "request":
                out.append(Request(to_id=m["to_id"], kind=(Kind.WORK if m["kind"] == "W" else Kind.INFO),
                                   body=m["body"], from_id=m["sender_id"], message_id=str(m["msg_id"])))
            elif m["msg_type"] == "response":
                out.append(Response(from_id=m["sender_id"], body=m["body"],
                                    replies_to=str(m["reply_to"]) if m["reply_to"] else None,
                                    message_id=str(m["msg_id"])))
            elif m["msg_type"] == "plain" and include_plain and (m["body"] or "").strip():
                out.append(Request(to_id=None, kind=Kind.WORK, body=m["body"].strip(),
                                   from_id=m["sender_id"], message_id=str(m["msg_id"])))
        return out

    async def edit_message(self, channel_id, message_id, content):
        await self._post("/api/guide/ingest/", {
            "op": "edit_message", "message_id": int(message_id), "body": strip_server_paths(content)})

    async def post_document(self, channel_id, sender_id, title, body):
        """[B-12 — Guide 선택 메서드] 회의 발언 *전문*을 murmur Document로 저장 → 열람 ref 반환.
        전용 엔드포인트(/api/guide/document/) — 아직 이 op를 모르는 구 배포 백엔드에선 404가 나
        None을 돌려주고, 호출부(_say_speech)가 clip 폴백한다(전문이 plain 메시지로 오기록되지 않게
        ingest op에 태우지 않음). 실패는 전부 None(무중단)."""
        ch = self._thread_channel.get(int(channel_id)) or ORIGIN_CHANNEL.get() \
            or self._origin_channel or int(channel_id)
        try:
            res = await self._post("/api/guide/document/", {
                "channel_id": int(ch), "sender_id": int(sender_id or 0),
                "title": str(title or "")[:200], "body": strip_server_paths(body)})
        except Exception:
            return None
        ref = (res or {}).get("ref")
        return str(ref) if ref else None

    # ── 정체성/직군 — SNS 로스터는 스튜디오가 관리. 러너는 건드리지 않음(best-effort no-op) ──
    async def assign_job_role(self, guild_id, user_id, job_name): return True
    async def assign_job_roles(self, guild_id, id_to_job): return len(id_to_job or {})

    # ── 디스코드 전용 — 안전 no-op ─────────────────────────────────
    def register_organt(self, user_id, client=None): pass

    @asynccontextmanager
    async def typing(self, channel_id, sender_id=None):
        # DiscordGuide.typing과 같은 계약: async with로 쓰는 컨텍스트 매니저(SNS엔 타이핑 표시 없음 → no-op).
        yield

    async def send_file(self, channel_id, path, sender_id=0, caption=""):
        """[파일 공유 실구현(2026-07-08)] murmur에선 업로드가 아니라 **경로 메시지** — 파일은 이미
        워크스페이스에 있고 웹이 /projects/<pid>/file/?path=로 서빙한다. 종전 no-op 스텁이라 봇의
        이미지·산출물 공유가 조용히 증발했었다(사용자 관측)."""
        import os as _os
        rel = _os.path.basename(str(path)) if _os.path.isabs(str(path)) else str(path)
        # 워크스페이스 절대경로가 오면 상대화 시도(작업공간 밖이면 이름만)
        try:
            _ws = None
            for _cand in (getattr(self, "_workspace_hint", None),):
                pass
        except Exception:
            pass
        if _os.path.isabs(str(path)):
            # 절대경로 → 'workspace/' 뒤 상대부만 추출(러너 규약: 작업공간 내 파일만 전송됨)
            parts = str(path).split("/organt_sns_workspace/", 1)
            if len(parts) == 2 and "/" in parts[1]:
                rel = parts[1].split("/", 1)[1]
        ch = self._thread_channel.get(int(channel_id)) or ORIGIN_CHANNEL.get() or self._origin_channel or int(channel_id)
        res = await self._post("/api/guide/ingest/", {
            "op": "post", "channel_id": int(ch), "thread_id": int(channel_id),
            "sender_id": int(sender_id or 0), "msg_type": "plain",
            "body": f"[파일] {rel}" + (f" — {caption}" if caption else ""),
            "payload": {"file": rel}})
        if int(sender_id or 0) != 0:
            self._track_last(ch, channel_id, res.get("msg_id"))
        return str(res.get("msg_id"))
    async def react(self, channel_id, message_id, emoji): return None
    async def delete_message(self, channel_id, message_id): return None
    async def hide_channel(self, guild_id, channel_id): return None
    async def set_channel_topic(self, channel_id, topic): return True
    async def get_channel_topics(self, guild_id): return {}
    async def set_nick(self, guild_id, user_id, nick): return True
    async def set_nicks(self, guild_id, id_to_nick): return len(id_to_nick or {})
    async def get_member_jobs(self, guild_id, user_ids): return {}
    async def get_member_nicks(self, guild_id, user_ids): return {}
    async def get_custom_role_names(self, guild_id): return []
    async def get_guild_bot_nicks(self, guild_id): return None
    async def not_in_guild(self, guild_id, user_ids): return []

    async def deploy_creds(self, channel_id):
        """배포 자격증명(BYO) — 채널 프로젝트 소유자 금고에서. 브리지가 서버 내부에서만 복호화해 내려준다."""
        if not channel_id:
            return {}
        try:
            res = await self._get("/api/guide/deploy_creds/", {"channel": int(channel_id)})
        except Exception:
            return {}
        return (res or {}).get("creds", {}) or {}

    # ── [배달 계약 구현 — SYS.run이 쓰는 추상 Guide 계약의 HTTP 구현체] ─────────────
    # 수신·claim·진행표시·완료·재개·살아있음. SYS(추상)는 이 메서드들만 알고, murmur API로의
    # 실제 전송은 여기(구현체)가 담당(guide_bridge). ORM판(SnsGuide)은 같은 계약을 DB로 구현.
    async def get_pending(self):
        """미처리 요청 목록(claim 대기). 매체 수신의 HTTP 구현."""
        data = await self._get("/api/guide/pending/")
        return data.get("pending", [])

    async def pick(self, msg_id, done=False, touch=False, unpick=False, idle=None,
                   activity=None, actor=None, start_retry=False):
        """요청 claim/진행표시(touch)/완료(done)/재개(unpick). claim 패배 시 False.
        activity: '지금 하는 일' 최근 줄 목록. actor: 지금 베턴 쥔 봇 id — live_status.actor를 현재 일꾼으로."""
        mid = int(msg_id)
        is_claim = not (unpick or done or touch)
        body = {"msg_id": msg_id, "done": done, "touch": touch, "unpick": unpick}
        if is_claim:
            # 같은 호출 안의 _post_sync 재전송뿐 아니라, 세 번 모두 응답만 유실돼 상위 폴이 다시
            # claim하는 경우에도 같은 세대를 재사용한다. 확정 패배/종결 때만 아래에서 버린다.
            claim_token = self._claim_tokens.get(mid) or uuid.uuid4().hex
            self._claim_tokens[mid] = claim_token
            self._unpick_ops.pop(mid, None)
            body["claim_token"] = claim_token
        else:
            claim_token = self._claim_tokens.get(mid)
            if claim_token:
                body["claim_token"] = claim_token
            if unpick:
                prior = self._unpick_ops.get(mid)
                if prior is None or prior[0] != claim_token:
                    prior = (claim_token, uuid.uuid4().hex)
                    self._unpick_ops[mid] = prior
                body["op_id"] = prior[1]
        if start_retry:
            body["start_retry"] = True
        if idle is not None:
            body["idle"] = int(idle)
        if activity is not None:
            body["activity"] = [str(x)[:120] for x in activity][-2000:] if isinstance(activity, (list, tuple)) else [str(activity)[:120]]
        if actor is not None:
            body["actor"] = int(actor)
        res = await self._post("/api/guide/pick/", body)
        out = res or {}
        claimed = out.get("claimed", True)
        if is_claim:
            if claimed is False:
                # 다른 세대가 이미 선점했거나 terminal이다. 이 클라이언트의 후보 토큰을 남기면
                # 뒤의 touch/unpick이 타 세대를 겨냥할 수 있으므로 확정 응답에서만 정리한다.
                if self._claim_tokens.get(mid) == claim_token:
                    self._claim_tokens.pop(mid, None)
            return claimed
        if out.get("stale_generation") or out.get("stopped"):
            return False
        if unpick and out.get("ok", True):
            self._unpick_ops.pop(mid, None)
            if self._claim_tokens.get(mid) == claim_token:
                self._claim_tokens.pop(mid, None)
        elif done and out.get("ok", True):
            if self._claim_tokens.get(mid) == claim_token:
                self._claim_tokens.pop(mid, None)
        return bool(out.get("ok", True))

    async def mark_continuation(self, msg_id, task_id=""):
        """이 요청은 기존 Task의 이어가기임을 매체에 남긴다(화면이 새 Task 카드로 그리지 않게)."""
        try:
            await self._post("/api/guide/ingest/", {"op": "mark_continuation",
                                                    "msg_id": int(msg_id), "task": str(task_id or "")})
        except Exception:
            pass

    async def channel_roster(self, channel_id):
        """[내 직원만(2026-07-28)] 이 채널이 부를 수 있는 직원 {bot_id: 직군} — 채널 주인의 소유·추가
        승인분(+채용 직군). 매체가 판정하고 SYS는 받은 대로 쓴다(매체-특화는 Guide 안에서)."""
        try:
            d = await self._get("/api/guide/roster/", {"channel": int(channel_id)})
        except Exception:
            return {}
        out = {}
        for a in (d or {}).get("agents") or []:
            try:
                out[int(a["bot_id"])] = a.get("role") or "예비"
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def heartbeat(self, note="remote"):
        """엔진 살아있음 신호 — 매체(murmur)에 전송."""
        await self._post("/api/guide/heartbeat/", {"note": note})

    async def check_stop(self, channel_id):
        d = await self._get(f"/api/guide/stops/?channel={channel_id}")
        return bool(d.get("stopped"))

    async def all_stops(self):
        d = await self._get("/api/guide/stops/")
        signals = d.get("signals")
        if isinstance(signals, list):
            return signals
        return [int(c) for c in d.get("channels", [])]

    async def ack_stop(self, channel_id, signal_id=None, requested_at=None):
        """이미 웹이 terminal로 쓴 사용자 stop 신호만 확인한다.

        채널 전체 요청을 다시 쓰지 않아 stop 직후 사용자가 재개한 세대를 늦은 러너 ack가
        재중지하지 않는다.
        """
        body = {"channel": int(channel_id), "ack_only": True}
        if signal_id is not None:
            body["signal_id"] = int(signal_id)
        if requested_at is not None:
            body["requested_at"] = float(requested_at)
        await self._post("/api/guide/stop_channel/", body)

    async def mark_stopped(self, channel_id, msg_id=None):
        body = {"channel": int(channel_id)}
        if msg_id is not None:
            body["msg_id"] = int(msg_id)
        res = await self._post("/api/guide/stop_channel/", body)
        return int((res or {}).get("stopped") or 0)

    async def check_interject(self, channel_id):
        d = await self._get(f"/api/guide/interjects/?channel={channel_id}")
        return d.get("infos", [])

    async def set_identity(self, bot_id, name, persona):
        """[채용 제네시스] 리크루터가 빚은 신규 봇 이름·persona를 웹(Agent DB)에 반영. 빈 필드만
        채움(브리지가 사용자 편집 보존). 실패해도 흐름 무해(정체성 미반영이 온보딩을 막지 않음)."""
        try:
            await self._post("/api/guide/agent_identity/",
                             {"bot_id": int(bot_id), "name": name or "", "persona": persona or ""})
        except Exception:
            pass

    async def set_craft(self, bot_id, craft, distilled=False):
        """[봇별 완전 격리] 이 봇 '개인'의 직무 기준을 웹(Agent.craft)에 동기 — UI가 직군 공용 대신
        개인 노하우를 보이게. 시스템 소유 필드라 항상 덮어씀. 실패해도 흐름 무해(표시용 미러)."""
        try:
            await self._post("/api/guide/agent_craft/",
                             {"bot_id": int(bot_id), "craft": craft or "", "distilled": bool(distilled)})
        except Exception:
            pass

    @staticmethod
    def invite_url(app_id, perms=None):
        return f"(SNS 봇 #{app_id} — 초대 불필요)"
