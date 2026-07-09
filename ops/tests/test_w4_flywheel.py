"""[W4 — BOT_ARCH_REDESIGN 2026-07-03] 개인 플라이휠·라우팅 검증.

B-19 distill_bot + bot_profiles(직군 증류의 개인판 — [개인기준] ≤600자 영속·주입)
B-20 peers 강점 1줄(개인 기준 다이제스트 > capability ledger 상위 실적 폴백)
B-21 capability ledger(품질 게이트된 적립 — owner 정당 수임+교차검증 통과 저작만, cover 비편입 4용도)
B-22 persona 매체중립 저장소(personas.json — murmur 미러→Discord 로드, 부재 시 종전 동작)
"""
import asyncio
import json as _json

from test_sys import FakeGuide

from system.audit import AuditLog, CAP_MIN, capability_of, make_post_tool_use_hook
from system.guide_tools import Flow
from system.protocol import Kind
from system.rule.communication import _free_alternatives
from system.rule.task import _ledger_accrue
from system.sys_core import Sys, load_personas, save_personas


# ──────────────────────────── B-19 distill_bot + bot_profiles ────────────────────────────

def test_B19_개인증류_경험8건이_개인기준으로_압축·영속(tmp_path):
    """[B-19] 개인 경험(원석) 8건+ 쌓인 봇을 수면이 깨워 '개인 기준'(≤600자)으로 증류 —
    bot_profiles[mid] 영속·원석 풀 비움·별도 세션(bdistill_). distill_role 동형의 개인판."""
    g = FakeGuide()
    calls = {}

    class _Distiller:
        async def handle(self, prompt):
            calls["prompt"] = prompt
            return "[개인기준] QA\n- 소켓 e2e는 기동 대기 후 검증한다\n- 회귀는 실플레이로 끝까지\n[/개인기준]"

    def builder(oid, srv, role, flow=None, state_tag=None):
        calls["state_tag"] = state_tag
        return _Distiller()

    s = Sys(g, guild_id=1, organt_builder=builder, bot_info={11: "QA"}, session_dir=str(tmp_path))
    s.bot_experience[11] = [f"교훈{i}" for i in range(8)]
    assert s.pick_distill_bots() == [11]                      # 임계(8건) 도달 봇 선정
    ok = asyncio.run(s.distill_bot(11))
    assert ok is True
    assert "소켓 e2e" in s.bot_profiles[11]                   # 개인 기준 영속(메모리)
    assert s.bot_experience[11] == []                         # 원석 비움
    assert calls["state_tag"] == "bdistill_11"                # 작업·직군 증류 세션과 분리
    assert "교훈3" in calls["prompt"] and "600자" in calls["prompt"]   # 원석 주입 + 예산 강제
    assert any(e["event"] == "bot_distilled" for e in s.flow_log)
    saved = _json.load(open(tmp_path / "role_profiles.json", encoding="utf-8"))
    assert "소켓 e2e" in saved["bot_profiles"]["11"]          # 디스크 영속
    s2 = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "QA"},
             session_dir=str(tmp_path))
    assert "소켓 e2e" in s2.bot_profiles[11]                  # 재기동 복원(다음 wake 주입 원료)
    assert s2.pick_distill_bots() == []                       # 증류 후 대상 없음


def test_B19_임계미달·점유중이면_증류안함(tmp_path):
    """[B-19] 원석이 발동선(5건 — 2026-07-08 하향: 주입 창 6건 이하 & 압축 재료 하한) 미만이면 증류
    비발동, 그 봇이 흐름 점유 중이면 이번 주기 스킵(유휴 판정 이식)."""
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "QA"}, session_dir=str(tmp_path))
    s.bot_experience[11] = ["교훈"] * 4
    assert s.pick_distill_bots() == []                        # 임계 미달 — 후보 아님
    assert asyncio.run(s.distill_bot(11)) is False
    s.bot_experience[11] = ["교훈"] * 5
    s.engaged.engage(11, "__distill__")                       # 이미 점유 중(항상 live인 의사스코프)
    assert asyncio.run(s.distill_bot(11)) is False            # 점유 중 스킵(작업 우선·중복 증류 차단)
    s.engaged.release(11, "__distill__")


def test_B19_개인기준이_원시6줄을_대체주입·자수비증가(tmp_path):
    """[B-19·격리] _craft_note: 증류된 개인 기준이 있으면 '당신의 직무 기준'으로 주입하고 원시 경험
    줄들을 *대체*한다(≤600자 — 원시 6줄 대비 자수 비증가). 증류 전이면 원시 주입 그대로."""
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "QA"}, session_dir=str(tmp_path))
    long = ["아주 긴 개인 교훈 줄 " * 10 for _ in range(6)]
    s.bot_experience[11] = long
    before = s._craft_note(11)
    assert "당신의 최근 경험" in before                        # 증류 전 — 원시 주입(종전 동작)
    s.bot_profiles[11] = "핵심 원칙: 실측 우선"
    after = s._craft_note(11)
    assert "당신의 직무 기준" in after and "실측 우선" in after  # 개인 기준 = 유일한 직무 기준으로 주입
    assert "당신의 최근 경험" not in after                      # 원시 줄 대체(중복 지불 없음)
    assert len(after) <= len(before)                           # 자수 비증가(토큰 중립~순이득)


def test_B19_구스키마_role_profiles_로드_하위호환(tmp_path):
    """[B-19·B-21] bot_profiles·capability_ledger 키가 없는 구 role_profiles.json도 그대로 열린다
    (관용 로드 — 신설 키는 빈 상태로 시작, 기존 profiles/experience 복원 무손실)."""
    (tmp_path / "role_profiles.json").write_text(_json.dumps(
        {"profiles": {"QA": "기준"}, "experience": {"QA": ["교훈"]},
         "bot_experience": {"11": ["개인 교훈"]}}, ensure_ascii=False), encoding="utf-8")
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "QA"}, session_dir=str(tmp_path))
    assert s.role_profiles["QA"] == "기준" and s.bot_experience[11] == ["개인 교훈"]
    assert s.bot_profiles == {} and s.capability_ledger == {}   # 신설 키 부재 = 빈 시작(하위호환)


# ──────────────────────────── B-20 peers 강점 1줄 ────────────────────────────

def test_B20_peers에_개인기준_다이제스트_ledger실적_폴백():
    """[B-20] 동료 목록에 봇별 강점 1줄 — 개인 기준 다이제스트(첫 줄) 우선, 없으면 capability ledger
    상위 '검증된 실적'(임계치 이상) 폴백. RULE_SPEC §11(4) 수렴 — 공급만, 선택 판단은 리더 몫."""
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None,
            bot_info={11: "리더", 12: "백엔드", 13: "QA", 14: "프론트엔드"})
    s.bot_profiles[12] = "- 실데이터 파이프라인을 끝까지 검증한다\n- 둘째 줄"
    s.capability_ledger[13] = {"배포·인프라(DevOps)": 5}
    s.capability_ledger[14] = {"배포·인프라(DevOps)": 1}        # 임계(3) 미달 — 표면화 금지
    p = s._prompt("게임 만들어줘", Kind.WORK, "leader", 11, leader_id=11)
    assert "강점: 실데이터 파이프라인을 끝까지 검증한다" in p    # ① 개인 기준 다이제스트(첫 줄)
    assert "검증된 실적: 배포·인프라(DevOps) 저작 5건" in p      # ② ledger 폴백(임계 이상만)
    assert "저작 1건" not in p                                  # 임계 미달은 침묵(우연 저작 배제)


def test_B20_데이터없으면_동료목록_종전그대로():
    """[B-20 회귀] bot_profiles·ledger가 빈 초기 상태면 peers 문자열은 종전과 동일 — 증가분 0(무중단)."""
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "리더", 12: "백엔드"})
    p = s._prompt("게임 만들어줘", Kind.WORK, "leader", 11, leader_id=11)
    assert "12(백엔드)" in p and "강점" not in p and "검증된 실적" not in p


# ──────────────────────────── B-21 capability ledger ────────────────────────────

def test_B21_확장자·run도메인·배포이력_매핑표():
    """[B-21 구현 명세] 확장자→_CAPS 4능력 매핑 + run 도메인 키워드 + 배포 이력 — 증거 분류 순수 함수."""
    assert capability_of("Write", {"file_path": "model.pkl"}) == "AI/ML(모델 학습·예측)"
    assert capability_of("Edit", {"file_path": "data/rows.csv"}) == "실데이터 수집·파이프라인"
    assert capability_of("Write", {"file_path": "schema.sql"}) == "데이터 영속·DB"
    assert capability_of("Write", {"file_path": "Dockerfile"}) == "배포·인프라(DevOps)"   # 무확장 이름
    assert capability_of("mcp__guide__run", {"command": "docker build -t app ."}) == "배포·인프라(DevOps)"
    assert capability_of("mcp__guide__deploy", {}) == "배포·인프라(DevOps)"               # 배포 이력
    assert capability_of("Write", {"file_path": "app.py"}) == "백엔드·API 구현"           # [2026-07-08 장부 공백 교정]
    assert capability_of("Write", {"file_path": "readme.md"}) is None                     # 범주 밖 — 증거 아님
    assert set(CAP_MIN) == {"AI/ML(모델 학습·예측)", "실데이터 수집·파이프라인",
                            "데이터 영속·DB", "배포·인프라(DevOps)",
                            "웹 프론트엔드 구현", "백엔드·API 구현", "품질 검증(QA)"}      # 임계치 전수(+3범주)


def test_B21_PostToolUse가_행위자별_증거수집(tmp_path):
    """[B-21] audit PostToolUse 훅이 능력 증거를 flow.cap_evidence[actor]에 누계(writes_by_role 관례의
    능력판) — 여기선 관측만, 영속 적립은 complete_task 품질 게이트 뒤."""
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11, bot_info={11: "L", 12: "백엔드"})
    hook = make_post_tool_use_hook(AuditLog(tmp_path / "a.jsonl"), actor=12, role="백엔드", flow=f)
    asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "etl/rows.csv"}}, "t1", None))
    asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "etl/more.csv"}}, "t2", None))
    asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "app.py"}}, "t3", None))
    assert f.cap_evidence[12]["실데이터 수집·파이프라인"] == 2   # 범주 증거만 누계(.py는 비증거)


class _T:
    def __init__(self, owner=12, delivered=True, cc=1):
        self.owner, self.owner_delivered, self.cross_checks = owner, delivered, cc


class _F:
    def __init__(self, current, ev):
        self.current, self.cap_evidence, self.earned = current, ev, []
        self.persist_capability = lambda mid, e: self.earned.append((mid, e))


def test_B21_적립은_owner정당수임_교차검증통과만_흡수형은_0():
    """[B-21 핵심 — 능력 세탁 차단(부록 A-5)] 적립 조건: owner_delivered + cross_checks>0 Task의
    **owner 본인 저작**만. ① 흡수형(비owner) 저작 적립 0 ② 단독 마감(교차검증 0) 적립 0
    ③ owner 미인도 적립 0 — Task 경계에서 잔여 증거 전부 폐기(다음 Task로 세탁 이월 불가)."""
    ev = {12: {"실데이터 수집·파이프라인": 3}, 13: {"실데이터 수집·파이프라인": 4}}
    f = _F(_T(owner=12, delivered=True, cc=1), dict(ev))
    _ledger_accrue(f)
    assert f.earned == [(12, {"실데이터 수집·파이프라인": 3})]   # owner 몫만 적립
    assert f.cap_evidence == {}                                  # 비owner(13, 흡수형) 증거 폐기 = 적립 0
    f2 = _F(_T(owner=12, delivered=True, cc=0), dict(ev))        # 단독 마감(교차검증 0)
    _ledger_accrue(f2)
    assert f2.earned == [] and f2.cap_evidence == {}             # 품질 게이트 미통과 — 적립 0
    f3 = _F(_T(owner=12, delivered=False, cc=1), dict(ev))       # owner 미인도(대리 마감형)
    _ledger_accrue(f3)
    assert f3.earned == [] and f3.cap_evidence == {}


def test_B21_영속과_관측로그_그리고_cover판정_무변경(tmp_path):
    """[B-21] _persist_capability가 role_profiles.json capability_ledger 키로 영속 + capability_earned
    로그(용도④ 관측). cover 판정(_capability_gaps)은 장부와 무관 — 라벨만 본다(게이트 완화 0 검산)."""
    from system.rule.communication import _capability_gaps
    s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={12: "백엔드"}, session_dir=str(tmp_path))
    s._persist_capability(12, {"배포·인프라(DevOps)": 2})
    s._persist_capability(12, {"배포·인프라(DevOps)": 3})
    assert s.capability_ledger[12]["배포·인프라(DevOps)"] == 5   # 누적 합산
    saved = _json.load(open(tmp_path / "role_profiles.json", encoding="utf-8"))
    assert saved["capability_ledger"]["12"]["배포·인프라(DevOps)"] == 5
    assert any(e["event"] == "capability_earned" for e in s.flow_log)
    # cover 판정 무변경: ledger에 DevOps 실적 5건이 있어도 라벨(백엔드)이 못 덮으면 갭 그대로.
    gaps = _capability_gaps("쿠버네티스 인프라 구축", ["백엔드"])
    assert "배포·인프라(DevOps)" in gaps                          # 실적이 게이트를 완화하지 않음


def test_B21_free_alternatives가_실적후보_나열_판정아님():
    """[B-21 용도②] 점유 거부 안내문에 '검증된 실적 보유 후보'를 정보로만 나열(임계 이상, 판정 아님).
    장부가 비면 종전 문구 그대로(무중단)."""
    g = FakeGuide()
    f = Flow(g, channel_id=500, guild_id=1, leader_id=11,
             bot_info={11: "리더", 12: "데브옵스", 13: "백엔드"})
    base = _free_alternatives(f, 11, 12)                          # 장부 없음 — 종전 동작
    assert "검증된 실적 보유 후보" not in base
    f.capability_ledger = {13: {"배포·인프라(DevOps)": 4}}
    out = _free_alternatives(f, 11, 12)
    assert "검증된 실적 보유 후보(참고 정보 — 판정 아님)" in out
    assert "배포·인프라(DevOps) 4건" in out and "id 13" in out


def test_B21_recommend_투영_ledger정합_가산_키없으면_불변():
    """[B-21 용도③] recommend.score_candidates가 capability_ledger를 질의 정합으로 가산 — 같은 직군
    두 후보 중 검증된 실적 보유자가 앞선다. 키 없는 후보군은 기존 점수 그대로(회귀 0)."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "sns_recommend", "/root/ClaudeCompany/murmur/backend/sns/recommend.py")   # 라이브(은퇴 PJT 아님)
    rec = _ilu.module_from_spec(spec)
    spec.loader.exec_module(rec)
    base = [{"bot_id": 1, "name": "a", "role": "백엔드", "event_count": 1},
            {"bot_id": 2, "name": "b", "role": "백엔드", "event_count": 1}]
    out0 = rec.score_candidates("배포 인프라 구축", [dict(c) for c in base])
    assert out0[0]["score"] == out0[1]["score"]                   # 동률(장부 없음 = 종전)
    withled = [dict(base[0]), dict(base[1], capability_ledger={"배포·인프라(DevOps)": 5})]
    out1 = rec.score_candidates("배포 인프라 구축", withled)
    assert out1[0]["bot_id"] == 2 and out1[0]["score"] > out1[1]["score"]   # 실적 보유자 우선 투영


# ──────────────────────────── B-22 persona 매체중립 저장소 ────────────────────────────

def test_B22_personas_json_저장·로드_라운드트립(tmp_path):
    """[B-22] save_personas(murmur 러너 DB→JSON 미러)와 load_personas(Discord 러너)가 스키마
    {"personas": {봇id: persona}}로 왕복 — 빈 persona는 저장 안 함(노이즈 컷)."""
    save_personas(str(tmp_path), {111: "신중하고 보안에 집착", 222: "  ", 333: "유쾌한 낙관주의자"})
    data = _json.load(open(tmp_path / "personas.json", encoding="utf-8"))
    assert data == {"personas": {"111": "신중하고 보안에 집착", "333": "유쾌한 낙관주의자"}}
    assert load_personas(str(tmp_path)) == {111: "신중하고 보안에 집착", 333: "유쾌한 낙관주의자"}


def test_B22_파일부재·손상은_빈map_종전동작(tmp_path):
    """[B-22 무중단] personas.json 부재(미러 이전 환경·Discord 미기동)·손상 시 빈 map — 빌더는
    persona_map 없이 종전 동작(system_prompt 불변) 폴백."""
    assert load_personas(str(tmp_path)) == {}                     # 부재
    (tmp_path / "personas.json").write_text("{깨진 json", encoding="utf-8")
    assert load_personas(str(tmp_path)) == {}                     # 손상
    assert load_personas("") == {}                                # session_dir 없음


def test_B22_미러_로드_빌더_전달로_Discord_인격이_프롬프트에(tmp_path):
    """[B-22 전달 경로] murmur 러너 미러(save) → Discord 러너 로드(load) → _make_builder(persona_map)
    체인으로 그 봇의 system_prompt에 인격이 실린다(빌더 시그니처 기수용 — builder.py 무변경).
    Discord 라이브는 이 VPS에서 비검증(ARCHITECTURE §6) — 단위 테스트 한정."""
    from system.config import Config
    from organt_discord.main import _make_builder
    (tmp_path / "logs").mkdir()
    save_personas(str(tmp_path / "logs"), {111: "너는 신중하고 보안에 집착한다"})
    persona_map = load_personas(str(tmp_path / "logs"))           # Discord 러너 경로와 동일 소스
    cfg = Config(system_bot_token="x", channel_id=1, model="sonnet",
                 workspace_dir=tmp_path, audit_log_path=tmp_path / "logs" / "audit.jsonl")
    builder = _make_builder(cfg, AuditLog(cfg.audit_log_path), {111: "백엔드", 222: "QA"},
                            persona_map=persona_map)
    assert "신중하고 보안에 집착" in (builder(111, {}, "백엔드").options.system_prompt or "")
    assert "신중하고 보안에 집착" not in (builder(222, {}, "QA").options.system_prompt or "")
