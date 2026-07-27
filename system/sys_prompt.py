"""SYS 프롬프트 조립 — sys_core.Sys에서 파사드 보존 추출(LLM_DX_AUDIT 1-C ⑤).

이 모듈은 '프롬프트/노트 텍스트 조립'만 담는다(순수·읽기 전용 — Sys 상태를 바꾸지 않음).
공개 표면은 그대로 Sys 메서드(`Sys._prompt`·`Sys._craft_note` 등)이며 그 메서드들이 여기로
위임한다 — 외부(테스트·러너)는 종전 이름을 그대로 쓴다. sys_core를 import하지 않는다(단방향).
함수 첫 인자 `sys`는 Sys 인스턴스(상태 읽기 전용). Sys 메서드 호출은 `sys._X()` 경유 —
테스트 monkeypatch·서브클래스 오버라이드 의미 보존.
"""
import os
import time

from .audit import CAP_MIN


# ── [컨텍스트 큐레이션 — 구조적 강제(2026-07)] ─────────────────────────────────────
# 문제 진단: 프롬프트 조립이 하드코딩 블록의 손 concat이라 예산·dedup·우선순위가 없다. '제대로된
# 것만 주입'이 구조가 아니라 사람 규율(주석의 블록↔게이트 백스톱 대조표)에 의존 → 사고마다 블록이
# 붙어(accretion) 같은 원칙이 2~3회 반복(PRINCIPLE 실측 ~68%가 중복 재진술). 학습 컨텍스트(직군
# 기준)엔 증류('쌓기 아닌 정리')가 있는데 표준 컨텍스트엔 정리 기제가 0 — 이 비대칭이 근본 원인.
# 이 조립기가 그 정리 기제를 표준 컨텍스트에도 준다:
#   ① dedup(theme key): 같은 key는 하나만 — 새 교훈은 '새 블록'이 아니라 '기존 theme text에 병합'해야
#      통과(증류의 '겹치면 합쳐 한 원칙으로'를 구조로 강제 → 중복 재발 차단).
#   ② 우선순위+예산: 필수(정체성·핵심 규칙)부터 채우고 예산 초과분은 낮은 것(형성 문구)부터 드롭 —
#      '표기된 드롭'(로깅), 침묵 절단 금지(기억·규칙이 소리 없이 사라지지 않게).
#   ③ 논리 순서 보존: 포함 판정은 우선순위로, emit은 입력(논리) 순서로.
def assemble_context(sections, budget_chars, log=None, min_priority=0):
    """선언적 섹션 [(key, priority, text)]을 dedup·우선순위·예산으로 조립해 문자열 반환.
    priority 높을수록 필수. 같은 key는 최고 priority 하나만(dedup). 예산 초과분은 낮은 priority부터
    제외(log('context_budget_drop', ...))하되 emit 순서는 입력(논리) 순서.
    min_priority: 이 미만 우선순위는 아예 제외 — 재-wake에서 '핵심(threshold 이상)만' 주입에 쓴다
    (예산 best-fit이 낮은 섹션을 높은 것보다 끼워넣는 걸 방지하는 명시적 tier 컷)."""
    order, best = [], {}
    for key, pri, text in sections:
        if not (text or "").strip() or pri < min_priority:
            continue
        if key not in best:
            order.append(key)
        if key not in best or pri > best[key][0]:
            best[key] = (pri, text)                          # dedup: 같은 key는 최고 우선순위 하나
    included, used, dropped = set(), 0, []
    for key in sorted(order, key=lambda k: -best[k][0]):     # 포함 판정 = 우선순위 desc
        t = best[key][1]
        t = t if t.endswith("\n") else t + "\n"
        if used + len(t) <= budget_chars:
            included.add(key)
            used += len(t)
        else:
            dropped.append(key)
    if dropped and log:
        log("context_budget_drop", budget=int(budget_chars), used=used, dropped=dropped)
    parts = []
    for key in order:                                        # emit = 입력(논리) 순서(우선순위는 포함 판정에만)
        if key in included:
            t = best[key][1]
            parts.append(t if t.endswith("\n") else t + "\n")
    return "".join(parts)

# 턴 한도로 작업이 끊겼을 때 같은 세션으로 이어가게 하는 지시(구조적 연속 실행).
# [B-17 — 행동지시 단락 삭제(BOT_ARCH_REDESIGN 2026-07-03)] 둘째 단락("동료는 비동기로 일하지 않습니다"
# 폴링 금지·재위임 지시)은 백스톱 실재로 삭제 — _PRINCIPLE [단일활성] 항목이 매 턴 주입되고, 위임 진행 중
# solo run/추가 request는 guide_tools·request의 [대기] 게이트가 기계 차단하며, 미완(owner_incomplete)
# 이어가기는 SYS _auto_continue_owner가 리더 판단 없이 직접 발사한다.
_CONTINUE_BODY = (
    "[이어서 계속 — 처음부터 다시 하지 말 것] 직전 턴이 작업 도중 '턴 한도'로 끊겼습니다. "
    "진행 중이던 Task가 아직 열려 있을 수 있습니다. 현재 작업공간 상태를 Read/run으로 먼저 확인한 뒤, "
    "이미 한 부분은 건너뛰고 남은 부분만 마저 진행해 그 Task를 complete_task로 마감하세요. "
    "마감(또는 명시적 완료)까지가 목표입니다."
)


# [G3 — 캐주얼 판정 술어(B-06, _prompt에서 호이스트)] 분명한 캐주얼 신호·빌드 동사 목록 — _prompt(캐주얼
# 프롬프트 분기)와 run_turn(도구 미장착 판정)이 같은 원천을 본다(판정 이원화 방지). 보수적: 캐주얼 신호가
# 있고 빌드 동사가 없을 때만 '좁은 캐주얼'이다.
_CASUAL_HINTS = ("배고", "출출", "추천", "점심", "저녁", "아침", "뭐 먹", "뭐먹",
                 "심심", "안녕", "맛집", "졸려", "피곤", "ㅎㅇ", "ㅋㅋ")
_BUILD_VERBS = ("만들", "제작", "구현", "개발", "배포", "구축", "짜줘", "짜 줘",
                "세워", "고쳐", "리팩", "디버그")


def _casual_turn(body, role) -> bool:
    """[G3 — 좁은 캐주얼 판정(B-06)] `캐주얼 신호 AND not 빌드동사`일 때만 True(리더 턴 한정) —
    이때만 도구를 casual 모드(run만)로 장착한다. Info 단독은 제외: Info엔 '팀 토론 진행'류가 있어
    request/meet 경로가 필요하다(오분류가 하드 능력 상실로 격상되는 것 방지)."""
    if role != "leader":
        return False
    bl = body or ""
    return any(c in bl for c in _CASUAL_HINTS) and not any(c in bl for c in _BUILD_VERBS)


# [B-18① — B 이동(BOT_ARCH_REDESIGN 2026-07-03 W3)] 매 wake 반복 지불이던 정적 원칙 3블록
# (완성도/자원동원/차선책)을 PLAYBOOK.md(.collab/ — _write_dossier_scaffold가 워크스페이스마다 실재
# 보장)로 이동하고 프롬프트엔 1줄 참조만 남긴다. [작업공간 레이아웃]은 A-6(게이트 백스톱 없음 — 위반이
# run 타임아웃·배포 실패로만 늦게 드러남)에 따라 **워커 push 유지**(_PRINCIPLE_LAYOUT — 멤버 프롬프트
# 전용), 리더는 PLAYBOOK 참조로. 워커 run 타임아웃 메시지(guide_tools — GPU 없음·Render Node 전용)도
# 종전 유지. 이동 원문은 PLAYBOOK_PRINCIPLES(문서 원본)에 보존.
PLAYBOOK_PRINCIPLES = (
    "[완성도 기준] 산출물은 '동작하는' 수준이 아니라 '그 종류 결과물로서 완성·정돈된' 수준이어야 "
    "합니다 — 같은 요청을 숙련자가 받았다면 당연히 갖췄을 요소·손맛·디자인을 갖추세요(요청의 함의에 맞는 "
    "깊이, 골격/최소판 금지). '무엇이 완성인지'는 그 artifact 종류의 상식에서 끌어내세요(하드코딩 아님). "
    "검증·리뷰도 '되나'만이 아니라 '완성도·경험의 질'까지 봅니다.\n"
    "[자원 동원 — 닿는 건 다 끌어다 최대 품질] 닿는 모든 자원을 **스스로** 동원하세요 — 실제 재료가 필요한데 "
    "코드로 placeholder 대체하게 되면 최종으로 내지 말고, WebSearch로 무료(CC0/오픈) 자원을 찾아 받아 통합하거나 "
    "닿는 무료 수단으로 확보하세요. 모르는 기법·라이브러리는 WebSearch로 익혀 적용('상상'으로 구현 말 것). 사용자/"
    "위임자에게 '키·자원 달라' 요구 금지(스스로 무키로 해결). **특히 '실제·공공 데이터로 학습/분석'이 핵심이면 "
    "데이터를 지어내지 마세요**(합성·랜덤 생성=placeholder 아니라 요구 위반, 지표가 순환논리) — 무키 경로(벌크 CSV·"
    "미러)를 찾아 실데이터를 쓰고, 정말 안 닿으면 합성으로 '완성'인 척 말고 보고하세요. 구한 소스·기법은 [경험]에 남기세요.\n"
    "[안 닿으면 최선의 차선책 — 포기·placeholder 금지] 이상적 도구·자원·데이터가 이 환경에서 안 닿으면(큰 다운로드·"
    "차단·GPU 필요 등) 실패나 placeholder가 아니라 **닿는 것 중 최선의 대안으로 갈아타 최고 품질로 완성**하는 게 "
    "목표입니다. 착수 전 **test-fetch/test-run으로 도달을 확인**하고 안 닿으면 즉시 피벗(막다른 길 반복 금지). "
    "'안 닿는다'는 보고는 어떤 차선책으로도 품질이 안 나올 때만의 최후수단.\n"
    "[작업공간 레이아웃] 모든 산출물은 작업공간 '루트' 기준 하나의 일관된 구조로 만드세요(중첩 프로젝트 "
    "폴더 만들지 말 것). 산출물 종류의 관례를 따르되 — **웹 앱이면** 서버는 루트(server.js 또는 app.py), "
    "정적 프론트는 public/(index.html·style.css·app.js)로 두면 그대로 배포됩니다. 같은 산출물을 두 위치에 "
    "만들지 말고, 동료에게 위임할 땐 정확한 경로를 주세요."
)

# [워커 push 유지 — A-6] 레이아웃 관례는 멤버(워커) 프롬프트에서만 종전대로 push(리더는 PLAYBOOK 참조).
PRINCIPLE_LAYOUT = (
    "[작업공간 레이아웃] 모든 산출물은 작업공간 '루트' 기준 하나의 일관된 구조로 만드세요(중첩 프로젝트 "
    "폴더 만들지 말 것). 산출물 종류의 관례를 따르되 — **웹 앱이면** 서버는 루트(server.js 또는 app.py), "
    "정적 프론트는 public/(index.html·style.css·app.js)로 두면 그대로 배포됩니다. 같은 산출물을 두 위치에 "
    "만들지 말고, 동료에게 위임할 땐 정확한 경로를 주세요.\n"
)

# 모든 Organt 공통 원칙 — 선언적 섹션(theme key)로 두어 assemble_context가 dedup·예산 강제. 종전엔
# 같은 원칙이 다른 제목으로 2~3번 반복(추측금지 ×3·단일활성 ×2·run검증 ×2, PRINCIPLE의 ~68%) — 같은
# theme로 병합해 **커버리지 손실 없이** 축소(가드는 다 살아있고 각 원칙이 한 번 강하게 서술). 규율: 새
# 교훈은 '새 섹션'이 아니라 '해당 theme text에 병합'(같은 key면 dedup되므로 구조가 병합을 강제).
_PRINCIPLE_BUDGET = int(os.environ.get("ORGANT_PRINCIPLE_BUDGET", "2200"))
PRINCIPLE_SECTIONS = [
    # [잘못된 상식 교정(2026-07-27, 사용자: '얘들이 명령어 호출을 1번도 시도를 안 하는 거야?
    #  잘못된 상식 때문에?')] 실측: 구현·검증이 전부 끝난 판에서 봇들이 8시간 동안 complete_task를
    #  **한 번도** 부르지 않았다(관문 거절조차 0). 대신 "owner/리더가 확정되면 마감하면 된다"만
    #  반복했다 — 권한을 열어도 **배운 상식**이 그대로면 행동은 바뀌지 않는다. 그래서 가르친다.
    ("closing_is_everyones", 90,
     "[마감은 자리가 아니라 관문] 할 일이 끝났다고 판단되면 **누가 시켜주기를 기다리지 말고 직접** "
     "complete_task를 호출하세요. 마감 권한은 특정 자리(리더·owner)에 있지 않습니다 — 팀의 누구든 "
     "호출할 수 있고, 옳은지는 마감 관문이 **증거로** 판정합니다(주기 완료·전수 검증·교차검증·산출물). "
     "자격이 없으면 관문이 그 자리에서 거절하고 무엇이 부족한지 알려주니, 거절을 두려워 말고 시도하세요. "
     "'owner가 정해지면'·'권한이 열리면' 같은 대기는 성립하지 않습니다 — 기다릴 대상이 없습니다. "
     "거절되면 그 사유 하나만 해소하고 다시 호출하세요."),
    ("consult", 90,
     "[협의로 규격을 맞춘다 — 혼자 추측 금지] 동료의 규격·산출물·의도를 모르거나 가정이 필요하면 추측해 "
     "진행하지 말고 그 정보를 가진 동료에게 request(Info)로 물어 확인하세요(모호하면 재질문 — 단 진행에 꼭 "
     "필요한 것만). 필드명·데이터 형태·API 경로·디자인 토큰 같은 인터페이스나 다른 도메인과 맞물리는 가정은 "
     "혼자 정하지 말고 그 도메인 담당 동료와 **먼저** 맞추세요(회의에서 협의하거나 request(Info)로 질문). "
     "확인이 안 되면 그 산출물을 Read/Glob로 직접 대조해 검증하세요. 우리는 모두 같은 "
     "모델이라 품질은 *역할이 다른 동료와의 협의*에서만 나옵니다(혼자 일하면 같은 맹점) — 협업 빈도가 곧 품질."),
    ("single_active", 95,   # 인격(CLAUDE.md)에 없는 운영 규칙(폴링 데드락 가드) → resume에도 유지
     "[단일활성 — 한 번에 한 명, 폴링 금지] 회의 발언권도 백로그 작업도 **한 번에 한 명만**(SYS 베턴 보장) — "
     "백그라운드·시차 도착·동시 진행은 없습니다. 자기 차례에 자기 몫만 하고 반환하세요('처리 중' 말·추가 도구 "
     "남발·ls/run 폴링 금지). 작업 배분은 위임이 아니라 **백로그 순차 릴레이** — pick_backlog로 하나 집어 직접 "
     "만들고, 끝내면(report_iter 실증 / 불가면 drop_backlog) 마무리자가 다음 수행자를 pick_backlog(id)로 선정합니다. "
     "미완('⚠ 턴 한도 도달')으로 끊겼으면 어디까지·뭐가 남았는지만 적어 반환 — SYS가 그 지점부터 잇습니다."),
    ("verify", 90,
     "[run으로 실제 결과를 검증한다] 버그·요청은 스펙에서 유추하지 말고 **run으로 실제 산출물을 돌려 증상을 "
     "재현**한 뒤 원인 층을 고치세요(동작/규칙 문제와 표현/배치 문제는 대개 다른 층 — 표면만 보고 엉뚱한 층 "
     "고치기 금지; 스펙 문서는 참고일 뿐 할 일 목록 아님). 검증 기준은 '구동되나'가 아니라 **의도한 동작"
     "(사용자가 받는 결과)이 일어나나** — goal 성공조건이 진짜 충족되는지, 엣지·내부 일관성까지 사용자 입장에서 "
     "끝까지 재현하세요('실행됨'에서 멈추지 말 것; '⚠ 턴 한도 도달'이면 미완)."),
    ("usability", 70,
     "[실 사용성 — 기능 완비 ≠ 잘 쓰임] 기능을 다 넣는 것과 *진짜 사용자가 핵심 목표를 쉽게 달성*하는 건 "
     "다릅니다. **주 사용 경로의 마찰을 없애세요**(맥락 기반이면 자동감지·기본값·원탭 — 다단계 수동 폼 금지). "
     "'사용자가 열면 *제일 먼저* 뭘 하고 싶을까'를 그 종류 최고 앱처럼 처리하세요(접근성도 실사용성). 손으로 "
     "다 설정시키는 설계 금지."),
    ("report", 60,
     "[보고] 결과는 간결한 일반 텍스트로 반환하세요 — 그 반환값이 곧 요청자에게 가는 Response. '---' 구분선/"
     "'✅ 완성' 배너/표/긴 머리말 같은 장식은 쓰지 말고, 보고하려고 request 쓰지 마세요. "
     "**채팅(Response)엔 이번 작업의 결과·결정·다음 액션만 — 교훈·회고·일반화된 노하우('~해야 한다'류 "
     "메타 배움)는 채팅에 쓰지 마세요.** 그건 협업 피드를 어지럽힙니다 — 배운 건 **report(experience=…) "
     "인자**나 **[경험]…[/경험] 블록**으로만 남기세요(시스템이 흡수해 당신 개인 증류로 보관, 채팅엔 안 남음). "
     "동료에게 '무엇을 하라'는 지시·요청과 '무엇을 배웠다'는 회고는 다릅니다 — 후자는 피드에 남기지 말 것."),
    # [B-map — .collab/ 어포던스는 persona로 내구 이전] '협업 사실은 .collab/에 있으니 Read'는 회사 공통·
    # 압축에도 살아남아야 하는 어포던스라 persona(organt/CLAUDE.md → system_prompt)에 각인한다. PRINCIPLE에
    # 중복 주입하지 않는다(디스크 파일은 자기설명적 헤더 보유 + persona가 언제 Read할지 안내).
]
# assemble_context: dedup(중복 재발 차단)·예산(비대 차단)·우선순위(예산 압박 시 형성 문구부터 드롭).
PRINCIPLE = assemble_context(PRINCIPLE_SECTIONS, _PRINCIPLE_BUDGET)

# 리더 전용 규칙 — 진행자(퍼실리테이터) 형성 문구. 내용은 그대로, theme key만 부여해 PRINCIPLE_SECTIONS와
# 함께 한 예산·한 dedup 파이프라인으로 조립(표준 규칙이 양 분기 모두 구조적 큐레이션 아래). 우선순위는
# 형성 문구라 PRINCIPLE 핵심(90)보다 낮게 — 예산 압박 시 이쪽부터 밀림(핵심 규칙·정체성은 보존).
LEADER_EXTRA_SECTIONS = [
    ("lead_facilitator", 82,
     "[퍼실리테이터 — 진행자] 당신은 해석자가 아니라 진행자입니다. 사용자 원문을 당신 식으로 바꾸지 말고 "
     "**그대로 인용**해 묻고, set_goal의 Purpose·Goal은 **전문가 제안을 종합**해 적으세요(혼자 저작 금지). "
     "채용은 기획에서 드러난 도메인 공백 기준(연속 무응답엔 재채용 말 것 — 인프라 문제)."),
    ("lead_position", 82,
     "[당신의 위치 — 진행자이자 한 직원] 당신도 Write·run을 가진 직원입니다. **혼자 다 만들지도, 다 위임하고 "
     "구경만 하지도 마세요**(둘 다 잘못) — 당신 도메인 하나는 직접 맡고, 다른 전문가 도메인은 그 owner에게 "
     "Work로 위임해 끝까지 책임지게. 위임은 '구현 스펙' 말고 '측정가능한 목표'로(어떻게는 owner가 정함)."),
    ("lead_acceptance", 80,
     "[최종 인수 + 수평 수렴] (검증 게이트) 부분검증은 도메인 동료가, **전체 최종 인수(사용자 여정 처음~끝)는 "
     "만든 사람 아닌 QA/독립 동료**가 — 저자편향 차단(자가검증으론 마감 안 됨). 통합 후 owner들+검증자를 meet로 "
     "한 번 모아 '합쳐놓고 좋은가' 교차비평하고, 비평은 **그 owner에게 직접** 넘겨 1회 끌어올리게(당신 경유 금지). "
     "왕복 2회+면 공정 종결."),
    ("lead_team", 80,
     "[팀 구성] 작업 무게를 보고 팀 규모를 정해 create_project(team=…) — 무겁거나 중요한 도메인엔 여러 명, "
     "풀 여유 인력도 활용(놀리지 말 것). 필요 직군이 없으면 recruit(role='직군')로 채용하세요 — 그 직군 "
     "전문가가 즉석 생성돼 합류합니다. **말로 '너 X 담당'은 불가(직군 부여가 먼저, 시스템이 강제), "
     "1봇 1직업, 같은 직군 있으면 재사용.**"),
    ("lead_branches", 84,
     "[처리 갈래] 요청 성격을 보고:\n"
     "- 단순 질문(혼자 답 가능) → 답만 간결히 반환.\n"
     "- 팀 논의/토론 → 진행자로서 한 쪽 주장을 다른 쪽에 전달해 실제 반박/수용이 오가게 하고, 합의되면 채택·"
     "안 되면(왕복 길면) 당신이 공정하게 단일 결론(자기편·무승부 금지).\n"
     "- 실작업 Project → create_project로 팀을 모아 **기획 회의부터**: 주제·도메인·데이터·분해·담당을 통보 말고 "
     "전문가 제안을 수렴(주제 미정 열린 요청이면 주제 선정이 첫 안건 — 접근 가능한 데이터인지 확인; 분해·담당도 "
     "각 전문가가 자기 도메인을 정의). 이후 **산출물 단위 Task 하나씩**(complete로 마감해야 다음): "
     "create_task(빈 껍데기, members) → 멤버 전원에게 request(Info)로 'Purpose·도메인 목표·성공기준'을 물어 수렴 → "
     "set_goal(**측정가능한 결과만**, 구현 방법·파일은 적지 말 것 — 그건 owner 몫) → 맡을 동료에게 "
     "request(Work)(받는 사람이 owner) → 검증 → complete → deploy."),
    ("lead_no_monopoly", 76,
     "[무응답 시 독점 금지] 동료가 무응답이면 떠안지 말고 같은 직군 재배정/recruit로 충원(무응답은 대개 "
     "인프라 문제 — 충원 남발 말고 최후수단; 직접 구현은 당신 도메인에 한함)."),
]
_LEADER_RULE_BUDGET = int(os.environ.get("ORGANT_LEADER_RULE_BUDGET", "4200"))
# [적당히 — wake-aware 규칙(트레이드오프)] 첫 wake는 전체 규칙(봇이 배움), 재-wake는 핵심(우선순위 threshold
# 이상)만. 근거: 봇은 첫 wake에 전부 배웠고 대화가 기억하며 **게이트가 규칙의 백스톱**이라, 재-wake에서 형성
# 문구를 빼도 막힘 위험이 낮다. 반대로 task·cwd(백스톱 없음)는 매 턴 full 유지(막힘 방지). 막힘↔성능 균형점.
# 임계: 멤버 90(핵심 규칙만), 리더 80(핵심 규칙 + 진행자 핵심 블록). 형성 문구(50~76)만 재-wake에서 빠짐.
_NO_RULES_MINPRI = 10_000   # resume: PRINCIPLE 규칙 0 — 되살리기 대신 내구 구조가 담보(단일활성=persona·베턴
                            # 게이트, verify/owner/iface=task 게이트, 완성도·레이아웃=디스크 PLAYBOOK).


def member_principle(sys, first_wake=True):
    """멤버 규칙 — 첫 wake만 전체 주입(1회 각인). resume엔 0 — 반복 재주입이 아니라 persona·게이트·디스크가 담보."""
    return assemble_context(PRINCIPLE_SECTIONS, _PRINCIPLE_BUDGET, log=getattr(sys, "_log", None),
                            min_priority=0 if first_wake else _NO_RULES_MINPRI)


def leader_rules(sys, first_wake=True):
    """리더 규칙 세트(PRINCIPLE + 리더 형성 문구)를 한 예산·한 dedup 파이프라인으로 조립. 첫 wake만 전체,
    resume엔 0 — 형성 문구·원칙은 대화 기억 + persona + 게이트 + 디스크가 담보(되살리기 없음)."""
    return assemble_context(PRINCIPLE_SECTIONS + LEADER_EXTRA_SECTIONS, _LEADER_RULE_BUDGET,
                            log=getattr(sys, "_log", None),
                            min_priority=0 if first_wake else _NO_RULES_MINPRI)


def status_text(sys, flow, t0, final=None) -> str:
    """[Rule/Status — 상태 가시화] 흐름 상태 메시지 본문. 묻기 전에 보이는 계기판:
    무엇이(요청 요약), 얼마나(시작 시각), 지금 누가(베턴 보유자), 살아 있는가(마지막 활동).
    시각은 Discord **동적 타임스탬프**(<t:유닉스:R>)로 박는다 — 상대시간을 클라이언트가
    계속 다시 그리므로, 컨테이너가 멈춰 수정(edit)이 끊겨도 표시는 '1초 전→2시간 전'으로
    늙는다. 수정 시점에 계산한 'N초 전' 고정 문자열은 박제되면 **거짓 생존 신호**가 되던
    결함(사용자 관측: 동면 중에도 '마지막 활동 1초 전')의 구조적 수정 — 죽으면 죽어 보인다.
    final이 오면 종결 확정 표기('✅ 완료'/'⏸ 중단')로 닫는다."""
    req = (getattr(flow, "status_req", "") or "")[:60]
    if final is not None:
        return f"{final} {time.strftime('%H:%M')} — “{req}”"
    now_m, now_w = time.monotonic(), time.time()
    start_ts = int(now_w - max(0, now_m - t0))
    alive = flow.comm.alive
    who = flow._info(alive) or ("담당자" if alive == flow.leader else f"<@{alive}>")
    done = sum(1 for h in flow.comm.history if h[0] == "respond")
    last_ts = int(now_w - max(0, now_m - (flow.last_activity or t0)))
    # [진행 가시성] '작업중'만 뜨던 답답함 해소 — 이 흐름의 최신 활동(도구·추론)을 한 줄 보인다.
    # 훅·narrate가 flow.note_activity로 남긴 전체 기록의 최신(신선한 것만 — 30초 지나면 '이어서' 폴백).
    act = ""
    _log = getattr(flow, "activity_log", None) or []
    if _log:
        _fresh = (now_m - _log[-1][1]) < 30
        act = f"\n지금 하는 일: {_log[-1][0]}" + ("" if _fresh else " (이어서 작업 중…)")
    return (f"● 작업 중(시작 <t:{start_ts}:R>) — “{req}”\n"
            f"담당: {who} · 위임 {done}건 완주 · 세그먼트 {max(1, flow.leader_segment)}{act}\n"
            f"마지막 활동: <t:{last_ts}:R>")


def craft_note(sys, me, first_wake=True) -> str:
    """[봇별 완전 격리 — 2026-07-06] '당신의 직무 기준' = 이 봇 자신의 개인 기준(bot_profiles)뿐.
    직군 공용 기준 주입 폐지 — 탄생 시 채용 제네시스(리크루터)가 직군 유산을 '이 사람의 시작 기준'으로
    빚어 넣고(기계적 시드 아님), 이후 자기 경험→distill_bot 증류로만 발전한다. 같은 직군 동료와도
    기억·기준이 섞이지 않는다(오염 차단). first_wake 1회 각인(되살리기 없음) — resume엔 재주입 안 함,
    [경험]의 내구 홈은 report 툴 experience 필드. 기준 내용은 시스템이 정하지 않는다(자기정의 보존)."""
    jobs = [j.strip() for j in str(sys.bot_info.get(me, "")).split("·")
            if j.strip() and not j.strip().startswith("예비")]
    if not jobs:
        return ""
    notes = []
    mine = (sys.bot_profiles.get(me) or "").strip()
    exp = sys.bot_experience.get(me)
    if first_wake:      # 개인 기준·원시 경험은 불변이라 재-wake엔 대화에 이미 있음 → 첫 wake만 주입
        if mine:
            notes.append(f"[당신의 직무 기준 — {jobs[0]} 전문가인 '당신 자신'의 자기검수 기준(당신의 경험으로 "
                         f"빚어져 수면 증류로 발전하는, 당신만의 것). 이 기준을 충족한 산출물만 인도하세요]\n" + mine)
        elif exp:
            notes.append("[당신의 최근 경험 — 당신이 실제 작업에서 직접 얻은 교훈. 같은 함정을 반복하지 마세요]\n"
                         + "\n".join(f"- {e}" for e in exp[-6:]))
        else:
            # [폴백 — 온보딩 전 첫 투입] 개인 기준·경험이 다 비면(리크루터가 아직 못 채움) 자기 기준을
            # 스스로 작성하게 요청 — [직무기준] 블록은 흡수 때 bot_profiles(자기 것)로 영속된다.
            notes.append(
                f"[직무 기준 작성 — 이번 한 번만] 당신의 직무 기준(자기검수 기준)이 아직 없습니다. "
                f"이번 보고 **맨 끝에** 아래 형식으로 '{jobs[0]}' 전문가로서 당신의 '훌륭한 산출물·검증 기준' "
                f"5~8줄을 작성해 포함하세요. 이후 모든 작업에서 당신의 자기검수 기준으로 영속·주입되고, "
                f"**마감 검증의 루브릭**으로도 쓰입니다(일반론 말고 이 직군 특유의 품질·검증 기준으로). "
                f"[RFC-008] 품질은 추상적 규칙보다 **예시로 더 잘 전수**되니(암묵지), 기준 끝에 '좋은 예 / "
                f"흔한 나쁜 예'를 각 1줄 덧붙이면 검증자가 'good'을 구체로 잡습니다:\n"
                f"[직무기준] {jobs[0]}\n(기준 줄들)\n좋은 예: …\n나쁜 예(흔한 미달): …\n[/직무기준]")
        # [의무형 — 데이터 근거] 선택형("없으면 생략")은 라이브에서 0% 산출, 의무형은 100% 산출.
        # 개인 플라이휠(자기 기준→경험→개인 증류→개선)의 원료가 여기서만 나오므로 고정 섹션으로
        # 강제하되, '없음' 탈출구로 억지 채움(노이즈)을 막는다('없음'은 흡수 때 버려짐).
        notes.append(
            f"[경험 — 보고의 고정 섹션(생략 금지)] 작업 보고 끝에 반드시 아래 블록을 포함하세요. "
            f"이번 작업에서 얻은 **다음 작업에도 일반화 가치가 있는** 교훈(함정·효과적이었던 방법) "
            f"1~2줄만 — 당신의 다음 작업에 주입되고 수면 중 당신의 직무 기준으로 증류됩니다(당신 개인의 "
            f"학습 — 남과 섞이지 않음). 새 교훈이 진짜 없으면 본문에 '없음'이라고 쓰세요(억지로 채우는 "
            f"것보다 '없음'이 낫습니다 — 일회성 디테일·당연한 일반론은 노이즈입니다):\n"
            f"[경험] {jobs[0]}\n(교훈 또는 '없음')\n[/경험]")
    return ("\n\n".join(notes) + "\n\n") if notes else ""


def portfolio_note(sys) -> str:
    """회사가 지금까지 만든 것(기존 프로젝트 목록)을 담당자에게 사실로 보여준다.

    봇은 프로젝트 역사를 볼 수 없어 같은 도메인을 반복 선택하곤 했다(라이브: '안 쓰던 분야의
    공공데이터'를 요청받고도 이미 여러 번 쓴 대기질을 또 고름 — 담당자가 어떤 분야를 썼는지 몰라
    환각으로 판단). per-bot 플라이휠(bot_profiles·bot_experience)이 '각자의 일'을 누적하듯,
    이건 '회사가 무엇을 만들어왔나'를 의사결정자(담당자)에게 누적해 신규성 판단·중복 회피·기존
    작품 이어가기의 사실 근거를 준다. 담당자 프롬프트에만 주입한다(도메인 선택은 담당자의 몫이고,
    팀원 프롬프트엔 노이즈)."""
    rows = []
    for p in sys.projects.values():
        pid = str(p.get("id") or "?")
        name = (p.get("name") or "").strip()
        gist = (p.get("summary") or p.get("purpose") or "").strip().replace("\n", " ")
        if len(gist) > 70:
            gist = gist[:70].rstrip() + "…"
        # 이름을 앞에, 식별번호는 괄호로 뒤에 — 'P-NNN 이름' 표기를 봇이 새 프로젝트 이름으로
        # 흉내 내(번호를 이름에 박아) 채널·폴더에 번호가 중복되던 것 방지(번호는 시스템 몫).
        label = f"{name} ({pid})" if name else pid
        rows.append((pid, f"- {label}" + (f" — {gist}" if gist else "")))
    if not rows:
        return ""                              # 아직 만든 게 없으면 주입 안 함(하위호환·노이즈 0)
    rows.sort(key=lambda t: t[0])              # P-001, P-002 … 안정 정렬
    # [B-18③] push 캡(16건)은 현행 유지 — pull 전환 기각(A-8: '몰라서 못 물음'). 캡 밖 전체는
    # list_projects 도구(pull 보강)로 조회 가능함을 캡이 실제로 잘렸을 때만 한 줄 알린다.
    cap_note = (f"(이 목록은 최근 16건 — 전체 {len(rows)}건은 list_projects 도구로 조회)\n"
                if len(rows) > 16 else "")
    shown = [ln for _, ln in rows[-16:]]       # 길어지면 최근 것 위주(프롬프트 비대 방지)
    return (
        "[참고 — 회사 이력(배경 정보)] 아래는 우리 회사가 진행/배포한 프로젝트 목록입니다. **이건 배경 "
        "참고일 뿐이며, 지금 이 채널에서 사용자가 실제로 요청한 일이 무엇인지가 항상 우선입니다.** 이 채널의 "
        "상황·원문과 무관하면 **무시하세요** — 단순 질문·추천·잡담에 과거 프로젝트를 억지로 엮지 마세요"
        "(라이브 사례: 음식 추천 채널에서 엉뚱한 게임 프로젝트 기준으로 답함). **사용자가 '새 소프트웨어를 "
        "만들어/기존 작품을 발전시켜' 같은 프로젝트성 요청을 한 경우에만** 아래를 근거로 쓰세요: 신규성 판단"
        "(이 목록에 없는 도메인이라야 신규 — 출처만 바꿔 같은 분야 반복은 신규 아님), 중복 회피, 기존 작품 "
        "이어가기(그 P-번호 채널에서). 그때도 도메인·주제는 통보 말고 회의에서 전문가 제안을 수렴해 정하세요"
        "(당신은 퍼실리테이터). 괄호 안 P-번호는 시스템이 자동 부여하는 식별자입니다(새 프로젝트 이름엔 넣지 말 것):\n"
        + cap_note + "\n".join(shown) + "\n\n")


async def channel_situation(sys, channel_id, exclude_root=None, limit=14) -> str:
    """이 채널의 최근 대화를 짧게 추려 흐름에 부착할 텍스트로 — 봇이 '지금 이 채널이 무엇을 주고받는
    자리이고 어떤 맥락인지'를 알고 답하게 한다. 신규 흐름은 세션이 비어 채널 맥락을 모르므로(라이브:
    음식 추천 채널에서 'FPS 게임 밸런스'로 답한 버그) read_thread로 최근 대화를 끌어온다. 매체 중립
    (Discord·SNS 모두 read_thread 보유). 실패해도 흐름을 막지 않는다(빈 문자열 폴백)."""
    try:
        msgs = await sys.guide.read_thread(channel_id, limit=limit, include_plain=True)
    except Exception:
        return ""
    lines = []
    for m in msgs or []:
        body = (getattr(m, "body", "") or "").strip().replace("\n", " ")
        if not body:
            continue
        if exclude_root and str(getattr(m, "message_id", "")) == str(exclude_root):
            continue   # 방금 들어온 그 요청 자신은 제외(원문은 origin_note가 따로 보여줌)
        fid = getattr(m, "from_id", None)
        who = ("사람" if fid in (0, None)
               else (str(sys.bot_info.get(fid) or "").split("·")[0].strip() or f"동료{fid}"))
        if len(body) > 100:
            body = body[:100].rstrip() + "…"
        lines.append(f"- {who}: {body}")
    if not lines:
        return ""
    return ("[이 채널의 지금 상황 — 최근 대화] 아래는 이 채널에서 최근 오간 대화입니다. **여기가 무엇을 하는 "
            "자리이고 지금 무슨 맥락인지**를 이걸로 파악해 그 맥락에 맞게 답하세요 — 회사의 다른 프로젝트 "
            "이력보다 '지금 이 채널의 상황'이 우선입니다(무관한 과거 프로젝트로 새지 말 것):\n"
            + "\n".join(lines[-10:]) + "\n\n")


def env_note(sys) -> str:
    """[이 환경의 능력·경계 — 담당자가 '닿는 범위에서 최고 품질' 경로로 팀을 이끌게 하는 사실]
    손코딩에 갇히지 말고 실제 툴·에셋을 끌어 쓰되, 막다른 길(Godot 1GB·온디바이스 AI생성 등)을
    처음부터 피하게 사실만 준다(해법은 팀이 정함 — 하드코딩 아님). [B-18①] 매턴 주입에서
    PLAYBOOK.md 원문 공급으로 전환 — 리더 프롬프트엔 하드 경계 3사실 1줄 + 참조만(워커는 종전대로
    run 타임아웃 메시지가 결정 지점 공급)."""
    return (
        "[이 환경의 능력·경계(사실) — 닿는 범위에서 최고 품질로 팀을 이끄세요]\n"
        "- run은 **root Bash**(npm·pip·apt·curl 다 됨) → 실제 툴·라이브러리·에셋을 설치/다운로드해 품질을 "
        "올리세요. 단 run 한 번 ~1분이라 **큰 단일 다운로드(수백MB+)는 안 됨** → 닿는 경량 대안으로.\n"
        "- **GPU 없음** → 온디바이스 AI 생성(이미지·영상) 불가. 그래픽은 CC0 에셋 + 절차적 생성(Canvas·SVG·Pillow).\n"
        "- 배포는 **Render Node-웹 전용** → 최종물은 웹 서빙 가능해야(게임=웹엔진 Three.js·Phaser, AI=빌드타임 "
        "학습→예측 JSON을 Node가 서빙; 런타임 Python 서버는 배포 게이트가 막음).\n"
        "- 외부 소스는 다 닿지 않음 → 데이터·에셋 소스는 **착수 전 test-fetch로 도달 확인** 후 진행.\n\n"
    )


def prompt(sys, body, kind, role, me, leader_id=None, flow=None, first_wake=True, micro=False):
    # [마이크로 wake(2026-07-16, ch75 실측 11.6K 표결 프롬프트)] 표결·응찰·병합 같은 '한 줄 상호작용'엔
    # 풀 프레임(craft·PRINCIPLE·동료 강점·[경험] 의무)을 싣지 않는다 — 상식은 첫 실질 턴에 1회 각인,
    # 마이크로 턴은 본문만(기억 오염 최소). 'run으로 검증'·craft 'Read 후 판단' 지시가 표결에 실려
    # 파일 탐사를 유발하던 모순의 소스 제거(금지 문구 덧칠이 아니라 조립 자체를 좁힘). 밀린 각인은
    # run_turn의 _micro_first 장부로 다음 실질 턴이 받는다.
    if micro:
        return (f"당신의 역할: {sys.bot_info.get(me, '팀원')}\n"
                f"받은 요청({getattr(kind, 'value', kind)}): {body}")
    # '담당자'는 고정 직책이 아니라 이번 흐름의 To 수신자(=leader)다. 동료 목록엔 직군만 적고, 담당자에게만
    # '(담당자)' 표식을 단다(다른 흐름에선 같은 봇이 한 직원으로 참여).
    def _peer(i):
        lbl = sys.bot_info.get(i, "?")
        return f"{lbl}(담당자)" if i == leader_id else lbl

    def _strength(i):
        # [B-20 — peers 강점 1줄] 봇별로 '증류된 개인 기준 다이제스트(첫 줄)' > 'capability ledger
        # 상위 검증 실적(임계치 이상)' 순 폴백을 동료 목록에 공급 — RULE_SPEC §11(4) 'accumulated
        # 직무기준/strengths로 멤버 선택'의 스펙-코드 간극을 코드가 스펙 쪽으로 수렴. 공급 원칙:
        # 시스템은 정보만 주고 선택 판단은 리더 몫. 데이터 없는 봇은 종전 표기 그대로(증가분 0).
        prof = (sys.bot_profiles.get(i) or "").strip()
        if prof:
            first = next((ln.lstrip("-•* ").strip() for ln in prof.splitlines() if ln.strip()), "")
            if first:
                return f" — 강점: {first[:80]}"
        tops = [(int(c), n) for n, c in (sys.capability_ledger.get(i) or {}).items()
                if int(c) >= CAP_MIN.get(n, 3)]
        if tops:
            c, n = max(tops)
            return f" — 검증된 실적: {n} 저작 {c}건"
        return ""
    peers = ", ".join(f"{i}({_peer(i)}{_strength(i)})" for i in sys.bot_info if i != me)
    # [동료 변경주입 — 이벤트 기반, 매 턴 고정 X] 로스터(동료+강점)는 recruit 등으로 바뀔 때만 새 정보다.
    # first_wake이거나 마지막으로 본 로스터와 다를 때만 주입하고 본 것을 flow에 기록 — 안 바뀌면 대화 기억에
    # 있으니 안 넣는다('동료를 왜 매 턴 넣나'의 구조적 해결: 변화라는 실제 신호에 결속).
    _sr = getattr(flow, "seen_roster", None) if flow is not None else None
    _show_peers = first_wake or _sr is None or (peers != _sr.get(me))
    if _sr is not None and _show_peers:
        _sr[me] = peers
    peers_note = (f"동료: {peers}\n" if _show_peers else "")
    domain = sys.bot_info.get(me, "")
    # 탈중앙(퍼실리테이터): 모두가 '담당자의 요약'이 아니라 '사용자 원문'을 직접 본다 → 한 명의 해석을
    # 거치며 의도가 왜곡되는 걸 막는다. 받은 지시가 원문과 어긋나면 원문 의도를 우선·되물음.
    # [흐름별 원문 우선] 흐름에 박제된 원문을 먼저 본다 — 전역 self._origin_request는 다음 개입이
    # 덮어쓰므로 동시 흐름에서 교차 오염된다(웹 흐름이 게임 원문을 받던 라이브 버그). flow가 없을
    # 때만(도구 형식용 빈 흐름 등) 전역으로 폴백.
    orig = ((getattr(flow, "origin_request", "") if flow is not None else "")
            or getattr(sys, "_origin_request", "") or "").strip()
    # [원문 = first_wake 각인 + 디스크 내구] 원문(의도)은 해석이 일어나는 첫 wake에 salient하게 1회 주입 —
    # 이후 되살리지 않는다. 압축돼도 잃지 않도록 흐름 시작에 `.collab/ORIGIN.md`로 디스크에 박제하고(내구),
    # 컨텍스트 지도가 그 파일을 가리킨다. 매 턴 받는 task의 Goal도 의도를 이어 나른다.
    origin_note = (f"[사용자 원문 요청 — 진짜 의도(누구의 요약·해석도 아닌, 사용자가 실제로 한 말)]: {orig}\n"
                   f"이 원문이 기준입니다. 받은 지시·질문이 원문과 어긋나 보이면 원문 의도를 우선하고, 모호하면 되물으세요.\n\n"
                   if orig and first_wake else "")
    # [상황 인지] 흐름 시작 때 채널 최근 대화를 부착해둔 것(handle_user_input). 봇이 '지금 이 채널 상황'을 알게 한다.
    situation_note = (getattr(flow, "channel_situation", "") if flow is not None else "") or ""
    # [파일 전송 — 인바운드] 사용자가 첨부한 파일은 작업공간 inbox/에 staging됨 → 봇이 Read로 확인해 쓰게 안내.
    _inb = (getattr(flow, "inbound_files", None) if flow is not None else None) or []
    inbound_note = ((f"[사용자가 첨부한 파일 — 작업공간 inbox/에 있습니다] "
                     f"{', '.join('inbox/' + n for n in _inb)}\n이 파일들을 Read로 확인해 요청에 반영하세요"
                     f"(사용자가 자료로 함께 보낸 것 — 추측 말고 실제 내용 확인). owner에게 위임 시 이 경로를 알려주세요.\n\n")
                    if _inb else "")
    # [사람 중간 개입 — 진행 중 도착] 흐름 도중 사람이 이 봇에게 넘긴 정보(매체가 deliver_human_info로 적재).
    # 순수 프롬프트 노트(요청·baton 아님): 맹종 말고 당신 판단으로 작업에 반영하고, 반영 여부·방식을 응답에 한 줄로
    # 알리세요(대화 느낌). 워커에게 보낼 게 있으면 당신(리더)이 그 owner에게 그 취지로 재위임/지시하세요.
    _pend = (getattr(flow, "pending_info", None) or {}).get(me) if flow is not None else None
    # [미답 질문 상시 재주입(2026-07-09)] 1회성 노트는 바쁜 리더가 무시한다(라이브: 3턴 미답) —
    # [답변] 게시가 관측될 때까지 리더 매 턴 선두에 다시 박는다(소비-clear 대상 아님).
    _unans = (getattr(flow, "unanswered_questions", None) or []) if (flow is not None and role == "leader") else []
    _pend = list(_unans) + list(_pend or []) if (_unans or _pend) else _pend
    human_info_note = (("[사람이 작업 중 전한 정보 — 방금 도착]\n"
                        + "\n".join(f"· {t}" for t in _pend)
                        + "\n→ 당신 판단으로 진행 중 작업에 반영하고(원문 의도 기준), 반영 여부·방식을 응답에 간단히 알리세요.\n\n")
                       if _pend else "")
    # [일상 대화 경로 — 근본] Info(질문·추천·잡담)는 프로젝트 기계 없이 그 채널 맥락에서 대화로 답한다.
    # 종전엔 모든 메시지가 담당자=프로젝트 프롬프트(create_project·recruit·회의·_PRINCIPLE)를 받아
    # "배고파"에도 "뭘 만들까요"가 나왔다(라이브 규명). 캐주얼은 캐주얼하게 — 동료처럼 한 턴에 답.
    _is_info = str(getattr(kind, "value", kind)).upper().startswith("I")
    # [근본·미배포 안전] 미디어 분류(kind)에 더해 두뇌도 캐주얼을 직접 감지 — classify 미배포 환경에서도
    # 일상 발화가 대화로 처리되게. 보수적으로: 분명한 캐주얼 신호 + 빌드 '동사' 없을 때만.
    # 술어는 모듈 레벨로 호이스트(B-06) — run_turn의 도구 casual 모드 판정과 같은 원천.
    if (_is_info or _casual_turn(body, role)) and role == "leader":
        return (
            f"당신은 {domain or '직원'}입니다. 사용자가 이 채널에서 당신에게 말을 걸었습니다.\n"
            f"{origin_note}{situation_note}{inbound_note}{human_info_note}"
            f"받은 말: {body}\n\n"
            f"[이건 일상 대화·질문·추천입니다 — 소프트웨어 작업 요청이 아닙니다]\n"
            f"- **create_project·create_task·recruit·set_goal·meet·deploy 등 협업/제작 도구를 쓰지 마세요.** "
            f"팀을 모으거나 회의를 잡거나 프로젝트를 열지 마세요.\n"
            f"- 그냥 **동료처럼, 이 채널 맥락에 맞춰 간결하고 자연스럽게 대화로** 답하세요. "
            f"'성공 기준/산출물/인터페이스' 같은 문서식 형식 말고 사람처럼. 한 턴에 끝내고 반환하세요.\n"
            f"- 위 '이 채널의 지금 상황'(있으면)을 보고 그 흐름에 이어 답하세요. 회사의 다른 프로젝트로 새지 마세요.\n"
            f"- 정말로 사용자가 **무언가를 새로 만들어 달라(소프트웨어·게임·앱·사이트 제작)**고 *명시*하면, "
            f"그때만 '프로젝트로 시작할까요?'라고 한 줄로 되물으세요(멋대로 프로젝트를 열지 말 것).\n"
            f"- 필요하면 WebSearch로 사실 확인해도 됩니다(맛집·추천 등). 모르면 솔직히.\n"
        )
    # [마일스톤 파이프라인 — 결정권자 프레임(PIPELINE_REWORK §1·§4·§5)] 플래그 ON에서 흐름을 여는
    # To 수신자는 '리더(전 구간 관할)'가 아니라 '결정권자(3권한: 수렴 확정·동률·교착)'다.
    # 진행=주기(iter)·마감=완수조건·배분=백로그 릴레이 — 지시·배분·독식은 게이트가 막는다.
    from .rule.milestone import next_milestone as _ms_next, pipeline_on as _ms_on
    if role == "leader" and _ms_on():
        _ms = _ms_next(flow) if flow is not None else None
        _ms_note = ""
        if _ms is not None:
            _rem = [c.desc for c in _ms.criteria if not c.passed and getattr(c, "status", "active") != "waived"]
            _ms_note = (f"[진행 중 주기] {_ms.ms_id} — {_ms.goal[:60]} (iter {_ms.iter_n}, "
                        f"미충족 {len(_rem)}/{len(_ms.criteria)}"
                        + ((": " + " · ".join(d[:30] for d in _rem[:3])) if _rem else "") + ")\n")
            # [조건 불가능 출구 #1 — 프레임 노출] 정체(진전 없는 반복)면 재협상을 프레임에서 직접 안내.
            if getattr(_ms, "iter_stuck", 0) >= 2:
                _ms_note += (f"[정체 경보 — {_ms.iter_stuck}회 진전 없음] 반복이 결과를 못 바꾸는 중입니다. "
                             f"조건이 환경상 달성 불가라면 renegotiate_criterion(조건, 사유)으로 재협상하세요"
                             f"(사람 승인으로 포기/변경) — 같은 iter를 무한 반복하지 마세요.\n")
        # [개입 우선 #2 — 미답 개입은 iter보다 먼저] 사람 개입이 도착해 있으면 주기 진행 전에 먼저
        # 반영하라고 못박는다(마일스톤 주기가 길어져도 '40분 대기'가 iter 단위로 재발하지 않게).
        _intv = "[개입 우선] 위에 사람이 전한 정보가 있으면, 주기(회의·조건·검증) 진행보다 **먼저** 반영·응답하세요.\n" if human_info_note else ""
        # [결정권자 폐지(2026-07-09, 사용자)] To 수신자는 이제 '발제자' — 요청을 받아 회의를 여는
        # 첫 턴의 역할일 뿐, 권한이 없다. 확정=종결 표결(가결 시 수렴안 자동 등록), 동률=침묵 오래된
        # 순(기계), 교착=회의→사람 사다리. 남은 중앙은 사람 주권(조건 포기 승인)뿐이다.
        # [중앙집권 해제(2026-07-14, 사용자: '발제자같은 중앙집권은 해제')] 이 봇은 '발제자'가 아니라
        # 판을 굴리기 시작하는 첫 턴일 뿐 — 권한·대표성 0. 개인이 정하는 것은 없다: GOAL·마일스톤·단위는
        # 회의 수렴안이, 배분은 순차 릴레이가, 진행은 iter가, 마감은 완수조건이 맡는다(전부 비인격 구조).
        return (
            f"당신은 이 판의 **첫 턴**입니다 — 우두머리가 아니라 공을 굴리기 시작하는 역할뿐입니다. "
            f"혼자 정하는 것은 없습니다: GOAL·마일스톤·서브태스크(단위)는 **회의 수렴안**이, 작업 배분은 "
            f"**순차 릴레이**가, 진행은 iter가, 마감은 완수조건이 맡습니다. 당신의 역할: {domain or '팀원'}\n"
            f"{origin_note}{situation_note}{inbound_note}{human_info_note}{_intv}"
            f"받은 형태: {body}\n{peers_note}{_ms_note}\n"
            f"[사이클] 회의는 **하나가 딱 하나**를 정하고 시스템이 다음 회의를 자동으로 엽니다 — 한 번에 다 "
            f"정하려 하지 마세요. ① **meet**(topic, my_opinion 필수) — 지금 단계의 질문(무엇을 만들지→이번에 "
            f"보여줄 하나→작업 영역→백로그) 하나에만 답하는 회의. **결론은 공동 파일(DRAFT.md)에서 함께 완성** "
            f"— 각자 자기 몫을 직접 편집·이의(`> [이의 @직군]`)·해소하고, 완성되면 전원 표결로 확정(개인 set_goal/"
            f"set_milestone 아님 — 팀 판에선 막힙니다). ② 백로그가 서면 각자 **pick_backlog**(desc='내가 할 "
            f"일')로 하나씩 집어 **직접** 만듭니다 — **한 번에 한 명만**, 끝내면(report_iter 실증 / 불가면 "
            f"drop_backlog) 마무리자가 다음 수행자를 pick_backlog(id)로 선정하는 순차 릴레이. ③ 전 조건 "
            f"실증되면 주기가 닫히고 **사용자 보고 후 다음 단계**로. ④ 조건이 환경상 불가면 renegotiate_criterion.\n"
            f"{sys._craft_note(me, first_wake)}"
        )
    if role == "leader":
        my_role = f"{domain}(담당자)" if domain else "담당자"
        # 담당자가 '예비'(직군 미배정)로 호명된 경우: 자길 예비로 방치하지 말고 먼저 자기 직군부터 채용해
        # 한 직원으로 참여한 뒤 팀을 꾸린다(사용자: '자기 자신도 프로젝트의 일원으로 참여해야지').
        is_spare_leader = str(domain).startswith("예비")
        spare_lead_note = (
            f"[당신은 '예비'로 호명됨 — 가장 먼저, 무엇보다 자기 직군부터] 당신(id {me})은 아직 직군 미배정 "
            f"'예비'인데 이번 흐름의 담당자로 호명됐습니다. **create_project·create_task·request 그 무엇보다 먼저, "
            f"맨 첫 행동으로 recruit(member={me}, role='당신이 맡을 직군')를 호출해 자기 직군부터 확정**하세요 — "
            f"이건 Task가 없어도 됩니다(Task 전에 호출 가능). 이 순서를 안 지키고 '예비'인 채로 프로젝트/Task를 "
            f"열면 화면(상태블록·동료 프롬프트)에 담당자가 '예비'로 박힙니다. 자기 직군을 정한 뒤엔 한 직원으로 "
            f"직접 참여(자기 도메인 구현에 기여)하고, 일에 필요한 다른 직군 동료를 골라 팀을 꾸리며 부족한 직군은 "
            f"recruit로 채우세요.\n"
            if is_spare_leader else "")
        # 팀은 고정이 아니라 담당자가 일에 맞게 동적으로 짠다(직군 고정 해결).
        team_note = (
            f"[팀은 당신이 동적으로 짠다 — 자동 전원 아님] 직군 구성은 미리 고정돼 있지 않습니다. 이 일에 **필요한 "
            f"직군을 당신이 직접 고르세요** — create_project(team='필요한 직군/동료들')로 팀을 정하고, 모자란 직군은 "
            f"recruit(role='직군명')로 더하세요(그 직군 전문가가 즉석 생성돼 합류). 자동으로 전원이 소집되지 않습니다"
            f"(놀던 인력까지 무조건 부르지 말 것). set_goal은 '당신이 고른 그 팀 전원'의 협의로 통과합니다.\n")
        portfolio = sys._portfolio_note()   # 회사가 만들어온 것 — 신규성 판단·중복 회피의 사실 근거(담당자에게만)
        # [B-17 — A(중복) 프롬프트 삭제(BOT_ARCH_REDESIGN 2026-07-03 W3)] "(시스템 강제)" 블록은 매 wake
        # 반복 지불인데 전부 게이트·훅 백스톱이 실재한다 — 위반 시 거부 메시지가 처방을 동봉하므로(결정
        # 지점 공급) 사전 고지를 삭제한다. 항목별 백스톱 대조표(전부 실재 확인):
        #   · [구현은 위임 — 독식 금지]     → permissions #6(리더 독식)·#8(리더 흡수)·#4(owner 대리구현)
        #   · [전문 능력은 흡수 말고 투입]  → rule/task set_goal 스태핑 커버리지 보류 + request 직군밖 게이트
        #   · [재요청은 Redo로]            → rule/communication delivered_work→redo(한도·RedoLimitExceeded)
        #   · [set_goal 전원 협의(시스템 강제) 문구] → rule/task set_goal 합의 커버리지 보류
        #   · [협업 인터페이스]            → Work 위임의 [직접 합의] 주입 + complete iface_dialogue 게이트
        #   · [owner 작업중 완료·대리구현 금지] → rule/task owner_delivered 거부 + permissions #4
        #   · [검증·배포]                  → rule/task verified·교차검증 게이트 + SYS _ensure_deploy(직접 배포)
        # 게이트 백스톱 없는 첫-시도 형성 문구([퍼실리테이터]·[당신의 위치]("위임은 측정가능한 목표로")·
        # [팀 구성]·[처리 갈래]·[무응답 시 독점 금지]·[최종 인수+수평 수렴])는 삭제 목록에서 제외(존치).
        # [B-18①] _env_note 상세는 PLAYBOOK.md로 이동 — 하드 경계 3사실만 1줄 유지(참조가 실패해도
        # 막다른 길을 피할 최소 사실). 상세·레이아웃·완성도 원칙은 .collab/PLAYBOOK.md(스캐폴드가 실재 보장).
        return (
            f"당신은 이번 요청의 To로 지정돼 흐름을 여는 '담당자'입니다 — 고정 직책이 아니라 To를 받아 "
            f"이번 흐름의 담당이 된 것이며(다른 흐름에선 한 직원으로 참여), 특별한 권력자가 아닙니다. "
            f"당신의 역할: {my_role}\n"
            f"{origin_note}"
            f"{situation_note}"
            f"{inbound_note}"
            f"{human_info_note}"
            f"{portfolio}"
            f"[환경 경계(사실) — 상세는 PLAYBOOK] GPU 없음 · 배포는 Render Node-웹 전용 · run 1회 ~1분(큰 "
            f"단일 다운로드 불가) — 상세 능력·경계와 레이아웃 관례는 작업공간 `.collab/PLAYBOOK.md`를 Read하세요.\n"
            f"받은 형태: {body}\n{peers_note}\n"
            f"{sys._craft_note(me, first_wake)}"
            f"{spare_lead_note}{team_note}\n"
            f"{leader_rules(sys, first_wake)}"
        )
    my_role = domain or "팀원"
    # [B-17 — 멤버 교차도메인 단락 축소] 장문 행동지시를 2줄 사실로 — 백스톱 실재: 비-리더 교차도메인
    # Work는 rule/communication 게이트(cap_hit)가 막아 리더 조율 큐로 이관하고(거부 메시지가 처방 동봉),
    # SYS _auto_coordinate가 큐를 기계적으로 비운다. [B-18①] 레이아웃 관례는 워커 push 유지(A-6).
    return (
        f"당신은 자율적으로 일하는 팀원입니다(당신도 필요하면 동료에게 먼저 묻습니다). "
        f"당신의 역할: {my_role}\n{origin_note}{inbound_note}{human_info_note}받은 요청({getattr(kind, 'value', kind)}): {body}\n{peers_note}\n"
        f"{sys._craft_note(me, first_wake)}"
        # [레이아웃 wake-aware] 레이아웃 관례는 컨텍스트 지도가 'PLAYBOOK에 있음'을 이미 가리키고 재-wake엔
        # 대화에도 있음 → fresh만 push(봇이 배움·게이트 백스톱 없어 첫 학습은 유지), resume 드롭.
        f"{member_principle(sys, first_wake)}\n{sys._PRINCIPLE_LAYOUT + chr(10) if first_wake else ''}"
        # [교차층 dedup + 기억 우선] owner책임·완성도·독식·맞물림 등은 CLAUDE.md 인격에 있음(재주입 X). 아래
        # 운영 규칙은 CLAUDE.md엔 없지만 **게이트/구조가 강제**(Write차단=permissions·미완 이어가기=continue
        # body·교차도메인 라우팅=coordination 게이트)하고 **대화(turn-1)에도 있음** → fresh만 push, resume 드롭
        # ('전에 준 걸 또 주지 않는다' — 기억+게이트가 담보).
        + ((
            "[운영 규칙 — 원칙은 CLAUDE.md 인격에 있고 여기선 이 협업 기계의 규칙만] 회의(협의) 중엔 제안·합의만 하고 "
            "**파일 Write는 차단**됩니다 — 실제 구현은 회의에서 백로그가 정해진 뒤 pick_backlog로 자기 몫을 집어 만듭니다. "
            "작업이 미완('턴 한도')으로 끊겼으면 '어디까지·뭐가 남았는지'만 정확히 적어 반환하세요 — SYS가 그 지점부터 "
            "'이어서' 잇습니다('진행 중·마저 하겠다'로 멈추지 말 것).\n"
            "[이니셔티브의 방향] 도메인 *밖*의 것(남이 고칠 결함·타 도메인 구현·재배정)은 직접 행동 말고 **보고/제안**으로 "
            "올리세요 — 배분은 백로그 릴레이가 합니다."
        ) if first_wake else "")
    )
