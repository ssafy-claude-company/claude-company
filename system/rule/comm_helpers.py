"""Communication 헬퍼 — 팀·역량 라우팅/협업 실행의 순수·자립 헬퍼(communication.py에서 추출).

[분할 — LLM 국소수정 용이성(2026-07-03)] communication.py(~2,090줄)의 자립 응집 그룹을 모듈로 추출.
communication.py가 이 모듈을 재수출하므로 기존 import 경로(`from system.rule.communication import
_is_spare` 등)는 그대로 동작한다(파사드 보존). 이 모듈은 communication.py를 import하지 않는다
(순환 없음 — flow는 duck-typed 인자, 예외·매니저 불필요).

수록 그룹(내용·순서는 추출 전 communication.py와 동일):
- 팀·역량 라우팅: _kw · _CAPS · _capability_gaps · _needed_caps_coverage · _offdomain_capability_hit
- 직군/예비: _SPARE_LABEL · _is_spare · _norm_job · _JOB_SEP · _jobs_of · _job_tokens · _free_alternatives
- 협업 라우팅: _resolve_members · _uniq · _find_variant_job
- 응답 실질성: _HOLLOW_PING · _clarify_hold · _body_overlap · _is_substantive
- 협업 실행: _fork_collect · _group_of · _add_members · _say · _say_speech
"""
import asyncio
import os
import re
from typing import List, Optional

from ..protocol import Kind


# ══ [팀·역량 라우팅 Rule — guide_tools에서 §7 rule/communication로 이관] ══
# '누구에게 위임하나'(능력표 _CAPS·직군·전역 점유)를 판정하는 소통 Rule. 잘못된 병합을 원래대로 복원.
# flow는 duck-typed 인자(Flow 임포트 불필요).
def _kw(*kws):
    """키워드 중 하나라도 문자열에 있으면 True인 술어 생성(능력 need/cover 판정용)."""
    return lambda s: any(k in s for k in kws)


# 능력 표 — (표시명, need(goal 소문자)→bool, cover(labels 소문자 합본)→bool). 고신호만(과채용 최소):
# 그 능력이 *작업의 실질 축*일 때만 need=True. cover는 관대(누군가 plausibly 덮으면 갭 아님).
# 일반화 동기(2026-06-22 사용자 '데브옵스·DBA 채용이 안 보인다'): 단일 AI/ML만 보던 탓에 반복 수요인
# 공공데이터 수집이 게이트에 안 걸려 흡수됐고(실데이터를 합성·가짜로 위장하는 사고의 *상류* 원인),
# 배포 인프라는 아무도 전담 안 해 리더에 귀속됐다(P-028 배포 1인 루프). 기능으로 식별(직군 타이틀 X).
_CAPS = [
    # AI/ML 모델링 — 모델 학습·예측이 핵심인데 AI/ML 직군이 없을 때(백엔드는 cover 아님 — 별도 전문성).
    ("AI/ML(모델 학습·예측)",
     lambda t: (_kw("학습시키", "머신러닝", "딥러닝", "신경망", "ml 모델", "예측 모델", "ai 모델")(t)
                or ("ai" in t and _kw("학습", "예측", "모델")(t))),
     _kw("ai", "머신", "딥러닝", "인공지능", "ml", "데이터 과학", "데이터 사이언", "data scien", "machine learn")),
    # 실데이터 수집·파이프라인 — 실/공공 데이터를 받아와 쓰는 게 전제일 때(백엔드/AI가 흡수하던 영역이라
    # 백엔드는 cover 아님 — 전담 데이터 직군 강제 → 데이터엔지니어↔AI엔지니어 핸드오프 협업도 생긴다).
    ("실데이터 수집·파이프라인",
     lambda t: (_kw("공공데이터", "공공 데이터", "실데이터", "실제 데이터", "오픈데이터", "open data")(t)
                and _kw("받아", "수집", "연동", "활용", "파이프라인", "크롤", "가져", "fetch", "적재")(t)),
     _kw("데이터 엔지니", "데이터엔지니", "data eng", "데이터 수집", "데이터 파이프", "etl", "데이터 분석")),
    # 데이터 영속·DB — 계정·기록·랭킹 등 지속 저장이 핵심일 때. 기본 CRUD는 백엔드가 덮으니 백엔드·DBA가
    # 둘 다 없을 때만 갭(과채용 방지 — 백엔드 있으면 발동 안 함).
    ("데이터 영속·DB",
     _kw("데이터베이스", "데이터 베이스", "database", "영속 저장", "계정", "로그인", "회원가입",
         "랭킹 저장", "기록 저장", "쿼리 최적"),
     _kw("dba", "데이터베이스", "데이터 베이스", "백엔드", "backend", "서버 개발")),
    # 배포·인프라(DevOps) — 배포 파이프라인·운영 자동화가 *명시적으로* 요구될 때만(평범한 웹 배포는 표준
    # 파이프라인이 처리 → 안 걸림). 키워드를 좁혀 과채용 방지.
    ("배포·인프라(DevOps)",
     _kw("ci/cd", "cicd", "파이프라인 구축", "도커", "컨테이너 오케", "쿠버네티스", "kubernetes",
         "오토스케일", "무중단", "로드밸런", "인프라 구축", "운영 자동화", "sre"),
     _kw("devops", "데브옵스", "인프라", "sre", "배포 엔지니", "플랫폼 엔지니")),
]


def _capability_gaps(goal_text, labels):
    """목표가 요구하는 전문 능력 중 팀(라벨들)이 *아무도 보유 못 한* 것 — 능력명 리스트. 리더가 자기 직군
    밖 도메인을 흡수(언더스태핑)하는 걸 set_goal에서 잡기 위함. 기능 식별(직군 타이틀 하드코딩 아님)."""
    t = str(goal_text or "").lower()
    have = " ".join(str(l or "").lower() for l in (labels or []))
    return [name for name, need, covered in _CAPS if need(t) and not covered(have)]


def _needed_caps_coverage(goal_text, labels):
    """목표가 *요구하는* 능력(need True)별 '덮는 팀원 수' {능력명: 수}. 깊이 게이트가 '필요 능력이 다 1명뿐'
    (그 도메인 품질이 한 사람 지능에 인질)인지 보는 데 쓴다 — 갭(0)은 staffing이 먼저 잡으므로 여기선 1명 이상 전제."""
    t = str(goal_text or "").lower()
    out = {}
    for name, need, covered in _CAPS:
        if need(t):
            out[name] = sum(1 for l in (labels or []) if covered(str(l or "").lower()))
    return out


def _offdomain_capability_hit(flow, to, body):
    """[직군밖 사전 차단 — P4 직군밖 거부 부활(2026-06-22)] Work body가 요구하는 능력(_CAPS need) 중 수신자(to)
    직군이 못 덮고 *다른* 팀원(리더 제외)이 덮는 것 → {능력명: [멤버]}. 비면 직군밖 아님(또는 덮는 전문가가
    없어 staffing 영역). 종전 [직군밖]는 받은 봇이 거부하는 사후 채널인데 1회만 쓰였다(봇은 받으면 그냥 흡수)
    — 이건 *위임 전에* 능력표로 잡아 그 전문가에게 리다이렉트(P-022 백엔드가 AI·data 흡수 차단). 의식적 예외는
    body '[직군초과: 사유]'. 능력표 밖 도메인(사운드↔VFX 등)은 봇-side [직군밖] 반려가 백스톱."""
    if "[직군초과" in (body or ""):
        return {}
    tl = (flow._info(to) or "").lower()
    bn = [name for name, need, covered in _CAPS if need((body or "").lower()) and not covered(tl)]
    if not bn:
        return {}
    hit = {}
    for name, need, cov in _CAPS:
        if name in bn:
            ms = [m for m in flow.current.team if m != to and m != flow.leader
                  and cov((flow._info(m) or "").lower())]
            if ms:
                hit[name] = ms
    return hit


# 채용 대기 인력(직군 미배정). recruit(role=…)로 런타임에 '게임 기획자·UX 디자이너' 등 필요한 직군으로
# 채용해 합류시킨다. 로스터에서 라벨이 '예비'인 봇들이며, 첫 '전원 기획'엔 안 들어가고 필요할 때 합류한다.
_SPARE_LABEL = "예비"


def _is_spare(flow, oid) -> bool:
    return (flow._info(oid) or "").strip().startswith(_SPARE_LABEL)


def _norm_job(name: str) -> str:
    return " ".join((name or "").split()).casefold()


# 겸직 라벨 구분자: '백엔드·QA' = 주직군 + 부직군. 겸직은 예외(예비 0명 또는 유사 직무)에서만,
# 봇당 최대 2개 — 더하기만 하던 시절의 '직군 5~6개 스택'(라이브 관측)으로 회귀하지 않기 위한 한도.
_JOB_SEP = "·"


def _jobs_of(label) -> List[str]:
    """라벨 → 보유 직군 목록('백엔드·QA' → ['백엔드','QA']). 단일 직군이면 1개짜리 리스트."""
    return [j.strip() for j in str(label or "").split(_JOB_SEP) if j.strip()]


def _job_tokens(name: str):
    return {t.casefold() for t in (name or "").split() if t}


def _free_alternatives(flow, me_id, to) -> str:
    """[전역 점유] 타 흐름에 점유된 to 대신 '지금 가용한 같은 직군 동료'와 채용 옵션을 안내문으로.
    재시도(폴링) 대신 구조적 선택지를 줘서, 점유 거부가 막다른 길이 아니라 분기점이 되게 한다."""
    eng, scope = flow.comm.engagement, flow.comm.scope
    jobs = {_norm_job(j) for j in _jobs_of(flow._info(to) or "")} - {""}
    alts = []
    for b in flow.pool:
        if b in (to, me_id) or _is_spare(flow, b):
            continue
        if jobs and not (jobs & {_norm_job(j) for j in _jobs_of(flow._info(b) or "")}):
            continue
        if eng is not None and scope is not None and eng.busy_elsewhere(b, scope):
            continue
        alts.append(f"{flow._info(b)}(id {b})")
    spares = [s for s in flow.pool if _is_spare(flow, s)]
    parts = []
    if alts:
        parts.append("지금 가용한 같은 직군 동료: " + ", ".join(alts[:4]))
    # [B-21 용도② — capability ledger 후보 나열(판정 아님)] 점유된 to의 직군이 덮는 능력(_CAPS cover)에
    # '검증된 실적'(owner 정당 수임+교차검증 통과 Task 저작만 적립된 장부, 임계치 이상)을 가진 가용 봇을
    # *정보로만* 나열한다 — 선택 판단은 요청자 몫이고, 스태핑·직군밖 게이트 판정은 이 장부를 안 본다(무완화).
    led = getattr(flow, "capability_ledger", None) or {}
    if led:
        from ..audit import CAP_MIN
        _tl = (flow._info(to) or "").lower()
        caps_of_to = [n for n, _need, cov in _CAPS if cov(_tl)]
        track = []
        for b in flow.pool:
            if b in (to, me_id) or _is_spare(flow, b):
                continue
            if eng is not None and scope is not None and eng.busy_elsewhere(b, scope):
                continue
            hits = [f"{n} {int((led.get(b) or {}).get(n, 0))}건" for n in caps_of_to
                    if int((led.get(b) or {}).get(n, 0)) >= CAP_MIN.get(n, 3)]
            if hits:
                track.append(f"{flow._info(b)}(id {b}: {', '.join(hits)})")
        if track:
            parts.append("검증된 실적 보유 후보(참고 정보 — 판정 아님): " + ", ".join(track[:3]))
    if spares:
        parts.append(f"또는 recruit(role=…)로 예비 {len(spares)}명 중 채용")
    return ("; ".join(parts) if parts else
            "지금은 같은 직군의 가용 동료가 없습니다 — 다른 직군 동료로 진행 가능한 부분을 먼저 하거나, "
            "불가하면 그 사정을 보고에 남기세요")


# ── [협업 라우팅 헬퍼 — guide_tools에서 이관] 멤버 해석·중복제거·변형직군 매칭·응답 실질성 ──
def _resolve_members(spec, flow, allowed) -> List[int]:
    """'12, 백엔드A' 처럼 id 또는 역할명으로 동료를 지정 → allowed 안의 id 리스트(중복 제거)."""
    out: List[int] = []
    for tok in str(spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.lstrip("-").isdigit():
            v = int(tok)
            if v in allowed and v not in out:
                out.append(v)
        else:  # 역할명(부분일치)로도 지정 가능
            for i in allowed:
                if i not in out and tok.lower() in (flow._info(i) or "").lower():
                    out.append(i)
                    break
    return out


def _uniq(xs) -> List[int]:
    seen: List[int] = []
    for x in xs:
        if x not in seen:
            seen.append(x)
    return seen


def _find_variant_job(name: str, existing) -> Optional[str]:
    """기존 직군과 '이름은 다른데 토큰을 공유'하면 변형(중복 생성) 의심으로 그 기존 직군을 돌려준다.
    recruit가 자유 텍스트 직군명을 받다 보니 흐름마다 'VFX 전문가'/'VFX 아티스트' 같은 변형이 새 역할로
    계속 불어났다(중복 생성 오류의 뿌리). 무엇이 '정답 이름'인지는 시스템이 정하지 않는다(하드코딩 금지)
    — 같은 이름(공백·대소문자 무시)은 기존 역할 재사용이라 통과시키고, 변형만 멈춰 세워 에이전트가
    '재사용'인지 '진짜 새 직군'인지 명시하게 한다."""
    mine_n, mine_t = _norm_job(name), _job_tokens(name)
    if not mine_t:
        return None
    if any(_norm_job(ex) == mine_n for ex in existing):
        return None                        # 같은 이름이 이미 있음 → 그대로 재사용(변형 아님), 즉시 통과
    for ex in sorted(existing):            # 정렬: 같은 입력엔 같은 안내(메시지 결정성)
        if mine_t & _job_tokens(ex):
            return ex
    return None


# 협의로 '인정되는' Info인지 — 순수 응답확인 핑('응답 가능하신가요?')은 합의로 치지 않는다(빈 핑 차단).
# 짧은데 핑 문구가 거의 전부일 때만 비실질(긴 메시지는 핑 문구가 섞여도 실질로 본다).
_HOLLOW_PING = ("응답 가능", "응답가능", "응답 되시", "응답되시", "계신가요", "준비되셨", "들리시",
                "확인 가능하신", "ready?", "available?", "are you there", "are you available")


def _clarify_hold(flow, me_id):
    """[G2 — clarify 행동 잠금(BOT_ARCH_REDESIGN B-02)] 되묻기(pending_clarify)를 남긴 봇이 답을 받기 전에
    추측으로 진행(request/run/vote/meet)하는 걸 구조로 막는다 — 종전엔 지시문(:1149)뿐이라 세팅(:1146)과
    소비(_deliver) 사이 창에서 추측 진행이 재발(라이브 사고 재발점 ②). `from==me_id` 키잉 한정 — stale 슬롯이
    위임자·제3자 턴까지 잠그는 회귀 방지(fork_active 게이트와 동형 1조건). 잠금 시 안내문(str), 아니면 None."""
    pc = getattr(flow, "pending_clarify", None)
    if pc and pc.get("from") == me_id:
        return ("[대기] 되묻기가 위임자에게 전달 중 — 이 턴을 마치세요"
                "(추가 도구 호출·추측 진행 금지. 위임자가 답하면 이 작업을 다시 맡깁니다).")
    return None


def _body_overlap(a, b) -> bool:
    """[G1 보조(B-04)] 두 위임 본문이 '실질 중복'(같은 산출물을 다시 맡김)인지 — 토큰 겹침 보수 판정.
    작은 쪽 토큰의 60%+가 겹치면 중복으로 본다(표현 바꿔 같은 일을 타인에게 재발사하는 패턴 포착).
    도메인·직군 하드코딩 없음(순수 어휘 겹침)."""
    ta = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(a or "").lower()))
    tb = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(b or "").lower()))
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6


def _token_overlap_score(a, b) -> float:
    """[② 관련성 주입] 두 텍스트의 토큰 겹침 비율(0.0~1.0) — _body_overlap의 스코어판(bool 아님).
    작은 쪽 토큰 대비 교집합 비율. 회의 발언을 '이 봇 도메인에 얼마나 관련되나'로 랭킹해 관련
    발언에 주입 예산을 더 주는 데 쓴다(하드코딩 없는 순수 어휘 겹침 — _body_overlap 관례 동형)."""
    ta = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(a or "").lower()))
    tb = set(re.findall(r"[0-9A-Za-z가-힣]{2,}", str(b or "").lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _is_substantive(body: str) -> bool:
    b = (body or "").strip()
    if not b:
        return False
    low = b.lower()
    return not (len(b) <= 30 and any(h in low for h in _HOLLOW_PING))


# ── [협업 실행 헬퍼 — guide_tools에서 이관] 그룹핑·스레드 멤버십·병렬 포크수집 ──
async def _fork_collect(flow, me_id, members, body_of, kind=Kind.INFO):
    """[병렬 Info fork-join] '독립 의견 수집'(표결·회의 1라운드)을 동시에 돈다 — Communication.md
    13–14행("여럿(병렬)은 이 제약을 완화하는 Feature로 둔다")의 구현. 완화는 정확히 이 구간뿐:
    - 가지(branch)는 comm 프레임을 열지 않는다 → 가지 봇은 '활성'이 아니므로 request가 규약
      에러로 자연 차단된다(가지의 중첩 요청 금지가 프롬프트가 아니라 구조로 강제 — 답만 한다).
    - 회사 풀 관점은 전역 점유로 일관: 수집 동안 가지 봇은 점유돼 타 흐름이 못 집어가고, 끝나면
      즉시 풀로 돌아간다. 타 흐름 점유/이 흐름에서 위임 보유 중인 멤버는 건너뛴다(부분 조인 —
      일부 멤버 때문에 수집 전체가 막히지 않는다).
    - 행 안전: 각 가지는 워커 침묵 워치독이 종결을 보장 → 조인이 영원히 안 닫히는 일이 구조적으로
      없다. 동시 폭은 ORGANT_FORK_FAN(기본 3)으로 묶는다(토큰 속도 운영 노브, 1이면 직렬과 동일).
    kind: 가지의 작업 종류 — Info(의견 수집, 기본)면 훅이 가지의 선구현(Write/Edit)을 종전대로
    차단한다(flow.fork_kind로 프레임 없는 가지에 게이트 연결; Work 가지는 휴면 — 호출부 없음).
    수집 동안 flow.fork_active를 올려 신규 요청/중첩 수집을 [대기]로 막는다 — CLI가 같은 턴에
    병렬 도구 호출을 내도(vote+request 등) 가지와 같은 동료를 이중으로 깨우는 일이 구조적으로 없다.
    반환: 멤버 순서 보존 [(member, res|None, 제외/실패 사유)]."""
    eng, scope = flow.comm.engagement, flow.comm.scope
    sem = asyncio.Semaphore(max(1, int(os.environ.get("ORGANT_FORK_FAN", "3"))))

    async def _branch(m):
        if flow.comm.is_busy(m):
            return (m, None, "(이 흐름에서 진행 중인 위임 보유 — 이번 수집에서 제외)")
        if eng is not None and scope is not None and eng.busy_elsewhere(m, scope):
            return (m, None, f"(타 흐름({eng.holder(m)}) 참여 중 — 이번 수집에서 제외)")
        if eng is not None and scope is not None:
            eng.engage(m, scope)
        flow.fork_kind[m] = kind
        try:
            async with sem:
                return (m, await flow.wake(m, body_of(m), kind), "")
        except Exception as e:
            return (m, None, f"(수집 실패: {e})")
        finally:
            flow.fork_kind.pop(m, None)
            if eng is not None and scope is not None and not flow.comm.is_busy(m):
                eng.release(m, scope)

    flow.fork_active = getattr(flow, "fork_active", 0) + 1
    try:
        return list(await asyncio.gather(*(_branch(m) for m in members)))
    finally:
        flow.fork_active -= 1


def _group_of(flow, team):
    return [(f"<@{i}>", flow._info(i)) for i in team]


async def _add_members(g, thread_id, member_ids):
    """Task 스레드에 팀원 추가(멤버십=팀). Guide에 메서드 없으면 건너뜀."""
    fn = getattr(g, "add_thread_members", None)
    if fn:
        await fn(thread_id, member_ids)


async def _say(flow, who, text):
    """[Communication] 회의·표결 발언을 '그 봇 본인 명의'로 스레드에 남긴다 — 독립 의견이 리더 명의
    묶음으로 게시돼 '중앙 공지'처럼 보이던 착시 제거(협업 가시성=실체). 실패는 조용히(best-effort).
    flow는 duck-typed(current·guide)."""
    g = flow.guide
    try:
        if flow.current:
            await g.post(int(flow.current.thread_id), who, text)
    except Exception:
        pass


async def _say_speech(flow, who, prefix, full):
    """[B-12 — 매체 조건부 clip(BOT_ARCH_REDESIGN 2026-07-03)] 회의 발언의 채널 게시:
    Guide가 선택 메서드 `post_document`를 실구현한 매체(murmur)면 발언 *전문*을 문서로 남기고
    채널엔 500자 clip + `…[전문: <ref>]`(전문 접근 경로 실존 — §17). 미구현/실패 매체(디스코드 등)는
    500자 clip 폴백 — 닿을 수 없는 참조는 붙이지 않는다('표기만 있고 접근 불가' 금지, 부록 A-10).
    (2026-07-03: 채널 clip 200→500 — 쇼케이스 발언 legibility 보존, 전문 접근 불가 상태의 정보손실 완화.)
    getattr-optional 관례(add_thread_members·edit_message 동형): Guide 계약은 post() 폴백 1건."""
    from .._util import _speech_clip
    ref = None
    fn = getattr(flow.guide, "post_document", None)   # 선택 메서드 — 없으면 폴백
    if fn is not None and flow.current is not None:
        try:
            ref = await fn(int(flow.current.thread_id), who, str(prefix)[:80], full)
        except Exception:
            ref = None                                # 미배포 백엔드·순단 — clip 폴백(무중단)
    if ref:
        await _say(flow, who, f"{prefix} {_speech_clip(full, 500)} …[전문: {ref}]")
    else:
        await _say(flow, who, f"{prefix} {_speech_clip(full, 500)}")

