"""구조화 메시지 프로토콜 — Discord Guide의 계약 (docs: Other/Guide/Discord.md).

Discord엔 구조화된 형식만 오간다. Discord가 주는 정보(From=보낸 봇, RepliesTo=reply,
식별=메시지 ID)는 블록에 쓰지 않고, 블록엔 Discord가 주지 않는 것만 적는다.
사람도 읽고 System Bot도 파싱한다.

  [Request]            [Response]          [Task-XXX]
  To: @XXX             Body: ---           Purpose / Status / Goal / Group / (result)
  Kind: Work|Info
  Body: ---
"""
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Union


class Kind(str, Enum):
    WORK = "Work"   # 요구가 작업(목표)
    INFO = "Info"   # 요구가 정보(질문)


@dataclass
class Request:
    """무언가를 요구하는 메시지 (Request.md)."""
    to_id: Optional[int]            # To: 멘션 대상(하나)
    kind: Kind                      # Work | Info
    body: str                       # Work면 목표, Info면 질문
    from_id: Optional[int] = None   # From: 보낸 봇(수신 시 Discord가 채움)
    message_id: Optional[str] = None
    attachments: list = field(default_factory=list)  # [파일 전송] 사용자가 첨부한 파일 [(filename, bytes), ...]


@dataclass
class Response:
    """Request를 닫는 메시지 (Response.md)."""
    body: str                       # Work면 결과보고, Info면 답
    from_id: Optional[int] = None
    replies_to: Optional[str] = None  # RepliesTo: 닫는 Request의 메시지 ID(reply)
    message_id: Optional[str] = None


@dataclass
class TaskStatus:
    """채널에 게시되는 Task 상태블록 (Discord.md). System Bot이 수시 갱신."""
    task_id: str
    purpose: str = ""
    status: str = ""
    goal: str = ""
    owner: str = ""                                             # 단일 책임자(accountable)
    group: List[Tuple[str, str]] = field(default_factory=list)  # [(@멘션, 봇 정보)]
    result: Optional[str] = None


class Marker:
    """수행문 마커 사전 — 봇 발화 속 신호(시스템이 파싱해 동작)의 단일 정의.

    대화가 매체인 시스템에서 발화로 수행하는 것은 정당하다(사람도 "동의합니다"로 표결한다).
    단 그 정의가 여러 파일에 흩어지면 물리법칙(Rule)이 자연어 표면에 산개된다 — 여기가 단일 사전이다.
    이관 현황(2026-07-08): organt/builder 완료. communication·floor·flow·sys_prompt는
    본체 세션 착지 후 이관(같은 파일 동시 편집 충돌 회피 — ops Task 게이트 정리와 함께).
    """
    BID = "응찰"            # [응찰: N] 발언권 응찰(강도 0~9)
    CONTINUE = "계속"        # [계속: N] 종결 반대 = 발언 의무를 진 응찰(동형 강도)
    PASS = "패스"            # [패스] 발언권 포기
    END = "종료"             # [종료] 종결 찬성
    APPLY = "지원"            # [지원] 채용 공고에 대한 지원 선언(+지원서 본문이 뒤따름)
    DELEGATED = "위임됨"      # [위임됨] 위임 접수 확인(시스템 발화)
    OFFDOMAIN = "직군밖"      # [직군밖] Work 반려 — 내 도메인이 아님
    EXPERIENCE = "경험"       # [경험] 보고 속 경험 적재 필드
    SYS_PROBE = "SYS 프로브"  # 시스템 프로브 표식(봇 생각 아님)

    # 파싱 정본 정규식 — 소비처는 이걸 import해 쓴다(로컬 재정의 금지)
    BID_RE = re.compile(r"\[\s*(?:응찰|계속)\s*[:：]\s*([0-9])\s*\]")
    PASS_OR_END_RE = re.compile(r"^\[?\s*(패스|종료)\s*\]?\s*$")
    APPLY_RE = re.compile(r"\[\s*지원\s*\]")
    ROLE_DECL_RE = re.compile(r"\[\s*직군\s*[:：]\s*([^\]]+?)\s*\]")   # 무직 지원자의 자기 직군 선언

    # 진행 가시성(narrate)에서 '봇의 생각'이 아닌 메커니즘 발화로 거를 토큰들
    MECHANISM_TOKENS = ("응찰", "[패스", "[계속", "발언권", "[SYS 프로브")


# --- 포맷팅 (SYS → Discord) ---

def format_request(to_id: int, kind: Union[Kind, str], body: str) -> str:
    k = kind.value if isinstance(kind, Kind) else str(kind)
    return f"[Request]\nTo: <@{to_id}>\nKind: {k}\nBody: {body}"


def format_response(body: str) -> str:
    return f"[Response]\nBody: {body}"


def format_task_status(ts: TaskStatus) -> str:
    lines = [
        f"[Task-{ts.task_id}]",
        f"Purpose: {ts.purpose or '---'}",
        f"Status: {ts.status or '---'}",
        f"Goal: {ts.goal or '---'}",
        f"Owner: {ts.owner or '—(공동)'}",
        "Group:",
    ]
    for mention, info in ts.group:
        lines.append(f"- {mention}: {info}")
    if ts.result is not None:
        lines.append(f"- result: {ts.result}")
    return "\n".join(lines)


# --- 파싱 (Discord → SYS) ---

def _fields(content: str) -> dict:
    """'Key: value' 라인들을 dict로 (키는 소문자). 헤더('[..]')는 제외.
    주의: 본문(Body)은 여러 줄·'['로 시작하는 줄을 포함할 수 있으므로 여기서 뽑지 말고
    _multiline_body로 따로 뽑는다(아래). 여긴 To/Kind 같은 단일 헤더 추출용."""
    out = {}
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("[") or ":" not in s:
            continue
        key, _, val = s.partition(":")
        k = key.strip().lower()
        if k == "body":          # 본문은 첫 줄만 담기지 않도록 _fields에서 제외(멀티라인 보존)
            break
        out[k] = val.strip()
    return out


def _multiline_body(content: str) -> str:
    """'Body:' 이후의 '모든 줄'을 본문으로 돌려준다(여러 줄·'['로 시작하는 줄 포함).
    멀티라인 요청/응답 본문이 첫 줄에서 잘리던 버그를 막는다."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("body:"):
            first = line.split(":", 1)[1].strip() if ":" in line else ""
            rest = lines[i + 1:]
            return "\n".join(([first] if first else []) + rest).strip()
    return ""


def parse(*, message_id, author_id, mention_ids: List[int], reply_to_id,
          content: str) -> Optional[Union[Request, Response]]:
    """Discord 메시지(primitive) → Request/Response/None."""
    c = (content or "").strip()
    if not c:
        return None
    head = c.splitlines()[0].strip()

    if head.startswith("[Response]") and reply_to_id is not None:
        return Response(body=_multiline_body(c), from_id=author_id,
                        replies_to=str(reply_to_id), message_id=str(message_id))

    if head.startswith("[Request]"):
        f = _fields(c)
        kind = Kind.WORK if f.get("kind", "").strip().lower().startswith("work") else Kind.INFO
        to_id = mention_ids[0] if mention_ids else None
        return Request(to_id=to_id, kind=kind, body=_multiline_body(c),
                       from_id=author_id, message_id=str(message_id))

    return None
