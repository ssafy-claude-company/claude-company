import asyncio
from unittest.mock import patch

import requests

from guide.murmur_guide import MurmurGuide


class _RetryResponse:
    content = b"{}"

    def __init__(self, fail, body):
        self.fail = fail
        self.body = body

    def raise_for_status(self):
        if self.fail:
            raise requests.ConnectionError("response lost after commit")

    def json(self):
        return self.body


def test_http_unpick_response_loss_reuses_one_operation_id():
    """첫 POST가 서버에 적용된 뒤 응답만 유실돼도 재전송은 카운터를 한 번만 올린다."""
    guide = MurmurGuide("https://murmur.invalid", "test-token")
    guide._claim_tokens[41] = "claim-generation"

    class _Session:
        def __init__(self):
            self.calls = []
            self.applied = set()
            self.repick_n = 0
            self.start_retry_n = 0

        def post(self, _url, json, timeout):
            body = dict(json)
            self.calls.append(body)
            op_id = body["op_id"]
            if op_id not in self.applied:
                self.applied.add(op_id)
                self.repick_n += 1
                if body.get("start_retry"):
                    self.start_retry_n += 1
            return _RetryResponse(
                fail=len(self.calls) == 1,
                body={"ok": True, "claimed": False},
            )

    session = _Session()
    guide._s = session
    with patch("guide.murmur_guide.time.sleep", return_value=None):
        assert asyncio.run(guide.pick(41, unpick=True, start_retry=True)) is True

    assert len(session.calls) == 2
    assert session.calls[0]["op_id"] == session.calls[1]["op_id"]
    assert session.calls[0]["claim_token"] == session.calls[1]["claim_token"]
    assert session.repick_n == 1
    assert session.start_retry_n == 1
    assert 41 not in guide._claim_tokens


def test_http_claim_response_loss_reuses_one_generation():
    """claim 커밋 뒤 응답 유실도 같은 세대로 재전송돼 이중 흐름이 되지 않는다."""
    guide = MurmurGuide("https://murmur.invalid", "test-token")

    class _Session:
        def __init__(self):
            self.calls = []
            self.applied = set()

        def post(self, _url, json, timeout):
            body = dict(json)
            self.calls.append(body)
            self.applied.add(body["claim_token"])
            return _RetryResponse(
                fail=len(self.calls) == 1,
                body={"ok": True, "claimed": True},
            )

    session = _Session()
    guide._s = session
    with patch("guide.murmur_guide.time.sleep", return_value=None):
        assert asyncio.run(guide.pick(52)) is True

    assert len(session.calls) == 2
    assert session.calls[0]["claim_token"] == session.calls[1]["claim_token"]
    assert len(session.applied) == 1
    assert guide._claim_tokens[52] == session.calls[0]["claim_token"]
