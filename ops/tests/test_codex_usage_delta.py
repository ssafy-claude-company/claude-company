"""[과금 정확도 회귀(2026-07-28, U-074 실측)] codex 누계를 그대로 청구하면 과거를 다시 청구한다.

실측(같은 세션, 짧은 프롬프트 3연속): turn.completed.usage.input_tokens = 11,556 → 24,671 → 37,805.
이건 '그 턴이 쓴 양'이 아니라 **스레드 누계**다. 누계를 청구하면 N턴짜리 판이 대략 N/2배로 부풀고,
실제로 168턴 판이 3,000크레딧(전 한도)을 태웠다. 여기서 '차분만 청구'를 계약으로 고정한다.
"""
import json
from pathlib import Path

from organt.organt import Organt
from system.config import Config


def _organt(tmp_path) -> Organt:
    cfg = Config(system_bot_token="s", channel_id=1, model=None,
                 workspace_dir=Path("/tmp/ws"), audit_log_path=Path("/tmp/audit.jsonl"))
    return Organt(cfg, state_path=str(tmp_path / "state.json"))


def test_이어가는_턴은_차분만_청구된다(tmp_path):
    o = _organt(tmp_path)
    seq = [
        {"input_tokens": 11556, "cached_input_tokens": 4480, "output_tokens": 50},
        {"input_tokens": 24671, "cached_input_tokens": 15616, "output_tokens": 129},
        {"input_tokens": 37805, "cached_input_tokens": 28288, "output_tokens": 224},
    ]
    charged = [o._codex_usage_delta("sid-1", u) for u in seq]
    assert charged[0]["input_tokens"] == 11556          # 첫 턴 = 전액(그 스레드의 첫 청구)
    assert charged[1]["input_tokens"] == 13115          # 24,671 − 11,556
    assert charged[2]["input_tokens"] == 13134          # 37,805 − 24,671
    # 총 청구 = 마지막 누계와 같다(과거 재청구 0)
    assert sum(c["input_tokens"] for c in charged) == 37805
    assert sum(c["output_tokens"] for c in charged) == 224


def test_세션이_바뀌면_다시_전액(tmp_path):
    o = _organt(tmp_path)
    o._codex_usage_delta("sid-1", {"input_tokens": 30000, "output_tokens": 900})
    d = o._codex_usage_delta("sid-2", {"input_tokens": 12000, "output_tokens": 100})
    assert d["input_tokens"] == 12000 and d["output_tokens"] == 100


def test_누계가_줄면_이번_값만_청구(tmp_path):
    """세션 재생성 등으로 누계가 후퇴해도 음수 청구(=환불)나 0원 턴이 되지 않는다."""
    o = _organt(tmp_path)
    o._codex_usage_delta("sid-1", {"input_tokens": 50000, "output_tokens": 500})
    d = o._codex_usage_delta("sid-1", {"input_tokens": 900, "output_tokens": 10})
    assert d["input_tokens"] == 900 and d["output_tokens"] == 10


def test_사용량_누계는_세션과_함께_영속된다(tmp_path):
    """러너는 턴마다 Organt를 새로 만든다 — 메모리에만 두면 매 턴이 첫 턴이 되어 수리가 무효."""
    o1 = _organt(tmp_path)
    o1._codex_usage_delta("sid-1", {"input_tokens": 11556, "output_tokens": 50})
    o2 = _organt(tmp_path)                                    # 다음 턴의 새 인스턴스
    d = o2._codex_usage_delta("sid-1", {"input_tokens": 24671, "output_tokens": 129})
    assert d["input_tokens"] == 13115


def test_마이크로턴_새_스레드가_본세션_기준선을_지우지_않는다(tmp_path):
    """응찰·표결 마이크로 턴은 매번 새 스레드다 — 장부가 한 칸이면 그게 본세션 기준선을 덮어써
    다음 실질 턴이 다시 전액 청구된다(과다청구 재발)."""
    o = _organt(tmp_path)
    o._codex_usage_delta("main", {"input_tokens": 20000, "output_tokens": 300})
    o._codex_usage_delta("micro-1", {"input_tokens": 9000, "output_tokens": 20})   # 끼어든 마이크로
    d = o._codex_usage_delta("main", {"input_tokens": 33000, "output_tokens": 480})
    assert d["input_tokens"] == 13000 and d["output_tokens"] == 180


def test_세션id_저장이_사용량을_지우지_않는다(tmp_path):
    """상태 파일은 병합 저장 — session_id를 쓰면서 누계를 날리면 다시 과다청구로 돌아간다."""
    o = _organt(tmp_path)
    o._codex_usage_delta("sid-1", {"input_tokens": 11556, "output_tokens": 50})
    o._save_session_id("sid-1")
    st = json.loads(Path(o.state_path).read_text(encoding="utf-8"))
    assert st["session_id"] == "sid-1" and st["codex_usage"]["sid-1"]["input_tokens"] == 11556
    assert o._codex_usage_delta("sid-1", {"input_tokens": 24671, "output_tokens": 129})["input_tokens"] == 13115


def test_사용량이_없으면_결산도_없다(tmp_path):
    assert _organt(tmp_path)._codex_usage_delta("sid-1", {}) == {}
    assert _organt(tmp_path)._codex_usage_delta("sid-1", None) == {}
