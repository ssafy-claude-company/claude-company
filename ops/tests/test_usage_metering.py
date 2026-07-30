"""계측 계약 — 기준선 유실 없이 차분만 청구하고, 감사 필드가 결산에 실린다."""
import json

from organt.organt import Organt


class _Stub(Organt):
    def __init__(self, tmp_path):
        self.state_path = tmp_path / "state.json"
        self.session_id = "MAIN"


def _u(i, c, o):
    return {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o}


def test_이어가는_턴은_차분만_청구한다(tmp_path):
    o = _Stub(tmp_path)
    assert o._codex_usage_delta("MAIN", _u(10_000, 8_000, 200))["input_tokens"] == 10_000
    d = o._codex_usage_delta("MAIN", _u(24_000, 20_000, 500))
    assert d["input_tokens"] == 14_000 and d["output_tokens"] == 300


def test_micro_스레드가_본세션_기준선을_밀어내지_않는다(tmp_path):
    """[U-079 실측] 장부가 4칸이라 응찰·표결(매번 새 스레드)이 지나가면 본 세션 기준선이 사라졌고,
    다음 작업 턴이 누계 전액을 그 턴 몫으로 청구했다(입력·출력이 턴마다 단조 증가)."""
    o = _Stub(tmp_path)
    o._codex_usage_delta("MAIN", _u(1_000_000, 900_000, 5_000))
    for i in range(12):                                  # 마이크로 턴 12회 = 새 스레드 12개
        o._codex_usage_delta(f"MICRO-{i}", _u(12_000, 11_000, 100))
    d = o._codex_usage_delta("MAIN", _u(1_100_000, 990_000, 5_400))
    assert d["input_tokens"] == 100_000, "본 세션 기준선이 유실됨(누계 전액 청구)"
    assert d["output_tokens"] == 400


def test_기준선_유실은_조용히_넘어가지_않는다(tmp_path):
    o = _Stub(tmp_path)
    o._codex_usage_delta("MAIN", _u(500_000, 400_000, 3_000))
    st = json.loads(o.state_path.read_text(encoding="utf-8"))
    st["codex_usage"] = {}                               # 장부만 소실(세션은 이미 본 적 있음)
    o.state_path.write_text(json.dumps(st), encoding="utf-8")
    o._codex_usage_delta("MAIN", _u(600_000, 500_000, 3_500))
    assert (o._usage_anomaly or {}).get("why") == "baseline_lost"


def test_감사필드가_결산에_실린다(tmp_path):
    o = _Stub(tmp_path)
    o._codex_usage_delta("MAIN", _u(10_000, 8_000, 200))
    a = o._usage_audit
    assert a["sid"] == "MAIN" and a["first_turn_of_thread"] is True and a["cum_input"] == 10_000
