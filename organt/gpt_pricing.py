"""GPT(codex) 턴의 토큰 → USD 환산 — 과금 원장이 GPT 봇도 계량하게 하는 유일한 자리.

Claude 경로는 SDK ResultMessage가 `total_cost_usd`를 직접 준다. codex 경로는 토큰 수만 오므로
여기서 공표 단가로 환산한다. 이 환산이 없으면 GPT 봇의 턴은 원장에 0원으로 적립돼
**크레딧이 소비되지 않고 한도(MURMUR_QUOTA_ENFORCE)도 영원히 안 걸린다** — 2026-07-28 사용자 지적.

단가는 100만 토큰당 USD (입력, 캐시된 입력, 출력). 2026-07-28 공표가 기준.
배포 없이 갱신하려면 러너 env `ORGANT_GPT_PRICES`에 JSON을 넣는다:
  ORGANT_GPT_PRICES='{"gpt-5.6-luna": [1.0, 0.1, 6.0]}'   # 부분 갱신(나머지는 기본표 유지)
"""
import json
import os

# (입력, 캐시된 입력, 출력) — USD / 1M tokens
_PRICES = {
    "gpt-5.6-sol":   (5.00, 0.50, 30.00),
    "gpt-5.6-terra": (2.50, 0.25, 15.00),
    "gpt-5.6-luna":  (1.00, 0.10, 6.00),
    "gpt-5.5":       (5.00, 0.50, 30.00),
    "gpt-5.4":       (2.50, 0.25, 15.00),
    "gpt-5.4-mini":  (0.75, 0.075, 4.50),
}
# 표에 없는 gpt-* 모델(운영자가 새 모델을 지정한 경우) — 중간 등급으로 셈한다. 0으로 두면
# '무료 모델'이 되어 무제한 소비가 되므로, 모르는 모델일수록 계량은 하되 과대청구는 피한다.
_FALLBACK = (2.50, 0.25, 15.00)


def _table():
    t = dict(_PRICES)
    raw = (os.environ.get("ORGANT_GPT_PRICES") or "").strip()
    if raw:
        try:
            for k, v in (json.loads(raw) or {}).items():
                if isinstance(v, (list, tuple)) and len(v) >= 3:
                    t[str(k).lower()] = (float(v[0]), float(v[1]), float(v[2]))
        except Exception:
            pass                     # 잘못된 JSON은 무시 — 기본표로 계속 계량한다(0원 회귀 방지)
    return t


def rates_for(model):
    """모델의 (입력, 캐시입력, 출력) 단가. 정확 일치 → 최장 접두 일치 → 폴백."""
    m = str(model or "").strip().lower()
    t = _table()
    if m in t:
        return t[m]
    best = ""
    for k in t:
        if m.startswith(k) and len(k) > len(best):
            best = k
    return t[best] if best else _FALLBACK


def estimate_cost_usd(model, usage):
    """codex usage(dict) → USD. cached_input_tokens는 input_tokens의 부분집합(OpenAI 계약)이라
    캐시분은 빼고 캐시 단가로 따로 센다. 필드가 없으면 0(과소청구 — 사용자 유리)."""
    u = usage or {}
    if not isinstance(u, dict):
        return 0.0

    def _n(*keys):
        for k in keys:
            v = u.get(k)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0

    tin = _n("input_tokens", "prompt_tokens")
    cached = min(_n("cached_input_tokens", "cache_read_input_tokens"), tin)
    tout = _n("output_tokens", "completion_tokens")
    p_in, p_cached, p_out = rates_for(model)
    return ((tin - cached) * p_in + cached * p_cached + tout * p_out) / 1_000_000.0


def usage_record(model, usage):
    """턴 결산 레코드(Organt._last_result 형태) — 없는 값은 넣지 않는다."""
    u = usage or {}
    if not isinstance(u, dict) or not u:
        return {}
    rec = {"cost_usd": round(estimate_cost_usd(model, u), 6)}
    for src, dst in (("input_tokens", "tokens_in"), ("output_tokens", "tokens_out")):
        v = u.get(src)
        if isinstance(v, (int, float)):
            rec[dst] = int(v)
    v = u.get("cached_input_tokens")
    if isinstance(v, (int, float)):
        rec["tokens_cached"] = int(v)
    return rec
