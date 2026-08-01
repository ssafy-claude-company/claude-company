# -*- coding: utf-8 -*-
"""봇 실행 방법 — 어느 엔진으로 도는가를 한 곳에서 정한다(2026-07-30, 현준-4).

[왜 떼어냈나]
종전엔 `str(model).startswith("gpt-")`가 곧 실행 엔진 선택이었다(organt/builder.py). 문자열
접두사가 분기라서, 새 엔진이 붙을 때마다 판단이 코드 여기저기로 번지고 어디를 고쳐야 하는지
알 수 없다. 제3자 로컬 LLM을 등록받으려면 '주소·자격증명 주인·한도'가 함께 와야 하는데
20자 문자열에는 그것들이 들어가지 않는다. 설계는 ops/2026-07-30-실행방법-분리-설계.md.

지금 단계에서는 **판별만** 여기로 모은다. 동작은 종전과 같다 — 스키마를 바꾸기 전에
'어디서 정해지는가'를 하나로 만들어 두는 것이 먼저다.

[보안]
제3자 엔드포인트를 받게 되면 그 주소로 **우리 러너가** 접속한다. 러너는 내부에 다 닿는다
(실측: 127.0.0.1:8000 → 200). 그래서 주소 검증(validate_endpoint)을 여기 함께 둔다 —
등록을 열기 전에 반드시 지나야 하는 문이다.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

# 코드가 아는 실행 종류. 여기 없는 값은 실행하지 않는다(모르는 엔진을 추측으로 돌리지 않는다).
CLAUDE = "claude"
CODEX = "codex"
KINDS = (CLAUDE, CODEX)


def runtime_kind(model):
    """이 모델 값이 어느 엔진으로 도는가. 빈 값·미지정은 기본(claude).

    codex 경로는 역사적으로 `gpt-` 접두사로 갈렸다. 그 규칙 자체는 유지하되 판단은 여기 한
    곳에서만 한다 — 호출부가 문자열을 다시 뜯어보지 않게.
    """
    m = str(model or "").strip().lower()
    if m.startswith("gpt-"):
        return CODEX
    return CLAUDE


def kind_of(model, declared=""):
    """실행 종류. 웹이 알려준 것이 있으면 그것을 믿고, 없으면 문자열 규칙으로 떨어진다.

    [내 LLM 경로 수선(2026-08-01, 현준-4)] 종전엔 모델 문자열만 봤다. 그러면 내가 등록한
    LLM 이름이 'llama-3.3-70b'일 때 gpt- 접두사가 아니라서 Claude 경로로 가고, 애써 등록한
    주소는 영영 안 쓰인다 - 이름이 실행 방식을 정하는 구조라 이름을 바꾸면 경로가 바뀐다.

    실행 설정이 종류를 명시하므로 그것을 그대로 쓴다. 우리 것(claude) 말고는 전부 codex
    프로토콜로 돈다(openai_compat·relay 모두 OpenAI 호환 얼굴을 쓴다).
    """
    d = str(declared or "").strip().lower()
    if d:
        return CLAUDE if d == CLAUDE else CODEX
    return runtime_kind(model)


def is_codex(model):
    """호출부가 읽기 쉬운 형태. builder가 쓴다."""
    return runtime_kind(model) == CODEX


class EndpointError(Exception):
    """등록 주소가 안전하지 않다 — 저장도 호출도 하지 않는다."""


def _is_public_ip(raw):
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    # 사설·루프백·링크로컬(169.254.169.254 메타데이터 포함)·유니크로컬·멀티캐스트·예약 전부 거부.
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def validate_endpoint(url, resolve=True):
    """제3자 실행 엔드포인트 주소를 검증한다. 통과하면 (호스트, 해석된 주소들)을 준다.

    [왜 이렇게 좁은가] 이 주소로 접속하는 것은 봇이 아니라 **러너**다. 러너는 오늘 만든 egress
    관문의 대상이 아니다(러너 자신이 모델 API로 나가야 하므로). 그래서 주소를 그대로 믿으면
    murmur 내부 API·DB·클라우드 메타데이터로 요청을 돌릴 수 있다(SSRF).

    · https만 — 평문이면 프롬프트가 그대로 흐른다
    · 이름을 해석해 사설·루프백·링크로컬이면 거부
    · 저장 때만이 아니라 **호출 때도** 다시 부른다 — 저장 시 공인, 호출 시 사설로 바꾸는
      우회(DNS rebinding)를 막는다
    """
    u = urlsplit(str(url or "").strip())
    if u.scheme != "https":
        raise EndpointError("엔드포인트는 https여야 한다(평문은 프롬프트가 그대로 흐른다)")
    if not u.hostname:
        raise EndpointError("엔드포인트에 호스트가 없다")
    if u.username or u.password:
        raise EndpointError("주소에 자격증명을 담지 않는다(금고에 둔다)")
    host = u.hostname
    # 주소 리터럴이면 그 자리에서 판정한다.
    try:
        ipaddress.ip_address(host)
        if not _is_public_ip(host):
            raise EndpointError(f"내부 주소는 등록할 수 없다: {host}")
        return host, [host]
    except ValueError:
        pass
    if not resolve:
        return host, []
    try:
        infos = socket.getaddrinfo(host, u.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise EndpointError(f"이름을 해석할 수 없다: {host}") from e
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise EndpointError(f"이름이 어떤 주소로도 풀리지 않는다: {host}")
    bad = [a for a in addrs if not _is_public_ip(a)]
    if bad:
        raise EndpointError(f"내부 주소로 풀린다: {host} → {', '.join(bad)}")
    return host, addrs
