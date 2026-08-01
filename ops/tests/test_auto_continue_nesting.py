"""[이어가기 안내가 스스로를 감싼다(2026-08-01, U-442 대화 전문 실측)] 한 위임이 59겹까지 중첩돼
본문이 7,000자로 불었다 — 봇은 '처음부터 다시 하지 말 것'을 59번 읽고 진짜 지시를 맨 안쪽에서 찾았다."""
from system.sys_core import _unwrap_auto_continue

WRAP = ("[SYS 자동 이어가기 — 처음부터 다시 하지 말 것] 직전에 이 작업으로 위임받았습니다(원문 그대로):\n"
        "{inner}\n\n[이어가기] 작업공간에서 이미 된 부분은 그대로 두고 남은 부분만 마저 끝내 완성하세요.")
REAL = "공개 URL의 정적 자산 MIME과 manifest를 대조하고 결과를 report_iter로 제출하세요."


def test_감싸지_않은_원문은_그대로_둔다():
    assert _unwrap_auto_continue(REAL) == REAL


def test_한겹은_벗긴다():
    assert _unwrap_auto_continue(WRAP.format(inner=REAL)) == REAL


def test_실측처럼_쉰아홉겹도_원문만_남는다():
    body = REAL
    for _ in range(59):
        body = WRAP.format(inner=body)
    assert len(body) > 6000
    assert _unwrap_auto_continue(body) == REAL


def test_인용_없는_끊김_안내는_남길_원문이_없다():
    assert _unwrap_auto_continue("[SYS 자동 이어가기 — 처음부터 다시 하지 말 것] 직전 작업이 끊겼습니다.") == ""


def test_빈값도_안전하다():
    assert _unwrap_auto_continue(None) == "" and _unwrap_auto_continue("") == ""
