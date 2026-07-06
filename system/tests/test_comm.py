"""브레인 검증 — 베턴+요청 스택 상태기계·busy 가드·상신·Redo·전역 점유(Engagement)·comm 헬퍼·Flow 마감.

PJT tests/test_baton.py·test_busy_escalate.py 전체와 test_sys.py 순수부(전역 점유·capability 헬퍼·
close_flow)를 pytest → 표준 unittest로 번역 포팅.

실행:
  cd /root/murmur-stack && PYTHONPATH=/root/murmur-stack \
  /root/murmur-stack/.venv/bin/python -m unittest discover -s system/tests -t /root/murmur-stack -v
"""
import unittest

from system.guide_tools import Flow, _capability_gaps, _needed_caps_coverage, _norm_job
from system.protocol import Kind
from system.rule.communication import (BusyInOtherFlow, CommError,
                                       CommunicationManager, Engagement,
                                       RedoLimitExceeded)
from system.sys_core import Sys

A, B, C = 1, 2, 3


class FakeGuide:
    """오프라인 Guide 대역 — 게시·상태 갱신 호출만 기록한다(PJT test_sys.py와 동일 최소 구성)."""

    def __init__(self):
        self.calls = []

    async def post(self, ch, sender, content, reply_to=None):
        self.calls.append(("post", ch, sender, content))
        return "m1"

    async def create_project_channel(self, gid, name):
        self.calls.append(("create_channel", name))
        return 9001

    async def open_task(self, ch, status):
        self.calls.append(("open_task", ch, status.purpose))
        return "blk", "thr"

    async def update_status(self, ch, blk, status):
        self.calls.append(("update", status.status))
        return blk

    async def send_request(self, thr, sender, to, kind, body):
        self.calls.append(("req", sender, to, body))
        return "reqid"

    async def send_response(self, thr, sender, req, body):
        self.calls.append(("resp", sender, body))
        return "respid"

    async def send_file(self, channel_id, path, sender_id=0, caption=""):
        self.calls.append(("file", channel_id, path, sender_id, caption))
        return "fileid"


def _flow(g, leader=11):
    f = Flow(g, channel_id=500, guild_id=1, leader_id=leader, bot_info={11: "L", 12: "M"})
    f.start_root("root")
    f.gap_checked = True   # 각 게이트 보류는 전용 테스트 영역 — 상태기계 검증에선 기본 우회
    f.percept_checked = True
    f.acceptance_checked = True
    f.decomp_checked = True
    f.data_prov_checked = True
    f.staffing_exempt = True
    f.iface_dialogue_checked = True
    f._parallel_enabled = True
    f.offdomain_checked = True
    f.crossdomain_checked = True
    return f


class BatonStackTest(unittest.TestCase):
    """기능12: 베턴 + 요청 스택 상태기계 (A→B→C 역순 close → 시작점 복귀)."""

    def test_단일_요청_응답_후_시작점_종료(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        assert m.is_alive(B) and not m.is_alive(A)   # receiver wake, sender sleep
        m.respond(B)
        assert m.done and m.is_alive(A) and m.open_requests == []

    def test_3단_요청후_역순_close_시작점복귀(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")   # 활성 B
        m.request(B, C, "r2")   # 활성 C
        assert m.is_alive(C) and len(m.open_requests) == 2

        m.respond(C)            # close r2(B→C) → 활성 B
        assert m.is_alive(B) and len(m.open_requests) == 1 and not m.done
        m.respond(B)            # close r1(A→B) → 시작점 A 복귀, 종료
        assert m.done and m.is_alive(A) and m.open_requests == []

        # 역순(C→B→A)으로 닫혔는지: respond 이벤트 순서 r2 먼저, r1 나중
        responds = [h for h in m.history if h[0] == "respond"]
        assert responds[0][3] == "r2" and responds[1][3] == "r1"

    def test_활성아닌_Organt는_요청불가(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")   # 활성 B
        with self.assertRaises(CommError):
            m.request(A, C, "x")  # A는 자고 있음 → 불가

    def test_활성아닌_Organt는_응답불가(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")   # 활성 B
        with self.assertRaises(CommError):
            m.respond(A)          # 활성은 B인데 A가 응답 시도

    def test_열린요청_없으면_응답불가(self):
        m = CommunicationManager(A)
        with self.assertRaises(CommError):
            m.respond(A)

    def test_종료후_추가요청_불가(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.respond(B)
        assert m.done
        with self.assertRaises(CommError):
            m.request(A, B, "r2")

    # ── 상류 선행작업 되감기(report_up_to) — 임의 깊이·임의 대상 일반형 ────────────────
    # A→B→C에서 C가 A에게 Work를 요청 = 선행작업 미완 신호 → 막다른 거부 대신 보고체계로 되감는다.

    def test_상류보고_루트까지_3단(self):
        """A→B→C에서 C가 A(루트)로 되감기 → alive=A·종료, 서브체인 A→B→C 보존(owner→…→reporter)."""
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.request(B, C, "r2")
        assert m.is_alive(C)
        sub = m.report_up_to(C, A, "A 선행작업 필요")
        assert m.is_alive(A) and m.done                       # 루트까지 되감김 → 시작점 복귀
        assert [(s["from"], s["to"]) for s in sub] == [(A, B), (B, C)]

    def test_상류보고_부분되감기_중간주인_5단(self):
        """A→B→C→D→E에서 E가 B(중간)에게 보고: C·D relay, alive=B, A→B 유지(부분 되감기)."""
        D, E = 4, 5
        m = CommunicationManager(A)
        for frm, to, r in [(A, B, "r1"), (B, C, "r2"), (C, D, "r3"), (D, E, "r4")]:
            m.request(frm, to, r)
        assert m.is_alive(E) and len(m.open_requests) == 4
        sub = m.report_up_to(E, B, "B 선행작업 필요")
        assert m.is_alive(B) and not m.done                   # 중간 주인 → 부분 되감기(흐름 안 끝남)
        assert [(f.from_id, f.to_id) for f in m.open_requests] == [(A, B)]   # owner 위 프레임만 남음
        assert [(s["from"], s["to"]) for s in sub] == [(B, C), (C, D), (D, E)]

    def test_상류보고_루트까지_5단(self):
        """A→B→C→D→E에서 E가 A(루트)에게 보고 → 끝까지 되감김(alive=A·종료), 전체 경로 보존."""
        D, E = 4, 5
        m = CommunicationManager(A)
        for frm, to, r in [(A, B, "r1"), (B, C, "r2"), (C, D, "r3"), (D, E, "r4")]:
            m.request(frm, to, r)
        sub = m.report_up_to(E, A)
        assert m.is_alive(A) and m.done and m.open_requests == []
        assert [(s["from"], s["to"]) for s in sub] == [(A, B), (B, C), (C, D), (D, E)]

    def test_상류보고_비상류_대상은_거부(self):
        """보고 대상이 상류(ancestor)가 아니면 거부 — 되감기는 위로만."""
        D = 4
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.request(B, C, "r2")            # alive=C, ancestors={A,B}
        with self.assertRaises(CommError):
            m.report_up_to(C, D)        # D는 상류가 아님

    def test_상류보고_활성아닌_보고자_거부(self):
        """활성(베턴 보유)인 워커만 상류 보고 가능 — 자고 있는 동료는 보고 못 함."""
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.request(B, C, "r2")            # alive=C
        with self.assertRaises(CommError):
            m.report_up_to(B, A)        # 활성은 C인데 B가 보고 시도

    # ── 정밀 복구: 체인 내부 복원(restore_chain) — 평탄화 없이 가장 깊은 워커부터 재개 ──────

    def test_정밀복구_체인내부복원_가장깊은워커재개_자연unwind(self):
        """active_chain(A→B→C→D)을 채팅 재발행 없이 스택으로 복원하고 가장 깊은 D부터 재개.
        끝나면 respond가 D→C→B→A로 자연 unwind — 각자 범위 보존(평탄화로 중간 안 빼먹음)."""
        D = 4
        m = CommunicationManager(A)
        frames = [                                      # 위→아래 순(active_chain 형태)
            {"from": A, "to": B, "kind": "work", "body": "A→B 원문"},
            {"from": B, "to": C, "kind": "work", "body": "B→C 원문"},
            {"from": C, "to": D, "kind": "work", "body": "C→D 원문"},
        ]
        deepest = m.restore_chain(frames)
        assert deepest == D and m.is_alive(D)           # 가장 깊은 워커부터 재개
        assert len(m.open_requests) == 3 and not m.done  # 체인 그대로 복원
        assert m.open_requests[-1].body == "C→D 원문"    # 끊긴 그 깊이의 원문 보존
        # 끝났을 때 자연 unwind — 각자 범위 보존
        m.respond(D); assert m.is_alive(C) and not m.done   # D 완료 → C가 통합(C 범위)
        m.respond(C); assert m.is_alive(B) and not m.done   # C 완료 → B가 통합(B 범위)
        m.respond(B); assert m.is_alive(A) and m.done       # B 완료 → A 복귀·종료

    def test_정밀복구_체인내부복원_종료흐름은거부(self):
        """종료된 흐름엔 체인 복원 불가(유령 복원 차단)."""
        m = CommunicationManager(A)
        m.request(A, B, "r1"); m.respond(B)
        assert m.done
        with self.assertRaises(CommError):
            m.restore_chain([{"from": A, "to": B, "kind": "work", "body": "x"}])


class BusyEscalateRedoTest(unittest.TestCase):
    """기능13: busy 가드(증명②) + 상신(증명③) + Accept/Redo + delivered_work."""

    def test_busy_Organt에_Work요청_거부(self):
        D = 4
        m = CommunicationManager(A)
        m.request(A, B, "r1")        # 활성 B, 참여 {A,B}
        m.request(B, C, "r2")        # 활성 C, 참여 {A,B,C}
        assert m.is_busy(B) and m.is_busy(A)
        with self.assertRaises(CommError):
            m.request(C, B, "x")     # B는 미완 Work 보유 → 거부
        assert not m.is_busy(D)

    def test_새_Organt에는_요청가능(self):
        D = 4
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.request(B, C, "r2")
        assert not m.is_busy(D)
        f = m.request(C, D, "r3")    # D는 신규 → OK
        assert f.to_id == D and m.is_alive(D)

    def test_상위동료_되묻기_재진입금지_Info도(self):
        # 상위(응답 대기 중) 동료에겐 Info조차 되물을 수 없다(재진입 방지). 신규 동료는 가능.
        D = 4
        m = CommunicationManager(A)
        m.request(A, B, "r1")        # 스택 [A→B]
        m.request(B, C, "r2")        # 스택 [A→B, B→C], 활성 C
        with self.assertRaises(CommError):
            m.check_request(C, B, "info")   # B는 C 응답 대기 중 → 금지
        with self.assertRaises(CommError):
            m.check_request(C, A, "info")   # A(조상)도 대기 중 → 금지
        m.check_request(C, D, "info")       # 멈춰있지 않은 신규 동료엔 Info OK(예외 없음)

    def test_B가_멈추면_상신되어_교착없이_종료(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")        # 활성 B
        m.escalate("B 타임아웃")       # B 멈춤 → 강제 close + 상신
        assert m.done and m.is_alive(A) and m.escalated_to_origin

    def test_중간Organt_멈춤_깊은체인_상신(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        m.request(B, C, "r2")        # 활성 C
        m.respond(C)                 # 활성 B (B가 A에 응답해야)
        m.escalate("B 멈춤")          # B 멈춤 → A→B 강제 close, A로 상신
        assert m.done and m.is_alive(A) and m.escalated_to_origin
        assert "B 멈춤" in m.escalations[-1][1]

    def test_redo_한계내_재요청_후_초과시_상신신호(self):
        m = CommunicationManager(A, redo_limit=2)
        m.request(A, B, "r1")        # 활성 B
        m.request(B, C, "r2")        # 활성 C
        m.respond(C, "redo")         # 활성 B (B 불만족)
        m.redo(B, C, "r2a")          # redo 1 → 활성 C
        m.respond(C, "redo")
        m.redo(B, C, "r2b")          # redo 2 → 활성 C
        m.respond(C, "redo")
        with self.assertRaises(RedoLimitExceeded):
            m.redo(B, C, "r2c")      # redo 3 > 2 → 상신 필요

    def test_accept_응답은_정상_close(self):
        m = CommunicationManager(A)
        m.request(A, B, "r1")
        f = m.respond(B, "accept", "완료")
        assert m.done and f.request_id == "r1"

    def test_delivered_work_완료응답쌍_기록과_reset(self):
        """Work가 '완료(accept) 응답'까지 닫힌 (위임자→owner) 쌍만 delivered — 재위임=Redo 판별 근거.
        redo 응답은 인도가 아니고, reset_task_tracking(새 Task)이면 추적이 비워진다."""
        m = CommunicationManager(A)
        m.request(A, B, "r1", Kind.WORK)
        assert not m.delivered_work(A, B)                 # 아직 미인도
        m.respond(B, "accept", "완료")
        assert m.delivered_work(A, B)                     # 완료 인도 기록
        assert not m.delivered_work(B, A)                 # 방향성 있음
        m2 = CommunicationManager(A)
        m2.request(A, B, "r1", Kind.WORK)
        m2.respond(B, "redo")                             # 불만족 응답 → 인도 아님
        assert not m2.delivered_work(A, B)
        m.reset_task_tracking()                           # 새 Task(새 산출물 단위) → 추적 초기화
        assert not m.delivered_work(A, B)


class EngagementTest(unittest.TestCase):
    """전역 점유(Engagement) — '흐름 수 상한'을 대체하는 구조적 병렬 안전 + 자가치유."""

    def test_전역점유_타흐름_동료는_Kind불문_차단_응답시_즉시해제(self):
        """한 직원(봇)은 한 시점에 한 흐름에만 참여한다 — Work는 물론 Info도 타 흐름 점유 중엔
        차단(이중 존재 방지; 흐름 안의 Info는 종전대로). 응답을 마친 봇은 즉시 회사 풀로 돌아간다."""
        eng = Engagement()
        a = CommunicationManager(0)
        a.attach_engagement(eng, "P-A")
        b = CommunicationManager(0)
        b.attach_engagement(eng, "P-B")
        a.request(0, 11, "ra", Kind.WORK)                  # A 리더 점유
        a.request(11, 13, "r1", Kind.WORK)                 # 13은 A에서 작업 중
        b.request(0, 12, "rb", Kind.WORK)                  # 리더가 다르면 흐름은 동시 진행
        assert eng.holder(11) == "P-A" and eng.holder(13) == "P-A" and eng.holder(12) == "P-B"
        with self.assertRaises(BusyInOtherFlow):
            b.check_request(12, 13, Kind.WORK)
        with self.assertRaises(BusyInOtherFlow):
            b.check_request(12, 13, Kind.INFO)
        a.respond(13, "accept")                            # 응답 완료 → 즉시 해제
        assert eng.holder(13) is None
        b.request(12, 13, "r2", Kind.INFO)                 # 이제 B가 쓸 수 있다
        assert eng.holder(13) == "P-B"
        b.respond(13, "accept")
        a.respond(11, "accept")
        b.respond(12, "accept")
        assert eng.holder(11) is None and eng.holder(12) is None   # 흐름 종료 → 전원 해제

    def test_전역점유_상신_강제정리도_해제대칭(self):
        """escalate(타임아웃·복구의 강제 close)도 respond와 같은 지점에서 점유를 해제한다 —
        복구 경로에서 봇이 '바쁨'으로 영구히 굳는 누수가 구조적으로 없다."""
        eng = Engagement()
        a = CommunicationManager(0)
        a.attach_engagement(eng, "P-A")
        a.request(0, 11, "ra", Kind.WORK)
        a.request(11, 13, "r1", Kind.WORK)
        a.escalate("타임아웃 정리")
        assert eng.holder(13) is None and eng.holder(11) == "P-A"   # 13만 풀리고 리더는 계속
        a.escalate("종료 정리")
        assert a.done and eng.holder(11) is None                    # origin 복귀 → 전원 해제

    def test_전역점유_유령점유_자가치유(self):
        """장부는 인메모리 + 조회 시 스코프 생존 검사 — 끝난/죽은 흐름의 점유는 holder() 조회 순간
        스스로 지워진다(예외로 해제가 누락돼도 봇이 영구 '바쁨'으로 굳지 않음)."""
        eng = Engagement(is_live=lambda s: s == "LIVE")
        eng.engage(7, "DEAD")
        assert eng.holder(7) is None                       # 죽은 스코프 → 자가 치유
        assert not eng.busy_elsewhere(7, "LIVE")
        eng.engage(7, "LIVE")
        assert eng.busy_elsewhere(7, "OTHER") and not eng.busy_elsewhere(7, "LIVE")
        eng.release_scope("LIVE")
        assert eng.holder(7) is None


class CommHelperTest(unittest.TestCase):
    """팀·역량 라우팅 comm 헬퍼(순수 함수) — _capability_gaps·_needed_caps_coverage·_norm_job."""

    def test_capability_gaps_일반화_데이터_DevOps_DBA_커버리지(self):
        """능력 커버리지 일반화(_CAPS): 목표가 그 능력을 실질 축으로 요구하는데 팀이 아무도 못
        덮으면 갭. 고신호만(과채용 방지): 평범한 '웹 배포'엔 DevOps 갭 안 걸리고, 백엔드가 있으면
        기본 DB(DBA)는 cover."""
        # 기존 AI/ML 거동 보존
        assert _capability_gaps("AI를 학습시키고 예측 웹", ["백엔드", "프론트엔드"]) == ["AI/ML(모델 학습·예측)"]
        assert _capability_gaps("AI를 학습시키고", ["백엔드", "AI 엔지니어"]) == []
        assert _capability_gaps("스네이크 게임 만들어줘", ["백엔드"]) == []
        # 실데이터 수집·파이프라인 — 공공/실데이터 + 취득동사일 때(백엔드는 cover 아님)
        assert "실데이터 수집·파이프라인" in _capability_gaps("공공데이터를 받아와 통계 사이트", ["백엔드", "프론트엔드"])
        assert "실데이터 수집·파이프라인" not in _capability_gaps("공공데이터를 받아와 통계", ["데이터 엔지니어"])
        # 반복 수요('공공데이터로 AI 학습 웹')는 AI/ML + 데이터 두 갭을 동시에 — 두 전문가 협업 강제
        assert set(_capability_gaps("공공데이터를 활용해서 AI를 학습시키고 웹사이트", ["백엔드", "프론트엔드"])) == {
            "AI/ML(모델 학습·예측)", "실데이터 수집·파이프라인"}
        # 데이터 영속·DB — 백엔드·DBA가 둘 다 없을 때만 갭
        assert "데이터 영속·DB" in _capability_gaps("회원가입 로그인 계정 기록 저장", ["프론트엔드"])
        assert "데이터 영속·DB" not in _capability_gaps("회원가입 로그인 계정", ["백엔드"])
        # 배포·인프라(DevOps) — 명시적 인프라 수요에만
        assert "배포·인프라(DevOps)" in _capability_gaps("CI/CD 파이프라인 구축, 쿠버네티스 오토스케일", ["백엔드"])
        assert "배포·인프라(DevOps)" not in _capability_gaps("웹사이트 만들어서 배포해줘", ["백엔드"])
        assert "배포·인프라(DevOps)" not in _capability_gaps("CI/CD 파이프라인 구축", ["DevOps"])
        # 평범한 게임/웹엔 새 갭 없음(과발동 방지)
        assert _capability_gaps("오버워치 같은 게임 만들어줘", ["게임 기획자", "프론트엔드"]) == []

    def test_needed_caps_coverage_필요능력별_커버수(self):
        """필요 능력(need True)별 '덮는 팀원 수' — 협업 깊이 게이트의 판정 입력."""
        assert _needed_caps_coverage("공공데이터 받아와 AI 학습", ["AI 엔지니어", "데이터 엔지니어"]) == {
            "AI/ML(모델 학습·예측)": 1, "실데이터 수집·파이프라인": 1}

    def test_norm_job_공백정규화_casefold(self):
        """직군명 비교 정규화: 연속 공백 접기 + casefold(대소문자 무시). 빈 입력은 빈 문자열."""
        assert _norm_job("  게임   기획자 ") == "게임 기획자"
        assert _norm_job("Backend") == "backend"
        assert _norm_job("QA") == _norm_job("qa")
        assert _norm_job(None) == "" and _norm_job("") == ""


class FlowCloseTest(unittest.TestCase):
    """Flow 마감(_close_flow) — 정상 close와 비정상 베턴 강제 드레인(교착 없는 종료)."""

    def test_close_flow_정상_clean_close(self):
        s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "L"})
        f = _flow(s.guide)                          # comm: [origin→11], alive=11
        s._close_flow(f, 11, "결과")
        assert f.comm.done                          # 리더가 alive → 정상 close

    def test_close_flow_비정상베턴_강제드레인(self):
        s = Sys(FakeGuide(), guild_id=1, organt_builder=None, bot_info={11: "L", 12: "M"})
        f = _flow(s.guide)
        f.comm.request(11, 12, "leak", Kind.WORK)   # 닫히지 않은 프레임 → alive=12(비정상)
        assert not f.comm.done and f.comm.alive == 12
        s._close_flow(f, 11, "결과")                # 강제 드레인
        assert f.comm.done                          # 교착 없이 종료


if __name__ == "__main__":
    unittest.main()
