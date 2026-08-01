"""Organt(LLM) 본체.

claude-agent-sdk의 ClaudeSDKClient로 Organt를 구동한다.
작업공간(cwd) 안에서 내장 파일 툴(Read/Write/Edit)로 파일을 다룬다.

기능4 범위: Organt 본체 구성 + 파일시스템 접근.
- 인격(CLAUDE.md)·세션 보존(resume)은 Step2,
- Discord 소통 툴은 기능5, audit 훅은 기능6에서 옵션 override로 붙인다.
"""
import asyncio
import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from system.config import ROOT, Config
from . import botpool

# Organt 기본 인격(폴백). CLAUDE.md가 없거나 비면 이걸 system_prompt로 쓴다.
ORGANT_PERSONA = (
    "당신은 Discord 위에서 일하는 AI 직원 'Organt'입니다. 동료와 함께 한 회사처럼 일합니다. "
    "현재 작업 디렉터리 안에서 '상대경로'로만 파일을 만들고 수정합니다(작업공간 밖 접근 금지). "
    "짧은 요청이라도 목표는 '문자 그대로 최소'가 아니라 가용 자원으로 낼 수 있는 '최대' 품질입니다 — "
    "명시 안 됐어도 그 종류 산출물이 당연히 갖출 것까지 채우고(최소판 금지), 모르면 상상 말고 WebSearch로 "
    "조사하며, 추측보다 run·소통으로 검증하세요. 실작업은 request로 그 도메인 전문가 동료에게 맡기고, "
    "run으로 검증하고, deploy로 배포합니다."
)

# Organt 인격 파일(CLAUDE.md) 경로. 인격·기억·Rule/Guide 목록을 담는다.
PERSONA_PATH = ROOT / "organt" / "CLAUDE.md"


def load_persona(path=None) -> str:
    """Organt 인격(CLAUDE.md)을 읽어 system_prompt로 쓴다. 없거나 비면 기본 인격."""
    p = Path(path) if path is not None else PERSONA_PATH
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        return ORGANT_PERSONA
    return text or ORGANT_PERSONA


_MAX_API_RETRY = 3   # 일시적 API 오류(과부하 등) 재시도 횟수


def _is_stale_session_error(text: str) -> bool:
    """resume 대상 세션이 CLI 저장소에 없다(cwd 불일치·저장소 유실) — 일시 오류와 달리 재시도가
    무의미하며, 세션을 버리고 새로 시작하는 것이 유일한 전진이다."""
    return "no conversation found" in (text or "").lower()


def pinned_cwd(state_path) -> Optional[str]:
    """이 상태 파일의 세션이 '시작된' cwd(있고, 디렉터리가 살아 있으면). CLI 세션 저장소는 cwd
    기준이라 같은 세션을 resume하는 빌드는 이 cwd를 그대로 써야 찾는다 — 흐름 도중 작업공간이
    바뀌어도(create_project 카빙) 세션 연속성이 깨지지 않게 하는 고정점."""
    try:
        d = json.loads(Path(state_path).read_text(encoding="utf-8"))
        c = str(d.get("cwd") or "")
        if d.get("session_id") and c and os.path.isdir(c):
            return c
    except (OSError, ValueError):
        pass
    return None


def _is_transient_api_error(text: str) -> bool:
    """응답이 일시적 오류(429/5xx/529 과부하·rate limit, 또는 SDK 서브프로세스 사망=SIGTERM/143·
    파이프 끊김·메시지리더 크래시·**제어 스트림 닫힘(Stream closed)**)로 보이는지 — resume 재시도 대상.
    빈 응답('')도 '서브프로세스가 발화 없이 조용히 종료'한 신호라 재시도 대상으로 본다(호출부에서 처리)."""
    t = (text or "").strip().lower()
    if not t.startswith("api error"):
        return False
    # [과민 재시도 교정(2026-06-23 전수감사)] bare 토큰 'cancel/abort/disconnect/stream/closed'는 watchdog의
    # *의도적* 취소나 무관한 "...closed" 텍스트까지 transient로 오인해 죽은 서브프로세스에 3회 재시도 churn을
    # 냈다. 특정 구문('stream closed' 등)·서브프로세스 사망 신호(sigterm/143/process exited=resume 의도)는
    # 유지하되 bare 토큰은 제거한다.
    return any(s in t for s in ("429", "500", "502", "503", "529", "overload", "rate", "timeout",
                                "command failed", "exit code", "sigterm", "143", "137",
                                "broken pipe", "message reader", "connection", "disconnected",
                                "stream closed", "stream is closed", "process exited"))


def _strip_decoration(text: str) -> str:
    """보고에서 장식용 수평선('---' 등)만 제거한다(내용은 보존)."""
    lines = [ln for ln in (text or "").splitlines()
             if ln.strip() not in ("---", "***", "___", "—", "──────")]
    return "\n".join(lines).strip()


def build_options(config: Config, **overrides) -> ClaudeAgentOptions:
    """Organt용 ClaudeAgentOptions를 만든다.

    기능5·6에서 mcp_servers/hooks/allowed_tools 등을 override로 주입한다.
    """
    opts = dict(
        model=config.model,                       # None이면 SDK 기본 모델
        system_prompt=load_persona(),             # CLAUDE.md 인격 로딩(없으면 기본)
        cwd=str(config.workspace_dir),            # 작업공간 안에서만 파일 작업
        allowed_tools=["Read", "Write", "Edit", "Bash"],  # 내장 파일/셸 툴(Step1 범위)
        permission_mode="acceptEdits",            # 파일 편집 자동 승인(권한 훅은 Step2)
        max_turns=16,                             # 작업당 턴 상한(폭주 방지)
    )
    # [워커 CLI 버전 교정(2026-06-24) — CCR 프록시 호환] SDK 번들 CLI(2.1.170)는 CCR-v2 egress 프록시를
    # 통과할 때 모델 API 응답 스트림을 끝없이 버퍼링→RSS 10GB로 폭주해 OOM(라이브: 모든 워커가 어떤 모델
    # 이든 시동 직후 죽음, 소켓 read 4.2GB 관측). 시스템에 설치된 새 CLI(2.1.187 — 메인 세션이 쓰는 그
    # 버전)는 같은 프록시로 정상 작동(즉시 응답·폭주 없음). README의 "upgrade it" 처방대로, 프록시를 끄지
    # 않고(금지) 워커가 호환 CLI를 쓰게 cli_path를 명시한다. 그 바이너리가 없으면 번들로 안전 폴백.
    _wcli = os.environ.get("ORGANT_WORKER_CLI") or "/opt/node22/bin/claude"
    if os.path.exists(_wcli):
        opts["cli_path"] = _wcli
    opts.update(overrides)
    return ClaudeAgentOptions(**opts)


class Organt:
    """파일시스템에 접근하고, 세션 resume로 State를 보존하는 Organt(LLM) 본체."""

    def __init__(self, config: Config, options: Optional[ClaudeAgentOptions] = None,
                 state_path=None, narrate=None, on_activity=None, on_turn=None):
        self.config = config
        self.options = options or build_options(config)
        self.narrate = narrate   # (text)->None: 매 발화(추론) 기록 콜백(관측). 없으면 미기록.
        self.on_activity = on_activity   # ()->None: 메시지 수신마다 호출 — 침묵 워치독 하트비트.
        # [관측 v1 — 2026-07-07] (dict)->None: wake 1회 결산 콜백(비용·지연·토큰·재시도). SDK
        # ResultMessage의 usage/cost/duration을 종전엔 버렸음(max_turns 판정만). 이제 관측에 실린다.
        self.on_turn = on_turn
        # State(작업 맥락)는 세션 ID로 보존한다. 재시작(새 인스턴스) 시 파일에서 복원.
        self.state_path = (Path(state_path) if state_path is not None
                           else config.audit_log_path.parent / "organt_state.json")
        self.session_id = self._load_session_id()

    def _load_session_id(self) -> Optional[str]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")).get("session_id")
        except (OSError, ValueError):
            return None

    def _state_read(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _state_write(self, **fields) -> None:
        """상태 파일을 **병합** 저장 — 남의 키(codex 사용량 누계 등)를 지우지 않는다."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            st = self._state_read()
            st.update(fields)
            self.state_path.write_text(json.dumps(st), encoding="utf-8")
        except OSError:
            pass

    def _save_session_id(self, sid: str) -> None:
        self.session_id = sid
        # cwd를 함께 영속 — CLI 세션 저장소는 cwd 기준이라, 같은 세션을 잇는 다음 빌드가
        # '세션이 시작된 그 cwd'를 그대로 쓰게 한다(pinned_cwd). 흐름 도중 작업공간이 바뀌어도
        # (create_project의 폴더 카빙) resume가 깨지지 않는 구조적 근거.
        self._state_write(session_id=sid, cwd=str(self.options.cwd or ""))

    # [계측 장부 용량(2026-07-30, U-079 정밀검사)] 종전 4칸. micro 턴은 매번 **새 스레드**라
    # 응찰·표결이 몇 번만 지나가도 본 스레드의 기준선이 밀려났고, 그러면 다음 작업 턴이 누계
    # 전액을 그 턴 몫으로 기록한다(실측: 입력·출력이 턴마다 단조 증가 — 3초 턴에 출력 2.6만).
    # 활성 세션은 절대 밀어내지 않고, 나머지는 최근 사용 순으로 넉넉히 보관한다.
    _USAGE_BOOK_CAP = 64

    def _codex_usage_delta(self, sid, usage) -> dict:
        """codex가 준 **스레드 누계**를 이 턴 몫(차분)으로 바꾼다.

        [과금 수리(2026-07-28, U-074 실측)] `turn.completed.usage`는 그 턴이 쓴 양이 아니라
        **스레드 전체 누계**다(실측: 같은 세션에 짧은 프롬프트 3연속 → 11,556 → 24,671 → 37,805).
        누계를 그대로 청구하면 이어가는 턴마다 과거를 다시 청구한다 — 턴 N개면 대략 N/2배 과다.
        직전 누계를 세션과 함께 영속해 차분만 청구한다(새 세션이면 그 턴 전액 = 그 스레드 첫 청구).

        [기준선 유실 관측(2026-07-30)] 기준선이 없어 전액을 청구하는 것은 '새 스레드'일 때만
        정상이다. 이미 본 적 있는 세션인데 기준선이 없으면 그건 장부 결함이다 — 조용히 과금하지
        말고 `usage_baseline_lost`로 드러낸다. 감사용으로 누계·차분·세션을 함께 돌려준다.
        """
        u = usage if isinstance(usage, dict) else {}
        if not u:
            return {}
        keys = ("input_tokens", "cached_input_tokens", "output_tokens",
                "reasoning_output_tokens", "cache_write_input_tokens")
        now = {k: float(u.get(k) or 0) for k in keys if isinstance(u.get(k), (int, float))}
        st = self._state_read()
        book = st.get("codex_usage") if isinstance(st.get("codex_usage"), dict) else {}
        key = str(sid or "")
        prev = book.get(key) if isinstance(book.get(key), dict) else {}
        seen = st.get("codex_seen_sids") if isinstance(st.get("codex_seen_sids"), list) else []
        first_time = key not in seen
        delta = {}
        for k, v in now.items():
            base = float(prev.get(k) or 0)
            # 누계가 줄어드는 일은 없어야 하지만(세션 재생성 등), 나면 음수 대신 이번 값을 청구.
            delta[k] = int(v - base) if v >= base else int(v)
        book[key] = {k: int(v) for k, v in now.items()}
        book[key]["_ts"] = int(time.time())
        # 활성 세션은 남기고, 나머지를 최근 사용 순으로 정리한다(삽입 순서가 아니라 사용 시각).
        if len(book) > self._USAGE_BOOK_CAP:
            _live = {key, str(self.session_id or "")}
            _rest = sorted((k for k in book if k not in _live),
                           key=lambda k: int((book.get(k) or {}).get("_ts") or 0), reverse=True)
            keep = set(_rest[: max(0, self._USAGE_BOOK_CAP - len(_live))]) | _live
            book = {k: v for k, v in book.items() if k in keep}
        if key and first_time:
            seen = (seen + [key])[-512:]
        self._state_write(codex_usage=book, codex_seen_sids=seen)
        if not prev and not first_time and any(v for v in delta.values()):
            # 이미 본 세션인데 기준선이 없다 = 장부에서 밀려났다는 뜻. 청구는 하되 기록으로 남긴다.
            self._usage_anomaly = {"why": "baseline_lost", "sid": key,
                                   "cum_input": int(now.get("input_tokens") or 0)}
        # [스레드 누계는 다음 턴의 요금이다(2026-08-01, U-442 실측)] 이 값이 그대로 다음 호출의
        # 입력이 된다 — 끊을 자리를 정하려면 지금 얼마를 이고 있는지부터 알아야 한다.
        self._thread_cum_sid = key
        self._thread_cum_input = int(now.get("input_tokens") or 0)
        # 감사 필드 — 러너가 turn_done에 그대로 실어 사후 검산이 가능하게 한다.
        self._usage_audit = {"sid": key, "first_turn_of_thread": bool(first_time),
                             "cum_input": int(now.get("input_tokens") or 0),
                             "cum_output": int(now.get("output_tokens") or 0)}
        return delta

    def _reset_session(self) -> None:
        """스테일 세션 폐기 — resume 대상이 저장소에 없을 때(cwd 불일치·유실) 새 출발.
        상태 파일째 지워 cwd 고정도 함께 푼다(새 세션은 현재의 올바른 작업공간에서 시작)."""
        self.session_id = None
        try:
            self.state_path.unlink()
        except OSError:
            pass

    def _session_in_store(self) -> bool:
        """resume 대상이 실제 CLI 저장소에 있는가.

        Claude는 cwd 슬러그 아래, Codex는 날짜별 rollout 아래에 저장한다. GPT 세션을 Claude
        경로에서 검사하면 매 턴 정상 sid를 지워 같은 작업을 fresh rollout으로 반복하므로 런타임
        종류에 맞는 저장소를 본다.
        """
        if not self.session_id:
            return False
        if getattr(self, "_codex_model", None):
            sid = str(self.session_id)
            # Codex session id는 UUID 계열이다. glob 메타문자를 받지 않아 저장소 밖 탐색이나
            # 패턴 확장을 만들지 않으며, 현재 정식 레이아웃(year/month/day)을 정확히 좁힌다.
            if not sid or any(ch not in "0123456789abcdefABCDEF-" for ch in sid):
                return False
            root = Path.home() / ".codex" / "sessions"
            try:
                return next(root.glob(f"*/*/*/rollout-*{sid}.jsonl"), None) is not None
            except OSError:
                return False
        cwd = str(self.options.cwd or os.getcwd())
        p = (Path.home() / ".claude" / "projects" / cwd.replace("/", "-")
             / f"{self.session_id}.jsonl")
        try:
            return p.exists()
        except OSError:
            return False

    def will_resume(self) -> bool:
        """[적당히 — wake-aware 주입] 이번 실행이 직전 세션을 resume하는가(=대화 기억이 보존되는가).
        _session_in_store가 결정론(세션 파일 실재)이라 SYS가 프롬프트 조립 전에 '전체(fresh) vs 델타
        (resume)'를 추측 없이 정확히 안다. True=resume(핵심 규칙만·나머지는 대화 기억), False=fresh(전체)."""
        return self._session_in_store()

    def _options_for_call(self, micro: bool = False) -> ClaudeAgentOptions:
        """직전 세션이 있으면 resume를 붙인 옵션을 만든다.

        [마이크로 무도구(2026-07-20, e2e ch79 비용 실측)] 응찰·표결 같은 즉답 턴은 도구를 안 쓰는데
        guide MCP 도구 스키마가 매 호출 입력 토큰으로 실렸다(프로브 1회 평균 $0.042의 큰 몫).
        micro면 mcp_servers·allowed_tools를 비워 스키마를 원천 제거 — resume(세션 기억)은 그대로라
        '못 본 것만 주입' 원칙과 정합(제한이 아니라 낭비 제거: 즉답 턴에 즉답만 싣는다)."""
        o = self.options
        if micro:
            o = dataclasses.replace(o, mcp_servers={}, allowed_tools=[])
            # [프로브 무기억화(2026-07-22, U-041 실측: 프로브 회당 ~1cr = 세션 캐시 재읽기 과금 —
            # 판당 430cr/원장의 27%)] 즉답 턴(응찰·표결·프로브)의 프롬프트는 자족적('이 텍스트만
            # 보고 즉답')이고 정체성(직군·개인 기준)은 시스템 프롬프트가 담는다 — resume을 떼어 신선
            # 1회 호출로(회당 ~0.1cr). 세션 기억 오염 방지는 handle의 sid 미저장이 담당.
            # 되돌리기: ORGANT_MICRO_FRESH=0.
            if os.environ.get("ORGANT_MICRO_FRESH", "1") != "0":
                return o
        _sid = self._resume_sid(micro)
        if _sid:
            return dataclasses.replace(o, resume=_sid)
        return o

    def _resume_sid(self, micro: bool):
        """[짧은 상호작용은 세션을 물지 않는다(2026-07-30, U-079 실측)] 응찰·표결 한 줄에도 그 봇의
        작업 스레드 전체가 다시 실렸다 — 168토큰 말하려고 12만~25만 토큰을 읽는 구조였다. 세션의
        '기억'은 서버 상태가 아니라 대화 기록이고, 매 호출마다 전량이 다시 전송되기 때문이다.

        micro 프롬프트는 이미 자족적이다(역할 + 본문 두 줄). 작업 턴은 종전대로 세션을 잇고,
        micro만 새 스레드로 띄운다 — 기억이 필요 없는 곳에서만 끊는다. ORGANT_MICRO_FRESH=0이면
        종전 동작(세션 이어감).
        """
        if micro and os.environ.get("ORGANT_MICRO_FRESH", "1") != "0":
            return None
        # [스레드는 일감 경계에서 끊는다(2026-08-01, U-478 실측)] 작업 턴 하나가 26~34분 동안
        # **1,200만~3,200만 토큰**을 실어 나른다(캐시 97~99% — 거의 전부 같은 스레드의 재전송).
        # 상위 5턴이 판 전체 비용의 41%였다. 스레드가 길어질수록 매 내부 호출이 그 전량을 다시
        # 보내기 때문이다. 기억을 통째로 버리자는 게 아니라 **일감이 바뀌는 자리**에서 끊는다 —
        # 그 일감이 무엇인지·무엇을 했는지는 장부(백로그 본문·활동 기록)가 들고 있고, 새 스레드는
        # 그걸 프롬프트로 받는다. 같은 일감을 이어가는 턴은 종전대로 세션을 잇는다.
        _mark = str(getattr(self, "_work_scope", "") or "")
        if _mark and _mark != str(getattr(self, "_work_scope_seen", "") or ""):
            self._work_scope_seen = _mark
            if os.environ.get("ORGANT_SCOPE_FRESH", "1") != "0":
                return None
        # [스레드 누계 상한은 폐기했다(2026-08-01, 같은 날 실측으로 반증)] 누계 1억 토큰짜리 스레드를
        # 보고 "스레드가 길어서 비싸다"로 읽었으나, 같은 길이 구간에서 첫 턴과 이어가는 턴을 나란히
        # 재 보니 이득이 턴 길이에 따라 뒤집혔다 — 30~120초 턴은 새 스레드가 7.0배 쌌지만 15분 넘는
        # 작업 턴은 0.8배로 **역전**했다(새 스레드는 작업공간을 처음부터 다시 파악한다). 누계 300만
        # 상한은 5~8턴마다 리셋을 강제해 바로 그 긴 작업 턴까지 끊는다. 진짜 원인은 턴 길이다 —
        # 초당 입력은 어느 구간에서나 7천~1만2천으로 거의 일정하고, 15분 넘는 턴 11개가 전체 비용의
        # 21%였다. 일감 경계 리셋(위)만 남긴다.
        return self.session_id

    async def _run_codex(self, prompt: str, micro: bool = False):
        """[GPT 봇(2026-07-22)] codex(bwrap 외부 샌드박스) 한 턴 — guide 도구는 HTTP 브리지로 물린다.
        Claude _run_once와 같은 (최종발화, session_id)를 돌려주고, cwd 앵커 주입·botpool 슬롯도 동일."""
        from .codex_mcp_bridge import run_codex_turn
        _cwd = str(getattr(self.options, "cwd", None) or "")
        if _cwd:
            prompt = (f"[작업공간 — 절대경로] 당신의 모든 파일은 정확히 여기 있습니다: {_cwd}\n"
                      f"이 경로가 당신의 cwd입니다. 파일·디렉터리는 항상 이 절대경로 기준으로 확인하세요.\n\n") + prompt
        _model = getattr(self, "_codex_model", None)
        _raw_usage = {}

        def _take_usage(u):
            # codex가 주는 값은 **스레드 누계** — 여기선 받아만 두고, 세션 id를 아는 아래에서
            # 차분으로 바꿔 청구한다(_codex_usage_delta).
            if isinstance(u, dict) and u:
                _raw_usage.clear()
                _raw_usage.update(u)

        async with botpool.slot():
            _text, _sid = await run_codex_turn(
                prompt=prompt, cwd=_cwd, session_id=self._resume_sid(micro),
                tools=([] if micro else (getattr(self, "_codex_tools", None) or [])),
                model=_model,
                effort=getattr(self, "_codex_effort", None),
                read_only=bool(getattr(self, "_codex_read_only", False)),
                on_activity=self.on_activity, on_narrate=self.narrate,
                on_usage=_take_usage,
                # [턴 예산(2026-07-28)] 호출이 곧 일인 턴(마감·e2e)에서만 침묵 턴을 이어 붙인다 —
                # SYS가 그 턴에 표식을 단다. 회의 발언 턴은 종전 그대로 한 판에 끝난다.
                expect_tool=bool(getattr(self, "_codex_expect_tool", False)) and not micro)

        # 누계 → 이 턴 몫으로 환산해 결산에 싣는다. handle()의 on_turn이 그대로 실어
        # 러너 → 웹(report_usage) → UsageLedger로 흘려보낸다.
        from .gpt_pricing import usage_record
        rec = usage_record(_model, self._codex_usage_delta(_sid or self.session_id, _raw_usage))
        if rec:
            # [사후 검산 가능하게(2026-07-30)] 청구값만 남기면 그 값이 맞는지 나중에 확인할 방법이
            # 없다 — 어느 스레드의 몇 번째 턴인지, 그 시점 누계가 얼마인지를 함께 싣는다.
            rec = {**rec, **(getattr(self, "_usage_audit", None) or {})}
            _an = getattr(self, "_usage_anomaly", None)
            if _an:
                rec["usage_anomaly"] = _an
                self._usage_anomaly = None
            self._last_result = {**(getattr(self, "_last_result", None) or {}), **rec}
        return _text, _sid

    async def _run_once(self, prompt: str, micro: bool = False):
        """ClaudeSDKClient 한 번 실행 → (최종 발화, session_id).

        SYS의 무진행 취소(CancelledError)가 나도 `async with`의 정상 종료(__aexit__)가 SDK 자원을
        정리한다. 취소는 '도구 활동이 완전히 멈춘'(진짜 행) 경우에만 일어나므로 — 일하는 워커는 자르지
        않으므로 — 정상 종료가 깔끔히 이뤄진다(바쁜 워커를 끊다 자원이 남던 과거 문제의 근본 회피)."""
        # [GPT 봇 라우팅(2026-07-22)] 모델이 gpt-*면(빌더가 _codex_model 심음) Claude SDK 대신 codex 경로.
        if getattr(self, "_codex_model", None):
            return await self._run_codex(prompt, micro=micro)
        final_text = ""
        captured_sid: Optional[str] = None
        truncated = False
        budget_cut = False
        # stderr 수집: CLI의 실패 사유(예: 'No conversation found')는 stderr로만 나와 SDK 예외
        # 텍스트에 안 실린다(라이브 관측 — 마커 감지 불발의 원인). 꼬리를 모아 예외에 붙여
        # '왜 죽었는지'가 항상 에러 텍스트에 남게 한다(스테일 마커·일시오류 판별 모두 강화).
        err_tail: list = []

        def _collect_stderr(line: str) -> None:
            if line and len(err_tail) < 20:
                err_tail.append(str(line).strip())
            # stderr 출력 = CLI 서브프로세스가 살아 움직인다(레이트리밋 재시도·진행 로그 등). 표준출력
            # 메시지가 한동안 없어도(첫 토큰까지 침묵) 이 신호로 무진행 워치독의 사각을 메운다 —
            # '살아서 대기 중'을 '행(hang)'으로 오인해 잘 돌아가는 흐름을 끊던 결함 교정.
            if line and self.on_activity:
                try:
                    self.on_activity()
                except Exception:
                    pass

        # [작업공간 앵커 — cwd 오진 차단(2026-06-23, 사용자)] 봇은 자기 작업공간 절대경로를 구조적으로
        # 못 받아 모델 내장 프라이어('/workspace')로 흘러, 빈 /workspace를 보고 '이전 파일 모두 유실'로
        # 오판해 중복 리빌드를 지시하던 라이브 결함(P-031 5925). 경로가 닿는 통로가 '위임자가 본문에 직접
        # 타이핑'뿐이라 봇마다 들쭉날쭉했다. system_prompt는 resume 세션에 재적용이 불확실하므로, *매 턴
        # 메시지 본문*에 cwd 절대경로를 못 박아 모든 봇·모든 턴에 구조적으로 보장한다(단일 chokepoint).
        _cwd = getattr(self.options, "cwd", None)
        if _cwd:
            prompt = (f"[작업공간 — 절대경로] 당신의 모든 파일은 정확히 여기 있습니다: {_cwd}\n"
                      f"이 경로가 당신의 cwd입니다 — `/workspace`가 아닙니다. 파일·디렉터리는 항상 이 절대경로로 "
                      f"확인하고, 무언가 안 보여도 '유실'로 단정하지 말고 먼저 이 경로를 Read/ls 하세요.\n\n") + prompt

        # [in-flight = 워커 *생존* 보호(2026-06, 사용자 검증)] 이 하트비트는 '진행을 조작'하는 게 아니라
        # '워커 서브프로세스가 *실제로 살아서* 이 턴을 도는 중'을 반영한다 — receive_response를 await하는
        # 동안은 서브프로세스가 살아있다(죽으면 SDK가 예외를 던져 루프가 끝난다). 레이트리밋으로 첫 토큰까지
        # 느리거나 긴 독립 검증 중이면 출력이 한동안 없을 수 있는데(라이브 확인: 16분+ 도는 워커 0.5%CPU),
        # 그 침묵만으로 *살아 일하는 워커*를 잘라선 안 된다. 진짜 wedge(살았지만 영영 멈춤)는 러너 max_age로.
        async def _inflight_alive():
            try:
                while True:
                    await asyncio.sleep(20)
                    if self.on_activity:
                        try:
                            self.on_activity()           # '생존' 신호 — 서브프로세스가 살아 도는 한
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass

        opts = dataclasses.replace(self._options_for_call(micro=micro), stderr=_collect_stderr)
        _alive = None
        try:
            # [P0 봇풀 바운딩] 전역 서브프로세스 세마포어 + 메모리 입장 제어 통과 후 CLI 스폰 —
            # 회의 병렬 심의단 × 다중 흐름으로 동시 서브프로세스가 무바운드로 자라 OOM 나던 것 상한.
            async with botpool.slot(), ClaudeSDKClient(options=opts) as client:
                await client.query(prompt)
                _alive = asyncio.ensure_future(_inflight_alive())
                async for msg in client.receive_response():
                    # 메시지 수신도 '활동'이다 — 도구 호출이 없는 긴 모델 생성(거대 파일 하나를 첫 Write로
                    # 만들기 직전의 장문 사고/작성)이 침묵 워치독에 '행'으로 오인되지 않게, 도구 훅(Pre/Post)
                    # 사이의 사각을 메시지 단위 하트비트로 메운다.
                    if self.on_activity:
                        try:
                            self.on_activity()
                        except Exception:
                            pass
                    sid = getattr(msg, "session_id", None)
                    if sid:
                        captured_sid = sid
                    if isinstance(msg, AssistantMessage):
                        t = "".join(b.text for b in msg.content if isinstance(b, TextBlock)).strip()
                        if t:
                            final_text = t   # 마지막 비어있지 않은 발화만 유지
                            if self.narrate:   # 관측: 매 발화(추론)를 기록 — '왜 그 행동을 했나'를 본다
                                try:
                                    self.narrate(t)
                                except Exception:
                                    pass
                    elif isinstance(msg, ResultMessage):   # 턴 한도 등으로 끊겼는지 + [관측 v1] 결산 포집
                        st = (getattr(msg, "subtype", "") or "") + (getattr(msg, "stop_reason", "") or "")
                        if "max_turns" in st.lower():
                            truncated = True
                        # [턴 예산 캡(2026-07-21, U-036 재작업 #4)] max_budget_usd 초과 종료도 같은
                        # '끊긴 턴' 족 — 정직 마커(아래)로 미완 참칭을 막고, 세션은 보존돼 resume가 잇는다.
                        if "max_budget" in st.lower():
                            budget_cut = True
                        # SDK 결산(모델·SDK 버전마다 필드명 상이 — 방어적으로). 종전엔 전량 폐기.
                        _u = getattr(msg, "usage", None) or {}
                        if not isinstance(_u, dict):
                            _u = getattr(_u, "__dict__", {}) or {}
                        # [캐시 축이 빠지면 지도가 반쪽(2026-07-30, U-079 정밀검사)] Anthropic은
                        # 캐시 읽기·쓰기를 input_tokens와 **별도 필드**로 준다 — 종전엔 그 둘을
                        # 버려, Claude 봇의 입력 총량이 실제보다 작게 잡혔다(비용의 대부분이 입력인데).
                        # codex 경로와 같은 축(입력=신선+캐시)으로 맞춘다.
                        _cache_r = int(_u.get("cache_read_input_tokens") or 0)
                        _cache_w = int(_u.get("cache_creation_input_tokens") or 0)
                        _in = int(_u.get("input_tokens") or _u.get("prompt_tokens") or 0)
                        self._last_result = {
                            "cost_usd": getattr(msg, "total_cost_usd", None),
                            "duration_ms": getattr(msg, "duration_ms", None),
                            "num_turns": getattr(msg, "num_turns", None),
                            "tokens_in": (_in + _cache_r + _cache_w) or None,
                            "tokens_cached": _cache_r or None,
                            "tokens_cache_write": _cache_w or None,
                            "tokens_out": _u.get("output_tokens") or _u.get("completion_tokens"),
                        }
        except asyncio.CancelledError:
            raise                                    # 워치독 취소는 의미 보존(감싸지 않음)
        except Exception as e:
            tail = " | ".join(x for x in err_tail[-3:] if x)
            raise RuntimeError(f"{e}{(' [stderr] ' + tail) if tail else ''}") from e
        finally:
            if _alive is not None:
                _alive.cancel()                          # 턴 종료(서브프로세스 죽음·완료) → 생존 신호 중단
        if truncated and not _is_transient_api_error(final_text):
            final_text = (final_text + "\n(⚠ 턴 한도 도달 — 작업이 미완일 수 있음)").strip()
        # [턴 예산 캡] '턴 한도 도달' 문구 족을 공유해 이어가기 신호(sys_core continue 조건)와 정합 —
        # 빈 발화여도 마커로 비-공백이 되므로 handle의 빈응답 재시도(예산 재소진)에도 안 빠진다.
        if budget_cut and not _is_transient_api_error(final_text):
            final_text = (final_text + "\n(⚠ 턴 한도 도달(예산 상한) — 여기서 끊고 반환, 작업이 미완일 수 있음)").strip()
        return final_text, captured_sid

    async def handle(self, prompt: str, micro: bool = False) -> str:
        """요청 한 건을 처리하고 **최종 발화**(=보고/응답)만 돌려준다.

        턴마다의 중간 narration은 버리고 마지막 메시지만 반환(Response가 간결). 직전 세션이
        있으면 resume로 이어간다(State 보존). 일시적 API 오류(429/5xx/529 과부하)는 백오프 재시도.
        """
        # [사전 점검 — 스테일 resume 차단] 세션이 '이 cwd의' 저장소에 없으면 스폰 전에 폐기한다.
        # 레거시 상태 파일(cwd 미기록)·cwd 불일치·저장소 유실 전부가 여기서 결정론적으로 걸러져,
        # 'No conversation found' 영구 헛돌이(라이브 12회×2 관측)가 원천 차단된다.
        if self.session_id and not self._session_in_store():
            self._reset_session()
        final_text = ""
        self._last_result = {}
        _t0 = time.monotonic()
        _retries = 0
        _err = None
        for attempt in range(_MAX_API_RETRY):
            try:
                # micro가 아닐 땐 종전 호출 형태 유지 — 시그니처 스텁(테스트·외부 대체)의 무회귀.
                final_text, captured_sid = (await self._run_once(prompt, micro=True) if micro
                                            else await self._run_once(prompt))
            except Exception as e:                       # 전송/스트림 예외도 일시오류로 간주해 재시도
                final_text, captured_sid = f"API Error: {e}", None
                # [진단 문자열은 잘리면 쓸모없다(2026-07-30, 실측)] 150자에서 잘려 실패 원인이
                # 붙는 꼬리(stdout 이벤트)가 통째로 사라졌다 — 실패 조사에서 눈이 먼 이유였다.
                _err = str(e)[:400]
            if attempt > 0:
                _retries = attempt
            if captured_sid and not (micro and os.environ.get("ORGANT_MICRO_FRESH", "1") != "0"):
                # [프로브 무기억화] 신선 micro 호출이 만든 세션 id는 버린다 — 본세션 기억 포크 방지.
                self._save_session_id(captured_sid)
            # 마커 감지(이중 안전망): 사전 점검이 레이아웃 변화로 못 거른 변종이 stderr 꼬리로 잡히면
            # 같은 처리 — 세션을 버리고 즉시 새 세션으로 전진(재시도해 봐야 영원히 같은 실패라서).
            if _is_stale_session_error(final_text) and self.session_id:
                self._reset_session()
                continue
            # 정상 응답(비어있지 않고 일시오류도 아님)이면 종료. **빈 응답('')은 서브프로세스가 발화 없이
            # 조용히 죽은 신호**이므로(이게 동료가 '무응답'으로 보여 리더가 충원·재처리로 churn하던 원인)
            # 일시오류와 똑같이 resume 재시도한다. 끝내 비면 그대로 반환(무한루프 없음 — 최대 _MAX_API_RETRY).
            if final_text.strip() and not _is_transient_api_error(final_text):
                break
            if attempt < _MAX_API_RETRY - 1:
                await asyncio.sleep(2 * (attempt + 1))   # 2s, 4s 백오프
        # [관측 v1] wake 결산 방출 — 비용·지연·토큰·재시도. 실제 소요는 monotonic로(SDK duration 결측 대비).
        if self.on_turn:
            _ok = bool(final_text.strip()) and not final_text.startswith("API Error:") \
                and not _is_transient_api_error(final_text)
            try:
                self.on_turn({**(self._last_result or {}),
                              # 실제로 이 턴을 돈 모델 — 종전엔 전역 설정값을 실어 per-agent 지정
                              # (특히 GPT 봇)이 결산·원장에서 전부 전역 모델로 보였다.
                              "model": (getattr(self, "_codex_model", None)
                                        or getattr(self.options, "model", None)
                                        or getattr(self.config, "model", "") or ""),
                              "duration_ms": (self._last_result or {}).get("duration_ms")
                              or int((time.monotonic() - _t0) * 1000),
                              "retries": _retries, "ok": _ok, "error": _err})
            except Exception:
                pass
        return _strip_decoration(final_text)
